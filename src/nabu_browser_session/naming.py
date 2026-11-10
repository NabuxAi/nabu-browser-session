"""Names for unofficial MCP tools.

Official gateway tools stay ``{platform}_{action}`` (for example
``instagram_profile``). Browser-login tools use a separate namespace so a
client can tell the unofficial path from the official API path.
"""

from __future__ import annotations

import re

UNOFFICIAL_DISCLAIMER = (
    "UNOFFICIAL browser session, not the platform's official API. "
    "The account owner accepts the platform terms and the risk of checkpoints "
    "or bans. Actions are paced slowly on purpose."
)

UNOFFICIAL_ACTIONS: tuple[str, ...] = (
    "login_start",
    "login_refresh",
    "session_status",
    "profile",
    "list",
    "send",
    "download",
)

_PLATFORM = re.compile(r"[a-z][a-z0-9_]{0,31}")
_ACTION = re.compile(r"[a-z][a-z0-9_]{0,48}")


def unofficial_tool_name(platform: str, action: str) -> str:
    """Return ``{platform}_unofficial_{action}``.

    Example: ``unofficial_tool_name("instagram", "profile")`` is
    ``instagram_unofficial_profile``.
    """
    platform_slug = str(platform or "").strip().lower()
    action_slug = str(action or "").strip().lower()
    if not _PLATFORM.fullmatch(platform_slug):
        raise ValueError("platform must be a short lowercase slug such as 'instagram'")
    if not _ACTION.fullmatch(action_slug):
        raise ValueError("action must be a short lowercase slug such as 'profile'")
    return f"{platform_slug}_unofficial_{action_slug}"


def unofficial_disclaimer() -> str:
    """The sentence every unofficial tool description should include."""
    return UNOFFICIAL_DISCLAIMER
