"""Save platform media into a directory the gateway already owns.

The helper returns file paths and metadata. It does not pick a platform or
decide which post, reel, or story to fetch — the adapter does that.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from nabu_browser_session.errors import DownloadError

logger = logging.getLogger("nabu_browser_session.download")

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
}


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    url: str
    content_type: str | None
    size_bytes: int
    filename: str
    sha256: str
    unofficial: bool = True

    def public_dict(self) -> dict[str, Any]:
        return {
            "unofficial": True,
            "path": str(self.path),
            "url": self.url,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "filename": self.filename,
            "sha256": self.sha256,
        }


def safe_filename(url: str, content_type: str | None) -> str:
    """A single path segment. Directory pieces in the URL are dropped."""
    raw_name = Path(unquote(urlsplit(url).path)).name
    name = _SAFE.sub("_", raw_name).strip("._")
    if not name or ".." in name or "/" in name or "\\" in name:
        name = "media"
    name = name[:80]
    base_type = (content_type or "").split(";")[0].strip().lower()
    if "." not in name:
        name = f"{name}{_EXT.get(base_type, '')}" or "media"
    return name or "media"


def cookies_from_storage(storage_state: Mapping[str, Any] | None) -> httpx.Cookies:
    """Copy Playwright cookies into an httpx jar. Do not log the jar."""
    jar = httpx.Cookies()
    if not storage_state:
        return jar
    cookies = storage_state.get("cookies")
    if not isinstance(cookies, list):
        return jar
    for item in cookies:
        if not isinstance(item, dict) or not item.get("name") or item.get("value") is None:
            continue
        kwargs: dict[str, str] = {"path": str(item.get("path") or "/")}
        domain = item.get("domain")
        if domain:
            kwargs["domain"] = str(domain)
        jar.set(str(item["name"]), str(item["value"]), **kwargs)
    return jar


async def download_media(
    urls: Iterable[str],
    media_dir: Path,
    *,
    client: httpx.AsyncClient | None = None,
    headers: Mapping[str, str] | None = None,
    storage_state: Mapping[str, Any] | None = None,
    max_bytes: int = 100 * 1024 * 1024,
    allowed_prefixes: tuple[str, ...] = ("image/", "video/", "audio/"),
    timeout: float = 60.0,
) -> list[DownloadResult]:
    """Download each URL into ``media_dir``.

    A non-media content type, a non-http URL, or a body over ``max_bytes``
    raises :class:`DownloadError` and deletes the partial file.
    """
    if max_bytes < 1:
        raise DownloadError("max_bytes must be at least 1")
    root = Path(media_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    own_client = client is None
    http = client or httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=dict(headers or {}),
        cookies=cookies_from_storage(storage_state),
    )
    results: list[DownloadResult] = []
    try:
        for url in urls:
            results.append(
                await _download_one(
                    http,
                    str(url),
                    root,
                    max_bytes=max_bytes,
                    allowed_prefixes=allowed_prefixes,
                )
            )
    finally:
        if own_client:
            await http.aclose()
    return results


async def _download_one(
    client: httpx.AsyncClient,
    url: str,
    root: Path,
    *,
    max_bytes: int,
    allowed_prefixes: tuple[str, ...],
) -> DownloadResult:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise DownloadError("media URL must be http or https")
    async with client.stream("GET", url) as response:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise DownloadError(f"media download failed with HTTP {exc.response.status_code}") from exc
        header = response.headers.get("content-type")
        base_type = header.split(";")[0].strip().lower() if header else ""
        if base_type and allowed_prefixes and not any(base_type.startswith(prefix) for prefix in allowed_prefixes):
            raise DownloadError(f"refusing content type {base_type}")
        filename = safe_filename(url, base_type or None)
        dest = _destination(root, filename)
        hasher = hashlib.sha256()
        size = 0
        try:
            with dest.open("wb") as handle:
                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_bytes:
                        raise DownloadError("download exceeds NABU_DOWNLOAD_MAX_BYTES")
                    hasher.update(chunk)
                    handle.write(chunk)
        except Exception:
            dest.unlink(missing_ok=True)
            raise
    logger.info("downloaded unofficial media host=%s bytes=%s", parts.netloc, size)
    return DownloadResult(
        path=dest,
        url=url,
        content_type=base_type or None,
        size_bytes=size,
        filename=dest.name,
        sha256=hasher.hexdigest(),
    )


def _destination(root: Path, filename: str) -> Path:
    candidate = (root / f"{secrets.token_hex(4)}_{filename}").resolve()
    if not candidate.is_relative_to(root):
        raise DownloadError("download path escaped the media directory")
    return candidate
