"""CYTRIX crawler entrypoint — scan config in ``CONFIG``; run with ``python main.py``."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from playwright.async_api import async_playwright

import cytrix_crawler.config as cfg
from cytrix_crawler.auth.login import prepare_session_for_crawl
from cytrix_crawler.auth.session_store import (
    ensure_session_dir,
    get_session_state_path,
    session_state_exists,
)
from cytrix_crawler.config import (
    ConfigError,
    config_for_target,
    validate_config,
    MONGO_DB_NAME,
    MONGO_URI,
)
from cytrix_crawler.crawl.orchestrator import run_crawl
from cytrix_crawler.storage.indexes import bootstrap_indexes
from cytrix_crawler.storage.mongo import (
    close_mongo_client,
    get_database,
    get_mongo_client,
    ping_database,
)
from cytrix_crawler.storage.scans import bootstrap_scan, update_login_result
from cytrix_crawler.util.shutdown import create_stop_event, install_signal_handlers


logger = logging.getLogger(__name__)


def _print_crawl_summary(summary: dict, *, scan_id: str) -> None:
    counts = summary.get("queue_counts", {})
    print(f"Crawl finished (scan_id={scan_id}):")
    print(f"  processed:       {summary['processed']}")
    print(f"  failed:          {summary['failed']}")
    print(f"  enqueued_links:  {summary['enqueued_links']}")
    print(f"  pages (stored): {summary.get('pages_count', 0)}")
    print(f"  links (stored): {summary.get('links_count', 0)}")
    print(f"  forms (stored): {summary.get('forms_count', 0)}")
    print(f"  browser requests: {summary.get('browser_requests_total', 0)}")
    print(f"  api requests: {summary.get('browser_requests_api', 0)}")
    print(f"  static requests: {summary.get('browser_requests_static', 0)}")
    print(f"  errors: {summary.get('errors', 0)}")
    ncf = summary.get("network_capture_failures", 0)
    if ncf:
        print(f"  network capture failures: {ncf}")
    fe = summary.get("forms_extraction_failures", 0)
    if fe:
        print(f"  forms extraction failures: {fe}")
    print(
        "  queue: "
        f"pending={counts.get('pending', 0)} "
        f"in_progress={counts.get('in_progress', 0)} "
        f"done={counts.get('done', 0)} "
        f"failed={counts.get('failed', 0)} "
        f"skipped={counts.get('skipped', 0)}"
    )
    for worker in summary.get("workers", []):
        print(
            f"  {worker['worker_id']}: "
            f"processed={worker['processed']} "
            f"failed={worker['failed']} "
            f"enqueued_links={worker['enqueued_links']} "
            f"captured={worker.get('captured_requests', 0)} "
            f"api={worker.get('api_requests', 0)} "
            f"static={worker.get('static_requests', 0)}"
        )


def _configure_logging() -> None:
    level_name = os.environ.get("CYTRIX_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(levelname)s [%(name)s] %(message)s",
    )


def _select_crawl_target() -> str:
    """Pick exactly one target: env ``CYTRIX_TARGET``, else interactive prompt, else demo."""
    raw = os.getenv("CYTRIX_TARGET", "").strip().lower()
    if raw:
        if raw in ("practice", "real"):
            return "practicetestautomation"
        if raw == "demo":
            return "demo"
        if raw == "practicetestautomation":
            return "practicetestautomation"
        print(f"Unknown CYTRIX_TARGET={raw!r}; using demo.")
        return "demo"

    if sys.stdin.isatty():
        print()
        print("Select crawl target (only one runs per process):")
        print("  1) demo            — local demo_app (deterministic; start the server first)")
        print("  2) practicetestautomation — real public practice site on the internet")
        print()
        while True:
            choice = input("Enter 1 or 2: ").strip().lower()
            if choice in ("1", "demo", "d"):
                print()
                demo_login = os.getenv("DEMO_BASE_URL", "http://localhost:8000").rstrip("/") + "/login"
                print(
                    "Target: demo. Ensure demo_app is reachable "
                    f"({demo_login}). Examples:"
                )
                print("  docker compose up -d mongo demo-app")
                print("  or: cd demo_app && pip install -r requirements.txt && python app.py")
                print()
                return "demo"
            if choice in ("2", "practicetestautomation", "pta", "p", "practice", "real"):
                print()
                print("Target: practicetestautomation.com (public network).")
                print()
                return "practicetestautomation"
            print("Please type 1 for demo or 2 for practicetestautomation.")

    print("Non-interactive stdin and no CYTRIX_TARGET; using demo.")
    return "demo"


def apply_crawl_target() -> None:
    """Set ``cfg.CONFIG`` to the chosen target before the async crawl starts."""
    name = _select_crawl_target()
    cfg.CONFIG = config_for_target(name)
    print(f"Active target: {name} (scan_id={cfg.CONFIG['scan_id']})")


async def run() -> None:
    _configure_logging()
    print("CYTRIX Authenticated Browser Crawler")
    validate_config(cfg.CONFIG)
    print("Config validation: OK")

    stop_event = create_stop_event()
    remove_signal_handlers = install_signal_handlers(stop_event)

    client = get_mongo_client(MONGO_URI)
    try:
        db = get_database(client, MONGO_DB_NAME)
        await ping_database(db)
        print("MongoDB connection: OK")

        await bootstrap_indexes(db)
        print("Indexes bootstrapped: OK")

        await bootstrap_scan(db, cfg.CONFIG)
        print(f"Scan bootstrapped: {cfg.CONFIG['scan_id']}")

        session_state_path = get_session_state_path(cfg.CONFIG["scan_id"])
        ensure_session_dir(session_state_path)
        had_session_file = session_state_exists(session_state_path)

        async with async_playwright() as playwright:
            headless = bool(cfg.CONFIG.get("headless", False))
            browser = await playwright.chromium.launch(headless=headless)
            try:
                crawl_context, login_result = await prepare_session_for_crawl(
                    browser, cfg.CONFIG, session_state_path
                )
                await update_login_result(db, cfg.CONFIG["scan_id"], login_result.to_dict())

                if login_result.success and had_session_file and "storage_state_saved" not in (
                    login_result.indicators or []
                ):
                    session_reuse_status = "valid"
                elif login_result.success:
                    session_reuse_status = "logged_in"
                elif had_session_file:
                    session_reuse_status = "invalid"
                else:
                    session_reuse_status = "skipped"

                print(f"Session reuse: {session_reuse_status}")
                print(f"Login success: {str(login_result.success).lower()}")
                print(f"Storage state: {session_state_path}")

                if not login_result.success:
                    await crawl_context.close()
                    print("Crawl skipped: authentication failed.")
                    return

                try:
                    summary = await run_crawl(
                        db,
                        playwright,
                        cfg.CONFIG,
                        session_state_path,
                        stop_event=stop_event,
                        login_success=True,
                        external_browser=browser,
                        external_context=crawl_context,
                    )
                    _print_crawl_summary(summary, scan_id=cfg.CONFIG["scan_id"])
                    logger.info(
                        "Run finished scan_id=%s interrupted=%s",
                        cfg.CONFIG["scan_id"],
                        summary.get("interrupted"),
                    )

                    if summary.get("interrupted"):
                        print("Crawl interrupted. State is resumable with the same scan_id.")
                finally:
                    await crawl_context.close()
            finally:
                await browser.close()
    finally:
        remove_signal_handlers()
        close_mongo_client(client)


def main() -> None:
    try:
        apply_crawl_target()
        asyncio.run(run())
    except KeyboardInterrupt:
        print("Crawl interrupted. State is resumable with the same scan_id.")
    except ConfigError as exc:
        print(f"Configuration error:\n{exc}")
    except Exception as exc:  # noqa: BLE001 - entrypoint should guard and print errors
        source_module = exc.__class__.__module__
        if source_module.startswith(("pymongo", "motor")):
            print(f"MongoDB error: {exc}")
        else:
            print(f"Bootstrap failed: {exc}")


if __name__ == "__main__":
    main()
