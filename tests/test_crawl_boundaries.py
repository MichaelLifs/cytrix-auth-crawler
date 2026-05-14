from __future__ import annotations

from copy import deepcopy

from cytrix_crawler.config import CONFIG
from cytrix_crawler.crawl.boundaries import (
    is_allowed_domain,
    matches_exclude_pattern,
    should_enqueue_url,
)


def _valid_config() -> dict:
    """Use localhost-friendly ``allowed_domains``; boundary tests are URL-scheme focused."""
    c = deepcopy(CONFIG)
    c["allowed_domains"] = ["localhost", "127.0.0.1", "demo-app", "practicetestautomation.com"]
    return c


def test_allows_configured_localhost_url() -> None:
    config = _valid_config()
    allowed, reason = should_enqueue_url("http://localhost/dashboard", depth=1, config=config)
    assert allowed is True
    assert reason is None


def test_rejects_external_domain() -> None:
    config = _valid_config()
    allowed, reason = should_enqueue_url("https://example.com/home", depth=1, config=config)
    assert allowed is False
    assert reason == "domain_not_allowed"


def test_rejects_unsupported_scheme() -> None:
    config = _valid_config()
    allowed, reason = should_enqueue_url("mailto:admin@example.com", depth=1, config=config)
    assert allowed is False
    assert reason == "unsupported_scheme"


def test_rejects_excluded_pattern_case_insensitive() -> None:
    config = _valid_config()
    allowed, reason = should_enqueue_url(
        "http://localhost/account/DELETE-confirm", depth=1, config=config
    )
    assert allowed is False
    assert reason == "excluded_pattern"


def test_rejects_depth_above_max_depth() -> None:
    config = _valid_config()
    allowed, reason = should_enqueue_url(
        "http://localhost/dashboard", depth=config["max_depth"] + 1, config=config
    )
    assert allowed is False
    assert reason == "max_depth_exceeded"


def test_allows_depth_equal_to_max_depth() -> None:
    config = _valid_config()
    allowed, reason = should_enqueue_url(
        "http://localhost/dashboard", depth=config["max_depth"], config=config
    )
    assert allowed is True
    assert reason is None


def test_returns_clear_reason_for_invalid_url() -> None:
    config = _valid_config()
    allowed, reason = should_enqueue_url("://invalid-url", depth=0, config=config)
    assert allowed is False
    assert reason == "invalid_url"


def test_allowed_domain_checks_hostname_not_full_netloc() -> None:
    config = _valid_config()
    allowed, reason = should_enqueue_url("http://localhost:8000/dashboard", depth=1, config=config)
    assert allowed is True
    assert reason is None
    assert is_allowed_domain("http://localhost:8000/dashboard", ["localhost"]) is True


def test_matches_exclude_pattern_is_case_insensitive_substring() -> None:
    assert matches_exclude_pattern("http://localhost/Logout", ["logout"]) is True
    assert matches_exclude_pattern("http://localhost/dashboard", ["logout"]) is False

