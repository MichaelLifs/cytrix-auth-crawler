from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

from cytrix_crawler.auth.login import validate_existing_session
from cytrix_crawler.config import CONFIG


def test_validate_existing_session_returns_invalid_when_file_missing(tmp_path: Path) -> None:
    config = deepcopy(CONFIG)
    missing_path = str(tmp_path / "missing-state.json")

    result = asyncio.run(validate_existing_session(None, config, missing_path))

    assert result.success is False
    assert result.message == "Session storage_state file does not exist."

