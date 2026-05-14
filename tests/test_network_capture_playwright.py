"""Playwright-backed regression: PageNetworkCapture collects + classifies traffic.

This test stands up a tiny in-process HTTP server (no Mongo, no Flask) so it
stays deterministic in CI and does not depend on the demo app or MongoDB.
"""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.async_api import async_playwright

from cytrix_crawler.network.capture import PageNetworkCapture


class _FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A003 - stdlib hook
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook
        if self.path == "/":
            body = (
                b"<!doctype html><html><head>"
                b"<link rel='stylesheet' href='/static/style.css' />"
                b"</head><body><h1>fixture</h1>"
                b"<script src='/static/app.js'></script>"
                b"<script>fetch('/api/data', {headers:{'Accept':'application/json'}});</script>"
                b"</body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/static/style.css":
            body = b"body{color:#222}"
            self.send_response(200)
            self.send_header("Content-Type", "text/css; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/static/app.js":
            body = b"window.__demo = 1;"
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/data":
            body = b'{"ok": true, "data": [1,2,3]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


@pytest.fixture
def fixture_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()


class _FakeCollection:
    def __init__(self) -> None:
        self.ops: list = []

    async def bulk_write(self, operations, ordered=False):  # noqa: ARG002
        self.ops.extend(operations)

    async def update_one(self, *args, **kwargs):
        self.ops.append(("update_one", args, kwargs))


class _FakeDB:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        if name not in self.collections:
            self.collections[name] = _FakeCollection()
        return self.collections[name]


def test_capture_collects_static_and_api_traffic(fixture_server) -> None:
    page_url = f"{fixture_server}/"

    async def scenario() -> dict:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context()
                page = await context.new_page()
                capture = PageNetworkCapture(page_url=page_url)
                capture.attach(page)
                await page.goto(page_url, wait_until="domcontentloaded")
                await page.wait_for_load_state("load")
                await asyncio.sleep(0.5)
                db = _FakeDB()
                summary = await capture.flush(db, scan_id="test_capture")
                return {"summary": summary, "ops_len": len(db["browser_requests"].ops)}
            finally:
                await browser.close()

    out = asyncio.run(scenario())
    summary = out["summary"]
    assert summary["captured_requests"] >= 3
    assert summary["api_requests"] >= 1
    assert summary["static_requests"] >= 1
    assert out["ops_len"] >= 3
