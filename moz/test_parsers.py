"""Offline tests: parsers, input resolution, schemas and block classification
against saved moz.com pages (moz/fixtures/, captured 2026-09-25).

    python -m pytest moz/test_parsers.py -q
"""
import os
from datetime import date

import pytest

from moz import fetch, parsers, refs, schemas
from moz.shared import paginate_items
from schema_fields import load_query

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
TODAY = date(2026, 9, 25)


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


# ---- input resolution --------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("hubspot.com", "hubspot.com"),
    ("HubSpot.COM", "hubspot.com"),
    ("www.hubspot.com", "hubspot.com"),
    ("blog.hubspot.com", "blog.hubspot.com"),
    ("https://www.hubspot.com/marketing?utm=x#top", "hubspot.com"),
    ("//www.bbc.co.uk/news", "bbc.co.uk"),
    ("https://moz.com/domain-analysis/semrush.com", "semrush.com"),
    ("https://moz.com/domain-analysis?site=www.semrush.com", "semrush.com"),
    ("münchen.de", "xn--mnchen-3ya.de"),
    ("example.org.", "example.org"),
])
def test_resolve_domain(value, expected):
    assert refs.resolve_domain(value) == expected


@pytest.mark.parametrize("value", ["", "localhost", "8.8.8.8", "not a domain", "foo.c", "-bad-.com", "http://"])
def test_resolve_domain_rejects(value):
    with pytest.raises(ValueError):
        refs.resolve_domain(value)


def test_resolve_feature():
    assert refs.resolve_feature("Local Packs") == "local-packs"
    assert refs.resolve_feature("lcl_new") == "local-packs"
    assert refs.resolve_feature("reviews (stars)") == "reviews"
    with pytest.raises(ValueError):
        refs.resolve_feature("maps")


# ---- value helpers -----------------------------------------------------------------

def test_value_helpers():
    assert parsers.abbreviated_count("455.9k") == 455900
    assert parsers.abbreviated_count("2.3m") == 2300000
    assert parsers.abbreviated_count("1m") == 1000000
    assert parsers.abbreviated_count("1,234") == 1234
    assert parsers.abbreviated_count("--") is None
    assert parsers.percent("6%") == 6
    assert parsers.percent("--") is None
    assert parsers.written_date("May 3rd, 2015") == "2015-05-03"
    assert parsers.written_date("Sept 27, 2016") == "2016-09-27"
    assert parsers.written_date("June 24, 2026") == "2026-06-24"
    assert parsers.written_date("sometime") is None
    assert parsers.utc_date("2026-09-24T16:00:23.000Z") == "2026-09-24"


def test_label_dates_cross_new_year():
    assert parsers.label_dates(["12/30", "12/31", "01/01", "01/02"], today=date(2027, 1, 3)) == [
        "2026-12-30", "2026-12-31", "2027-01-01", "2027-01-02"]


# ---- domain report -------------------------------------------------------------------

def test_domain_report_full():
    report = parsers.parse_domain_report(fixture("report_hubspot.html"), "hubspot.com", today=TODAY)
    assert report["domain"] == "hubspot.com"
    assert report["link"] == "https://hubspot.com"
    assert report["domain_authority"] == 93
    assert report["linking_root_domains"] == 455900
    assert report["ranking_keywords"] == 424500
    assert report["spam_score"] == 1
    assert report["home_page_authority"] == 80
    assert report["top_pages"][0] == {"link": "https://www.hubspot.com/", "page_authority": 80}
    assert len(report["top_pages"]) == 7
    assert report["top_linking_domains"][0] == {"domain": "www.google.com", "link": "https://www.google.com",
                                               "domain_authority": 100}
    history = report["link_history"]
    assert len(history["days"]) == 61
    assert history["period_start"] == "2026-07-23" and history["period_end"] == "2026-09-21"
    assert history["days"][0]["date"] == "2026-09-21"          # newest first
    assert all(d["lost"] >= 0 for d in history["days"])       # the chart's negatives become counts
    assert history["net_change"] == history["total_discovered"] - history["total_lost"]
    assert report["report_link"] == "https://moz.com/domain-analysis/hubspot.com"


def test_domain_report_unknown_domain():
    report = parsers.parse_domain_report(fixture("report_unknown.html"), "nonexistent-domain-xyz-123.com")
    assert report["domain_authority"] == 1
    assert report["linking_root_domains"] == 0
    assert report["spam_score"] is None
    assert report["top_pages"] == [] and report["top_linking_domains"] == []
    assert report["link_history"]["days"] == []
    assert report["home_page_authority"] is None


def test_domain_report_partial_page():
    report = parsers.parse_domain_report(fixture("report_partial_bbc_co_uk.html"), "bbc.co.uk", today=TODAY)
    assert report["top_pages"] == []
    assert len(report["top_linking_domains"]) == 7
    assert report["link_history"]["days"]


def test_domain_report_from_browser_dom():
    report = parsers.parse_domain_report(fixture("report_browser_zapier.html"), "zapier.com", today=TODAY)
    assert report["domain_authority"] == 82
    assert len(report["top_pages"]) == 7
    assert len(report["link_history"]["days"]) == 61


