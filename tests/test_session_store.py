from __future__ import annotations

from pathlib import Path

from cytrix_crawler.auth.session_store import get_session_state_path, session_state_exists


def test_get_session_state_path_is_deterministic_by_scan_id() -> None:
    assert get_session_state_path("scan_demo") == str(Path("sessions") / "scan_demo.json")


def test_session_state_exists_returns_false_for_missing_file(tmp_path: Path) -> None:
    missing_path = tmp_path / "does-not-exist.json"
    assert session_state_exists(missing_path) is False

