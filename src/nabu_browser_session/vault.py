"""Fernet-encrypted session vault.

The SQLite file stores ciphertext plus non-secret bookkeeping (counts, the
``needs_relogin`` flag, status). Cookie and storage values exist only inside
the ciphertext and inside :class:`SealedState`, whose string form is masked.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from cryptography.fernet import Fernet, InvalidToken

from nabu_browser_session.config import FERNET_KEY_ALIAS, FERNET_KEY_ENV, Settings
from nabu_browser_session.db import open_database
from nabu_browser_session.errors import ConfigurationError, NeedsReloginError
from nabu_browser_session.masking import mask_session

logger = logging.getLogger("nabu_browser_session.vault")

STATUS_ACTIVE = "active"
STATUS_NEEDS_RELOGIN = "needs_relogin"
STATUS_LOGGED_OUT = "logged_out"
STATUS_ABSENT = "absent"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KeySource(Protocol):
    """Pluggable Fernet key. Gateways can wrap their own secret store."""

    def fernet(self) -> Fernet: ...


class EnvKeySource:
    """Read ``NABU_SESSION_FERNET_KEY``, or ``FERNET_KEY`` when the primary is unset."""

    def __init__(
        self,
        key: str | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        env_var: str = FERNET_KEY_ENV,
    ) -> None:
        self._key = key
        self._environ = environ
        self._env_var = env_var

    def fernet(self) -> Fernet:
        raw = self._raw()
        try:
            return Fernet(raw.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise ConfigurationError(
                f"{self._env_var} is not a valid Fernet key"
            ) from exc

    def _raw(self) -> str:
        if self._key:
            return self._key.strip()
        env = self._environ
        if env is None:
            import os

            env = os.environ
        raw = str(env.get(self._env_var, "") or "").strip()
        if not raw:
            raw = str(env.get(FERNET_KEY_ALIAS, "") or "").strip()
        if not raw:
            raise ConfigurationError(
                f"Set {self._env_var} to a Fernet key. Generate one with: "
                'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
        return raw


class StaticKeySource:
    """Explicit key, used by tests and by a gateway that already holds the secret."""

    def __init__(self, key: str | bytes) -> None:
        self._key = key.decode("ascii") if isinstance(key, bytes) else key.strip()

    def fernet(self) -> Fernet:
        try:
            return Fernet(self._key.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise ConfigurationError("Fernet key is not valid") from exc


class SealedState:
    """Storage state that does not reveal itself when logged or printed.

    Call :meth:`reveal` at the moment the bytes are handed to the browser.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise ConfigurationError("session state must be a dict")
        self._payload = payload

    def reveal(self) -> dict[str, Any]:
        return self._payload

    def __repr__(self) -> str:
        return mask_session(self._payload)

    def __str__(self) -> str:
        return mask_session(self._payload)


@dataclass(frozen=True)
class SessionKey:
    owner_id: str
    platform: str
    account: str

    @classmethod
    def parse(cls, owner_id: str, platform: str, account: str) -> SessionKey:
        if owner_id is None or platform is None or account is None:
            raise ConfigurationError("owner_id, platform, and account are required")
        owner = str(owner_id).strip()
        plat = str(platform).strip().lower()
        acct = str(account).strip()
        if not owner or len(owner) > 128 or any(ch.isspace() for ch in owner):
            raise ConfigurationError("owner_id must be a non-empty token up to 128 characters")
        if not plat.isidentifier() or len(plat) > 32:
            raise ConfigurationError("platform must be a short identifier such as 'instagram'")
        if not acct or len(acct) > 128 or any(ord(ch) < 32 for ch in acct):
            raise ConfigurationError("account must be a non-empty label up to 128 characters")
        return cls(owner_id=owner, platform=plat, account=acct)

    @property
    def bucket(self) -> str:
        return f"{self.owner_id}\n{self.platform}\n{self.account}"


@dataclass
class SessionRecord:
    """Non-secret view plus a sealed payload.

    ``payload`` is ``None`` when the row has no ciphertext (logged out, or absent).
    """

    key: SessionKey
    status: str
    needs_relogin: bool
    cookie_count: int
    origin_count: int
    created_at: str | None
    updated_at: str | None
    last_checked_at: str | None
    last_error: str | None
    payload: SealedState | None = None

    def __repr__(self) -> str:
        return (
            f"SessionRecord(platform={self.key.platform!r} status={self.status} "
            f"needs_relogin={self.needs_relogin} {self.payload or '<session empty>'})"
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "unofficial": True,
            "owner_id": self.key.owner_id,
            "platform": self.key.platform,
            "account": self.key.account,
            "status": self.status,
            "needs_relogin": self.needs_relogin,
            "cookie_count": self.cookie_count,
            "origin_count": self.origin_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_checked_at": self.last_checked_at,
            "last_error": self.last_error,
        }


