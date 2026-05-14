"""Worker navigation timeout: page closes, queue item marked retryable."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from cytrix_crawler.crawl.worker import _process_item


def test_process_item_navigation_timeout_records_and_closes_page(event_loop, monkeypatch) -> None:
    mark_failed_mock = AsyncMock()
    record_error_mock = AsyncMock()
    monkeypatch.setattr("cytrix_crawler.crawl.worker.mark_failed", mark_failed_mock)
    monkeypatch.setattr("cytrix_crawler.crawl.worker.record_error", record_error_mock)

    page = MagicMock()
    page.goto = AsyncMock(side_effect=PlaywrightTimeoutError("Navigation timeout"))
    page.close = AsyncMock()

    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)

    item = {
        "url": "http://localhost:8000/slow-fixture",
        "normalized_url": "http://localhost:8000/slow-fixture",
        "depth": 1,
    }
    config = {
        "scan_id": "scan_nav_timeout_unit",
        "max_depth": 3,
        "exclude_patterns": [],
    }

    async def run():
        return await _process_item(
            context=context,
            db=MagicMock(),
            config=config,
            worker_id="worker-test",
            item=item,
        )

    delta = event_loop.run_until_complete(run())

    assert delta["failed"] == 1
    assert delta["processed"] == 0
    page.close.assert_awaited()

    record_error_mock.assert_awaited_once()
    re_kw = record_error_mock.await_args.kwargs
    assert re_kw["scan_id"] == config["scan_id"]
    assert re_kw["error_type"] == "NAVIGATION_TIMEOUT"
    assert re_kw["phase"] == "navigation"

    mark_failed_mock.assert_awaited_once()
    mf_kw = mark_failed_mock.await_args.kwargs
    assert mf_kw["retryable"] is True
    assert mf_kw["normalized_url"] == item["normalized_url"]
