from __future__ import annotations

import ast
from pathlib import Path

import pytest

from nabu_browser_session import (
    AdapterNotImplemented,
    PlatformAdapter,
    StubPlatformAdapter,
    UNOFFICIAL_ACTIONS,
    unofficial_disclaimer,
    unofficial_tool_name,
)
from tests.fakes import FakePage


def test_incomplete_adapter_cannot_be_constructed() -> None:
    class Half(PlatformAdapter):
        platform = "instagram"
        login_url = "https://www.instagram.com/accounts/login/"

    with pytest.raises(TypeError):
        Half()


async def test_stub_contract() -> None:
    adapter = StubPlatformAdapter()
    assert adapter.unofficial is True
    assert adapter.platform == "stub"
    assert adapter.login_url.startswith("https://")
    assert adapter.probe_url == adapter.login_url
    assert await adapter.detect_login_success(FakePage("https://example.test/logged-in")) is True
    assert await adapter.is_logged_in(FakePage("https://example.test/login")) is False
    profile = await adapter.fetch_profile(FakePage())
    assert profile["unofficial"] is True
    assert profile["stub"] is True
    listed = await adapter.list_items(FakePage(), kind="thread", limit=5)
    assert listed[0]["kind"] == "thread"
    with pytest.raises(AdapterNotImplemented):
        await adapter.send_message(FakePage(), recipient="friend", text="hi")
    with pytest.raises(AdapterNotImplemented):
        await adapter.download_targets(FakePage(), target="post-1")


def test_tool_namespace_sits_beside_official_names() -> None:
    assert unofficial_tool_name("instagram", "profile") == "instagram_unofficial_profile"
    names = [unofficial_tool_name("whatsapp", action) for action in UNOFFICIAL_ACTIONS]
    assert names[0] == "whatsapp_unofficial_login_start"
    assert "whatsapp_profile" not in names
    assert "UNOFFICIAL" in unofficial_disclaimer()
    assert unofficial_tool_name("Instagram", "Profile") == "instagram_unofficial_profile"
    with pytest.raises(ValueError):
        unofficial_tool_name("not a platform", "profile")
    with pytest.raises(ValueError):
        unofficial_tool_name("x", "Send Message")


def test_gateway_example_parses_and_uses_the_namespace() -> None:
    source = Path(__file__).parents[1].joinpath("examples", "wire_gateway.py").read_text()
    ast.parse(source)
    assert "unofficial_tool_name" in source
    assert "BrowserSessionService.from_env" in source
    assert "StubPlatformAdapter" in source
    assert source.count("\n") < 45
