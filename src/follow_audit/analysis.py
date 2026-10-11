"""Set comparisons, snapshot diffs and the timeline. Pure reads over the database."""

from __future__ import annotations

import csv
import io
import re
import sqlite3
import unicodedata
from collections.abc import Container
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cached_property

from .ingest import membership

LIST_NAMES = (
    "not_following_back",  # pending review: nothing decided about them yet
    "not_following_back_ignored",
    "not_following_back_flagged",
    "fans",
    "fans_ignored",
    "fans_flagged",
    "mutual",
    "mutual_close_friends",
    "pending_sent",
    "pending_received",
    "pending_unavailable",
    "unfollowed",
    "unfollowed_unavailable",
)
UNAVAILABLE_STATUSES = ("unavailable", "possibly_unavailable")
# Lists whose accounts can be unavailable accounts.
UNAVAILABLE_SOURCES = (
    "not_following_back",
    "fans",
    "mutual",
    "pending_sent",
    "pending_received",
    "recently_unfollowed",
    "close_friends",
)
CONTEXT = {
    "not_following_back": "Followed",
    "fans": "Followed you",
    "mutual": "Followed",
    "pending_sent": "Requested",
    "pending_received": "Asked to follow you",
    "recently_unfollowed": "Unfollowed",
    "close_friends": "Added to close friends",
}
CSV_FIELDS = [
    "username",
    "full_name",
    "date",
    "status",
    "private",
    "verified",
    "ignored",
    "last_post",
    "flag",
]


class NotFound(Exception):
    pass


@dataclass
class Item:
    username: str
    ts: int | None
    context: str
    full_name: str | None = None


# ---------------------------------------------------------------- owners / snapshots


def owners(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT owner, COUNT(*) AS n, MAX(taken_at) AS latest FROM snapshots"
        " GROUP BY owner ORDER BY latest DESC, MAX(id) DESC"
    ).fetchall()
    return [{"owner": r["owner"], "label": r["owner"] or None, "snapshots": r["n"]} for r in rows]


def snapshots(conn: sqlite3.Connection, owner: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM snapshots WHERE owner = ? ORDER BY taken_at DESC, id DESC", (owner,)
    ).fetchall()
    return [
        {
            "id": r["id"],
            "owner": r["owner"],
            "taken_at": r["taken_at"],
            "partial": r["partial_reason"],
            "ingested_at": r["ingested_at"],
        }
        for r in rows
    ]


def resolve(conn: sqlite3.Connection, owner: str | None, snapshot: int | None) -> sqlite3.Row:
    """Pick the snapshot row for an owner (default: owner of the newest snapshot)."""
    if snapshot is not None:
        row = conn.execute("SELECT * FROM snapshots WHERE id=?", (snapshot,)).fetchone()
        if not row:
            raise NotFound("snapshot not found")
        if owner is not None and row["owner"] != owner:
            raise NotFound("snapshot belongs to a different account")
        return row
    if owner is None:
        row = conn.execute(
            "SELECT * FROM snapshots ORDER BY taken_at DESC, id DESC LIMIT 1"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM snapshots WHERE owner = ? ORDER BY taken_at DESC, id DESC LIMIT 1",
            (owner,),
        ).fetchone()
    if not row:
        raise NotFound("no snapshots found")
    return row


# ---------------------------------------------------------------- set helpers


def load_kind(conn: sqlite3.Connection, sid: int, kind: str) -> dict[str, sqlite3.Row]:
    rows = membership.read(conn, owner_of(conn, sid), kind, sid)
    return {r["username_key"]: r for r in rows}


def kinds_present(conn: sqlite3.Connection, sid: int) -> list[str]:
    return membership.kinds_seen(conn, owner_of(conn, sid), sid)


def _chunks(seq: list, n: int = 500):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def account_info(conn: sqlite3.Connection, keys: list[str]) -> dict[str, sqlite3.Row]:
    out: dict[str, sqlite3.Row] = {}
    for chunk in _chunks(keys):
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(f"SELECT * FROM accounts WHERE username_key IN ({marks})", chunk):
            out[r["username_key"]] = r
    return out


