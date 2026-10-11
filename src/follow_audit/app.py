"""App factory: API routes, static UI and lifespan (startup scan, folder watcher, checker)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import api, db
from .checker.worker import CheckerWorker
from .config import Settings
from .ingest.snapshot import scan
from .security import LocalOnly
from .watcher import DataWatcher, record_scan

log = logging.getLogger("follow_audit")
STATIC_DIR = Path(__file__).parent / "static"


class RevalidatingStaticFiles(StaticFiles):
    """Static files the browser re-checks on every load.

    Without this, browsers may keep using an old copy of the page's scripts and styles for a
    while after an update. ``no-cache`` still allows cheap "304 not modified" answers.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        # leftovers of an upload that was cut short (the app was stopped while it was running)
        shutil.rmtree(settings.state_dir / "incoming", ignore_errors=True)
        db.init_db(settings.db_path)
        watcher = DataWatcher(app)
        watcher.prime()  # fingerprint first, so the startup scan covers what is on disk now
        if settings.scan_on_startup:

            def work():
                conn = db.connect(settings.db_path)
                try:
                    return scan(conn, settings.data_dir)
                finally:
                    conn.close()

            summary = await asyncio.to_thread(work)
            record_scan(app, summary)
            log.info(
                "startup scan: %d found, %d imported, %d skipped, %d errors",
                summary.found,
                len(summary.imported),
                summary.skipped,
                len(summary.errors),
            )
        tasks = [asyncio.create_task(watcher.run(), name="data-watcher")]
        if settings.checker_enabled:
            app.state.worker = CheckerWorker(settings)
            tasks.append(asyncio.create_task(app.state.worker.run(), name="account-checker"))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="Follow Audit", lifespan=lifespan)
    extra = {h.strip() for h in settings.allowed_hosts.split(",") if h.strip()}
    app.add_middleware(LocalOnly, allowed_hosts=extra)
    app.state.settings = settings
    app.state.worker = None
    app.state.imports = 0  # snapshots imported so far; the UI polls this to refresh itself
    app.state.last_import = None
    app.include_router(api.router)
    app.mount("/", RevalidatingStaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
