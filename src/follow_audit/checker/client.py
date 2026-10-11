"""Instagram profile lookup (unauthenticated).

What the probe found (October 2026, see README "How the checker works"):

* Strategy A, ``/api/v1/users/web_profile_info/``: answers ``401`` with
  ``"require_login": true`` ("Please wait a few minutes...") for **every** username, existing or
  not, when logged out. It carries no signal, so it is deliberately not implemented.
* Strategy B, the profile page ``GET /<user>/``: works logged out, but only when the request looks
  like an ordinary browser navigation (``Sec-Fetch-*`` and ``Sec-Ch-Ua`` headers). Without them
  Instagram serves its error page even for accounts that exist.

Signals used by :class:`ProfilePageStrategy`:

====================================  =========================================================
Response                              Outcome
====================================  =========================================================
200, ``"pageID":"profilePage"``       ``live`` (name, picture, flags from ``og:`` tags/JSON)
200, ``"pageID":"httpErrorPage"``     ``missing``. A missing account and a blocked request look
                                      identical, so the worker confirms with a canary request for
                                      a known-live account before it believes this.
401 / 403 / 429, redirect to          ``rate_limited``
``/accounts/login``, "wait a few
minutes" body
anything else                         ``error`` (never guessed as unavailable)
====================================  =========================================================

``missing`` means deactivated, banned, or renamed. They can't be told apart.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

import httpx

Outcome = Literal["live", "missing", "rate_limited", "error"]

# Headers of a normal Chrome navigation. No cookies, and no Accept-Encoding override so httpx
# only advertises encodings it can decode.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
        "image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}
IMAGE_HEADERS = {
    "User-Agent": BROWSER_HEADERS["User-Agent"],
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "image",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "cross-site",
}
CANARY_USERNAME = "instagram"
TIMEOUT = 30.0


@dataclass
class LookupResult:
    outcome: Outcome
    http_status: int | None = None
    detail: str | None = None
    full_name: str | None = None
    is_private: bool | None = None
    is_verified: bool | None = None
    ig_user_id: str | None = None
    picture_url: str | None = None
    # 'known': read from the page (last_post_at is None when the account has no posts);
    # 'hidden': the page doesn't show posts (private account, or a layout we can't read).
    posts_state: Literal["known", "hidden"] = "hidden"
    last_post_at: str | None = None  # ISO 8601, UTC
    # as the page shows them: big accounts are rounded ("27K"), and None means not shown
    followers_count: int | None = None
    following_count: int | None = None


class Strategy(Protocol):
    async def lookup(self, username: str) -> LookupResult: ...


def new_client(timeout: float = TIMEOUT) -> httpx.AsyncClient:
    """A fresh client with an empty cookie jar. Callers must not reuse it between requests."""
    return httpx.AsyncClient(timeout=timeout, follow_redirects=True)


def _unescape_json_string(raw: str) -> str:
    try:
        return json.loads(f'"{raw}"')
    except ValueError:
        return raw.replace("\\/", "/").replace("\\u0026", "&")


def _meta(body: str, prop: str) -> str | None:
    m = re.search(rf'<meta[^>]+(?:property|name)="{re.escape(prop)}"[^>]+content="([^"]*)"', body)
    return html.unescape(m.group(1)) if m else None


def _json_bool(body: str, key: str) -> bool | None:
    m = re.search(rf'"{key}":(true|false)', body)
    return None if not m else m.group(1) == "true"


# Instagram media IDs are snowflake-style: the creation time (ms since this epoch) sits above
# the lowest 23 bits. Checked against the dates in the posts' accessibility captions.
IG_EPOCH_MS = 1314220021721
_POST_RE = re.compile(r'"pk":"(\d{15,20})","code":"[A-Za-z0-9_-]{8,14}"')
_POST_COUNT_RE = re.compile(r"(\d[\d.,]*[KkMm]?)\s+Posts?\b")
_EARLIEST = datetime(2010, 10, 1, tzinfo=UTC)
_UNITS = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
_FOLLOWERS_RE = re.compile(r"(\d[\d.,]*[KkMmBb]?)\s+Followers\b")
_FOLLOWING_RE = re.compile(r"(\d[\d.,]*[KkMmBb]?)\s+Following\b")


def parse_count(text: str) -> int | None:
    """ "2,395" -> 2395, "27K" -> 27000, "1.2M" -> 1200000. None when it isn't clear: a dot
    without a K/M/B (a thousands separator in another language) could mean either."""
    unit = _UNITS.get(text[-1].lower())
    number = text[:-1] if unit else text
    try:
        if unit:
            return round(float(number.replace(",", "")) * unit)
        return int(number.replace(",", "")) if "." not in number else None
    except ValueError:
        return None


def _read_counts(description: str | None) -> tuple[int | None, int | None]:
    """Followers and following from the page's description ("27K Followers, 228 Following, ...")."""
    found = []
    for pattern in (_FOLLOWERS_RE, _FOLLOWING_RE):
        m = pattern.search(description or "")
        found.append(parse_count(m.group(1)) if m else None)
    return found[0], found[1]


