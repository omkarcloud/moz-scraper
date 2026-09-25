"""/moz/mozcast/* and /moz/google-algorithm-updates* — Moz's Google
volatility tracking.

  MozCast (/mozcast): one reading a day over a fixed set of 10,000 keywords
  (20 industries, 5 US cities): the "temperature" (day-over-day ranking
  change; ~70 °F is an uneventful day), the domain-crowding metrics and the
  prevalence of 7 SERP features, 90 days deep.

  Algorithm history (/google-algorithm-change): every named or observed
  Google update since 2000 with Moz's write-up and source articles, plus
  the "hottest days" list from its volatility chart.
"""
from moz import cache_config as ttl
from moz import fetch, parsers, refs
from moz.shared import paginate_items

UPDATES_PER_PAGE = 25


def _mozcast_page():
    return fetch.open_page("/mozcast", marker="weather:")


def _mozcast():
    html = _mozcast_page()
    return {"weather": parsers.parse_mozcast_weather(html), "features": parsers.parse_mozcast_features(html)}


def _algorithm_page():
    return fetch.open_page("/google-algorithm-change", marker="js-change-entry")


def _algorithm_history():
    html = _algorithm_page()
    return {"updates": parsers.parse_algorithm_updates(html),
            "most_volatile_days": parsers.parse_most_volatile_days(html)}


def weather(days=30):
    """The last `days` daily readings (newest first) + a summary."""
    readings = fetch.dataset("mozcast", _mozcast, ttl.MOZCAST_CACHE)["weather"][:days]
    temps = [(d["temperature"], d["date"]) for d in readings if d["temperature"] is not None]
    dated = [d["date"] for d in readings if d["date"]]
    hottest = max(temps) if temps else (None, None)
    summary = {
        "period_start": dated[-1] if dated else None,
        "period_end": dated[0] if dated else None,
        "average_temperature": round(sum(t for t, _ in temps) / len(temps), 1) if temps else None,
        "min_temperature": min(t for t, _ in temps) if temps else None,
        "max_temperature": hottest[0],
        "hottest_date": hottest[1],
    }
    return {"summary": summary, "days": readings}


def serp_features(feature=None, days=30):
    """Prevalence of each tracked SERP feature (% of page-one SERPs that show
    it), current value + `days` of history (newest first)."""
    features = fetch.dataset("mozcast", _mozcast, ttl.MOZCAST_CACHE)["features"]
    out = []
    for item in features:
        if feature and item["slug"] != feature:
            continue
        out.append({**item, "history": item["history"][:days]})
    if feature and not out:
        raise ValueError(f"MozCast no longer tracks '{feature}'; one of: {', '.join(refs.SERP_FEATURES)}.")
    return {"features": out}


def algorithm_updates(year=None, status=None, query=None, page=1):
    """Google algorithm updates, newest first, filterable by year, status
    (confirmed | unconfirmed) and a text query over name + description."""
    updates = fetch.dataset("algorithm_history", _algorithm_history, ttl.ALGORITHM_UPDATES_CACHE)["updates"]
    needle = (query or "").lower()
    selected = [
        u for u in updates
        if (year is None or u["year"] == year)
        and (status is None or u["is_confirmed"] == (status == "confirmed"))
        and (not needle or needle in (u["name"] or "").lower() or needle in (u["description"] or "").lower())
    ]
    items, pagination = paginate_items(selected, page, UPDATES_PER_PAGE)
    return {"pagination": pagination, "updates": items}


def most_volatile_days():
    """The hottest MozCast days Moz charts on its algorithm-history page."""
    history = fetch.dataset("algorithm_history", _algorithm_history, ttl.ALGORITHM_UPDATES_CACHE)
    return {"days": history["most_volatile_days"]}
