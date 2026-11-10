from __future__ import annotations

import logging
from pathlib import Path

from cryptography.fernet import Fernet

from nabu_browser_session import (
    ConfigurationError,
    EnvKeySource,
    NeedsReloginError,
    SealedState,
    SessionKey,
    SessionVault,
    StaticKeySource,
    generate_fernet_key,
    mask_session,
)

SECRET = "COOKIEVALUE_sentinel_9f3a"


def _state() -> dict:
    return {
        "cookies": [
            {
                "name": "sessionid",
                "value": SECRET,
                "domain": ".example.test",
                "path": "/",
            }
        ],
        "origins": [
            {
                "origin": "https://example.test",
                "localStorage": [{"name": "token", "value": SECRET}],
            }
        ],
    }


def test_roundtrip_and_plaintext_absent_from_database(tmp_path: Path, fernet_key: str) -> None:
    vault = SessionVault(tmp_path / "sessions.db", keys=StaticKeySource(fernet_key))
    key = SessionKey.parse("owner-1", "instagram", "igp_account")
    saved = vault.save(key, _state())
    assert saved.needs_relogin is False
    assert saved.status == "active"
    assert saved.cookie_count == 1
    assert saved.origin_count == 1
    assert SECRET not in repr(saved)
    assert SECRET not in str(saved.payload)

    loaded = vault.get(key)
    assert loaded is not None
    assert loaded.payload is not None
    assert loaded.payload.reveal()["cookies"][0]["value"] == SECRET
    assert SECRET not in mask_session(loaded.payload.reveal())

    raw = (tmp_path / "sessions.db").read_bytes()
    assert SECRET.encode() not in raw
    assert b"sessionid" not in raw


def test_accounts_are_isolated(tmp_path: Path, fernet_key: str) -> None:
    vault = SessionVault(tmp_path / "sessions.db", keys=StaticKeySource(fernet_key))
    first = SessionKey.parse("owner-1", "instagram", "one")
    second = SessionKey.parse("owner-1", "instagram", "two")
    other_owner = SessionKey.parse("owner-2", "instagram", "one")
    vault.save(first, _state())
    assert vault.get(second) is None
    assert vault.get(other_owner) is None


def test_wrong_key_cannot_decrypt_and_flags_relogin(tmp_path: Path) -> None:
    path = tmp_path / "sessions.db"
    key = SessionKey.parse("owner-1", "x", "me")
    SessionVault(path, keys=StaticKeySource(Fernet.generate_key().decode())).save(key, _state())
    other = SessionVault(path, keys=StaticKeySource(Fernet.generate_key().decode()))
    try:
        other.get(key)
        raise AssertionError("expected ConfigurationError")
    except ConfigurationError as exc:
        assert SECRET not in str(exc)
    flagged = other.get_meta(key)
    assert flagged is not None
    assert flagged.needs_relogin is True
    assert flagged.cookie_count == 0
    assert SECRET.encode() not in path.read_bytes()


def test_require_missing_session_raises(tmp_path: Path, fernet_key: str) -> None:
    vault = SessionVault(tmp_path / "sessions.db", keys=StaticKeySource(fernet_key))
    key = SessionKey.parse("owner-1", "threads", "me")
    try:
        vault.require(key)
        raise AssertionError("expected NeedsReloginError")
    except NeedsReloginError as exc:
        assert exc.needs_relogin is True
        assert exc.platform == "threads"
    meta = vault.get_meta(key)
    assert meta is not None
    assert meta.needs_relogin is True
    assert meta.status == "needs_relogin"


def test_env_key_source_accepts_primary_and_alias(fernet_key: str) -> None:
    primary = EnvKeySource(environ={"NABU_SESSION_FERNET_KEY": fernet_key})
    assert primary.fernet().decrypt(primary.fernet().encrypt(b"ok")) == b"ok"
    alias = EnvKeySource(environ={"FERNET_KEY": fernet_key})
    token = alias.fernet().encrypt(b"ok")
    assert alias.fernet().decrypt(token) == b"ok"
    both = EnvKeySource(
        environ={"NABU_SESSION_FERNET_KEY": fernet_key, "FERNET_KEY": Fernet.generate_key().decode()}
    )
    assert both.fernet().decrypt(primary.fernet().encrypt(b"z")) == b"z"


def test_missing_and_invalid_keys() -> None:
    try:
        EnvKeySource(environ={}).fernet()
        raise AssertionError("expected ConfigurationError")
    except ConfigurationError as exc:
        assert "NABU_SESSION_FERNET_KEY" in str(exc)
    try:
        EnvKeySource(environ={"NABU_SESSION_FERNET_KEY": "not-a-key"}).fernet()
        raise AssertionError("expected ConfigurationError")
    except ConfigurationError as exc:
        assert "not a valid Fernet key" in str(exc)


def test_save_log_is_masked(tmp_path: Path, fernet_key: str, caplog) -> None:
    vault = SessionVault(tmp_path / "sessions.db", keys=StaticKeySource(fernet_key))
    with caplog.at_level(logging.INFO):
        vault.save(SessionKey.parse("owner-1", "facebook", "me"), _state())
    assert SECRET not in caplog.text
    assert "<session cookies=1 origins=1>" in caplog.text


def test_sealed_state_masks_itself() -> None:
    sealed = SealedState(_state())
    assert SECRET not in repr(sealed)
    assert sealed.reveal()["cookies"][0]["value"] == SECRET


def test_generate_fernet_key_roundtrip() -> None:
    key = generate_fernet_key()
    vault_key = StaticKeySource(key).fernet()
    assert vault_key.decrypt(vault_key.encrypt(b"session")) == b"session"
