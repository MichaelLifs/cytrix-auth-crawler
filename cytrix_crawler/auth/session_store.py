"""Session storage path and file helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cytrix_crawler.config import SESSION_STATE_DIR


def get_session_state_path(scan_id: str) -> str:
    """Return deterministic storage_state path for a scan id."""
    return str(Path(SESSION_STATE_DIR) / f"{scan_id}.json")


def ensure_session_dir(path_or_config: str | Path | dict[str, Any]) -> Path:
    """Ensure the parent directory for a session state file exists."""
    if isinstance(path_or_config, dict):
        if "scan_id" not in path_or_config:
            raise KeyError("scan_id")
        path = Path(get_session_state_path(path_or_config["scan_id"]))
    else:
        path = Path(path_or_config)

    path.parent.mkdir(parents=True, exist_ok=True)
    return path.parent


def session_state_exists(path: str | Path) -> bool:
    """Return True when the storage_state file exists."""
    return Path(path).is_file()

