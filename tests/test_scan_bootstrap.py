from __future__ import annotations

import asyncio
from copy import deepcopy

from cytrix_crawler.config import CONFIG
from cytrix_crawler.storage.scans import bootstrap_scan


class _FakeCollection:
    def __init__(self) -> None:
        self.last_filter = None
        self.last_update = None
        self.last_upsert = None

    async def update_one(self, filter_query, update_query, upsert=False):
        self.last_filter = filter_query
        self.last_update = update_query
        self.last_upsert = upsert


class _FakeDb:
    def __init__(self) -> None:
        self.scans = _FakeCollection()

    def __getitem__(self, name: str):
        if name != "scans":
            raise KeyError(name)
        return self.scans


def test_bootstrap_scan_uses_idempotent_upsert_shape() -> None:
    db = _FakeDb()
    config = deepcopy(CONFIG)

    asyncio.run(bootstrap_scan(db, config))

    assert db.scans.last_filter == {"scan_id": config["scan_id"]}
    assert db.scans.last_upsert is True

    update_query = db.scans.last_update
    assert "$setOnInsert" in update_query
    assert "$set" in update_query
    assert update_query["$setOnInsert"]["scan_id"] == config["scan_id"]
    assert update_query["$setOnInsert"]["config_snapshot"] == config
    assert update_query["$setOnInsert"]["login"] is None
    assert update_query["$setOnInsert"]["summary"] is None
    assert update_query["$setOnInsert"]["can_resume"] is True
    assert update_query["$set"]["status"] == "initialized"
    assert "updated_at" in update_query["$set"]
