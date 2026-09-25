"""Moz input parsing: ONE param per input that auto-detects its forms
(tripadvisor QueryOrLinkField convention — never a sibling `url`/`domain`
pair), plus the lookup tables the routes share.

  domain   hubspot.com | www.hubspot.com | blog.hubspot.com
           | https://www.hubspot.com/marketing?utm=x   (any page link -> its host)
           | https://moz.com/domain-analysis/hubspot.com  (a Moz report link)
           | münchen.de                                   (IDN -> punycode)
  feature  local-packs | Local Packs | lcl_new           (MozCast SERP feature)

Moz reports Domain Authority, linking root domains, ranking keywords and
spam score for the ROOT domain: blog.hubspot.com and hubspot.com answer the
same numbers. The host is still passed through as typed (minus a leading
`www.`) so the report heading matches what the caller asked for.
"""
import ipaddress
import re
from urllib.parse import parse_qs, unquote, urlparse

SITE = "https://moz.com"
REPORT_PATH = "/domain-analysis/"

_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_TLD_RE = re.compile(r"^(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$")


def resolve_domain(value):
    """A domain, any page link on it, or a moz.com/domain-analysis/<domain>
    link -> the lower-cased host without a leading `www.`. Raises ValueError
    with a user-facing message for anything that is not a public hostname."""
    raw = (value or "").strip()
    if not raw:
        raise ValueError("Must be a domain (hubspot.com) or a link to a page on it.")
    candidate = raw
    if "://" not in candidate and not candidate.startswith("//"):
        candidate = "https://" + candidate
    elif candidate.startswith("//"):
        candidate = "https:" + candidate
    try:
        parsed = urlparse(candidate)
        host = parsed.hostname or ""
    except ValueError:
        raise ValueError(f"'{raw}' is not a valid domain or link.")
    host = host.strip().rstrip(".").lower()
    # A Moz report link names the analysed domain in its path (or, for the
    # checker's own form, in `?site=`).
    if host in ("moz.com", "www.moz.com") and parsed.path.startswith(REPORT_PATH):
        inner = unquote(parsed.path[len(REPORT_PATH):]).strip("/")
        if inner:
            return resolve_domain(inner)
    if host in ("moz.com", "www.moz.com") and parsed.path.rstrip("/") == REPORT_PATH.rstrip("/"):
        site = (parse_qs(parsed.query).get("site") or [""])[0].strip()
        if site:
            return resolve_domain(site)
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError(f"'{raw}' is not a valid domain name.")
    if host.startswith("www."):
        host = host[4:]
    if _is_ip(host):
        raise ValueError("IP addresses have no Moz report; pass a domain name (hubspot.com).")
    labels = host.split(".")
    if len(labels) < 2 or len(host) > 253 or not all(_LABEL_RE.match(label) for label in labels) \
            or not _TLD_RE.match(labels[-1]):
        raise ValueError(f"'{raw}' is not a valid domain (e.g. hubspot.com).")
    return host


def _is_ip(host):
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def report_link(domain):
    return f"{SITE}{REPORT_PATH}{domain}"


def site_link(host_or_path):
    """'www.hubspot.com/pricing' (scheme-less, as Moz prints it) -> https link."""
    value = (host_or_path or "").strip()
    if not value:
        return None
    if value.startswith("//"):
        return "https:" + value
    if value.startswith(("http://", "https://")):
        return value
    return "https://" + value


# ---- MozCast SERP features --------------------------------------------------------
# public slug -> (Moz feature token in the page data, display name)
SERP_FEATURES = {
    "local-packs": ("lcl_new", "Local Packs"),
    "featured-snippets": ("answer2", "Featured Snippets"),
    "knowledge-graph": ("kgraph", "Knowledge Graph"),
    "videos": ("videos", "Videos"),
    "https-results": ("https", "HTTPS Results"),
    "reviews": ("reviews", "Reviews (Stars)"),
    "sitelinks": ("sitelinks", "Sitelinks"),
}
# Literal copy of the keys (schemas + the listing tooling read it as an enum).
SERP_FEATURE_SLUGS = ["local-packs", "featured-snippets", "knowledge-graph", "videos",
                      "https-results", "reviews", "sitelinks"]
FEATURE_SLUG_BY_TOKEN ={token: slug for slug, (token, _name) in SERP_FEATURES.items()}


def resolve_feature(value):
    """'local-packs' | 'Local Packs' | 'lcl_new' -> 'local-packs'."""
    key = " ".join((value or "").strip().lower().replace("_", " ").replace("-", " ").split())
    for slug, (token, name) in SERP_FEATURES.items():
        if key in (slug.replace("-", " "), token.replace("_", " "), name.lower(),
                   name.lower().replace("(", "").replace(")", "")):
            return slug
    raise ValueError(f"Must be one of: {', '.join(SERP_FEATURES)}.")


# ---- Google algorithm history -----------------------------------------------------
# Source-link suffix "(SEL)" -> publisher name. Unknown suffixes pass through.
PUBLISHERS = {
    "SEL": "Search Engine Land",
    "SER": "Search Engine Roundtable",
    "SEROUNDTABLE": "Search Engine Roundtable",
    "SEJ": "Search Engine Journal",
    "SEW": "Search Engine Watch",
    "WMW": "WebmasterWorld",
    "GSQI": "GSQi",
    "SEM POST": "The SEM Post",
    "MATTCUTTS.COM": "Matt Cutts",
    "MARIEHAYNES.COM": "Marie Haynes",
    "RANKRANGER": "Rank Ranger",
}
UPDATE_STATUSES = ["confirmed", "unconfirmed"]
