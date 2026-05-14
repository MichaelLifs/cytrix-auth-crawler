"""Mongo-backed tests for the links ledger."""

from __future__ import annotations

from cytrix_crawler.storage.links import LINKS_COLLECTION, count_links, upsert_link


def test_upsert_link_set_on_insert_only(event_loop, mongo_db, scan_id) -> None:
    async def scenario() -> None:
        await upsert_link(
            mongo_db,
            scan_id=scan_id,
            normalized_url="http://localhost:8000/a",
            first_seen_on="http://localhost:8000/parent",
            depth_first_seen=1,
        )
        first = await mongo_db[LINKS_COLLECTION].find_one(
            {"scan_id": scan_id, "normalized_url": "http://localhost:8000/a"}
        )
        await upsert_link(
            mongo_db,
            scan_id=scan_id,
            normalized_url="http://localhost:8000/a",
            first_seen_on="http://localhost:9000/other",
            depth_first_seen=9,
        )
        second = await mongo_db[LINKS_COLLECTION].find_one(
            {"scan_id": scan_id, "normalized_url": "http://localhost:8000/a"}
        )
        assert first is not None and second is not None
        assert second["first_seen_on"] == first["first_seen_on"]
        assert second["depth_first_seen"] == first["depth_first_seen"]

    try:
        event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(mongo_db[LINKS_COLLECTION].delete_many({"scan_id": scan_id}))


def test_links_dedupe_by_scan_and_url(event_loop, mongo_db, scan_id) -> None:
    async def scenario() -> int:
        await upsert_link(
            mongo_db,
            scan_id=scan_id,
            normalized_url="http://localhost:8000/link",
            first_seen_on=None,
            depth_first_seen=0,
        )
        await upsert_link(
            mongo_db,
            scan_id=scan_id,
            normalized_url="http://localhost:8000/link",
            first_seen_on="http://x",
            depth_first_seen=99,
        )
        await upsert_link(
            mongo_db,
            scan_id="other_scan",
            normalized_url="http://localhost:8000/link",
            first_seen_on=None,
            depth_first_seen=0,
        )
        return await count_links(mongo_db, scan_id=scan_id)

    try:
        count = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(mongo_db[LINKS_COLLECTION].delete_many({"scan_id": scan_id}))
        event_loop.run_until_complete(
            mongo_db[LINKS_COLLECTION].delete_many({"scan_id": "other_scan"})
        )

    assert count == 1
