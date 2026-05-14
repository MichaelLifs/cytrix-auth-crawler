"""Operational error persistence for crawler runs.

Errors are deliberately stored as plain inserts (not upserts): each occurrence
is its own event, ordered by ``occurred_at``. Persistence is best-effort — if
writing to Mongo itself fails, we log and swallow, because losing visibility
into a single error must never crash the crawler.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

ERRORS_COLLECTION = "errors"

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def record_error(
    db: Any,
    *,
    scan_id: str,
    phase: str,
    error_type: str,
    message: str,
    url: str | None = None,
    normalized_url: str | None = None,
    worker_id: str | None = None,
    context: dict | None = None,
    traceback_text: str | None = None,
) -> None:
    """Insert one operational error document.

    Best-effort: a Mongo failure while recording is logged, never raised.
    """
    document = {
        "scan_id": scan_id,
        "phase": phase,
        "error_type": error_type,
        "message": message,
        "url": url,
        "normalized_url": normalized_url,
        "worker_id": worker_id,
        "context": context or {},
        "traceback": traceback_text,
        "occurred_at": _utcnow(),
    }
    try:
        await db[ERRORS_COLLECTION].insert_one(document)
    except Exception as exc:  # noqa: BLE001 - never let error persistence crash the crawler
        logger.warning(
            "record_error failed (scan_id=%s, phase=%s, error_type=%s): %s: %s",
            scan_id,
            phase,
            error_type,
            exc.__class__.__name__,
            exc,
        )


async def count_errors(db: Any, *, scan_id: str) -> int:
    """Return the number of error documents recorded for the scan."""
    return await db[ERRORS_COLLECTION].count_documents({"scan_id": scan_id})
