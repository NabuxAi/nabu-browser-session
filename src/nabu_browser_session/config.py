"""Environment-driven settings.

Gateways already keep a ``/data`` volume (``DATA_DIR``). When ``SESSION_DIR``
is unset, the vault lives at ``$DATA_DIR/browser-sessions`` so the same volume
holds encrypted browser sessions.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from nabu_browser_session.errors import ConfigurationError

FERNET_KEY_ENV = "NABU_SESSION_FERNET_KEY"
FERNET_KEY_ALIAS = "FERNET_KEY"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _text(env: Mapping[str, str], name: str) -> str:
    return str(env.get(name, "") or "").strip()


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = _text(env, name).lower()
    if not raw:
        return default
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = _text(env, name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = _text(env, name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


def _session_dir(env: Mapping[str, str]) -> Path:
    explicit = _text(env, "SESSION_DIR")
    if explicit:
        return Path(explicit)
    data_dir = _text(env, "DATA_DIR")
    if data_dir:
        return Path(data_dir) / "browser-sessions"
    return Path("./.nabu-sessions")


@dataclass(frozen=True)
class Settings:
    """Process settings. Construct with :meth:`from_env` in a gateway."""

    browser_engine: str
    lightpanda_cdp_url: str | None
    headless: bool
    session_dir: Path
    session_db_path: Path
    fernet_key_env: str
    rate_enabled: bool
    rate_capacity: float
    rate_refill_per_minute: float
    rate_min_interval_seconds: float
    rate_jitter_seconds: float
    login_timeout_seconds: float
    login_max_captures: int
    download_max_bytes: int

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if environ is None else environ
        engine = _text(env, "BROWSER_ENGINE").lower() or "chromium"
        if engine not in {"chromium", "lightpanda"}:
            raise ConfigurationError("BROWSER_ENGINE must be 'chromium' or 'lightpanda'")
        cdp = _text(env, "LIGHTPANDA_CDP_URL") or None
        session_dir = _session_dir(env)
        db_raw = _text(env, "SESSION_DB_PATH")
        db_path = Path(db_raw) if db_raw else session_dir / "sessions.db"

        capacity = _float(env, "NABU_RATE_CAPACITY", 3.0)
        refill = _float(env, "NABU_RATE_REFILL_PER_MINUTE", 3.0)
        interval = _float(env, "NABU_RATE_MIN_INTERVAL_SECONDS", 12.0)
        jitter = _float(env, "NABU_RATE_JITTER_SECONDS", 4.0)
        login_timeout = _float(env, "NABU_LOGIN_TIMEOUT_SECONDS", 300.0)
        login_max = _int(env, "NABU_LOGIN_MAX_CAPTURES", 4)
        max_bytes = _int(env, "NABU_DOWNLOAD_MAX_BYTES", 100 * 1024 * 1024)

        if capacity < 1:
            raise ConfigurationError("NABU_RATE_CAPACITY must be at least 1")
        if refill <= 0:
            raise ConfigurationError("NABU_RATE_REFILL_PER_MINUTE must be greater than 0")
        if interval < 0:
            raise ConfigurationError("NABU_RATE_MIN_INTERVAL_SECONDS must be >= 0")
        if jitter < 0:
            raise ConfigurationError("NABU_RATE_JITTER_SECONDS must be >= 0")
        if login_timeout <= 0:
            raise ConfigurationError("NABU_LOGIN_TIMEOUT_SECONDS must be greater than 0")
        if login_max < 1:
            raise ConfigurationError("NABU_LOGIN_MAX_CAPTURES must be at least 1")
        if max_bytes < 1:
            raise ConfigurationError("NABU_DOWNLOAD_MAX_BYTES must be at least 1")

        return cls(
            browser_engine=engine,
            lightpanda_cdp_url=cdp,
            headless=_bool(env, "NABU_BROWSER_HEADLESS", True),
            session_dir=session_dir,
            session_db_path=db_path,
            fernet_key_env=FERNET_KEY_ENV,
            rate_enabled=_bool(env, "NABU_RATE_ENABLED", True),
            rate_capacity=capacity,
            rate_refill_per_minute=refill,
            rate_min_interval_seconds=interval,
            rate_jitter_seconds=jitter,
            login_timeout_seconds=login_timeout,
            login_max_captures=login_max,
            download_max_bytes=max_bytes,
        )
