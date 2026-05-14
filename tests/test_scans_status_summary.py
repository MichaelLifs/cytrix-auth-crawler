"""Unit tests for ``scans.update_scan_status`` and ``update_scan_summary``.

The full Mongo round-trip is covered by the integration tests; here we lock
down the exact update shape so ``config_snapshot`` and other insert-only
fields cannot drift between phases.
"""

from __future__ import annotations

import asyncio

from cytrix_crawler.storage.scans import update_scan_status, update_scan_summary


class _FakeCollection:
    def __init__(self) -> None:
        self.last_filter = None
        self.last_update = None
        self.calls = 0

    async def update_one(self, filter_query, update_query):
        self.last_filter = filter_query
        self.last_update = update_query
        self.calls += 1


class _FakeDb:
    def __init__(self) -> None:
        self.scans = _FakeCollection()

    def __getitem__(self, name: str):
        if name != "scans":
            raise KeyError(name)
        return self.scans


def test_update_scan_status_sets_status_and_updated_at_only() -> None:
    db = _FakeDb()

    asyncio.run(update_scan_status(db, scan_id="scan_demo", status="running"))

    assert db.scans.last_filter == {"scan_id": "scan_demo"}
    update = db.scans.last_update
    assert set(update.keys()) == {"$set"}
    assert update["$set"]["status"] == "running"
    assert "updated_at" in update["$set"]
    assert "config_snapshot" not in update["$set"]
    assert "summary" not in update["$set"]


def test_update_scan_summary_persists_summary_and_lifecycle_fields() -> None:
    db = _FakeDb()
    summary = {
        "login_success": True,
        "processed": 3,
        "failed": 0,
        "enqueued_links": 4,
        "pages_crawled": 3,
        "errors": 0,
        "can_resume": True,
    }

    asyncio.run(
        update_scan_summary(
            db, scan_id="scan_demo", status="completed", summary=summary
        )
    )

    assert db.scans.last_filter == {"scan_id": "scan_demo"}
    set_doc = db.scans.last_update["$set"]
    assert set_doc["status"] == "completed"
    assert set_doc["summary"] == summary
    assert set_doc["can_resume"] is True
    assert "finished_at" in set_doc
    assert "updated_at" in set_doc
    assert "config_snapshot" not in set_doc
    assert "created_at" not in set_doc


def test_update_scan_summary_supports_interrupted_status() -> None:
    db = _FakeDb()
    asyncio.run(
        update_scan_summary(
            db,
            scan_id="scan_demo",
            status="interrupted",
            summary={"processed": 1, "can_resume": True},
        )
    )

    set_doc = db.scans.last_update["$set"]
    assert set_doc["status"] == "interrupted"
    assert set_doc["can_resume"] is True
