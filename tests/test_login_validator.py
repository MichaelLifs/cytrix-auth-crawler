from __future__ import annotations

from datetime import datetime, timezone

from cytrix_crawler.auth.validator import LoginResult


def test_login_result_to_dict_has_expected_shape() -> None:
    validated_at = datetime.now(timezone.utc)
    result = LoginResult(
        success=True,
        indicators=["final_url_not_login", "session_cookies_present"],
        final_url="http://localhost:8000/dashboard",
        message=None,
        validated_at=validated_at,
    )

    payload = result.to_dict()

    assert payload == {
        "success": True,
        "indicators": ["final_url_not_login", "session_cookies_present"],
        "final_url": "http://localhost:8000/dashboard",
        "message": None,
        "validated_at": validated_at,
    }

