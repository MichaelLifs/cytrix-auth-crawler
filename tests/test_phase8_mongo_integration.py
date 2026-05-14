"""Phase 8 integration tests against MongoDB.

Covers the operational side-effects added in Phase 8:

- ``recover_stuck_items`` returns expired ``in_progress`` rows to ``pending``
  exactly the way the orchestrator startup expects.
- ``record_error`` actually persists into the ``errors`` collection.
- ``update_scan_summary`` writes a resumable, status-correct summary while
  preserving ``config_snapshot``.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from cytrix_crawler.config import CONFIG
from cytrix_crawler.queue.manager import (
    count_by_status,
    enqueue_url,
    recover_stuck_items,
)
from cytrix_crawler.queue.models import QUEUE_COLLECTION
from cytrix_crawler.storage.errors import ERRORS_COLLECTION, count_errors, record_error
from cytrix_crawler.storage.scans import (
    bootstrap_scan,
    update_scan_status,
    update_scan_summary,
)


def _config(scan_id: str) -> dict:
    cfg = deepcopy(CONFIG)
    cfg["scan_id"] = scan_id
    cfg["allowed_domains"] = list(
        dict.fromkeys(list(cfg["allowed_domains"]) + ["localhost", "127.0.0.1", "demo-app"])
    )
    return cfg


def test_recover_stuck_items_runs_before_seed_returns_pending(
    event_loop, mongo_db, scan_id
) -> None:
    config = _config(scan_id)

    async def scenario() -> dict:
        await enqueue_url(
            mongo_db,
            scan_id=scan_id,
            raw_url="http://localhost:8000/profile",
            depth=0,
            config=config,
        )
        stale_time = datetime.now(timezone.utc) - timedelta(seconds=600)
        await mongo_db[QUEUE_COLLECTION].update_one(
            {"scan_id": scan_id, "normalized_url": "http://localhost:8000/profile"},
            {
                "$set": {
                    "status": "in_progress",
                    "locked_by": "old-worker",
                    "locked_at": stale_time,
                }
            },
        )
        recovered = await recover_stuck_items(
            mongo_db, scan_id=scan_id, lease_timeout_seconds=300
        )
        counts = await count_by_status(mongo_db, scan_id=scan_id)
        doc = await mongo_db[QUEUE_COLLECTION].find_one(
            {"scan_id": scan_id, "normalized_url": "http://localhost:8000/profile"}
        )
        return {"recovered": recovered, "counts": counts, "doc": doc}

    try:
        outcome = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[QUEUE_COLLECTION].delete_many({"scan_id": scan_id})
        )

    assert outcome["recovered"] == 1
    assert outcome["counts"]["pending"] == 1
    assert outcome["counts"]["in_progress"] == 0
    assert outcome["doc"]["locked_by"] is None
    assert outcome["doc"]["locked_at"] is None


def test_record_error_persists_navigation_like_error(event_loop, mongo_db, scan_id) -> None:
    async def scenario() -> dict:
        await record_error(
            mongo_db,
            scan_id=scan_id,
            phase="navigation",
            error_type="NAVIGATION_TIMEOUT",
            message="boom timeout",
            url="http://localhost:8000/profile",
            normalized_url="http://localhost:8000/profile",
            worker_id="worker-1",
            context={"depth": 1},
        )
        total = await count_errors(mongo_db, scan_id=scan_id)
        doc = await mongo_db[ERRORS_COLLECTION].find_one({"scan_id": scan_id})
        return {"total": total, "doc": doc}

    try:
        outcome = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[ERRORS_COLLECTION].delete_many({"scan_id": scan_id})
        )

    assert outcome["total"] == 1
    doc = outcome["doc"]
    assert doc["phase"] == "navigation"
    assert doc["error_type"] == "NAVIGATION_TIMEOUT"
    assert doc["worker_id"] == "worker-1"
    assert doc["url"] == "http://localhost:8000/profile"
    assert doc["context"] == {"depth": 1}
    assert doc["occurred_at"] is not None


def test_update_scan_summary_preserves_config_snapshot_and_sets_interrupted(
    event_loop, mongo_db, scan_id
) -> None:
    config = _config(scan_id)

    async def scenario() -> dict:
        await bootstrap_scan(mongo_db, config)
        await update_scan_status(mongo_db, scan_id=scan_id, status="running")
        partial_summary = {
            "login_success": True,
            "processed": 1,
            "failed": 0,
            "enqueued_links": 2,
            "pages_crawled": 1,
            "unique_links": 2,
            "unique_forms": 0,
            "browser_requests_total": 4,
            "api_requests": 1,
            "static_requests": 2,
            "errors": 0,
            "queue_counts": {
                "pending": 1,
                "in_progress": 0,
                "done": 1,
                "failed": 0,
                "skipped": 0,
            },
            "can_resume": True,
        }
        await update_scan_summary(
            mongo_db,
            scan_id=scan_id,
            status="interrupted",
            summary=partial_summary,
        )
        return await mongo_db["scans"].find_one({"scan_id": scan_id})

    try:
        doc = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(mongo_db["scans"].delete_one({"scan_id": scan_id}))

    assert doc["status"] == "interrupted"
    assert doc["can_resume"] is True
    assert doc["summary"]["pages_crawled"] == 1
    assert doc["summary"]["api_requests"] == 1
    assert doc["summary"]["queue_counts"]["pending"] == 1
    assert doc["finished_at"] is not None
    assert doc["config_snapshot"]["scan_id"] == scan_id
    assert doc["config_snapshot"]["allowed_domains"] == config["allowed_domains"]


def test_update_scan_status_round_trip(event_loop, mongo_db, scan_id) -> None:
    config = _config(scan_id)

    async def scenario() -> dict:
        await bootstrap_scan(mongo_db, config)
        await update_scan_status(mongo_db, scan_id=scan_id, status="running")
        return await mongo_db["scans"].find_one({"scan_id": scan_id})

    try:
        doc = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(mongo_db["scans"].delete_one({"scan_id": scan_id}))

    assert doc["status"] == "running"
    assert doc["config_snapshot"]["scan_id"] == scan_id
    assert doc["summary"] is None
