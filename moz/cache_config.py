"""Cache TTL per /moz/* endpoint (cache.py, keyed on the validated params —
marshmallow fills the defaults, so `?page=1` and no `page` share a row).

Tiers follow how fast Moz itself moves each surface: Domain Authority and
the link counts move with Moz's index (roughly monthly for DA, daily for the
discovered/lost chart), the Top 500 lists are refreshed a few times a year,
MozCast posts one reading a day (~16:00 UTC) and the algorithm history gains
an entry every few weeks.
"""
from datetime import timedelta

# --- domain reports ------------------------------------------------------------
# One upstream page serves all six domain routes; the discovered/lost chart
# gains a day every day, so half a day keeps it current.
REPORT_CACHE = timedelta(hours=12)

# --- top lists -----------------------------------------------------------------
TOP_LISTS_CACHE = timedelta(days=7)

# --- MozCast / algorithm history -------------------------------------------------
MOZCAST_CACHE = timedelta(hours=1)
ALGORITHM_UPDATES_CACHE = timedelta(hours=12)
