# Running the Zelda tracker

Python 3.11+ on Linux is sufficient. The program uses the standard library.

```sh
python -m unittest -v
python tracker.py --once
```

The default command checks without sending notifications. `products.json` contains three items across five retailers. Null URLs are unresolved listings and are reported as unknown. Only exact matching product offers with an explicitly approved seller, new condition, USD price at or below the configured cap, and InStock status trigger alerts. Page metadata can lag actual inventory. Checkout availability and regional delivery require confirmation on the retailer site.

## Phone notifications

Create a random topic using `python -c 'import secrets; print("zelda-" + secrets.token_hex(20))'`. Subscribe to that topic on ntfy.sh in the Android app. Keep it out of commits. Anyone who knows an unreserved topic can subscribe or publish to it.

Set `NTFY_TOPIC` privately in the process environment. Optionally set `NTFY_TOKEN` for an account with a protected topic. Run `python tracker.py --test-notification` to verify delivery, then `python tracker.py --notify` to start monitoring.

## Render deployment

Use a Python Background Worker connected to this repository. Use `python -m unittest` as the build command and `python tracker.py --notify` as the start command. Choose the smallest suitable paid compute plan in Render and review its displayed cost. Attach a persistent disk at `/var/data` and set `STATE_PATH=/var/data/stock.sqlite3`. Set `NTFY_TOPIC` as a private environment value. Set `POLL_SECONDS=120` initially. One worker instance is supported.

Persistent storage prevents repeated initial alerts on every deployment. The worker handles SIGTERM and uses a file lock to prevent simultaneous instances sharing the database. It checks stores concurrently and listings within each store sequentially. A full cycle takes the request time plus the polling interval. Requests time out after 20 seconds. A failed notification is retried on the next eligible observation. Delivery is at-least-once; a process crash after successful delivery but before saving may cause a duplicate.

## Coverage and diagnostics

Each check logs eligible, unavailable, or unknown, and the latest observation is saved in SQLite. Unknown preserves the preceding confirmed stock state. An error never creates a new restock. Initial eligible stock generates an alert. A confirmed unavailable observation rearms it for a later restock.

Nintendo, Best Buy and GameStop returned usable structured offers during initial verification. Target did not expose a complete offer in its initial response. Walmart and remaining listings require live verification. The tracker fails closed when a source omits required evidence. Inspect logs regularly; missing coverage requires a retailer-specific integration. This version does not implement local pickup checks or automatic checkout.

No hosting service or notification subscription is created by committing this code.
