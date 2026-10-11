"""HTTP routes. All JSON except the CSV export and avatar images."""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import analysis, db, runtime_settings
from .ingest import membership
from .ingest.parse import valid_username
from .ingest.snapshot import IMPORT_LOCK, ScanSummary
from .ingest.upload import import_uploads
from .watcher import record_scan

router = APIRouter(prefix="/api")

PAGE_MAX = 500
UPLOAD_MAX_BYTES = 1024 * 1024 * 1024  # per file; real exports of these lists are far smaller


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = db.connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


def _resolve(conn: sqlite3.Connection, owner: str | None, snapshot: int | None):
    try:
        return analysis.resolve(conn, owner, snapshot)
    except analysis.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


def _owner_or_default(conn: sqlite3.Connection, owner: str | None) -> str:
    """The Instagram account a request is about: the one given, or the newest export's."""
    if owner is None:
        owners = analysis.owners(conn)
        if not owners:
            raise HTTPException(404, "no snapshots found")
        return owners[0]["owner"]
    if not analysis.snapshots(conn, owner):
        raise HTTPException(404, "unknown account")
    return owner


@router.get("/health")
def health() -> dict:
    return {"ok": True}


@router.get("/owners")
def get_owners(conn: Conn) -> list[dict]:
    return analysis.owners(conn)


@router.get("/snapshots")
def get_snapshots(conn: Conn, owner: str | None = None) -> list[dict]:
    return analysis.snapshots(conn, _owner_or_default(conn, owner))


@router.delete("/snapshots/{snapshot_id}")
def delete_snapshot(conn: Conn, snapshot_id: int) -> dict:
    """Remove one snapshot from the database. The export file itself is left alone."""
    row = conn.execute("SELECT owner FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "snapshot not found")
    owner = row["owner"]
    with IMPORT_LOCK, conn:
        membership.remove_snapshot(conn, snapshot_id)
    return {
        "deleted": snapshot_id,
        "owner": owner,
        "remaining": len(analysis.snapshots(conn, owner)),
    }


class TooLarge(Exception):
    pass


def _copy_capped(src, dest: Path) -> None:
    size = 0
    with dest.open("wb") as out:
        while chunk := src.read(1024 * 1024):
            size += len(chunk)
            if size > UPLOAD_MAX_BYTES:
                raise TooLarge("that file is too large to be a followers export")
            out.write(chunk)


@router.post("/upload")
async def upload(
    request: Request, files: list[UploadFile], owner: str | None = None, force: bool = False
) -> dict:
    """Import an export (.zip) or loose followers/following files sent from the browser.

    ``owner`` is the account to use for files that do not say whose they are (their name and
    contents come first). Without it such files are not imported but listed under ``needs_owner``.
    An export whose followers look cut to a date range is not imported but reported under
    ``partial``; sending it again with ``force`` imports it anyway.
    """
    settings = request.app.state.settings
    if owner and not valid_username(owner):
        raise HTTPException(422, "invalid username")

    def work() -> ScanSummary:
        incoming = settings.state_dir / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        saved: list[tuple[str, Path]] = []
        try:
            for f in files:
                tmp = incoming / f"{uuid.uuid4().hex}.part"
                saved.append((f.filename or "", tmp))
                _copy_capped(f.file, tmp)
            conn = db.connect(settings.db_path)
            try:
                return import_uploads(
                    conn, settings.data_dir, settings.state_dir, saved, owner or None, force
                )
            finally:
                conn.close()
        finally:
            for _, tmp in saved:
                tmp.unlink(missing_ok=True)

    try:
        summary = await asyncio.to_thread(work)
    except TooLarge as exc:
        raise HTTPException(413, str(exc)) from exc
    record_scan(request.app, summary)
    return summary.as_dict()


@router.get("/watch")
def get_watch(request: Request) -> dict:
    """How many snapshots have been imported so far. The UI polls this to pick up new exports."""
    state = request.app.state
    return {"imports": state.imports, "last_import": state.last_import}


@router.get("/summary")
def get_summary(
    request: Request, conn: Conn, owner: str | None = None, snapshot: int | None = None
) -> dict:
    snap = _resolve(conn, owner, snapshot)
    settings = runtime_settings.effective(request.app.state.settings, conn)
    return {**analysis.summary(conn, snap), "old_snapshot_days": settings.old_snapshot_days}


SortKey = Literal["newest", "oldest", "az", "post_new", "post_old"]


def _rows(request, conn, name, owner, snapshot, q, sort) -> list[dict]:
    snap = _resolve(conn, owner, snapshot)
    settings = runtime_settings.effective(request.app.state.settings, conn)
    try:
        return analysis.build_rows(
            conn,
            snap["id"],
            name,
            q=q,
            sort=sort,
            inactive_days=settings.inactive_after_days,
            high_ratio_at=settings.high_follower_ratio,
        )
    except analysis.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/lists/{name}")
def get_list(
    request: Request,
    conn: Conn,
    name: str,
    owner: str | None = None,
    snapshot: int | None = None,
    q: str | None = None,
    sort: SortKey = "newest",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=PAGE_MAX)] = 150,
) -> dict:
    rows = _rows(request, conn, name, owner, snapshot, q, sort)
    return {"total": len(rows), "offset": offset, "items": rows[offset : offset + limit]}


