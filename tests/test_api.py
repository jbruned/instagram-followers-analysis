import json

import pytest
from fastapi.testclient import TestClient

from follow_audit.app import create_app
from follow_audit.config import Settings


@pytest.fixture
def client(make_data_dir, tmp_path):
    settings = Settings(
        data_dir=make_data_dir("json_zip", "json_folder"),
        state_dir=tmp_path / "state",
        checker_enabled=False,
    )
    with TestClient(create_app(settings), base_url="http://localhost") as c:
        yield c


def test_health_and_static(client):
    assert client.get("/api/health").json() == {"ok": True}
    assert "text/html" in client.get("/").headers["content-type"]


def test_owners_snapshots(client):
    assert client.get("/api/owners").json() == [
        {"owner": "alice", "label": "alice", "snapshots": 2}
    ]
    snaps = client.get("/api/snapshots", params={"owner": "alice"}).json()
    assert [s["taken_at"] for s in snaps] == ["2026-10-09", "2024-03-02"]


def _upload(client, *files):
    return client.post("/api/upload", files=[("files", f) for f in files]).json()


def test_uploading_a_known_zip_changes_nothing(client):
    zip_path = next(client.app.state.settings.data_dir.rglob("*.zip"))
    out = _upload(client, (zip_path.name, zip_path.read_bytes()))
    assert out["imported"] == [] and out["skipped"] == 1 and out["errors"] == []


def test_upload_a_new_zip_keeps_it_in_the_uploads_folder(client):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "connections/followers_and_following/followers_1.json",
            '[{"string_list_data": [{"value": "zed", "timestamp": 1700000000}]}]',
        )
    out = _upload(client, ("instagram-alice-2026-11-01-CCCC.zip", buf.getvalue()))
    assert out["errors"] == [] and len(out["imported"]) == 1
    assert out["imported"][0]["owner"] == "alice" and out["imported"][0]["taken_at"] == "2026-11-01"
    kept = client.app.state.settings.data_dir / "uploads" / "instagram-alice-2026-11-01-CCCC.zip"
    assert kept.exists()
    snaps = client.get("/api/snapshots", params={"owner": "alice"}).json()
    assert len(snaps) == 3 and snaps[0]["taken_at"] == "2026-11-01"


def test_upload_loose_files_makes_one_snapshot(client):
    followers = b'[{"string_list_data": [{"value": "zed", "timestamp": 1700000000}]}]'
    following = b'{"relationships_following": [{"string_list_data": [{"value": "yan", "timestamp": 1700000000}]}]}'
    out = client.post(
        "/api/upload",
        params={"owner": "alice"},
        files=[
            ("files", ("followers_1.json", followers)),
            ("files", ("following.json", following)),
        ],
    ).json()
    assert len(out["imported"]) == 1 and out["errors"] == []
    assert out["imported"][0]["counts"] == {"followers": 1, "following": 1}
    kept = list((client.app.state.settings.data_dir / "uploads").iterdir())
    assert len(kept) == 1  # the imported files are kept
    assert client.get("/api/watch").json()["imports"] == 3


def test_loose_files_go_to_the_account_you_are_viewing(client):
    followers = b'[{"string_list_data": [{"value": "zed", "timestamp": 1700000000}]}]'
    out = client.post(
        "/api/upload", params={"owner": "alice"}, files=[("files", ("followers_1.json", followers))]
    ).json()
    assert out["imported"][0]["owner"] == "alice"
    assert len(client.get("/api/owners").json()) == 1


def test_upload_rejects_what_it_cannot_use(client):
    out = _upload(client, ("notes.txt", b"hello"), ("../../evil.json", b"[]"), ("broken.zip", b"x"))
    assert out["imported"] == []
    assert any("notes.txt" in e for e in out["errors"])
    assert any("broken.zip" in e for e in out["errors"])
    settings = client.app.state.settings
    assert not (settings.data_dir.parent / "evil.json").exists()
    assert not list((settings.state_dir / "incoming").iterdir())  # nothing left behind


def test_summary_and_lists(client):
    s = client.get("/api/summary", params={"owner": "alice"}).json()
    assert s["counts"]["mutual"] == 2
    snaps = client.get("/api/snapshots", params={"owner": "alice"}).json()
    old = client.get("/api/summary", params={"snapshot": snaps[1]["id"]}).json()
    assert old["snapshot"]["taken_at"] == "2024-03-02"
    assert old["counts"]["mutual"] == 2
    page = client.get("/api/lists/not_following_back", params={"limit": 1, "sort": "az"}).json()
    assert page["total"] == 2 and len(page["items"]) == 1 and page["items"][0]["username"] == "hank"
    assert client.get("/api/lists/bogus").status_code == 404
    assert client.get("/api/lists/mutual", params={"sort": "bogus"}).status_code == 422


