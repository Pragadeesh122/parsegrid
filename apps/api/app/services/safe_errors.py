"""ParseGrid — error sanitation and connection-target guarding.

Single home for everything that decides what error detail may leave the
server: the /connections/test infrastructure blocklist, the classified
connection-failure messages, and DSN scrubbing for job error_message.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from app.core.config import settings

_DEFAULT_PORTS = {
    "postgresql": 5432,
    "postgres": 5432,
    "redis": 6379,
    "http": 80,
    "https": 443,
    "bolt": 7687,
    "neo4j": 7687,
}

_DSN_RE = re.compile(r"\b[a-z][a-z0-9+]*://\S+", re.IGNORECASE)
# libpq keyword DSNs ("host=... password=... dbname=...") have no scheme,
# so _DSN_RE misses them — redact the credential tokens directly.
_KEYWORD_SECRET_RE = re.compile(r"\b(password|pwd)\s*=\s*\S+", re.IGNORECASE)

_AUTH_MARKERS = ("password", "auth", "permission denied", "access denied", "unauthorized")
_REACH_MARKERS = (
    "timeout",
    "timed out",
    "could not translate",
    "name or service not known",
    "refused",
    "unreachable",
    "no route",
)


def _endpoint(url: str) -> tuple[str, int] | None:
    """(host, port) for a URL/DSN, or None when there is no host.

    Matching is by hostname string — we deliberately do not resolve DNS, so
    "localhost" and "127.0.0.1" are distinct entries. Self-hosters needing
    stricter rules add entries to CONNECTION_TEST_BLOCKLIST.
    """
    parsed = urlparse(url)
    if not parsed.hostname:
        return None
    port = parsed.port or _DEFAULT_PORTS.get((parsed.scheme or "").split("+")[0], 0)
    return (parsed.hostname.lower(), port)


def internal_endpoints() -> set[tuple[str, int]]:
    """ParseGrid's own infrastructure endpoints, derived from settings."""
    candidates = [
        settings.database_url.replace("+asyncpg", ""),
        settings.redis_url,
        settings.s3_endpoint_url or "",
        settings.neo4j_uri,
        settings.qdrant_url,
        *settings.connection_test_blocklist,
    ]
    endpoints: set[tuple[str, int]] = set()
    for url in candidates:
        ep = _endpoint(url)
        if ep:
            endpoints.add(ep)
    return endpoints


def blocked_reason(connection_string: str) -> str | None:
    """Non-None when the DSN targets ParseGrid's internal infrastructure."""
    ep = _endpoint(connection_string)
    if ep is not None and ep in internal_endpoints():
        return "Connection target is ParseGrid's internal infrastructure and is not allowed."
    return None


def sanitize_connection_error(exc: Exception) -> str:
    """Classified, detail-free message safe to return to the client."""
    text = str(exc).lower()
    if any(marker in text for marker in _AUTH_MARKERS):
        return "Connection failed: authentication failed."
    if any(marker in text for marker in _REACH_MARKERS):
        return "Connection failed: could not reach the database host."
    return "Connection failed: the database rejected the connection."


def public_error_message(exc: Exception) -> str:
    """Job error_message safe for the owning user: keeps the exception type
    and message but scrubs any embedded DSN (which may carry credentials)."""
    msg = _DSN_RE.sub("<connection-string>", str(exc))
    msg = _KEYWORD_SECRET_RE.sub(r"\1=<redacted>", msg)
    return f"{type(exc).__name__}: {msg[:300]}"
