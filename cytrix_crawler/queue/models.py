"""Crawl queue status constants.

A queue item is always in exactly one of these states. Kept as plain string
constants because Mongo stores them as strings and we want minimal indirection.
"""

from __future__ import annotations

STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

QUEUE_STATUSES: tuple[str, ...] = (
    STATUS_PENDING,
    STATUS_IN_PROGRESS,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_SKIPPED,
)

QUEUE_COLLECTION = "crawl_queue"
