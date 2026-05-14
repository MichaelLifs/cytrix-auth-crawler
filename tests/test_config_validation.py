from __future__ import annotations

from copy import deepcopy

import pytest

from cytrix_crawler.config import CONFIG, ConfigError, validate_config


def _valid_config() -> dict:
    return deepcopy(CONFIG)


def test_valid_config_passes_validation() -> None:
    validate_config(_valid_config())


def test_missing_required_fields_are_reported_together() -> None:
    config = _valid_config()
    del config["scan_id"]
    del config["login_url"]
    del config["start_url_after_login"]
    del config["allowed_domains"]

    with pytest.raises(ConfigError) as exc_info:
        validate_config(config)

    message = str(exc_info.value)
    assert "scan_id must be a non-empty string." in message
    assert "login_url must be a valid http/https URL." in message
    assert "start_url_after_login must be a valid http/https URL." in message
    assert "allowed_domains must be a non-empty list of strings." in message


@pytest.mark.parametrize(
    "field,value",
    [
        ("login_url", "not-a-url"),
        ("start_url_after_login", "file:///dashboard"),
    ],
)
def test_invalid_urls_fail(field: str, value: str) -> None:
    config = _valid_config()
    config[field] = value

    with pytest.raises(ConfigError):
        validate_config(config)


def test_invalid_allowed_domains_fail() -> None:
    config = _valid_config()
    config["allowed_domains"] = ["localhost", ""]

    with pytest.raises(ConfigError) as exc_info:
        validate_config(config)

    message = str(exc_info.value)
    assert "allowed_domains must contain only non-empty strings." in message


def test_start_url_host_must_be_allowed() -> None:
    config = _valid_config()
    config["allowed_domains"] = ["example.com"]

    with pytest.raises(ConfigError) as exc_info:
        validate_config(config)

    assert "start_url_after_login host must be included in allowed_domains." in str(
        exc_info.value
    )


def test_invalid_numeric_values_fail() -> None:
    config = _valid_config()
    config["max_depth"] = -1
    config["max_pages"] = 0
    config["concurrency"] = 33

    with pytest.raises(ConfigError) as exc_info:
        validate_config(config)

    message = str(exc_info.value)
    assert "max_depth must be an integer >= 0." in message
    assert "max_pages must be an integer > 0." in message
    assert "concurrency must be an integer between 1 and 32." in message


def test_invalid_login_steps_fail() -> None:
    config = _valid_config()
    config["login_steps"] = [{"selector": " ", "value": 123}, {"selector": "#ok"}]

    with pytest.raises(ConfigError) as exc_info:
        validate_config(config)

    message = str(exc_info.value)
    assert "login_steps[0].selector must be a non-empty string." in message
    assert "login_steps[0].value must be a string." in message
    assert "login_steps[1].value is required." in message


def test_config_for_target_returns_distinct_urls_and_scan_ids() -> None:
    from cytrix_crawler.config import (
        SCAN_ID_DEMO,
        SCAN_ID_PRACTICETESTAUTOMATION,
        config_for_target,
    )

    d = config_for_target("demo")
    p = config_for_target("practicetestautomation")
    assert d["scan_id"] == SCAN_ID_DEMO
    assert p["scan_id"] == SCAN_ID_PRACTICETESTAUTOMATION
    assert d["scan_id"] != p["scan_id"]
    assert "localhost" in d["login_url"] or "demo-app" in d["login_url"] or "127.0.0.1" in d["login_url"]
    assert "practicetestautomation.com" in p["login_url"]


def test_invalid_scan_id_fails() -> None:
    config = _valid_config()
    config["scan_id"] = "bad id with spaces"

    with pytest.raises(ConfigError) as exc_info:
        validate_config(config)

    assert "scan_id must contain only letters, numbers, underscores, or dashes" in str(
        exc_info.value
    )
