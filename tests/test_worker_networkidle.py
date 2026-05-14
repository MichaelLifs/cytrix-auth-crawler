"""Unit tests for the bounded networkidle wait helper in the worker.

The real concern is that the helper is best-effort: it must attempt the
``networkidle`` wait when called, but never let a timeout or Playwright error
fail the page crawl.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from cytrix_crawler.crawl.worker import NETWORK_IDLE_WAIT_MS, _wait_for_network_idle


class _FakePage:
    def __init__(self, *, raise_exc: Exception | None = None) -> None:
        self.wait_for_load_state = AsyncMock()
        if raise_exc is not None:
            self.wait_for_load_state.side_effect = raise_exc


def test_wait_for_network_idle_calls_playwright_with_bounded_timeout() -> None:
    page = _FakePage()

    asyncio.run(_wait_for_network_idle(page, worker_id="test-worker"))

    page.wait_for_load_state.assert_awaited_once()
    args, kwargs = page.wait_for_load_state.call_args
    assert args == ("networkidle",) or kwargs.get("state") == "networkidle"
    assert kwargs.get("timeout") == NETWORK_IDLE_WAIT_MS


def test_wait_for_network_idle_swallows_timeout() -> None:
    page = _FakePage(raise_exc=PlaywrightTimeoutError("timeout"))

    asyncio.run(_wait_for_network_idle(page, worker_id="test-worker"))

    page.wait_for_load_state.assert_awaited_once()


def test_wait_for_network_idle_swallows_unexpected_exception() -> None:
    page = _FakePage(raise_exc=RuntimeError("page closed"))

    asyncio.run(_wait_for_network_idle(page, worker_id="test-worker"))

    page.wait_for_load_state.assert_awaited_once()


def test_network_idle_constant_is_bounded() -> None:
    """A regression guard: the wait must stay small enough that a slow page
    can never hang the crawl significantly."""
    assert 0 < NETWORK_IDLE_WAIT_MS <= 5_000