def owner_of(conn: sqlite3.Connection, sid: int) -> str:
    """The Instagram account a snapshot belongs to ('' when the export doesn't say)."""
    row = conn.execute("SELECT owner FROM snapshots WHERE id=?", (sid,)).fetchone()
    return row["owner"] if row else ""


def decisions(conn: sqlite3.Connection, owner: str) -> dict[str, tuple[str, str | None]]:
    """What you decided about each account for one Instagram account: username_key ->
    (state, detail).

    state is 'flagged' (detail: 'follow' or 'unfollow'), 'snoozed' (detail: when it ends) or
    'ignored'. There is one decision per account. Expired snoozes simply stop counting.
    """
    now = datetime.now(UTC).isoformat(timespec="seconds")
    out: dict[str, tuple[str, str | None]] = {}
    for r in conn.execute(
        "SELECT username_key, kind, until FROM decisions WHERE owner = ?", (owner,)
    ):
        if r["kind"] in ("follow", "unfollow"):
            out[r["username_key"]] = ("flagged", r["kind"])
        elif r["kind"] == "snoozed":
            if r["until"] > now:
                out[r["username_key"]] = ("snoozed", r["until"])
        else:
            out[r["username_key"]] = ("ignored", None)
    return out


def decision_times(conn: sqlite3.Connection, owner: str) -> dict[str, str]:
    """When each decision was made: username_key -> time."""
    return {
        r["username_key"]: r["decided_at"]
        for r in conn.execute(
            "SELECT username_key, decided_at FROM decisions WHERE owner = ?", (owner,)
        )
    }


def unavailable_keys(conn: sqlite3.Connection) -> set[str]:
    """Accounts the checker could not find (confirmed or still only possibly unavailable)."""
    marks = ",".join("?" * len(UNAVAILABLE_STATUSES))
    return {
        r["username_key"]
        for r in conn.execute(
            f"SELECT username_key FROM accounts WHERE status IN ({marks})", UNAVAILABLE_STATUSES
        )
    }


def flag_done(kind: str, in_following: bool) -> bool:
    """Has a flag been carried out? Unfollow is done once the account is no longer in the list
    of accounts you follow; follow is done once it is."""
    return (not in_following) if kind == "unfollow" else in_following


class View:
    """One snapshot's lists, read once and shared by everything built for a request.

    Building a page of rows, the tab counts or an account's window all start from the same few
    lists; without this each step read them again."""

    def __init__(self, conn: sqlite3.Connection, sid: int) -> None:
        self.conn, self.sid = conn, sid
        self.owner = owner_of(conn, sid)
        self._kinds: dict[str, dict[str, sqlite3.Row]] = {}
        self._raw: dict[str, dict[str, Item]] = {}
        self._base: dict[str, dict[str, Item]] = {}
        self._unfollowed: tuple[dict, dict] | None = None

    def kind(self, name: str) -> dict[str, sqlite3.Row]:
        """Everyone in one list (followers, following, ...) as of this snapshot."""
        if name not in self._kinds:
            rows = membership.read(self.conn, self.owner, name, self.sid)
            self._kinds[name] = {r["username_key"]: r for r in rows}
        return self._kinds[name]

    def member(self, name: str, key: str) -> sqlite3.Row | None:
        """One account's entry in a list, without loading the whole list unless it is loaded."""
        if name in self._kinds:
            return self._kinds[name].get(key)
        return self.conn.execute(
            "SELECT username_key, username, ts, full_name FROM membership"
            " WHERE owner=? AND kind=? AND username_key=? AND first_snapshot<=?"
            " AND (last_snapshot IS NULL OR last_snapshot>=?)",
            (self.owner, name, key, self.sid, self.sid),
        ).fetchone()

    @cached_property
    def gone(self) -> set[str]:
        return unavailable_keys(self.conn)

    @cached_property
    def decided(self) -> dict[str, tuple[str, str | None]]:
        return decisions(self.conn, self.owner)

    @cached_property
    def flagged_at(self) -> dict[str, str]:
        return decision_times(self.conn, self.owner)

    def base(self, name: str) -> dict[str, Item]:
        if name not in self._base:
            self._base[name] = _base_items(self, name)
        return self._base[name]


