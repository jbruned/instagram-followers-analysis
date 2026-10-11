"""Regenerates the synthetic fixture exports. Never put real exports here.

Run: uv run python tests/fixtures/build.py
"""

import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent / "exports"
T0 = 1709380000  # 2024-03-02
T1 = 1760000000  # 2025-10-09


def sld(name, ts, *, href=True, value=True, underscore=False):
    entry = {"timestamp": ts}
    if href:
        entry["href"] = f"https://www.instagram.com/{'_u/' if underscore else ''}{name}"
    if value:
        entry["value"] = name
    return {"title": "", "media_list_data": [], "string_list_data": [entry]}


def title_only(name, ts):
    return {
        "title": name,
        "string_list_data": [{"href": f"https://www.instagram.com/_u/{name}", "timestamp": ts}],
    }


def label_item(name, ts, full):
    return {
        "timestamp": ts,
        "media": [],
        "fbid": "1",
        "label_values": [
            {"label": "URL", "value": ""},
            {"label": "Name", "value": full},
            {"label": "Username", "value": name},
        ],
    }


def snapshot_files(*, followers, following, pending, unfollowed, close):
    half = len(followers) // 2 + 1
    return {
        "followers_1.json": [sld(n, t) for n, t in followers[:half]],
        "followers_2.json": [sld(n, t, underscore=True, value=False) for n, t in followers[half:]],
        "following.json": {"relationships_following": [title_only(n, t) for n, t in following]},
        "pending_follow_requests.json": [label_item(n, t, n.title()) for n, t in pending],
        "recently_unfollowed_profiles.json": {
            "relationships_unfollowed_users": [sld(n, t) for n, t in unfollowed]
        },
        "close_friends.json": [label_item(n, t, n.title()) for n, t in close],
        "blocked_profiles.json": label_item("spammer", T0, "Spam"),
        "custom_lists.json": [{"timestamp": 1, "label_values": [{"label": "Name", "value": "x"}]}],
    }


A = snapshot_files(
    followers=[("bob", T0), ("carol", T0), ("dave", T0), ("erin", T0), ("frank", T0)],
    following=[("bob", T0), ("carol", T0), ("gina", T0), ("hank", T0)],
    pending=[("ivan", T0)],
    unfollowed=[("old_friend", T0)],
    close=[("bob", T0)],
)
B = snapshot_files(
    followers=[("bob", T0), ("carol", T1), ("erin", T0), ("zoe", T1)],
    following=[("bob", T0), ("carol", T1), ("hank", T0), ("newguy", T1)],
    pending=[("ivan", T0), ("judy", T1)],
    unfollowed=[("old_friend", T0), ("gina", T1)],
    close=[("bob", T0)],
)


def write_dir(base: Path, files: dict, sub="connections/followers_and_following"):
    d = base / sub
    d.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (d / name).write_text(json.dumps(content), encoding="utf-8")


def html_item(name, stamp):
    return (
        f'<div class="pam"><div><div>{name}</div><div><a target="_blank" '
        f'href="https://www.instagram.com/{name}">https://www.instagram.com/{name}</a></div>'
        f"<div>{stamp}</div></div></div>"
    )


def main():
    if ROOT.exists():
        shutil.rmtree(ROOT)
    # 1. current JSON zip (extra top-level folder, as in real exports)
    zdir = ROOT / "json_zip"
    zdir.mkdir(parents=True)
    with zipfile.ZipFile(zdir / "instagram-alice-2024-03-02-AAAA.zip", "w") as zf:
        for name, content in A.items():
            zf.writestr(
                f"instagram-alice-2024-03-02-AAAA/connections/followers_and_following/{name}",
                json.dumps(content),
            )
    # 2. unzipped folder with extra top-level folder, later snapshot
    write_dir(ROOT / "json_folder" / "instagram-alice-2026-10-09-BBBB", B)
    # 3. HTML export, owner unknown, dates only in text
    h = ROOT / "html_export" / "followers_and_following"
    h.mkdir(parents=True)
    page = "<html><body>{}<a href='https://www.instagram.com/accounts/login'>x</a></body></html>"
    (h / "followers_1.html").write_text(
        page.format(
            html_item("htmluser1", "Jun 01, 2024 2:21 pm")
            + html_item("htmluser2", "Jun 02, 2024 11:05 am")
        )
    )
    (h / "following.html").write_text(page.format(html_item("htmluser1", "Jun 03, 2024 1:00 pm")))
    # 4. 2021 format
    write_dir(
        ROOT / "legacy2021",
        {
            "followers.json": {
                "relationships_followers": [sld("old1", 1620000000), sld("old2", 1621000000)]
            },
            "following.json": {"relationships_following": [sld("old1", 1620000000)]},
        },
        sub="followers_and_following",
    )
    # 5. 2020 connections.json
    (ROOT / "legacy2020").mkdir(parents=True)
    (ROOT / "legacy2020" / "connections.json").write_text(
        json.dumps(
            {
                "followers": {"l1": "2019-05-01T12:00:00+00:00", "l2": "2019-06-01T12:00:00+00:00"},
                "following": {"l1": "2019-05-02T12:00:00+00:00"},
                "follow_requests_sent": {"l3": "2019-07-01T12:00:00+00:00"},
                "close_friends": {"l1": "2019-08-01T12:00:00+00:00"},
                "blocked_users": {},
                "following_hashtags": {"tag": "2019-01-01T00:00:00+00:00"},
            }
        )
    )


if __name__ == "__main__":
    main()
