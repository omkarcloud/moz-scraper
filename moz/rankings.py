"""/moz/rankings/* — Moz's public Top 500 lists.

  top-domains  /top-500/download/?table=top500Domains (CSV: exact linking
               root domain counts, fresher than the HTML table)
  top-brands   /top-brands (Top 500 US brands by Brand Authority)
"""
from moz import cache_config as ttl
from moz import fetch, parsers
from moz.shared import paginate_items

PER_PAGE = 50


def _filtered(items, query):
    if not query:
        return items
    needle = query.lower()
    return [item for item in items if needle in (item.get("domain") or "")]


def _top_domains():
    return parsers.parse_top_domains_csv(
        fetch.open_page("/top-500/download/?table=top500Domains", marker="Root Domain", csv=True))


def _top_brands():
    return parsers.parse_top_brands(fetch.open_page("/top-brands", marker="Brand Authority"))


def top_domains(page=1, query=None):
    rows = fetch.dataset("top_domains", _top_domains, ttl.TOP_LISTS_CACHE)
    items, pagination = paginate_items(_filtered(rows, query), page, PER_PAGE)
    return {"pagination": pagination, "domains": items}


def top_brands(page=1, query=None):
    data = fetch.dataset("top_brands", _top_brands, ttl.TOP_LISTS_CACHE)
    items, pagination = paginate_items(_filtered(data.get("brands") or [], query), page, PER_PAGE)
    return {"pagination": pagination, "last_updated": data.get("last_updated"), "brands": items}
