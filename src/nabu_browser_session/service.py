"""The object a gateway constructs once and shares across unofficial tools."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from nabu_browser_session.adapter import PlatformAdapter
from nabu_browser_session.config import Settings
from nabu_browser_session.download import download_media
from nabu_browser_session.engine import (
    Capability,
    EngineBuilder,
    default_engine_builder,
    run_with_engine,
    storage_state_of,
)
from nabu_browser_session.errors import (
    ConfigurationError,
    DownloadError,
    EngineUnavailable,
    NabuBrowserError,
    NeedsReloginError,
)
from nabu_browser_session.lifecycle import HealthResult, SessionCallbacks, SessionLifecycle
from nabu_browser_session.login import LoginCapture
from nabu_browser_session.naming import UNOFFICIAL_DISCLAIMER
from nabu_browser_session.pacing import RateLimiter
from nabu_browser_session.vault import (
    STATUS_ABSENT,
    STATUS_LOGGED_OUT,
    STATUS_NEEDS_RELOGIN,
    EnvKeySource,
    KeySource,
    SessionKey,
    SessionRecord,
    SessionVault,
)

logger = logging.getLogger("nabu_browser_session.service")

PageFn = Callable[[Any], Awaitable[Any]]


class BrowserSessionService:
    """Vault, pacing, browser, and login capture for one gateway process."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        vault: SessionVault | None = None,
        callbacks: SessionCallbacks | None = None,
        lifecycle: SessionLifecycle | None = None,
        limiter: RateLimiter | None = None,
        builder: EngineBuilder | None = None,
        login_clock: Callable[[], float] | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.callbacks = callbacks or SessionCallbacks()
        self.vault = vault or SessionVault(self.settings.session_db_path, settings=self.settings)
        self.lifecycle = lifecycle or SessionLifecycle(self.vault, self.callbacks)
        self.limiter = limiter or RateLimiter.from_settings(self.settings)
        self.builder = builder or default_engine_builder
        self.login = LoginCapture(
            settings=self.settings,
            vault=self.vault,
            lifecycle=self.lifecycle,
            limiter=self.limiter,
            builder=self.builder,
            now=login_clock,
        )

    @classmethod
    def from_env(
        cls,
        environ: dict[str, str] | None = None,
        *,
        keys: KeySource | None = None,
        builder: EngineBuilder | None = None,
    ) -> BrowserSessionService:
        settings = Settings.from_env(environ)
        vault = SessionVault(
            settings.session_db_path,
            keys=keys or EnvKeySource(environ=environ),
            settings=settings,
        )
        return cls(settings, vault=vault, builder=builder)

    def status(self, adapter: PlatformAdapter, owner_id: str, account: str) -> dict[str, Any]:
        """Read the flag. Does not open a browser. Absent sessions need a login."""
        key = SessionKey.parse(owner_id, adapter.platform, account)
        record = self.vault.get_meta(key)
        if record is None:
            return {
                "unofficial": True,
                "disclaimer": UNOFFICIAL_DISCLAIMER,
                "owner_id": key.owner_id,
                "platform": key.platform,
                "account": key.account,
                "status": STATUS_ABSENT,
                "needs_relogin": True,
                "cookie_count": 0,
                "origin_count": 0,
                "last_error": None,
                "updated_at": None,
            }
        body = record.public_dict()
        body["disclaimer"] = UNOFFICIAL_DISCLAIMER
        return body

    def logout(self, adapter: PlatformAdapter, owner_id: str, account: str) -> dict[str, Any]:
        key = SessionKey.parse(owner_id, adapter.platform, account)
        self.lifecycle.mark_logged_out(key)
        return self.status(adapter, owner_id, account)

    async def health_check(
        self,
        adapter: PlatformAdapter,
        owner_id: str,
        account: str,
    ) -> HealthResult:
        key = SessionKey.parse(owner_id, adapter.platform, account)
        record = self._usable(key)
        await self.limiter.acquire(key)

        async def operation(engine: Any, choice: Any) -> HealthResult:
            context = await engine.new_context(storage_state=record.payload.reveal())  # type: ignore[union-attr]
            try:
                page = await context.new_page()
                await page.goto(adapter.probe_url)
                ok = await adapter.is_logged_in(page)
                if ok:
                    self.vault.save(key, await storage_state_of(page))
            finally:
                await _close_context(context)
            if ok:
                return self.lifecycle.apply_check(key, True, reason="login probe passed")
            return self.lifecycle.apply_check(key, False, reason="login probe returned false")

        return await self._run(key, Capability.READ, operation)

    async def profile(self, adapter: PlatformAdapter, owner_id: str, account: str) -> dict[str, Any]:
        async def _run(page: Any) -> dict[str, Any]:
            data = await adapter.fetch_profile(page)
            if not isinstance(data, dict):
                raise NabuBrowserError(f"{adapter.platform} profile primitive must return a dict")
            body = dict(data)
            body["unofficial"] = True
            body.setdefault("platform", adapter.platform)
            return body

        return await self.with_page(adapter, owner_id, account, Capability.READ, _run)

    async def list_items(
        self,
        adapter: PlatformAdapter,
        owner_id: str,
        account: str,
        *,
        kind: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        async def _run(page: Any) -> dict[str, Any]:
            items = await adapter.list_items(page, kind=kind, limit=limit)
            if not isinstance(items, list):
                raise NabuBrowserError(f"{adapter.platform} list primitive must return a list")
            return {
                "unofficial": True,
                "platform": adapter.platform,
                "kind": kind,
                "items": items,
                "count": len(items),
            }

        return await self.with_page(adapter, owner_id, account, Capability.READ, _run)

    async def send(
        self,
        adapter: PlatformAdapter,
        owner_id: str,
        account: str,
        *,
        recipient: str,
        text: str,
    ) -> dict[str, Any]:
        if not str(recipient or "").strip() or text is None or str(text) == "":
            raise ConfigurationError("recipient and text are required")

        async def _run(page: Any) -> dict[str, Any]:
            result = await adapter.send_message(page, recipient=str(recipient), text=str(text))
            if not isinstance(result, dict):
                raise NabuBrowserError(f"{adapter.platform} send primitive must return a dict")
            body = dict(result)
            body["unofficial"] = True
            return body

        return await self.with_page(adapter, owner_id, account, Capability.SEND, _run)

    async def download(
        self,
        adapter: PlatformAdapter,
        owner_id: str,
        account: str,
        *,
        target: str,
        media_dir: Path,
    ) -> list[dict[str, Any]]:
        async def _run(page: Any) -> list[dict[str, Any]]:
            urls = await adapter.download_targets(page, target=target)
            if not isinstance(urls, list) or not urls:
                raise DownloadError("download primitive returned no media URLs")
            state = await storage_state_of(page)
            saved = await download_media(
                [str(url) for url in urls],
                Path(media_dir),
                storage_state=state,
                max_bytes=self.settings.download_max_bytes,
            )
            return [item.public_dict() for item in saved]

        return await self.with_page(adapter, owner_id, account, Capability.DOWNLOAD, _run)

    async def with_page(
        self,
        adapter: PlatformAdapter,
        owner_id: str,
        account: str,
        capability: Capability,
        fn: PageFn,
        *,
        probe: bool = True,
    ) -> Any:
        """Open a saved session, run ``fn(page)``, and store the updated state.

        A missing session or a failed login probe raises :class:`NeedsReloginError`.
        """
        key = SessionKey.parse(owner_id, adapter.platform, account)
        record = self._usable(key)
        await self.limiter.acquire(key)

        async def operation(engine: Any, choice: Any) -> Any:
            context = await engine.new_context(storage_state=record.payload.reveal())  # type: ignore[union-attr]
            try:
                page = await context.new_page()
                if probe:
                    await page.goto(adapter.probe_url)
                if not await adapter.is_logged_in(page):
                    self.lifecycle.mark_needs_relogin(key, "login probe returned false")
                    raise NeedsReloginError(
                        owner_id=key.owner_id,
                        platform=key.platform,
                        account=key.account,
                        reason="login probe returned false",
                    )
                try:
                    result = await fn(page)
                except NeedsReloginError as exc:
                    self.lifecycle.mark_needs_relogin(key, exc.reason)
                    raise
                state = await storage_state_of(page)
                self.vault.save(key, state)
                self.lifecycle.note_saved(key, reason=f"{capability.value} completed")
                return result
            finally:
                await _close_context(context)

        return await self._run(key, capability, operation)

    def _usable(self, key: SessionKey) -> SessionRecord:
        try:
            record = self.vault.get(key)
        except ConfigurationError:
            self.lifecycle.mark_needs_relogin(key, "stored session cannot be decrypted")
            raise
        if record is None or record.payload is None or record.status == STATUS_LOGGED_OUT:
            self.lifecycle.mark_needs_relogin(key, "no saved session")
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

    async def _run(self, key: SessionKey, capability: Capability, operation: PageFn) -> Any:
        try:
            return await run_with_engine(
                capability,
                operation,
                self.settings,
                builder=self.builder,
            )
        except NeedsReloginError:
            raise
        except EngineUnavailable:
            raise
        except Exception as exc:
            self.lifecycle.note_check_failed(key, type(exc).__name__)
            logger.info(
                "unofficial action failed platform=%s capability=%s error=%s",
                key.platform,
                capability.value,
                type(exc).__name__,
            )
            raise


async def _close_context(context: Any) -> None:
    close = getattr(context, "close", None)
    if close is None:
        return
    result = close()
    if hasattr(result, "__await__"):
        await result