class _Probe:
    """``key in probe``: asks the database about one account, for a window on a single account."""

    def __init__(self, view: View, kind: str) -> None:
        self.view, self.kind = view, kind

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and self.view.member(self.kind, key) is not None


def _flagged_items(view: View, kind: str) -> dict[str, Item]:
    """Every account flagged to follow or unfollow, including ones that have since left the
    export (an account you unfollowed is no longer in your following list)."""
    source = view.kind("following" if kind == "unfollow" else "followers")
    context = CONTEXT["not_following_back"] if kind == "unfollow" else CONTEXT["fans"]
    out: dict[str, Item] = {}
    for r in view.conn.execute(
        "SELECT d.username_key AS k, a.username AS username, a.full_name AS full_name"
        " FROM decisions d LEFT JOIN accounts a USING(username_key)"
        " WHERE d.owner = ? AND d.kind = ?",
        (view.owner, kind),
    ):
        seen = source.get(r["k"])
        out[r["k"]] = Item(
            r["username"] or (seen["username"] if seen else r["k"]),
            seen["ts"] if seen else None,
            context,
            r["full_name"] or (seen["full_name"] if seen else None),
        )
    return out


def _epoch(iso: str | None) -> int | None:
    try:
        return int(datetime.fromisoformat(iso).timestamp()) if iso else None
    except ValueError:
        return None


def _unfollowed_parts(view: View) -> tuple[dict, dict]:
    """(unfollowed with this app, recently unfollowed). The first are accounts you flagged to
    unfollow that a newer export no longer lists among the accounts you follow; their date is
    the unfollow date when Instagram's export gives one, else the day you flagged them. The
    second are the unfollows Instagram reports that are not in the first (no account is in
    both)."""
    if view._unfollowed is None:
        following = view.kind("following")
        reported = {
            k: Item(r["username"], r["ts"], CONTEXT["recently_unfollowed"], r["full_name"])
            for k, r in view.kind("recently_unfollowed").items()
        }
        app: dict[str, Item] = {}
        for k, item in _flagged_items(view, "unfollow").items():
            if k in following:
                continue  # still to do: it lives in the Not following you back tab
            known = reported.get(k)
            if known and known.ts:
                ts, context = known.ts, CONTEXT["recently_unfollowed"]
            else:
                ts, context = _epoch(view.flagged_at.get(k)), "Flagged"
            app[k] = Item(item.username, ts, context, item.full_name or (known and known.full_name))
        view._unfollowed = (app, {k: v for k, v in reported.items() if k not in app})
    return view._unfollowed


def base_items(conn: sqlite3.Connection, sid: int, name: str) -> dict[str, Item]:
    """Membership for a list (a tab or one of its sub-tabs)."""
    return View(conn, sid).base(name)


def _base_items(view: View, name: str) -> dict[str, Item]:
    """Not following you back and You don't follow back split into pending review (nothing
    decided), ignored (for good or snoozed) and flagged. Unavailable accounts are left out of
    every list except the two made for them: under Unfollowed, and for requests under Requests
    sent.
    """
    if name not in LIST_NAMES:
        raise NotFound(f"unknown list: {name}")
    gone, decided = view.gone, view.decided

    def live(items: dict[str, Item]) -> dict[str, Item]:
        return {k: v for k, v in items.items() if k not in gone}

    for tab, flag in (("not_following_back", "unfollow"), ("fans", "follow")):
        if name == tab:
            return {k: v for k, v in live(_raw_items(view, tab)).items() if k not in decided}
        if name in (f"{tab}_ignored", f"{tab}_flagged"):
            if name.endswith("_ignored"):

                def wanted(state: str, detail: str | None) -> bool:
                    return state in ("ignored", "snoozed")
            else:

                def wanted(state: str, detail: str | None, flag: str = flag) -> bool:
                    return state == "flagged" and detail == flag

            return {
                k: v
                for k, v in live(_raw_items(view, tab)).items()
                if k in decided and wanted(*decided[k])
            }
    if name == "mutual":
        return live(_raw_items(view, "mutual"))
    if name == "mutual_close_friends":
        close = view.kind("close_friends").keys()
        return {k: v for k, v in live(_raw_items(view, "mutual")).items() if k in close}
    if name in ("pending_sent", "pending_received"):
        return live(_raw_items(view, name))
    if name == "pending_unavailable":  # requests in either direction
        requests = {**_raw_items(view, "pending_received"), **_raw_items(view, "pending_sent")}
        return {k: v for k, v in requests.items() if k in gone}
    app, recent = _unfollowed_parts(view)
    if name == "unfollowed":
        return live({**recent, **app})
    # unfollowed_unavailable: every unavailable account that is not a pending request
    pending = view.kind("pending_sent").keys() | view.kind("pending_received").keys()
    out = {k: v for k, v in _raw_items(view, "unavailable").items() if k not in pending}
    out.update({k: v for k, v in {**recent, **app}.items() if k in gone})
    return out


