"""Unofficial browser sessions for Nabux social and messaging gateways.

The official API path of each gateway stays as it is. This package is the
shared browser-login path beside it. Sessions are encrypted, actions are
slow, and a dead session raises instead of returning an empty success.

Not affiliated with Instagram, Meta, WhatsApp, Snapchat, Facebook, Threads,
X, LinkedIn, or TikTok. The account owner accepts those platforms' terms and
the risk of checkpoints or bans.
"""

from __future__ import annotations

from nabu_browser_session.adapter import (
    BrowserPage,
    LoginSelectors,
    PlatformAdapter,
    StubPlatformAdapter,
)
from nabu_browser_session.config import FERNET_KEY_ALIAS, FERNET_KEY_ENV, Settings
from nabu_browser_session.download import DownloadResult, cookies_from_storage, download_media, safe_filename
from nabu_browser_session.engine import (
    BrowserEngine,
    Capability,
    ChromiumEngine,
    EngineChoice,
    EngineKind,
    EngineSelector,
    LightpandaEngine,
    LivePage,
    capture_live_page,
    default_engine_builder,
    open_engine,
    page_url,
    run_with_engine,
    storage_state_of,
)
from nabu_browser_session.errors import (
    AdapterNotImplemented,
    ConfigurationError,
    DownloadError,
    EngineUnavailable,
    LoginCaptureError,
    NabuBrowserError,
    NeedsReloginError,
)
from nabu_browser_session.lifecycle import (
    HealthResult,
    SessionCallbacks,
    SessionEvent,
    SessionLifecycle,
    SessionNotice,
)
from nabu_browser_session.login import LoginCapture, LoginView, render_login_fragment
from nabu_browser_session.masking import mask_account, mask_secret, mask_session
from nabu_browser_session.naming import UNOFFICIAL_ACTIONS, UNOFFICIAL_DISCLAIMER, unofficial_disclaimer, unofficial_tool_name
from nabu_browser_session.pacing import Clock, PaceTicket, RateLimiter, rate_limiter_from_env
from nabu_browser_session.service import BrowserSessionService
from nabu_browser_session.vault import (
    EnvKeySource,
    KeySource,
    SealedState,
    SessionKey,
    SessionRecord,
    SessionVault,
    StaticKeySource,
    generate_fernet_key,
)

__version__ = "0.1.0"

__all__ = [
    "AdapterNotImplemented",
    "BrowserEngine",
    "BrowserPage",
    "BrowserSessionService",
    "Capability",
    "ChromiumEngine",
    "Clock",
    "ConfigurationError",
    "DownloadError",
    "DownloadResult",
    "EngineChoice",
    "EngineKind",
    "EngineSelector",
    "EngineUnavailable",
    "EnvKeySource",
    "FERNET_KEY_ALIAS",
    "FERNET_KEY_ENV",
    "HealthResult",
    "KeySource",
    "LightpandaEngine",
    "LivePage",
    "LoginCapture",
    "LoginCaptureError",
    "LoginSelectors",
    "LoginView",
    "NabuBrowserError",
    "NeedsReloginError",
    "PaceTicket",
    "PlatformAdapter",
    "RateLimiter",
    "SealedState",
    "SessionCallbacks",
    "SessionEvent",
    "SessionKey",
    "SessionLifecycle",
    "SessionNotice",
    "SessionRecord",
    "SessionVault",
    "Settings",
    "StaticKeySource",
    "StubPlatformAdapter",
    "UNOFFICIAL_ACTIONS",
    "UNOFFICIAL_DISCLAIMER",
    "capture_live_page",
    "cookies_from_storage",
    "default_engine_builder",
    "download_media",
    "generate_fernet_key",
    "mask_account",
    "mask_secret",
    "mask_session",
    "open_engine",
    "page_url",
    "rate_limiter_from_env",
    "render_login_fragment",
    "run_with_engine",
    "safe_filename",
    "storage_state_of",
    "unofficial_disclaimer",
    "unofficial_tool_name",
]
