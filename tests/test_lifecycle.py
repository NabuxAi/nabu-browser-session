from __future__ import annotations

from pathlib import Path

import pytest

from nabu_browser_session import (
    NeedsReloginError,
    SessionCallbacks,
    SessionEvent,
    SessionKey,
    SessionLifecycle,
    SessionVault,
    StaticKeySource,
)

SECRET = "COOKIEVALUE_sentinel_9f3a"


def _vault(tmp_path: Path, fernet_key: str) -> SessionVault:
    return SessionVault(tmp_path / "sessions.db", keys=StaticKeySource(fernet_key))


def _state() -> dict:
    return {"cookies": [{"name": "sessionid", "value": SECRET, "domain": ".example.test", "path": "/"}], "origins": []}


def test_transitions_and_events(tmp_path: Path, fernet_key: str) -> None:
    vault = _vault(tmp_path, fernet_key)
    notices: list = []
    callbacks = SessionCallbacks()
    callbacks.on(None, notices.append)
    life = SessionLifecycle(vault, callbacks)
    key = SessionKey.parse("owner-1", "instagram", "me")

    with pytest.raises(NeedsReloginError):
        life.apply_check(key, True, reason="no session yet")
    assert vault.get_meta(key) is not None
    assert vault.get_meta(key).needs_relogin is True

    vault.save(key, _state())
    life.note_saved(key, reason="login capture succeeded")
    assert vault.get(key).needs_relogin is False

    result = life.apply_check(key, True, reason="login probe passed")
    assert result.ok is True
    assert result.needs_relogin is False

    with pytest.raises(NeedsReloginError) as raised:
        life.apply_check(key, False, reason="login probe returned false")
    assert raised.value.needs_relogin is True
    assert SECRET not in str(raised.value)
    flagged = vault.get(key)
    assert flagged.needs_relogin is True
    assert flagged.status == "needs_relogin"
    assert flagged.payload.reveal()["cookies"][0]["value"] == SECRET

    again = life.mark_needs_relogin(key, "login probe returned false")
    assert again.needs_relogin is True
    transitions = [notice.transition for notice in notices if notice.event is SessionEvent.NEEDS_RELOGIN]
    assert transitions[0] is True
    assert transitions[-1] is False

    life.mark_logged_out(key, reason="logged out by owner")
    logged_out = vault.get(key)
    assert logged_out.status == "logged_out"
    assert logged_out.payload is None
    assert SECRET.encode() not in (tmp_path / "sessions.db").read_bytes()

    kinds = [notice.event for notice in notices]
    assert SessionEvent.SAVED in kinds
    assert SessionEvent.HEALTHY in kinds
    assert SessionEvent.LOGGED_OUT in kinds


def test_callback_errors_propagate(tmp_path: Path, fernet_key: str) -> None:
    vault = _vault(tmp_path, fernet_key)
    callbacks = SessionCallbacks()

    def boom(notice) -> None:
        raise RuntimeError("dashboard down")

    callbacks.on(SessionEvent.NEEDS_RELOGIN, boom)
    life = SessionLifecycle(vault, callbacks)
    with pytest.raises(RuntimeError, match="dashboard down"):
        life.mark_needs_relogin(SessionKey.parse("owner", "x", "me"), "no saved session")


def test_transient_failure_does_not_flag_relogin(tmp_path: Path, fernet_key: str) -> None:
    vault = _vault(tmp_path, fernet_key)
    key = SessionKey.parse("owner", "threads", "me")
    vault.save(key, _state())
    life = SessionLifecycle(vault, SessionCallbacks())
    life.note_check_failed(key, "TimeoutError")
    record = vault.get(key)
    assert record.needs_relogin is False
    assert record.status == "active"
    assert vault.recent_events(key)[0]["event"] == "check_failed"
