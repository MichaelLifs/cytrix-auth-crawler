"""Per-page browser network capture.

A ``PageNetworkCapture`` is bound to exactly one Playwright ``Page``. Listeners
are attached via ``page.on(...)`` so that traffic from other pages or other
workers cannot leak into this collector. Capture is intentionally best-effort:
any failure while reading response bodies or building documents is logged and
counted, but never propagated out of :meth:`flush`. The page crawl must keep
working even if a single resource cannot be inspected.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from cytrix_crawler.dedupe.hashing import build_request_hash
from cytrix_crawler.network.classify import classify_request_response
from cytrix_crawler.storage.browser_requests import upsert_browser_requests_bulk

logger = logging.getLogger(__name__)

# Response bodies are bounded because we never need full payloads to detect
# API/static traffic or to dedupe by hash, and full bodies can be megabytes of
# binary data on real apps.
MAX_BODY_PREVIEW_BYTES = 64 * 1024

# ``response.body()`` and ``all_headers()`` can block indefinitely on third-party
# pages (long polls, bot walls, social redirects). Bound them so ``flush`` never
# stalls the worker loop or leaves queue rows in ``in_progress``.
_REQUEST_HEADERS_TIMEOUT_S = 5.0
_RESPONSE_HEADERS_TIMEOUT_S = 5.0
_RESPONSE_BODY_TIMEOUT_S = 10.0
_REQUEST_RESPONSE_TIMEOUT_S = 20.0

# Sensitive headers we refuse to store, even if the upstream server sent them.
# Authorization presence is recorded as a boolean marker in build_request_hash;
# the actual secret value is dropped here on the storage path too.
_REDACTED_REQUEST_HEADERS = frozenset({"cookie", "authorization", "proxy-authorization"})
_REDACTED_RESPONSE_HEADERS = frozenset({"set-cookie"})

_BINARY_CT_PREFIXES = ("image/", "font/", "audio/", "video/", "application/octet-stream")
_TEXT_LIKE_CT_PREFIXES = ("text/", "application/json", "application/xml", "application/javascript")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sanitize_headers(headers: dict | None, redacted: frozenset[str]) -> dict[str, str]:
    if not isinstance(headers, dict):
        return {}
    clean: dict[str, str] = {}
    for key, value in headers.items():
        if not isinstance(key, str):
            continue
        lower = key.lower()
        if lower in redacted:
            continue
        clean[lower] = "" if value is None else str(value)
    return clean


def _looks_textual(content_type: str) -> bool:
    if not content_type:
        return True
    ct = content_type.lower()
    if any(ct.startswith(prefix) for prefix in _BINARY_CT_PREFIXES):
        return False
    if any(ct.startswith(prefix) for prefix in _TEXT_LIKE_CT_PREFIXES):
        return True
    return False


def _build_body_preview(raw: bytes, content_type: str) -> tuple[str | None, bool, int]:
    size = len(raw)
    if not _looks_textual(content_type):
        return None, size > 0, size
    truncated = size > MAX_BODY_PREVIEW_BYTES
    sliced = raw[:MAX_BODY_PREVIEW_BYTES] if truncated else raw
    try:
        preview = sliced.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - defensive: never crash capture on decode
        return None, truncated, size
    return preview, truncated, size


class PageNetworkCapture:
    """Collect and persist browser network events for a single page navigation."""

    def __init__(self, *, page_url: str) -> None:
        self._page_url = page_url
        self._records: list[dict[str, Any]] = []
        self._failures = 0
        self._attached = False

    def attach(self, page: Any) -> None:
        if self._attached:
            return
        page.on("request", self._on_request)
        page.on("requestfinished", self._on_request_finished)
        page.on("requestfailed", self._on_request_failed)
        self._attached = True

    def _on_request(self, request: Any) -> None:
        if not _is_http_scheme(request):
            return
        self._records.append({"request": request, "state": "started", "seen_at": _utcnow()})

    def _on_request_finished(self, request: Any) -> None:
        record = self._find_record(request)
        if record is None:
            if not _is_http_scheme(request):
                return
            record = {"request": request, "seen_at": _utcnow()}
            self._records.append(record)
        record["state"] = "finished"

    def _on_request_failed(self, request: Any) -> None:
        record = self._find_record(request)
        if record is None:
            if not _is_http_scheme(request):
                return
            record = {"request": request, "seen_at": _utcnow()}
            self._records.append(record)
        record["state"] = "failed"
        try:
            record["failure_text"] = request.failure
        except Exception:  # noqa: BLE001 - capture is best-effort
            record["failure_text"] = None

    def _find_record(self, request: Any) -> dict[str, Any] | None:
        for record in self._records:
            if record.get("request") is request:
                return record
        return None

    async def flush(self, db: Any, *, scan_id: str) -> dict[str, int]:
        """Build documents from collected events and bulk-upsert into Mongo.

        Returns a summary of captured counts. Per-record failures are counted
        but never raised; the worker must keep processing even if one
        resource cannot be read.
        """
        docs: list[dict[str, Any]] = []
        for record in self._records:
            try:
                doc = await self._build_document(record)
            except Exception as exc:  # noqa: BLE001 - capture is best-effort
                self._failures += 1
                logger.debug(
                    "browser network capture: failed to build doc (page_url=%s): %s: %s",
                    self._page_url,
                    exc.__class__.__name__,
                    exc,
                )
                continue
            if doc is not None:
                doc["scan_id"] = scan_id
                docs.append(doc)

        captured = len(docs)
        api_count = sum(1 for d in docs if d["classification"]["is_api"])
        static_count = sum(1 for d in docs if d["classification"]["is_static"])

        try:
            await upsert_browser_requests_bulk(db, request_docs=docs)
        except Exception as exc:  # noqa: BLE001 - persistence failure must not crash worker
            self._failures += 1
            logger.warning(
                "browser network capture: bulk upsert failed (page_url=%s, attempted=%d): %s: %s",
                self._page_url,
                captured,
                exc.__class__.__name__,
                exc,
            )

        return {
            "captured_requests": captured,
            "api_requests": api_count,
            "static_requests": static_count,
            "network_capture_failures": self._failures,
        }

    async def _build_document(self, record: dict[str, Any]) -> dict[str, Any] | None:
        request = record["request"]
        state = record.get("state", "started")

        try:
            method = request.method
            url = request.url
        except Exception:  # noqa: BLE001
            return None

        try:
            raw_request_headers = await asyncio.wait_for(
                request.all_headers(),
                timeout=_REQUEST_HEADERS_TIMEOUT_S,
            )
        except Exception:  # noqa: BLE001
            raw_request_headers = _safe_attr_dict(request, "headers")

        post_data = _safe_attr(request, "post_data")

        response_status: int | None = None
        raw_response_headers: dict[str, Any] = {}
        response_content_type: str | None = None
        body_preview: str | None = None
        body_truncated = False
        body_size = 0
        failure_text = record.get("failure_text")

        response = None
        if state != "failed":
            try:
                response = await asyncio.wait_for(
                    request.response(),
                    timeout=_REQUEST_RESPONSE_TIMEOUT_S,
                )
            except Exception:  # noqa: BLE001
                response = None

        if response is not None:
            try:
                response_status = response.status
            except Exception:  # noqa: BLE001
                response_status = None
            try:
                raw_response_headers = await asyncio.wait_for(
                    response.all_headers(),
                    timeout=_RESPONSE_HEADERS_TIMEOUT_S,
                )
            except Exception:  # noqa: BLE001
                raw_response_headers = _safe_attr_dict(response, "headers")
            response_content_type = ""
            for hk, hv in raw_response_headers.items():
                if isinstance(hk, str) and hk.lower() == "content-type" and hv is not None:
                    response_content_type = str(hv)
                    break
            try:
                raw_body = await asyncio.wait_for(
                    response.body(),
                    timeout=_RESPONSE_BODY_TIMEOUT_S,
                )
            except Exception:  # noqa: BLE001 - timeouts, redirects, blocked reads
                raw_body = None
            if raw_body is not None:
                body_preview, body_truncated, body_size = _build_body_preview(
                    raw_body, response_content_type or ""
                )

        request_hash = build_request_hash(
            method=method,
            url=url,
            headers=raw_request_headers,
            post_data=post_data,
        )

        classification = classify_request_response(
            method=method,
            url=url,
            request_headers=raw_request_headers,
            response_headers=raw_response_headers if response is not None else None,
            response_content_type=response_content_type,
            response_body_preview=body_preview,
        )

        request_doc: dict[str, Any] = {
            "method": method.upper() if isinstance(method, str) else "GET",
            "url": url,
            "headers": _sanitize_headers(raw_request_headers, _REDACTED_REQUEST_HEADERS),
            "has_authorization": _has_header(raw_request_headers, "authorization"),
            "post_data_present": post_data is not None,
        }

        response_doc: dict[str, Any] | None = None
        if response is not None or state == "failed":
            response_doc = {
                "status": response_status,
                "headers": _sanitize_headers(raw_response_headers, _REDACTED_RESPONSE_HEADERS)
                if response is not None
                else {},
                "content_type": response_content_type,
                "body_preview": body_preview,
                "body_truncated": body_truncated,
                "body_size": body_size,
                "failed": state == "failed",
                "failure_text": failure_text if state == "failed" else None,
            }

        return {
            "hash": request_hash,
            "page_url": self._page_url,
            "request": request_doc,
            "response": response_doc,
            "classification": classification,
            "captured_at": record.get("seen_at") or _utcnow(),
        }


def _is_http_scheme(request: Any) -> bool:
    try:
        url = request.url
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(url, str):
        return False
    lowered = url.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _safe_attr(obj: Any, name: str) -> Any:
    try:
        return getattr(obj, name, None)
    except Exception:  # noqa: BLE001
        return None


def _safe_attr_dict(obj: Any, name: str) -> dict[str, Any]:
    value = _safe_attr(obj, name)
    return value if isinstance(value, dict) else {}


def _has_header(headers: Any, name: str) -> bool:
    if not isinstance(headers, dict):
        return False
    target = name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == target and value:
            return True
    return False
