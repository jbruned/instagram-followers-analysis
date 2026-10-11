import shutil

from follow_audit.ingest.discover import discover
from follow_audit.ingest.snapshot import parse_name, scan


def rows(conn, sid, kind):
    return {
        r["username_key"]
        for r in conn.execute(
            "SELECT username_key FROM membership WHERE first_snapshot<=? AND kind=?"
            " AND (last_snapshot IS NULL OR last_snapshot>=?)",
            (sid, kind, sid),
        )
    }


def test_parse_name():
    assert parse_name(["instagram-some.user-2024-03-02-AbC"]) == ("some.user", "2024-03-02")
    assert parse_name(["instagram-x-2024-13-45-AbC"]) == (None, None)
    assert parse_name(["random"]) == (None, None)


def test_discovers_zip_and_folders(make_data_dir):
    data = make_data_dir("json_zip", "json_folder", "legacy2021", "legacy2020", "html_export")
    names = sorted(e.name for e in discover(data))
    assert names == [
        "html_export",
        "instagram-alice-2024-03-02-AAAA.zip",
        "instagram-alice-2026-10-09-BBBB",
        "legacy2020",
        "legacy2021",
    ]


def test_zip_import_and_metadata(make_data_dir, conn):
    summary = scan(conn, make_data_dir("json_zip"))
    assert len(summary.imported) == 1 and not summary.errors
    snap = conn.execute("SELECT * FROM snapshots").fetchone()
    assert (snap["owner"], snap["taken_at"], snap["taken_at_source"]) == (
        "alice",
        "2024-03-02",
        "name",
    )
    # multi-part followers files are merged
    assert rows(conn, snap["id"], "followers") == {"bob", "carol", "dave", "erin", "frank"}
    assert rows(conn, snap["id"], "following") == {"bob", "carol", "gina", "hank"}
    assert rows(conn, snap["id"], "pending_sent") == {"ivan"}
    assert rows(conn, snap["id"], "blocked") == {"spammer"}
    # unknown files such as custom_lists.json are ignored
    assert "custom_lists.json" not in snap["file_list_json"]


def test_folder_import_with_extra_top_level(make_data_dir, conn):
    scan(conn, make_data_dir("json_folder"))
    snap = conn.execute("SELECT * FROM snapshots").fetchone()
    assert (snap["owner"], snap["taken_at"]) == ("alice", "2026-10-09")


def test_idempotent(make_data_dir, conn):
    data = make_data_dir("json_zip", "json_folder")
    assert len(scan(conn, data).imported) == 2
    again = scan(conn, data)
    assert again.imported == [] and again.skipped == 2
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2
    # the same export under a different container/path is still recognised
    shutil.copytree(data / "json_folder", data / "copy")
    assert scan(conn, data).imported == []


def test_html_date_fallback_to_latest_entry(make_data_dir, conn):
    scan(conn, make_data_dir("html_export"))
    snap = conn.execute("SELECT * FROM snapshots").fetchone()
    assert snap["owner"] == ""
    assert (snap["taken_at"], snap["taken_at_source"]) == ("2024-06-03", "latest_entry")


def test_legacy_formats(make_data_dir, conn):
    scan(conn, make_data_dir("legacy2021", "legacy2020"))
    by_kind = {}
    for r in conn.execute(
        "SELECT s.source_path, r.kind, COUNT(*) n FROM membership r "
        "JOIN snapshots s ON s.id=r.first_snapshot GROUP BY 1,2"
    ):
        by_kind[(r["source_path"].rsplit("/", 1)[-1], r["kind"])] = r["n"]
    assert by_kind[("legacy2021", "followers")] == 2
    assert by_kind[("legacy2020", "pending_sent")] == 1
    assert ("legacy2020", "following_hashtags") not in by_kind


def test_bad_zip_does_not_block_others(make_data_dir, conn):
    data = make_data_dir("json_folder")
    (data / "broken.zip").write_bytes(b"not a zip")
    summary = scan(conn, data)
    assert len(summary.imported) == 1 and len(summary.errors) == 1
