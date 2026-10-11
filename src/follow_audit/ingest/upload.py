"""Import files sent from the browser: a whole export (.zip) or loose followers/following files.

Files are staged in the state folder and imported from there. Only what was actually imported is
kept, moved to ``data/uploads/``, so the data folder stays the home of every export.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import uuid
from pathlib import Path

from .discover import PERSONAL_INFO_NAME, Export
from .parse import is_relevant
from .snapshot import NeedsOwner, PartialExport, ScanSummary, import_export

log = logging.getLogger(__name__)

UPLOADS = "uploads"
FOLDER = "followers_and_following"


def clean_path(name: str) -> list[str]:
    """The parts of an uploaded file name, safe to join under a folder."""
    return [p for p in name.replace("\\", "/").split("/") if p not in ("", ".", "..")]


def _loose_target(parts: list[str]) -> Path | None:
    """Where a loose file goes so the folder looks like a real export (None: not ours)."""
    name = parts[-1]
    if name == PERSONAL_INFO_NAME:
        return Path("personal_information") / name
    if not is_relevant(name) and not is_relevant("/".join(parts)):
        return None
    if FOLDER in parts[:-1]:
        return Path("connections", *parts[parts.index(FOLDER) :])
    return Path("connections", FOLDER, name)


def _keep(src: Path, data_dir: Path, name: str) -> Path:
    """Move an imported file or folder under data/uploads, never overwriting anything."""
    folder = data_dir / UPLOADS
    dest = folder / name
    try:
        folder.mkdir(parents=True, exist_ok=True)
        n = 2
        while dest.exists():
            dest = folder / f"{Path(name).stem}-{n}{Path(name).suffix}"
            n += 1
        shutil.move(str(src), dest)
    except OSError as exc:
        raise OSError(
            f"could not save a copy under {folder} ({exc.strerror or exc}); is the data folder"
            " writable?"
        ) from exc
    return dest


def import_uploads(
    conn: sqlite3.Connection,
    data_dir: Path,
    state_dir: Path,
    files: list[tuple[str, Path]],
    owner: str | None = None,
    force: bool = False,
) -> ScanSummary:
    """Import ``files`` (name as sent, temporary path). Zips are one snapshot each; every loose
    file together makes one more."""
    summary = ScanSummary()
    stage = state_dir / "incoming" / uuid.uuid4().hex
    stage.mkdir(parents=True)
    try:
        loose = stage / "loose"
        have_loose = False
        for name, tmp in files:
            parts = clean_path(name)
            if not parts:
                continue
            if parts[-1].lower().endswith(".zip"):
                zip_path = (
                    stage / uuid.uuid4().hex[:8] / parts[-1]
                )  # keep the name: it has the owner
                zip_path.parent.mkdir()
                shutil.move(str(tmp), zip_path)
                _import_one(
                    conn, data_dir, Export(zip_path, is_zip=True), parts[-1], summary, owner, force
                )
                continue
            target = _loose_target(parts)
            if target is None:
                summary.errors.append(f"{parts[-1]}: not a followers or following file")
                continue
            dest = loose / target
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(tmp), dest)
            have_loose = True
        if have_loose:
            _import_one(
                conn, data_dir, Export(loose, is_zip=False), "export", summary, owner, force
            )
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return summary


def _import_one(
    conn,
    data_dir: Path,
    export: Export,
    name: str,
    summary: ScanSummary,
    owner: str | None,
    force: bool,
) -> None:
    summary.found += 1
    try:
        result = import_export(
            conn,
            export,
            owner,
            uploaded=True,
            force=force,
            keep=lambda who, day: _keep(
                export.path, data_dir, name if export.is_zip else _folder_name(who, day)
            ),
        )
    except NeedsOwner:
        summary.needs_owner.append({"name": name})
        return
    except PartialExport as exc:
        summary.partial.append(
            {"name": name, "reason": exc.reason, "first": exc.first, "previous": exc.previous}
        )
        return
    except Exception as exc:  # a bad file must not block the others
        log.warning("could not import upload %s: %s", name, exc)
        summary.errors.append(f"{name}: {exc}")
        return
    if result is None:
        summary.skipped += 1
        return
    summary.imported.append(result)


def _folder_name(owner: str, taken_at: str) -> str:
    # a name without an owner must not look like an export's, or a rebuild would invent one
    return f"instagram-{owner}-{taken_at}" if owner else f"loose-files-{taken_at}"
