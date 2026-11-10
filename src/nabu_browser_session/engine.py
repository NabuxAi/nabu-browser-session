"""Browser engines.

Chromium (Playwright) is the default. Lightpanda is an optional CDP engine for
light reads and downloads. Login, upload, and send stay on Chromium, and a
Lightpanda failure falls back to Chromium instead of failing closed-silent.
"""

from __future__ import annotations

import inspect
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any

from nabu_browser_session.config import Settings
from nabu_browser_session.errors import ConfigurationError, EngineUnavailable

logger = logging.getLogger("nabu_browser_session.engine")


class EngineKind(str, Enum):
    CHROMIUM = "chromium"
    LIGHTPANDA = "lightpanda"


class Capability(str, Enum):
    LOGIN = "login"
    UPLOAD = "upload"
    SEND = "send"
    READ = "read"
    DOWNLOAD = "download"


# Lightpanda cannot complete interactive login, file upload, or a compose box.
_LIGHTPANDA_OK = {Capability.READ, Capability.DOWNLOAD}


@dataclass(frozen=True)
class EngineChoice:
    kind: EngineKind
    requested: EngineKind
    fell_back: bool
    reason: str
    capability: Capability

    def public_dict(self) -> dict[str, object]:
        return {
            "unofficial": True,
            "engine": self.kind.value,
            "requested": self.requested.value,
            "fell_back": self.fell_back,
            "reason": self.reason,
            "capability": self.capability.value,
        }


@dataclass(frozen=True)
class LivePage:
    """A screenshot of the page the account owner has to finish (QR or login)."""

    url: str
    title: str
    screenshot_png: bytes
    engine: str
    unofficial: bool = True


