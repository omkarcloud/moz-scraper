"""/moz/domains/* — Moz's free domain report (/domain-analysis/<domain>):
Domain Authority, linking root domains, ranking keywords, spam score, top
pages by Page Authority, top linking domains and 60 days of discovered/lost
linking domains. All six routes read ONE upstream page per domain, shared
through fetch.memoized + the response cache.

Counts on the report are Moz's rounded display values ("455.9k" ->
455900); Domain Authority, Page Authority and the daily discovered/lost
numbers are exact.
"""
import config
from cache import cached_call
from moz import cache_config as ttl
from moz import fetch, parsers, refs


def _fetch_report(domain):
    return parsers.parse_domain_report(fetch.report_page(domain), domain)


def report(domain):
    """The parsed report for a resolved domain (see refs.resolve_domain)."""
    return fetch.memoized(("report", domain), lambda: cached_call(
        "moz.domain_report", {"domain": domain}, _fetch_report, ttl.REPORT_CACHE))


def overview(domain):
    """Everything the report shows."""
    return report(domain)


def authority(domain):
    """The DA-checker view: headline metrics only."""
    return parsers.authority_summary(report(domain))


def _failed_summary(domain, error):
    return {
        "domain": domain,
        "link": refs.site_link(domain),
        "domain_authority": None,
        "home_page_authority": None,
        "linking_root_domains": None,
        "ranking_keywords": None,
        "spam_score": None,
        "report_link": refs.report_link(domain),
        "error": error,
    }


def bulk_authority(domains):
    """Headline metrics for up to 10 domains, fetched in parallel (each worker
    on its own exit). A domain that fails carries `error` and null metrics;
    the others are still returned."""
    outcomes = fetch.run_parallel([lambda d=d: authority(d) for d in domains], config.MOZ_BULK_WORKERS)
    results = []
    for domain, (status, value) in zip(domains, outcomes):
        if status == "ok":
            results.append({**value, "error": None})
        else:
            message = str(value) or type(value).__name__
            results.append(_failed_summary(domain, message))
    return {
        "count": len(results),
        "failed_count": sum(1 for r in results if r["error"]),
        "results": results,
    }


def top_pages(domain):
    data = report(domain)
    return {
        "domain": data["domain"],
        "link": data["link"],
        "top_pages": data["top_pages"],
        "report_link": data["report_link"],
    }


def top_linking_domains(domain):
    data = report(domain)
    return {
        "domain": data["domain"],
        "link": data["link"],
        "linking_root_domains": data["linking_root_domains"],
        "top_linking_domains": data["top_linking_domains"],
        "report_link": data["report_link"],
    }


def link_history(domain):
    data = report(domain)
    return {
        "domain": data["domain"],
        "link": data["link"],
        "linking_root_domains": data["linking_root_domains"],
        **data["link_history"],
        "report_link": data["report_link"],
    }
