"""Human login capture.

Opens the platform login URL and hands the gateway a screenshot plus a capture
id. The account owner scans the QR or types the password. This package does
not store passwords and does not submit credentials on its own.
"""

from __future__ import annotations

import asyncio
import base64
import html
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from nabu_browser_session.adapter import PlatformAdapter
from nabu_browser_session.config import Settings
from nabu_browser_session.engine import (
    Capability,
    EngineBuilder,
    EngineChoice,
    EngineSelector,
    capture_live_page,
    default_engine_builder,
    launch_with_fallback,
    storage_state_of,
)
from nabu_browser_session.errors import LoginCaptureError
from nabu_browser_session.lifecycle import SessionLifecycle
from nabu_browser_session.naming import UNOFFICIAL_DISCLAIMER
from nabu_browser_session.pacing import RateLimiter
from nabu_browser_session.vault import STATUS_ACTIVE, SessionKey, SessionVault

logger = logging.getLogger("nabu_browser_session.login")


@dataclass
class LoginView:
    """What ``app/web.py`` renders. ``screenshot_png`` stays off the logs."""

    capture_id: str
    owner_id: str
    platform: str
    account: str
    url: str
    title: str
    status: str
    needs_relogin: bool
    engine: str
    fell_back: bool
    screenshot_png: bytes
    message: str
    unofficial: bool = True

    def public_dict(self) -> dict[str, Any]:
        encoded = (
            base64.b64encode(self.screenshot_png).decode("ascii") if self.screenshot_png else None
        )
        return {
            "unofficial": True,
            "disclaimer": UNOFFICIAL_DISCLAIMER,
            "capture_id": self.capture_id,
            "owner_id": self.owner_id,
            "platform": self.platform,
            "account": self.account,
            "url": self.url,
            "title": self.title,
            "status": self.status,
            "needs_relogin": self.needs_relogin,
            "engine": self.engine,
            "fell_back": self.fell_back,
            "message": self.message,
            "screenshot_base64": encoded,
        }


def render_login_fragment(view: LoginView) -> str:
    """A small HTML page a gateway can return from its own route.

    The fragment is framework-agnostic: drop it in a FastAPI or Flask response.
    Every dynamic value is escaped.
    """
    shot = ""
    if view.screenshot_png:
        encoded = base64.b64encode(view.screenshot_png).decode("ascii")
        shot = f'<img alt="Unofficial login screen" src="data:image/png;base64,{encoded}"/>'
    else:
        shot = "<p>No screenshot yet.</p>"
    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        f"<title>Unofficial {html.escape(view.platform)} login</title></head><body>"
        f"<p><strong>UNOFFICIAL</strong> {html.escape(UNOFFICIAL_DISCLAIMER)}</p>"
        f"<p>Status: {html.escape(view.status)}. {html.escape(view.message)}</p>"
        f"<p>Platform: {html.escape(view.platform)}. Engine: {html.escape(view.engine)}.</p>"
        f"<p>Login URL: {html.escape(view.url)}</p>"
        f"{shot}</body></html>"
    )


@dataclass
class _Active:
    capture_id: str
    key: SessionKey
    adapter: PlatformAdapter
    engine: Any
    context: Any
    page: Any
    choice: EngineChoice
    started: float


