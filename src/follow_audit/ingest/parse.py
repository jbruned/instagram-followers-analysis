"""Parsers for Instagram export files (JSON, HTML and legacy ``connections.json``).

Every parser returns a list of :class:`Entry` per relationship ``kind``. Nothing here touches
the disk or the database, so it is easy to test with in-memory bytes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import PurePosixPath

USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
HREF_RE = re.compile(r"instagram\.com/(?:_u/)?([A-Za-z0-9._]+)", re.IGNORECASE)
RESERVED_PATHS = {
    "accounts",
    "explore",
    "p",
    "reel",
    "reels",
    "stories",
    "direct",
    "about",
    "legal",
}

# JSON top-level key -> kind. Checked before the file name.
KEY_TO_KIND = {
    "relationships_following": "following",
    "relationships_followers": "followers",
    "relationships_follow_requests_sent": "pending_sent",
    "relationships_follow_requests_received": "pending_received",
    "relationships_permanent_follow_requests": "recent_requests",
    "relationships_unfollowed_users": "recently_unfollowed",
    "relationships_close_friends": "close_friends",
    "relationships_blocked_users": "blocked",
    "relationships_restricted_users": "restricted",
    "relationships_hide_stories_from": "hide_story_from",
    "relationships_dismissed_suggested_users": "removed_suggestions",
    "relationships_feed_favorites": "favorites",
}

# File stem (lowercase, apostrophes removed, trailing _<n> removed) -> kind.
STEM_TO_KIND = {
    "followers": "followers",
    "following": "following",
    "pending_follow_requests": "pending_sent",
    "follow_requests_youve_received": "pending_received",
    "recent_follow_requests": "recent_requests",
    "recently_unfollowed_profiles": "recently_unfollowed",
    "recently_unfollowed_accounts": "recently_unfollowed",
    "close_friends": "close_friends",
    "blocked_profiles": "blocked",
    "restricted_profiles": "restricted",
    "hide_story_from": "hide_story_from",
    "removed_suggestions": "removed_suggestions",
    "profiles_youve_favorited": "favorites",
}

# 2020 connections.json keys -> kind.
LEGACY_TO_KIND = {
    "followers": "followers",
    "following": "following",
    "following_hashtags": None,
    "follow_requests_sent": "pending_sent",
    "follow_requests_received": "pending_received",
    "permanent_follow_requests": "recent_requests",
    "dismissed_suggested_users": "removed_suggestions",
    "close_friends": "close_friends",
    "blocked_users": "blocked",
    "restricted_users": "restricted",
    "hide_stories_from": "hide_story_from",
    "unfollowed_users": "recently_unfollowed",
    "feed_favorites": "favorites",
}

DATE_FORMATS = ("%b %d, %Y %I:%M %p", "%b %d, %Y, %I:%M %p", "%B %d, %Y %I:%M %p")
DATE_RE = re.compile(r"[A-Za-z]{3,9} \d{1,2}, \d{4},? \d{1,2}:\d{2} ?[AaPp][Mm]")


@dataclass(frozen=True)
class Entry:
    username: str
    ts: int | None = None
    full_name: str | None = None

    @property
    def key(self) -> str:
        return self.username.lower()


def valid_username(name: str | None) -> bool:
    return bool(name) and USERNAME_RE.match(name) is not None


def username_from_href(href: str | None) -> str | None:
    if not href:
        return None
    m = HREF_RE.search(href)
    if not m:
        return None
    name = m.group(1)
    if name.lower() in RESERVED_PATHS:
        return None
    return name


def _norm_filename(path: str) -> tuple[str, str]:
    """Return (stem normalised for lookup, lowercase extension)."""
    p = PurePosixPath(path.replace("\\", "/"))
    stem = p.stem.lower().replace("'", "").replace("’", "")
    stem = re.sub(r"_\d+$", "", stem)
    return stem, p.suffix.lower()


def kind_from_filename(path: str) -> str | None:
    stem, ext = _norm_filename(path)
    if ext not in (".json", ".html"):
        return None
    return STEM_TO_KIND.get(stem)


def is_legacy_connections(path: str) -> bool:
    return PurePosixPath(path.replace("\\", "/")).name.lower() == "connections.json"


def is_relevant(path: str) -> bool:
    return kind_from_filename(path) is not None or is_legacy_connections(path)


def _iso_to_ts(value: str) -> int | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def _clean_ts(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value) if value > 0 else None


def repair_text(text: str) -> str:
    """Undo Instagram's habit of writing UTF-8 bytes as if they were Latin-1 characters in its
    JSON exports ("MartÃ\u00adn" for "Martín"). Text that is already right is returned as it was."""
    if text.isascii():
        return text
    try:
        fixed = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text  # not that pattern (it has real non-Latin-1 characters, or isn't valid UTF-8)
    return fixed


def _entry_from_item(item: object) -> Entry | None:
    """Turn one list item of any known JSON shape into an Entry."""
    if not isinstance(item, dict):
        return None
    name: str | None = None
    full_name: str | None = None
    ts: int | None = None
    sld = item.get("string_list_data")
    if isinstance(sld, list) and sld and isinstance(sld[0], dict):
        first = sld[0]
        ts = _clean_ts(first.get("timestamp"))
        candidates = [first.get("value"), username_from_href(first.get("href")), item.get("title")]
        name = next((c for c in candidates if isinstance(c, str) and valid_username(c)), None)
    elif isinstance(item.get("label_values"), list):
        ts = _clean_ts(item.get("timestamp"))
        labels = {
            lv.get("label"): lv.get("value") for lv in item["label_values"] if isinstance(lv, dict)
        }
        url_name = username_from_href(
            labels.get("URL") if isinstance(labels.get("URL"), str) else None
        )
        candidates = [labels.get("Username"), url_name]
        name = next((c for c in candidates if isinstance(c, str) and valid_username(c)), None)
        if isinstance(labels.get("Name"), str) and labels["Name"]:
            full_name = repair_text(labels["Name"])
    else:
        # title-only entries (no string_list_data)
        title = item.get("title")
        if isinstance(title, str) and valid_username(title):
            name = title
    if name is None:
        return None
    return Entry(name, ts, full_name)


def _entries_from_list(items: object) -> list[Entry]:
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        e = _entry_from_item(item)
        if e:
            out.append(e)
    return out


def parse_json(path: str, data: bytes) -> dict[str, list[Entry]]:
    doc = json.loads(data.decode("utf-8-sig"))
    result: dict[str, list[Entry]] = {}
    file_kind = kind_from_filename(path)

    if isinstance(doc, dict):
        rel_keys = [k for k in doc if k.startswith("relationships_")]
        if rel_keys:
            for key in rel_keys:
                kind = KEY_TO_KIND.get(key, key)
                result.setdefault(kind, []).extend(_entries_from_list(doc[key]))
            return result
        if "label_values" in doc:  # single-object file, e.g. blocked_profiles.json
            if file_kind:
                result[file_kind] = _entries_from_list(doc)
            return result
        return result

    if file_kind:
        result[file_kind] = _entries_from_list(doc)
    return result


def parse_legacy_connections(data: bytes) -> dict[str, list[Entry]]:
    doc = json.loads(data.decode("utf-8-sig"))
    result: dict[str, list[Entry]] = {}
    if not isinstance(doc, dict):
        return result
    for key, value in doc.items():
        if key not in LEGACY_TO_KIND:
            continue
        kind = LEGACY_TO_KIND[key]
        if kind is None or not isinstance(value, dict):
            continue
        entries = []
        for username, stamp in value.items():
            if valid_username(username):
                ts = _iso_to_ts(stamp) if isinstance(stamp, str) else None
                entries.append(Entry(username, ts))
        result.setdefault(kind, []).extend(entries)
    return result


def parse_date_text(text: str) -> int | None:
    text = re.sub(r"[\u202f\u00a0\s]+", " ", text).strip()
    for fmt in DATE_FORMATS:
        try:
            return int(datetime.strptime(text, fmt).replace(tzinfo=UTC).timestamp())
        except ValueError:
            continue
    return None


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.entries: list[list] = []  # [username, ts]
        self._in_a = False
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            name = username_from_href(href)
            if name and valid_username(name):
                self._in_a = True
                self.entries.append([name, None])

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._in_a = False

    def handle_data(self, data: str) -> None:
        if self._in_a or not self.entries or self.entries[-1][1] is not None:
            return
        m = DATE_RE.search(re.sub(r"[  \s]+", " ", data))
        if m:
            self.entries[-1][1] = parse_date_text(m.group(0))


def parse_html(path: str, data: bytes) -> dict[str, list[Entry]]:
    kind = kind_from_filename(path)
    if not kind:
        return {}
    collector = _LinkCollector()
    collector.feed(data.decode("utf-8", errors="replace"))
    seen: dict[str, Entry] = {}
    for name, ts in collector.entries:
        seen.setdefault(name.lower(), Entry(name, ts))
    return {kind: list(seen.values())}


def parse_file(path: str, data: bytes) -> dict[str, list[Entry]]:
    """Parse one archive member. Raises on malformed content; callers decide how to report."""
    if is_legacy_connections(path):
        return parse_legacy_connections(data)
    _, ext = _norm_filename(path)
    if ext == ".html":
        return parse_html(path, data)
    if ext == ".json":
        return parse_json(path, data)
    return {}