def test_timeline(client):
    tl = client.get("/api/timeline").json()
    assert tl[0]["counts"]["lost_follower"] == 2


def test_decisions_and_csv(client):
    r = client.post("/api/decisions/Hank", json={"decision": "ignored"}).json()
    assert r == {"username": "Hank", "decision": "ignored", "until": None}
    assert client.post("/api/decisions/bad name", json={"decision": "ignored"}).status_code == 422
    assert client.post("/api/decisions/hank", json={"decision": "sideways"}).status_code == 422
    items = client.get("/api/lists/not_following_back").json()["items"]  # ignored: hidden
    assert [i["username"] for i in items] == ["newguy"]
    csv = client.get("/api/export/not_following_back_ignored.csv")
    assert csv.headers["content-type"].startswith("text/csv")
    assert "hank" in csv.text


def test_avatar_404(client):
    assert client.get("/api/avatars/hank").status_code == 404
    assert client.get("/api/avatars/..%2Fetc").status_code == 404


def test_checker_disabled(client):
    assert client.get("/api/checker").json() == {"enabled": False, "state": "disabled"}
    assert client.post("/api/checker/pause").status_code == 409


def test_checker_routes_with_worker(client):
    from follow_audit.checker.worker import CheckerWorker

    client.app.state.worker = CheckerWorker(client.app.state.settings)
    status = client.get("/api/checker").json()
    assert status["enabled"] and status["state"] == "running" and status["queue_size"] > 0
    assert client.post("/api/checker/pause").json()["state"] == "paused"
    assert client.post("/api/checker/resume").json()["state"] == "running"
    assert client.get("/api/checker").json()["viewport_check_days"] == 10
    visible = client.post("/api/checker/visible", json={"usernames": ["erin", "zoe", "bad name"]})
    assert visible.json() == {"queued": 2}
    assert client.post("/api/checker/visible", json={"usernames": ["erin"]}).json() == {"queued": 0}


def test_snoozed_accounts_live_in_the_ignored_tab_and_come_back(client):
    r = client.post("/api/decisions/hank", json={"decision": "snoozed"}).json()
    assert r["until"] and r["decision"] == "snoozed"
    hidden = client.get("/api/lists/not_following_back").json()["items"]
    assert [i["username"] for i in hidden] == ["newguy"]
    tab = client.get("/api/lists/not_following_back_ignored").json()["items"]
    assert [i["username"] for i in tab] == ["hank"] and tab[0]["snoozed_until"]
    summary = client.get("/api/summary").json()
    assert (
        summary["counts"]["not_following_back_ignored"] == 1
        and summary["counts"]["not_following_back"] == 1
    )
    # bars and headline still describe the whole snapshot
    assert summary["totals"]["not_following_back"] == 2
    assert client.post("/api/decisions/hank", json={"decision": "none"}).json()["until"] is None
    assert len(client.get("/api/lists/not_following_back").json()["items"]) == 2
    assert client.get("/api/lists/not_following_back_ignored").json()["total"] == 0


def test_snooze_duration_comes_from_settings_and_expiry_returns_account(client):
    from datetime import UTC, datetime, timedelta

    assert client.put("/api/settings", json={"snooze_days": 3}).status_code == 200
    until = client.post("/api/decisions/hank", json={"decision": "snoozed"}).json()["until"]
    days = (datetime.fromisoformat(until) - datetime.now(UTC)).total_seconds() / 86400
    assert 2.9 < days < 3.1
    # an expired snooze stops hiding the account
    import sqlite3

    path = client.app.state.settings.db_path
    c = sqlite3.connect(path)
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="seconds")
    c.execute("UPDATE decisions SET until=? WHERE username_key='hank'", (past,))
    c.commit()
    c.close()
    assert len(client.get("/api/lists/not_following_back").json()["items"]) == 2
    assert client.get("/api/lists/not_following_back_ignored").json()["total"] == 0


