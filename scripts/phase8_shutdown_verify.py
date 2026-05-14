"""Phase 8 manual verification helper for graceful shutdown + resume.

NOT part of the crawler product runtime. Use:

    python scripts/phase8_shutdown_verify.py

It triggers the same code path the signal handler uses (setting the
stop_event), then re-runs the same scan_id to prove resume works.
"""

from __future__ import annotations

import asyncio
import os
from copy import deepcopy

from playwright.async_api import async_playwright

from cytrix_crawler.auth.login import prepare_session_for_crawl
from cytrix_crawler.auth.session_store import ensure_session_dir, get_session_state_path
from cytrix_crawler.config import CONFIG, MONGO_DB_NAME, MONGO_URI
from cytrix_crawler.crawl.orchestrator import run_crawl
from cytrix_crawler.queue.manager import count_by_status
from cytrix_crawler.storage.indexes import bootstrap_indexes
from cytrix_crawler.storage.mongo import (
    close_mongo_client,
    get_database,
    get_mongo_client,
    ping_database,
)
from cytrix_crawler.storage.scans import bootstrap_scan, update_login_result

SCAN_ID = os.getenv("PHASE8_SHUTDOWN_SCAN_ID", "scan_demo_phase8_shutdown")
EARLY_STOP_AFTER_S = float(os.getenv("PHASE8_STOP_AFTER", "0.5"))


async def _delayed_stop(stop_event: asyncio.Event) -> None:
    await asyncio.sleep(EARLY_STOP_AFTER_S)
    print(f"-> triggering stop_event after {EARLY_STOP_AFTER_S}s (simulated SIGINT)")
    stop_event.set()


async def _run_one(db, playwright, browser, config, session_state_path, *, stop_event):
    crawl_context, result = await prepare_session_for_crawl(browser, config, session_state_path)
    try:
        await update_login_result(db, config["scan_id"], result.to_dict())
        if not result.success:
            print("Login failed; aborting.")
            return None
        return await run_crawl(
            db,
            playwright,
            config,
            session_state_path,
            stop_event=stop_event,
            login_success=True,
            external_browser=browser,
            external_context=crawl_context,
        )
    finally:
        await crawl_context.close()


async def main() -> None:
    config = deepcopy(CONFIG)
    config["scan_id"] = SCAN_ID

    client = get_mongo_client(MONGO_URI)
    try:
        db = get_database(client, MONGO_DB_NAME)
        await ping_database(db)
        await bootstrap_indexes(db)

        session_state_path = get_session_state_path(config["scan_id"])
        ensure_session_dir(session_state_path)

        await bootstrap_scan(db, config)

        async with async_playwright() as playwright:
            headless = bool(config.get("headless", False))
            browser = await playwright.chromium.launch(headless=headless)
            try:
                print("--- Run 1: interrupted shortly after start ---")
                stop_event_1 = asyncio.Event()
                stopper = asyncio.create_task(_delayed_stop(stop_event_1))
                summary_1 = await _run_one(
                    db, playwright, browser, config, session_state_path, stop_event=stop_event_1
                )
                await stopper

                doc_1 = await db["scans"].find_one({"scan_id": SCAN_ID})
                counts_1 = await count_by_status(db, scan_id=SCAN_ID)
                print(f"  status:     {doc_1['status']}")
                print(f"  can_resume: {doc_1['can_resume']}")
                print(f"  finished_at:{doc_1.get('finished_at')}")
                print(f"  queue:      {counts_1}")
                print(f"  summary:    processed={summary_1['processed']} interrupted={summary_1['interrupted']}")

                assert doc_1["status"] == "interrupted", "scan should be interrupted"
                assert doc_1["can_resume"] is True
                assert counts_1["in_progress"] == 0, "no in_progress items must remain"

                print()
                print("--- Run 2: same scan_id resumes and completes ---")
                stop_event_2 = asyncio.Event()
                summary_2 = await _run_one(
                    db, playwright, browser, config, session_state_path, stop_event=stop_event_2
                )
                doc_2 = await db["scans"].find_one({"scan_id": SCAN_ID})
                counts_2 = await count_by_status(db, scan_id=SCAN_ID)
                print(f"  status:     {doc_2['status']}")
                print(f"  can_resume: {doc_2['can_resume']}")
                print(f"  finished_at:{doc_2.get('finished_at')}")
                print(f"  queue:      {counts_2}")
                print(
                    "  summary:    "
                    f"processed={summary_2['processed']} "
                    f"pages={summary_2['pages_count']} "
                    f"errors={summary_2['errors']} "
                    f"interrupted={summary_2['interrupted']}"
                )
                assert doc_2["status"] == "completed"
                assert counts_2["in_progress"] == 0
                assert counts_2["pending"] == 0
                print()
                print("Shutdown + resume verification: OK")
            finally:
                await browser.close()
    finally:
        close_mongo_client(client)


if __name__ == "__main__":
    asyncio.run(main())