def _raw_items(view: View, name: str) -> dict[str, Item]:
    if name not in view._raw:
        view._raw[name] = _compute_raw(view, name)
    return view._raw[name]


def _compute_raw(view: View, name: str) -> dict[str, Item]:
    def items(source: dict[str, sqlite3.Row], keys, ctx: str) -> dict[str, Item]:
        return {
            k: Item(source[k]["username"], source[k]["ts"], ctx, source[k]["full_name"])
            for k in keys
        }

    if name in ("not_following_back", "fans", "mutual"):
        following, followers = view.kind("following"), view.kind("followers")
        if name == "not_following_back":
            return items(following, following.keys() - followers.keys(), CONTEXT[name])
        if name == "fans":
            return items(followers, followers.keys() - following.keys(), CONTEXT[name])
        return items(following, following.keys() & followers.keys(), CONTEXT[name])
    if name in ("pending_sent", "pending_received", "recently_unfollowed", "close_friends"):
        data = view.kind(name)
        return items(data, data.keys(), CONTEXT[name])
    if name == "unavailable":
        merged: dict[str, Item] = {}
        for src in UNAVAILABLE_SOURCES:
            for k, v in _raw_items(view, src).items():
                merged.setdefault(k, v)
        gone = view.gone
        return {k: v for k, v in merged.items() if k in gone}
    raise NotFound(f"unknown list: {name}")


def _avatar_url(info: sqlite3.Row | None) -> str | None:
    if info and info["avatar_path"]:
        return f"/api/avatars/{info['username_key']}?v={info['checked_at'] or ''}"
    return None


# Accounts with fewer followers than this are never marked as having a high follower ratio
MIN_FOLLOWERS_FOR_RATIO = 1000


def high_ratio(followers: int | None, following: int | None, ratio: float | None) -> bool:
    """Many more people follow the account than it follows: a public figure, a brand, a creator."""
    if not ratio or followers is None or following is None:
        return False
    return followers >= MIN_FOLLOWERS_FOR_RATIO and followers > ratio * max(following, 1)


@dataclass
class RowContext:
    """What turning an account into a row needs, loaded once per request."""

    owner: str
    decided: dict
    flagged_at: dict
    following_keys: Container
    follower_keys: Container
    close: Container
    inactive_days: int | None
    high_ratio: float | None
    now: datetime


def row_context(
    view: View,
    *,
    inactive_days: int | None = None,
    high_ratio_at: float | None = None,
    single: bool = False,
) -> RowContext:
    """``single``: the rows are for one account, so ask about it instead of loading whole lists."""

    def lists(kind: str) -> Container:
        return _Probe(view, kind) if single else view.kind(kind).keys()

    return RowContext(
        owner=view.owner,
        decided=view.decided,
        flagged_at=view.flagged_at,
        following_keys=lists("following"),
        follower_keys=lists("followers"),
        close=lists("close_friends"),
        inactive_days=inactive_days,
        high_ratio=high_ratio_at,
        now=datetime.now(UTC),
    )


