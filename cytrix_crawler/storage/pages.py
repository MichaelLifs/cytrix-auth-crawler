"""Page document persistence with idempotent inserts and refreshed crawl snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

PAGES_COLLECTION = "pages"

_INSERT_ONLY_FIELDS = ("scan_id", "normalized_url")
_MUTABLE_FIELDS = (
    "buttons_count",
    "content_type",
    "crawled_at",
    "depth",
    "final_url",
    "forms_count",
    "links_count",
    "metadata",
    "scripts_count",
    "status_code",
    "title",
    "url",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def upsert_page(db: Any, *, scan_id: str, page_doc: dict[str, Any]) -> None:
    normalized_url = page_doc["normalized_url"]
    now = _utcnow()

    set_on_insert = {field: page_doc.get(field) for field in _INSERT_ONLY_FIELDS}
    set_on_insert["scan_id"] = scan_id
    set_on_insert["normalized_url"] = normalized_url
    set_on_insert["first_seen_at"] = now

    mutable = {field: page_doc.get(field) for field in _MUTABLE_FIELDS}
    mutable["updated_at"] = now

    await db[PAGES_COLLECTION].update_one(
        {"scan_id": scan_id, "normalized_url": normalized_url},
        {"$setOnInsert": set_on_insert, "$set": mutable},
        upsert=True,
    )


async def count_pages(db: Any, *, scan_id: str) -> int:
    return await db[PAGES_COLLECTION].count_documents({"scan_id": scan_id})
