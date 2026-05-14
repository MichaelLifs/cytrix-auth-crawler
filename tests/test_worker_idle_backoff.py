"""Unit tests for worker idle backoff (no Playwright runtime)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cytrix_crawler.crawl.worker import (
    IDLE_SLEEP_SECONDS,
    MAX_IDLE_ATTEMPTS,
    run_worker,
)


@pytest.fixture
def dummy_browser():
    browser = MagicMock()
    ctx = MagicMock()
    ctx.close = AsyncMock()
    browser.new_context = AsyncMock(return_value=ctx)
    return browser


def test_worker_sleeps_before_exhausting_idle_attempts(event_loop, dummy_browser) -> None:
    sleep_mock = AsyncMock()
    claim_calls = {"n": 0}

    async def claim_side_effect(*args, **kwargs):
        claim_calls["n"] += 1
        return None

    with patch(
        "cytrix_crawler.crawl.worker.claim_next", side_effect=claim_side_effect
    ), patch(
        "cytrix_crawler.crawl.worker.count_pages", new_callable=AsyncMock
    ) as count_pages_mock:
        count_pages_mock.return_value = 0
        with patch("asyncio.sleep", sleep_mock):
            event_loop.run_until_complete(
                run_worker(
                    db=MagicMock(),
                    browser=dummy_browser,
                    config={"scan_id": "scan_idle", "max_pages": 10},
                    worker_id="w0",
                    session_state_path="sessions/w0.json",
                )
            )

    assert claim_calls["n"] == MAX_IDLE_ATTEMPTS
    assert sleep_mock.await_count == MAX_IDLE_ATTEMPTS - 1
    for call in sleep_mock.await_args_list:
        assert call.args[0] == IDLE_SLEEP_SECONDS


def test_worker_stops_promptly_when_stop_event_pre_set(event_loop, dummy_browser) -> None:
    stop = asyncio.Event()
    stop.set()
    sleep_mock = AsyncMock()
    claim_mock = AsyncMock(side_effect=AssertionError("claim_next should not be called"))

    with patch("cytrix_crawler.crawl.worker.claim_next", claim_mock), patch(
        "cytrix_crawler.crawl.worker.count_pages",
        AsyncMock(side_effect=AssertionError("count_pages should not be called")),
    ), patch("asyncio.sleep", sleep_mock):
        event_loop.run_until_complete(
            run_worker(
                db=MagicMock(),
                browser=dummy_browser,
                config={"scan_id": "scan_stop", "max_pages": 10},
                worker_id="w0",
                session_state_path="sessions/w0.json",
                stop_event=stop,
            )
        )

    claim_mock.assert_not_awaited()
    sleep_mock.assert_not_awaited()