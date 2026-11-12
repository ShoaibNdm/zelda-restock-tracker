"""Read Nintendo's visible purchase button without adding anything to a cart."""
import decimal
import re
import logging


def check_button(listing, metadata):
    from playwright.sync_api import sync_playwright
    if metadata.get('seller') not in listing['sellers'] or 'price' not in metadata:
        return metadata
    if decimal.Decimal(metadata['price']) > decimal.Decimal(listing['max_price']):
        return metadata
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(listing['url'], wait_until='domcontentloaded', timeout=30000)
                # Hydration can replace the initial placeholder purchase controls.
                page.wait_for_timeout(5000)
                buttons = page.get_by_role('button', name=re.compile(r'^Add to cart$', re.I))
                visible = [button for button in buttons.all() if button.is_visible()]
                if len(visible) != 1:
                    labels = page.locator('button, [role="button"]').all_text_contents()
                    logging.warning('Nintendo button check unresolved for %s; visible matches=%s; page title=%r; control labels=%r', listing['id'], len(visible), page.title(), [label.strip()[:120] for label in labels][:40])
                    print(f"::warning::Nintendo button check unresolved for {listing['id']}")
                    return dict(metadata, button_status='unknown', button_reason='No unique visible Add to cart button')
                button = visible[0]
                color = button.evaluate('(element) => getComputedStyle(element).backgroundColor')
                if not button.is_enabled():
                    return dict(metadata, button_status='disabled', button_color=color)
                # Trial checks visibility, stability and overlays without clicking.
                button.click(trial=True, timeout=3000)
                return dict(metadata, status='eligible', button_status='clickable', button_color=color, signal='visible Add to cart button')
            finally:
                browser.close()
    except Exception as error:
        logging.warning('Nintendo browser check failed for %s (%s)', listing['id'], type(error).__name__)
        print(f"::warning::Nintendo browser check failed for {listing['id']}")
        return dict(metadata, button_status='unknown', button_reason=type(error).__name__)
