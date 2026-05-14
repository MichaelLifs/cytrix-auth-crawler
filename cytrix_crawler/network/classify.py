"""Heuristic classification of captured browser requests as API-like or static.

These are intentionally simple, deterministic heuristics. A request can be
neither, one, or (rarely) both. Treat the result as guidance, not ground truth.
"""

from __future__ import annotations

from urllib.parse import urlsplit

_STATIC_EXTENSIONS = (
    ".css",
    ".js",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".map",
)

_STATIC_CONTENT_TYPE_PREFIXES = ("image/", "font/", "text/css")
_API_CONTENT_TYPE_MARKERS = ("application/json", "application/xml", "text/xml", "graphql")
_API_URL_MARKERS = ("/api/", "/graphql", "/rest/", "/v1/", "/v2/")
_API_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_JS_CONTENT_TYPES = ("application/javascript", "text/javascript", "application/x-javascript")
_JS_ASSET_HINTS = ("/static/", "/assets/", "/dist/", "/build/", "/_next/", "bundle", "chunk")


def _url_path(url: str | None) -> str:
    if not isinstance(url, str) or not url:
        return ""
    try:
        return urlsplit(url).path or ""
    except ValueError:
        return ""


def _header_value(headers: dict | None, name: str) -> str:
    if not isinstance(headers, dict):
        return ""
    target = name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == target and value is not None:
            return str(value)
    return ""


def _content_type_value(response_headers: dict | None, response_content_type: str | None) -> str:
    if isinstance(response_content_type, str) and response_content_type:
        return response_content_type.lower()
    return _header_value(response_headers, "content-type").lower()


def _looks_like_json_body(body_preview: str | None) -> bool:
    if not isinstance(body_preview, str):
        return False
    stripped = body_preview.lstrip()
    if not stripped:
        return False
    return stripped[0] in "{["


def _is_static(url: str, response_content_type: str) -> bool:
    path = _url_path(url).lower()
    if any(path.endswith(ext) for ext in _STATIC_EXTENSIONS):
        return True
    if any(response_content_type.startswith(prefix) for prefix in _STATIC_CONTENT_TYPE_PREFIXES):
        return True
    if any(response_content_type.startswith(js_ct) for js_ct in _JS_CONTENT_TYPES):
        if any(hint in path for hint in _JS_ASSET_HINTS) or path.endswith(".js"):
            return True
    return False


def _is_api(
    method: str,
    url: str,
    request_headers: dict | None,
    response_content_type: str,
    response_body_preview: str | None,
) -> bool:
    if method.upper() in _API_METHODS:
        return True

    request_content_type = _header_value(request_headers, "content-type").lower()
    accept = _header_value(request_headers, "accept").lower()
    for marker in _API_CONTENT_TYPE_MARKERS:
        if marker in request_content_type or marker in response_content_type:
            return True
    if "application/json" in accept:
        return True

    lower_url = (url or "").lower()
    if any(marker in lower_url for marker in _API_URL_MARKERS):
        return True

    if _looks_like_json_body(response_body_preview):
        return True
    return False


def classify_request_response(
    *,
    method: str,
    url: str,
    request_headers: dict | None,
    response_headers: dict | None,
    response_content_type: str | None,
    response_body_preview: str | None,
) -> dict:
    """Return ``{"is_api": bool, "is_static": bool}`` for a captured exchange.

    The two flags are independent: a request can be neither (e.g. an HTML
    navigation) or, in rare cases, both. Static classification is biased
    toward URL extensions and content-type prefixes; API classification toward
    methods, content-types, and URL markers.
    """
    method_norm = (method or "GET").upper()
    content_type = _content_type_value(response_headers, response_content_type)

    is_static = _is_static(url or "", content_type)
    is_api = _is_api(method_norm, url or "", request_headers, content_type, response_body_preview)
    return {"is_api": is_api, "is_static": is_static}
