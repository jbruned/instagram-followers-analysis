"""Manual probe of Instagram's logged-out behaviour. Not part of the app.

Usage:
    uv run python scripts/probe_instagram.py instagram nonexistent_name_93847562 deactivated_one

For every username it tries each candidate strategy once, waiting 30 seconds between requests,
and prints what came back so the live / unavailable / rate-limited signals can be chosen from
evidence. No cookies are sent or kept: each request uses a fresh client.

Total time is about 60 seconds per username. Don't run it in a loop: if Instagram starts
answering 429 or redirecting to a login page, stop and wait a while.
"""

import argparse
import asyncio
import re

import httpx

PAUSE_SECONDS = 30
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
INTERESTING_HEADERS = (
    "content-type",
    "location",
    "retry-after",
    "x-ig-origin-region",
    "x-fb-debug",
    "server",
    "x-content-type-options",
    "cache-control",
    "set-cookie",
    "www-authenticate",
)


def strategies(username: str) -> list[tuple[str, str, dict[str, str]]]:
    return [
        (
            "A web_profile_info",
            f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}",
            {"X-IG-App-ID": "936619743392459", "User-Agent": USER_AGENT, "Accept": "*/*"},
        ),
        (
            "B profile page",
            f"https://www.instagram.com/{username}/",
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        ),
    ]


def page_signals(body: str) -> list[str]:
    out = []
    title = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
    if title:
        out.append(f"title: {title.group(1).strip()[:120]!r}")
    for prop in ("og:title", "og:description", "og:image", "og:type"):
        m = re.search(rf'<meta[^>]+property="{prop}"[^>]+content="([^"]*)"', body, re.I)
        if m:
            out.append(f"{prop}: {m.group(1)[:120]!r}")
    for phrase in (
        "isn't available",
        "Page Not Found",
        "Sorry, this page",
        "Please wait a few minutes",
        "login",
        "challenge",
    ):
        if phrase.lower() in body.lower():
            out.append(f"body contains: {phrase!r}")
    return out


async def probe(username: str, label: str, url: str, headers: dict[str, str]) -> None:
    print(f"\n=== {username} :: {label}\nGET {url}")
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30, cookies=None) as client:
            r = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        print(f"request failed: {type(exc).__name__}: {exc}")
        return
    print(f"status: {r.status_code}")
    print(f"final url: {r.url}")
    if r.history:
        print(
            "redirects: "
            + " -> ".join(f"{h.status_code} {h.headers.get('location', '')}" for h in r.history)
        )
    for name in INTERESTING_HEADERS:
        if name in r.headers:
            value = r.headers[name]
            print(f"header {name}: {value[:160]}")
    body = r.text
    print(f"body length: {len(body)}")
    print(f"body[:300]: {body[:300]!r}")
    if label.startswith("B"):
        for line in page_signals(body):
            print(f"signal: {line}")
    if label.startswith("A") and r.headers.get("content-type", "").startswith("application/json"):
        try:
            data = r.json()
            user = (data.get("data") or {}).get("user") if isinstance(data, dict) else None
            print(f"json keys: {sorted(data) if isinstance(data, dict) else type(data).__name__}")
            print(f"data.user present: {user is not None}")
            if user:
                print(
                    "user fields: "
                    + ", ".join(
                        k
                        for k in (
                            "id",
                            "full_name",
                            "is_private",
                            "is_verified",
                            "profile_pic_url",
                            "profile_pic_url_hd",
                        )
                        if k in user
                    )
                )
        except ValueError:
            print("body is not valid JSON")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("usernames", nargs="+")
    args = parser.parse_args()
    first = True
    for username in args.usernames:
        for label, url, headers in strategies(username):
            if not first:
                print(f"\n(waiting {PAUSE_SECONDS}s)")
                await asyncio.sleep(PAUSE_SECONDS)
            first = False
            await probe(username, label, url, headers)


if __name__ == "__main__":
    asyncio.run(main())
