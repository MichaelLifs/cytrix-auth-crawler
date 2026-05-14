"""Crawl orchestration.

Responsibilities, in order:

1. Recover any stuck ``in_progress`` queue items from a previous run.
2. Seed the start URL idempotently.
3. Move the scan to ``running``.
4. Launch N workers sharing a single Chromium instance and one
   ``BrowserContext`` so headed runs use one window; each worker opens URLs
   with ``new_page()`` (tabs) instead of separate windows.
5. Aggregate counters into a summary and persist scan status:
   ``completed`` if we finished naturally, ``interrupted`` if ``stop_event``
   was set during the run.

We deliberately do not use task cancellation as the shutdown primitive — the
shared ``stop_event`` is enough, and waiting for workers to finish their
current item keeps queue rows from being left in ``in_progress``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

from cytrix_crawler.crawl.worker import run_worker
from cytrix_crawler.queue.manager import (
    count_by_status,
    recover_stuck_items,
    seed_start_url,
)
from cytrix_crawler.storage.browser_requests import count_browser_requests
from cytrix_crawler.storage.errors import count_errors
from cytrix_crawler.storage.forms import count_forms
from cytrix_crawler.storage.links import count_links
from cytrix_crawler.storage.pages import count_pages
from cytrix_crawler.storage.scans import update_scan_status, update_scan_summary

QUEUE_LEASE_TIMEOUT_SECONDS = 300

_WORKER_COUNTER_KEYS = (
    "processed",
    "failed",
    "enqueued_links",
    "forms_extraction_failures",
    "captured_requests",
    "network_capture_failures",
)


async def _build_summary(
    db: Any,
    *,
    scan_id: str,
    worker_results: list[dict[str, Any]],
    login_success: bool,
) -> dict[str, Any]:
    queue_counts = await count_by_status(db, scan_id=scan_id)
    browser_request_counts = await count_browser_requests(db, scan_id=scan_id)
    pages_count = await count_pages(db, scan_id=scan_id)
    links_count = await count_links(db, scan_id=scan_id)
    forms_count = await count_forms(db, scan_id=scan_id)
    errors_count = await count_errors(db, scan_id=scan_id)

    summary: dict[str, Any] = {
        "login_success": login_success,
        "workers": worker_results,
        "queue_counts": queue_counts,
        "pages_count": pages_count,
        "pages_crawled": pages_count,
        "unique_links": links_count,
        "links_count": links_count,
        "unique_forms": forms_count,
        "forms_count": forms_count,
        "browser_requests_total": browser_request_counts["total"],
        "browser_requests_api": browser_request_counts["api"],
        "api_requests": browser_request_counts["api"],
        "browser_requests_static": browser_request_counts["static"],
        "static_requests": browser_request_counts["static"],
        "pages_discovered": sum(queue_counts.values()),
        "errors": errors_count,
        "can_resume": True,
    }
    for key in _WORKER_COUNTER_KEYS:
        summary[key] = sum(w.get(key, 0) for w in worker_results)
    return summary


async def run_crawl(
    db: Any,
    playwright: Any,
    config: dict[str, Any],
    session_state_path: str,
    *,
    stop_event: asyncio.Event | None = None,
    login_success: bool = True,
    external_browser: Any | None = None,
    external_context: Any | None = None,
) -> dict[str, Any]:
    """Run the configured number of crawler workers against a single browser.

    When ``external_browser`` / ``external_context`` are provided (typical
    ``main.py`` flow), the crawl reuses the same Chromium window the user
    already saw during login validation so only tabs are opened for workers.

    Returns the aggregated crawl summary. Also persists ``scans.summary`` and
    transitions ``scans.status`` to ``completed`` or ``interrupted`` depending
    on whether ``stop_event`` was set at the end of the run.
    """
    scan_id = config["scan_id"]
    logger.info(
        "Crawl starting scan_id=%s start_url=%s concurrency=%s max_pages=%s",
        scan_id,
        config.get("start_url_after_login"),
        config.get("concurrency"),
        config.get("max_pages"),
    )

    recovered = await recover_stuck_items(
        db, scan_id=scan_id, lease_timeout_seconds=QUEUE_LEASE_TIMEOUT_SECONDS
    )
    if recovered:
        logger.info(
            "Queue recovery: %s stuck in_progress item(s) returned to pending",
            recovered,
        )
    else:
        logger.info("Queue recovery: no stuck items (or none past lease timeout)")

    seed_outcome = await seed_start_url(db, config)
    logger.info(
        "Seed start URL: enqueued=%s duplicate=%s normalized_url=%s reason=%s",
        seed_outcome.get("enqueued"),
        seed_outcome.get("duplicate"),
        seed_outcome.get("normalized_url"),
        seed_outcome.get("reason"),
    )

    await update_scan_status(db, scan_id=scan_id, status="running")
    logger.info("Scan status updated to running")

    concurrency = max(1, int(config["concurrency"]))
    headless = bool(config.get("headless", False))
    owns_browser = external_browser is None
    owns_context = external_context is None

    if owns_browser:
        logger.info(
            "Launching Chromium headless=%s with %s parallel worker(s) (tabs in one window when headed)",
            headless,
            concurrency,
        )
        browser = await playwright.chromium.launch(headless=headless)
        logger.info("Chromium launched")
    else:
        browser = external_browser
        logger.info(
            "Using shared Chromium (headless=%s) with %s worker(s); each page is a tab",
            headless,
            concurrency,
        )

    if owns_context:
        context = await browser.new_context(storage_state=session_state_path)
        logger.info("Crawl browser context created from storage_state (workers use new_page() as tabs)")
    else:
        context = external_context
        logger.info("Reusing authenticated browser context for crawl (workers use new_page() as tabs)")

    worker_results: list[dict[str, Any]] = []
    try:
        logger.info("Starting %s crawl worker task(s)", concurrency)
        worker_results = await asyncio.gather(
            *(
                run_worker(
                    db=db,
                    browser=browser,
                    config=config,
                    worker_id=f"worker-{index}",
                    session_state_path=session_state_path,
                    stop_event=stop_event,
                    shared_context=context,
                )
                for index in range(concurrency)
            )
        )
        logger.info("All crawl workers finished")
    finally:
        if owns_context:
            logger.info("Closing crawl browser context")
            try:
                await context.close()
            except Exception as exc:  # noqa: BLE001 - teardown must not mask the summary
                logger.warning(
                    "Context close failed (ignored): %s: %s", exc.__class__.__name__, exc
                )
            else:
                logger.info("Browser context closed")
        else:
            logger.info("Leaving crawl browser context open (caller owns lifecycle)")
        if owns_browser:
            logger.info("Closing Chromium instance")
            try:
                await browser.close()
            except Exception as exc:  # noqa: BLE001 - browser teardown must not mask the summary
                logger.warning("Browser close failed (ignored): %s: %s", exc.__class__.__name__, exc)
            else:
                logger.info("Browser closed")
        else:
            logger.info("Leaving Chromium open (caller owns lifecycle)")

    interrupted = stop_event is not None and stop_event.is_set()
    logger.info(
        "Building crawl summary (interrupted=%s login_success=%s)",
        interrupted,
        login_success,
    )
    summary = await _build_summary(
        db,
        scan_id=scan_id,
        worker_results=worker_results,
        login_success=login_success,
    )
    summary["interrupted"] = interrupted

    final_status = "interrupted" if interrupted else "completed"
    summary["scan_id"] = scan_id
    summary["status"] = final_status
    await update_scan_summary(
        db,
        scan_id=scan_id,
        status=final_status,
        summary=summary,
    )
    logger.info("Crawl finished scan_id=%s status=%s", scan_id, final_status)
    logger.debug(
        "Crawl summary (JSON): %s",
        json.dumps(summary, default=str, ensure_ascii=False),
    )
    return summary