def make_row(key: str, item: Item, a: sqlite3.Row | None, ctx: RowContext) -> dict:
    state, detail = ctx.decided.get(key, (None, None))
    full_name = (a["full_name"] if a and a["full_name"] else None) or item.full_name
    last_post = a["last_post_at"] if a else None
    posts_state = a["posts_state"] if a else None
    followers = a["followers_count"] if a else None
    following = a["following_count"] if a else None
    inactive = bool(
        last_post
        and ctx.inactive_days
        and (ctx.now - datetime.fromisoformat(last_post)).days >= ctx.inactive_days
    )
    return {
        "username": item.username,
        "key": key,
        "ts": item.ts,
        "context": item.context,
        "status": a["status"] if a else "unchecked",
        "checked_at": a["checked_at"] if a else None,
        "first_unavailable_at": a["first_unavailable_at"] if a else None,
        "full_name": full_name,
        "is_private": None if not a or a["is_private"] is None else bool(a["is_private"]),
        "is_verified": None if not a or a["is_verified"] is None else bool(a["is_verified"]),
        "last_post_at": last_post,
        "no_posts": posts_state == "known" and last_post is None,
        "inactive": inactive,
        "followers_count": followers,
        "following_count": following,
        "high_ratio": high_ratio(followers, following, ctx.high_ratio),
        "ignored": state == "ignored",
        "flag": detail if state == "flagged" else None,
        # "pending": still to do; "done": your following list no longer shows it (unfollow)
        # or now shows it (follow). flagged_at is when you flagged it.
        "flag_state": (
            ("done" if flag_done(detail, key in ctx.following_keys) else "pending")
            if state == "flagged"
            else None
        ),
        "flagged_at": ctx.flagged_at.get(key) if state == "flagged" else None,
        # the flag this account can be given: unfollow if you follow it, else follow back
        "flag_action": "unfollow"
        if key in ctx.following_keys
        else ("follow" if key in ctx.follower_keys else None),
        "snoozed_until": detail if state == "snoozed" else None,
        "close_friend": key in ctx.close,
        "avatar_url": _avatar_url(a),
    }


def build_rows(
    conn: sqlite3.Connection,
    sid: int,
    name: str,
    *,
    q: str | None = None,
    sort: str = "newest",
    inactive_days: int | None = None,
    high_ratio_at: float | None = None,
    view: View | None = None,
) -> list[dict]:
    view = view or View(conn, sid)
    base = view.base(name)
    ctx = row_context(view, inactive_days=inactive_days, high_ratio_at=high_ratio_at)
    info = account_info(conn, list(base))
    needle = (q or "").strip().lower().lstrip("@")
    rows = []
    for key, item in base.items():
        a = info.get(key)
        if needle:
            full_name = (a["full_name"] if a and a["full_name"] else None) or item.full_name
            if needle not in key and needle not in (full_name or "").lower():
                continue
        rows.append(make_row(key, item, a, ctx))
    if sort == "az":
        rows.sort(key=lambda r: r["key"])
    elif sort in ("post_new", "post_old"):
        newest_first = sort == "post_new"

        def post_order(r: dict) -> tuple:
            # Accounts with a date come first for "newest"; for "oldest" the ones with no posts
            # at all lead, since they are the most inactive. Unknown dates always go last.
            if r["last_post_at"]:
                stamp = datetime.fromisoformat(r["last_post_at"]).timestamp()
                return (0 if newest_first else 1, -stamp if newest_first else stamp, r["key"])
            if r["no_posts"]:
                return (1 if newest_first else 0, 0, r["key"])
            return (2, 0, r["key"])

        rows.sort(key=post_order)
    else:
        have = [r for r in rows if r["ts"] is not None]
        none = sorted((r for r in rows if r["ts"] is None), key=lambda r: r["key"])
        have.sort(key=lambda r: (r["ts"], r["key"]), reverse=(sort != "oldest"))
        rows = have + none
    return rows


