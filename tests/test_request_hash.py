"""Unit tests for browser request hashing."""

from __future__ import annotations

from cytrix_crawler.dedupe.hashing import build_request_hash


def _base_kwargs() -> dict:
    return {
        "method": "GET",
        "url": "http://localhost:8000/api/profile",
        "headers": {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (CYTRIX)",
        },
        "post_data": None,
    }


def test_request_hash_starts_with_sha256_and_64_hex() -> None:
    h = build_request_hash(**_base_kwargs())
    assert h.startswith("sha256:")
    rest = h.split(":", 1)[1]
    assert len(rest) == 64
    int(rest, 16)


def test_request_hash_stable_for_same_inputs() -> None:
    a = build_request_hash(**_base_kwargs())
    b = build_request_hash(**_base_kwargs())
    assert a == b


def test_request_hash_changes_when_method_changes() -> None:
    base = build_request_hash(**_base_kwargs())
    other_kwargs = _base_kwargs()
    other_kwargs["method"] = "POST"
    assert build_request_hash(**other_kwargs) != base


def test_request_hash_changes_when_url_changes() -> None:
    base = build_request_hash(**_base_kwargs())
    other_kwargs = _base_kwargs()
    other_kwargs["url"] = "http://localhost:8000/api/settings"
    assert build_request_hash(**other_kwargs) != base


def test_request_hash_changes_when_post_data_changes() -> None:
    base_kwargs = _base_kwargs()
    base_kwargs["method"] = "POST"
    base_kwargs["post_data"] = '{"a":1}'
    base = build_request_hash(**base_kwargs)

    other_kwargs = dict(base_kwargs)
    other_kwargs["post_data"] = '{"a":2}'
    assert build_request_hash(**other_kwargs) != base


def test_request_hash_ignores_volatile_headers() -> None:
    base = build_request_hash(**_base_kwargs())

    volatile = dict(_base_kwargs())
    volatile["headers"] = {
        **_base_kwargs()["headers"],
        "User-Agent": "Different/2.0",
        "Cookie": "session=abc; tracker=xyz",
        "Date": "Mon, 01 Jan 2024 00:00:00 GMT",
        "X-Request-Id": "req-1234",
        "Sec-Ch-Ua": '"Chromium";v="120"',
        "Sec-Fetch-Mode": "cors",
    }
    assert build_request_hash(**volatile) == base


def test_request_hash_authorization_presence_changes_hash_but_value_does_not() -> None:
    no_auth = build_request_hash(**_base_kwargs())

    auth_a = dict(_base_kwargs())
    auth_a["headers"] = {**_base_kwargs()["headers"], "Authorization": "Bearer aaa"}
    auth_b = dict(_base_kwargs())
    auth_b["headers"] = {**_base_kwargs()["headers"], "Authorization": "Bearer bbb"}

    h_a = build_request_hash(**auth_a)
    h_b = build_request_hash(**auth_b)
    assert h_a == h_b
    assert h_a != no_auth


def test_request_hash_header_case_insensitive() -> None:
    upper = build_request_hash(**_base_kwargs())
    lower_kwargs = _base_kwargs()
    lower_kwargs["headers"] = {
        "content-type": "application/json",
        "accept": "application/json",
        "user-agent": "Mozilla/5.0 (CYTRIX)",
    }
    assert build_request_hash(**lower_kwargs) == upper


def test_request_hash_truncates_oversized_post_data_safely() -> None:
    big = "x" * (200 * 1024)
    kwargs = _base_kwargs()
    kwargs["method"] = "POST"
    kwargs["post_data"] = big
    out = build_request_hash(**kwargs)
    assert out.startswith("sha256:")
