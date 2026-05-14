"""Mongo-backed tests for form persistence."""

from __future__ import annotations

from cytrix_crawler.storage.forms import FORMS_COLLECTION, count_forms, upsert_form


def test_upsert_form_sets_on_insert_mutable_updates(event_loop, mongo_db, scan_id) -> None:
    base_doc = {
        "action": "http://localhost:8000/act",
        "buttons": [],
        "csrf_detected": True,
        "fields": [{"name": "n", "type": "text", "required": False}],
        "form_hash": "sha256:testhash",
        "method": "POST",
        "page_url": "http://localhost:8000/page",
    }

    async def scenario() -> None:
        await upsert_form(mongo_db, scan_id=scan_id, form_doc=dict(base_doc))
        first = await mongo_db[FORMS_COLLECTION].find_one(
            {"scan_id": scan_id, "form_hash": base_doc["form_hash"]}
        )
        updated_doc = dict(base_doc)
        updated_doc["method"] = "PUT"
        await upsert_form(mongo_db, scan_id=scan_id, form_doc=updated_doc)
        second = await mongo_db[FORMS_COLLECTION].find_one(
            {"scan_id": scan_id, "form_hash": base_doc["form_hash"]}
        )
        assert first is not None and second is not None
        assert second["method"] == "PUT"
        assert second["first_seen_at"] == first["first_seen_at"]

    try:
        event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(mongo_db[FORMS_COLLECTION].delete_many({"scan_id": scan_id}))


def test_forms_dedupe_distinct_hashes(event_loop, mongo_db, scan_id) -> None:
    doc_a = {
        "action": "http://localhost:8000/a",
        "buttons": [],
        "csrf_detected": False,
        "fields": [],
        "form_hash": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "method": "GET",
        "page_url": "http://localhost:8000/p",
    }
    doc_b = {**doc_a, "form_hash": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}

    async def scenario() -> int:
        await upsert_form(mongo_db, scan_id=scan_id, form_doc=dict(doc_a))
        await upsert_form(mongo_db, scan_id=scan_id, form_doc=dict(doc_b))
        await upsert_form(mongo_db, scan_id="other_scan", form_doc=dict(doc_a))
        return await count_forms(mongo_db, scan_id=scan_id)

    try:
        cnt = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(mongo_db[FORMS_COLLECTION].delete_many({"scan_id": scan_id}))
        event_loop.run_until_complete(
            mongo_db[FORMS_COLLECTION].delete_many({"scan_id": "other_scan"})
        )

    assert cnt == 2
