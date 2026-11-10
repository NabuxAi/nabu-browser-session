from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from nabu_browser_session import Settings


@pytest.fixture
def fernet_key() -> str:
    return Fernet.generate_key().decode("ascii")


@pytest.fixture
def settings(tmp_path: Path, fernet_key: str, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("NABU_SESSION_FERNET_KEY", fernet_key)
    return Settings.from_env(
        {
            "SESSION_DIR": str(tmp_path / "sessions"),
            "NABU_SESSION_FERNET_KEY": fernet_key,
            "BROWSER_ENGINE": "chromium",
            "NABU_BROWSER_HEADLESS": "1",
            "NABU_RATE_ENABLED": "1",
            "NABU_RATE_CAPACITY": "10",
            "NABU_RATE_REFILL_PER_MINUTE": "600",
            "NABU_RATE_MIN_INTERVAL_SECONDS": "0",
            "NABU_RATE_JITTER_SECONDS": "0",
            "NABU_LOGIN_TIMEOUT_SECONDS": "300",
            "NABU_LOGIN_MAX_CAPTURES": "4",
            "NABU_DOWNLOAD_MAX_BYTES": "1048576",
        }
    )