def rows_to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_FIELDS)
    for r in rows:
        date = datetime.fromtimestamp(r["ts"], UTC).strftime("%Y-%m-%d") if r["ts"] else ""
        w.writerow(
            [
                _csv_safe(r["username"]),
                _csv_safe(r["full_name"] or ""),
                date,
                r["status"],
                "" if r["is_private"] is None else str(r["is_private"]).lower(),
                "" if r["is_verified"] is None else str(r["is_verified"]).lower(),
                str(r.get("ignored", False)).lower(),
                (r.get("last_post_at") or "")[:10],
                r.get("flag") or "",
            ]
        )
    return buf.getvalue()


def _csv_safe(value: str) -> str:
    # Neutralise spreadsheet formula injection from user-controlled names.
    # spreadsheets also read formulas after leading blanks and in their full-width forms
    stripped = value.lstrip(" \t\r\n\u00a0")
    first = unicodedata.normalize("NFKC", stripped[:1])
    return "'" + value if first in ("=", "+", "-", "@") or value[:1] in ("\t", "\r") else value


# ---------------------------------------------------------------- summary


def _partial_since(reason: str | None) -> str | None:
    """The date a partial followers list starts at: the first date in the reason it was flagged
    with (None when it names none, e.g. an empty list)."""
    found = re.search(r"\d{4}-\d{2}-\d{2}", reason or "")
    return found.group(0) if found else None


def _previous_date(conn: sqlite3.Connection, snap: sqlite3.Row) -> str | None:
    row = conn.execute(
        "SELECT taken_at FROM snapshots WHERE owner = ? AND id < ? ORDER BY id DESC LIMIT 1",
        (snap["owner"], snap["id"]),
    ).fetchone()
    return row["taken_at"] if row else None


def summary(conn: sqlite3.Connection, snap: sqlite3.Row) -> dict:
    sid = snap["id"]
    view = View(conn, sid)
    present = kinds_present(conn, sid)
    counts = {name: len(view.base(name)) for name in LIST_NAMES}
    # Totals describe the accounts that still exist: unavailable ones are counted only in the
    # Unavailable tab. Snoozed accounts stay in the totals; tab counts leave them out.
    following_keys = view.kind("following").keys() - view.gone
    followers_keys = view.kind("followers").keys() - view.gone
    n_following, n_followers = len(following_keys), len(followers_keys)
    totals = {
        "mutual": len(following_keys & followers_keys),
        "not_following_back": len(following_keys - followers_keys),
        "fans": len(followers_keys - following_keys),
    }
    nfb = totals["not_following_back"]
    if "following" not in present:
        headline = "This snapshot has no list of accounts you follow."
    elif n_following == 0:
        headline = "You don't follow anyone in this snapshot."
    else:
        verb = "doesn't" if nfb == 1 else "don't"
        headline = f"{nfb} of the {n_following} accounts you follow {verb} follow you back."
    return {
        "snapshot": {
            "id": sid,
            "owner": snap["owner"],
            "taken_at": snap["taken_at"],
            "partial": snap["partial_reason"],
            "partial_since": _partial_since(snap["partial_reason"]),
            "previous_taken_at": _previous_date(conn, snap),
        },
        "headline": headline,
        "counts": counts,
        "totals": totals,
        "following": n_following,
        "followers": n_followers,
        "kinds_present": present,
        "snapshot_count": conn.execute(
            "SELECT COUNT(*) FROM snapshots WHERE owner = ?", (snap["owner"],)
        ).fetchone()[0],
    }


# ---------------------------------------------------------------- one account

STATES = ("mutual", "fan", "following", "none")  # fan: follows you, you don't follow back


