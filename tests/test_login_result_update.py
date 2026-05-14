from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from cytrix_crawler.storage.scans import update_login_result


class _FakeCollection:
    def __init__(self) -> None:
        self.last_filter = None
        self.last_update = None
        self.call_count = 0

    async def update_one(self, filter_query, update_query):
        self.last_filter = filter_query
        self.last_update = update_query
        self.call_count += 1


class _FakeDb:
    def __init__(self) -> None:
        self.scans = _FakeCollection()

    def __getitem__(self, name: str):
        if name != "scans":
            raise KeyError(name)
        return self.scans


def test_update_login_result_builds_expected_mongo_update_shape() -> None:
    db = _FakeDb()
    login_result = {
        "success": True,
        "indicators": ["session_cookies_present"],
        "final_url": "http://localhost:8000/dashboard",
        "message": None,
        "validated_at": datetime.now(timezone.utc),
    }

    asyncio.run(update_login_result(db, "scan_demo", login_result))

    assert db.scans.call_count == 1
    assert db.scans.last_filter == {"scan_id": "scan_demo"}
    assert "$set" in db.scans.last_update
    assert db.scans.last_update["$set"]["login"] == login_result
    assert db.scans.last_update["$set"]["status"] == "authenticated"
    assert "updated_at" in db.scans.last_update["$set"]

