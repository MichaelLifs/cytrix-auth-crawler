from __future__ import annotations

from cytrix_crawler.queue.models import (
    QUEUE_COLLECTION,
    QUEUE_STATUSES,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_IN_PROGRESS,
    STATUS_PENDING,
    STATUS_SKIPPED,
)


def test_queue_status_constants_have_expected_values() -> None:
    assert STATUS_PENDING == "pending"
    assert STATUS_IN_PROGRESS == "in_progress"
    assert STATUS_DONE == "done"
    assert STATUS_FAILED == "failed"
    assert STATUS_SKIPPED == "skipped"


def test_queue_statuses_tuple_is_complete_and_unique() -> None:
    assert set(QUEUE_STATUSES) == {
        STATUS_PENDING,
        STATUS_IN_PROGRESS,
        STATUS_DONE,
        STATUS_FAILED,
        STATUS_SKIPPED,
    }
    assert len(QUEUE_STATUSES) == len(set(QUEUE_STATUSES))


def test_queue_collection_name() -> None:
    assert QUEUE_COLLECTION == "crawl_queue"
