"""SQLite schema, versioning and connection helpers.

``SCHEMA`` is the current schema, and ``PRAGMA user_version`` says which version a database is at.
A new database gets ``SCHEMA`` as it is. A change to it also goes in ``MIGRATIONS``, for the
databases that already exist: entry ``i`` upgrades version ``i + 1`` to ``i + 2``. Databases made
by the development versions of this app (they have a ``migrations`` table) are not supported.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

SCHEMA = """
CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY,
    source_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL UNIQUE,
    owner TEXT NOT NULL DEFAULT '',  -- the Instagram account the export belongs to ('': not stated)
    taken_at TEXT NOT NULL,   -- YYYY-MM-DD
    taken_at_source TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    file_list_json TEXT NOT NULL,
    partial_reason TEXT       -- why its followers list is cut short (NULL: complete)
);

-- Who is in each list (followers, following, ...) as intervals of snapshot ids; see
-- ingest/membership.py. last_snapshot NULL = still in the list.
CREATE TABLE membership (
    owner TEXT NOT NULL,
    kind TEXT NOT NULL,
    username_key TEXT NOT NULL,
    username TEXT NOT NULL,
    ts INTEGER,
    full_name TEXT,
    first_snapshot INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    last_snapshot INTEGER REFERENCES snapshots(id) ON DELETE CASCADE,
    PRIMARY KEY (owner, kind, username_key, first_snapshot)
);
CREATE INDEX idx_membership_open ON membership(owner, kind, last_snapshot);

-- The lists each snapshot's export carried (a list it lacks keeps its last known state).
CREATE TABLE snapshot_kinds (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, kind)
);

-- Exports whose snapshot was deleted: a scan skips them, an upload brings them back.
CREATE TABLE deleted_exports (
    content_sha256 TEXT PRIMARY KEY,
    deleted_at TEXT NOT NULL
);

-- What the account checker learned about an Instagram account (shared by every owner).
CREATE TABLE accounts (
    username_key TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unchecked',
    http_status INTEGER,
    full_name TEXT,
    is_private INTEGER,
    is_verified INTEGER,
    ig_user_id TEXT,
    avatar_path TEXT,
    checked_at TEXT,
    next_check_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    unavailable_hits INTEGER NOT NULL DEFAULT 0,
    first_unavailable_at TEXT,
    priority_bump INTEGER NOT NULL DEFAULT 0,
    -- posts_state: NULL never looked at, 'known' read from the page (last_post_at is NULL
    -- when there are no posts), 'hidden' not visible to us (private, or an unreadable page)
    last_post_at TEXT,
    posts_state TEXT,
    followers_count INTEGER,  -- as the profile page shows them (rounded for big accounts)
    following_count INTEGER,
    -- which version of what we read from a profile page this row was fetched with (see
    -- checker/worker.py CACHE_VERSION); a live row from an older version is refetched, after
    -- everything unchecked or expired
    fetch_version INTEGER NOT NULL DEFAULT 0
);

-- What you decided about an account, per Instagram account (owner, as in snapshots).
CREATE TABLE decisions (
    owner TEXT NOT NULL,
    username_key TEXT NOT NULL REFERENCES accounts(username_key) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('ignored', 'snoozed', 'follow', 'unfollow')),
    decided_at TEXT NOT NULL,
    until TEXT,
    PRIMARY KEY (owner, username_key),
    CHECK ((kind = 'snoozed') = (until IS NOT NULL))
);
CREATE INDEX idx_decisions_kind ON decisions(owner, kind);

CREATE TABLE checker_state (key TEXT PRIMARY KEY, value TEXT);

-- Every request made to Instagram, for the daily cap (rows older than a day are deleted).
CREATE TABLE requests (ts REAL NOT NULL);
CREATE INDEX idx_requests_ts ON requests(ts);
CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = []

VERSION = 1 + len(MIGRATIONS)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection) -> None:
    """Create the schema in an empty database, or bring an older one up to date."""
    current = version(conn)
    if current == 0:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name IN ('snapshots', 'migrations')"
        ).fetchone():
            raise RuntimeError(
                "this database was made by a development version; start with a new state folder"
            )
        with conn:
            conn.executescript(SCHEMA)
            conn.execute(f"PRAGMA user_version = {VERSION}")
        current = VERSION
    if current > VERSION:
        raise RuntimeError(f"database is version {current}, newer than this app ({VERSION})")
    if current < VERSION:
        _backup(conn, current)
    for target in range(current + 1, VERSION + 1):
        with conn:
            MIGRATIONS[target - 2](conn)
            conn.execute(f"PRAGMA user_version = {target}")


def _backup(conn: sqlite3.Connection, version_: int) -> None:
    """Copy the database next to itself before it is migrated (once per starting version)."""
    row = conn.execute("PRAGMA database_list").fetchone()
    if not row or not row["file"]:
        return
    dest = Path(row["file"] + f".before-version-{version_ + 1}")
    if dest.exists():
        return
    target = sqlite3.connect(dest)
    try:
        conn.backup(target)
    finally:
        target.close()


def init_db(path: Path) -> None:
    """Open (creating or upgrading as needed) the database at ``path``."""
    conn = connect(path)
    try:
        migrate(conn)
    finally:
        conn.close()
