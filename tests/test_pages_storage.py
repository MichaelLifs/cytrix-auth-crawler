"""Integration tests for the pages repository.

Require a running MongoDB instance. Skipped automatically by the
``mongo_db`` fixture when Mongo is not reachable.
"""

from __future__ import annotations

from datetime import datetime, timezone

from cytrix_crawler.extract.metadata import empty_page_metadata
from cytrix_crawler.storage.pages import PAGES_COLLECTION, count_pages, upsert_page


def _page_doc(normalized_url: str, **overrides) -> dict:
    meta = empty_page_metadata()
    doc = {
        "buttons_count": 0,
        "content_type": "text/html",
        "crawled_at": datetime.now(timezone.utc),
        "depth": 0,
        "final_url": normalized_url,
        "forms_count": 0,
        "links_count": 0,
        "metadata": meta,
        "normalized_url": normalized_url,
        "scan_id": "ignored-overridden-by-call",
        "scripts_count": 0,
        "status_code": 200,
        "title": "Demo",
        "url": normalized_url,
    }
    doc.update(overrides)
    return doc


def test_upsert_page_inserts_with_first_seen_at(event_loop, mongo_db, scan_id) -> None:
    async def scenario() -> dict:
        await upsert_page(
            mongo_db,
            scan_id=scan_id,
            page_doc=_page_doc("http://localhost:8000/dashboard"),
        )
        return await mongo_db[PAGES_COLLECTION].find_one(
            {"scan_id": scan_id, "normalized_url": "http://localhost:8000/dashboard"}
        )

    try:
        doc = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[PAGES_COLLECTION].delete_many({"scan_id": scan_id})
        )

    assert doc is not None
    assert doc["scan_id"] == scan_id
    assert doc["normalized_url"] == "http://localhost:8000/dashboard"
    assert doc["title"] == "Demo"
    assert doc["status_code"] == 200
    assert "first_seen_at" in doc
    assert "updated_at" in doc


def test_upsert_page_does_not_overwrite_first_seen_at(
    event_loop, mongo_db, scan_id
) -> None:
    async def scenario() -> dict:
        await upsert_page(
            mongo_db,
            scan_id=scan_id,
            page_doc=_page_doc("http://localhost:8000/profile", title="initial"),
        )
        original = await mongo_db[PAGES_COLLECTION].find_one(
            {"scan_id": scan_id, "normalized_url": "http://localhost:8000/profile"}
        )
        await upsert_page(
            mongo_db,
            scan_id=scan_id,
            page_doc=_page_doc("http://localhost:8000/profile", title="re-crawl"),
        )
        latest = await mongo_db[PAGES_COLLECTION].find_one(
            {"scan_id": scan_id, "normalized_url": "http://localhost:8000/profile"}
        )
        return {"original": original, "latest": latest}

    try:
        outcome = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[PAGES_COLLECTION].delete_many({"scan_id": scan_id})
        )

    assert outcome["original"]["first_seen_at"] == outcome["latest"]["first_seen_at"]
    assert outcome["original"]["title"] == "initial"
    assert outcome["latest"]["title"] == "re-crawl"


def test_count_pages_returns_documents_for_scan(event_loop, mongo_db, scan_id) -> None:
    async def scenario() -> dict:
        await upsert_page(
            mongo_db,
            scan_id=scan_id,
            page_doc=_page_doc("http://localhost:8000/a"),
        )
        await upsert_page(
            mongo_db,
            scan_id=scan_id,
            page_doc=_page_doc("http://localhost:8000/b"),
        )
        await upsert_page(
            mongo_db,
            scan_id="other_scan",
            page_doc=_page_doc("http://localhost:8000/a"),
        )
        return {
            "this_scan": await count_pages(mongo_db, scan_id=scan_id),
            "other_scan": await count_pages(mongo_db, scan_id="other_scan"),
        }

    try:
        counts = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[PAGES_COLLECTION].delete_many({"scan_id": scan_id})
        )
        event_loop.run_until_complete(
            mongo_db[PAGES_COLLECTION].delete_many({"scan_id": "other_scan"})
        )

    assert counts["this_scan"] == 2
    assert counts["other_scan"] == 1


def test_page_enrichment_updates_counts_and_keeps_first_seen(event_loop, mongo_db, scan_id) -> None:
    meta_initial = empty_page_metadata()
    meta_initial["title"] = "Seed"

    meta_updated = empty_page_metadata()
    meta_updated["title"] = "Updated"
    meta_updated["buttons"] = ["Save", "Cancel"]

    async def scenario() -> dict:
        await upsert_page(
            mongo_db,
            scan_id=scan_id,
            page_doc=_page_doc(
                "http://localhost:8000/rich",
                links_count=1,
                metadata=meta_initial,
                forms_count=0,
                scripts_count=2,
                buttons_count=3,
                title="Seed",
            ),
        )
        first = await mongo_db[PAGES_COLLECTION].find_one(
            {"scan_id": scan_id, "normalized_url": "http://localhost:8000/rich"}
        )
        await upsert_page(
            mongo_db,
            scan_id=scan_id,
            page_doc=_page_doc(
                "http://localhost:8000/rich",
                links_count=10,
                metadata=meta_updated,
                forms_count=4,
                scripts_count=5,
                buttons_count=2,
                title="Updated",
            ),
        )
        latest = await mongo_db[PAGES_COLLECTION].find_one(
            {"scan_id": scan_id, "normalized_url": "http://localhost:8000/rich"}
        )
        return {"first": first, "latest": latest}

    try:
        outcome = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[PAGES_COLLECTION].delete_many({"scan_id": scan_id})
        )

    first = outcome["first"]
    latest = outcome["latest"]
    assert first is not None and latest is not None
    assert latest["metadata"]["title"] == "Updated"
    assert latest["links_count"] == 10
    assert latest["forms_count"] == 4
    assert latest["scripts_count"] == 5
    assert latest["buttons_count"] == 2
    assert latest["first_seen_at"] == first["first_seen_at"]
