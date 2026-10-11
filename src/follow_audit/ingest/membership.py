"""Who is in each list (followers, following, ...), stored as intervals, not a copy per snapshot.

A row says "this account was in this list from snapshot ``first_snapshot`` to ``last_snapshot``"
(NULL while it still is). Importing a snapshot only writes what changed against the lists as they
stand, so a day with ten changes costs ten rows. Reading any snapshot is one indexed query; nothing
is replayed. Snapshots of one account are ordered by id (the order they were imported in).

A list missing from an export is left as it was. A list that is present is complete: whoever is
open and not in it is closed. That is only true for followers when the export covers all time, which
:func:`partial_reason` checks before anything is written.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime

# Fewer known followers than this is too little to tell a partial list from a real change.
MIN_EVIDENCE = 20
# A complete list still holds nearly everyone who followed before its first date.
KEPT_SHARE = 0.5

# The first follower is this much newer than the first followed account: the list was cut.
FIRST_GAP = 365 * 86400

# (username_key, username, ts, full_name)
Member = tuple[str, str, int | None, str | None]


def read(conn: sqlite3.Connection, owner: str, kind: str, sid: int) -> list[sqlite3.Row]:
    """The members of one list as of snapshot ``sid``."""
    return conn.execute(
        "SELECT username_key, username, ts, full_name FROM membership"
        " WHERE owner=? AND kind=? AND first_snapshot<=?"
        " AND (last_snapshot IS NULL OR last_snapshot>=?)",
        (owner, kind, sid, sid),
    ).fetchall()


def kinds_seen(conn: sqlite3.Connection, owner: str, sid: int) -> list[str]:
    """Lists that any export up to ``sid`` carried."""
    return [
        r["kind"]
        for r in conn.execute(
            "SELECT DISTINCT k.kind FROM snapshot_kinds k JOIN snapshots s ON s.id = k.snapshot_id"
            " WHERE COALESCE(s.owner, '') = ? AND k.snapshot_id <= ?",
            (owner, sid),
        )
    ]


def open_members(conn: sqlite3.Connection, owner: str, kind: str) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        "SELECT username_key, username, ts, full_name FROM membership"
        " WHERE owner=? AND kind=? AND last_snapshot IS NULL",
        (owner, kind),
    )
    return {r["username_key"]: r for r in rows}


def latest_snapshot(conn: sqlite3.Connection, owner: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, taken_at FROM snapshots WHERE owner = ? ORDER BY id DESC LIMIT 1",
        (owner,),
    ).fetchone()


def partial_reason(new: dict[str, int | None], known: dict[str, int | None]) -> str | None:
    """Why a followers list cannot be the complete list, or None when it can.

    ``new`` and ``known`` map username_key -> follow time. A list cut to a date range only holds
    people who followed inside it, so it misses nearly everyone who followed earlier. A complete
    list keeps most of them (a few leaving is normal)."""
    if len(known) < MIN_EVIDENCE:
        return None
    if not new:
        return f"it lists no followers although {len(known)} are known"
    stamps = [t for t in new.values() if t]
    if not stamps:
        return None  # no dates to judge by
    start = min(stamps)
    older = [k for k, t in known.items() if t and t < start]
    if len(older) < MIN_EVIDENCE:
        return None
    missing = [k for k in older if k not in new]
    if len(older) - len(missing) >= KEPT_SHARE * len(older):
        return None
    return (
        f"its followers only go back to {_day(start)}, and {len(missing)} of the {len(older)} "
        "known from before that are missing"
    )


def first_import_reason(new: dict[str, int | None], following: dict[str, int | None]) -> str | None:
    """Like :func:`partial_reason` when there is no earlier list to compare with.

    ``following`` is always complete, and it reaches back as far as you have followed anyone. If
    the first follower is much newer than the first followed account, the followers were most
    likely cut to a date range. Someone could really have gone years before their first follower,
    which is why this only warns."""
    if len(following) < MIN_EVIDENCE:
        return None
    if not new:
        return f"it lists no followers although you follow {len(following)} accounts"
    first_follower = min((t for t in new.values() if t), default=None)
    first_following = min((t for t in following.values() if t), default=None)
    if first_follower is None or first_following is None:
        return None
    if first_follower - first_following <= FIRST_GAP:
        return None
    return (
        f"its first follower is from {_day(first_follower)}, but you have been following "
        f"accounts since {_day(first_following)}"
    )


def judge(
    new: dict[str, int | None],
    following: dict[str, int | None],
    known: dict[str, int | None],
) -> tuple[str | None, bool]:
    """(why the followers list looks partial or None, whether it was judged without an earlier
    list). The earlier list is used when it holds enough followers, else ``following``."""
    if len(known) >= MIN_EVIDENCE:
        return partial_reason(new, known), False
    return first_import_reason(new, following), True


def _day(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")


def apply(
    conn: sqlite3.Connection,
    owner: str,
    kind: str,
    sid: int,
    previous: int | None,
    members: Iterable[Member],
    *,
    only_add: bool = False,
) -> None:
    """Make the list match ``members`` as of snapshot ``sid``.

    ``previous`` is the snapshot before it (None for the first). With ``only_add`` nobody is
    closed: for a list known to be cut short."""
    current = open_members(conn, owner, kind)
    seen: set[str] = set()
    for key, username, ts, full_name in members:
        seen.add(key)
        row = current.get(key)
        if row is not None and ts and row["ts"] and ts != row["ts"] and previous is not None:
            # followed again between two snapshots: a new stretch, not the same one
            _close(conn, owner, kind, key, previous)
            row = None
        if row is None:
            conn.execute(
                "INSERT INTO membership(owner, kind, username_key, username, ts, full_name,"
                " first_snapshot) VALUES (?,?,?,?,?,?,?)",
                (owner, kind, key, username, ts, full_name, sid),
            )
        elif (row["username"], row["full_name"]) != (username, full_name):
            conn.execute(
                "UPDATE membership SET username=?, full_name=? WHERE owner=? AND kind=?"
                " AND username_key=? AND last_snapshot IS NULL",
                (username, full_name, owner, kind, key),
            )
    if only_add or previous is None:
        return
    for key in current.keys() - seen:
        _close(conn, owner, kind, key, previous)


def _close(conn: sqlite3.Connection, owner: str, kind: str, key: str, last: int) -> None:
    conn.execute(
        "UPDATE membership SET last_snapshot=? WHERE owner=? AND kind=? AND username_key=?"
        " AND last_snapshot IS NULL",
        (last, owner, kind, key),
    )


def remove_snapshot(conn: sqlite3.Connection, sid: int) -> bool:
    """Delete a snapshot and mend the intervals that touch it.

    What the snapshot added is dropped (or, if it lasted past it, now starts at the next
    snapshot); a stretch that ended at it now ends at the one before. Everything else is as it
    was, so every other snapshot still reads the same. The export is remembered as deleted so a
    scan does not import it again. Returns False when there is no such snapshot."""
    snap = conn.execute("SELECT owner, content_sha256 FROM snapshots WHERE id=?", (sid,)).fetchone()
    if snap is None:
        return False
    owner = snap["owner"]
    where = "owner = ?"
    prev = conn.execute(
        f"SELECT MAX(id) FROM snapshots WHERE {where} AND id < ?", (owner, sid)
    ).fetchone()[0]
    nxt = conn.execute(
        f"SELECT MIN(id) FROM snapshots WHERE {where} AND id > ?", (owner, sid)
    ).fetchone()[0]
    if nxt is None:
        conn.execute("DELETE FROM membership WHERE owner=? AND first_snapshot=?", (owner, sid))
        if prev is not None:
            # whoever it saw leave is back in the list as of the snapshot before it
            conn.execute(
                "UPDATE membership SET last_snapshot=NULL WHERE owner=? AND last_snapshot=?",
                (owner, prev),
            )
    else:
        # lists that were carried through it keep their place in the next snapshot
        conn.execute(
            "INSERT OR IGNORE INTO snapshot_kinds SELECT ?, kind FROM snapshot_kinds"
            " WHERE snapshot_id=?",
            (nxt, sid),
        )
        conn.execute(
            "DELETE FROM membership WHERE owner=? AND first_snapshot=? AND last_snapshot < ?",
            (owner, sid, nxt),
        )
        conn.execute(
            "UPDATE membership SET first_snapshot=? WHERE owner=? AND first_snapshot=?",
            (nxt, owner, sid),
        )
    if prev is not None and nxt is not None:
        conn.execute(
            "UPDATE membership SET last_snapshot=? WHERE owner=? AND last_snapshot=?",
            (prev, owner, sid),
        )
    conn.execute(
        "INSERT OR REPLACE INTO deleted_exports VALUES (?, ?)",
        (snap["content_sha256"], datetime.now(UTC).isoformat(timespec="seconds")),
    )
    conn.execute("DELETE FROM snapshots WHERE id=?", (sid,))
    return True
