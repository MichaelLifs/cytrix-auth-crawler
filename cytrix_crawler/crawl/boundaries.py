"""Pure boundary decision helpers for URL enqueue filtering."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlsplit

SUPPORTED_SCHEMES = {"http", "https"}
RejectionReason = Literal[
    "unsupported_scheme",
    "domain_not_allowed",
    "excluded_pattern",
    "max_depth_exceeded",
    "invalid_url",
]


def _get_hostname(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    return parsed.hostname.lower() if parsed.hostname else None


def is_allowed_domain(url: str, allowed_domains: list[str]) -> bool:
    """Check whether URL host is exactly in allowed domains (case-insensitive)."""
    hostname = _get_hostname(url)
    if not hostname:
        return False
    allowed = {domain.strip().lower() for domain in allowed_domains if isinstance(domain, str)}
    return hostname in allowed


def matches_exclude_pattern(url: str, exclude_patterns: list[str]) -> bool:
    """Check if URL contains any excluded substring (case-insensitive)."""
    lowered_url = url.lower()
    for pattern in exclude_patterns:
        if not isinstance(pattern, str):
            continue
        candidate = pattern.strip().lower()
        if candidate and candidate in lowered_url:
            return True
    return False


def should_enqueue_url(url: str, depth: int, config: dict[str, Any]) -> tuple[bool, RejectionReason | None]:
    """Decide if a URL is eligible for enqueueing in future crawler phases."""
    max_depth = config.get("max_depth")
    if not isinstance(max_depth, int):
        return False, "invalid_url"
    if depth > max_depth:
        return False, "max_depth_exceeded"

    try:
        parsed = urlsplit(url)
    except ValueError:
        return False, "invalid_url"

    scheme = parsed.scheme.lower()
    if scheme not in SUPPORTED_SCHEMES:
        if not scheme:
            return False, "invalid_url"
        return False, "unsupported_scheme"

    if not parsed.hostname:
        return False, "invalid_url"
    try:
        parsed.port
    except ValueError:
        return False, "invalid_url"

    allowed_domains = config.get("allowed_domains", [])
    if not is_allowed_domain(url, allowed_domains):
        return False, "domain_not_allowed"

    exclude_patterns = config.get("exclude_patterns", [])
    if matches_exclude_pattern(url, exclude_patterns):
        return False, "excluded_pattern"

    return True, None