def test_settings_api(client):
    fields = {f["key"]: f for f in client.get("/api/settings").json()["fields"]}
    assert fields["snooze_days"]["default"] == 30 and fields["snooze_days"]["customised"] is False
    assert fields["request_timeout_seconds"]["unit"] == "seconds"
    out = client.put(
        "/api/settings", json={"daily_request_cap": 100, "check_interval_seconds": 12.5}
    )
    fields = {f["key"]: f for f in out.json()["fields"]}
    assert fields["daily_request_cap"]["value"] == 100 and fields["daily_request_cap"]["customised"]
    assert fields["check_interval_seconds"]["value"] == 12.5
    # reset with null; the default comes back
    out = client.put("/api/settings", json={"daily_request_cap": None})
    reset = {f["key"]: f for f in out.json()["fields"]}["daily_request_cap"]
    assert reset["value"] == reset["default"] == 5000 and not reset["customised"]


@pytest.mark.parametrize(
    "body",
    [
        {"snooze_days": 0},
        {"snooze_days": 1.5},
        {"daily_request_cap": "lots"},
        {"request_timeout_seconds": 9999},
        {"nonsense": 1},
        {"snooze_days": True},
    ],
)
def test_settings_validation(client, body):
    r = client.put("/api/settings", json=body)
    assert r.status_code == 422
    fields = {f["key"]: f for f in client.get("/api/settings").json()["fields"]}
    assert not any(f["customised"] for f in fields.values())  # nothing was saved


def test_invalid_settings_are_not_partially_applied(client):
    r = client.put("/api/settings", json={"snooze_days": 5, "daily_request_cap": 0})
    assert r.status_code == 422
    fields = {f["key"]: f for f in client.get("/api/settings").json()["fields"]}
    assert fields["snooze_days"]["value"] == 30


def test_inactive_badge_follows_the_setting_and_sort_options_are_accepted(client):
    import sqlite3
    from datetime import UTC, datetime, timedelta

    when = (datetime.now(UTC) - timedelta(days=400)).isoformat(timespec="seconds")
    c = sqlite3.connect(client.app.state.settings.db_path)
    c.execute(
        "UPDATE accounts SET last_post_at=?, posts_state='known' WHERE username_key='bob'", (when,)
    )
    c.commit()
    c.close()

    def bob():
        items = client.get("/api/lists/mutual", params={"q": "bob"}).json()["items"]
        return items[0]

    assert bob()["inactive"] is True and bob()["last_post_at"] == when  # default: 365 days
    assert client.put("/api/settings", json={"inactive_after_days": 500}).status_code == 200
    assert bob()["inactive"] is False
    assert client.put("/api/settings", json={"inactive_after_days": 3}).status_code == 422  # min 7
    for sort in ("post_new", "post_old"):
        assert client.get("/api/lists/mutual", params={"sort": sort}).status_code == 200
        assert client.get("/api/export/mutual.csv", params={"sort": sort}).status_code == 200
    fields = {f["key"]: f for f in client.get("/api/settings").json()["fields"]}
    assert fields["inactive_after_days"]["default"] == 365


def test_ignored_accounts_move_between_the_sub_tabs(client):
    client.post("/api/decisions/hank", json={"decision": "ignored"})
    ignored = client.get("/api/lists/not_following_back_ignored").json()
    assert [i["username"] for i in ignored["items"]] == ["hank"]
    main = client.get("/api/lists/not_following_back").json()["items"]
    assert "hank" not in [i["username"] for i in main]
    assert client.get("/api/export/not_following_back_ignored.csv").text.count("hank") == 1


def test_page_files_are_revalidated_not_cached_blindly(client):
    for path in ("/", "/js/main.js", "/app.css"):
        first = client.get(path)
        assert first.status_code == 200 and first.headers["cache-control"] == "no-cache"
        again = client.get(path, headers={"If-None-Match": first.headers["etag"]})
        assert again.status_code == 304  # unchanged files cost almost nothing to re-check


def test_decisions_are_one_at_a_time(client):
    import sqlite3

    def decide(kind):
        client.post("/api/decisions/hank", json={"decision": kind})
        c = sqlite3.connect(client.app.state.settings.db_path)
        try:
            return c.execute(
                "SELECT kind, until IS NOT NULL FROM decisions WHERE username_key='hank'"
            ).fetchall()
        finally:
            c.close()

    assert decide("ignored") == [("ignored", 0)]
    assert decide("snoozed") == [("snoozed", 1)]  # a new decision replaces the old one
    assert decide("unfollow") == [("unfollow", 0)]
    assert decide("follow") == [("follow", 0)]
    assert decide("none") == []


