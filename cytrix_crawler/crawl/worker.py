"""Async crawler worker.

Each worker pulls one queue item at a time using a Playwright
``BrowserContext`` — either the crawl orchestrator's shared context (so all
workers open pages as tabs in one headed window) or, when ``shared_context``
is omitted, its own context from ``storage_state`` (used by unit tests).

Flow per item:

  claim -> navigate -> extract (HTML only) -> persist -> flush capture -> mark_done

A few non-obvious behaviors are worth highlighting:

- ``stop_event`` is polled at every safe boundary (before claiming, before
  and after idle sleep). It is intentionally *not* used to cancel in-flight
  page processing — letting the current item finish lets us call
  ``mark_done`` and avoid leaving a queue row in ``in_progress`` that would
  later have to be recovered by ``recover_stuck_items``.
- ``_safe_flush_capture`` and the various extraction try/excepts are best-
  effort: one bad page must never crash the worker. Operational errors are
  recorded into the ``errors`` collection for visibility, never re-raised.
- ``NETWORK_IDLE_WAIT_MS`` is a bounded best-effort wait so inline ``fetch``
  calls have a chance to finish before we flush the capture. It must never
  hang the crawl, so timeouts are silently ignored.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime, timezone
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from cytrix_crawler.crawl.boundaries import should_enqueue_url
from cytrix_crawler.dedupe.hashing import form_hash
from cytrix_crawler.extract.forms import extract_forms
from cytrix_crawler.extract.links import extract_links
from cytrix_crawler.extract.metadata import empty_page_metadata, extract_page_metadata
from cytrix_crawler.extract.normalize import normalize_url
from cytrix_crawler.network.capture import PageNetworkCapture
from cytrix_crawler.queue.manager import (
    claim_next,
    enqueue_url,
    mark_done,
    mark_failed,
)
from cytrix_crawler.storage.errors import record_error
from cytrix_crawler.storage.forms import upsert_form
from cytrix_crawler.storage.links import upsert_link
from cytrix_crawler.storage.pages import count_pages, upsert_page

logger = logging.getLogger(__name__)

IDLE_SLEEP_SECONDS = 0.25
MAX_IDLE_ATTEMPTS = 5
NAVIGATION_TIMEOUT_MS = 30_000
NETWORK_IDLE_WAIT_MS = 3_000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _should_stop(stop_event: asyncio.Event | None) -> bool:
    return stop_event is not None and stop_event.is_set()


def _empty_summary(worker_id: str) -> dict[str, Any]:
    return {
        "worker_id": worker_id,
        "processed": 0,
        "failed": 0,
        "enqueued_links": 0,
        "forms_extraction_failures": 0,
        "captured_requests": 0,
        "api_requests": 0,
        "static_requests": 0,
        "network_capture_failures": 0,
    }


def _empty_delta() -> dict[str, int]:
    return {
        "processed": 0,
        "failed": 0,
        "enqueued_links": 0,
        "forms_extraction_failures": 0,
        "captured_requests": 0,
        "api_requests": 0,
        "static_requests": 0,
        "network_capture_failures": 0,
    }


async def _wait_for_network_idle(page: Any, *, worker_id: str) -> None:
    """Best-effort wait so inline ``fetch(...)`` finishes before capture flush.

    Bounded by ``NETWORK_IDLE_WAIT_MS``; timeouts and Playwright errors are
    swallowed so a slow page can never hang the crawl.
    """
    try:
        await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_WAIT_MS)
    except PlaywrightTimeoutError:
        logger.debug(
            "%s: networkidle wait timed out after %sms (continuing)",
            worker_id,
            NETWORK_IDLE_WAIT_MS,
        )
        return
    except Exception:  # noqa: BLE001 - best-effort wait, never raise
        logger.debug("%s: networkidle wait ended with error (continuing)", worker_id)
        return
    else:
        logger.debug("%s: networkidle reached within %sms", worker_id, NETWORK_IDLE_WAIT_MS)


async def _enqueue_discovered_links(
    db: Any,
    *,
    config: dict[str, Any],
    links: list[str],
    parent_depth: int,
    discovered_from: str,
    worker_id: str,
) -> int:
    scan_id = config["scan_id"]
    child_depth = parent_depth + 1
    enqueued = 0
    persist_error_reported = False
    for link in links:
        normalized = normalize_url(link)
        if normalized is None:
            continue
        allowed, _ = should_enqueue_url(normalized, child_depth, config)
        if not allowed:
            continue
        try:
            await upsert_link(
                db,
                scan_id=scan_id,
                normalized_url=normalized,
                first_seen_on=discovered_from,
                depth_first_seen=child_depth,
            )
        except Exception as exc:  # noqa: BLE001 - one link must not stop the page
            logger.warning(
                "upsert_link failed (url=%s): %s: %s",
                normalized,
                exc.__class__.__name__,
                exc,
            )
            if not persist_error_reported:
                persist_error_reported = True
                await record_error(
                    db,
                    scan_id=scan_id,
                    phase="links_persist",
                    error_type="LINK_PERSIST_FAILED",
                    message=f"{exc.__class__.__name__}: {exc}",
                    url=link,
                    normalized_url=normalized,
                    worker_id=worker_id,
                    context={"discovered_from": discovered_from, "depth": child_depth},
                )
            continue
        outcome = await enqueue_url(
            db,
            scan_id=scan_id,
            raw_url=link,
            depth=child_depth,
            config=config,
            discovered_from=discovered_from,
        )
        if outcome["enqueued"]:
            enqueued += 1
    logger.info(
        "%s: link discovery from=%s depth=%s raw_links=%s new_queue_entries=%s",
        worker_id,
        discovered_from,
        child_depth,
        len(links),
        enqueued,
    )
    return enqueued


async def _persist_forms(
    db: Any,
    *,
    scan_id: str,
    forms: list[dict],
    page_url: str,
    worker_id: str,
) -> None:
    n = len(forms)
    if n:
        logger.info("%s: persisting %s form(s) for page_url=%s", worker_id, n, page_url)
    persist_error_reported = False
    for form in forms:
        try:
            digest = form_hash(form, page_url)
        except Exception as exc:  # noqa: BLE001 - skip the one malformed form
            logger.warning(
                "form_hash failed (page_url=%s): %s: %s",
                page_url,
                exc.__class__.__name__,
                exc,
            )
            continue
        try:
            await upsert_form(
                db,
                scan_id=scan_id,
                form_doc={
                    "form_hash": digest,
                    "page_url": page_url,
                    "action": form.get("action"),
                    "method": form.get("method"),
                    "fields": form.get("fields"),
                    "buttons": form.get("buttons"),
                    "csrf_detected": bool(form.get("csrf_detected")),
                },
            )
        except Exception as exc:  # noqa: BLE001 - one bad form must not stop the page
            logger.warning(
                "upsert_form failed (page_url=%s): %s: %s",
                page_url,
                exc.__class__.__name__,
                exc,
            )
            if not persist_error_reported:
                persist_error_reported = True
                await record_error(
                    db,
                    scan_id=scan_id,
                    phase="forms_persist",
                    error_type="FORM_PERSIST_FAILED",
                    message=f"{exc.__class__.__name__}: {exc}",
                    url=page_url,
                    worker_id=worker_id,
                )


async def _process_item(
    *,
    context: Any,
    db: Any,
    config: dict[str, Any],
    worker_id: str,
    item: dict[str, Any],
) -> dict[str, Any]:
    scan_id = config["scan_id"]
    queued_url = item["url"]
    queued_normalized = item["normalized_url"]
    depth = item["depth"]

    logger.info(
        "%s: begin queue item depth=%s url=%s normalized=%s",
        worker_id,
        depth,
        queued_url,
        queued_normalized,
    )

    page = await context.new_page()
    logger.info("%s: new Playwright page for %s", worker_id, queued_url)
    capture = PageNetworkCapture(page_url=queued_url)
    capture.attach(page)
    try:
        try:
            logger.info("%s: navigating goto wait_until=domcontentloaded url=%s", worker_id, queued_url)
            response = await page.goto(
                queued_url,
                wait_until="domcontentloaded",
                timeout=NAVIGATION_TIMEOUT_MS,
            )
        except PlaywrightTimeoutError as exc:
            logger.warning(
                "scan_id=%s %s: navigation timeout url=%s (%s)",
                scan_id,
                worker_id,
                queued_url,
                exc,
            )
            await record_error(
                db,
                scan_id=scan_id,
                phase="navigation",
                error_type="NAVIGATION_TIMEOUT",
                message=str(exc),
                url=queued_url,
                normalized_url=queued_normalized,
                worker_id=worker_id,
            )
            await mark_failed(
                db,
                scan_id=scan_id,
                normalized_url=queued_normalized,
                error_message=f"navigation timeout: {exc}",
                retryable=True,
                worker_id=worker_id,
            )
            delta = _empty_delta()
            delta["failed"] = 1
            logger.info(
                "%s: queue item failed (navigation timeout) normalized=%s",
                worker_id,
                queued_normalized,
            )
            return delta
        except PlaywrightError as exc:
            logger.warning(
                "scan_id=%s %s: navigation failed url=%s %s: %s",
                scan_id,
                worker_id,
                queued_url,
                exc.__class__.__name__,
                exc,
            )
            await record_error(
                db,
                scan_id=scan_id,
                phase="navigation",
                error_type="NAVIGATION_FAILED",
                message=f"{exc.__class__.__name__}: {exc}",
                url=queued_url,
                normalized_url=queued_normalized,
                worker_id=worker_id,
            )
            await mark_failed(
                db,
                scan_id=scan_id,
                normalized_url=queued_normalized,
                error_message=f"navigation error: {exc}",
                retryable=True,
                worker_id=worker_id,
            )
            delta = _empty_delta()
            delta["failed"] = 1
            logger.info(
                "%s: queue item failed (navigation error) normalized=%s",
                worker_id,
                queued_normalized,
            )
            return delta

        status_code = response.status if response is not None else None
        content_type = ""
        if response is not None:
            content_type = response.headers.get("content-type", "") or ""
        is_html = content_type.lower().startswith("text/html")

        final_url = page.url
        final_normalized = normalize_url(final_url) or queued_normalized

        logger.info(
            "%s: navigation ok status=%s content_type=%s is_html=%s final_url=%s",
            worker_id,
            status_code,
            (content_type[:120] + "…") if len(content_type) > 120 else content_type,
            is_html,
            final_url,
        )

        if is_html:
            try:
                logger.info("%s: waiting for load state (HTML page)", worker_id)
                await page.wait_for_load_state("load", timeout=5000)
            except Exception:  # noqa: BLE001 - load-state wait is best-effort
                logger.info("%s: load state wait finished (timeout or error ignored)", worker_id)
            else:
                logger.info("%s: load state reached", worker_id)
            try:
                links = await extract_links(page, base_url=final_url)
            except Exception:  # noqa: BLE001 - extraction must not crash crawl
                links = []
                logger.warning("%s: extract_links failed; treating as zero links url=%s", worker_id, final_url)
            logger.info("%s: extract_links count=%s url=%s", worker_id, len(links), final_url)
            try:
                forms_list, forms_err = await extract_forms(page, final_url)
            except Exception as exc:  # noqa: BLE001 - extraction must not crash crawl
                forms_list = []
                forms_err = f"{exc.__class__.__name__}: {exc}"
                logger.warning(
                    "%s: extract_forms raised unexpectedly: %s (url=%s)",
                    worker_id,
                    forms_err,
                    final_url,
                )
            if forms_err:
                logger.warning(
                    "%s: extract_forms reported error url=%s detail=%s",
                    worker_id,
                    final_url,
                    forms_err,
                )
                await record_error(
                    db,
                    scan_id=scan_id,
                    phase="forms_extract",
                    error_type="FORMS_EXTRACTION_FAILED",
                    message=forms_err,
                    url=final_url,
                    normalized_url=final_normalized,
                    worker_id=worker_id,
                )
            forms_extraction_failures = 1 if forms_err else 0
            logger.info(
                "%s: extract_forms forms=%s extraction_error=%s",
                worker_id,
                len(forms_list),
                bool(forms_err),
            )
            try:
                metadata = await extract_page_metadata(page)
            except Exception:  # noqa: BLE001 - metadata is non-critical
                metadata = empty_page_metadata()
                logger.info("%s: extract_page_metadata failed; using empty metadata url=%s", worker_id, final_url)

            scripts = metadata["scripts"]
            scripts_count = len(scripts.get("external_srcs") or []) + int(
                scripts.get("inline_script_count") or 0
            )
            buttons_meta = metadata.get("buttons") or []
            buttons_count = len(buttons_meta) if isinstance(buttons_meta, list) else 0

            await _persist_forms(
                db,
                scan_id=scan_id,
                forms=forms_list,
                page_url=final_url,
                worker_id=worker_id,
            )

            enqueued_links = await _enqueue_discovered_links(
                db,
                config=config,
                links=links,
                parent_depth=depth,
                discovered_from=final_normalized,
                worker_id=worker_id,
            )

            title = ""
            candidate_title = metadata.get("title")
            if isinstance(candidate_title, str):
                title = candidate_title.strip()
            if not title:
                try:
                    title = await page.title()
                except PlaywrightError:
                    title = ""

            page_doc = {
                "buttons_count": buttons_count,
                "content_type": content_type,
                "crawled_at": _utcnow(),
                "depth": depth,
                "final_url": final_url,
                "forms_count": len(forms_list),
                "links_count": len(links),
                "metadata": metadata,
                "normalized_url": final_normalized,
                "scan_id": scan_id,
                "scripts_count": scripts_count,
                "status_code": status_code,
                "title": title,
                "url": queued_url,
            }

            logger.info(
                "%s: waiting up to %sms for networkidle before capture flush url=%s",
                worker_id,
                NETWORK_IDLE_WAIT_MS,
                final_url,
            )
            await _wait_for_network_idle(page, worker_id=worker_id)
        else:
            logger.info(
                "%s: non-HTML response; skipping link/form extraction url=%s",
                worker_id,
                final_url,
            )
            title = ""
            try:
                title = await page.title()
            except PlaywrightError:
                title = ""
            metadata = empty_page_metadata()
            page_doc = {
                "buttons_count": 0,
                "content_type": content_type,
                "crawled_at": _utcnow(),
                "depth": depth,
                "final_url": final_url,
                "forms_count": 0,
                "links_count": 0,
                "metadata": metadata,
                "normalized_url": final_normalized,
                "scan_id": scan_id,
                "scripts_count": 0,
                "status_code": status_code,
                "title": title,
                "url": queued_url,
            }
            enqueued_links = 0
            forms_extraction_failures = 0

        logger.info(
            "%s: upsert_page normalized_url=%s title=%r status_code=%s",
            worker_id,
            final_normalized,
            (title[:200] + "…") if isinstance(title, str) and len(title) > 200 else title,
            status_code,
        )
        try:
            await upsert_page(db, scan_id=scan_id, page_doc=page_doc)
        except Exception as exc:  # noqa: BLE001 - persistence failure must not crash worker
            logger.warning(
                "upsert_page failed (url=%s): %s: %s",
                final_url,
                exc.__class__.__name__,
                exc,
            )
            await record_error(
                db,
                scan_id=scan_id,
                phase="page_persist",
                error_type="PAGE_PERSIST_FAILED",
                message=f"{exc.__class__.__name__}: {exc}",
                url=final_url,
                normalized_url=final_normalized,
                worker_id=worker_id,
            )
        else:
            logger.info("%s: upsert_page stored successfully", worker_id)

        capture_summary = await _safe_flush_capture(
            capture,
            db=db,
            scan_id=scan_id,
            page_url=queued_url,
            worker_id=worker_id,
        )
        logger.info(
            "%s: network capture flush captured=%s api=%s static=%s failures=%s",
            worker_id,
            capture_summary.get("captured_requests"),
            capture_summary.get("api_requests"),
            capture_summary.get("static_requests"),
            capture_summary.get("network_capture_failures"),
        )

        await mark_done(
            db,
            scan_id=scan_id,
            normalized_url=queued_normalized,
            worker_id=worker_id,
        )
        delta = _empty_delta()
        delta["processed"] = 1
        delta["enqueued_links"] = enqueued_links
        delta["forms_extraction_failures"] = forms_extraction_failures
        delta.update(capture_summary)
        logger.info(
            "%s: queue item finished ok normalized=%s delta=%s",
            worker_id,
            queued_normalized,
            {k: delta[k] for k in sorted(delta)},
        )
        return delta
    except Exception as exc:  # noqa: BLE001 - one bad page must not crash the worker
        logger.exception(
            "scan_id=%s %s: unexpected page processing failure url=%s normalized=%s",
            scan_id,
            worker_id,
            queued_url,
            queued_normalized,
        )
        await record_error(
            db,
            scan_id=scan_id,
            phase="page_process",
            error_type="PAGE_PROCESS_FAILED",
            message=f"{exc.__class__.__name__}: {exc}",
            url=queued_url,
            normalized_url=queued_normalized,
            worker_id=worker_id,
            traceback_text=traceback.format_exc(),
        )
        await mark_failed(
            db,
            scan_id=scan_id,
            normalized_url=queued_normalized,
            error_message=f"unexpected: {exc.__class__.__name__}: {exc}",
            retryable=False,
            worker_id=worker_id,
        )
        delta = _empty_delta()
        delta["failed"] = 1
        logger.info(
            "%s: queue item failed (unexpected) normalized=%s",
            worker_id,
            queued_normalized,
        )
        return delta
    finally:
        try:
            await page.close()
        except Exception:  # noqa: BLE001 - page may already be closed by browser shutdown
            pass


async def _safe_flush_capture(
    capture: PageNetworkCapture,
    *,
    db: Any,
    scan_id: str,
    page_url: str,
    worker_id: str,
) -> dict[str, int]:
    """Run capture.flush; never let its failures propagate to the worker."""
    logger.info("%s: flushing network capture for page_url=%s", worker_id, page_url)
    try:
        return await capture.flush(db, scan_id=scan_id)
    except Exception as exc:  # noqa: BLE001 - capture must not fail page crawl
        logger.warning(
            "network capture flush failed scan_id=%s worker_id=%s page_url=%s: %s: %s",
            scan_id,
            worker_id,
            page_url,
            exc.__class__.__name__,
            exc,
        )
        await record_error(
            db,
            scan_id=scan_id,
            phase="network_capture",
            error_type="NETWORK_CAPTURE_FAILED",
            message=f"{exc.__class__.__name__}: {exc}",
            url=page_url,
            worker_id=worker_id,
        )
        return {
            "captured_requests": 0,
            "api_requests": 0,
            "static_requests": 0,
            "network_capture_failures": 1,
        }


async def run_worker(
    *,
    db: Any,
    browser: Any,
    config: dict[str, Any],
    worker_id: str,
    session_state_path: str,
    stop_event: asyncio.Event | None = None,
    shared_context: Any | None = None,
) -> dict[str, Any]:
    summary = _empty_summary(worker_id)
    scan_id = config["scan_id"]
    max_pages = config["max_pages"]
    idle_attempts = 0

    logger.info(
        "%s: worker starting scan_id=%s max_pages=%s session_state=%s",
        worker_id,
        scan_id,
        max_pages,
        session_state_path,
    )

    owns_context = shared_context is None
    if owns_context:
        context = await browser.new_context(storage_state=session_state_path)
        logger.info("%s: browser context created (storage_state loaded)", worker_id)
    else:
        context = shared_context
        logger.info("%s: using shared browser context", worker_id)
    try:
        while True:
            if _should_stop(stop_event):
                logger.info("%s: stop event set; exiting worker loop", worker_id)
                break
            if await count_pages(db, scan_id=scan_id) >= max_pages:
                logger.info(
                    "%s: max_pages=%s reached (stored page count); exiting worker loop",
                    worker_id,
                    max_pages,
                )
                break

            item = await claim_next(db, scan_id=scan_id, worker_id=worker_id)
            if item is None:
                idle_attempts += 1
                logger.debug(
                    "%s: no queue item (idle attempt %s/%s)",
                    worker_id,
                    idle_attempts,
                    MAX_IDLE_ATTEMPTS,
                )
                if idle_attempts >= MAX_IDLE_ATTEMPTS:
                    logger.info(
                        "%s: queue idle after %s attempts; exiting worker loop",
                        worker_id,
                        MAX_IDLE_ATTEMPTS,
                    )
                    break
                if _should_stop(stop_event):
                    logger.info("%s: stop event set during idle wait; exiting worker loop", worker_id)
                    break
                logger.debug(
                    "%s: queue empty; sleeping %ss before next claim",
                    worker_id,
                    IDLE_SLEEP_SECONDS,
                )
                await asyncio.sleep(IDLE_SLEEP_SECONDS)
                if _should_stop(stop_event):
                    logger.info("%s: stop event set after idle sleep; exiting worker loop", worker_id)
                    break
                continue

            idle_attempts = 0
            logger.info(
                "%s: claimed queue item url=%s depth=%s",
                worker_id,
                item.get("url"),
                item.get("depth"),
            )
            delta = await _process_item(
                context=context,
                db=db,
                config=config,
                worker_id=worker_id,
                item=item,
            )
            for key in (
                "processed",
                "failed",
                "enqueued_links",
                "forms_extraction_failures",
                "captured_requests",
                "api_requests",
                "static_requests",
                "network_capture_failures",
            ):
                summary[key] += delta.get(key, 0)
            logger.info(
                "%s: cumulative summary processed=%s failed=%s enqueued_links=%s captured=%s",
                worker_id,
                summary["processed"],
                summary["failed"],
                summary["enqueued_links"],
                summary["captured_requests"],
            )
    except asyncio.CancelledError:
        raise
    finally:
        if owns_context:
            logger.info("%s: closing browser context", worker_id)
            try:
                await context.close()
            except Exception:  # noqa: BLE001 - context may already be torn down
                logger.warning("%s: context.close failed or context already closed", worker_id)

    logger.info("%s: worker finished %s", worker_id, summary)
    return summary
