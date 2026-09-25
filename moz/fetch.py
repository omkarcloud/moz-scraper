"""Moz transport: plain curl_cffi with a Safari TLS fingerprint through
rotating residential exits, and a headful patchright load as the last resort.

Every upstream is a server-rendered moz.com page (validated 2026-09-25):

  * REPORT  https://moz.com/domain-analysis/<domain>
    The SEO landing page behind the free Domain Authority checker: DA,
    linking root domains, ranking keywords, spam score, top pages by PA,
    top linking domains by DA and a 60-day discovered/lost linking-domain
    chart, all in the HTML. Unlike the checker's own `?site=` form (3
    reports per IP per day, then "You have exceeded your search limit"),
    the path form has no daily cap — but Cloudflare guards it:
      - Chrome/Edge TLS fingerprints from residential exits get a managed
        challenge (403, `cf-mitigated: challenge`); Safari fingerprints pass
        (safari15_5 / safari17_2_ios 4/4, safari17_0 3/4 in the bake-off).
      - A per-IP rate rule answers 429 (challenge) after ~5-6 reports from
        one exit, whatever the pacing (0 s, 3 s and 6 s gaps all stop at 6).
    So a worker thread holds one sticky residential proxy exit
    (config.moz_proxy) that is rotated after config.MOZ_REQUESTS_PER_EXIT
    reports or on any block. When config.MOZ_MAX_ATTEMPTS exits in a row are
    challenged, one headful patchright load on a fresh exit fetches the page
    (a real browser clears the managed challenge: 7/8 reports on one exit).

  * OPEN    /top-500/download/?table=top500Domains (CSV), /top-brands,
            /mozcast, /google-algorithm-change
    No rate rule seen. Fetched direct (or through config.moz_open_proxy);
    a challenged direct attempt moves to a fresh residential exit.

Nothing needs a login, cookie or token. The official Moz API (api.moz.com
JSON-RPC) and the MozBar RPC methods answer 401 without an account token,
and the Brand Authority form is Turnstile-gated, so none of them is used.

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  MozUpstreamError  transport failure / 5xx / unexpected page  — retryable
  MozBlocked        Cloudflare 403 / 429 / challenge page       — retryable on a new exit
  MozNotFound       moz.com 404 page                            — never retried
"""
import os
import random
import sys
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from scraper_errors import Blocked, NotFound, UpstreamError

SITE = "https://moz.com"
# Cloudflare's bake-off winners (2026-09-25: 4/4 each; safari17_0 went 3/4,
# safari15_3 and every chrome/edge profile 0/4); one is picked per session.
SAFARI_PROFILES = ("safari15_5", "safari17_2_ios")

PAGE_TIMEOUT = 45
BROWSER_SETTLE_TIMEOUT = 25      # seconds a fallback browser waits for the challenge to clear
BROWSER_EXITS = 2                # fresh exits one fallback call tries

PAGE_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "referer": SITE + "/",
}
CSV_HEADERS = {
    "accept": "text/csv,text/plain,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "referer": SITE + "/top500",
}

CHALLENGE_MARKERS = ("Just a moment...", "cf-chl-", "challenge-platform/h/")
LIMIT_MARKER = "You have exceeded your search limit"
NOT_FOUND_MARKER = "Roger is totally lost"


class MozUpstreamError(UpstreamError):
    """Transport failure, 5xx or an unexpected page — retryable."""


class MozBlocked(MozUpstreamError, Blocked):
    """Cloudflare challenge / 403 / 429 — retryable on a fresh exit."""


class MozNotFound(NotFound):
    """moz.com answered its 404 page — never retried."""


# ---- sessions --------------------------------------------------------------------
# One curl session per worker thread and kind (a curl handle must not be
# shared across threads). A "report" session holds one sticky exit and is
# rotated after config.MOZ_REQUESTS_PER_EXIT requests or on any failure.
_local = threading.local()


def _new_session(proxy):
    from curl_cffi import requests as curl_requests
    sess = curl_requests.Session(impersonate=random.choice(SAFARI_PROFILES))
    if proxy:
        sess.proxies = {"http": proxy, "https": proxy}
    return sess


