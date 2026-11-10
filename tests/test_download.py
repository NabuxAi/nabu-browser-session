from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from nabu_browser_session import DownloadError, download_media, safe_filename

SECRET = "COOKIEVALUE_sentinel_9f3a"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


async def test_download_writes_metadata_inside_media_dir(tmp_path: Path, monkeypatch) -> None:
    body = b"\xff\xd8fake-jpeg"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "cdn.example.test"
        assert SECRET in request.headers.get("cookie", "")
        return httpx.Response(200, content=body, headers={"content-type": "image/jpeg"})

    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr("nabu_browser_session.download.httpx.AsyncClient", client)
    saved = await download_media(
        ["https://cdn.example.test/posts/../../photo.jpg"],
        tmp_path / "media",
        storage_state={
            "cookies": [
                {"name": "sessionid", "value": SECRET, "domain": "cdn.example.test", "path": "/"}
            ]
        },
    )
    assert len(saved) == 1
    item = saved[0]
    assert item.path.is_relative_to(tmp_path / "media")
    assert item.path.read_bytes() == body
    assert item.sha256 == hashlib.sha256(body).hexdigest()
    assert item.content_type == "image/jpeg"
    assert item.size_bytes == len(body)
    assert ".." not in item.path.name
    assert item.public_dict()["unofficial"] is True


def test_safe_filename_drops_directories() -> None:
    assert safe_filename("https://cdn.example.test/a/b/photo.jpg", "image/jpeg") == "photo.jpg"
    assert "/" not in safe_filename("https://cdn.example.test/a/%2e%2e%2fpasswd", "image/png")
    named = safe_filename("https://cdn.example.test/download", "video/mp4")
    assert named.endswith(".mp4")


async def test_rejects_non_http_html_and_oversized_bodies(tmp_path: Path) -> None:
    with pytest.raises(DownloadError):
        await download_media(["file:///etc/passwd"], tmp_path, client=_client(lambda request: httpx.Response(200)))

    def html(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html></html>", headers={"content-type": "text/html"})

    with pytest.raises(DownloadError, match="content type"):
        await download_media(["https://cdn.example.test/page"], tmp_path, client=_client(html))

    def big(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"abcdef", headers={"content-type": "image/png"})

    with pytest.raises(DownloadError, match="NABU_DOWNLOAD_MAX_BYTES"):
        await download_media(
            ["https://cdn.example.test/big.png"],
            tmp_path / "big",
            client=_client(big),
            max_bytes=4,
        )
    assert list((tmp_path / "big").glob("*")) == []


async def test_http_error(tmp_path: Path) -> None:
    def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"nope")

    with pytest.raises(DownloadError, match="404"):
        await download_media(["https://cdn.example.test/gone.jpg"], tmp_path, client=_client(missing))
