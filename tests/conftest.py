"""Shared pytest fixtures for queue integration tests.

The ``mongo_db`` fixture connects to whatever MongoDB instance the
environment exposes via ``MONGO_URI`` (default ``mongodb://localhost:27017``).
Tests that require Mongo should depend on this fixture; if Mongo is not
reachable the fixture skips the test rather than failing the suite.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from cytrix_crawler.storage.indexes import bootstrap_indexes
from cytrix_crawler.storage.mongo import (
    close_mongo_client,
    get_database,
    get_mongo_client,
    ping_database,
)

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_TEST_DB_NAME = os.getenv("MONGO_TEST_DB_NAME", "cytrix_crawler_test")


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()


@pytest.fixture
def mongo_db(event_loop):
    """Provide an initialized async Mongo DB handle or skip if unreachable."""
    client = get_mongo_client(MONGO_URI)
    db = get_database(client, MONGO_TEST_DB_NAME)
    try:
        event_loop.run_until_complete(
            asyncio.wait_for(ping_database(db), timeout=2.0)
        )
    except Exception as exc:  # noqa: BLE001 - skip if Mongo not available
        close_mongo_client(client)
        pytest.skip(f"MongoDB not available at {MONGO_URI}: {exc}")

    event_loop.run_until_complete(bootstrap_indexes(db))
    try:
        yield db
    finally:
        close_mongo_client(client)


@pytest.fixture
def scan_id() -> str:
    """Return a unique scan_id to keep test data isolated."""
    return f"test_scan_{uuid.uuid4().hex[:12]}"
