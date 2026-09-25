"""Live endpoint smoke tests: one call per /moz/* route against a running
service, with example values proven to return data (2026-09-25). The
listing tooling reads these calls as each route's working example.

Skipped unless MOZ_BASE points at a running service:

    ONLY_SCRAPER=moz python run.py            # or any bottle runner
    MOZ_BASE=http://127.0.0.1:6002 python -m pytest moz/test_endpoints.py -q
"""
import os

import pytest

BASE = os.environ.get("MOZ_BASE", "").rstrip("/")

pytestmark = pytest.mark.skipif(not BASE, reason="set MOZ_BASE to run live endpoint tests")


def call(path, **params):
    from curl_cffi import requests
    resp = requests.get(BASE + path, params=params, timeout=300)
    assert resp.status_code == 200, f"{path} {params} -> {resp.status_code} {resp.text[:300]}"
    body = resp.json()
    assert body, f"{path} returned an empty body"
    return body


# ---- domain reports ----------------------------------------------------------------

def test_domain_reports():
    overview = call("/moz/domains/overview", domain="hubspot.com")
    assert overview["domain_authority"] and overview["top_pages"] and overview["link_history"]["days"]
    assert call("/moz/domains/authority", domain="https://www.semrush.com/features/")["domain_authority"]
    assert call("/moz/domains/top-pages", domain="hubspot.com")["top_pages"]
    assert call("/moz/domains/top-linking-domains", domain="hubspot.com")["top_linking_domains"]
    assert call("/moz/domains/link-history", domain="hubspot.com")["days"]


def test_bulk_authority():
    body = call("/moz/domains/authority/bulk", domains="nike.com,adidas.com,ikea.com")
    assert body["count"] == 3 and body["failed_count"] == 0
    assert all(r["domain_authority"] for r in body["results"])


# ---- top lists ---------------------------------------------------------------------

def test_rankings():
    assert len(call("/moz/rankings/top-domains")["domains"]) == 50
    assert call("/moz/rankings/top-domains", query="google")["domains"]
    brands = call("/moz/rankings/top-brands", page=2)
    assert brands["brands"] and brands["last_updated"]


# ---- MozCast / algorithm history -----------------------------------------------------

def test_mozcast():
    weather = call("/moz/mozcast/weather", days=7)
    assert len(weather["days"]) == 7 and weather["summary"]["average_temperature"]
    features = call("/moz/mozcast/serp-features", feature="local-packs", days=5)["features"]
    assert features[0]["slug"] == "local-packs" and len(features[0]["history"]) == 5


def test_algorithm_updates():
    assert call("/moz/google-algorithm-updates")["updates"]
    assert call("/moz/google-algorithm-updates", year=2025, status="confirmed")["updates"]
    assert call("/moz/google-algorithm-updates", query="panda")["updates"]
    assert call("/moz/google-algorithm-updates/most-volatile")["days"]
