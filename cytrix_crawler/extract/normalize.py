"""URL normalization helpers used before enqueue/persistence."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

_SUPPORTED_SCHEMES = {"http", "https"}
_TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_eid", "_ga", "ref"}
_PATH_SAFE_CHARS = "/:@!$&'()*+,;=-._~"


def _normalize_path(path: str) -> str:
    normalized = path or "/"
    normalized = re.sub(r"/{2,}", "/", normalized)
    normalized = quote(unquote(normalized), safe=_PATH_SAFE_CHARS)
    if normalized != "/" and normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    return normalized or "/"


def _normalize_query(query: str) -> str:
    pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=False)
    filtered_pairs = [
        (key, value)
        for key, value in pairs
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_QUERY_KEYS
    ]
    filtered_pairs.sort(key=lambda item: (item[0], item[1]))
    return urlencode(filtered_pairs, doseq=True)


def normalize_url(raw_url: str, base_url: str | None = None) -> str | None:
    """Normalize input URL into a deterministic absolute http/https URL."""
    if not isinstance(raw_url, str):
        return None

    candidate = raw_url.strip()
    if not candidate:
        return None

    try:
        parsed_raw = urlsplit(candidate)
    except ValueError:
        return None

    if parsed_raw.scheme:
        if parsed_raw.scheme.lower() not in _SUPPORTED_SCHEMES:
            return None
        absolute_url = candidate
    else:
        if not base_url:
            return None
        try:
            parsed_base = urlsplit(base_url)
        except ValueError:
            return None
        if parsed_base.scheme.lower() not in _SUPPORTED_SCHEMES or not parsed_base.hostname:
            return None
        absolute_url = urljoin(base_url, candidate)

    try:
        parsed = urlsplit(absolute_url)
    except ValueError:
        return None

    scheme = parsed.scheme.lower()
    if scheme not in _SUPPORTED_SCHEMES:
        return None

    hostname = parsed.hostname
    if not hostname:
        return None

    try:
        port = parsed.port
    except ValueError:
        return None

    normalized_host = hostname.lower()
    if ":" in normalized_host and not normalized_host.startswith("["):
        normalized_host = f"[{normalized_host}]"

    if port is None or (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        netloc = normalized_host
    else:
        netloc = f"{normalized_host}:{port}"

    path = _normalize_path(parsed.path)
    query = _normalize_query(parsed.query)
    return urlunsplit((scheme, netloc, path, query, ""))

