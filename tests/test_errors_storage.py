"""Unit tests for the operational ``errors`` collection writer."""

from __future__ import annotations

import asyncio

from cytrix_crawler.storage.errors import (
    ERRORS_COLLECTION,
    count_errors,
    record_error,
)


class _FakeErrors:
    def __init__(self) -> None:
        self.inserted: list[dict] = []
        self.count_filter = None
        self.raise_on_insert = False

    async def insert_one(self, document):
        if self.raise_on_insert:
            raise RuntimeError("simulated mongo failure")
        self.inserted.append(document)

    async def count_documents(self, filter_query):
        self.count_filter = filter_query
        return len(
            [d for d in self.inserted if d.get("scan_id") == filter_query.get("scan_id")]
        )


class _FakeDb:
    def __init__(self) -> None:
        self.errors = _FakeErrors()

    def __getitem__(self, name: str):
        if name != ERRORS_COLLECTION:
            raise KeyError(name)
        return self.errors


def test_record_error_inserts_full_document_shape() -> None:
    db = _FakeDb()

    asyncio.run(
        record_error(
            db,
            scan_id="scan_demo",
            phase="navigation",
            error_type="NAVIGATION_TIMEOUT",
            message="boom",
            url="http://localhost:8000/profile",
            normalized_url="http://localhost:8000/profile",
            worker_id="worker-1",
            context={"depth": 1},
            traceback_text="Traceback...",
        )
    )

    assert len(db.errors.inserted) == 1
    doc = db.errors.inserted[0]
    assert doc["scan_id"] == "scan_demo"
    assert doc["phase"] == "navigation"
    assert doc["error_type"] == "NAVIGATION_TIMEOUT"
    assert doc["message"] == "boom"
    assert doc["url"] == "http://localhost:8000/profile"
    assert doc["normalized_url"] == "http://localhost:8000/profile"
    assert doc["worker_id"] == "worker-1"
    assert doc["context"] == {"depth": 1}
    assert doc["traceback"] == "Traceback..."
    assert doc["occurred_at"] is not None


def test_record_error_defaults_empty_context_when_omitted() -> None:
    db = _FakeDb()
    asyncio.run(
        record_error(
            db,
            scan_id="scan_demo",
            phase="forms_extract",
            error_type="FORMS_EXTRACTION_FAILED",
            message="bad form",
        )
    )

    doc = db.errors.inserted[0]
    assert doc["context"] == {}
    assert doc["url"] is None
    assert doc["normalized_url"] is None
    assert doc["worker_id"] is None
    assert doc["traceback"] is None


def test_record_error_swallows_mongo_failure() -> None:
    db = _FakeDb()
    db.errors.raise_on_insert = True

    asyncio.run(
        record_error(
            db,
            scan_id="scan_demo",
            phase="navigation",
            error_type="NAVIGATION_TIMEOUT",
            message="boom",
        )
    )

    assert db.errors.inserted == []


def test_count_errors_uses_scan_id_filter() -> None:
    db = _FakeDb()
    asyncio.run(
        record_error(
            db, scan_id="scan_demo", phase="navigation", error_type="X", message="m"
        )
    )
    asyncio.run(
        record_error(db, scan_id="other", phase="navigation", error_type="X", message="m")
    )
    asyncio.run(
        record_error(
            db, scan_id="scan_demo", phase="navigation", error_type="X", message="m"
        )
    )

    count = asyncio.run(count_errors(db, scan_id="scan_demo"))

    assert count == 2
    assert db.errors.count_filter == {"scan_id": "scan_demo"}
