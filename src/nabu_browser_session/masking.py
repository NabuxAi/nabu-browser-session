"""Log-safe descriptions of sessions and secrets.

``str`` and ``repr`` of sealed session state go through these helpers.
Cookie values, storage values, and Fernet tokens must not appear in logs.
"""

from __future__ import annotations

from typing import Any


def mask_secret(value: object) -> str:
    """Describe a secret by its length only."""
    if value is None:
        return "<empty>"
    if isinstance(value, (bytes, bytearray)):
        return f"<redacted bytes len={len(value)}>"
    text = str(value)
    if text == "":
        return "<empty>"
    return f"<redacted len={len(text)}>"


def mask_session(payload: Any) -> str:
    """Summarise a Playwright storage state without names or values."""
    if payload is None:
        return "<session empty>"
    if isinstance(payload, dict):
        cookies = payload.get("cookies")
        origins = payload.get("origins")
        cookie_count = len(cookies) if isinstance(cookies, list) else 0
        origin_count = len(origins) if isinstance(origins, list) else 0
        return f"<session cookies={cookie_count} origins={origin_count}>"
    if isinstance(payload, (bytes, bytearray)):
        return f"<redacted bytes len={len(payload)}>"
    return f"<redacted len={len(str(payload))}>"


def mask_account(account: str) -> str:
    """Short label for an account key. Not a cookie."""
    text = str(account or "")
    if len(text) <= 4:
        return "<account>"
    return f"{text[:2]}…{text[-2:]}"
