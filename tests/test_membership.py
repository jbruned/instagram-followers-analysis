import json

import pytest

from follow_audit import analysis as an
from follow_audit.ingest import membership
from follow_audit.ingest.discover import Export
from follow_audit.ingest.snapshot import PartialExport, import_export, scan

DAY = 86400
T0 = 1_600_000_000  # follow times: T0, T0 + 1 day, ...


def entry(name, ts):
    return {"string_list_data": [{"value": name, "timestamp": ts}]}


def make_export(tmp_path, date, followers=None, following=None, owner="alice"):
    root = tmp_path / f"instagram-{owner}-{date}-X"
    folder = root / "connections" / "followers_and_following"
    folder.mkdir(parents=True)
    if followers is not None:
        (folder / "followers_1.json").write_text(json.dumps([entry(n, t) for n, t in followers]))
    if following is not None:
        doc = {"relationships_following": [entry(n, t) for n, t in following]}
        (folder / "following.json").write_text(json.dumps(doc))
    return Export(root, is_zip=False)


def crowd(n, start=0):
    return [(f"user{i}", T0 + i * DAY) for i in range(start, start + n)]


def stretches(conn, kind, key):
    return [
        tuple(r)
        for r in conn.execute(
            "SELECT first_snapshot, last_snapshot FROM membership"
            " WHERE kind=? AND username_key=? ORDER BY first_snapshot",
            (kind, key),
        )
    ]


def test_only_changes_are_stored(conn, tmp_path):
    import_export(conn, make_export(tmp_path, "2026-10-01", crowd(100), crowd(50)))
    import_export(conn, make_export(tmp_path, "2026-10-02", crowd(100) + crowd(2, 100), crowd(50)))
    import_export(
        conn, make_export(tmp_path, "2026-10-03", crowd(100) + crowd(2, 100), crowd(51))
    )  # day 3: one more followed account, no follower changes
    # followers: 100 written once plus the 2 who arrived on day 2; following: 50, plus 1
    counts = dict(conn.execute("SELECT kind, COUNT(*) FROM membership GROUP BY kind").fetchall())
    assert counts == {"followers": 102, "following": 51}
    # every snapshot still reads as its own full list
    assert [len(an.load_kind(conn, s, "followers")) for s in (1, 2, 3)] == [100, 102, 102]


def test_unfollowers_close_their_stretch(conn, tmp_path):
    import_export(conn, make_export(tmp_path, "2026-10-01", crowd(100), crowd(50)))
    import_export(conn, make_export(tmp_path, "2026-10-02", crowd(99), crowd(50)))  # user99 left
    assert stretches(conn, "followers", "user99") == [(1, 1)]
    assert "user99" in an.load_kind(conn, 1, "followers")
    assert "user99" not in an.load_kind(conn, 2, "followers")
    events = an.timeline(conn, "alice")[0]["events"]
    assert [e["key"] for e in events["lost_follower"]] == ["user99"]


def test_refollow_starts_a_new_stretch(conn, tmp_path):
    import_export(conn, make_export(tmp_path, "2026-10-01", crowd(100), crowd(50)))
    import_export(conn, make_export(tmp_path, "2026-10-02", crowd(99), crowd(50)))
    back = crowd(99) + [("user99", T0 + 500 * DAY)]
    import_export(conn, make_export(tmp_path, "2026-10-03", back, crowd(50)))
    assert stretches(conn, "followers", "user99") == [(1, 1), (3, None)]


def test_list_missing_from_an_export_is_kept(conn, tmp_path):
    import_export(conn, make_export(tmp_path, "2026-10-01", crowd(100), crowd(50)))
    import_export(conn, make_export(tmp_path, "2026-10-02", following=crowd(40)))
    assert len(an.load_kind(conn, 2, "followers")) == 100  # carried over
    assert len(an.load_kind(conn, 2, "following")) == 40
    assert set(an.kinds_present(conn, 2)) == {"followers", "following"}


def test_export_cut_to_a_date_range_is_held_back(conn, tmp_path):
    import_export(conn, make_export(tmp_path, "2026-10-01", crowd(100), crowd(50)))
    today = crowd(3, 500)  # only people who followed in the last day
    with pytest.raises(PartialExport) as held:
        import_export(conn, make_export(tmp_path, "2026-10-02", today, crowd(50)))
    assert held.value.first is False and held.value.previous == "2026-10-01"
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
    assert len(an.load_kind(conn, 1, "followers")) == 100