def relation_timeline(conn: sqlite3.Connection, owner: str, key: str) -> dict:
    """How you and one account were related over the snapshots of one Instagram account.

    ``segments`` run from each snapshot's date to the next one's, with the state at that
    snapshot ("mutual", "fan", "following" or "none"). ``events`` are the changes between two
    snapshots, with the exact time when the export gives one. ``dates`` are all the snapshots'."""
    snaps = conn.execute(
        "SELECT id, taken_at FROM snapshots WHERE owner = ? ORDER BY id", (owner,)
    ).fetchall()
    stretches = conn.execute(
        "SELECT kind, ts, first_snapshot, last_snapshot FROM membership"
        " WHERE owner = ? AND username_key = ? AND kind IN ('followers', 'following')",
        (owner, key),
    ).fetchall()

    def present(kind: str, sid: int) -> sqlite3.Row | None:
        for r in stretches:
            if r["kind"] == kind and r["first_snapshot"] <= sid:
                if r["last_snapshot"] is None or r["last_snapshot"] >= sid:
                    return r
        return None

    points = []
    for snap in snaps:
        follower, followed = present("followers", snap["id"]), present("following", snap["id"])
        state = {(True, True): "mutual", (True, False): "fan", (False, True): "following"}.get(
            (follower is not None, followed is not None), "none"
        )
        points.append(
            {
                "date": snap["taken_at"],
                "state": state,
                "follower_ts": follower["ts"] if follower else None,
                "following_ts": followed["ts"] if followed else None,
            }
        )
    segments: list[dict] = []
    for i, p in enumerate(points):
        end = points[i + 1]["date"] if i + 1 < len(points) else p["date"]
        last = segments[-1] if segments else None
        if last and last["state"] == p["state"]:
            last["to"] = end
        else:
            segments.append({"state": p["state"], "from": p["date"], "to": end})
    follows = {"follower": ("mutual", "fan"), "following": ("mutual", "following")}
    events = []
    for before, after in zip(points, points[1:], strict=False):
        for who, on, off, ts_key in (
            ("follower", "they_followed", "they_unfollowed", "follower_ts"),
            ("following", "you_followed", "you_unfollowed", "following_ts"),
        ):
            was, now = before["state"] in follows[who], after["state"] in follows[who]
            if was != now:
                ts = after[ts_key] if now else None
                events.append(
                    {
                        "kind": on if now else off,
                        "from": before["date"],
                        "to": after["date"],
                        "ts": ts,
                    }
                )
    return {"segments": segments, "events": events, "dates": [p["date"] for p in points]}


def profile(
    conn: sqlite3.Connection,
    snap: sqlite3.Row,
    username: str,
    *,
    inactive_days: int | None = None,
    high_ratio_at: float | None = None,
) -> dict | None:
    """Everything known about one account, as a row plus how you are related to it."""
    key = username.lower()
    view = View(conn, snap["id"])
    ctx = row_context(view, inactive_days=inactive_days, high_ratio_at=high_ratio_at, single=True)
    entries = {
        kind: view.member(kind, key)
        for kind in (
            "following",
            "followers",
            "pending_sent",
            "pending_received",
            "recently_unfollowed",
        )
    }
    a = account_info(conn, [key]).get(key)
    item = None
    for kind, context in (("following", CONTEXT["mutual"]), ("followers", CONTEXT["fans"])):
        r = entries[kind]
        if r is not None:
            item = Item(r["username"], r["ts"], context, r["full_name"])
            break
    if item is None and a is None:
        return None
    if item is None:
        item = Item(a["username"], None, "", None)
    row = make_row(key, item, a, ctx)

    def when(kind: str) -> int | None:
        return entries[kind]["ts"] if entries[kind] else None

    row["relation"] = {
        "following": entries["following"] is not None,
        "follows_you": entries["followers"] is not None,
        "pending_sent": entries["pending_sent"] is not None,
        "pending_received": entries["pending_received"] is not None,
        # when the export says it began (a unix time); None when not following or unknown
        "follows_you_since": when("followers"),
        "following_since": when("following"),
        # when you unfollowed them, if your export lists it among the recent unfollows
        "you_unfollowed_at": when("recently_unfollowed"),
    }
    row["timeline"] = relation_timeline(conn, ctx.owner, key)
    row["incomplete"] = account_incomplete(a)
    return row


def account_incomplete(a: sqlite3.Row | None) -> bool:
    """Is there something about this account the checker could still fetch? Accounts it found
    to be unavailable are as complete as they will get."""
    if a is None or a["status"] in UNAVAILABLE_STATUSES:
        return False
    return (
        a["status"] in ("unchecked", "error")
        or not a["avatar_path"]
        or a["followers_count"] is None
    )


# ---------------------------------------------------------------- history


def _event(row: sqlite3.Row, **extra) -> dict:
    return {"username": row["username"], "key": row["username_key"], "ts": row["ts"], **extra}


