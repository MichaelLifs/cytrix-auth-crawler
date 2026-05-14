"""Playwright login/session workflow."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Browser, Playwright

from cytrix_crawler.auth.session_store import ensure_session_dir, session_state_exists
from cytrix_crawler.auth.validator import LoginResult, validate_login_success


async def _run_fresh_login_on_context(
    context: Any, config: dict[str, Any], session_state_path: str
) -> LoginResult:
    """Log in on a new empty context; persist ``storage_state`` when successful."""
    page = await context.new_page()
    try:
        await page.goto(config["login_url"], wait_until="domcontentloaded", timeout=10000)

        for step in config["login_steps"]:
            await page.locator(step["selector"]).fill(step["value"], timeout=10000)

        await page.locator(config["submit_selector"]).click(timeout=10000)
        await page.wait_for_load_state("domcontentloaded", timeout=10000)

        login_result = await validate_login_success(page=page, context=context, config=config)
        if login_result.success:
            await context.storage_state(path=session_state_path)
            login_result.indicators.append("storage_state_saved")
        return login_result
    finally:
        try:
            await page.close()
        except Exception:  # noqa: BLE001 - teardown best-effort
            pass


async def prepare_session_for_crawl(
    browser: "Browser", config: dict[str, Any], session_state_path: str
) -> tuple[Any, LoginResult]:
    """Validate saved session or log in using an already-launched browser.

    Returns ``(browser_context, result)``. The context stays open for the crawl
    (workers call ``new_page()`` so each URL opens as a tab in headed mode).
    The caller must ``close()`` the context when finished.
    """
    ensure_session_dir(session_state_path)

    if session_state_exists(session_state_path):
        context = await browser.new_context(storage_state=session_state_path)
        page = await context.new_page()
        try:
            await page.goto(
                config["start_url_after_login"],
                wait_until="domcontentloaded",
                timeout=10000,
            )
            result = await validate_login_success(page=page, context=context, config=config)
        except Exception as exc:  # noqa: BLE001 - treat as invalid session
            result = LoginResult(
                success=False,
                message=f"Existing session is invalid: {exc}",
            )
        finally:
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass

        if result.success:
            return context, result

        await context.close()
        context = await browser.new_context()
        result = await _run_fresh_login_on_context(context, config, session_state_path)
        return context, result

    context = await browser.new_context()
    result = await _run_fresh_login_on_context(context, config, session_state_path)
    return context, result


async def perform_login(
    playwright: "Playwright", config: dict[str, Any], session_state_path: str
) -> LoginResult:
    """Run login flow, validate authentication, and persist storage state."""
    ensure_session_dir(session_state_path)
    headless = bool(config.get("headless", False))
    browser = await playwright.chromium.launch(headless=headless)
    try:
        context = await browser.new_context()
        try:
            return await _run_fresh_login_on_context(context, config, session_state_path)
        finally:
            await context.close()
    finally:
        await browser.close()


async def validate_existing_session(
    playwright: "Playwright" | None, config: dict[str, Any], session_state_path: str
) -> LoginResult:
    """Validate whether existing storage_state still authenticates."""
    if not session_state_exists(session_state_path):
        return LoginResult(success=False, message="Session storage_state file does not exist.")

    if playwright is None:
        return LoginResult(success=False, message="Playwright instance is required for session check.")

    headless = bool(config.get("headless", False))
    browser = await playwright.chromium.launch(headless=headless)
    try:
        context = await browser.new_context(storage_state=session_state_path)
        page = await context.new_page()
        try:
            await page.goto(config["start_url_after_login"], wait_until="domcontentloaded", timeout=10000)
            return await validate_login_success(page=page, context=context, config=config)
        finally:
            await context.close()
    except Exception as exc:  # noqa: BLE001 - explicit invalid state signal
        return LoginResult(success=False, message=f"Existing session is invalid: {exc}")
    finally:
        await browser.close()

