"""MongoDB index bootstrap for crawler collections."""

from __future__ import annotations

from typing import Any

from pymongo import ASCENDING, DESCENDING


async def bootstrap_indexes(db: Any) -> None:
    """Create the unique and lookup indexes required by all crawler collections."""
    await db["scans"].create_index([("scan_id", ASCENDING)], unique=True)

    await db["crawl_queue"].create_index(
        [("scan_id", ASCENDING), ("normalized_url", ASCENDING)],
        unique=True,
    )
    await db["crawl_queue"].create_index(
        [
            ("scan_id", ASCENDING),
            ("status", ASCENDING),
            ("depth", ASCENDING),
            ("created_at", ASCENDING),
        ]
    )
    await db["crawl_queue"].create_index(
        [("scan_id", ASCENDING), ("status", ASCENDING), ("locked_at", ASCENDING)]
    )

    await db["pages"].create_index(
        [("scan_id", ASCENDING), ("normalized_url", ASCENDING)],
        unique=True,
    )
    await db["links"].create_index(
        [("scan_id", ASCENDING), ("normalized_url", ASCENDING)],
        unique=True,
    )
    await db["forms"].create_index([("scan_id", ASCENDING), ("form_hash", ASCENDING)], unique=True)
    await db["browser_requests"].create_index([("scan_id", ASCENDING), ("hash", ASCENDING)], unique=True)
    await db["errors"].create_index([("scan_id", ASCENDING), ("occurred_at", DESCENDING)])
