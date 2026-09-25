"""Pure parsers: moz.com HTML / CSV -> clean API dicts. No network here.

Every parser tolerates a partial page (a missing section yields null / [])
and never raises on odd values; fixtures live in moz/fixtures/.

Response conventions: snake_case, `link` for URLs, `is_*` booleans, ISO
dates (YYYY-MM-DD), numbers as numbers, null for missing values.

Fields intentionally NOT exposed (and why):
  domain report   the favicon <img>, the "Learn more" help links and the
                  chart colours/axis config (presentation only); the page's
                  meta description (the same marketing copy on every report).
  top lists       the favicon URLs and progress-bar widths (presentation; the
                  bar width equals the authority value).
  MozCast         `icon` path (reduced to its weather name), `dateStr` /
                  `dayStr` (display forms of `date`), the feature `id`,
                  `feature_category_id` (internal ids) and `average` (always 0).
  algorithm list  the social-share widgets and filter <select>s.
"""
import csv
import html as html_lib
import io
import json
import re
from datetime import date, datetime, timedelta, timezone

from bs4 import BeautifulSoup

from moz import refs

_ABBREV_RE = re.compile(r"^\s*([\d.,]+)\s*([kmbt]?)\s*$", re.I)
_INT_RE = re.compile(r"-?\d[\d,]*")
_ORDINAL_RE = re.compile(r"(\d+)(st|nd|rd|th)\b", re.I)
_PUBLISHER_RE = re.compile(r"\s*\(([^()]+)\)\s*$")
_DAY_LABEL_RE = re.compile(r"^(\d{1,2})/(\d{1,2})$")
_MONTHS = {name.lower(): i for i, name in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], start=1)}
_MONTHS.update({name[:3].lower(): i for name, i in list(_MONTHS.items())})
_MONTHS["sept"] = 9


# ---- value helpers ---------------------------------------------------------------

def soup(markup):
    return BeautifulSoup(markup or "", "lxml")


def clean(node):
    """A node / string -> whitespace-collapsed text or None."""
    if node is None:
        return None
    value = node.get_text(" ") if hasattr(node, "get_text") else str(node)
    value = " ".join(html_lib.unescape(value).replace("\xa0", " ").split())
    return value or None


def to_int(value):
    """'1,234' / '95' / 95 -> int; junk -> None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = _INT_RE.search(str(value or ""))
    if not match:
        return None
    try:
        return int(match.group(0).replace(",", ""))
    except ValueError:
        return None


def abbreviated_count(value):
    """Moz's display counts: '455.9k' -> 455900, '2.3m' -> 2300000,
    '1m' -> 1000000, '181' -> 181, '1,234' -> 1234; '--' / junk -> None."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    match = _ABBREV_RE.match(str(value))
    if not match:
        return None
    try:
        amount = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    amount *= {"": 1, "k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}[match.group(2).lower()]
    return int(round(amount))


def percent(value):
    """'6%' -> 6; '--' / junk -> None."""
    text = str(value or "").strip()
    if not text or text.startswith("--"):
        return None
    return to_int(text)


def rounded(value, digits=2):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def written_date(value):
    """'June 24, 2026' | 'May 3rd, 2015' | 'Sept 27, 2016' -> '2026-06-24'."""
    text = _ORDINAL_RE.sub(r"\1", clean(value) or "").replace(".", "")
    match = re.match(r"^([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})$", text)
    if not match:
        return None
    month = _MONTHS.get(match.group(1).lower())
    if not month:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(2))).isoformat()
    except ValueError:
        return None