def _session(kind, fresh_exit=False):
    """The thread's session for `kind` ("report" | "open"). fresh_exit forces
    a new residential exit (the retry path after a block)."""
    sessions = getattr(_local, "sessions", None)
    if sessions is None:
        sessions = _local.sessions = {}
    entry = sessions.get(kind)
    budget = config.MOZ_REQUESTS_PER_EXIT if kind == "report" else None
    if entry is not None and (fresh_exit or (budget and entry["used"] >= budget)):
        _drop_session(kind)
        entry = None
    if entry is None:
        if kind == "report" or fresh_exit:
            proxy = config.moz_proxy()
        else:
            proxy = config.moz_open_proxy()
        entry = {"session": _new_session(proxy), "used": 0, "proxy": proxy}
        sessions[kind] = entry
    entry["used"] += 1
    return entry["session"]


def _drop_session(kind):
    sessions = getattr(_local, "sessions", None) or {}
    entry = sessions.pop(kind, None)
    if entry is not None:
        try:
            entry["session"].close()
        except Exception:
            pass


def dump_debug(name, text):
    """Write a raw response to $MOZ_DEBUG_DIR/<name>.html."""
    dbg = os.environ.get("MOZ_DEBUG_DIR", "")
    if dbg and text:
        try:
            os.makedirs(dbg, exist_ok=True)
            with open(os.path.join(dbg, name + ".html"), "w") as f:
                f.write(text)
        except OSError:
            pass


# ---- classification ----------------------------------------------------------------

def _is_challenge(status, headers, text):
    head = (text or "")[:6000]
    if (headers or {}).get("cf-mitigated") == "challenge":
        return True
    return status in (403, 429, 503) and any(m in head for m in CHALLENGE_MARKERS)


def classify(status, headers, text, url, marker):
    """Raise the failure a response represents, or return its text."""
    text = text or ""
    if _is_challenge(status, headers, text) or status in (403, 429):
        dump_debug("blocked", text)
        raise MozBlocked(f"HTTP {status} challenge on {url}")
    if LIMIT_MARKER in text:
        raise MozBlocked(f"daily report limit hit on {url}")
    if status == 404 or NOT_FOUND_MARKER in text:
        raise MozNotFound(f"moz.com has no page at {url}")
    if status >= 500:
        raise MozUpstreamError(f"HTTP {status} on {url}")
    if status != 200:
        raise MozUpstreamError(f"HTTP {status} on {url}")
    if marker and marker not in text:
        dump_debug("unexpected", text)
        raise MozUpstreamError(f"unexpected page at {url} (layout changed?)")
    return text


# ---- fetching --------------------------------------------------------------------

def _curl_once(url, kind, headers, marker, fresh_exit):
    sess = _session(kind, fresh_exit=fresh_exit)
    try:
        resp = sess.get(url, headers=headers, timeout=PAGE_TIMEOUT, allow_redirects=True)
    except Exception as e:
        raise MozUpstreamError(f"request failed: {type(e).__name__}: {e}")
    return classify(resp.status_code, dict(resp.headers), resp.text, url, marker)


_browser_slots = threading.BoundedSemaphore(max(1, config.MOZ_BROWSER_CONCURRENCY))


def _browser_get(url, marker):
    """One headful patchright load per fresh exit (up to BROWSER_EXITS); the
    real browser clears Cloudflare's managed challenge. Returns the HTML."""
    from chrome_manager import create_scope
    from patchright_driver import PatchrightDriver

    last = None
    with _browser_slots:
        for _ in range(BROWSER_EXITS):
            driver = None
            try:
                with create_scope():
                    driver = PatchrightDriver(proxy_url=config.moz_proxy(), headless=False)
                result = driver.nav(url, referer=SITE + "/", mode="blocked",
                                    challenge_markers=CHALLENGE_MARKERS)
                html = result.get("html") or ""
                deadline = time.monotonic() + BROWSER_SETTLE_TIMEOUT
                while marker not in html and NOT_FOUND_MARKER not in html and time.monotonic() < deadline:
                    time.sleep(1.5)
                    html = driver.content() or ""
                status = 200 if marker in html else (result.get("status") or 200)
                return classify(status, {}, html, url, marker)
            except MozNotFound:
                raise
            except Exception as e:
                last = e
                print(f"moz: browser fallback failed on {url}: {type(e).__name__}: {e}")
            finally:
                if driver is not None:
                    try:
                        driver.close()
                    except Exception:
                        pass
    if isinstance(last, MozUpstreamError):
        raise last
    raise MozBlocked(f"browser fallback could not load {url}: {last}")


