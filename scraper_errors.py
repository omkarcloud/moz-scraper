"""The four failure kinds moz/fetch.py raises (routes.py maps them to HTTP)."""


class UpstreamError(Exception):
    """Transport failure or 5xx — retryable."""


class Blocked(UpstreamError):
    """Anti-bot challenge / 403 / 429 — retryable on a fresh connection."""


class BadRequest(Exception):
    """Upstream rejected the params — never retried."""


class NotFound(Exception):
    """Entity / page does not exist — never retried."""
