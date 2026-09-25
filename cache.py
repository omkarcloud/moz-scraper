"""In-process response cache: cached_call(namespace, params, fn, ttl) serves
fn(**params) from memory until `ttl` (a timedelta) runs out. Keeps repeat
lookups of the same domain off moz.com while the server runs."""
import json
import threading
import time

_store = {}
_lock = threading.Lock()
MAX_ENTRIES = 2000


def cached_call(namespace, data, fn, ttl, cache=True):
    seconds = ttl.total_seconds() if hasattr(ttl, "total_seconds") else (ttl or 0)
    if not cache or not seconds:
        return fn(**data)
    key = namespace + ":" + json.dumps(data, sort_keys=True, default=str)
    now = time.monotonic()
    with _lock:
        hit = _store.get(key)
        if hit and hit[0] > now:
            return hit[1]
    result = fn(**data)
    with _lock:
        if len(_store) >= MAX_ENTRIES:
            _store.clear()
        _store[key] = (now + seconds, result)
    return result
