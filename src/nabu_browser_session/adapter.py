"""Contract a gateway implements for one platform.

The package owns the browser, the vault, pacing, and login capture. The
adapter owns the platform: where login lives, how to tell it succeeded, and
the profile, list, send, and download primitives.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol

from nabu_browser_session.engine import page_url
from nabu_browser_session.errors import AdapterNotImplemented


class BrowserPage(Protocol):
    """The slice of a Playwright page adapters should rely on.

    A real Playwright page satisfies this. ``url`` is a string, matching
    Playwright. Extra methods are available on the real page via normal
    attribute access; tests can pass a smaller fake.
    """

    url: str

    async def goto(self, url: str, **kwargs: Any) -> Any: ...

    async def screenshot(self, **kwargs: Any) -> bytes: ...

    async def title(self) -> str: ...

    async def content(self) -> str: ...

    async def click(self, selector: str, **kwargs: Any) -> None: ...

    async def fill(self, selector: str, value: str, **kwargs: Any) -> None: ...

    async def press(self, selector: str, key: str, **kwargs: Any) -> None: ...


@dataclass(frozen=True)
class LoginSelectors:
    """Optional CSS selectors for a human typing into the dashboard.

    The package never stores the password. The dashboard sends keystrokes
    through :meth:`LoginCapture.interact` while the owner is present.
    """

    username: str | None = None
    password: str | None = None
    submit: str | None = None


class PlatformAdapter(ABC):
    """Platform-specific half of an unofficial browser session.

    Set ``platform`` and ``login_url`` on the subclass. Implement every
    primitive. A primitive that cannot do the work raises. It does not return
    an empty success.
    """

    unofficial: bool = True
    platform: str
    login_url: str

    def __init__(self) -> None:
        platform = getattr(self, "platform", "")
        login_url = getattr(self, "login_url", "")
        if not isinstance(platform, str) or not platform.strip():
            raise TypeError(f"{type(self).__name__} must set platform")
        if not isinstance(login_url, str) or not login_url.startswith(("http://", "https://")):
            raise TypeError(f"{type(self).__name__} must set an http(s) login_url")

    @property
    def probe_url(self) -> str:
        """Page opened to decide whether the saved session is still logged in."""
        return self.login_url

    def login_selectors(self) -> LoginSelectors | None:
        return None

    @abstractmethod
    async def detect_login_success(self, page: Any) -> bool:
        """True only when the human has finished login on this page."""

    async def is_logged_in(self, page: Any) -> bool:
        return await self.detect_login_success(page)

    @abstractmethod
    async def fetch_profile(self, page: Any) -> dict[str, Any]:
        """Return a JSON-ready profile dict. Include ``unofficial: True``."""

    @abstractmethod
    async def list_items(self, page: Any, *, kind: str, limit: int = 20) -> list[dict[str, Any]]:
        """List posts, threads, or other items. ``kind`` is the gateway's word."""

    @abstractmethod
    async def send_message(self, page: Any, *, recipient: str, text: str) -> dict[str, Any]:
        """Send one message as the logged-in owner. Pace is applied by the caller."""

    @abstractmethod
    async def download_targets(self, page: Any, *, target: str) -> list[str]:
        """Return http(s) media URLs for one post, reel, story, or message."""


class StubPlatformAdapter(PlatformAdapter):
    """Example adapter. It does not call a real social platform.

    Gateways copy this shape, replace ``platform`` and ``login_url``, and fill
    in the five primitives with their own page logic.
    """

    platform = "stub"
    login_url = "https://example.test/login"

    async def detect_login_success(self, page: Any) -> bool:
        return "logged-in" in page_url(page)

    async def fetch_profile(self, page: Any) -> dict[str, Any]:
        return {
            "unofficial": True,
            "platform": self.platform,
            "id": "stub-user",
            "stub": True,
        }

    async def list_items(self, page: Any, *, kind: str, limit: int = 20) -> list[dict[str, Any]]:
        item = {
            "unofficial": True,
            "platform": self.platform,
            "kind": kind,
            "id": "stub-item",
            "stub": True,
        }
        if limit <= 0:
            return []
        return [item]

    async def send_message(self, page: Any, *, recipient: str, text: str) -> dict[str, Any]:
        raise AdapterNotImplemented(
            "StubPlatformAdapter does not send messages. The platform adapter fills this in."
        )

    async def download_targets(self, page: Any, *, target: str) -> list[str]:
        raise AdapterNotImplemented(
            "StubPlatformAdapter does not list media URLs. The platform adapter fills this in."
        )
