"""Scan root document persistence.

A scan document goes through these states across phases:
``initialized`` -> ``authenticated`` / ``failed_login`` -> ``running`` ->
``completed`` / ``interrupted``. All updates here are single-document atomic
operations; ``config_snapshot`` is set once via ``$setOnInsert`` so subsequent
status/summary updates can never overwrite the original scan configuration.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def bootstrap_scan(db: Any, config: dict[str, Any]) -> None:
    """Idempotently create or update the scan root document."""
    now = _utcnow()
    scan_id = config["scan_id"]
    config_snapshot = deepcopy(config)

    await db["scans"].update_one(
        {"scan_id": scan_id},
        {
            "$setOnInsert": {
                "scan_id": scan_id,
                "created_at": now,
                "started_at": now,
                "config_snapshot": config_snapshot,
                "login": None,
                "summary": None,
                "can_resume": True,
            },
            "$set": {
                "updated_at": now,
                "status": "initialized",
            },
        },
        upsert=True,
    )


async def update_login_result(db: Any, scan_id: str, login_result: dict[str, Any]) -> None:
    """Persist login result and move scan status to authenticated/failed_login."""
    now = _utcnow()
    status = "authenticated" if login_result.get("success") else "failed_login"

    await db["scans"].update_one(
        {"scan_id": scan_id},
        {
            "$set": {
                "login": login_result,
                "status": status,
                "updated_at": now,
            }
        },
    )


async def update_scan_status(db: Any, *, scan_id: str, status: str) -> None:
    """Move the scan to a new lifecycle status without touching the summary."""
    await db["scans"].update_one(
        {"scan_id": scan_id},
        {"$set": {"status": status, "updated_at": _utcnow()}},
    )


async def update_scan_summary(
    db: Any,
    *,
    scan_id: str,
    status: str,
    summary: dict[str, Any],
) -> None:
    """Persist the final/interrupted crawl summary.

    Only mutates ``status``, ``summary``, ``finished_at``, ``can_resume``, and
    ``updated_at`` — ``config_snapshot`` and ``created_at`` are insert-only and
    must never be rewritten here.
    """
    now = _utcnow()
    await db["scans"].update_one(
        {"scan_id": scan_id},
        {
            "$set": {
                "status": status,
                "summary": summary,
                "finished_at": now,
                "can_resume": True,
                "updated_at": now,
            }
        },
    )
