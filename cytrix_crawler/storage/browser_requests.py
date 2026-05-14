"""browser_requests collection persistence.

Captured browser network exchanges are deduplicated by ``(scan_id, hash)``.
The unique index lives in ``cytrix_crawler.storage.indexes`` so the upsert
relies on Mongo to keep ``first_seen_at`` insert-only while letting mutable
fields refresh on every re-capture. No multi-document transactions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo import UpdateOne

BROWSER_REQUESTS_COLLECTION = "browser_requests"

_INSERT_ONLY_FIELDS = ("scan_id", "hash", "first_seen_at")
_MUTABLE_FIELDS = ("page_url", "request", "response", "classification", "captured_at")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _build_update_ops(request_doc: dict[str, Any], *, now: datetime) -> dict[str, dict[str, Any]]:
    set_on_insert = {
        "scan_id": request_doc["scan_id"],
        "hash": request_doc["hash"],
        "first_seen_at": now,
    }
    mutable = {field: request_doc.get(field) for field in _MUTABLE_FIELDS}
    mutable["updated_at"] = now
    return {"$setOnInsert": set_on_insert, "$set": mutable}


async def upsert_browser_request(db: Any, *, request_doc: dict[str, Any]) -> None:
    """Upsert a single captured request by ``(scan_id, hash)``."""
    scan_id = request_doc["scan_id"]
    request_hash = request_doc["hash"]
    now = _utcnow()
    await db[BROWSER_REQUESTS_COLLECTION].update_one(
        {"scan_id": scan_id, "hash": request_hash},
        _build_update_ops(request_doc, now=now),
        upsert=True,
    )


async def upsert_browser_requests_bulk(db: Any, *, request_docs: list[dict[str, Any]]) -> int:
    """Bulk upsert captured requests. Returns the number attempted."""
    if not request_docs:
        return 0
    now = _utcnow()
    operations: list[UpdateOne] = []
    for doc in request_docs:
        if not isinstance(doc, dict):
            continue
        scan_id = doc.get("scan_id")
        request_hash = doc.get("hash")
        if not isinstance(scan_id, str) or not isinstance(request_hash, str):
            continue
        operations.append(
            UpdateOne(
                {"scan_id": scan_id, "hash": request_hash},
                _build_update_ops(doc, now=now),
                upsert=True,
            )
        )
    if not operations:
        return 0
    await db[BROWSER_REQUESTS_COLLECTION].bulk_write(operations, ordered=False)
    return len(operations)


async def count_browser_requests(db: Any, *, scan_id: str) -> dict[str, int]:
    """Return ``{total, api, static}`` counts for a scan."""
    collection = db[BROWSER_REQUESTS_COLLECTION]
    total = await collection.count_documents({"scan_id": scan_id})
    api = await collection.count_documents(
        {"scan_id": scan_id, "classification.is_api": True}
    )
    static = await collection.count_documents(
        {"scan_id": scan_id, "classification.is_static": True}
    )
    return {"total": total, "api": api, "static": static}
