"""Errors raised by unofficial browser sessions.

Messages name the platform and the reason. They never include cookies,
localStorage, passwords, or Fernet tokens.
"""

from __future__ import annotations


class NabuBrowserError(Exception):
    """Base error for nabu_browser_session."""


class ConfigurationError(NabuBrowserError):
    """An environment variable or constructor argument is missing or invalid."""


class NeedsReloginError(NabuBrowserError):
    """The unofficial session cannot be used until a human logs in again.

    Raised on purpose. Callers must surface it to the dashboard or the MCP
    client. Catching it and returning an empty success hides a dead session.
    """

    def __init__(self, *, owner_id: str, platform: str, account: str, reason: str) -> None:
        self.owner_id = owner_id
        self.platform = platform
        self.account = account
        self.reason = reason
        self.needs_relogin = True
        self.unofficial = True
        super().__init__(
            f"UNOFFICIAL {platform} session needs a fresh login ({reason}). "
            "Open the gateway login view and let the account owner finish it."
        )


class EngineUnavailable(NabuBrowserError):
    """The selected browser engine could not launch or could not drive the flow."""


class LoginCaptureError(NabuBrowserError):
    """The in-memory human login view is missing, expired, or already finished."""


class DownloadError(NabuBrowserError):
    """A media download could not be saved."""


class AdapterNotImplemented(NabuBrowserError):
    """The platform adapter has not filled in this primitive."""
