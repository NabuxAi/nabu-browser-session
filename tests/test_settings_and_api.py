from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from nabu_browser_session import (
    FERNET_KEY_ENV,
    ConfigurationError,
    Settings,
    __version__,
)
from nabu_browser_session.__main__ import main


def test_version_matches_pyproject() -> None:
    pyproject = tomllib.loads(Path(__file__).parents[1].joinpath("pyproject.toml").read_text())
    assert pyproject["project"]["version"] == "0.1.0"
    assert __version__ == "0.1.0"


def test_session_dir_uses_the_gateway_data_volume(tmp_path: Path) -> None:
    settings = Settings.from_env({"DATA_DIR": str(tmp_path / "data")})
    assert settings.session_dir == tmp_path / "data" / "browser-sessions"
    assert settings.session_db_path == settings.session_dir / "sessions.db"
    explicit = Settings.from_env(
        {"DATA_DIR": str(tmp_path / "data"), "SESSION_DIR": str(tmp_path / "custom")}
    )
    assert explicit.session_dir == tmp_path / "custom"
    db = Settings.from_env(
        {"SESSION_DIR": str(tmp_path / "custom"), "SESSION_DB_PATH": str(tmp_path / "custom" / "mine.db")}
    )
    assert db.session_db_path == tmp_path / "custom" / "mine.db"


def test_invalid_rates(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        Settings.from_env({"SESSION_DIR": str(tmp_path), "NABU_RATE_REFILL_PER_MINUTE": "0"})
    with pytest.raises(ConfigurationError):
        Settings.from_env({"SESSION_DIR": str(tmp_path), "NABU_BROWSER_HEADLESS": "maybe"})


def test_main_does_not_print_secrets(tmp_path: Path, monkeypatch, capsys) -> None:
    key = "QUITE_SECRET_FERNET_KEY_VALUE"
    monkeypatch.setenv(FERNET_KEY_ENV, key)
    monkeypatch.setenv("SESSION_DIR", str(tmp_path))
    monkeypatch.setenv("BROWSER_ENGINE", "lightpanda")
    monkeypatch.setenv("LIGHTPANDA_CDP_URL", "ws://secret-host.example/devtools")
    main()
    out = capsys.readouterr().out
    assert key not in out
    assert "secret-host" not in out
    assert "fernet_key_set: True" in out
    assert "lightpanda_cdp_url_set: True" in out
    assert "capability login: engine=chromium fell_back=true" in out
    assert "capability read: engine=lightpanda fell_back=false" in out
    assert "UNOFFICIAL" in out
