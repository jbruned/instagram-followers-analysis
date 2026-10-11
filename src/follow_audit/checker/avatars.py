"""Download and cache profile pictures. Picture URLs are signed and expire, so fetch at once."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .client import IMAGE_HEADERS, TIMEOUT, new_client

log = logging.getLogger(__name__)
MAX_BYTES = 2_000_000
# The page names the picture's address, so only fetch it from Instagram's own image hosts.
IMAGE_HOSTS = (".cdninstagram.com", ".fbcdn.net", ".instagram.com")
MAGIC = (b"\xff\xd8", b"\x89PNG", b"GIF8", b"RIFF")  # jpeg, png, gif, webp


def allowed_url(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and host.endswith(IMAGE_HOSTS)


async def download_avatar(url: str, dest: Path, timeout: float = TIMEOUT) -> bool:
    """Save the image at ``url`` to ``dest`` (no cookies). Returns False on any failure."""
    if not allowed_url(url):
        log.debug("avatar refused: not an Instagram image address")
        return False
    body = bytearray()
    try:
        async with (
            new_client(timeout) as client,
            client.stream("GET", url, headers=IMAGE_HEADERS) as r,
        ):
            ctype = r.headers.get("content-type", "")
            if r.status_code != 200 or not ctype.startswith("image/"):
                log.debug("avatar rejected: %s %s", r.status_code, ctype)
                return False
            if not allowed_url(str(r.url)):  # a redirect must not leave Instagram's hosts
                return False
            async for chunk in r.aiter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    log.debug("avatar rejected: over %d bytes", MAX_BYTES)
                    return False
    except httpx.HTTPError as exc:
        log.debug("avatar download failed: %s", exc)
        return False
    if not body or not bytes(body[:4]).startswith(MAGIC):
        log.debug("avatar rejected: not an image")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(body)
    tmp.replace(dest)
    return True