def utc_date(value):
    """'2026-09-24T16:00:23.000Z' -> '2026-09-24' (UTC); junk -> None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.date().isoformat()


def label_dates(labels, today=None):
    """Chart labels 'MM/DD' (oldest first, no year) -> ISO dates. Each label
    takes the latest year that does not put it in the future (a window
    spanning New Year gets the previous year for its December labels)."""
    today = today or datetime.now(timezone.utc).date()
    out = []
    for label in labels:
        match = _DAY_LABEL_RE.match(str(label).strip())
        if not match:
            out.append(None)
            continue
        month, day = int(match.group(1)), int(match.group(2))
        resolved = None
        for year in (today.year, today.year - 1):
            try:
                candidate = date(year, month, day)
            except ValueError:
                continue
            if candidate <= today + timedelta(days=2):
                resolved = candidate.isoformat()
                break
        out.append(resolved)
    return out


def _json_after(text, anchor):
    """The JSON value that starts right after `anchor` in a script, or None."""
    idx = (text or "").find(anchor)
    if idx < 0:
        return None
    start = idx + len(anchor)
    while start < len(text) and text[start] in " \t\r\n":
        start += 1
    try:
        value, _ = json.JSONDecoder().raw_decode(text, start)
    except ValueError:
        return None
    return value


def _json_ld_date_modified(doc):
    for script in doc.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        nodes = data.get("@graph") if isinstance(data, dict) else None
        for node in nodes if isinstance(nodes, list) else [data]:
            if isinstance(node, dict) and node.get("dateModified"):
                return str(node["dateModified"])[:10]
    return None


# ---- domain report (/domain-analysis/<domain>) --------------------------------------

_METRIC_KEYS = {
    "domain authority": "domain_authority",
    "linking root domains": "linking_root_domains",
    "ranking keywords": "ranking_keywords",
    "spam score": "spam_score",
}


def _section_rows(doc, heading):
    """The <tr>s of the results card whose <h3> starts with `heading`."""
    for h3 in doc.select("h3"):
        if (clean(h3) or "").lower().startswith(heading.lower()):
            card = h3.find_parent(class_="results-card") or h3.find_parent("div")
            if card is None:
                return []
            body = card.select_one("tbody")
            return body.select("tr") if body is not None else []
    return []


def _link_history(html, today=None):
    # Anchor on the chart script: a browser-rendered DOM (the patchright
    # fallback) carries other "columns:" text (CSS grid rules) earlier on.
    start = (html or "").find("var domainLabels")
    if start < 0:
        return None
    script = html[start:]
    labels = _json_after(script, "var domainLabels =")
    columns = _json_after(script, "columns:")
    if not isinstance(labels, list) or not isinstance(columns, list):
        return None
    series = {}
    for column in columns:
        if isinstance(column, list) and column and isinstance(column[0], str):
            series[column[0].lower()] = column[1:]
    discovered = series.get("discovered") or []
    lost = series.get("lost") or []
    dates = label_dates(labels, today)
    days = []
    for i, day in enumerate(dates):
        gained = to_int(discovered[i]) if i < len(discovered) else None
        dropped = abs(to_int(lost[i])) if i < len(lost) and to_int(lost[i]) is not None else None
        days.append({
            "date": day,
            "discovered": gained,
            "lost": dropped,
            "net_change": gained - dropped if gained is not None and dropped is not None else None,
        })
    days.reverse()   # newest first
    total_discovered = sum(d["discovered"] or 0 for d in days)
    total_lost = sum(d["lost"] or 0 for d in days)
    dated = [d["date"] for d in days if d["date"]]
    return {
        "period_start": dated[-1] if dated else None,
        "period_end": dated[0] if dated else None,
        "total_discovered": total_discovered,
        "total_lost": total_lost,
        "net_change": total_discovered - total_lost,
        "days": days,
    }


def parse_domain_report(html, requested_domain=None, today=None):
    """The full report of one /domain-analysis/<domain> page."""
    doc = soup(html)
    shown = doc.select_one("h4 span.text-pink")
    domain = (clean(shown) or requested_domain or "").lower() or None
    if domain and domain.startswith("www."):
        domain = domain[4:]

    metrics = {key: None for key in _METRIC_KEYS.values()}
    for label in doc.select("h5"):
        key = _METRIC_KEYS.get((clean(label) or "").lower())
        value_node = label.find_next_sibling("h1")
        if key and value_node is not None:
            raw = clean(value_node)
            if key == "spam_score":
                metrics[key] = percent(raw)
            elif key == "domain_authority":
                metrics[key] = to_int(raw)
            else:
                metrics[key] = abbreviated_count(raw)

    top_pages = []
    for row in _section_rows(doc, "Top Pages by Links"):
        anchor = row.select_one("a")
        cells = row.select("td")
        page = (anchor.get("title") if anchor is not None else None) or clean(cells[0] if cells else None)
        if not page:
            continue
        top_pages.append({
            "link": refs.site_link(page),
            "page_authority": to_int(clean(cells[-1])) if len(cells) > 1 else None,
        })

    linking_domains = []
    for row in _section_rows(doc, "Top Linking Domains"):
        anchor = row.select_one("a")
        cells = row.select("td")
        name = (anchor.get("title") if anchor is not None else None) or clean(cells[0] if cells else None)
        if not name:
            continue
        name = name.strip().lower()
        linking_domains.append({
            "domain": name,
            "link": refs.site_link(name),
            "domain_authority": to_int(clean(cells[-1])) if len(cells) > 1 else None,
        })

    report = {
        "domain": domain,
        "link": refs.site_link(domain) if domain else None,
        "domain_authority": metrics["domain_authority"],
        "home_page_authority": None,
        "linking_root_domains": metrics["linking_root_domains"],
        "ranking_keywords": metrics["ranking_keywords"],
        "spam_score": metrics["spam_score"],
        "top_pages": top_pages,
        "top_linking_domains": linking_domains,
        "link_history": _link_history(html, today) or {
            "period_start": None, "period_end": None, "total_discovered": 0,
            "total_lost": 0, "net_change": 0, "days": []},
        "report_link": refs.report_link(domain) if domain else None,
    }
    report["home_page_authority"] = home_page_authority(report)
    return report


def home_page_authority(report):
    """Page Authority of the domain's home page when it is among the report's
    top pages (it usually is: the home page tends to hold the highest PA),
    else None."""
    domain = report.get("domain") or ""
    if not domain:
        return None
    for page in report.get("top_pages") or []:
        link = (page.get("link") or "").split("://", 1)[-1].rstrip("/")
        if link in (domain, "www." + domain):
            return page.get("page_authority")
    return None


def authority_summary(report):
    """The headline metrics of a parsed report (the DA-checker view)."""
    return {
        "domain": report.get("domain"),
        "link": report.get("link"),
        "domain_authority": report.get("domain_authority"),
        "home_page_authority": home_page_authority(report),
        "linking_root_domains": report.get("linking_root_domains"),
        "ranking_keywords": report.get("ranking_keywords"),
        "spam_score": report.get("spam_score"),
        "report_link": report.get("report_link"),
    }


# ---- top lists ---------------------------------------------------------------------

def parse_top_domains_csv(text):
    """/top-500/download/?table=top500Domains -> [{rank, domain, link, ...}]."""
    rows = []
    reader = csv.DictReader(io.StringIO((text or "").lstrip("﻿")))
    for row in reader:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        domain = (row.get("root domain") or "").lower() or None
        if not domain:
            continue
        rows.append({
            "rank": to_int(row.get("rank")),
            "domain": domain,
            "link": refs.site_link(domain),
            "domain_authority": to_int(row.get("domain authority")),
            "linking_root_domains": to_int(row.get("linking root domains")),
        })
    return rows


def parse_top_brands(html):
    """/top-brands -> {"last_updated", "brands": [{rank, domain, link, ...}]}."""
    doc = soup(html)
    brands = []
    seen = set()
    for table in doc.select("table"):
        header = [(clean(th) or "").lower() for th in table.select("tr")[0].select("th, td")] \
            if table.select("tr") else []
        if "brand authority" not in header:
            continue
        idx = {name: i for i, name in enumerate(header)}
        for row in table.select("tr")[1:]:
            cells = row.select("td")
            if len(cells) < len(header):
                continue
            domain = (clean(cells[idx.get("root domain", 1)]) or "").lower() or None
            rank = to_int(clean(cells[idx.get("rank", 0)]))
            if not domain or (rank, domain) in seen:
                continue
            seen.add((rank, domain))
            brands.append({
                "rank": rank,
                "domain": domain,
                "link": refs.site_link(domain),
                "brand_authority": to_int(clean(cells[idx.get("brand authority", 3)])),
                "ranking_keywords": to_int(clean(cells[idx.get("ranking keywords", 2)])),
            })
    brands.sort(key=lambda b: (b["rank"] is None, b["rank"] or 0))
    return {"last_updated": _json_ld_date_modified(doc), "brands": brands}


# ---- MozCast (/mozcast) -------------------------------------------------------------

def _weather_name(icon):
    """'/svc/stargate/assets/images/mozcast/stormy.png' -> 'stormy'."""
    if not isinstance(icon, str) or not icon:
        return None
    name = icon.rsplit("/", 1)[-1].rsplit(".", 1)[0].strip()
    return name.replace("_", "-").lower() or None


def parse_mozcast_weather(html):
    """The page's 90-day weather series -> [day] newest first."""
    rows = _json_after(html, "weather:")
    days = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        big10 = rounded(row.get("big10"), 6)
        days.append({
            "date": utc_date(row.get("date")),
            "temperature": to_int(row.get("temp")),
            "weather": _weather_name(row.get("icon")),
            "average_page_one_results": rounded(row.get("serps_count")),
            "domain_diversity_percent": rounded(row.get("domain_diversity")),
            "big_10_share_percent": round(big10 * 100, 2) if big10 is not None else None,
            "exact_match_domain_share_percent": rounded(row.get("emd_influence")),
            "partial_match_domain_share_percent": rounded(row.get("pmd_influence")),
        })
    days.sort(key=lambda d: d["date"] or "", reverse=True)
    return days


