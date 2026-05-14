"""Integration tests for the MongoDB-backed crawl queue.

These tests require a running MongoDB instance. They are automatically
skipped when Mongo is not reachable (see ``conftest.mongo_db``).

Each test uses a unique ``scan_id`` so tests are isolated and may run in
parallel without colliding.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from cytrix_crawler.config import CONFIG
from cytrix_crawler.queue.manager import (
    claim_next,
    count_by_status,
    enqueue_url,
    mark_done,
    mark_failed,
    recover_stuck_items,
)
from cytrix_crawler.queue.models import QUEUE_COLLECTION


def _config() -> dict:
    cfg = deepcopy(CONFIG)
    # Queue fixtures use localhost URLs; allow them alongside whatever the global CONFIG targets.
    cfg["allowed_domains"] = list(
        dict.fromkeys(list(cfg["allowed_domains"]) + ["localhost", "127.0.0.1", "demo-app"])
    )
    return cfg


def test_enqueue_url_inserts_a_pending_item(event_loop, mongo_db, scan_id) -> None:
    config = _config()

    async def scenario() -> dict:
        result = await enqueue_url(
            mongo_db,
            scan_id=scan_id,
            raw_url="http://localhost:8000/dashboard",
            depth=0,
            config=config,
        )
        doc = await mongo_db[QUEUE_COLLECTION].find_one(
            {"scan_id": scan_id, "normalized_url": result["normalized_url"]}
        )
        return {"result": result, "doc": doc}

    try:
        outcome = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[QUEUE_COLLECTION].delete_many({"scan_id": scan_id})
        )

    assert outcome["result"]["enqueued"] is True
    assert outcome["result"]["duplicate"] is False
    doc = outcome["doc"]
    assert doc is not None
    assert doc["status"] == "pending"
    assert doc["depth"] == 0
    assert doc["attempts"] == 0
    assert doc["locked_by"] is None
    assert doc["locked_at"] is None
    assert doc["normalized_url"] == "http://localhost:8000/dashboard"


def test_duplicate_enqueue_does_not_reset_state(event_loop, mongo_db, scan_id) -> None:
    config = _config()

    async def scenario() -> dict:
        await enqueue_url(
            mongo_db,
            scan_id=scan_id,
            raw_url="http://localhost:8000/dashboard",
            depth=0,
            config=config,
        )
        await mongo_db[QUEUE_COLLECTION].update_one(
            {"scan_id": scan_id},
            {"$set": {"status": "in_progress", "attempts": 5}},
        )
        second = await enqueue_url(
            mongo_db,
            scan_id=scan_id,
            raw_url="http://localhost:8000/dashboard",
            depth=2,
            config=config,
        )
        doc = await mongo_db[QUEUE_COLLECTION].find_one({"scan_id": scan_id})
        count = await mongo_db[QUEUE_COLLECTION].count_documents({"scan_id": scan_id})
        return {"second": second, "doc": doc, "count": count}

    try:
        outcome = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[QUEUE_COLLECTION].delete_many({"scan_id": scan_id})
        )

    assert outcome["count"] == 1
    assert outcome["second"]["enqueued"] is False
    assert outcome["second"]["duplicate"] is True
    assert outcome["doc"]["status"] == "in_progress"
    assert outcome["doc"]["attempts"] == 5
    assert outcome["doc"]["depth"] == 0


def test_claim_next_returns_in_progress_item(event_loop, mongo_db, scan_id) -> None:
    config = _config()

    async def scenario() -> dict:
        await enqueue_url(
            mongo_db,
            scan_id=scan_id,
            raw_url="http://localhost:8000/dashboard",
            depth=0,
            config=config,
        )
        claimed = await claim_next(mongo_db, scan_id=scan_id, worker_id="worker-1")
        empty = await claim_next(mongo_db, scan_id=scan_id, worker_id="worker-2")
        return {"claimed": claimed, "empty": empty}

    try:
        outcome = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[QUEUE_COLLECTION].delete_many({"scan_id": scan_id})
        )

    claimed = outcome["claimed"]
    assert claimed is not None
    assert claimed["status"] == "in_progress"
    assert claimed["locked_by"] == "worker-1"
    assert claimed["locked_at"] is not None
    assert claimed["attempts"] == 1
    assert outcome["empty"] is None


def test_concurrent_claim_next_never_claims_the_same_item(
    event_loop, mongo_db, scan_id
) -> None:
    config = _config()
    urls = [f"http://localhost:8000/page-{index}" for index in range(10)]

    async def scenario() -> list[dict | None]:
        for url in urls:
            await enqueue_url(
                mongo_db,
                scan_id=scan_id,
                raw_url=url,
                depth=0,
                config=config,
            )
        return await asyncio.gather(
            *(
                claim_next(mongo_db, scan_id=scan_id, worker_id=f"worker-{i}")
                for i in range(len(urls))
            )
        )

    try:
        claims = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[QUEUE_COLLECTION].delete_many({"scan_id": scan_id})
        )

    claimed_urls = [c["normalized_url"] for c in claims if c is not None]
    assert len(claimed_urls) == len(urls)
    assert len(set(claimed_urls)) == len(urls)


def test_mark_done_clears_lock_and_sets_done(event_loop, mongo_db, scan_id) -> None:
    config = _config()

    async def scenario() -> dict:
        await enqueue_url(
            mongo_db,
            scan_id=scan_id,
            raw_url="http://localhost:8000/dashboard",
            depth=0,
            config=config,
        )
        claimed = await claim_next(mongo_db, scan_id=scan_id, worker_id="w-1")
        await mark_done(
            mongo_db, scan_id=scan_id, normalized_url=claimed["normalized_url"]
        )
        return await mongo_db[QUEUE_COLLECTION].find_one({"scan_id": scan_id})

    try:
        doc = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[QUEUE_COLLECTION].delete_many({"scan_id": scan_id})
        )

    assert doc["status"] == "done"
    assert doc["locked_by"] is None
    assert doc["locked_at"] is None
    assert doc["attempts"] == 1


def test_mark_failed_retryable_returns_item_to_pending(
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
        claimed = await claim_next(mongo_db, scan_id=scan_id, worker_id="w-1")
        await mark_failed(
            mongo_db,
            scan_id=scan_id,
            normalized_url=claimed["normalized_url"],
            error_message="boom",
            retryable=True,
            max_attempts=3,
        )
        return await mongo_db[QUEUE_COLLECTION].find_one({"scan_id": scan_id})

    try:
        doc = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[QUEUE_COLLECTION].delete_many({"scan_id": scan_id})
        )

    assert doc["status"] == "pending"
    assert doc["locked_by"] is None
    assert doc["locked_at"] is None
    assert doc["last_error"] == "boom"
    assert doc["attempts"] == 1


def test_mark_failed_non_retryable_sets_failed(
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
        claimed = await claim_next(mongo_db, scan_id=scan_id, worker_id="w-1")
        await mark_failed(
            mongo_db,
            scan_id=scan_id,
            normalized_url=claimed["normalized_url"],
            error_message="fatal",
            retryable=False,
            max_attempts=3,
        )
        return await mongo_db[QUEUE_COLLECTION].find_one({"scan_id": scan_id})

    try:
        doc = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[QUEUE_COLLECTION].delete_many({"scan_id": scan_id})
        )

    assert doc["status"] == "failed"
    assert doc["last_error"] == "fatal"
    assert doc["locked_by"] is None
    assert doc["locked_at"] is None


def test_mark_failed_exhausted_retries_sets_failed(
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
        for index in range(3):
            await claim_next(
                mongo_db, scan_id=scan_id, worker_id=f"worker-{index}"
            )
            await mark_failed(
                mongo_db,
                scan_id=scan_id,
                normalized_url="http://localhost:8000/dashboard",
                error_message=f"err-{index}",
                retryable=True,
                max_attempts=3,
            )
        return await mongo_db[QUEUE_COLLECTION].find_one({"scan_id": scan_id})

    try:
        doc = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[QUEUE_COLLECTION].delete_many({"scan_id": scan_id})
        )

    assert doc["status"] == "failed"
    assert doc["attempts"] == 3
    assert doc["last_error"] == "err-2"


def test_recover_stuck_items_returns_expired_to_pending(
    event_loop, mongo_db, scan_id
) -> None:
    config = _config()

    async def scenario() -> dict:
        await enqueue_url(
            mongo_db,
            scan_id=scan_id,
            raw_url="http://localhost:8000/stuck",
            depth=0,
            config=config,
        )
        await enqueue_url(
            mongo_db,
            scan_id=scan_id,
            raw_url="http://localhost:8000/fresh",
            depth=0,
            config=config,
        )
        stale_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        fresh_time = datetime.now(timezone.utc)
        await mongo_db[QUEUE_COLLECTION].update_one(
            {"scan_id": scan_id, "normalized_url": "http://localhost:8000/stuck"},
            {"$set": {"status": "in_progress", "locked_by": "w", "locked_at": stale_time}},
        )
        await mongo_db[QUEUE_COLLECTION].update_one(
            {"scan_id": scan_id, "normalized_url": "http://localhost:8000/fresh"},
            {"$set": {"status": "in_progress", "locked_by": "w", "locked_at": fresh_time}},
        )
        recovered = await recover_stuck_items(
            mongo_db, scan_id=scan_id, lease_timeout_seconds=60
        )
        stuck = await mongo_db[QUEUE_COLLECTION].find_one(
            {"scan_id": scan_id, "normalized_url": "http://localhost:8000/stuck"}
        )
        fresh = await mongo_db[QUEUE_COLLECTION].find_one(
            {"scan_id": scan_id, "normalized_url": "http://localhost:8000/fresh"}
        )
        return {"recovered": recovered, "stuck": stuck, "fresh": fresh}

    try:
        outcome = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[QUEUE_COLLECTION].delete_many({"scan_id": scan_id})
        )

    assert outcome["recovered"] == 1
    assert outcome["stuck"]["status"] == "pending"
    assert outcome["stuck"]["locked_by"] is None
    assert outcome["stuck"]["locked_at"] is None
    assert outcome["fresh"]["status"] == "in_progress"


def test_count_by_status_returns_expected_counts(
    event_loop, mongo_db, scan_id
) -> None:
    config = _config()

    async def scenario() -> dict:
        urls = [f"http://localhost:8000/p{index}" for index in range(4)]
        for url in urls:
            await enqueue_url(
                mongo_db,
                scan_id=scan_id,
                raw_url=url,
                depth=0,
                config=config,
            )
        c1 = await claim_next(mongo_db, scan_id=scan_id, worker_id="w-1")
        await mark_done(
            mongo_db, scan_id=scan_id, normalized_url=c1["normalized_url"]
        )
        c2 = await claim_next(mongo_db, scan_id=scan_id, worker_id="w-2")
        await mark_failed(
            mongo_db,
            scan_id=scan_id,
            normalized_url=c2["normalized_url"],
            error_message="x",
            retryable=False,
            max_attempts=3,
        )
        c3 = await claim_next(mongo_db, scan_id=scan_id, worker_id="w-3")
        # leave c3 in_progress; one item remains pending
        assert c3 is not None
        return await count_by_status(mongo_db, scan_id=scan_id)

    try:
        counts = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[QUEUE_COLLECTION].delete_many({"scan_id": scan_id})
        )

    assert counts == {
        "pending": 1,
        "in_progress": 1,
        "done": 1,
        "failed": 1,
        "skipped": 0,
    }