def test_partial_export_imported_anyway_cannot_remove_anyone(conn, tmp_path):
    import_export(conn, make_export(tmp_path, "2026-10-01", crowd(100), crowd(50)))
    today = crowd(3, 500)
    out = import_export(conn, make_export(tmp_path, "2026-10-02", today, crowd(50)), force=True)
    assert out["partial"]
    assert len(an.load_kind(conn, 2, "followers")) == 103  # the 100 stay, the 3 are added
    snap = conn.execute("SELECT partial_reason FROM snapshots WHERE id=2").fetchone()
    assert snap["partial_reason"] == out["partial"]
    snapshot = an.summary(conn, an.resolve(conn, "alice", None))["snapshot"]
    assert snapshot["partial"]
    assert (
        snapshot["partial_since"] == "2022-01-26"
    )  # when the first of the three new followers arrived
    assert snapshot["previous_taken_at"] == "2026-10-01"
    assert an.timeline(conn, "alice")[0]["partial"]
    assert an.timeline(conn, "alice")[0]["counts"]["lost_follower"] == 0
    # the next full export is compared with what we really know
    import_export(conn, make_export(tmp_path, "2026-10-03", crowd(99) + today, crowd(50)))
    assert stretches(conn, "followers", "user99") == [(1, 2)]


def test_empty_followers_list_is_held_back(conn, tmp_path):
    import_export(conn, make_export(tmp_path, "2026-10-01", crowd(100), crowd(50)))
    with pytest.raises(PartialExport, match="no followers"):
        import_export(conn, make_export(tmp_path, "2026-10-02", [], crowd(50)))


def test_first_import_is_judged_by_the_following_list(conn, tmp_path):
    # followed accounts since long ago, but the first follower is recent: cut to a date range
    following = [(f"f{i}", T0 + i * DAY) for i in range(40)]
    cut = [(f"u{i}", T0 + (800 + i) * DAY) for i in range(5)]
    export = make_export(tmp_path, "2026-10-01", cut, following)
    with pytest.raises(PartialExport) as held:
        import_export(conn, export)
    assert held.value.first is True and held.value.previous is None
    assert "first follower" in held.value.reason
    out = import_export(conn, export, force=True)
    assert out["partial"]


def test_first_import_with_followers_as_old_as_following_is_fine(conn, tmp_path):
    following = [(f"f{i}", T0 + i * DAY) for i in range(40)]
    followers = [(f"u{i}", T0 + (i + 30) * DAY) for i in range(40)]
    out = import_export(conn, make_export(tmp_path, "2026-10-01", followers, following))
    assert out["partial"] is None


