import shutil

import pytest
from conftest import FIXTURES
from fastapi.testclient import TestClient

from follow_audit import db, runtime_settings
from follow_audit.app import create_app
from follow_audit.config import Settings
from follow_audit.ingest.snapshot import scan
from follow_audit.watcher import DataWatcher, signature


@pytest.fixture
def watched(make_data_dir, tmp_path):
    data = make_data_dir("json_folder")
    settings = Settings(data_dir=data, state_dir=tmp_path / "state", checker_enabled=False)
    db.init_db(settings.db_path)
    conn = db.connect(settings.db_path)
    scan(conn, data)
    conn.close()
    app = create_app(settings)
    watcher = DataWatcher(app)
    watcher.prime()
    return app, watcher, data, settings


def drop_newer_zip(data, folder="json_zip"):
    """Copy the zip fixture in, dated after the folder export already imported."""
    export = data / folder
    shutil.copytree(FIXTURES / "json_zip", export)
    zip_path = next(export.glob("*.zip"))
    return zip_path.rename(zip_path.with_name("instagram-alice-2027-01-01-AAAA.zip"))


def count_snapshots(settings):
    conn = db.connect(settings.db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    finally:
        conn.close()


def test_signature_changes_when_files_change(make_data_dir):
    data = make_data_dir("json_folder")
    before = signature(data)
    assert before == signature(data)
    shutil.copytree(FIXTURES / "json_zip", data / "json_zip")
    assert signature(data) != before


async def test_nothing_changed_means_idle(watched):
    _, watcher, _, _ = watched
    assert await watcher.step() == "idle"


async def test_new_export_is_imported_once_stable(watched):
    app, watcher, data, settings = watched
    drop_newer_zip(data)
    assert await watcher.step() == "pending"  # seen once: might still be copying
    assert count_snapshots(settings) == 1
    assert await watcher.step() == "scanned"
    assert count_snapshots(settings) == 2
    assert app.state.imports == 1 and app.state.last_import["owner"] == "alice"
    assert await watcher.step() == "idle"  # nothing re-imported


async def test_file_still_growing_is_not_scanned(watched):
    _, watcher, data, settings = watched
    zip_path = drop_newer_zip(data)
    original = zip_path.read_bytes()
    zip_path.write_bytes(original[:100])  # half copied
    assert await watcher.step() == "pending"
    zip_path.write_bytes(original)  # the copy progressed, so the fingerprint changed
    assert await watcher.step() == "pending"
    assert count_snapshots(settings) == 1
    assert await watcher.step() == "scanned"
    assert count_snapshots(settings) == 2


async def test_can_be_switched_off_in_settings(watched):
    _, watcher, data, settings = watched
    conn = db.connect(settings.db_path)
    runtime_settings.update(conn, {"watch_interval_seconds": 0})
    conn.close()
    shutil.copytree(FIXTURES / "json_zip", data / "json_zip")
    assert await watcher.step() == "off"
    assert await watcher.step() == "off"
    assert count_snapshots(settings) == 1


async def test_bad_file_does_not_break_the_watcher(watched):
    app, watcher, data, settings = watched
    (data / "broken.zip").write_bytes(b"not a zip")
    await watcher.step()
    assert await watcher.step() == "scanned"
    assert app.state.imports == 0
    assert await watcher.step() == "idle"  # not retried until the file changes


def test_watch_endpoint_and_upload_counter(make_data_dir, tmp_path):
    data = make_data_dir("json_zip")
    settings = Settings(
        data_dir=data, state_dir=tmp_path / "s", checker_enabled=False, watch_interval_seconds=0
    )
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        first = client.get("/api/watch").json()
        assert first["imports"] == 1 and first["last_import"]["owner"] == "alice"  # startup scan
        zip_path = next((FIXTURES / "json_folder").rglob("followers_1.json"))
        out = client.post(
            "/api/upload",
            params={"owner": "alice"},
            files=[("files", (zip_path.name, zip_path.read_bytes()))],
        )
        assert len(out.json()["imported"]) == 1
        assert client.get("/api/watch").json()["imports"] == 2
