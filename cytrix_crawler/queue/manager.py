"""MongoDB-backed crawl queue operations.

Design highlights (kept here because they affect correctness, not just code):

- Uniqueness is enforced by the ``(scan_id, normalized_url)`` unique index
  defined in ``storage.indexes``. Enqueue uses ``$setOnInsert`` so duplicate
  enqueue calls never reset progress, status, attempts, or lock.
- ``claim_next`` uses ``find_one_and_update`` with a sort, which is the
  standard MongoDB primitive for race-free work-claim queues.
- Worker completion (``mark_done`` / ``mark_failed``) optionally filters by
  ``status == in_progress`` AND ``locked_by == worker_id``. This prevents a
  stale worker from completing an item that has already been reclaimed (e.g.
  after ``recover_stuck_items`` returned its lease to ``pending``).
- ``recover_stuck_items`` is a single atomic ``update_many`` over expired
  leases. No multi-document transactions are used anywhere.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

from pymongo import ASCENDING, ReturnDocument

from cytrix_crawler.crawl.boundaries import should_enqueue_url
from cytrix_crawler.extract.normalize import normalize_url
from cytrix_crawler.queue.models import (
    QUEUE_COLLECTION,
    QUEUE_STATUSES,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_IN_PROGRESS,
    STATUS_PENDING,
    STATUS_SKIPPED,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _queue(db: Any):
    return db[QUEUE_COLLECTION]


def _rejected_result(reason: str) -> dict[str, Any]:
    return {
        "enqueued": False,
        "duplicate": False,
        "normalized_url": None,
        "status": STATUS_SKIPPED,
        "reason": reason,
    }


def _ownership_filter(
    *, scan_id: str, normalized_url: str, worker_id: str | None
) -> dict[str, Any]:
    """Build the update filter for worker completion writes.

    When ``worker_id`` is provided, the filter requires the item to still be
    in ``in_progress`` with this exact worker holding the lock. This is the
    only guard against a stale worker overwriting state after lease recovery.
    """
    filter_query: dict[str, Any] = {"scan_id": scan_id, "normalized_url": normalized_url}
    if worker_id is not None:
        filter_query["status"] = STATUS_IN_PROGRESS
        filter_query["locked_by"] = worker_id
    return filter_query


async def enqueue_url(
    db: Any,
    *,
    scan_id: str,
    raw_url: str,
    depth: int,
    config: dict[str, Any],
    discovered_from: str | None = None,
) -> dict[str, Any]:
    """Idempotently enqueue a URL for a scan.

    Duplicates are no-ops: ``$setOnInsert`` ensures we never reset queue state
    for an existing document.
    """
    normalized = normalize_url(raw_url)
    if normalized is None:
        logger.debug(
            "enqueue_url rejected scan_id=%s reason=invalid_url raw=%r",
            scan_id,
            raw_url,
        )
        return _rejected_result("invalid_url")

    allowed, reason = should_enqueue_url(normalized, depth, config)
    if not allowed:
        logger.debug(
            "enqueue_url rejected scan_id=%s normalized=%s depth=%s reason=%s",
            scan_id,
            normalized,
            depth,
            reason or "rejected",
        )
        return _rejected_result(reason or "rejected")

    now = _utcnow()
    result = await _queue(db).update_one(
        {"scan_id": scan_id, "normalized_url": normalized},
        {
            "$setOnInsert": {
                "scan_id": scan_id,
                "url": raw_url,
                "normalized_url": normalized,
                "depth": depth,
                "status": STATUS_PENDING,
                "locked_by": None,
                "locked_at": None,
                "attempts": 0,
                "discovered_from": discovered_from,
                "last_error": None,
                "created_at": now,
                "updated_at": now,
            }
        },
        upsert=True,
    )

    inserted = result.upserted_id is not None
    if inserted:
        logger.info(
            "enqueue_url new scan_id=%s depth=%s url=%s discovered_from=%s",
            scan_id,
            depth,
            raw_url,
            discovered_from,
        )
    else:
        logger.debug(
            "enqueue_url duplicate scan_id=%s normalized=%s depth=%s",
            scan_id,
            normalized,
            depth,
        )
    return {
        "enqueued": inserted,
        "duplicate": not inserted,
        "normalized_url": normalized,
        "status": STATUS_PENDING,
        "reason": None,
    }


async def claim_next(
    db: Any,
    *,
    scan_id: str,
    worker_id: str,
) -> dict[str, Any] | None:
    """Atomically claim one pending item for the given worker."""
    now = _utcnow()
    doc = await _queue(db).find_one_and_update(
        {"scan_id": scan_id, "status": STATUS_PENDING},
        {
            "$set": {
                "status": STATUS_IN_PROGRESS,
                "locked_by": worker_id,
                "locked_at": now,
                "updated_at": now,
            },
            "$inc": {"attempts": 1},
        },
        sort=[("depth", ASCENDING), ("created_at", ASCENDING)],
        return_document=ReturnDocument.AFTER,
    )
    if doc is not None:
        logger.debug(
            "claim_next scan_id=%s worker_id=%s url=%s depth=%s attempts=%s",
            scan_id,
            worker_id,
            doc.get("url"),
            doc.get("depth"),
            doc.get("attempts"),
        )
    else:
        logger.debug("claim_next scan_id=%s worker_id=%s no pending item", scan_id, worker_id)
    return doc


async def mark_done(
    db: Any,
    *,
    scan_id: str,
    normalized_url: str,
    worker_id: str | None = None,
) -> bool:
    """Mark a claimed item as successfully completed.

    Returns ``True`` only if the update matched a document. When ``worker_id``
    is provided, the update is rejected unless this worker still owns the lock.
    """
    now = _utcnow()
    result = await _queue(db).update_one(
        _ownership_filter(scan_id=scan_id, normalized_url=normalized_url, worker_id=worker_id),
        {
            "$set": {
                "status": STATUS_DONE,
                "locked_by": None,
                "locked_at": None,
                "updated_at": now,
            }
        },
    )
    matched = result.matched_count > 0
    if matched:
        logger.info(
            "mark_done scan_id=%s worker_id=%s normalized_url=%s",
            scan_id,
            worker_id,
            normalized_url,
        )
    else:
        logger.warning(
            "mark_done no match (stale lock?) scan_id=%s worker_id=%s normalized_url=%s",
            scan_id,
            worker_id,
            normalized_url,
        )
    return matched


async def mark_failed(
    db: Any,
    *,
    scan_id: str,
    normalized_url: str,
    error_message: str,
    retryable: bool,
    max_attempts: int = 3,
    worker_id: str | None = None,
) -> bool:
    """Resolve a failing item as retryable (-> pending) or terminal failed.

    Returns ``True`` only if the update matched a document. When ``worker_id``
    is provided, the update is rejected unless this worker still owns the lock,
    which prevents a stale worker from overwriting a reclaimed item.
    """
    now = _utcnow()
    current = await _queue(db).find_one(
        {"scan_id": scan_id, "normalized_url": normalized_url},
        projection={"attempts": 1},
    )
    attempts = (current or {}).get("attempts", 0)

    should_retry = retryable and attempts < max_attempts
    next_status = STATUS_PENDING if should_retry else STATUS_FAILED

    result = await _queue(db).update_one(
        _ownership_filter(scan_id=scan_id, normalized_url=normalized_url, worker_id=worker_id),
        {
            "$set": {
                "status": next_status,
                "locked_by": None,
                "locked_at": None,
                "last_error": error_message,
                "updated_at": now,
            }
        },
    )
    matched = result.matched_count > 0
    if matched:
        logger.info(
            "mark_failed scan_id=%s worker_id=%s normalized_url=%s next_status=%s "
            "retryable=%s attempts=%s error=%s",
            scan_id,
            worker_id,
            normalized_url,
            next_status,
            retryable,
            attempts,
            error_message,
        )
    else:
        logger.warning(
            "mark_failed no match scan_id=%s worker_id=%s normalized_url=%s",
            scan_id,
            worker_id,
            normalized_url,
        )
    return matched


async def mark_skipped(
    db: Any,
    *,
    scan_id: str,
    normalized_url: str,
    reason: str,
) -> None:
    """Mark an item as skipped with a structured reason."""
    now = _utcnow()
    await _queue(db).update_one(
        {"scan_id": scan_id, "normalized_url": normalized_url},
        {
            "$set": {
                "status": STATUS_SKIPPED,
                "skip_reason": reason,
                "locked_by": None,
                "locked_at": None,
                "updated_at": now,
            }
        },
    )
    logger.info(
        "mark_skipped scan_id=%s normalized_url=%s reason=%s",
        scan_id,
        normalized_url,
        reason,
    )


async def recover_stuck_items(
    db: Any,
    *,
    scan_id: str,
    lease_timeout_seconds: int,
) -> int:
    """Return ``in_progress`` items whose lease has expired back to ``pending``."""
    cutoff = _utcnow() - timedelta(seconds=lease_timeout_seconds)
    result = await _queue(db).update_many(
        {
            "scan_id": scan_id,
            "status": STATUS_IN_PROGRESS,
            "locked_at": {"$lt": cutoff},
        },
        {
            "$set": {
                "status": STATUS_PENDING,
                "locked_by": None,
                "locked_at": None,
                "updated_at": _utcnow(),
            }
        },
    )
    n = result.modified_count
    if n:
        logger.info(
            "recover_stuck_items scan_id=%s returned %s row(s) to pending (lease>%ss)",
            scan_id,
            n,
            lease_timeout_seconds,
        )
    else:
        logger.debug(
            "recover_stuck_items scan_id=%s no expired in_progress leases",
            scan_id,
        )
    return n


async def count_by_status(db: Any, *, scan_id: str) -> dict[str, int]:
    """Return queue item counts grouped by status for the given scan."""
    counts: dict[str, int] = {status: 0 for status in QUEUE_STATUSES}
    pipeline = [
        {"$match": {"scan_id": scan_id}},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    async for row in _queue(db).aggregate(pipeline):
        counts[row["_id"]] = row["count"]
    return counts


async def seed_start_url(db: Any, config: dict[str, Any]) -> dict[str, Any]:
    """Enqueue the configured start URL at depth 0."""
    return await enqueue_url(
        db,
        scan_id=config["scan_id"],
        raw_url=config["start_url_after_login"],
        depth=0,
        config=config,
        discovered_from=None,
    )
