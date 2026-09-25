"""Configuration for the Moz Scraper. Everything can be set with an
environment variable; the defaults work out of the box.

    PORT       port the API listens on (default 8000)
    MOZ_PROXY  proxy URL for every request, e.g. http://user:pass@host:port
               (default: none — direct). Moz sits behind Cloudflare, which
               rate-limits domain reports to roughly 5-8 per IP in a burst.
               For a handful of lookups you don't need a proxy; for volume,
               use a ROTATING residential proxy (a fresh IP per connection):
               the scraper opens a new connection every few reports and on
               every block, so each retry lands on a new IP.

Everything else below is a plain constant with a working default — edit it
here if you need to.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

# Retry policy for transport errors and blocks (every request).
MAX_RETRIES = 3
RETRY_BACKOFF = 2          # seconds, multiplied by the attempt number

MOZ_PROXY = os.environ.get("MOZ_PROXY") or None

MOZ_REQUESTS_PER_EXIT = 4  # reports per connection before a fresh one (a new IP on a rotating proxy)
MOZ_MAX_ATTEMPTS = 4       # attempts per page, each on a fresh connection after a block
MOZ_BULK_WORKERS = 5       # parallel reports in /domains/authority/bulk

# The hosted version can fall back to a real browser when every attempt is
# challenged; this kit ships without one, so the fallback stays off.
MOZ_BROWSER_FALLBACK = False
MOZ_BROWSER_CONCURRENCY = 1


def moz_proxy():
    """Proxy for domain reports (None = direct)."""
    return os.environ.get("MOZ_PROXY") or None


def moz_open_proxy():
    """Proxy for the open pages (Top 500 lists, MozCast, algorithm history)."""
    return os.environ.get("MOZ_PROXY") or None