class SessionVault:
    """Encrypted store keyed by ``(owner_id, platform, account)``."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        keys: KeySource | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings
        self._path = Path(path or (settings.session_db_path if settings else Settings.from_env().session_db_path))
        self._keys = keys or EnvKeySource()
        self._lock = threading.RLock()
        self._conn = open_database(self._path)
        self._ensure()

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        keys: KeySource | None = None,
    ) -> SessionVault:
        settings = Settings.from_env(environ)
        return cls(settings.session_db_path, keys=keys or EnvKeySource(environ=environ), settings=settings)

    @property
    def path(self) -> Path:
        return self._path

    def _ensure(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                owner_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                account TEXT NOT NULL,
                ciphertext TEXT,
                cookie_count INTEGER NOT NULL DEFAULT 0,
                origin_count INTEGER NOT NULL DEFAULT 0,
                needs_relogin INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_checked_at TEXT,
                last_error TEXT,
                PRIMARY KEY (owner_id, platform, account)
            );
            CREATE TABLE IF NOT EXISTS session_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                account TEXT NOT NULL,
                event TEXT NOT NULL,
                reason TEXT,
                needs_relogin INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self._conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('version', '1') "
            "ON CONFLICT(key) DO NOTHING"
        )

    def save(self, key: SessionKey, state: Mapping[str, Any] | SealedState) -> SessionRecord:
        payload = state.reveal() if isinstance(state, SealedState) else dict(state)
        if not isinstance(payload, dict):
            raise ConfigurationError("session state must be a dict")
        cookies = payload.get("cookies") if isinstance(payload.get("cookies"), list) else []
        origins = payload.get("origins") if isinstance(payload.get("origins"), list) else []
        token = self._encrypt(payload)
        stamp = _now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sessions (
                    owner_id, platform, account, ciphertext, cookie_count, origin_count,
                    needs_relogin, status, created_at, updated_at, last_checked_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, NULL)
                ON CONFLICT(owner_id, platform, account) DO UPDATE SET
                    ciphertext = excluded.ciphertext,
                    cookie_count = excluded.cookie_count,
                    origin_count = excluded.origin_count,
                    needs_relogin = 0,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    last_checked_at = excluded.last_checked_at,
                    last_error = NULL
                """,
                (
                    key.owner_id,
                    key.platform,
                    key.account,
                    token,
                    len(cookies),
                    len(origins),
                    STATUS_ACTIVE,
                    stamp,
                    stamp,
                    stamp,
                ),
            )
        logger.info(
            "saved unofficial session platform=%s %s",
            key.platform,
            mask_session(payload),
        )
        record = self.get_meta(key)
        assert record is not None
        record.payload = SealedState(payload)
        return record

    def get_meta(self, key: SessionKey) -> SessionRecord | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT owner_id, platform, account, cookie_count, origin_count,
                       needs_relogin, status, created_at, updated_at,
                       last_checked_at, last_error
                FROM sessions
                WHERE owner_id = ? AND platform = ? AND account = ?
                """,
                (key.owner_id, key.platform, key.account),
            ).fetchone()
        if row is None:
            return None
        return _record_from_row(row, payload=None)

    def get(self, key: SessionKey) -> SessionRecord | None:
        """Return the record with a sealed payload. ``None`` when no row exists."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT owner_id, platform, account, ciphertext, cookie_count, origin_count,
                       needs_relogin, status, created_at, updated_at,
                       last_checked_at, last_error
                FROM sessions
                WHERE owner_id = ? AND platform = ? AND account = ?
                """,
                (key.owner_id, key.platform, key.account),
            ).fetchone()
        if row is None:
            return None
        payload = None
        if row["ciphertext"]:
            payload = SealedState(self._decrypt(key, row["ciphertext"]))
        return _record_from_row(row, payload=payload)

    def require(self, key: SessionKey) -> SessionRecord:
        """Return a usable sealed session or raise :class:`NeedsReloginError`."""
        record = self.get(key)
        if record is None or record.payload is None or record.status == STATUS_LOGGED_OUT:
            self.mark(key, needs_relogin=True, status=STATUS_NEEDS_RELOGIN, reason="no saved session")
            raise NeedsReloginError(
                owner_id=key.owner_id,
                platform=key.platform,
                account=key.account,
                reason="no saved session",
            )
        if record.needs_relogin or record.status == STATUS_NEEDS_RELOGIN:
            raise NeedsReloginError(
                owner_id=key.owner_id,
                platform=key.platform,
                account=key.account,
                reason=record.last_error or "session flagged needs_relogin",
            )
        return record

    def mark(
        self,
        key: SessionKey,
        *,
        needs_relogin: bool,
        status: str,
        reason: str | None,
        clear_secret: bool = False,
    ) -> SessionRecord:
        stamp = _now()
        with self._lock:
            existing = self._conn.execute(
                "SELECT created_at FROM sessions WHERE owner_id = ? AND platform = ? AND account = ?",
                (key.owner_id, key.platform, key.account),
            ).fetchone()
            created = existing["created_at"] if existing else stamp
            if clear_secret:
                self._conn.execute(
                    """
                    INSERT INTO sessions (
                        owner_id, platform, account, ciphertext, cookie_count, origin_count,
                        needs_relogin, status, created_at, updated_at, last_checked_at, last_error
                    ) VALUES (?, ?, ?, NULL, 0, 0, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(owner_id, platform, account) DO UPDATE SET
                        ciphertext = NULL,
                        cookie_count = 0,
                        origin_count = 0,
                        needs_relogin = excluded.needs_relogin,
                        status = excluded.status,
                        updated_at = excluded.updated_at,
                        last_checked_at = excluded.last_checked_at,
                        last_error = excluded.last_error
                    """,
                    (
                        key.owner_id,
                        key.platform,
                        key.account,
                        1 if needs_relogin else 0,
                        status,
                        created,
                        stamp,
                        stamp,
                        reason,
                    ),
                )
            else:
                self._conn.execute(
                    """
                    INSERT INTO sessions (
                        owner_id, platform, account, ciphertext, cookie_count, origin_count,
                        needs_relogin, status, created_at, updated_at, last_checked_at, last_error
                    ) VALUES (?, ?, ?, NULL, 0, 0, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(owner_id, platform, account) DO UPDATE SET
                        needs_relogin = excluded.needs_relogin,
                        status = excluded.status,
                        updated_at = excluded.updated_at,
                        last_checked_at = excluded.last_checked_at,
                        last_error = excluded.last_error
                    """,
                    (
                        key.owner_id,
                        key.platform,
                        key.account,
                        1 if needs_relogin else 0,
                        status,
                        created,
                        stamp,
                        stamp,
                        reason,
                    ),
                )
        logger.info(
            "session status platform=%s status=%s needs_relogin=%s reason=%s",
            key.platform,
            status,
            needs_relogin,
            reason or "",
        )
        record = self.get_meta(key)
        assert record is not None
        return record

    def add_event(
        self,
        key: SessionKey,
        *,
        event: str,
        reason: str,
        needs_relogin: bool,
        status: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO session_events (
                    owner_id, platform, account, event, reason, needs_relogin, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key.owner_id,
                    key.platform,
                    key.account,
                    event,
                    reason,
                    1 if needs_relogin else 0,
                    status,
                    _now(),
                ),
            )

    def recent_events(self, key: SessionKey, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT event, reason, needs_relogin, status, created_at
                FROM session_events
                WHERE owner_id = ? AND platform = ? AND account = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (key.owner_id, key.platform, key.account, max(1, min(int(limit), 100))),
            ).fetchall()
        return [
            {
                "event": row["event"],
                "reason": row["reason"],
                "needs_relogin": bool(row["needs_relogin"]),
                "status": row["status"],
                "created_at": row["created_at"],
                "unofficial": True,
            }
            for row in rows
        ]

    def _encrypt(self, payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return self._keys.fernet().encrypt(raw).decode("ascii")

    def _decrypt(self, key: SessionKey, token: str) -> dict[str, Any]:
        try:
            raw = self._keys.fernet().decrypt(token.encode("ascii"))
        except InvalidToken as exc:
            self.mark(
                key,
                needs_relogin=True,
                status=STATUS_NEEDS_RELOGIN,
                reason="stored session cannot be decrypted",
                clear_secret=True,
            )
            raise ConfigurationError(
                "Stored session cannot be decrypted (Fernet key changed?)"
            ) from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ConfigurationError("Stored session is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ConfigurationError("Stored session is not an object")
        return payload

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def generate_fernet_key() -> str:
    """Return a new Fernet key for ``NABU_SESSION_FERNET_KEY``."""
    return Fernet.generate_key().decode("ascii")


def _record_from_row(row: Any, *, payload: SealedState | None) -> SessionRecord:
    key = SessionKey(owner_id=row["owner_id"], platform=row["platform"], account=row["account"])
    return SessionRecord(
        key=key,
        status=row["status"],
        needs_relogin=bool(row["needs_relogin"]),
        cookie_count=int(row["cookie_count"]),
        origin_count=int(row["origin_count"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_checked_at=row["last_checked_at"],
        last_error=row["last_error"],
        payload=payload,
    )