class EngineSelector:
    """Pick Chromium or Lightpanda for one capability. Pure: it does not launch."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def choose(self, capability: Capability) -> EngineChoice:
        try:
            requested = EngineKind(self.settings.browser_engine)
        except ValueError as exc:
            raise ConfigurationError(
                "BROWSER_ENGINE must be 'chromium' or 'lightpanda'"
            ) from exc
        if requested is EngineKind.CHROMIUM:
            return EngineChoice(
                kind=EngineKind.CHROMIUM,
                requested=requested,
                fell_back=False,
                reason="Chromium selected",
                capability=capability,
            )
        if not self.settings.lightpanda_cdp_url:
            return EngineChoice(
                kind=EngineKind.CHROMIUM,
                requested=requested,
                fell_back=True,
                reason="LIGHTPANDA_CDP_URL is not set; using Chromium",
                capability=capability,
            )
        if capability not in _LIGHTPANDA_OK:
            return EngineChoice(
                kind=EngineKind.CHROMIUM,
                requested=requested,
                fell_back=True,
                reason=(
                    f"Lightpanda does not drive {capability.value}; using Chromium. "
                    "Login, upload, and send stay on Chromium."
                ),
                capability=capability,
            )
        return EngineChoice(
            kind=EngineKind.LIGHTPANDA,
            requested=requested,
            fell_back=False,
            reason="Lightpanda selected for a light read or download",
            capability=capability,
        )


class BrowserEngine(ABC):
    """One browser process (or one CDP connection) with a context per session."""

    kind: EngineKind

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._launched = False
        self._closed = False

    @property
    def launched(self) -> bool:
        return self._launched and not self._closed

    @abstractmethod
    def supports(self, capability: Capability) -> bool: ...

    @abstractmethod
    async def launch(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def new_context(self, *, storage_state: dict[str, Any] | None = None) -> Any:
        """Return a Playwright-like context. One context belongs to one account."""


class ChromiumEngine(BrowserEngine):
    """Local Playwright Chromium. This process owns the browser."""

    kind = EngineKind.CHROMIUM

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._playwright: Any = None
        self._browser: Any = None

    def supports(self, capability: Capability) -> bool:
        return True

    async def launch(self) -> None:
        if self._closed:
            raise EngineUnavailable("Chromium engine is already closed")
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.launch(headless=self.settings.headless)
        except Exception as exc:
            await self._playwright.stop()
            self._playwright = None
            raise EngineUnavailable("Chromium failed to launch") from exc
        self._launched = True
        logger.info("launched chromium headless=%s", self.settings.headless)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        self._launched = False

    async def new_context(self, *, storage_state: dict[str, Any] | None = None) -> Any:
        if not self.launched or self._browser is None:
            raise EngineUnavailable("Chromium is not launched")
        kwargs: dict[str, Any] = {}
        if storage_state:
            kwargs["storage_state"] = storage_state
        return await self._browser.new_context(**kwargs)


class LightpandaEngine(BrowserEngine):
    """Connect to a Lightpanda process over CDP. This process does not own it.

    ``close`` disconnects. It does not shut the Lightpanda server down.
    """

    kind = EngineKind.LIGHTPANDA

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._playwright: Any = None
        self._browser: Any = None

    def supports(self, capability: Capability) -> bool:
        return capability in _LIGHTPANDA_OK

    async def launch(self) -> None:
        if self._closed:
            raise EngineUnavailable("Lightpanda engine is already closed")
        if not self.settings.lightpanda_cdp_url:
            raise EngineUnavailable("LIGHTPANDA_CDP_URL is not set")
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.connect_over_cdp(
                self.settings.lightpanda_cdp_url
            )
        except Exception as exc:
            await self._playwright.stop()
            self._playwright = None
            raise EngineUnavailable("Lightpanda CDP connection failed") from exc
        self._launched = True
        logger.info("connected to lightpanda over CDP")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Drop the CDP session. Do not call browser.close(): that can stop the
        # shared Lightpanda process other reads are using.
        self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        self._launched = False

    async def new_context(self, *, storage_state: dict[str, Any] | None = None) -> Any:
        if not self.launched or self._browser is None:
            raise EngineUnavailable("Lightpanda is not connected")
        kwargs: dict[str, Any] = {}
        if storage_state:
            kwargs["storage_state"] = storage_state
        try:
            return await self._browser.new_context(**kwargs)
        except Exception as exc:
            raise EngineUnavailable("Lightpanda could not open a browser context") from exc


EngineBuilder = Callable[[EngineKind, Settings], BrowserEngine]


def default_engine_builder(kind: EngineKind, settings: Settings) -> BrowserEngine:
    if kind is EngineKind.LIGHTPANDA:
        return LightpandaEngine(settings)
    return ChromiumEngine(settings)


async def launch_with_fallback(
    choice: EngineChoice,
    settings: Settings,
    builder: EngineBuilder,
) -> tuple[BrowserEngine, EngineChoice]:
    """Launch ``choice``. If Lightpanda fails, launch Chromium once."""
    engine = builder(choice.kind, settings)
    try:
        await engine.launch()
        return engine, choice
    except Exception as exc:
        await engine.close()
        if choice.kind is not EngineKind.LIGHTPANDA:
            if isinstance(exc, EngineUnavailable):
                raise
            raise EngineUnavailable("Chromium failed to launch") from exc
        fallback = EngineChoice(
            kind=EngineKind.CHROMIUM,
            requested=choice.requested,
            fell_back=True,
            reason="Lightpanda unavailable; using Chromium",
            capability=choice.capability,
        )
        chromium = builder(EngineKind.CHROMIUM, settings)
        try:
            await chromium.launch()
        except Exception as exc2:
            await chromium.close()
            raise EngineUnavailable(
                "Lightpanda failed and the Chromium fallback failed"
            ) from exc2
        logger.info("fell back from lightpanda to chromium for %s", choice.capability.value)
        return chromium, fallback


@asynccontextmanager
async def open_engine(
    capability: Capability,
    settings: Settings | None = None,
    *,
    builder: EngineBuilder | None = None,
) -> AsyncIterator[tuple[BrowserEngine, EngineChoice]]:
    """Launch the engine for ``capability`` and close it on the way out."""
    settings = settings or Settings.from_env()
    builder = builder or default_engine_builder
    choice = EngineSelector(settings).choose(capability)
    engine, choice = await launch_with_fallback(choice, settings, builder)
    try:
        yield engine, choice
    finally:
        await engine.close()


async def run_with_engine(
    capability: Capability,
    operation: Callable[[BrowserEngine, EngineChoice], Awaitable[Any]],
    settings: Settings | None = None,
    *,
    builder: EngineBuilder | None = None,
) -> Any:
    """Run ``operation(engine, choice)``.

    A Lightpanda launch failure, or :class:`EngineUnavailable` raised by the
    operation before a side effect, is retried once on Chromium. Other
    exceptions are not retried, so a send is not performed twice.
    """
    settings = settings or Settings.from_env()
    builder = builder or default_engine_builder
    choice = EngineSelector(settings).choose(capability)
    engine, choice = await launch_with_fallback(choice, settings, builder)
    try:
        try:
            return await operation(engine, choice)
        except EngineUnavailable:
            if choice.kind is not EngineKind.LIGHTPANDA:
                raise
            await engine.close()
            fallback = EngineChoice(
                kind=EngineKind.CHROMIUM,
                requested=EngineKind.LIGHTPANDA,
                fell_back=True,
                reason=f"Lightpanda could not drive {capability.value}; using Chromium",
                capability=capability,
            )
            chromium, fallback = await launch_with_fallback(fallback, settings, builder)
            try:
                return await operation(chromium, fallback)
            finally:
                await chromium.close()
    finally:
        await engine.close()


def page_url(page: Any) -> str:
    """Read ``page.url`` the way Playwright exposes it (a string property)."""
    url = getattr(page, "url", "")
    if callable(url):
        url = url()
    if inspect.isawaitable(url):
        raise EngineUnavailable("page.url must be a string, matching Playwright")
    return str(url or "")


async def capture_live_page(page: Any, *, engine: str) -> LivePage:
    """Screenshot the current page so a dashboard can show a QR or login form."""
    title = ""
    if hasattr(page, "title"):
        titled = page.title()
        title = await titled if inspect.isawaitable(titled) else str(titled or "")
    if not hasattr(page, "screenshot"):
        raise EngineUnavailable("page cannot take a screenshot")
    shot = page.screenshot(type="png")
    if inspect.isawaitable(shot):
        shot = await shot
    if not isinstance(shot, (bytes, bytearray)) or not shot:
        raise EngineUnavailable("page screenshot did not return bytes")
    return LivePage(url=page_url(page), title=str(title or ""), screenshot_png=bytes(shot), engine=engine)


async def storage_state_of(page: Any) -> dict[str, Any]:
    """Export cookies and storage. The dict is a secret; do not log it."""
    context = getattr(page, "context", None)
    if context is None:
        raise EngineUnavailable("page has no browser context")
    method = getattr(context, "storage_state", None)
    if method is None:
        raise EngineUnavailable("browser context cannot export storage state")
    state = method()
    if inspect.isawaitable(state):
        state = await state
    if not isinstance(state, dict):
        raise EngineUnavailable("storage state was not a dict")
    return state
