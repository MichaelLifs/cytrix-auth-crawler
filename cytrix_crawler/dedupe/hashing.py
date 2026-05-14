"""Stable hashing for crawl artifacts."""

from __future__ import annotations

import hashlib
import json

from cytrix_crawler.extract.normalize import normalize_url


# Only headers whose semantic value affects request identity are hashed.
# Volatile or sensitive headers (cookie, authorization value, date, user-agent,
# sec-*, trace/request IDs) are intentionally excluded to keep the hash stable
# and to avoid leaking secrets into stored payloads.
_HASHED_HEADERS = ("content-type", "accept", "x-requested-with")
_MAX_POST_DATA_BYTES = 64 * 1024


def form_hash(form: dict, page_url: str) -> str:
    """Return a deterministic hash keyed by structural form semantics.

    Ordering of fields and buttons in ``form`` does not affect the result.
    Different ``page_url`` values intentionally produce different hashes for
    otherwise identical markup.
    """
    page_normalized = normalize_url(page_url)
    raw_action = form.get("action")
    action_norm = normalize_url(raw_action) if isinstance(raw_action, str) else None

    method = str(form.get("method") or "GET").strip().upper()

    pairs: list[tuple[str, str]] = []
    for field in form.get("fields") or []:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "")
        ftype = str(field.get("type") or "")
        pairs.append((name, ftype.lower()))
    pairs.sort()

    buttons_raw = form.get("buttons") or []
    buttons: list[str] = sorted(
        {str(btn).strip() for btn in buttons_raw if isinstance(btn, str) and str(btn).strip()}
    )

    payload = {
        "action": action_norm or "",
        "buttons": buttons,
        "fields": [{"name": p[0], "type": p[1]} for p in pairs],
        "method": method,
        "page_url": page_normalized or "",
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=True)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _normalize_headers(headers: dict | None) -> dict[str, str]:
    if not isinstance(headers, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        if not isinstance(key, str):
            continue
        normalized[key.strip().lower()] = "" if value is None else str(value)
    return normalized


def _truncate_post_data(post_data: str | None) -> str:
    if not isinstance(post_data, str) or not post_data:
        return ""
    encoded = post_data.encode("utf-8", errors="replace")
    if len(encoded) <= _MAX_POST_DATA_BYTES:
        return post_data
    return encoded[:_MAX_POST_DATA_BYTES].decode("utf-8", errors="replace")


def build_request_hash(
    *,
    method: str,
    url: str,
    headers: dict | None,
    post_data: str | None,
) -> str:
    """Return a deterministic hash for a captured browser request.

    The hash is stable across duplicate requests but sensitive to method, URL,
    and body changes. The header subset is restricted to identity-relevant
    fields so volatile/sensitive headers (cookie, authorization value,
    user-agent, sec-*, trace IDs) do not destabilize dedup or leak secrets.
    The presence of an Authorization header is recorded as a boolean marker,
    not its value.
    """
    normalized_method = (method or "GET").strip().upper() or "GET"
    normalized_url = normalize_url(url) or (url or "")

    header_map = _normalize_headers(headers)
    hashed_headers = {name: header_map[name] for name in _HASHED_HEADERS if name in header_map}
    has_authorization = "authorization" in header_map and bool(header_map["authorization"])

    payload = {
        "method": normalized_method,
        "url": normalized_url,
        "headers": hashed_headers,
        "authorization_present": has_authorization,
        "post_data": _truncate_post_data(post_data),
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=True)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
