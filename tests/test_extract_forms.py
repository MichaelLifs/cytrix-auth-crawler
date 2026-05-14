"""Unit tests for form extraction (stubbed DOM JSON)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from cytrix_crawler.extract.forms import extract_forms


class _FormsStubPage:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    async def evaluate(self, _script: str):
        return self._payload


def _run(coro):
    return asyncio.run(coro)


def test_extract_forms_handles_missing_attributes() -> None:
    payload = [
        {"action": None, "method": None, "fields": [{}], "buttons": [None, 123, " Submit "]},
        {
            "action": "",
            "method": "post",
            "fields": [
                {"name": None, "type": None, "required": "required"},
                {"name": "x", "type": "TEXT", "required": False},
            ],
            "buttons": ["ok"],
            "csrf_detected": False,
        },
    ]
    page = _FormsStubPage(payload)
    forms, err = _run(extract_forms(page, "http://localhost:8000/"))

    assert err is None
    assert len(forms) == 2
    assert forms[0]["method"] == "GET"
    assert forms[0]["buttons"] == ["Submit"]
    assert forms[1]["fields"][0]["name"] == ""
    assert forms[1]["fields"][0]["type"] == ""
    assert forms[1]["fields"][0]["required"] is True
    assert forms[1]["fields"][1]["type"] == "text"
    assert forms[1]["buttons"] == ["ok"]


def test_extract_forms_returns_empty_on_eval_failure() -> None:
    class _BadPage:
        async def evaluate(self, _script: str):
            raise RuntimeError("detached")

    forms, err = _run(extract_forms(_BadPage(), "http://localhost:8000/a"))
    assert forms == []
    assert err is not None
    assert "page.evaluate failed" in err
    assert "detached" in err


def test_extract_forms_returns_empty_when_not_list() -> None:
    page = _FormsStubPage({"not": "list"})
    forms, err = _run(extract_forms(page, "http://localhost:8000/a"))
    assert forms == []
    assert err is not None
    assert "expected list" in err


def test_extract_forms_logs_eval_failure(caplog: pytest.LogCaptureFixture) -> None:
    class _BadPage:
        async def evaluate(self, _script: str):
            raise RuntimeError("boom")

    with caplog.at_level("WARNING"):
        _run(extract_forms(_BadPage(), "http://localhost:8000/x"))
    assert "extract_forms" in caplog.text
    assert "boom" in caplog.text