def post_time(media_id: int) -> datetime | None:
    """Creation time of a post from its ID, or None if the result isn't plausible."""
    when = datetime.fromtimestamp(((media_id >> 23) + IG_EPOCH_MS) / 1000, UTC)
    return when if _EARLIEST <= when <= datetime.now(UTC) + timedelta(days=1) else None


def _read_posts(
    body: str, is_private: bool | None, description: str | None
) -> tuple[Literal["known", "hidden"], str | None]:
    """The newest post date from the posts embedded in a profile page.

    The page lists the latest posts plus pinned ones, so the maximum is the last post.
    Private accounts show no posts, and a page that says "0 Posts" has none to show.
    """
    if is_private:
        return "hidden", None
    times = [t for m in _POST_RE.finditer(body) if (t := post_time(int(m.group(1))))]
    if times:
        return "known", max(times).isoformat(timespec="seconds")
    count = _POST_COUNT_RE.search(description or "")
    if count and count.group(1) == "0":
        return "known", None
    return "hidden", None


def parse_profile_page(username: str, status: int, final_url: str, body: str) -> LookupResult:
    """Classify a profile page response. Pure function, easy to test."""
    if status != 200:
        # Profiles that exist and ones that don't both answer 200 (a missing one renders an error
        # page), so anything else means we are being pushed back or Instagram is struggling.
        # The check is taken as failed: the checker pauses, and the account is left as it was.
        return LookupResult("rate_limited", status, f"HTTP {status}")
    if "/accounts/login" in final_url:
        return LookupResult("rate_limited", status, "redirected to login")
    if re.search(r"please wait a few minutes", body[:5000], re.I):
        return LookupResult("rate_limited", status, "asked to wait")

    page_id = re.search(r'"pageID":"([A-Za-z]+)"', body)
    page = page_id.group(1) if page_id else None
    if page == "httpErrorPage":
        return LookupResult("missing", status, "error page")
    if page != "profilePage":
        return LookupResult("error", status, f"unrecognised page ({page or 'no pageID'})")

    og_title = _meta(body, "og:title") or ""
    name = None
    full = re.search(r'"full_name":"((?:[^"\\]|\\.)*)"', body)
    if full:
        name = _unescape_json_string(full.group(1)) or None
    if not name and "(@" in og_title:
        name = og_title.split(" (@", 1)[0].strip() or None

    pic = re.search(r'"profile_pic_url":"((?:[^"\\]|\\.)*)"', body)
    picture = _unescape_json_string(pic.group(1)) if pic else _meta(body, "og:image")

    user_id = None
    for m in re.finditer(rf'"username":"{re.escape(username)}"', body, re.I):
        near = body[max(0, m.start() - 600) : m.end() + 600]
        idm = re.search(r'"(?:id|pk)":"(\d{5,})"', near)
        if idm:
            user_id = idm.group(1)
            break

    is_private = _json_bool(body, "is_private")
    description = _meta(body, "og:description")
    posts_state, last_post = _read_posts(body, is_private, description)
    followers, following = _read_counts(description)
    return LookupResult(
        "live",
        status,
        full_name=name,
        is_private=is_private,
        is_verified=_json_bool(body, "is_verified"),
        ig_user_id=user_id,
        picture_url=picture,
        posts_state=posts_state,
        last_post_at=last_post,
        followers_count=followers,
        following_count=following,
    )


class ProfilePageStrategy:
    """Strategy B: ``GET https://www.instagram.com/<user>/`` with browser navigation headers."""

    timeout: float = TIMEOUT  # the worker updates this from the settings before each request

    async def lookup(self, username: str) -> LookupResult:
        url = f"https://www.instagram.com/{username}/"
        try:
            async with new_client(self.timeout) as client:
                r = await client.get(url, headers=BROWSER_HEADERS)
        except httpx.HTTPError as exc:
            return LookupResult("error", None, f"{type(exc).__name__}: {exc}")
        return parse_profile_page(username, r.status_code, str(r.url), r.text)