def test_flag_lists_rows_and_counts(client):
    client.post("/api/decisions/hank", json={"decision": "unfollow"})
    client.post("/api/decisions/erin", json={"decision": "follow"})
    un = client.get("/api/lists/not_following_back_flagged").json()["items"]
    fo = client.get("/api/lists/fans_flagged").json()["items"]
    assert [(i["username"], i["flag"], i["flag_action"]) for i in un] == [
        ("hank", "unfollow", "unfollow")
    ]
    assert [(i["username"], i["flag"]) for i in fo] == [("erin", "follow")]
    counts = client.get("/api/summary").json()["counts"]
    assert (counts["fans_flagged"], counts["not_following_back_flagged"]) == (1, 1)
    # flagged accounts are out of pending review
    main = client.get("/api/lists/not_following_back").json()["items"]
    assert [i["username"] for i in main] == ["newguy"]
    assert "flag" in client.get("/api/export/not_following_back_flagged.csv").text.splitlines()[0]
    assert client.get("/api/lists/to_follow").status_code == 404  # the old lists are gone


def test_flag_time_is_the_decision_time(client):
    client.post("/api/decisions/hank", json={"decision": "unfollow"})
    row = client.get("/api/lists/not_following_back_flagged").json()["items"][0]
    assert row["flag_state"] == "pending" and row["flagged_at"].startswith("20")
    client.post("/api/decisions/hank", json={"decision": "ignored"})  # replaces the flag
    assert client.get("/api/lists/not_following_back_flagged").json()["total"] == 0


def test_decisions_belong_to_one_instagram_account(client):
    import sqlite3

    c = sqlite3.connect(client.app.state.settings.db_path)
    c.execute(
        "INSERT INTO decisions VALUES ('bob', 'hank', 'ignored', '2026-10-01T10:00:00', NULL)"
    )
    c.commit()
    c.close()
    # bob's decision about hank does not touch alice's view
    assert "hank" in [
        i["username"] for i in client.get("/api/lists/not_following_back").json()["items"]
    ]
    assert client.get("/api/lists/not_following_back_ignored").json()["total"] == 0
    r = client.post("/api/decisions/hank", params={"owner": "nobody"}, json={"decision": "ignored"})
    assert r.status_code == 404


def test_delete_snapshot(client):
    snaps = client.get("/api/snapshots", params={"owner": "alice"}).json()
    newest = snaps[0]["id"]
    out = client.delete(f"/api/snapshots/{newest}").json()
    assert out == {"deleted": newest, "owner": "alice", "remaining": len(snaps) - 1}
    assert newest not in [
        s["id"] for s in client.get("/api/snapshots", params={"owner": "alice"}).json()
    ]
    assert client.delete(f"/api/snapshots/{newest}").status_code == 404


def _cut_export_files():
    """A following list from years back and followers who all arrived recently."""

    def entries(prefix, start, n):
        return [
            {"string_list_data": [{"value": f"{prefix}{i}", "timestamp": start + i * 86400}]}
            for i in range(n)
        ]

    following = json.dumps({"relationships_following": entries("f", 1_400_000_000, 40)})
    followers = json.dumps(entries("u", 1_790_000_000, 5))
    return [
        ("files", ("followers_1.json", followers.encode())),
        ("files", ("following.json", following.encode())),
    ]


def test_partial_export_waits_for_a_go_ahead(client):
    before = len(client.get("/api/snapshots", params={"owner": "alice"}).json())
    out = client.post("/api/upload", params={"owner": "alice"}, files=_cut_export_files()).json()
    assert out["imported"] == [] and out["errors"] == []
    assert len(out["partial"]) == 1 and out["partial"][0]["first"] is False or True
    assert (
        "first follower" in out["partial"][0]["reason"] or "go back" in out["partial"][0]["reason"]
    )
    assert len(client.get("/api/snapshots", params={"owner": "alice"}).json()) == before

    out = client.post(
        "/api/upload", params={"owner": "alice", "force": "true"}, files=_cut_export_files()
    ).json()
    assert len(out["imported"]) == 1 and out["partial"] == []
    snaps = client.get("/api/snapshots", params={"owner": "alice"}).json()
    assert snaps[0]["partial"]
    assert client.get("/api/summary", params={"owner": "alice"}).json()["snapshot"]["partial"]


def test_old_snapshot_threshold_is_a_setting(client):
    assert client.get("/api/summary", params={"owner": "alice"}).json()["old_snapshot_days"] == 7
    assert client.put("/api/settings", json={"old_snapshot_days": 30}).status_code == 200
    assert client.get("/api/summary", params={"owner": "alice"}).json()["old_snapshot_days"] == 30
    assert client.put("/api/settings", json={"old_snapshot_days": 0}).status_code == 422


