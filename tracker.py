"""Conservative stock monitoring using retailer product JSON-LD."""
import argparse
import concurrent.futures
import decimal
import fcntl
import json
import logging
import os
import signal
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

LOG = logging.getLogger('tracker')
STOP = threading.Event()

class StructuredData(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.buffer = ''
        self.documents = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script' and dict(attrs).get('type') == 'application/ld+json':
            self.active = True
            self.buffer = ''

    def handle_data(self, data):
        if self.active:
            self.buffer += data

    def handle_endtag(self, tag):
        if tag == 'script' and self.active:
            self.active = False
            try:
                self.documents.append(json.loads(self.buffer))
            except ValueError:
                pass

def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)

def inspect(html, listing):
    parser = StructuredData()
    parser.feed(html)
    for product in objects(parser.documents):
        kinds = product.get('@type', [])
        if isinstance(kinds, str):
            kinds = [kinds]
        if 'Product' not in kinds:
            continue
        name = product.get('name', '').lower()
        if not all(term.lower() in name for term in listing['match']):
            continue
        if any(term.lower() in name for term in listing.get('exclude', [])):
            continue
        offers = product.get('offers', [])
        if isinstance(offers, dict):
            offers = [offers]
        for offer in offers:
            seller = offer.get('seller', {})
            seller = seller.get('name', '') if isinstance(seller, dict) else seller
            if seller.casefold() not in [s.casefold() for s in listing['sellers']]:
                continue
            try:
                price = decimal.Decimal(str(offer.get('price', '')))
                if not price.is_finite() or price <= 0:
                    continue
            except decimal.InvalidOperation:
                continue
            if offer.get('priceCurrency') != 'USD':
                continue
            availability = str(offer.get('availability', '')).rsplit('/', 1)[-1]
            if availability not in ('InStock', 'OutOfStock', 'SoldOut'):
                continue
            condition = str(offer.get('itemCondition', '')).rsplit('/', 1)[-1]
            if condition != 'NewCondition':
                continue
            eligible = availability == 'InStock' and price <= decimal.Decimal(listing['max_price'])
            return {'status': 'eligible' if eligible else 'unavailable', 'price': str(price), 'seller': seller}
    return {'status': 'unknown', 'reason': 'Exact product, seller, new condition, USD price or stock signal missing'}

def check(listing):
    if not listing.get('url'):
        return {'status': 'unknown', 'reason': 'Product URL not verified yet'}
    try:
        request = urllib.request.Request(listing['url'], headers={'User-Agent': 'ZeldaRestockTracker/1.0 (personal stock monitor)'})
        with urllib.request.urlopen(request, timeout=20) as response:
            original = urllib.parse.urlsplit(listing['url']).hostname
            if urllib.parse.urlsplit(response.url).hostname != original:
                return {'status': 'unknown', 'reason': 'Unexpected redirect host'}
            body = response.read(5_000_001)
            if len(body) > 5_000_000:
                return {'status': 'unknown', 'reason': 'Response exceeds size limit'}
        result = inspect(body.decode('utf-8', errors='replace'), listing)
        if listing['store'] == 'Nintendo Store' and os.environ.get('NINTENDO_BUTTON_CHECK') == '1':
            from nintendo_button import check_button
            return check_button(listing, result)
        return result
    except Exception as error:
        return {'status': 'unknown', 'reason': type(error).__name__}

def notify(message, title, url=None):
    topic = os.environ.get('NTFY_TOPIC', '')
    if not topic or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in topic):
        raise ValueError('Set NTFY_TOPIC to a random alphanumeric topic')
    headers = {'Title': title, 'Priority': 'high', 'Content-Type': 'text/plain; charset=utf-8'}
    if url:
        headers['Click'] = url
    if os.environ.get('NTFY_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['NTFY_TOKEN']
    request = urllib.request.Request('https://ntfy.sh/' + topic, data=message.encode(), headers=headers)
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError('Notification rejected')

def database(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE IF NOT EXISTS states (id TEXT PRIMARY KEY, status TEXT, alerted INTEGER, observed REAL, detail TEXT)')
    return db

def record(db, listing, result, send):
    row = db.execute('SELECT status, alerted FROM states WHERE id=?', (listing['id'],)).fetchone()
    prior, alerted = row or ('unknown', 0)
    status = result['status']
    if status == 'unknown':
        # A failed request must never manufacture an out-of-stock transition.
        state = prior
    else:
        state = status
        if state == 'unavailable':
            alerted = 0
    if status == 'eligible' and not alerted and send:
        try:
            notify(f"{listing['name']} at {listing['store']} for ${result['price']}\n{listing['url']}", 'Zelda restock', listing['url'])
            alerted = 1
        except Exception as error:
            LOG.warning('Notification pending for %s (%s)', listing['id'], type(error).__name__)
    db.execute('INSERT OR REPLACE INTO states VALUES (?,?,?,?,?)', (listing['id'], state, alerted, time.time(), json.dumps(result)))
    db.commit()
    LOG.info('%s %s', listing['id'], json.dumps(result))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--notify', action='store_true', help='Enable stock notifications')
    parser.add_argument('--test-notification', action='store_true')
    parser.add_argument('--config', default='products.json')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    if args.test_notification:
        notify('Your Zelda tracker notification connection works.', 'Zelda tracker test')
        return
    listings = json.loads(Path(args.config).read_text())
    path = os.environ.get('STATE_PATH', 'data/stock.sqlite3')
    db = database(path)
    lock = open(path + '.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    interval = max(60, int(os.environ.get('POLL_SECONDS', '120')))
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: STOP.set())
    try:
        while not STOP.is_set():
            # One task per store limits each host to one active request.
            groups = {}
            for listing in listings:
                groups.setdefault(listing['store'], []).append(listing)
            def check_store(group):
                return [(item, check(item)) for item in group]
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                futures = [executor.submit(check_store, group) for group in groups.values()]
                for future in concurrent.futures.as_completed(futures):
                    for listing, result in future.result():
                        record(db, listing, result, args.notify)
            if args.once:
                break
            STOP.wait(interval)
    finally:
        db.close()
        lock.close()

if __name__ == '__main__':
    main()
