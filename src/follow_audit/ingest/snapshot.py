"""Snapshot metadata detection and idempotent import."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import membership
from .discover import PERSONAL_INFO_NAME, Export, discover
from .parse import Entry, parse_file, valid_username

log = logging.getLogger(__name__)

# An import reads the lists as they stand and then writes the changes. Two at once (an upload and
# the folder watcher, or two uploads) could both read the same state, so they take turns.
IMPORT_LOCK = threading.RLock()

NAME_RE = re.compile(r"instagram-(?P<user>[A-Za-z0-9._]+?)-(?P<date>\d{4}-\d{2}-\d{2})(?:-|$)")


@dataclass
class ScanSummary:
    found: int = 0
    imported: list[dict] = field(default_factory=list)
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    # exports held back because their followers look partial; importing them needs a go-ahead
    partial: list[dict] = field(default_factory=list)
    # uploads that say nothing about whose they are: someone has to pick the account
    needs_owner: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "found": self.found,
            "imported": self.imported,
            "skipped": self.skipped,
            "errors": self.errors,
            "partial": self.partial,
            "needs_owner": self.needs_owner,
        }


class NeedsOwner(ValueError):
    """Neither the name nor the contents say which Instagram account this export belongs to."""


class PartialExport(ValueError):
    """The followers list looks cut to a date range, so it cannot show who unfollowed you.

    ``first`` is True when there was no earlier list to compare with (the judgement rests on
    ``following`` alone); otherwise ``previous`` is the date of the snapshot before it."""

    def __init__(self, reason: str, first: bool, previous: str | None):
        super().__init__(f"partial export: {reason}")
        self.reason, self.first, self.previous = reason, first, previous


def parse_name(names: list[str]) -> tuple[str | None, str | None]:
    """(owner, YYYY-MM-DD) from the first zip/folder name carrying the standard pattern."""
    for name in names:
        m = NAME_RE.search(name)
        if m:
            try:
                datetime.strptime(m.group("date"), "%Y-%m-%d")
            except ValueError:
                continue
            return m.group("user"), m.group("date")
    return None, None


def owner_from_personal_info(files: dict[str, bytes]) -> str | None:
    for path, data in files.items():
        if path.rsplit("/", 1)[-1] != PERSONAL_INFO_NAME:
            continue
        try:
            doc = json.loads(data.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError):
            continue
        found = _find_username(doc)
        if found:
            return found
    return None


def _find_username(node: object) -> str | None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "Username" and isinstance(v, dict) and valid_username(v.get("value")):
                return v["value"]
            r = _find_username(v)
            if r:
                return r
    elif isinstance(node, list):
        for v in node:
            r = _find_username(v)
            if r:
                return r
    return None


def content_hash(files: dict[str, bytes]) -> str:
    """SHA-256 over relationship files only. Independent of container (zip vs folder)."""
    digests = sorted(
        (path.rsplit("/", 1)[-1], hashlib.sha256(data).hexdigest())
        for path, data in files.items()
        if path.rsplit("/", 1)[-1] != PERSONAL_INFO_NAME
    )
    h = hashlib.sha256()
    for name, digest in digests:
        h.update(f"{name}:{digest}\n".encode())
    return h.hexdigest()


def detect_taken_at(
    names: list[str], parsed: dict[str, list[Entry]], mtime: float
) -> tuple[str, str]:
    _, date = parse_name(names)
    if date:
        return date, "name"
    stamps = [e.ts for entries in parsed.values() for e in entries if e.ts]
    if stamps:
        return datetime.fromtimestamp(max(stamps), UTC).strftime("%Y-%m-%d"), "latest_entry"
    return datetime.fromtimestamp(mtime, UTC).strftime("%Y-%m-%d"), "file_mtime"


def import_export(
    conn: sqlite3.Connection,
    export: Export,
    default_owner: str | None = None,
    *,
    uploaded: bool = False,
    force: bool = False,
    keep: Callable[[str, str], Path] | None = None,
) -> dict | None:
    """Import one export. Returns a summary dict, or None if it was already imported.

    ``keep(owner, taken_at)`` stores a copy of the export and returns where it went. It runs as
    the last step of the import, so if it fails nothing is imported."""
    with IMPORT_LOCK:
        return _import_export(conn, export, default_owner, uploaded, force, keep)


def _import_export(conn, export, default_owner, uploaded, force, keep) -> dict | None:
    files = dict(export.read_files())
    rel_files = {p: d for p, d in files.items() if p.rsplit("/", 1)[-1] != PERSONAL_INFO_NAME}
    if not rel_files:
        raise ValueError("no follower/following files found")
    sha = content_hash(files)
    if conn.execute("SELECT 1 FROM snapshots WHERE content_sha256=?", (sha,)).fetchone():
        return None
    if conn.execute("SELECT 1 FROM deleted_exports WHERE content_sha256=?", (sha,)).fetchone():
        if not uploaded:
            return None  # deleted on purpose: a scan must not bring it back
        with conn:
            conn.execute("DELETE FROM deleted_exports WHERE content_sha256=?", (sha,))

    parsed: dict[str, dict[str, Entry]] = {}
    errors: list[str] = []
    for path, data in sorted(rel_files.items()):
        try:
            result = parse_file(path, data)
        except (ValueError, UnicodeDecodeError) as exc:
            errors.append(f"{path}: {exc}")
            continue
        for kind, entries in result.items():
            bucket = parsed.setdefault(kind, {})
            for e in entries:
                prev = bucket.get(e.key)
                if prev is None or (e.ts or 0) > (prev.ts or 0):
                    bucket[e.key] = e
    if not parsed and errors:
        raise ValueError("; ".join(errors))

    flat = {k: list(v.values()) for k, v in parsed.items()}
    names = export.top_level_names()
    owner, _ = parse_name(names)
    owner = owner or owner_from_personal_info(files) or default_owner or ""
    if uploaded and not owner:
        raise NeedsOwner("it does not say which account it belongs to")
    # Instagram names are not case sensitive: "Alice" and "alice" are one account
    owner = owner.lower()
    taken_at, source = detect_taken_at(names, flat, export.mtime())
    if uploaded and source != "name":
        # files sent just now with no date of their own describe the account as of today
        taken_at, source = datetime.now(UTC).strftime("%Y-%m-%d"), "upload"

    latest = membership.latest_snapshot(conn, owner)
    if latest and taken_at < latest["taken_at"]:
        raise ValueError(
            f"it is dated {taken_at}, older than the newest export already imported "
            f"({latest['taken_at']}); import exports in date order"
        )
    partial = None
    if "followers" in flat:
        known = {k: r["ts"] for k, r in membership.open_members(conn, owner, "followers").items()}
        following = (
            {e.key: e.ts for e in flat["following"]}
            if "following" in flat
            else {k: r["ts"] for k, r in membership.open_members(conn, owner, "following").items()}
        )
        partial, first = membership.judge(
            {e.key: e.ts for e in flat["followers"]}, following, known
        )
        if partial and not force:
            raise PartialExport(partial, first, None if first or not latest else latest["taken_at"])

    with conn:
        cur = conn.execute(
            "INSERT INTO snapshots(source_path, content_sha256, owner, taken_at, taken_at_source,"
            " ingested_at, file_list_json, partial_reason) VALUES (?,?,?,?,?,?,?,?)",
            (
                str(export.path),
                sha,
                owner,
                taken_at,
                source,
                datetime.now(UTC).isoformat(timespec="seconds"),
                json.dumps(sorted(Path(p).name for p in rel_files)),
                partial,
            ),
        )
        sid = cur.lastrowid
        previous = latest["id"] if latest else None
        for kind, entries in flat.items():
            members = [(e.key, e.username, e.ts, e.full_name) for e in entries]
            # a cut followers list can only add people: it cannot say who left
            only_add = partial is not None and kind == "followers"
            membership.apply(conn, owner, kind, sid, previous, members, only_add=only_add)
            conn.execute("INSERT INTO snapshot_kinds VALUES (?, ?)", (sid, kind))
        if keep is not None:
            kept = keep(owner, taken_at)
            conn.execute("UPDATE snapshots SET source_path=? WHERE id=?", (str(kept), sid))
            source_name = kept.name
        else:
            source_name = export.name
        # Make sure every username has an accounts row for the checker.
        conn.executemany(
            "INSERT OR IGNORE INTO accounts(username_key, username) VALUES (?,?)",
            [(e.key, e.username) for entries in flat.values() for e in entries],
        )
    return {
        "snapshot_id": sid,
        "source": source_name,
        "owner": owner,
        "taken_at": taken_at,
        "taken_at_source": source,
        "counts": {k: len(v) for k, v in flat.items()},
        "warnings": errors,
        "partial": partial,
    }


def _import_order(export: Export) -> tuple[str, str]:
    try:
        date = parse_name(export.top_level_names())[1]
    except Exception:  # unreadable: let the import report it
        date = None
    return date or "9999", export.name


def scan(conn: sqlite3.Connection, data_dir: Path) -> ScanSummary:
    summary = ScanSummary()
    # oldest first: an export older than one already imported is refused
    exports = sorted(discover(data_dir), key=_import_order)
    for export in exports:
        summary.found += 1
        try:
            result = import_export(conn, export)
        except PartialExport as exc:
            # only a person can decide to keep one, so a scan leaves it for an upload
            summary.errors.append(
                f"{export.name}: not imported, {exc.reason}. Upload it in the app to import it"
                " anyway"
            )
            continue
        except Exception as exc:  # one bad export must not block the others
            log.warning("could not import %s: %s", export.name, exc)
            summary.errors.append(f"{export.name}: {exc}")
            continue
        if result is None:
            summary.skipped += 1
        else:
            summary.imported.append(result)
    return summary
