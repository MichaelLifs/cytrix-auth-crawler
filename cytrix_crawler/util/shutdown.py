"""Graceful shutdown helpers.

The crawler uses a single ``asyncio.Event`` as its shutdown signal. Workers and
the orchestrator poll this event between safe boundaries (queue claims, idle
sleeps, item completion) rather than relying on task cancellation. That keeps
shutdown deterministic on both POSIX and Windows, where signal semantics
differ.

POSIX: ``loop.add_signal_handler`` for SIGINT/SIGTERM.
Windows: ``loop.add_signal_handler`` raises ``NotImplementedError``, so we fall
back to ``signal.signal``. On Windows SIGTERM is not delivered for Ctrl+C; only
SIGINT (Ctrl+C) is meaningful. We install both handlers when supported and
ignore failures silently — the worst case is the previous behavior (Ctrl+C
prints a traceback) rather than a crash.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any, Callable

logger = logging.getLogger(__name__)

_SHUTDOWN_SIGNALS: tuple[signal.Signals, ...] = tuple(
    sig
    for name in ("SIGINT", "SIGTERM")
    if (sig := getattr(signal, name, None)) is not None
)


def create_stop_event() -> asyncio.Event:
    """Return a fresh asyncio.Event used as the crawler stop signal."""
    return asyncio.Event()


def install_signal_handlers(stop_event: asyncio.Event) -> Callable[[], None]:
    """Install SIGINT/SIGTERM handlers that set ``stop_event``.

    Returns a cleanup callable that restores prior handlers. Installation is
    best-effort: in environments where signal handling is unavailable (e.g.
    non-main thread, restricted runtime), the call is a no-op.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    installed_via_loop: list[signal.Signals] = []
    previous_handlers: dict[signal.Signals, Any] = {}

    def _trigger() -> None:
        if not stop_event.is_set():
            stop_event.set()

    for sig in _SHUTDOWN_SIGNALS:
        if loop is not None:
            try:
                loop.add_signal_handler(sig, _trigger)
                installed_via_loop.append(sig)
                continue
            except (NotImplementedError, RuntimeError, ValueError):
                pass
        try:
            previous_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, lambda *_args: _trigger())
        except (ValueError, OSError):
            logger.debug("Could not install handler for %s", sig.name)

    def cleanup() -> None:
        for sig in installed_via_loop:
            try:
                loop.remove_signal_handler(sig)  # type: ignore[union-attr]
            except (NotImplementedError, RuntimeError, ValueError):
                pass
        for sig, prev in previous_handlers.items():
            try:
                signal.signal(sig, prev)
            except (ValueError, OSError, TypeError):
                pass

    return cleanup