def parse_mozcast_features(html):
    """The SERP-feature prevalence series -> [feature] (history newest first)."""
    rows = _json_after(html, "features:")
    features = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        token = row.get("name")
        history = []
        for point in row.get("data") or []:
            if not isinstance(point, dict):
                continue
            value = rounded(point.get("value"), 6)
            history.append({
                "date": utc_date(point.get("date")),
                "prevalence_percent": round(value * 100, 2) if value is not None else None,
            })
        history.sort(key=lambda p: p["date"] or "", reverse=True)
        features.append({
            "slug": refs.FEATURE_SLUG_BY_TOKEN.get(token) or (token or "").replace("_", "-") or None,
            "name": clean(row.get("title")),
            "current_prevalence_percent": history[0]["prevalence_percent"] if history else None,
            "history": history,
        })
    return features


# ---- Google algorithm change history (/google-algorithm-change) ---------------------

def _source(anchor):
    title = clean(anchor)
    publisher = None
    if title:
        match = _PUBLISHER_RE.search(title)
        if match:
            code = match.group(1).strip()
            publisher = refs.PUBLISHERS.get(code.upper(), code)
            title = title[:match.start()].strip() or title
    return {"title": title, "publisher": publisher, "link": anchor.get("href") or None}


def parse_algorithm_updates(html):
    """Every update card -> [{name, date, is_confirmed, description, sources}]
    (newest first, the page's order)."""
    doc = soup(html)
    updates = []
    for card in doc.select("article.js-change-entry"):
        heading = clean(card.select_one("header h5, h5"))
        name, _, when = (heading or "").partition("—")
        status = (card.get("data-confirmed") or clean(card.select_one("header span")) or "").lower()
        body = card.select_one(".card-body")
        paragraphs = [clean(p) for p in body.select("p")] if body is not None else []
        sources = [_source(a) for a in card.select("ul.js-change-links a")] if body is not None else []
        iso = written_date(when)
        year = to_int(card.get("data-year"))
        updates.append({
            "name": clean(name),
            "date": iso,
            "year": year if year is not None else (int(iso[:4]) if iso else None),
            "is_confirmed": status == "confirmed" if status in ("confirmed", "unconfirmed") else None,
            "description": "\n\n".join(p for p in paragraphs if p) or None,
            "sources": [s for s in sources if s["link"] or s["title"]],
        })
    return updates


def parse_most_volatile_days(html):
    """The 'hottest days' chart list -> [{name, date, temperature}]."""
    doc = soup(html)
    drawer = doc.select_one("#dataDrawer")
    out = []
    for item in drawer.select("li") if drawer is not None else []:
        name = clean(item.select_one("span"))
        parts = [clean(p) for p in (clean(item) or "").split("|")]
        when = parts[1] if len(parts) > 1 else None
        temp = parts[2] if len(parts) > 2 else None
        out.append({
            "name": name,
            "date": written_date(when),
            "temperature": to_int(temp),
        })
    out.sort(key=lambda d: d["temperature"] or 0, reverse=True)
    return out
