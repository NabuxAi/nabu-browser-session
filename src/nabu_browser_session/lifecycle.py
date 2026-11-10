"""Session health, the needs_relogin flag, and dashboard callbacks.

A failed check raises :class:`NeedsReloginError` after the flag is stored.
Returning an empty result from a dead session is intentionally not offered.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from nabu_browser_session.errors import NeedsReloginError
from nabu_browser_session.vault import (
    STATUS_ACTIVE,
    STATUS_LOGGED_OUT,
    STATUS_NEEDS_RELOGIN,
    SessionKey,
    SessionRecord,
    SessionVault,
)


class SessionEvent(str, Enum):
    SAVED = "saved"
    HEALTHY = "healthy"
    NEEDS_RELOGIN = "needs_relogin"
    LOGGED_OUT = "logged_out"
    CHECK_FAILED = "check_failed"


@dataclass(frozen=True)
class SessionNotice:
    event: SessionEvent
    owner_id: str
    platform: str
    account: str
    reason: str
    needs_relogin: bool
    status: str
    at: str
    transition: bool
    unofficial: bool = True

    def public_dict(self) -> dict[str, Any]:
        return {
            "unofficial": True,
            "event": self.event.value,
            "owner_id": self.owner_id,
            "platform": self.platform,
            "account": self.account,
            "reason": self.reason,
            "needs_relogin": self.needs_relogin,
            "status": self.status,
            "at": self.at,
            "transition": self.transition,
        }


@dataclass(frozen=True)
class HealthResult:
    ok: bool
    needs_relogin: bool
    status: str
    owner_id: str
    platform: str
    account: str
    reason: str
    unofficial: bool = True

    def public_dict(self) -> dict[str, Any]:
        return {
            "unofficial": True,
            "ok": self.ok,
            "needs_relogin": self.needs_relogin,
            "status": self.status,
            "owner_id": self.owner_id,
            "platform": self.platform,
            "account": self.account,
            "reason": self.reason,
        }


class SessionCallbacks:
    """Handlers a gateway dashboard or MCP tool registers.

    Exceptions from a handler propagate. They are not swallowed.
    """

    def __init__(self) -> None:
        self._handlers: list[tuple[SessionEvent | None, Callable[[SessionNotice], None]]] = []

    def on(
        self,
        event: SessionEvent | None,
        handler: Callable[[SessionNotice], None],
    ) -> Callable[[SessionNotice], None]:
        """Subscribe to one event, or every event when ``event`` is ``None``."""
        self._handlers.append((event, handler))
        return handler

    def emit(self, notice: SessionNotice) -> None:
        for subscribed, handler in list(self._handlers):
            if subscribed is None or subscribed is notice.event or subscribed == notice.event:
                handler(notice)


class SessionLifecycle:
    def __init__(self, vault: SessionVault, callbacks: SessionCallbacks | None = None) -> None:
        self.vault = vault
        self.callbacks = callbacks or SessionCallbacks()

    def mark_needs_relogin(self, key: SessionKey, reason: str) -> SessionRecord:
        previous = self.vault.get_meta(key)
        transition = (
            previous is None
            or not previous.needs_relogin
            or previous.status != STATUS_NEEDS_RELOGIN
        )
        record = self.vault.mark(
            key,
            needs_relogin=True,
            status=STATUS_NEEDS_RELOGIN,
            reason=reason,
        )
        self._emit(SessionEvent.NEEDS_RELOGIN, record, reason, transition)
        return record

    def mark_healthy(self, key: SessionKey, reason: str = "session is usable") -> SessionRecord:
        current = self.vault.get(key)
        if current is None or current.payload is None:
            self.mark_needs_relogin(key, "no saved session")
            raise NeedsReloginError(
                owner_id=key.owner_id,
                platform=key.platform,
                account=key.account,
                reason="no saved session",
            )
        transition = current.needs_relogin or current.status != STATUS_ACTIVE
        record = self.vault.mark(
            key,
            needs_relogin=False,
            status=STATUS_ACTIVE,
            reason=None,
        )
        self._emit(SessionEvent.HEALTHY, record, reason, transition)
        return record

    def mark_logged_out(self, key: SessionKey, reason: str = "logged out by owner") -> SessionRecord:
        record = self.vault.mark(
            key,
            needs_relogin=True,
            status=STATUS_LOGGED_OUT,
            reason=reason,
            clear_secret=True,
        )
        self._emit(SessionEvent.LOGGED_OUT, record, reason, True)
        return record

    def note_saved(self, key: SessionKey, reason: str = "session saved") -> SessionRecord | None:
        record = self.vault.get_meta(key)
        if record is None:
            return None
        self._emit(SessionEvent.SAVED, record, reason, True)
        return record

    def note_check_failed(self, key: SessionKey, reason: str) -> None:
        """Record a transient failure without flipping ``needs_relogin``."""
        record = self.vault.get_meta(key)
        status = record.status if record else "absent"
        needs = True if record is None else record.needs_relogin
        notice = SessionNotice(
            event=SessionEvent.CHECK_FAILED,
            owner_id=key.owner_id,
            platform=key.platform,
            account=key.account,
            reason=reason,
            needs_relogin=needs,
            status=status,
            at=_now(),
            transition=False,
        )
        self.vault.add_event(
            key,
            event=SessionEvent.CHECK_FAILED.value,
            reason=reason,
            needs_relogin=needs,
            status=status,
        )
        self.callbacks.emit(notice)

    def apply_check(self, key: SessionKey, ok: bool, *, reason: str) -> HealthResult:
        """Apply a health-check result. Failure raises; it does not return ``ok=False``."""
        if ok:
            record = self.mark_healthy(key, reason=reason or "health check passed")
            return HealthResult(
                ok=True,
                needs_relogin=False,
                status=record.status,
                owner_id=key.owner_id,
                platform=key.platform,
                account=key.account,
                reason=reason or "health check passed",
            )
        why = reason or "health check failed"
        self.mark_needs_relogin(key, why)
        raise NeedsReloginError(
            owner_id=key.owner_id,
            platform=key.platform,
            account=key.account,
            reason=why,
        )

    def _emit(self, event: SessionEvent, record: SessionRecord, reason: str, transition: bool) -> None:
        notice = SessionNotice(
            event=event,
            owner_id=record.key.owner_id,
            platform=record.key.platform,
            account=record.key.account,
            reason=reason,
            needs_relogin=record.needs_relogin,
            status=record.status,
            at=_now(),
            transition=transition,
        )
        self.vault.add_event(
            record.key,
            event=event.value,
            reason=reason,
            needs_relogin=record.needs_relogin,
            status=record.status,
        )
        self.callbacks.emit(notice)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