def test_authority_summary_keys():
    report = parsers.parse_domain_report(fixture("report_hubspot.html"), "hubspot.com", today=TODAY)
    summary = parsers.authority_summary(report)
    assert list(summary) == ["domain", "link", "domain_authority", "home_page_authority",
                             "linking_root_domains", "ranking_keywords", "spam_score", "report_link"]


# ---- classification ----------------------------------------------------------------

def test_classify_challenge_and_not_found():
    with pytest.raises(fetch.MozBlocked):
        fetch.classify(403, {"cf-mitigated": "challenge"}, fixture("challenge.html"), "u", "Showing results for")
    with pytest.raises(fetch.MozBlocked):
        fetch.classify(429, {}, "", "u", None)
    with pytest.raises(fetch.MozBlocked):
        fetch.classify(200, {}, "<h1>You have exceeded your search limit</h1>", "u", None)
    with pytest.raises(fetch.MozNotFound):
        fetch.classify(200, {}, fixture("not_found.html"), "u", "Showing results for")
    with pytest.raises(fetch.MozUpstreamError):
        fetch.classify(200, {}, "<html>redesigned</html>", "u", "Showing results for")
    assert fetch.classify(200, {}, fixture("report_hubspot.html"), "u", "Showing results for")


# ---- top lists ---------------------------------------------------------------------

def test_top_domains_csv():
    rows = parsers.parse_top_domains_csv(fixture("top500_domains.csv"))
    assert len(rows) == 500
    assert rows[0] == {"rank": 1, "domain": "www.google.com", "link": "https://www.google.com",
                       "domain_authority": 100, "linking_root_domains": rows[0]["linking_root_domains"]}
    assert isinstance(rows[0]["linking_root_domains"], int) and rows[0]["linking_root_domains"] > 1_000_000
    assert [r["rank"] for r in rows] == list(range(1, 501))


def test_top_brands():
    data = parsers.parse_top_brands(fixture("top_brands.html"))
    assert data["last_updated"] == "2024-02-14"
    assert len(data["brands"]) == 500
    assert data["brands"][0]["domain"] == "google.com" and data["brands"][0]["brand_authority"] == 100
    assert [b["rank"] for b in data["brands"]] == list(range(1, 501))


# ---- MozCast / algorithm history ---------------------------------------------------------

def test_mozcast_weather():
    days = parsers.parse_mozcast_weather(fixture("mozcast.html"))
    assert len(days) == 90
    assert days[0]["date"] > days[-1]["date"]
    first = days[0]
    assert first["temperature"] == 116 and first["weather"] == "stormy"
    assert 0 < first["big_10_share_percent"] < 100
    assert set(first) == {"date", "temperature", "weather", "average_page_one_results", "domain_diversity_percent",
                          "big_10_share_percent", "exact_match_domain_share_percent",
                          "partial_match_domain_share_percent"}


def test_mozcast_features():
    features = parsers.parse_mozcast_features(fixture("mozcast.html"))
    assert [f["slug"] for f in features] == list(refs.SERP_FEATURES)
    local = features[0]
    assert local["current_prevalence_percent"] == local["history"][0]["prevalence_percent"]
    assert len(local["history"]) == 90


def test_algorithm_updates():
    updates = parsers.parse_algorithm_updates(fixture("google_algorithm_change.html"))
    assert len(updates) == 279
    assert all(u["date"] and u["name"] for u in updates)
    first = updates[0]
    assert first["name"] == "June 2026 Spam Update" and first["date"] == "2026-06-24"
    assert first["is_confirmed"] is True
    assert first["sources"][0]["publisher"] == "Search Engine Land"
    assert not first["sources"][0]["title"].endswith(")")
    assert updates[-1]["date"] == "2000-12-01"


def test_most_volatile_days():
    days = parsers.parse_most_volatile_days(fixture("google_algorithm_change.html"))
    assert days[0] == {"name": "August 2023 Core", "date": "2023-08-22", "temperature": 123}
    assert all(d["date"] and d["temperature"] for d in days)


# ---- schemas / pagination ------------------------------------------------------------

def test_bulk_schema_dedupes_and_caps():
    data, error = load_query(schemas.BulkDomainsSchema, {"domains": "hubspot.com, www.hubspot.com,https://semrush.com/x"})
    assert error is None and data["domains"] == ["hubspot.com", "semrush.com"]
    _, error = load_query(schemas.BulkDomainsSchema, {"domains": ",".join(f"d{i}.com" for i in range(11))})
    assert error and "At most 10" in error["error"]


def test_schemas_reject_unknown_params():
    _, error = load_query(schemas.DomainSchema, {"domain": "moz.com", "url": "x"})
    assert error and "url" in error["errors"]


def test_paginate_items():
    items, pagination = paginate_items(list(range(120)), 3, 50)
    assert items == list(range(100, 120))
    assert pagination == {"page": 3, "items_per_page": 50, "total_pages": 3, "total_count": 120}
    with pytest.raises(ValueError):
        paginate_items(list(range(10)), 2, 50)
