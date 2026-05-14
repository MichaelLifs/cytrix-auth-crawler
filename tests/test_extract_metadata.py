"""Unit tests for metadata extraction stubs."""

from __future__ import annotations

import asyncio

from cytrix_crawler.extract.metadata import extract_page_metadata, empty_page_metadata


class _MetaStubPage:
    def __init__(self, payload):
        self._payload = payload

    async def evaluate(self, _script: str):
        return self._payload


def _run(coro):
    return asyncio.run(coro)


def test_empty_metadata_factory() -> None:
    md = empty_page_metadata()
    assert md["title"] == ""
    assert md["meta_description"] is None
    assert md["canonical_url"] is None


def test_extract_metadata_normalizes_heading_and_buttons() -> None:
    payload = {
        "title": " Demo title ",
        "meta_description": "Hello world",
        "canonical_url": "http://localhost:8000/canonical",
        "headings": {
            "h1": [" Alpha "],
            "h2": ["Beta", "_gamma_"],
            "h3": ["gamma"],
        },
        "scripts": {"external_srcs": ["http://cdn.example/foo.js"], "inline_script_count": 2},
        "buttons": ["Save", "", "Cancel", "Cancel"],
    }
    result = _run(extract_page_metadata(_MetaStubPage(payload)))

    assert result["title"] == "Demo title"
    assert result["meta_description"] == "Hello world"
    assert result["canonical_url"] == "http://localhost:8000/canonical"
    assert result["headings"]["h1"] == ["Alpha"]
    assert result["scripts"]["inline_script_count"] == 2
    assert len(result["scripts"]["external_srcs"]) == 1
    assert result["buttons"] == ["Save", "Cancel"]


def test_extract_metadata_falls_back_on_exceptions() -> None:
    class _Bad:
        async def evaluate(self, _script: str):
            raise RuntimeError("boom")

    result = _run(extract_page_metadata(_Bad()))
    assert result["title"] == ""
