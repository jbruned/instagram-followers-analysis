"""Background account checker: one request at a time, with cooldowns and a daily cap."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime

from .. import db, runtime_settings
from ..config import Settings
from ..ingest import membership
from .avatars import download_avatar
from .client import CANARY_USERNAME, LookupResult, ProfilePageStrategy, Strategy

log = logging.getLogger(__name__)

ERROR_RETRY_BASE = 3600
ERROR_RETRY_MAX = 24 * 3600
DAY = 86400
AVATAR_REFRESH_DAYS = 30
# Bump this when the checker starts reading something new from a profile page. Live accounts
# fetched with an older version are fetched again, but only after everything unchecked or due.
CACHE_VERSION = 1
MAX_PENDING_BUMPS = 50
MAX_VISIBLE_BATCH = 25
TIERS = {
    1: "not_following_back",
    2: "requests",
    3: "fans",
    4: "mutual",
}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds")


def _parse_iso(value: str | None) -> float | None:
    return datetime.fromisoformat(value).timestamp() if value else None


class CheckerWorker:
    def __init__(
        self,
        settings: Settings,
        strategy: Strategy | None = None,
        *,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], asyncio.Future] | None = None,
    ) -> None:
        self.base_settings = settings  # environment defaults
        self.settings = settings  # defaults plus anything saved from the settings dialog
        self.strategy = strategy or ProfilePageStrategy()
        self.clock = clock
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()  # one request to Instagram at a time
        self._sleeper = sleeper
        self.current: str | None = None

    def refresh_settings(self, conn: sqlite3.Connection) -> None:
        """Pick up changes made in the settings dialog. Called at the start of every step."""
        self.settings = runtime_settings.effective(self.base_settings, conn)
        if hasattr(self.strategy, "timeout"):
            self.strategy.timeout = self.settings.request_timeout_seconds

    # ------------------------------------------------------------ persisted state

    def _conn(self) -> sqlite3.Connection:
        return db.connect(self.base_settings.db_path)

    @staticmethod
    def _get(conn: sqlite3.Connection, key: str, default=None):
        row = conn.execute("SELECT value FROM checker_state WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    @staticmethod
    def _set(conn: sqlite3.Connection, key: str, value) -> None:
        with conn:
            conn.execute(
                "INSERT INTO checker_state(key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    def _requests_24h(self, conn: sqlite3.Connection) -> int:
        return conn.execute(
            "SELECT COUNT(*) FROM requests WHERE ts > ?", (self.clock() - DAY,)
        ).fetchone()[0]

    def _record_request(self, conn: sqlite3.Connection) -> None:
        now = self.clock()
        with conn:
            conn.execute("DELETE FROM requests WHERE ts <= ?", (now - DAY,))
            conn.execute("INSERT INTO requests VALUES (?)", (now,))

    # ------------------------------------------------------------ queue

    def queue(self, conn: sqlite3.Connection) -> dict[str, dict]:
        """Candidates in priority order plus per-tier progress. Bumped accounts come first."""
        now = self.clock()
        latest = conn.execute(
            "SELECT id, owner FROM (SELECT id, owner, ROW_NUMBER() OVER ("
            " PARTITION BY owner ORDER BY taken_at DESC, id DESC) rn FROM snapshots) WHERE rn=1"
        ).fetchall()
        tier_of: dict[str, int] = {}
        for snap in latest:
            sid = snap["id"]
            # Ignored accounts are skipped in tier 1; snoozed ones are not checked at all.
            # Decisions belong to one Instagram account; the accounts themselves are shared, so
            # an account is checked if any owner still wants it.
            kept = set()
            snoozed = set()
            for d in conn.execute(
                "SELECT username_key, kind, until FROM decisions WHERE owner = ?", (snap["owner"],)
            ):
                if d["kind"] == "ignored":
                    kept.add(d["username_key"])
                elif d["kind"] == "snoozed" and d["until"] > _iso(now):
                    snoozed.add(d["username_key"])
            sets = {
                kind: {r["username_key"] for r in membership.read(conn, snap["owner"], kind, sid)}
                for kind in ("following", "followers", "pending_sent", "pending_received")
            }
            following, followers = sets["following"], sets["followers"]
            groups = {
                1: (following - followers) - kept - snoozed,
                2: (sets["pending_sent"] | sets["pending_received"]) - snoozed,
                3: followers - following - snoozed,
                4: (following & followers) - snoozed,
            }
            for tier, keys in groups.items():
                for k in keys:
                    tier_of[k] = min(tier, tier_of.get(k, 99))
        rows = {}
        keys = list(tier_of)
        for i in range(0, len(keys), 500):
            chunk = keys[i : i + 500]
            for r in conn.execute(
                f"SELECT * FROM accounts WHERE username_key IN ({','.join('?' * len(chunk))})",
                chunk,
            ):
                rows[r["username_key"]] = r
        progress = {
            str(t): {"label": name, "total": 0, "checked": 0, "due": 0} for t, name in TIERS.items()
        }
        due: list[tuple] = []
        for key, tier in tier_of.items():
            r = rows.get(key)
            if r is None:
                continue
            p = progress[str(tier)]
            p["total"] += 1
            checked = r["status"] != "unchecked"
            p["checked"] += checked
            nxt = _parse_iso(r["next_check_at"])
            expired = r["status"] == "unchecked" or nxt is None or nxt <= now
            # fetched with an older version of what we read from a page: due, but behind
            # everything that is unchecked or expired
            outdated = not expired and r["status"] == "live" and r["fetch_version"] < CACHE_VERSION
            if r["priority_bump"] or expired or outdated:
                p["due"] += 1
                due.append(
                    (
                        0 if r["priority_bump"] else 1,
                        1 if outdated else 0,
                        tier,
                        0 if r["status"] == "unchecked" else 1,
                        r["checked_at"] or "",  # the oldest first
                        key,
                        r["username"],
                    )
                )
        due.sort()
        return {"due": [{"key": d[5], "username": d[6]} for d in due], "progress": progress}

    # ------------------------------------------------------------ one step

    def cooldown_remaining(self, conn: sqlite3.Connection) -> float:
        return max(0.0, (self._get(conn, "cooldown_until", 0) or 0) - self.clock())

    def _enter_cooldown(self, conn: sqlite3.Connection, reason: str) -> None:
        level = self._get(conn, "backoff_level", 0)
        base = self.settings.cooldown_base_minutes * 60
        seconds = min(base * 2**level, max(base, self.settings.cooldown_max_hours * 3600))
        self._set(conn, "backoff_level", level + 1)
        self._set(conn, "cooldown_until", self.clock() + seconds)
        self._set(conn, "last_cooldown_reason", reason)
        log.warning("rate limited (%s): pausing requests for %d minutes", reason, seconds // 60)

    def _reset_backoff(self, conn: sqlite3.Connection) -> None:
        self._set(conn, "backoff_level", 0)

    async def run_once(self) -> str:
        """Check one account if allowed. Returns a short reason code (also used by tests)."""
        conn = self._conn()
        try:
            self.refresh_settings(conn)
            if self._get(conn, "paused", False):
                return "paused"
            if self.cooldown_remaining(conn) > 0:
                return "cooldown"
            if self._requests_24h(conn) >= self.settings.daily_request_cap:
                return "cap"
            due = self.queue(conn)["due"]
            if not due:
                return "idle"
            target = due[0]
            async with self._lock:
                self.current = target["username"]
                try:
                    return await self._check(conn, target["key"], target["username"])
                finally:
                    self.current = None
        finally:
            conn.close()

    async def check_now(self, username: str) -> dict:
        """Look one account up right away, for someone who is waiting for it on screen.

        It skips the queue, the pacing delay, the pause switch and the daily cap, but not a
        cooldown: once Instagram has asked for a pause, asking again is what it asked us not to
        do. Returns the outcome ("live", "missing", "error", "cooldown", ...)."""
        conn = self._conn()
        try:
            self.refresh_settings(conn)
            key = username.lower()
            acc = conn.execute(
                "SELECT username FROM accounts WHERE username_key=?", (key,)
            ).fetchone()
            if acc is None:
                return {"outcome": "unknown"}
            remaining = self.cooldown_remaining(conn)
            if remaining > 0:
                return {"outcome": "cooldown", "retry_in": int(remaining)}
            async with self._lock:
                self.current = acc["username"]
                try:
                    outcome = await self._check(conn, key, acc["username"], urgent=True)
                    return {"outcome": outcome}
                finally:
                    self.current = None
        finally:
            conn.close()

    async def _request(self, conn: sqlite3.Connection, username: str) -> LookupResult:
        self._record_request(conn)
        return await self.strategy.lookup(username)

    async def _check(
        self, conn: sqlite3.Connection, key: str, username: str, *, urgent: bool = False
    ) -> str:
        result = await self._request(conn, username)
        if result.outcome == "missing":
            # A missing profile and a blocked request look the same, so confirm that a
            # known-live account still resolves before believing it.
            if self._requests_24h(conn) >= self.settings.daily_request_cap:
                return "cap"
            canary = await self._request(conn, CANARY_USERNAME)
            if canary.outcome != "live":
                self._enter_cooldown(conn, f"canary returned {canary.outcome}")
                self._record_last(conn, username, "blocked", canary.detail)
                return "cooldown"
        if result.outcome == "rate_limited":
            self._enter_cooldown(conn, result.detail or "rate limited")
            self._record_last(conn, username, "rate_limited", result.detail)
            return "rate_limited"
        self._reset_backoff(conn)
        now = self.clock()
        acc = conn.execute("SELECT * FROM accounts WHERE username_key=?", (key,)).fetchone()
        s = self.settings
        if result.outcome == "live":
            avatar_path = acc["avatar_path"]
            dest = s.avatar_dir / f"{key}.jpg"
            # A re-check doesn't need to download a picture that is only a few weeks old.
            fresh = dest.is_file() and 0 <= now - dest.stat().st_mtime < AVATAR_REFRESH_DAYS * DAY
            if fresh:
                avatar_path = f"avatars/{key}.jpg"  # the file may exist without the row knowing
            elif result.picture_url and (urgent or self._requests_24h(conn) < s.daily_request_cap):
                self._record_request(conn)
                if await download_avatar(result.picture_url, dest, s.request_timeout_seconds):
                    avatar_path = f"avatars/{key}.jpg"
            with conn:
                conn.execute(
                    "UPDATE accounts SET status='live', http_status=?,"
                    " full_name=COALESCE(?, full_name), is_private=?, is_verified=?,"
                    " ig_user_id=COALESCE(?, ig_user_id), avatar_path=?, checked_at=?,"
                    " next_check_at=?, attempts=0, last_error=NULL, unavailable_hits=0,"
                    " first_unavailable_at=NULL, priority_bump=0, fetch_version=?,"
                    " last_post_at=CASE WHEN ?='known' THEN ? ELSE last_post_at END,"
                    " posts_state=CASE WHEN ?='known' THEN 'known'"
                    " ELSE COALESCE(posts_state, 'hidden') END,"
                    " followers_count=COALESCE(?, followers_count),"
                    " following_count=COALESCE(?, following_count) WHERE username_key=?",
                    (
                        result.http_status,
                        result.full_name,
                        None if result.is_private is None else int(result.is_private),
                        None if result.is_verified is None else int(result.is_verified),
                        result.ig_user_id,
                        avatar_path,
                        _iso(now),
                        _iso(now + s.recheck_live_days * DAY),
                        CACHE_VERSION,
                        result.posts_state,
                        result.last_post_at,
                        result.posts_state,
                        result.followers_count,
                        result.following_count,
                        key,
                    ),
                )
        elif result.outcome == "missing":
            self._apply_missing(conn, acc, now, result)
        else:  # error
            attempts = acc["attempts"] + 1
            retry = min(ERROR_RETRY_BASE * 2 ** (attempts - 1), ERROR_RETRY_MAX)
            keep_status = acc["status"] not in ("unchecked", "error")
            with conn:
                conn.execute(
                    "UPDATE accounts SET status=?, http_status=?, last_error=?, attempts=?,"
                    " checked_at=?, next_check_at=?, priority_bump=0 WHERE username_key=?",
                    (
                        acc["status"] if keep_status else "error",
                        result.http_status,
                        result.detail,
                        attempts,
                        _iso(now),
                        _iso(now + retry),
                        key,
                    ),
                )
        self._record_last(conn, username, result.outcome, result.detail)
        return result.outcome

    def _apply_missing(self, conn, acc, now: float, result: LookupResult) -> None:
        """Two consistent results at least RECHECK_UNAVAILABLE_DAYS apart make it unavailable."""
        s = self.settings
        gap = s.recheck_unavailable_days * DAY
        first = _parse_iso(acc["first_unavailable_at"])
        hits = acc["unavailable_hits"]
        if first is None or hits == 0:
            status, first_iso, hits = "possibly_unavailable", _iso(now), 1
        elif now - first >= gap:
            status, first_iso, hits = "unavailable", acc["first_unavailable_at"], hits + 1
        else:  # a second result too soon (e.g. "Check now") doesn't count as confirmation
            status, first_iso = acc["status"], acc["first_unavailable_at"]
        with conn:
            conn.execute(
                "UPDATE accounts SET status=?, http_status=?, checked_at=?, next_check_at=?,"
                " attempts=0, last_error=NULL, unavailable_hits=?, first_unavailable_at=?,"
                " priority_bump=0 WHERE username_key=?",
                (
                    status,
                    result.http_status,
                    _iso(now),
                    _iso(now + gap),
                    hits,
                    first_iso,
                    acc["username_key"],
                ),
            )

    def _record_last(self, conn, username: str, outcome: str, detail: str | None) -> None:
        self._set(
            conn,
            "last_result",
            {"username": username, "outcome": outcome, "detail": detail, "at": _iso(self.clock())},
        )
        self._set(conn, "checks_done", (self._get(conn, "checks_done", 0) or 0) + 1)

    # ------------------------------------------------------------ control surface

    def pause(self) -> None:
        conn = self._conn()
        try:
            self._set(conn, "paused", True)
        finally:
            conn.close()
        self._wake.set()

    def resume(self) -> None:
        conn = self._conn()
        try:
            self._set(conn, "paused", False)
        finally:
            conn.close()
        self._wake.set()

    def bump_stale(self, usernames: list[str]) -> int:
        """Move accounts the user is looking at to the front, if unchecked or stale.

        Used by the UI when a row stays in view. The lookup still goes through the normal
        pacing, cooldown and daily cap. Returns how many were queued.
        """
        keys = list(dict.fromkeys(u.lower() for u in usernames))[:MAX_VISIBLE_BATCH]
        conn = self._conn()
        try:
            self.refresh_settings(conn)
            cutoff = _iso(self.clock() - self.settings.viewport_check_days * DAY)
            pending = conn.execute(
                "SELECT COUNT(*) FROM accounts WHERE priority_bump=1"
            ).fetchone()[0]
            queued = 0
            with conn:
                for key in keys:
                    if pending + queued >= MAX_PENDING_BUMPS:
                        break
                    cur = conn.execute(
                        "UPDATE accounts SET priority_bump=1 WHERE username_key=?"
                        " AND priority_bump=0 AND (checked_at IS NULL OR checked_at < ?"
                        " OR (status='live' AND posts_state IS NULL))",
                        (key, cutoff),
                    )
                    queued += cur.rowcount
        finally:
            conn.close()
        if queued:
            self._wake.set()
        return queued

    def status(self) -> dict:
        conn = self._conn()
        try:
            self.refresh_settings(conn)
            remaining = self.cooldown_remaining(conn)
            paused = bool(self._get(conn, "paused", False))
            used = self._requests_24h(conn)
            q = self.queue(conn)
            if paused:
                state = "paused"
            elif remaining > 0:
                state = "cooldown"
            elif used >= self.settings.daily_request_cap:
                state = "cap_reached"
            elif not q["due"]:
                state = "idle"  # nothing left to check right now
            else:
                state = "running"
            return {
                "enabled": True,
                "state": state,
                "paused": paused,
                "cooldown_until": _iso(self.clock() + remaining) if remaining > 0 else None,
                "cooldown_reason": self._get(conn, "last_cooldown_reason")
                if remaining > 0
                else None,
                "requests_24h": used,
                "daily_cap": self.settings.daily_request_cap,
                "viewport_check_days": self.settings.viewport_check_days,
                "viewport_dwell_seconds": self.settings.viewport_dwell_seconds,
                "queue_size": len(q["due"]),
                "tiers": q["progress"],
                "current": self.current,
                "last_result": self._get(conn, "last_result"),
                "checks_done": self._get(conn, "checks_done", 0),
            }
        finally:
            conn.close()

    # ------------------------------------------------------------ loop

    async def _sleep(self, seconds: float) -> None:
        if self._sleeper:
            await self._sleeper(seconds)
            return
        self._wake.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)

    async def run(self) -> None:
        log.info("account checker started")
        try:
            while True:
                try:
                    outcome = await self.run_once()
                except Exception:  # keep the loop alive; the account is retried later
                    log.exception("checker step failed")
                    outcome = "failed"
                if outcome in ("paused", "idle", "cap", "failed"):
                    await self._sleep(30 if outcome in ("idle", "failed") else 5)
                elif outcome == "cooldown":
                    conn = self._conn()
                    try:
                        wait = min(self.cooldown_remaining(conn), 60) or 1
                    finally:
                        conn.close()
                    await self._sleep(wait)
                else:
                    s = self.settings  # refreshed by run_once
                    await self._sleep(
                        s.check_interval_seconds + random.uniform(0, s.check_jitter_seconds)
                    )
        except asyncio.CancelledError:
            log.info("account checker stopped")
            raise
