"""Playwright-backed regression: form extraction runs in real Chromium."""

from __future__ import annotations

import asyncio

import pytest
from playwright.async_api import async_playwright

from cytrix_crawler.extract.forms import extract_forms

# Mirrors demo_app /profile fixture (GET): form + hidden _token + nickname + buttons.
_PROFILE_HTML = """
<!doctype html>
<html>
  <head><title>Demo profile</title></head>
  <body>
    <h1>Profile</h1>
    <form method="post" action="http://localhost:8000/profile">
      <input type="hidden" name="_token" value="demo-csrf-token" />
      <label for="nickname">Nickname</label>
      <input id="nickname" name="nickname" type="text" />
      <button type="submit">Save profile</button>
      <button type="button">Preview</button>
    </form>
  </body>
</html>
"""


def _run(coro):
    return asyncio.run(coro)


def test_extract_forms_playwright_profile_like_markup() -> None:
    async def scenario() -> None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(_PROFILE_HTML.strip(), wait_until="domcontentloaded")
                forms, err = await extract_forms(page, "http://localhost:8000/profile")
                assert err is None, err
                assert len(forms) == 1
                f0 = forms[0]
                assert f0["method"] == "POST"
                assert f0["csrf_detected"] is True
                names = {fld["name"] for fld in f0["fields"]}
                assert "_token" in names and "nickname" in names
                assert "Save profile" in f0["buttons"] and "Preview" in f0["buttons"]
                assert f0["action"].rstrip("/") == "http://localhost:8000/profile"
            finally:
                await browser.close()

    _run(scenario())
