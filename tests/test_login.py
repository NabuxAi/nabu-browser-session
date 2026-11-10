from __future__ import annotations

import logging

import pytest

from nabu_browser_session import (
    BrowserSessionService,
    EngineKind,
    LoginCaptureError,
    SessionKey,
    StubPlatformAdapter,
    render_login_fragment,
)
from tests.fakes import EngineFactory, ManualClock

SECRET = "COOKIEVALUE_sentinel_9f3a"
PASSWORD = "owner-typed-password"


def _service(settings, factory, clock=None) -> BrowserSessionService:
    return BrowserSessionService(settings, builder=factory, login_clock=clock)


async def test_login_persists_encrypted_session_and_surfaces_screenshot(settings, caplog) -> None:
    factory = EngineFactory()
    service = _service(settings, factory)
    adapter = StubPlatformAdapter()
    with caplog.at_level(logging.INFO):
        view = await service.login.start(adapter, owner_id="owner-1", account="me")
    assert view.status == "pending"
    assert view.needs_relogin is True
    assert view.unofficial is True
    assert view.screenshot_png.startswith(b"\x89PNG")
    assert view.public_dict()["screenshot_base64"]
    assert "UNOFFICIAL" in view.public_dict()["disclaimer"]
    assert SECRET not in caplog.text

    factory.engines[0].page.url = "https://example.test/logged-in"
    factory.engines[0].context.state = {
        "cookies": [{"name": "sessionid", "value": SECRET, "domain": ".example.test", "path": "/"}],
        "origins": [],
    }
    done = await service.login.refresh(view.capture_id)
    assert done.status == "succeeded"
    assert done.needs_relogin is False
    assert factory.engines[0].closed is True
    record = service.vault.get(SessionKey.parse("owner-1", "stub", "me"))
    assert record is not None
    assert record.payload.reveal()["cookies"][0]["value"] == SECRET
    assert SECRET.encode() not in settings.session_db_path.read_bytes()
    assert SECRET not in caplog.text
    with pytest.raises(LoginCaptureError):
        await service.login.refresh(view.capture_id)


async def test_login_on_lightpanda_request_uses_chromium(settings, fernet_key: str) -> None:
    from nabu_browser_session import Settings

    light = Settings.from_env(
        {
            "SESSION_DIR": str(settings.session_dir),
            "SESSION_DB_PATH": str(settings.session_db_path),
            "NABU_SESSION_FERNET_KEY": fernet_key,
            "BROWSER_ENGINE": "lightpanda",
            "LIGHTPANDA_CDP_URL": "ws://127.0.0.1:9222",
            "NABU_RATE_MIN_INTERVAL_SECONDS": "0",
            "NABU_RATE_JITTER_SECONDS": "0",
            "NABU_RATE_CAPACITY": "10",
            "NABU_RATE_REFILL_PER_MINUTE": "600",
        }
    )
    factory = EngineFactory()
    service = BrowserSessionService(light, builder=factory)
    view = await service.login.start(StubPlatformAdapter(), owner_id="owner-1", account="me")
    assert factory.kinds == [EngineKind.CHROMIUM]
    assert view.engine == "chromium"
    assert view.fell_back is True
    await service.login.cancel(view.capture_id)


async def test_interact_does_not_log_the_password(settings, caplog) -> None:
    factory = EngineFactory()
    service = _service(settings, factory)
    view = await service.login.start(StubPlatformAdapter(), owner_id="owner-1", account="me")
    with caplog.at_level(logging.INFO):
        pending = await service.login.interact(
            view.capture_id,
            action="fill",
            selector="input[name=password]",
            text=PASSWORD,
        )
    assert pending.status == "pending"
    assert factory.engines[0].page.fills == [("input[name=password]", PASSWORD)]
    assert PASSWORD not in caplog.text


async def test_login_view_expires(settings) -> None:
    clock = ManualClock()
    factory = EngineFactory()
    service = _service(settings, factory, clock)
    view = await service.login.start(StubPlatformAdapter(), owner_id="owner-1", account="me")
    clock.t = settings.login_timeout_seconds + 1
    expired = await service.login.refresh(view.capture_id)
    assert expired.status == "expired"
    assert factory.engines[0].closed is True
    with pytest.raises(LoginCaptureError):
        await service.login.refresh(view.capture_id)


def test_render_escapes_the_login_url() -> None:
    from nabu_browser_session import LoginView

    view = LoginView(
        capture_id="cap",
        owner_id="owner",
        platform="stub",
        account="me",
        url='https://example.test/login?next=<script>alert(1)</script>',
        title="Login",
        status="pending",
        needs_relogin=True,
        engine="chromium",
        fell_back=False,
        screenshot_png=b"\x89PNG",
        message="Scan the QR",
    )
    html = render_login_fragment(view)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
    assert "UNOFFICIAL" in html
    assert SECRET not in html