def diff_snapshots(conn: sqlite3.Connection, old: sqlite3.Row, new: sqlite3.Row) -> dict:
    o_followers, n_followers = (
        load_kind(conn, old["id"], "followers"),
        load_kind(conn, new["id"], "followers"),
    )
    o_following, n_following = (
        load_kind(conn, old["id"], "following"),
        load_kind(conn, new["id"], "following"),
    )
    have_followers = bool(o_followers) and bool(n_followers)
    have_following = bool(o_following) and bool(n_following)

    events: dict[str, list[dict]] = {
        k: []
        for k in (
            "lost_follower",
            "new_follower",
            "you_unfollowed",
            "you_followed",
            "refollow_follower",
            "refollow_following",
            "renamed",
        )
    }
    if have_followers:
        for k in o_followers.keys() - n_followers.keys():
            events["lost_follower"].append(_event(o_followers[k]))
        for k in n_followers.keys() - o_followers.keys():
            events["new_follower"].append(_event(n_followers[k]))
        for k in o_followers.keys() & n_followers.keys():
            a, b = o_followers[k]["ts"], n_followers[k]["ts"]
            if a and b and a != b:
                events["refollow_follower"].append(_event(n_followers[k], previous_ts=a))
    if have_following:
        for k in o_following.keys() - n_following.keys():
            events["you_unfollowed"].append(_event(o_following[k]))
        for k in n_following.keys() - o_following.keys():
            events["you_followed"].append(_event(n_following[k]))
        for k in o_following.keys() & n_following.keys():
            a, b = o_following[k]["ts"], n_following[k]["ts"]
            if a and b and a != b:
                events["refollow_following"].append(_event(n_following[k], previous_ts=a))

    # Renames: a lost and a new account that share the same ig_user_id.
    lost_keys = [e["key"] for e in events["lost_follower"]]
    new_keys = [e["key"] for e in events["new_follower"]]
    ids = {
        k: r["ig_user_id"]
        for k, r in account_info(conn, lost_keys + new_keys).items()
        if r["ig_user_id"]
    }
    new_by_id = {ids[e["key"]]: e for e in events["new_follower"] if e["key"] in ids}
    renamed_lost: set[str] = set()
    renamed_new: set[str] = set()
    for e in events["lost_follower"]:
        uid = ids.get(e["key"])
        if uid and uid in new_by_id:
            n = new_by_id[uid]
            events["renamed"].append(
                {
                    "username": n["username"],
                    "key": n["key"],
                    "ts": n["ts"],
                    "previous_username": e["username"],
                }
            )
            renamed_lost.add(e["key"])
            renamed_new.add(n["key"])
    events["lost_follower"] = [e for e in events["lost_follower"] if e["key"] not in renamed_lost]
    events["new_follower"] = [e for e in events["new_follower"] if e["key"] not in renamed_new]

    # Cross-reference lost followers with the checker.
    info = account_info(conn, [e["key"] for e in events["lost_follower"]])
    for e in events["lost_follower"]:
        a = info.get(e["key"])
        status = a["status"] if a else "unchecked"
        e["status"] = status
        e["label"] = "account unavailable" if status in UNAVAILABLE_STATUSES else "unfollowed you"
        if status == "possibly_unavailable":
            e["label"] = "possibly unavailable"

    for lst in events.values():
        lst.sort(key=lambda e: e["key"])
    return events


def timeline(conn: sqlite3.Connection, owner: str) -> list[dict]:
    snaps = conn.execute(
        "SELECT * FROM snapshots WHERE owner = ? ORDER BY taken_at ASC, id ASC", (owner,)
    ).fetchall()
    out = []
    for old, new in zip(snaps, snaps[1:], strict=False):
        events = diff_snapshots(conn, old, new)
        out.append(
            {
                "from_snapshot": old["id"],
                "to_snapshot": new["id"],
                "from_date": old["taken_at"],
                "to_date": new["taken_at"],
                "partial": new["partial_reason"],
                "counts": {k: len(v) for k, v in events.items()},
                "events": events,
            }
        )
    out.reverse()
    return out
