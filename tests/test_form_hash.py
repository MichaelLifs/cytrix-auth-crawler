"""Unit tests for form hashing."""

from __future__ import annotations

from cytrix_crawler.dedupe.hashing import form_hash


def _base_form() -> dict:
    return {
        "action": "http://localhost:8000/save",
        "buttons": ["Go", "Cancel"],
        "fields": [{"name": "a", "type": "text", "required": False}],
        "method": "POST",
        "csrf_detected": False,
    }


def test_form_hash_stable_when_field_order_changes() -> None:
    page_url = "http://localhost:8000/page1"

    fields_a_first = [{"name": "a", "type": "text", "required": False}, {"name": "z", "type": "hidden", "required": False}]
    fields_z_first = list(reversed(fields_a_first))

    f1 = {**_base_form(), "fields": fields_a_first, "buttons": ["Secondary", "Primary"]}
    f2 = {**_base_form(), "fields": fields_z_first, "buttons": ["Primary", "Secondary"]}

    assert form_hash(f1, page_url) == form_hash(f2, page_url)


def test_form_hash_changes_when_action_method_or_page_change() -> None:
    base = _base_form()
    page_url = "http://localhost:8000/here"

    h0 = form_hash(base, page_url)
    assert form_hash({**base, "action": "http://localhost:8000/other"}, page_url) != h0
    assert form_hash({**base, "method": "GET"}, page_url) != h0
    assert form_hash(base, "http://localhost:8000/there") != h0


def test_same_form_on_different_pages_is_distinct() -> None:
    form = _base_form()
    assert form_hash(form, "http://localhost:8000/a") != form_hash(form, "http://localhost:8000/b")


def test_form_hash_prefix_sha256() -> None:
    h = form_hash(_base_form(), "http://localhost:8000/z")
    assert h.startswith("sha256:")
    rest = h.split(":", 1)[1]
    assert len(rest) == 64
