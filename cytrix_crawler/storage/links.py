"""links collection persistence (discovery ledger, separate from crawl_queue)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

LINKS_COLLECTION = "links"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def upsert_link(
    db: Any,
    *,
    scan_id: str,
    normalized_url: str,
    first_seen_on: str | None,
    depth_first_seen: int | None,
) -> None:
    now = _utcnow()
    set_on_insert = {
        "scan_id": scan_id,
        "normalized_url": normalized_url,
        "depth_first_seen": depth_first_seen,
        "first_seen_at": now,
        "first_seen_on": first_seen_on,
    }
    await db[LINKS_COLLECTION].update_one(
        {"scan_id": scan_id, "normalized_url": normalized_url},
        {"$setOnInsert": set_on_insert},
        upsert=True,
    )


async def upsert_links_bulk(
    db: Any,
    *,
    scan_id: str,
    links: list[dict[str, Any]],
) -> int:
    if not links:
        return 0
    attempted = 0
    for entry in links:
        attempted += 1
        normalized = entry.get("normalized_url")
        if not isinstance(normalized, str) or not normalized:
            continue
        await upsert_link(
            db,
            scan_id=scan_id,
            normalized_url=normalized,
            first_seen_on=entry.get("first_seen_on"),
            depth_first_seen=entry.get("depth_first_seen"),
        )
    return attempted


async def count_links(db: Any, *, scan_id: str) -> int:
    return await db[LINKS_COLLECTION].count_documents({"scan_id": scan_id})
