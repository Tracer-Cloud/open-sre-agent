"""Small text/value helpers (truncate, coerce, URL checks)."""

from platform.text.coercion import safe_int
from platform.text.truncation import truncate
from platform.text.url_validation import is_loopback_host, validate_https_or_loopback_http_url

__all__ = [
    "is_loopback_host",
    "safe_int",
    "truncate",
    "validate_https_or_loopback_http_url",
]
