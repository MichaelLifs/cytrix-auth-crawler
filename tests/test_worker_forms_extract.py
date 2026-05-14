"""Worker wiring: forms_count and persistence when extract_forms succeeds."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from cytrix_crawler.crawl.worker import _process_item
from cytrix_crawler.extract.metadata import empty_page_metadata


def test_process_item_forms_count_and_persist_forms(event_loop, monkeypatch) -> None:
    upsert_form_mock = AsyncMock()
    upsert_page_mock = AsyncMock()
    mark_done_mock = AsyncMock()

    monkeypatch.setattr("cytrix_crawler.crawl.worker.upsert_form", upsert_form_mock)
    monkeypatch.setattr("cytrix_crawler.crawl.worker.upsert_page", upsert_page_mock)
    monkeypatch.setattr("cytrix_crawler.crawl.worker.mark_done", mark_done_mock)
    monkeypatch.setattr(
        "cytrix_crawler.crawl.worker._enqueue_discovered_links", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(
        "cytrix_crawler.crawl.worker.extract_links", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "cytrix_crawler.crawl.worker.extract_page_metadata",
        AsyncMock(return_value=empty_page_metadata()),
    )

    one_form = {
        "action": "http://localhost:8000/profile",
        "method": "POST",
        "fields": [
            {"name": "_token", "type": "hidden", "required": False},
            {"name": "nickname", "type": "text", "required": False},
        ],
        "buttons": ["Save profile", "Preview"],
        "csrf_detected": True,
    }
    monkeypatch.setattr(
        "cytrix_crawler.crawl.worker.extract_forms",
        AsyncMock(return_value=([one_form], None)),
    )

    page = MagicMock()
    page.goto = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.url = "http://localhost:8000/profile"
    page.title = AsyncMock(return_value="Profile")
    page.close = AsyncMock()

    response = MagicMock()
    response.status = 200
    response.headers = {"content-type": "text/html; charset=utf-8"}
    page.goto.return_value = response

    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)

    item = {
        "url": "http://localhost:8000/profile",
        "normalized_url": "http://localhost:8000/profile",
        "depth": 0,
    }
    config = {"scan_id": "scan_unit_forms", "max_depth": 3, "exclude_patterns": []}

    async def run():
        return await _process_item(
            context=context,
            db=MagicMock(),
            config=config,
            worker_id="w-test",
            item=item,
        )

    delta = event_loop.run_until_complete(run())

    assert delta["processed"] == 1
    assert delta["failed"] == 0
    assert delta["forms_extraction_failures"] == 0

    page_doc = upsert_page_mock.await_args.kwargs["page_doc"]
    assert page_doc["forms_count"] == 1

    assert upsert_form_mock.await_count == 1
    fd = upsert_form_mock.await_args.kwargs["form_doc"]
    assert fd["csrf_detected"] is True
    assert fd["page_url"] == "http://localhost:8000/profile"