@router.get("/profile/{username}")
def get_profile(
    request: Request,
    conn: Conn,
    username: str,
    owner: str | None = None,
    snapshot: int | None = None,
) -> dict:
    """One account: its row, how you are related to it, and the history of that relation."""
    if not valid_username(username):
        raise HTTPException(422, "invalid username")
    snap = _resolve(conn, owner, snapshot)
    settings = runtime_settings.effective(request.app.state.settings, conn)
    found = analysis.profile(
        conn,
        snap,
        username,
        inactive_days=settings.inactive_after_days,
        high_ratio_at=settings.high_follower_ratio,
    )
    if found is None:
        raise HTTPException(404, "unknown account")
    return found


@router.get("/timeline")
def get_timeline(conn: Conn, owner: str | None = None) -> list[dict]:
    return analysis.timeline(conn, _owner_or_default(conn, owner))


@router.get("/export/{name}.csv")
def export_csv(
    request: Request,
    conn: Conn,
    name: str,
    owner: str | None = None,
    snapshot: int | None = None,
    q: str | None = None,
    sort: SortKey = "newest",
) -> Response:
    rows = _rows(request, conn, name, owner, snapshot, q, sort)
    return Response(
        analysis.rows_to_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
    )


class DecisionBody(BaseModel):
    decision: Literal["ignored", "snoozed", "follow", "unfollow", "none"]  # "none" clears it


@router.post("/decisions/{username}")
def set_decision(
    request: Request, conn: Conn, username: str, body: DecisionBody, owner: str | None = None
) -> dict:
    """Record what you decided about an account, for one Instagram account (owner).

    There is one decision per account: choosing another replaces it, and "none" removes it.
    """
    if not valid_username(username):
        raise HTTPException(422, "invalid username")
    owner = _owner_or_default(conn, owner)
    key = username.lower()
    settings = runtime_settings.effective(request.app.state.settings, conn)
    now = datetime.now(UTC)
    until = None
    with conn:
        if body.decision == "none":
            conn.execute("DELETE FROM decisions WHERE owner = ? AND username_key = ?", (owner, key))
        else:
            if body.decision == "snoozed":
                until = (now + timedelta(days=settings.snooze_days)).isoformat(timespec="seconds")
            # the account row is what a decision points at
            conn.execute(
                "INSERT OR IGNORE INTO accounts(username_key, username) VALUES (?, ?)",
                (key, username),
            )
            conn.execute(
                "INSERT INTO decisions(owner, username_key, kind, decided_at, until)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(owner, username_key) DO UPDATE SET kind = excluded.kind,"
                " decided_at = excluded.decided_at, until = excluded.until",
                (owner, key, body.decision, now.isoformat(timespec="seconds"), until),
            )
    return {
        "username": username,
        "decision": None if body.decision == "none" else body.decision,
        "until": until,
    }


@router.get("/settings")
def get_settings(request: Request, conn: Conn) -> dict:
    return {"fields": runtime_settings.describe(request.app.state.settings, conn)}


@router.put("/settings")
def put_settings(request: Request, conn: Conn, body: dict[str, object]) -> dict:
    """Save changed settings. A value of null resets that setting to its default."""
    try:
        runtime_settings.update(conn, body)
    except runtime_settings.InvalidSetting as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"fields": runtime_settings.describe(request.app.state.settings, conn)}


@router.get("/avatars/{username}")
def get_avatar(request: Request, username: str) -> FileResponse:
    if not valid_username(username):
        raise HTTPException(404, "no avatar")
    path = request.app.state.settings.avatar_dir / f"{username.lower()}.jpg"
    if not path.is_file():
        raise HTTPException(404, "no avatar")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


# ---------------------------------------------------------------- checker


def _worker(request: Request):
    worker = request.app.state.worker
    if worker is None:
        raise HTTPException(409, "The account checker is disabled (FA_CHECKER_ENABLED=false).")
    return worker


@router.get("/checker")
def checker_status(request: Request) -> dict:
    worker = request.app.state.worker
    if worker is None:
        return {"enabled": False, "state": "disabled"}
    return worker.status()


@router.post("/checker/pause")
def checker_pause(request: Request) -> dict:
    _worker(request).pause()
    return request.app.state.worker.status()


@router.post("/checker/resume")
def checker_resume(request: Request) -> dict:
    _worker(request).resume()
    return request.app.state.worker.status()


@router.post("/checker/fetch/{username}")
async def checker_fetch(request: Request, username: str) -> dict:
    """Look an account up now, for the profile window. See CheckerWorker.check_now."""
    if not valid_username(username):
        raise HTTPException(422, "invalid username")
    outcome = await _worker(request).check_now(username)
    if outcome["outcome"] == "unknown":
        raise HTTPException(404, "unknown username")
    return outcome


class VisibleBody(BaseModel):
    usernames: list[str]


@router.post("/checker/visible")
def checker_visible(request: Request, body: VisibleBody) -> dict:
    """Usernames the user has been looking at; unchecked or stale ones are queued first."""
    valid = [u for u in body.usernames if valid_username(u)]
    return {"queued": _worker(request).bump_stale(valid)}
