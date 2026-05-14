"""Central configuration for the CYTRIX crawler assignment."""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse
from typing import Any


# Stable ``scan_id`` per target (embedded in each config dict below).
SCAN_ID_PRACTICETESTAUTOMATION = "scan_cytrix_practicetestautomation"
SCAN_ID_DEMO = "scan_demo_app"

def _truthy_env(name: str) -> bool:
    v = os.getenv(name)
    if v is None or not str(v).strip():
        return False
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _demo_base_url() -> str:
    """Base origin for the local ``demo_app`` only (no trailing slash)."""
    return os.getenv("DEMO_BASE_URL", "http://localhost:8000").rstrip("/")


_DEMO_BASE = _demo_base_url()

_CONFIG_DEMO: dict[str, Any] = {
    "scan_id": SCAN_ID_DEMO,
    "login_url": f"{_DEMO_BASE}/login",
    "start_url_after_login": f"{_DEMO_BASE}/dashboard",
    "allowed_domains": ["localhost", "127.0.0.1", "demo-app"],
    "max_depth": 3,
    "max_pages": 100,
    "concurrency": 3,
    "login_steps": [
        {"selector": "#email", "value": "admin@example.com"},
        {"selector": "#password", "value": "Password123!"},
    ],
    "submit_selector": "button[type='submit']",
    "exclude_patterns": ["logout", "delete", "remove"],
    "headless": _truthy_env("PLAYWRIGHT_HEADLESS"),
}

_CONFIG_PRACTICETESTAUTOMATION: dict[str, Any] = {
    "scan_id": SCAN_ID_PRACTICETESTAUTOMATION,
    "login_url": "https://practicetestautomation.com/practice-test-login/",
    "start_url_after_login": "https://practicetestautomation.com/logged-in-successfully/",
    "allowed_domains": ["practicetestautomation.com"],
    "max_depth": 3,
    "max_pages": 100,
    "concurrency": 3,
    "login_steps": [
        {"selector": "#username", "value": "student"},
        {"selector": "#password", "value": "Password123"},
    ],
    "submit_selector": "#submit",
    "exclude_patterns": ["logout", "delete", "remove"],
    "headless": _truthy_env("PLAYWRIGHT_HEADLESS"),
}


def config_for_target(name: str) -> dict[str, Any]:
    """Return the config dict for a single crawl target (demo or practicetestautomation)."""
    key = name.strip().lower()
    if key in ("demo", "1", "d"):
        return _CONFIG_DEMO
    if key in ("practicetestautomation", "practice", "real", "2", "p", "pta"):
        return _CONFIG_PRACTICETESTAUTOMATION
    raise ValueError(
        f"Unknown target {name!r}. Use 'demo' or 'practicetestautomation' "
        "(or set CYTRIX_TARGET to one of those)."
    )


# Default binding for tests and any code that imports ``CONFIG`` without going through ``main.py``.
# ``main.py`` overwrites this once per process after target selection.
CONFIG: dict[str, Any] = _CONFIG_DEMO


MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "cytrix_crawler")
SESSION_STATE_DIR = os.getenv("SESSION_STATE_DIR", "sessions")

_SCAN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ConfigError(ValueError):
    """Raised when configuration contains one or more validation errors."""


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _host_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    return parsed.hostname


def validate_config(config: dict[str, Any]) -> None:
    """Validate scan configuration and raise ConfigError on failure."""
    errors: list[str] = []

    scan_id = config.get("scan_id")
    if not isinstance(scan_id, str) or not scan_id:
        errors.append("scan_id must be a non-empty string.")
    elif _SCAN_ID_PATTERN.fullmatch(scan_id) is None:
        errors.append(
            "scan_id must contain only letters, numbers, underscores, or dashes "
            "and be at most 64 characters."
        )

    login_url = config.get("login_url")
    if not _is_http_url(login_url):
        errors.append("login_url must be a valid http/https URL.")

    start_url_after_login = config.get("start_url_after_login")
    if not _is_http_url(start_url_after_login):
        errors.append("start_url_after_login must be a valid http/https URL.")

    allowed_domains = config.get("allowed_domains")
    if not isinstance(allowed_domains, list) or not allowed_domains:
        errors.append("allowed_domains must be a non-empty list of strings.")
    else:
        invalid_domains = [
            domain for domain in allowed_domains if not isinstance(domain, str) or not domain.strip()
        ]
        if invalid_domains:
            errors.append("allowed_domains must contain only non-empty strings.")

    if _is_http_url(start_url_after_login) and isinstance(allowed_domains, list) and allowed_domains:
        start_host = _host_from_url(start_url_after_login)
        if not start_host or start_host not in set(allowed_domains):
            errors.append("start_url_after_login host must be included in allowed_domains.")

    max_depth = config.get("max_depth")
    if not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth < 0:
        errors.append("max_depth must be an integer >= 0.")

    max_pages = config.get("max_pages")
    if not isinstance(max_pages, int) or isinstance(max_pages, bool) or max_pages <= 0:
        errors.append("max_pages must be an integer > 0.")

    concurrency = config.get("concurrency")
    if (
        not isinstance(concurrency, int)
        or isinstance(concurrency, bool)
        or concurrency < 1
        or concurrency > 32
    ):
        errors.append("concurrency must be an integer between 1 and 32.")

    login_steps = config.get("login_steps")
    if not isinstance(login_steps, list) or not login_steps:
        errors.append("login_steps must be a non-empty list.")
    else:
        for index, step in enumerate(login_steps):
            if not isinstance(step, dict):
                errors.append(f"login_steps[{index}] must be an object.")
                continue
            selector = step.get("selector")
            if not isinstance(selector, str) or not selector.strip():
                errors.append(f"login_steps[{index}].selector must be a non-empty string.")
            if "value" not in step:
                errors.append(f"login_steps[{index}].value is required.")
            elif not isinstance(step.get("value"), str):
                errors.append(f"login_steps[{index}].value must be a string.")

    submit_selector = config.get("submit_selector")
    if not isinstance(submit_selector, str) or not submit_selector.strip():
        errors.append("submit_selector must be a non-empty string.")

    exclude_patterns = config.get("exclude_patterns")
    if not isinstance(exclude_patterns, list):
        errors.append("exclude_patterns must be a list of non-empty strings.")
    else:
        invalid_patterns = [
            pattern for pattern in exclude_patterns if not isinstance(pattern, str) or not pattern.strip()
        ]
        if invalid_patterns:
            errors.append("exclude_patterns must contain only non-empty strings.")

    if errors:
        error_lines = "\n".join(f"- {error}" for error in errors)
        raise ConfigError(f"Configuration validation failed:\n{error_lines}")
