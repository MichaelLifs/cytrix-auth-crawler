"""Unit tests for network request/response classification heuristics."""

from __future__ import annotations

from cytrix_crawler.network.classify import classify_request_response


def _classify(**overrides):
    base = {
        "method": "GET",
        "url": "http://localhost:8000/dashboard",
        "request_headers": {},
        "response_headers": {},
        "response_content_type": None,
        "response_body_preview": None,
    }
    base.update(overrides)
    return classify_request_response(**base)


def test_api_by_method_post() -> None:
    result = _classify(method="POST")
    assert result["is_api"] is True


def test_api_by_method_delete() -> None:
    assert _classify(method="DELETE")["is_api"] is True


def test_api_by_response_content_type_json() -> None:
    result = _classify(response_content_type="application/json; charset=utf-8")
    assert result["is_api"] is True


def test_api_by_request_content_type_xml() -> None:
    result = _classify(request_headers={"Content-Type": "application/xml"})
    assert result["is_api"] is True


def test_api_by_url_marker_api() -> None:
    result = _classify(url="http://localhost:8000/api/profile")
    assert result["is_api"] is True


def test_api_by_url_marker_graphql() -> None:
    assert _classify(url="http://localhost:8000/graphql")["is_api"] is True


def test_api_by_accept_header() -> None:
    result = _classify(request_headers={"Accept": "application/json, text/plain, */*"})
    assert result["is_api"] is True


def test_api_by_json_body_preview() -> None:
    result = _classify(response_body_preview='{"ok": true}')
    assert result["is_api"] is True
    assert _classify(response_body_preview="[1,2,3]")["is_api"] is True


def test_static_by_extension_css() -> None:
    result = _classify(url="http://localhost:8000/static/style.css")
    assert result["is_static"] is True


def test_static_by_extension_image() -> None:
    assert _classify(url="http://localhost:8000/static/logo.png")["is_static"] is True
    assert _classify(url="http://localhost:8000/icon.ico")["is_static"] is True


def test_static_by_response_content_type_image() -> None:
    result = _classify(response_content_type="image/png")
    assert result["is_static"] is True


def test_static_by_response_content_type_font() -> None:
    assert _classify(response_content_type="font/woff2")["is_static"] is True


def test_static_by_response_content_type_text_css() -> None:
    assert _classify(response_content_type="text/css")["is_static"] is True


def test_static_js_bundle_when_content_type_javascript() -> None:
    result = _classify(
        url="http://localhost:8000/static/app.js",
        response_content_type="application/javascript",
    )
    assert result["is_static"] is True


def test_html_navigation_is_neither_api_nor_static() -> None:
    result = _classify(
        method="GET",
        url="http://localhost:8000/dashboard",
        response_content_type="text/html; charset=utf-8",
    )
    assert result["is_api"] is False
    assert result["is_static"] is False


def test_no_response_data_classification_still_works() -> None:
    result = _classify(
        method="POST",
        url="http://localhost:8000/something",
        response_headers=None,
        response_content_type=None,
        response_body_preview=None,
    )
    assert result["is_api"] is True