def test_scan_does_not_import_a_partial_export(conn, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    import_export(conn, make_export(data, "2026-10-01", crowd(100), crowd(50)))
    make_export(data, "2026-10-02", crowd(3, 500), crowd(50))
    summary = scan(conn, data)
    assert not summary.imported
    assert any("partial" in e or "followers only go back" in e for e in summary.errors)


def test_few_known_followers_cannot_prove_a_partial_list(conn, tmp_path):
    import_export(conn, make_export(tmp_path, "2026-10-01", crowd(5), crowd(5)))
    import_export(conn, make_export(tmp_path, "2026-10-02", crowd(1, 100), crowd(5)))
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2


def test_losing_some_followers_is_not_partial(conn, tmp_path):
    import_export(conn, make_export(tmp_path, "2026-10-01", crowd(100), crowd(50)))
    scattered = [c for i, c in enumerate(crowd(100)) if i % 5]  # a fifth of them left
    import_export(conn, make_export(tmp_path, "2026-10-02", scattered, crowd(50)))
    assert len(an.load_kind(conn, 2, "followers")) == 80


def test_older_export_is_refused(conn, tmp_path):
    import_export(conn, make_export(tmp_path, "2026-10-05", crowd(100), crowd(50)))
    with pytest.raises(ValueError, match="date order"):
        import_export(conn, make_export(tmp_path, "2026-10-01", crowd(100), crowd(49)))


def test_accounts_do_not_mix(conn, tmp_path):
    import_export(conn, make_export(tmp_path, "2026-10-01", crowd(100), crowd(50)))
    import_export(conn, make_export(tmp_path, "2026-10-02", crowd(30), crowd(5), owner="bob"))
    assert len(an.load_kind(conn, 1, "followers")) == 100
    assert len(an.load_kind(conn, 2, "followers")) == 30


@pytest.mark.parametrize(
    ("new", "known", "refused"),
    [
        ({"a": T0}, {f"k{i}": T0 - i - 1 for i in range(30)}, True),
        ({f"k{i}": T0 - i - 1 for i in range(30)}, {f"k{i}": T0 - i - 1 for i in range(30)}, False),
        ({"a": None}, {f"k{i}": T0 - i - 1 for i in range(30)}, False),  # no dates to judge by
    ],
)
def test_partial_reason(new, known, refused):
    assert (membership.partial_reason(new, known) is not None) is refused


def three_days(conn, tmp_path):
    day1 = crowd(100)
    day2 = [c for c in day1 if c[0] != "user99"] + crowd(2, 100)  # user99 left, two arrived
    day3 = [c for c in day2 if c[0] != "user100"]  # one of the new ones left again
    for n, followers in enumerate((day1, day2, day3), start=1):
        import_export(conn, make_export(tmp_path, f"2026-10-0{n}", followers, crowd(50)))


def reads(conn, sids):
    return {s: set(an.load_kind(conn, s, "followers")) for s in sids}


def test_deleting_a_middle_snapshot_leaves_the_others_as_they_were(conn, tmp_path):
    three_days(conn, tmp_path)
    before = reads(conn, (1, 3))
    assert membership.remove_snapshot(conn, 2)
    assert reads(conn, (1, 3)) == before
    # user100 only existed on day 2, user101 starts on day 3 now
    assert stretches(conn, "followers", "user100") == []
    assert stretches(conn, "followers", "user101") == [(3, None)]
    assert stretches(conn, "followers", "user99") == [(1, 1)]
    # the diff between the remaining two snapshots still names who left
    events = an.timeline(conn, "alice")[0]["events"]
    assert [e["key"] for e in events["lost_follower"]] == ["user99"]
    assert (
        conn.execute("SELECT COUNT(*) FROM snapshot_kinds WHERE snapshot_id=2").fetchone()[0] == 0
    )


def test_deleting_the_first_snapshot_starts_history_at_the_next(conn, tmp_path):
    three_days(conn, tmp_path)
    before = reads(conn, (2, 3))
    membership.remove_snapshot(conn, 1)
    assert reads(conn, (2, 3)) == before
    assert stretches(conn, "followers", "user99") == []
    assert stretches(conn, "followers", "user5") == [(2, None)]


def test_deleting_the_latest_snapshot_reopens_nothing_it_added(conn, tmp_path):
    three_days(conn, tmp_path)
    before = reads(conn, (1, 2))
    membership.remove_snapshot(conn, 3)
    assert reads(conn, (1, 2)) == before
    assert stretches(conn, "followers", "user100") == [(2, None)]  # back, as on day 2
    assert "user100" in an.load_kind(conn, 2, "followers")
    assert an.snapshots(conn, "alice")[0]["id"] == 2


def test_deleting_the_only_snapshot_empties_the_account(conn, tmp_path):
    import_export(conn, make_export(tmp_path, "2026-10-01", crowd(100), crowd(50)))
    membership.remove_snapshot(conn, 1)
    assert conn.execute("SELECT COUNT(*) FROM membership").fetchone()[0] == 0
    assert an.owners(conn) == []


def test_deleted_export_is_not_brought_back_by_a_scan(conn, tmp_path):
    export = make_export(tmp_path, "2026-10-01", crowd(100), crowd(50))
    import_export(conn, export)
    membership.remove_snapshot(conn, 1)
    assert import_export(conn, export) is None  # a scan: skipped
    assert import_export(conn, export, uploaded=True) is not None  # chosen by hand: imported
    assert conn.execute("SELECT COUNT(*) FROM deleted_exports").fetchone()[0] == 0
