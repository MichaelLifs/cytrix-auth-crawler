from __future__ import annotations

from cytrix_crawler.extract.normalize import normalize_url


def test_normalize_lowercases_scheme_and_host() -> None:
    normalized = normalize_url("HTTP://Example.COM/Dashboard")
    assert normalized == "http://example.com/Dashboard"


def test_normalize_strips_default_ports() -> None:
    normalized = normalize_url("http://example.com:80/path")
    assert normalized == "http://example.com/path"


def test_normalize_preserves_non_default_ports() -> None:
    normalized = normalize_url("https://example.com:8443/path")
    assert normalized == "https://example.com:8443/path"


def test_normalize_removes_fragment() -> None:
    normalized = normalize_url("https://example.com/a#section")
    assert normalized == "https://example.com/a"


def test_normalize_sorts_query_params() -> None:
    normalized = normalize_url("https://example.com/a?b=2&a=1")
    assert normalized == "https://example.com/a?a=1&b=2"


def test_normalize_removes_tracking_params() -> None:
    normalized = normalize_url("https://example.com/a?utm_source=x&gclid=abc&a=1")
    assert normalized == "https://example.com/a?a=1"


def test_normalize_preserves_meaningful_query_params() -> None:
    normalized = normalize_url("https://example.com/search?q=term&page=2")
    assert normalized == "https://example.com/search?page=2&q=term"


def test_normalize_handles_relative_with_base_url() -> None:
    normalized = normalize_url("/profile", base_url="http://localhost:8000/dashboard")
    assert normalized == "http://localhost:8000/profile"


def test_normalize_returns_none_for_relative_without_base_url() -> None:
    normalized = normalize_url("/profile")
    assert normalized is None


def test_normalize_returns_none_for_unsupported_scheme() -> None:
    normalized = normalize_url("javascript:void(0)")
    assert normalized is None


def test_normalize_handles_malformed_input_safely() -> None:
    normalized = normalize_url("http://example.com:99999/path")
    assert normalized is None


def test_normalize_removes_trailing_slash_except_root() -> None:
    assert normalize_url("https://example.com/") == "https://example.com/"
    assert normalize_url("https://example.com/path/") == "https://example.com/path"

