from __future__ import annotations

import asyncio
from copy import deepcopy

from cytrix_crawler.config import CONFIG
from cytrix_crawler.extract.normalize import normalize_url
from cytrix_crawler.queue.manager import enqueue_url, seed_start_url


class _FakeUpdateResult:
    def __init__(self, upserted_id: object | None) -> None:
        self.upserted_id = upserted_id


class _FakeCollection:
    """In-memory stand-in for the crawl_queue collection.

    Simulates the unique (scan_id, normalized_url) constraint and the
    ``$setOnInsert`` semantics so we can exercise enqueue branches without
    booting MongoDB.
    """

    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], dict] = {}
        self.update_calls: list[tuple[dict, dict, bool]] = []

    async def update_one(self, filter_query, update_query, upsert=False):
        self.update_calls.append((filter_query, update_query, upsert))
        key = (filter_query["scan_id"], filter_query["normalized_url"])
        if key in self.docs:
            return _FakeUpdateResult(upserted_id=None)
        if not upsert:
            return _FakeUpdateResult(upserted_id=None)
        inserted = dict(update_query.get("$setOnInsert", {}))
        self.docs[key] = inserted
        return _FakeUpdateResult(upserted_id=f"id::{key[0]}::{key[1]}")


class _FakeDb:
    def __init__(self) -> None:
        self.crawl_queue = _FakeCollection()

    def __getitem__(self, name: str):
        if name != "crawl_queue":
            raise KeyError(name)
        return self.crawl_queue


def test_enqueue_rejects_invalid_url_without_writing() -> None:
    db = _FakeDb()
    config = deepcopy(CONFIG)

    result = asyncio.run(
        enqueue_url(
            db,
            scan_id=config["scan_id"],
            raw_url="://not-a-url",
            depth=0,
            config=config,
        )
    )

    assert result == {
        "enqueued": False,
        "duplicate": False,
        "normalized_url": None,
        "status": "skipped",
        "reason": "invalid_url",
    }
    assert db.crawl_queue.update_calls == []


def test_enqueue_rejects_disallowed_domain_with_reason() -> None:
    db = _FakeDb()
    config = deepcopy(CONFIG)

    result = asyncio.run(
        enqueue_url(
            db,
            scan_id=config["scan_id"],
            raw_url="https://evil.example.com/x",
            depth=0,
            config=config,
        )
    )

    assert result["enqueued"] is False
    assert result["status"] == "skipped"
    assert result["reason"] == "domain_not_allowed"
    assert db.crawl_queue.update_calls == []


def test_enqueue_rejects_when_depth_exceeds_max() -> None:
    db = _FakeDb()
    config = deepcopy(CONFIG)

    result = asyncio.run(
        enqueue_url(
            db,
            scan_id=config["scan_id"],
            raw_url=config["start_url_after_login"],
            depth=config["max_depth"] + 1,
            config=config,
        )
    )

    assert result["enqueued"] is False
    assert result["reason"] == "max_depth_exceeded"
    assert db.crawl_queue.update_calls == []


def test_enqueue_inserts_pending_with_setoninsert_only() -> None:
    db = _FakeDb()
    config = deepcopy(CONFIG)
    expected_norm = normalize_url(config["start_url_after_login"])
    assert expected_norm is not None

    result = asyncio.run(
        enqueue_url(
            db,
            scan_id=config["scan_id"],
            raw_url=config["start_url_after_login"],
            depth=0,
            config=config,
            discovered_from=None,
        )
    )

    assert result["enqueued"] is True
    assert result["duplicate"] is False
    assert result["status"] == "pending"
    assert result["normalized_url"] == expected_norm

    assert len(db.crawl_queue.update_calls) == 1
    _, update_query, upsert = db.crawl_queue.update_calls[0]
    assert upsert is True
    assert "$set" not in update_query
    set_on_insert = update_query["$setOnInsert"]
    assert set_on_insert["status"] == "pending"
    assert set_on_insert["attempts"] == 0
    assert set_on_insert["locked_by"] is None
    assert set_on_insert["locked_at"] is None
    assert set_on_insert["depth"] == 0
    assert set_on_insert["normalized_url"] == expected_norm


def test_duplicate_enqueue_reports_duplicate_and_does_not_reset_state() -> None:
    db = _FakeDb()
    config = deepcopy(CONFIG)

    first = asyncio.run(
        enqueue_url(
            db,
            scan_id=config["scan_id"],
            raw_url=config["start_url_after_login"],
            depth=0,
            config=config,
        )
    )
    second = asyncio.run(
        enqueue_url(
            db,
            scan_id=config["scan_id"],
            raw_url=config["start_url_after_login"],
            depth=2,
            config=config,
        )
    )

    assert first["enqueued"] is True and first["duplicate"] is False
    assert second["enqueued"] is False and second["duplicate"] is True
    assert second["normalized_url"] == first["normalized_url"]

    for _, update_query, _ in db.crawl_queue.update_calls:
        assert "$set" not in update_query


def test_seed_start_url_uses_start_url_at_depth_zero() -> None:
    db = _FakeDb()
    config = deepcopy(CONFIG)
    expected_norm = normalize_url(config["start_url_after_login"])
    assert expected_norm is not None

    result = asyncio.run(seed_start_url(db, config))

    assert result["enqueued"] is True
    assert result["normalized_url"] == expected_norm
    key = (config["scan_id"], expected_norm)
    inserted = db.crawl_queue.docs[key]
    assert inserted["depth"] == 0
    assert inserted["status"] == "pending"
    assert inserted["discovered_from"] is None
