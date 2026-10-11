"""Notice new exports dropped into the data folder and import them.

This polls the folder (file names, sizes and modification times, no content reads) instead of
using OS file events. Events are not delivered reliably for Docker bind mounts, and a short poll
of a handful of files costs nothing. A change must stay the same for two polls in a row before
the scan runs, so a zip that is still being copied is not read half-written.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastapi import FastAPI

from . import db, runtime_settings
from .ingest.snapshot import ScanSummary, scan

log = logging.getLogger(__name__)
EXTENSIONS = (".zip", ".json", ".html")


def signature(data_dir: Path) -> tuple:
    """A cheap fingerprint of the exports on disk."""
    entries = []
    for dirpath, _, files in os.walk(data_dir):
        for name in files:
            if name.lower().endswith(EXTENSIONS):
                path = Path(dirpath) / name
                try:
                    st = path.stat()
                except OSError:  # removed while walking
                    continue
                entries.append((str(path.relative_to(data_dir)), st.st_size, int(st.st_mtime)))
    return tuple(sorted(entries))


def record_scan(app: FastAPI, summary: ScanSummary) -> None:
    """Remember imports so the UI can notice them."""
    if summary.imported:
        app.state.imports += len(summary.imported)
        app.state.last_import = summary.imported[-1]


class DataWatcher:
    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.settings = app.state.settings
        self.last: tuple | None = None  # fingerprint of what has been scanned
        self.candidate: tuple | None = None  # a change seen once, waiting to be stable
        self.interval = 0.0  # as of the last poll

    def prime(self) -> None:
        """Take the fingerprint of what is on disk now (before the startup scan)."""
        self.last = signature(self.settings.data_dir)

    def _interval(self) -> float:
        conn = db.connect(self.settings.db_path)
        try:
            return runtime_settings.effective(self.settings, conn).watch_interval_seconds
        finally:
            conn.close()

    def _scan(self) -> ScanSummary:
        conn = db.connect(self.settings.db_path)
        try:
            return scan(conn, self.settings.data_dir)
        finally:
            conn.close()

    async def step(self) -> str:
        """One poll. Returns 'off', 'idle', 'pending' or 'scanned'."""
        self.interval = await asyncio.to_thread(
            self._interval
        )  # the database read stays off the loop
        if self.interval <= 0:
            self.candidate = None
            return "off"
        sig = await asyncio.to_thread(signature, self.settings.data_dir)
        if sig == self.last:
            self.candidate = None
            return "idle"
        if sig != self.candidate:
            self.candidate = sig
            return "pending"
        summary = await asyncio.to_thread(self._scan)
        self.last, self.candidate = sig, None
        record_scan(self.app, summary)
        if summary.imported or summary.errors:
            log.info(
                "data folder changed: %d imported, %d errors",
                len(summary.imported),
                len(summary.errors),
            )
        return "scanned"

    async def run(self) -> None:
        log.info("watching the data folder for new exports")
        while True:
            try:
                await self.step()
            except Exception:  # keep watching; the next poll tries again
                log.exception("data folder watch failed")
            await asyncio.sleep(self.interval if self.interval > 0 else 5)