class LoginCapture:
    """In-process login views. One gateway worker owns the open browsers."""

    def __init__(
        self,
        *,
        settings: Settings,
        vault: SessionVault,
        lifecycle: SessionLifecycle,
        limiter: RateLimiter,
        builder: EngineBuilder | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.settings = settings
        self.vault = vault
        self.lifecycle = lifecycle
        self.limiter = limiter
        self.builder = builder or default_engine_builder
        self._now = now or time.monotonic
        self._active: dict[str, _Active] = {}
        self._by_account: dict[tuple[str, str, str], str] = {}
        self._lock = asyncio.Lock()

    async def start(self, adapter: PlatformAdapter, *, owner_id: str, account: str) -> LoginView:
        key = SessionKey.parse(owner_id, adapter.platform, account)
        async with self._lock:
            for stale in [item for item in self._active.values() if self._expired(item)]:
                await self._drop(stale.capture_id, status="expired")
            previous = self._by_account.get(_account_id(key))
            if previous:
                await self._drop(previous, status="cancelled")
            if len(self._active) >= self.settings.login_max_captures:
                raise LoginCaptureError(
                    "Too many unofficial login views are already open on this worker."
                )
        await self.limiter.acquire(key)
        choice = EngineSelector(self.settings).choose(Capability.LOGIN)
        engine, choice = await launch_with_fallback(choice, self.settings, self.builder)
        try:
            context = await engine.new_context()
            page = await context.new_page()
            await page.goto(adapter.login_url)
            live = await capture_live_page(page, engine=choice.kind.value)
        except Exception:
            await engine.close()
            raise
        capture_id = secrets.token_urlsafe(16)
        active = _Active(
            capture_id=capture_id,
            key=key,
            adapter=adapter,
            engine=engine,
            context=context,
            page=page,
            choice=choice,
            started=self._now(),
        )
        async with self._lock:
            if len(self._active) >= self.settings.login_max_captures:
                await engine.close()
                raise LoginCaptureError(
                    "Too many unofficial login views are already open on this worker."
                )
            self._active[capture_id] = active
            self._by_account[_account_id(key)] = capture_id
        logger.info(
            "opened unofficial login platform=%s engine=%s fell_back=%s",
            key.platform,
            choice.kind.value,
            choice.fell_back,
        )
        return self._view(
            active,
            url=live.url,
            title=live.title,
            screenshot=live.screenshot_png,
            status="pending",
            message="Scan the QR or finish login in this view. The password is not stored.",
        )

    async def refresh(self, capture_id: str) -> LoginView:
        active = await self._require(capture_id)
        if self._expired(active):
            return await self._expire(active)
        try:
            success = await active.adapter.detect_login_success(active.page)
        except LoginCaptureError:
            raise
        except Exception as exc:
            raise LoginCaptureError("Could not check whether login finished.") from exc
        if not success:
            live = await capture_live_page(active.page, engine=active.choice.kind.value)
            return self._view(
                active,
                url=live.url,
                title=live.title,
                screenshot=live.screenshot_png,
                status="pending",
                message="Still waiting for the account owner to finish login.",
            )
        try:
            state = await storage_state_of(active.page)
        except Exception as exc:
            raise LoginCaptureError("Login succeeded but the session could not be exported.") from exc
        self.vault.save(active.key, state)
        self.lifecycle.note_saved(active.key, reason="login capture succeeded")
        live = await capture_live_page(active.page, engine=active.choice.kind.value)
        view = self._view(
            active,
            url=live.url,
            title=live.title,
            screenshot=live.screenshot_png,
            status="succeeded",
            message="Unofficial session saved. The browser login view is closed.",
            needs_relogin=False,
        )
        async with self._lock:
            await self._drop(active.capture_id, status="succeeded")
        logger.info("saved unofficial login platform=%s", active.key.platform)
        return view

    async def interact(
        self,
        capture_id: str,
        *,
        action: str,
        selector: str | None = None,
        text: str | None = None,
        key: str | None = None,
    ) -> LoginView:
        """Forward one human action (click, fill, type, press). ``text`` is not logged."""
        verb = str(action or "").strip().lower()
        if verb not in {"click", "fill", "type", "press"}:
            raise LoginCaptureError("action must be click, fill, type, or press")
        active = await self._require(capture_id)
        if self._expired(active):
            return await self._expire(active)
        if verb == "click":
            if not selector:
                raise LoginCaptureError("click requires a selector")
            await active.page.click(selector)
        elif verb == "fill":
            if not selector:
                raise LoginCaptureError("fill requires a selector")
            await active.page.fill(selector, "" if text is None else str(text))
        elif verb == "type":
            if not selector:
                raise LoginCaptureError("type requires a selector")
            await active.page.type(selector, "" if text is None else str(text))
        else:
            await active.page.press(selector or "body", key or "Enter")
        logger.info("unofficial login interact action=%s", verb)
        return await self.refresh(capture_id)

    async def cancel(self, capture_id: str) -> LoginView:
        active = await self._require(capture_id)
        view = self._view(
            active,
            url=getattr(active.page, "url", ""),
            title="",
            screenshot=b"",
            status="cancelled",
            message="Login view closed before the account owner finished.",
        )
        async with self._lock:
            await self._drop(capture_id, status="cancelled")
        return view

    async def _require(self, capture_id: str) -> _Active:
        async with self._lock:
            active = self._active.get(capture_id)
        if active is None:
            raise LoginCaptureError("Unknown or finished login view.")
        return active

    def _expired(self, active: _Active) -> bool:
        return (self._now() - active.started) > self.settings.login_timeout_seconds

    async def _expire(self, active: _Active) -> LoginView:
        view = self._view(
            active,
            url=getattr(active.page, "url", "") or "",
            title="",
            screenshot=b"",
            status="expired",
            message="Login view expired. Start it again.",
        )
        async with self._lock:
            await self._drop(active.capture_id, status="expired")
        return view

    async def _drop(self, capture_id: str, *, status: str) -> None:
        """Caller holds ``self._lock``."""
        active = self._active.pop(capture_id, None)
        if active is None:
            return
        self._by_account.pop(_account_id(active.key), None)
        await _close_quiet(active.context)
        await active.engine.close()
        logger.info("closed unofficial login status=%s platform=%s", status, active.key.platform)

    def _view(
        self,
        active: _Active,
        *,
        url: str,
        title: str,
        screenshot: bytes,
        status: str,
        message: str,
        needs_relogin: bool | None = None,
    ) -> LoginView:
        if needs_relogin is None:
            meta = self.vault.get_meta(active.key)
            if meta is None:
                needs_relogin = True
            else:
                needs_relogin = meta.needs_relogin or meta.status != STATUS_ACTIVE
        return LoginView(
            capture_id=active.capture_id,
            owner_id=active.key.owner_id,
            platform=active.key.platform,
            account=active.key.account,
            url=str(url or ""),
            title=title,
            status=status,
            needs_relogin=needs_relogin,
            engine=active.choice.kind.value,
            fell_back=active.choice.fell_back,
            screenshot_png=screenshot,
            message=message,
        )


def _account_id(key: SessionKey) -> tuple[str, str, str]:
    return (key.owner_id, key.platform, key.account)


async def _close_quiet(context: Any) -> None:
    close = getattr(context, "close", None)
    if close is None:
        return
    result = close()
    if hasattr(result, "__await__"):
        await result
