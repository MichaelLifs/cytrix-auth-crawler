"""Unit tests for link extraction.

We stub the Playwright ``Page`` interface because the real browser is not
needed to exercise the urljoin/dedup/filter logic.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from cytrix_crawler.extract.links import extract_links


@dataclass
class _StubPage:
    hrefs: list

    async def evaluate(self, _script: str) -> list:
        return list(self.hrefs)


def _run(coro):
    return asyncio.run(coro)


def test_extract_links_resolves_relative_hrefs_against_base_url() -> None:
    page = _StubPage(hrefs=["/profile", "settings", "http://localhost:8000/help"])

    result = _run(extract_links(page, base_url="http://localhost:8000/dashboard"))

    assert result == [
        "http://localhost:8000/profile",
        "http://localhost:8000/settings",
        "http://localhost:8000/help",
    ]


def test_extract_links_deduplicates_preserving_first_occurrence() -> None:
    page = _StubPage(
        hrefs=[
            "/profile",
            "/settings",
            "/profile",
            "http://localhost:8000/settings",
        ]
    )

    result = _run(extract_links(page, base_url="http://localhost:8000/dashboard"))

    assert result == [
        "http://localhost:8000/profile",
        "http://localhost:8000/settings",
    ]


def test_extract_links_drops_non_navigational_hrefs() -> None:
    page = _StubPage(
        hrefs=[
            "#section",
            "javascript:void(0)",
            "mailto:admin@example.com",
            "tel:+1234567890",
            "/dashboard",
            "",
            "   ",
        ]
    )

    result = _run(extract_links(page, base_url="http://localhost:8000/"))

    assert result == ["http://localhost:8000/dashboard"]


def test_extract_links_returns_empty_when_page_evaluate_raises() -> None:
    class _ExplodingPage:
        async def evaluate(self, _script: str):
            raise RuntimeError("page detached")

    result = _run(extract_links(_ExplodingPage(), base_url="http://localhost:8000/"))

    assert result == []


def test_extract_links_ignores_non_string_entries() -> None:
    page = _StubPage(hrefs=[None, 42, "/profile"])

    result = _run(extract_links(page, base_url="http://localhost:8000/dashboard"))

    assert result == ["http://localhost:8000/profile"]