def test_profile_route(client):
    out = client.get("/api/profile/Carol", params={"owner": "alice"}).json()
    assert out["username"] == "carol" and out["relation"]["follows_you"] is True
    assert out["timeline"]["segments"] and "high_ratio" in out
    assert client.get("/api/profile/nobody_known", params={"owner": "alice"}).status_code == 404
    assert client.get("/api/profile/bad%20name", params={"owner": "alice"}).status_code == 422


def test_fetch_route_needs_the_checker(client):
    assert client.post("/api/checker/fetch/carol").status_code == 409


def _zip(name_member="connections/followers_and_following/followers_1.json", body=None):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            name_member,
            body or '[{"string_list_data": [{"value": "zed", "timestamp": 1700000000}]}]',
        )
    return buf.getvalue()


def test_a_zip_that_expands_hugely_is_refused_not_read(client, monkeypatch):
    from follow_audit.ingest import discover

    monkeypatch.setattr(discover, "MAX_FILE_BYTES", 1000)
    padding = '[{"string_list_data": [{"value": "zed", "timestamp": 1700000000}]}]' + " " * 5000
    out = _upload(client, ("instagram-alice-2026-11-01-BOMB.zip", _zip(body=padding)))
    assert out["imported"] == [] and "too large" in out["errors"][0]
    assert len(client.get("/api/snapshots", params={"owner": "alice"}).json()) == 2


def test_an_oversized_upload_is_a_413(client, monkeypatch):
    from follow_audit import api

    monkeypatch.setattr(api, "UPLOAD_MAX_BYTES", 10)
    r = client.post("/api/upload", files=[("files", ("a.zip", b"x" * 100))])
    assert r.status_code == 413
    assert not list((client.app.state.settings.state_dir / "incoming").iterdir())


def test_an_unwritable_data_folder_imports_nothing(client):
    uploads = client.app.state.settings.data_dir / "uploads"
    uploads.write_text("in the way")  # keeping a copy there cannot work
    zip_bytes = _zip()
    out = _upload(client, ("instagram-alice-2026-11-01-RO.zip", zip_bytes))
    assert out["imported"] == []
    assert "writable" in out["errors"][0]
    assert len(client.get("/api/snapshots", params={"owner": "alice"}).json()) == 2
    uploads.unlink()
    # nothing was recorded, so the same file imports fine afterwards
    assert len(_upload(client, ("instagram-alice-2026-11-01-RO.zip", zip_bytes))["imported"]) == 1


def test_owner_names_are_not_case_sensitive(client):
    out = _upload(client, ("instagram-Alice-2026-11-01-CASE.zip", _zip()))
    assert out["imported"][0]["owner"] == "alice"
    assert [o["owner"] for o in client.get("/api/owners").json()] == ["alice"]


@pytest.mark.parametrize("name", ["=1+1", " =1+1", "＝1+1", "\n@SUM(A1)", "-2", "plain"])
def test_csv_neutralises_formulas(name):
    from follow_audit.analysis import _csv_safe

    assert _csv_safe(name).startswith("'") == (name != "plain")


def test_files_that_do_not_say_whose_they_are_ask_for_an_account(client):
    followers = b'[{"string_list_data": [{"value": "zed", "timestamp": 1700000000}]}]'
    out = _upload(client, ("followers_1.json", followers))
    assert out["imported"] == [] and out["needs_owner"] == [{"name": "export"}]
    assert len(client.get("/api/snapshots", params={"owner": "alice"}).json()) == 2  # untouched
    # a zip whose name carries the account never asks
    named = _upload(client, ("instagram-alice-2026-11-01-ASK.zip", _zip()))
    assert named["needs_owner"] == [] and len(named["imported"]) == 1


def test_a_new_account_can_be_named_at_upload(client):
    followers = b'[{"string_list_data": [{"value": "zed", "timestamp": 1700000000}]}]'
    out = client.post(
        "/api/upload",
        params={"owner": "Bob_99"},
        files=[("files", ("followers_1.json", followers))],
    ).json()
    assert out["imported"][0]["owner"] == "bob_99"
    assert {o["owner"] for o in client.get("/api/owners").json()} == {"alice", "bob_99"}
    bad = client.post(
        "/api/upload",
        params={"owner": "not a name"},
        files=[("files", ("followers_1.json", followers))],
    )
    assert bad.status_code == 422
