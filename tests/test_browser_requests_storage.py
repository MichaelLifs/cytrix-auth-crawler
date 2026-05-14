"""Mongo-backed tests for browser_requests persistence."""

from __future__ import annotations

from cytrix_crawler.storage.browser_requests import (
    BROWSER_REQUESTS_COLLECTION,
    count_browser_requests,
    upsert_browser_request,
    upsert_browser_requests_bulk,
)


def _doc(
    *,
    scan_id: str,
    request_hash: str,
    method: str = "GET",
    url: str = "http://localhost:8000/api/profile",
    is_api: bool = True,
    is_static: bool = False,
    body_preview: str | None = '{"ok":true}',
) -> dict:
    return {
        "scan_id": scan_id,
        "hash": request_hash,
        "page_url": "http://localhost:8000/profile",
        "request": {
            "method": method,
            "url": url,
            "headers": {"accept": "application/json"},
            "has_authorization": False,
            "post_data_present": False,
        },
        "response": {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "content_type": "application/json",
            "body_preview": body_preview,
            "body_truncated": False,
            "body_size": 11,
            "failed": False,
            "failure_text": None,
        },
        "classification": {"is_api": is_api, "is_static": is_static},
        "captured_at": None,
    }


def test_upsert_dedupes_by_scan_id_and_hash(event_loop, mongo_db, scan_id) -> None:
    request_hash = "sha256:" + "a" * 64

    async def scenario() -> dict:
        await upsert_browser_request(
            mongo_db, request_doc=_doc(scan_id=scan_id, request_hash=request_hash)
        )
        first = await mongo_db[BROWSER_REQUESTS_COLLECTION].find_one(
            {"scan_id": scan_id, "hash": request_hash}
        )
        updated = _doc(scan_id=scan_id, request_hash=request_hash, body_preview='{"ok":false}')
        await upsert_browser_request(mongo_db, request_doc=updated)
        second = await mongo_db[BROWSER_REQUESTS_COLLECTION].find_one(
            {"scan_id": scan_id, "hash": request_hash}
        )
        total = await mongo_db[BROWSER_REQUESTS_COLLECTION].count_documents(
            {"scan_id": scan_id}
        )
        return {"first": first, "second": second, "total": total}

    try:
        out = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[BROWSER_REQUESTS_COLLECTION].delete_many({"scan_id": scan_id})
        )

    assert out["total"] == 1
    assert out["first"]["first_seen_at"] == out["second"]["first_seen_at"]
    assert out["second"]["response"]["body_preview"] == '{"ok":false}'
    assert out["first"]["updated_at"] != out["second"]["updated_at"]


def test_bulk_upsert_inserts_and_updates(event_loop, mongo_db, scan_id) -> None:
    hash_a = "sha256:" + "b" * 64
    hash_b = "sha256:" + "c" * 64

    async def scenario() -> int:
        await upsert_browser_requests_bulk(
            mongo_db,
            request_docs=[
                _doc(scan_id=scan_id, request_hash=hash_a),
                _doc(scan_id=scan_id, request_hash=hash_b, is_api=False, is_static=True,
                     url="http://localhost:8000/static/style.css", body_preview=None),
                _doc(scan_id=scan_id, request_hash=hash_a),
            ],
        )
        return await mongo_db[BROWSER_REQUESTS_COLLECTION].count_documents(
            {"scan_id": scan_id}
        )

    try:
        total = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[BROWSER_REQUESTS_COLLECTION].delete_many({"scan_id": scan_id})
        )

    assert total == 2


def test_count_browser_requests_totals_and_classifications(
    event_loop, mongo_db, scan_id
) -> None:
    docs = [
        _doc(scan_id=scan_id, request_hash="sha256:" + "1" * 64, is_api=True, is_static=False),
        _doc(scan_id=scan_id, request_hash="sha256:" + "2" * 64, is_api=True, is_static=False),
        _doc(scan_id=scan_id, request_hash="sha256:" + "3" * 64, is_api=False, is_static=True,
             url="http://localhost:8000/static/style.css", body_preview=None),
        _doc(scan_id=scan_id, request_hash="sha256:" + "4" * 64, is_api=False, is_static=False,
             url="http://localhost:8000/dashboard", body_preview=None),
    ]

    async def scenario() -> dict:
        await upsert_browser_requests_bulk(mongo_db, request_docs=docs)
        return await count_browser_requests(mongo_db, scan_id=scan_id)

    try:
        counts = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[BROWSER_REQUESTS_COLLECTION].delete_many({"scan_id": scan_id})
        )

    assert counts == {"total": 4, "api": 2, "static": 1}


def test_upsert_scoped_to_scan_id(event_loop, mongo_db, scan_id) -> None:
    request_hash = "sha256:" + "9" * 64
    other_scan = f"{scan_id}_other"

    async def scenario() -> int:
        await upsert_browser_request(
            mongo_db, request_doc=_doc(scan_id=scan_id, request_hash=request_hash)
        )
        await upsert_browser_request(
            mongo_db, request_doc=_doc(scan_id=other_scan, request_hash=request_hash)
        )
        return await mongo_db[BROWSER_REQUESTS_COLLECTION].count_documents(
            {"hash": request_hash}
        )

    try:
        total = event_loop.run_until_complete(scenario())
    finally:
        event_loop.run_until_complete(
            mongo_db[BROWSER_REQUESTS_COLLECTION].delete_many({"scan_id": scan_id})
        )
        event_loop.run_until_complete(
            mongo_db[BROWSER_REQUESTS_COLLECTION].delete_many({"scan_id": other_scan})
        )

    assert total == 2
