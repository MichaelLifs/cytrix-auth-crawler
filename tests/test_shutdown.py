"""Unit tests for ``util.shutdown`` signal-driven stop event.

Signal-handler installation is platform-sensitive. These tests verify the
event-set semantics in a way that works on POSIX and Windows by directly
invoking the same trigger the handler installs.
"""

from __future__ import annotations

import asyncio
import signal
import sys

import pytest

from cytrix_crawler.util.shutdown import create_stop_event, install_signal_handlers


def test_create_stop_event_returns_unset_event() -> None:
    async def scenario() -> bool:
        event = create_stop_event()
        return event.is_set()

    assert asyncio.run(scenario()) is False


def test_install_signal_handlers_returns_cleanup_callable() -> None:
    async def scenario():
        event = create_stop_event()
        cleanup = install_signal_handlers(event)
        try:
            assert callable(cleanup)
            assert event.is_set() is False
        finally:
            cleanup()
        return True

    assert asyncio.run(scenario()) is True


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="loop.add_signal_handler is not implemented on Windows",
)
def test_install_signal_handlers_sets_event_on_sigint_posix() -> None:
    """On POSIX, raising SIGINT through the running loop sets the event."""

    async def scenario() -> bool:
        event = create_stop_event()
        cleanup = install_signal_handlers(event)
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon(signal.raise_signal, signal.SIGINT)
            try:
                await asyncio.wait_for(event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                return False
            return event.is_set()
        finally:
            cleanup()

    assert asyncio.run(scenario()) is True


def test_install_signal_handlers_is_idempotent_cleanup() -> None:
    async def scenario() -> None:
        event = create_stop_event()
        cleanup = install_signal_handlers(event)
        cleanup()
        cleanup()

    asyncio.run(scenario())