def fetch(path, kind="open", marker=None, headers=None, browser_fallback=False):
    """GET one moz.com page -> its text. Blocks rotate the exit; after
    config.MOZ_MAX_ATTEMPTS blocked curl attempts an optional headful
    browser load takes over."""
    url = path if path.startswith("http") else SITE + path
    headers = headers or PAGE_HEADERS
    last = None
    blocked_only = True
    for attempt in range(1, config.MOZ_MAX_ATTEMPTS + 1):
        try:
            return _curl_once(url, kind, headers, marker, fresh_exit=attempt > 1)
        except MozBlocked as e:
            last = e
        except MozUpstreamError as e:
            last = e
            blocked_only = False
        print(f"moz: attempt {attempt}/{config.MOZ_MAX_ATTEMPTS} on {path} failed: {last}")
        _drop_session(kind)
        if attempt < config.MOZ_MAX_ATTEMPTS:
            time.sleep(0.4 * attempt)
    if browser_fallback and blocked_only and config.MOZ_BROWSER_FALLBACK:
        print(f"moz: every curl attempt on {path} was challenged; loading it in a browser")
        return _browser_get(url, marker)
    raise last


def report_page(domain):
    """The /domain-analysis/<domain> HTML (resolved domain, see refs)."""
    return fetch(f"/domain-analysis/{domain}", kind="report", marker="Showing results for",
                 browser_fallback=True)


def open_page(path, marker=None, csv=False):
    return fetch(path, kind="open", marker=marker, headers=CSV_HEADERS if csv else PAGE_HEADERS,
                 browser_fallback=not csv)


# ---- memo + fan-out ---------------------------------------------------------------
# Several routes read ONE page (overview / authority / top pages / linking
# domains / link history all come from the domain report); this memo lets
# them share a fetch for a few minutes even with the response cache off.
_memo = OrderedDict()
_memo_lock = threading.Lock()
MEMO_TTL = 300
MEMO_MAX = 256


def memoized(key, fn, ttl=MEMO_TTL):
    now = time.monotonic()
    with _memo_lock:
        hit = _memo.get(key)
        if hit and now - hit[0] < ttl:
            _memo.move_to_end(key)
            return hit[1]
    value = fn()
    with _memo_lock:
        _memo[key] = (now, value)
        _memo.move_to_end(key)
        while len(_memo) > MEMO_MAX:
            _memo.popitem(last=False)
    return value


def dataset(name, build, ttl):
    """A whole parsed page shared by several routes / query variants (the
    algorithm history is filtered and paginated per request, but fetched
    once): in-process memo first, then the response cache (so pods share
    it), then `build()`."""
    from cache import cached_call
    seconds = ttl.total_seconds() if hasattr(ttl, "total_seconds") else ttl
    return memoized(("dataset", name),
                    lambda: cached_call(f"moz.dataset.{name}", {}, build, ttl),
                    ttl=min(seconds, MEMO_TTL))


def run_parallel(fns, workers):
    """Run zero-arg callables in parallel; results align with `fns`.
    Each result is ("ok", value) or ("error", exception)."""
    def guard(fn):
        try:
            return ("ok", fn())
        except Exception as e:
            return ("error", e)
    if not fns:
        return []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(fns)))) as ex:
        return list(ex.map(guard, fns))


if __name__ == "__main__":
    # Smoke test: python moz/fetch.py [domain]
    dom = sys.argv[1] if len(sys.argv) > 1 else "hubspot.com"
    html = report_page(dom)
    print(dom, len(html), "Linking Root Domains" in html)
