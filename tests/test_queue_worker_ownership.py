"""Worker ownership safety for ``mark_done`` and ``mark_failed``.

These tests prove that an item claimed by ``worker-A`` cannot be completed
by ``worker-B`` and that, after lease recovery, the original claimer can no
longer complete a reclaimed item. This is the Phase 4 race the queue
manager was tightened to address.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from cytrix_crawler.config import CONFIG
from cytrix_crawler.queue.manager import (
    claim_next,
    enqueue_url,
    mark_done,
    mark_failed,
    recover_stuck_items,
)
from cytrix_crawler.queue.models import QUEUE_COLLECTION


def _config() -> dict:
    cfg = deepcopy(CONFIG)
    cfg["allowed_domains"] = list(
        dict.fromkeys(list(cfg["allowed_domains"]) + ["localhost", "127.0.0.1", "demo-app"])
    )
    return cfg


def test_mark_done_rejects_when_wrong_worker_id(event_loop, mongo_db, scan_id) -> None:
    config = _config()

    async def scenario() -> dict:
        await enqueue_url(
            mongo_db,
            scan_id=scan_id,
            raw_url="http://localhost:8000/dashboard",
            depth=0,
            config=config,
        )
        await claim_next(mongo_db, scan_id=scan_id, worker_id="worker-A")
        ok = await mark_done(
            mongo_db,
            scan_id=scan_id,
            normalized_url="http://localhost:8000/dashboard",
            worker_id="worker-B",
        )
        doc = await mongo_db[QUEUE_COLLECTION].find_one({"scan_id": scan_id})
        return {"ok": ok, "doc": doc}

    try:
        outcome = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[QUEUE_COLLECTION].delete_many({"scan_id": scan_id})
        )

    assert outcome["ok"] is False
    assert outcome["doc"]["status"] == "in_progress"
    assert outcome["doc"]["locked_by"] == "worker-A"


def test_mark_failed_rejects_when_wrong_worker_id(event_loop, mongo_db, scan_id) -> None:
    config = _config()

    async def scenario() -> dict:
        await enqueue_url(
            mongo_db,
            scan_id=scan_id,
            raw_url="http://localhost:8000/dashboard",
            depth=0,
            config=config,
        )
        await claim_next(mongo_db, scan_id=scan_id, worker_id="worker-A")
        ok = await mark_failed(
            mongo_db,
            scan_id=scan_id,
            normalized_url="http://localhost:8000/dashboard",
            error_message="impostor",
            retryable=True,
            max_attempts=3,
            worker_id="worker-B",
        )
        doc = await mongo_db[QUEUE_COLLECTION].find_one({"scan_id": scan_id})
        return {"ok": ok, "doc": doc}

    try:
        outcome = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[QUEUE_COLLECTION].delete_many({"scan_id": scan_id})
        )

    assert outcome["ok"] is False
    assert outcome["doc"]["status"] == "in_progress"
    assert outcome["doc"]["locked_by"] == "worker-A"
    assert outcome["doc"].get("last_error") in (None,)


def test_mark_done_accepts_correct_worker_id(event_loop, mongo_db, scan_id) -> None:
    config = _config()

    async def scenario() -> dict:
        await enqueue_url(
            mongo_db,
            scan_id=scan_id,
            raw_url="http://localhost:8000/dashboard",
            depth=0,
            config=config,
        )
        await claim_next(mongo_db, scan_id=scan_id, worker_id="worker-A")
        ok = await mark_done(
            mongo_db,
            scan_id=scan_id,
            normalized_url="http://localhost:8000/dashboard",
            worker_id="worker-A",
        )
        doc = await mongo_db[QUEUE_COLLECTION].find_one({"scan_id": scan_id})
        return {"ok": ok, "doc": doc}

    try:
        outcome = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[QUEUE_COLLECTION].delete_many({"scan_id": scan_id})
        )

    assert outcome["ok"] is True
    assert outcome["doc"]["status"] == "done"
    assert outcome["doc"]["locked_by"] is None
    assert outcome["doc"]["locked_at"] is None


def test_mark_failed_retry_accepts_correct_worker_id(
    event_loop, mongo_db, scan_id
) -> None:
    config = _config()

    async def scenario() -> dict:
        await enqueue_url(
            mongo_db,
            scan_id=scan_id,
            raw_url="http://localhost:8000/dashboard",
            depth=0,
            config=config,
        )
        await claim_next(mongo_db, scan_id=scan_id, worker_id="worker-A")
        ok = await mark_failed(
            mongo_db,
            scan_id=scan_id,
            normalized_url="http://localhost:8000/dashboard",
            error_message="boom",
            retryable=True,
            max_attempts=3,
            worker_id="worker-A",
        )
        doc = await mongo_db[QUEUE_COLLECTION].find_one({"scan_id": scan_id})
        return {"ok": ok, "doc": doc}

    try:
        outcome = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[QUEUE_COLLECTION].delete_many({"scan_id": scan_id})
        )

    assert outcome["ok"] is True
    assert outcome["doc"]["status"] == "pending"
    assert outcome["doc"]["locked_by"] is None
    assert outcome["doc"]["last_error"] == "boom"


def test_stale_worker_cannot_mark_done_after_lease_recovery(
    event_loop, mongo_db, scan_id
) -> None:
    """The exact Phase 4 race: A claims, lease expires, B reclaims, A finishes late."""
    config = _config()

    async def scenario() -> dict:
        await enqueue_url(
            mongo_db,
            scan_id=scan_id,
            raw_url="http://localhost:8000/dashboard",
            depth=0,
            config=config,
        )
        await claim_next(mongo_db, scan_id=scan_id, worker_id="worker-A")
        await mongo_db[QUEUE_COLLECTION].update_one(
            {"scan_id": scan_id},
            {"$set": {"locked_at": datetime.now(timezone.utc) - timedelta(hours=1)}},
        )
        recovered = await recover_stuck_items(
            mongo_db, scan_id=scan_id, lease_timeout_seconds=60
        )
        reclaimed = await claim_next(mongo_db, scan_id=scan_id, worker_id="worker-B")
        late_ok = await mark_done(
            mongo_db,
            scan_id=scan_id,
            normalized_url="http://localhost:8000/dashboard",
            worker_id="worker-A",
        )
        doc = await mongo_db[QUEUE_COLLECTION].find_one({"scan_id": scan_id})
        return {
            "recovered": recovered,
            "reclaimed_by": reclaimed["locked_by"] if reclaimed else None,
            "late_ok": late_ok,
            "doc": doc,
        }

    try:
        outcome = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[QUEUE_COLLECTION].delete_many({"scan_id": scan_id})
        )

    assert outcome["recovered"] == 1
    assert outcome["reclaimed_by"] == "worker-B"
    assert outcome["late_ok"] is False
    assert outcome["doc"]["status"] == "in_progress"
    assert outcome["doc"]["locked_by"] == "worker-B"
