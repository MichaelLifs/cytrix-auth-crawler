"""Login success validation helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page

FAILURE_TEXT_MARKERS = ("invalid password", "incorrect", "login failed")
AUTH_FAILURE_STATUSES = {401, 403}


@dataclass(slots=True)
class LoginResult:
    """Structured outcome for login/session validation."""

    success: bool
    indicators: list[str] = field(default_factory=list)
    final_url: str = ""
    message: str | None = None
    validated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to MongoDB-safe dictionary."""
        return {
            "success": self.success,
            "indicators": self.indicators,
            "final_url": self.final_url,
            "message": self.message,
            "validated_at": self.validated_at,
        }


def _contains_failure_text(page_content: str) -> bool:
    lowered = page_content.lower()
    return any(marker in lowered for marker in FAILURE_TEXT_MARKERS)


async def validate_login_success(
    *,
    page: "Page",
    context: "BrowserContext",
    config: dict[str, Any],
) -> LoginResult:
    """Validate whether browser context appears authenticated."""
    indicators: list[str] = []
    final_url = page.url
    login_url = config["login_url"]

    if final_url and not final_url.startswith(login_url):
        indicators.append("final_url_not_login")

    cookies = await context.cookies()
    if cookies:
        indicators.append("session_cookies_present")

    response = await page.goto(config["start_url_after_login"], wait_until="domcontentloaded", timeout=10000)
    if response is not None and response.status not in AUTH_FAILURE_STATUSES:
        indicators.append("authenticated_page_access_ok")

    content = await page.content()
    if not _contains_failure_text(content):
        indicators.append("no_login_failure_text")

    success = (
        "final_url_not_login" in indicators
        and "session_cookies_present" in indicators
        and "authenticated_page_access_ok" in indicators
        and "no_login_failure_text" in indicators
    )

    message = None if success else "Authentication validation checks did not pass."
    return LoginResult(
        success=success,
        indicators=indicators,
        final_url=page.url,
        message=message,
    )

