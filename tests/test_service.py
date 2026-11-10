from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from nabu_browser_session import (
    BrowserSessionService,
    DownloadError,
    EngineKind,
    NeedsReloginError,
    PlatformAdapter,
    SessionEvent,
    SessionKey,
)
from tests.fakes import EngineFactory

SECRET = "COOKIEVALUE_sentinel_9f3a"


class DemoAdapter(PlatformAdapter):
    platform = "instagram"
    login_url = "https://www.instagram.com/accounts/login/"
    probe_url = "https://www.instagram.com/"

    def __init__(self, *, logged_in: bool = True, urls: list[str] | None = None) -> None:
        self.logged_in = logged_in
        self.urls = urls or []
        super().__init__()

    async def detect_login_success(self, page) -> bool:
        return self.logged_in

    async def fetch_profile(self, page) -> dict:
        return {"id": "42", "username": "owner"}

    async def list_items(self, page, *, kind: str, limit: int = 20) -> list[dict]:
        return [{"id": "m1", "kind": kind}]

    async def send_message(self, page, *, recipient: str, text: str) -> dict:
        if recipient == "dead":
            raise NeedsReloginError(
                owner_id="owner-1",
                platform=self.platform,
                account="me",
                reason="checkpoint",
            )
        return {"recipient": recipient, "sent": True}

    async def download_targets(self, page, *, target: str) -> list[str]:
        return list(self.urls)


def _ready(service: BrowserSessionService, adapter: DemoAdapter) -> None:
    service.vault.save(
        SessionKey.parse("owner-1", adapter.platform, "me"),
        {
            "cookies": [{"name": "sessionid", "value": SECRET, "domain": "www.instagram.com", "path": "/"}],
            "origins": [],
        },
    )


async def test_missing_session_raises_and_does_not_open_a_browser(settings) -> None:
    factory = EngineFactory()
    notices = []
    service = BrowserSessionService(settings, builder=factory)
    service.callbacks.on(SessionEvent.NEEDS_RELOGIN, notices.append)
    adapter = DemoAdapter()
    with pytest.raises(NeedsReloginError):
        await service.profile(adapter, "owner-1", "me")
    assert factory.kinds == []
    assert notices and notices[0].transition is True
    status = service.status(adapter, "owner-1", "me")
    assert status["needs_relogin"] is True
    assert status["status"] == "needs_relogin"
    assert SECRET not in str(status)
    with pytest.raises(NeedsReloginError):
        await service.profile(adapter, "owner-1", "me")
    assert len([notice for notice in notices if notice.transition]) == 1


async def test_profile_and_failed_probe(settings) -> None:
    factory = EngineFactory()
    service = BrowserSessionService(settings, builder=factory)
    adapter = DemoAdapter()
    _ready(service, adapter)
    profile = await service.profile(adapter, "owner-1", "me")
    assert profile["unofficial"] is True
    assert profile["username"] == "owner"
    assert profile["platform"] == "instagram"

    adapter.logged_in = False
    with pytest.raises(NeedsReloginError):
        await service.profile(adapter, "owner-1", "me")
    assert service.status(adapter, "owner-1", "me")["needs_relogin"] is True


async def test_send_sets_relogin_when_the_adapter_says_so(settings) -> None:
    factory = EngineFactory()
    service = BrowserSessionService(settings, builder=factory)
    adapter = DemoAdapter()
    _ready(service, adapter)
    sent = await service.send(adapter, "owner-1", "me", recipient="friend", text="hello")
    assert sent["sent"] is True
    assert sent["unofficial"] is True
    with pytest.raises(NeedsReloginError):
        await service.send(adapter, "owner-1", "me", recipient="dead", text="hello")
    assert service.vault.get_meta(SessionKey.parse("owner-1", "instagram", "me")).needs_relogin is True


async def test_download_uses_session_cookies_and_media_dir(settings, tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["cookie"] = request.headers.get("cookie", "")
        return httpx.Response(200, content=b"img", headers={"content-type": "image/png"})

    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr("nabu_browser_session.download.httpx.AsyncClient", client)
    factory = EngineFactory()
    service = BrowserSessionService(settings, builder=factory)
    adapter = DemoAdapter(urls=["https://www.instagram.com/reel.png"])
    _ready(service, adapter)
    saved = await service.download(adapter, "owner-1", "me", target="reel-1", media_dir=tmp_path / "media")
    assert saved[0]["unofficial"] is True
    assert Path(saved[0]["path"]).read_bytes() == b"img"
    assert SECRET in seen["cookie"]
    assert factory.kinds == [EngineKind.CHROMIUM]


async def test_empty_download_is_an_error(settings, tmp_path: Path) -> None:
    service = BrowserSessionService(settings, builder=EngineFactory())
    adapter = DemoAdapter(urls=[])
    _ready(service, adapter)
    with pytest.raises(DownloadError):
        await service.download(adapter, "owner-1", "me", target="story", media_dir=tmp_path)


async def test_logout_clears_the_secret(settings) -> None:
    service = BrowserSessionService(settings, builder=EngineFactory())
    adapter = DemoAdapter()
    _ready(service, adapter)
    body = service.logout(adapter, "owner-1", "me")
    assert body["status"] == "logged_out"
    assert body["needs_relogin"] is True
    assert SECRET.encode() not in settings.session_db_path.read_bytes()
    with pytest.raises(NeedsReloginError):
        await service.health_check(adapter, "owner-1", "me")


async def test_health_check_passes(settings) -> None:
    service = BrowserSessionService(settings, builder=EngineFactory())
    adapter = DemoAdapter()
    _ready(service, adapter)
    result = await service.health_check(adapter, "owner-1", "me")
    assert result.ok is True
    assert result.needs_relogin is False
    assert result.public_dict()["unofficial"] is True


def test_status_absent_prompts_without_writing_a_secret(settings) -> None:
    service = BrowserSessionService(settings, builder=EngineFactory())
    body = service.status(DemoAdapter(), "owner-9", "missing")
    assert body["status"] == "absent"
    assert body["needs_relogin"] is True
    assert "UNOFFICIAL" in body["disclaimer"]
