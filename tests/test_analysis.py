import pytest

from follow_audit import analysis as an
from follow_audit.ingest.snapshot import scan


@pytest.fixture
def two(make_data_dir, conn):
    scan(conn, make_data_dir("json_zip", "json_folder"))
    return conn


def names(conn, sid, name, **kw):
    return {r["key"] for r in an.build_rows(conn, sid, name, **kw)}


def test_set_analysis_latest(two):
    snap = an.resolve(two, None, None)
    assert snap["taken_at"] == "2026-10-09"
    sid = snap["id"]
    # following: bob carol hank newguy; followers: bob carol erin zoe
    assert names(two, sid, "not_following_back") == {"hank", "newguy"}
    assert names(two, sid, "fans") == {"erin", "zoe"}
    assert names(two, sid, "mutual") == {"bob", "carol"}
    assert names(two, sid, "pending_sent") == {"ivan", "judy"}
    assert names(two, sid, "unfollowed") == {"old_friend", "gina"}
    assert names(two, sid, "mutual_close_friends") == {"bob"}
    assert names(two, sid, "pending_unavailable") == set()
    s = an.summary(two, snap)
    assert s["headline"] == "2 of the 4 accounts you follow don't follow you back."
    assert "pending_sent" in s["kinds_present"]


def test_rows_enrichment_sort_and_filters(two):
    sid = an.resolve(two, None, None)["id"]
    rows = an.build_rows(two, sid, "not_following_back", sort="az")
    assert [r["username"] for r in rows] == ["hank", "newguy"]
    assert an.build_rows(two, sid, "not_following_back", sort="newest")[0]["username"] == "newguy"
    assert names(two, sid, "mutual", q="BO") == {"bob"}
    mark(two, "hank", "ignored")
    assert names(two, sid, "not_following_back") == {"newguy"}  # ignored accounts are hidden
    assert names(two, sid, "not_following_back_ignored") == {"hank"}
    bob = an.build_rows(two, sid, "mutual", q="bob")[0]
    assert bob["close_friend"] and bob["full_name"] is None and bob["status"] == "unchecked"


def test_unavailable_list(two):
    sid = an.resolve(two, None, None)["id"]
    two.execute("UPDATE accounts SET status='unavailable' WHERE username_key='hank'")
    two.execute("UPDATE accounts SET status='possibly_unavailable' WHERE username_key='zoe'")
    two.execute("UPDATE accounts SET status='live' WHERE username_key='bob'")
    assert names(two, sid, "unfollowed_unavailable") == {"hank", "zoe"}


def test_history_diff_with_refollow(two):
    tl = an.timeline(two, "alice")
    assert len(tl) == 1
    ev = tl[0]["events"]
    assert tl[0]["from_date"] == "2024-03-02" and tl[0]["to_date"] == "2026-10-09"
    assert {e["key"] for e in ev["lost_follower"]} == {"dave", "frank"}
    assert {e["key"] for e in ev["new_follower"]} == {"zoe"}
    assert {e["key"] for e in ev["you_unfollowed"]} == {"gina"}
    assert {e["key"] for e in ev["you_followed"]} == {"newguy"}
    assert {e["key"] for e in ev["refollow_follower"]} == {"carol"}
    assert {e["key"] for e in ev["refollow_following"]} == {"carol"}
    assert {e["label"] for e in ev["lost_follower"]} == {"unfollowed you"}


def test_lost_follower_cross_reference_and_rename(two):
    two.execute("UPDATE accounts SET status='unavailable' WHERE username_key='dave'")
    two.execute("UPDATE accounts SET ig_user_id='42' WHERE username_key IN ('frank','zoe')")
    ev = an.timeline(two, "alice")[0]["events"]
    labels = {e["key"]: e["label"] for e in ev["lost_follower"]}
    assert labels == {"dave": "account unavailable"}
    assert (
        ev["renamed"][0]["username"] == "zoe" and ev["renamed"][0]["previous_username"] == "frank"
    )
    assert ev["new_follower"] == []


def test_owners_and_unnamed_owner(make_data_dir, conn):
    scan(conn, make_data_dir("json_zip", "html_export"))
    owners = {o["owner"]: o["snapshots"] for o in an.owners(conn)}
    assert owners == {"alice": 1, "": 1}
    assert an.resolve(conn, "", None)["owner"] == ""
    with pytest.raises(an.NotFound):
        an.resolve(conn, "nobody", None)
    assert an.timeline(conn, "") == []


def test_csv_escapes_formulas():
    rows = [
        {
            "username": "a",
            "full_name": "=HYPERLINK(x)",
            "ts": 0,
            "status": "live",
            "is_private": None,
            "is_verified": True,
            "ignored": False,
        }
    ]
    out = an.rows_to_csv(rows)
    assert "'=HYPERLINK(x)" in out


def test_unavailable_accounts_leave_other_lists_and_totals(two):
    snap = an.resolve(two, None, None)
    sid = snap["id"]
    before = an.summary(two, snap)
    assert before["following"] == 4 and before["totals"]["not_following_back"] == 2
    two.execute("UPDATE accounts SET status='unavailable' WHERE username_key='hank'")
    two.execute("UPDATE accounts SET status='possibly_unavailable' WHERE username_key='zoe'")
    assert names(two, sid, "not_following_back") == {"newguy"}
    assert names(two, sid, "fans") == {"erin"}
    assert names(two, sid, "unfollowed_unavailable") == {"hank", "zoe"}  # still listed here
    after = an.summary(two, snap)
    assert after["following"] == 3 and after["followers"] == 3
    assert after["totals"] == {"mutual": 2, "not_following_back": 1, "fans": 1}
    assert after["counts"]["unfollowed_unavailable"] == 2
    assert after["headline"] == "1 of the 3 accounts you follow doesn't follow you back."
    # a live result brings the account back
    two.execute("UPDATE accounts SET status='live' WHERE username_key='hank'")
    assert names(two, sid, "not_following_back") == {"hank", "newguy"}


def test_an_unavailable_account_shows_in_the_unavailable_tab_whatever_you_decided(two):
    sid = an.resolve(two, None, None)["id"]
    two.execute("UPDATE accounts SET status='unavailable' WHERE username_key='hank'")
    mark(two, "hank", "snoozed", until=FAR)
    assert names(two, sid, "unfollowed_unavailable") == {"hank"}
    assert names(two, sid, "not_following_back_ignored") == set()  # it lives in Unavailable now


def _set_post(conn, key, days_ago, state="known"):
    from datetime import UTC, datetime, timedelta

    when = (
        None
        if days_ago is None
        else (datetime.now(UTC) - timedelta(days=days_ago)).isoformat(timespec="seconds")
    )
    conn.execute(
        "UPDATE accounts SET last_post_at=?, posts_state=? WHERE username_key=?", (when, state, key)
    )


def test_sort_by_last_post_and_inactivity_flags(two):
    sid = an.resolve(two, None, None)["id"]
    _set_post(two, "bob", 10)
    _set_post(two, "carol", 400)
    _set_post(two, "erin", None)  # account with no posts at all
    # zoe: never looked at (posts_state stays NULL)
    for key in ("bob", "carol", "erin", "zoe"):  # any list will do: use the Unavailable one
        two.execute("UPDATE accounts SET status='unavailable' WHERE username_key=?", (key,))

    def order(sort):
        return [
            r["key"]
            for r in an.build_rows(two, sid, "unfollowed_unavailable", sort=sort, inactive_days=365)
        ]

    assert order("post_new") == ["bob", "carol", "erin", "zoe"]  # unknown always last
    assert order("post_old") == ["erin", "carol", "bob", "zoe"]  # no posts = most inactive
    rows = {
        r["key"]: r for r in an.build_rows(two, sid, "unfollowed_unavailable", inactive_days=365)
    }
    assert [rows[k]["inactive"] for k in ("bob", "carol", "erin", "zoe")] == [
        False,
        True,
        False,
        False,
    ]
    assert rows["erin"]["no_posts"] and not rows["zoe"]["no_posts"]
    assert rows["zoe"]["last_post_at"] is None and not rows["zoe"]["no_posts"]
    # the threshold is applied when asked for, and not at all when there is none
    assert not an.build_rows(two, sid, "unfollowed_unavailable", q="carol", inactive_days=500)[0][
        "inactive"
    ]
    assert not an.build_rows(two, sid, "unfollowed_unavailable", q="carol")[0]["inactive"]


def test_csv_has_last_post_date(two):
    sid = an.resolve(two, None, None)["id"]
    _set_post(two, "bob", 10)
    rows = an.build_rows(two, sid, "mutual", q="bob")
    out = an.rows_to_csv(rows).splitlines()
    columns = dict(zip(out[0].split(","), out[1].split(","), strict=True))
    assert columns["last_post"] == rows[0]["last_post_at"][:10] and "flag" in columns


# ------------------------------------------------------------------ decisions


def mark(conn, key, kind, *, at="2026-10-01T10:00:00+00:00", until=None, owner="alice"):
    conn.execute(
        "INSERT OR REPLACE INTO decisions(owner, username_key, kind, decided_at, until)"
        " VALUES (?,?,?,?,?)",
        (owner, key, kind, at, until),
    )


FAR = "2999-01-01T00:00:00+00:00"


def test_flagged_accounts_have_their_own_lists_and_leave_the_pending_ones(two):
    sid = an.resolve(two, None, None)["id"]
    mark(two, "hank", "unfollow")  # you follow hank, who doesn't follow back
    mark(two, "erin", "follow")  # erin follows you, you don't follow back
    assert names(two, sid, "not_following_back_flagged") == {"hank"}
    assert names(two, sid, "fans_flagged") == {"erin"}
    assert names(two, sid, "not_following_back") == {"newguy"}
    assert names(two, sid, "fans") == {"zoe"}
    assert names(two, sid, "not_following_back_ignored") == set()
    # a flag of the other kind does not belong to the tab
    mark(two, "newguy", "follow")
    assert names(two, sid, "not_following_back_flagged") == {"hank"}
    row = an.build_rows(two, sid, "not_following_back_flagged")[0]
    assert row["flag"] == "unfollow" and row["ignored"] is False and row["snoozed_until"] is None


def test_ignored_includes_snoozed_in_each_tab(two):
    sid = an.resolve(two, None, None)["id"]
    mark(two, "hank", "ignored")  # ignored for good
    mark(two, "newguy", "snoozed", until=FAR)  # ignored for a while
    mark(two, "zoe", "ignored")
    assert names(two, sid, "not_following_back") == set()
    assert names(two, sid, "not_following_back_ignored") == {"hank", "newguy"}
    assert names(two, sid, "fans_ignored") == {"zoe"}
    assert names(two, sid, "fans") == {"erin"}
    assert names(two, sid, "not_following_back_flagged") == set()


def test_decisions_do_not_hide_accounts_in_the_other_tabs(two):
    sid = an.resolve(two, None, None)["id"]
    mark(two, "ivan", "ignored")  # ivan and judy are in "requests sent"
    mark(two, "judy", "snoozed", until=FAR)
    mark(two, "bob", "ignored")  # bob is mutual
    assert names(two, sid, "pending_sent") == {"ivan", "judy"}
    assert "bob" in names(two, sid, "mutual")
    row = {r["key"]: r for r in an.build_rows(two, sid, "pending_sent")}
    assert row["ivan"]["ignored"] and row["ivan"]["snoozed_until"] is None
    assert not row["judy"]["ignored"] and row["judy"]["snoozed_until"] == FAR


def test_flag_action_depends_on_how_you_are_connected(two):
    sid = an.resolve(two, None, None)["id"]
    by = lambda name: {r["key"]: r["flag_action"] for r in an.build_rows(two, sid, name)}  # noqa: E731
    assert set(by("not_following_back").values()) == {"unfollow"}  # you follow them
    assert set(by("mutual").values()) == {"unfollow"}
    assert set(by("fans").values()) == {"follow"}  # they follow you
    assert set(by("pending_sent").values()) == {None}  # nothing to follow or unfollow yet


def test_tab_counts_describe_each_sub_tab(two):
    snap = an.resolve(two, None, None)
    mark(two, "hank", "unfollow")
    mark(two, "erin", "ignored")
    mark(two, "newguy", "snoozed", until=FAR)
    s = an.summary(two, snap)
    c = s["counts"]
    assert (c["not_following_back"], c["not_following_back_flagged"]) == (0, 1)
    assert (c["not_following_back_ignored"], c["fans"], c["fans_ignored"]) == (1, 1, 1)
    assert (c["unfollowed"], c["unfollowed_unavailable"]) == (2, 0)
    assert s["totals"]["not_following_back"] == 2  # the diagram still describes the whole snapshot
    assert s["snapshot_count"] == 2


def test_a_flagged_account_that_is_unavailable_moves_to_the_unavailable_tab(two):
    sid = an.resolve(two, None, None)["id"]
    two.execute("UPDATE accounts SET status='unavailable' WHERE username_key='hank'")
    mark(two, "hank", "unfollow")
    assert names(two, sid, "not_following_back_flagged") == set()
    assert names(two, sid, "unfollowed_unavailable") == {"hank"}


def test_date_line_wording(two):
    sid = an.resolve(two, None, None)["id"]
    context = lambda name, i=0: an.build_rows(two, sid, name)[i]["context"]  # noqa: E731
    assert context("not_following_back") == "Followed"
    assert context("mutual") == "Followed"
    assert context("fans") == "Followed you"
    assert context("pending_sent") == "Requested"
    assert context("unfollowed") == "Unfollowed"  # reported by the export


def test_unfollowed_sub_tabs_never_repeat_an_account(two):
    snaps = an.snapshots(two, "alice")  # newest first
    latest, older = snaps[0]["id"], snaps[1]["id"]
    # hank is still followed (pending, in Not following you back); gina left your following list
    # and Instagram lists her as recently unfollowed too (one row); old_friend is only in the export
    mark(two, "hank", "unfollow")
    mark(two, "gina", "unfollow", at="2026-09-20T10:00:00+00:00")
    rows = an.build_rows(two, latest, "unfollowed")
    assert sorted(r["key"] for r in rows) == ["gina", "old_friend"]  # once each
    app = {r["key"]: r for r in rows if r["flag"]}
    recent = {r["key"]: r for r in rows if not r["flag"]}
    assert set(app) == {"gina"} and set(recent) == {"old_friend"}
    assert app["gina"]["flag_state"] == "done" and app["gina"]["flag"] == "unfollow"
    assert app["gina"]["flagged_at"] == "2026-09-20T10:00:00+00:00"
    assert app["gina"]["context"] == "Unfollowed"  # Instagram gave the date
    assert recent["old_friend"]["flag"] is None and recent["old_friend"]["flag_state"] is None
    # looking at the older export, where gina is still followed, she is still to do
    assert names(two, older, "not_following_back_flagged") >= {"gina"}
    # an unavailable account moves to Disabled accounts, from either source
    two.execute(
        "UPDATE accounts SET status='unavailable' WHERE username_key IN ('gina','old_friend')"
    )
    assert names(two, latest, "unfollowed_unavailable") == {"gina", "old_friend"}
    assert names(two, latest, "unfollowed") == set()


def test_follow_flag_is_done_once_you_follow_the_account(two):
    sid = an.resolve(two, None, None)["id"]
    mark(two, "erin", "follow")  # follows you, you don't follow back
    mark(two, "bob", "follow")  # you follow bob now
    pending = an.build_rows(two, sid, "fans_flagged")
    assert [(r["key"], r["flag_state"]) for r in pending] == [("erin", "pending")]
    bob = an.build_rows(two, sid, "mutual", q="bob")[0]
    assert bob["flag_state"] == "done"  # it shows in Mutual, with its tag
    assert an.summary(two, an.resolve(two, None, None))["counts"]["fans_flagged"] == 1


def test_unflagged_rows_have_no_flag_state(two):
    sid = an.resolve(two, None, None)["id"]
    row = an.build_rows(two, sid, "mutual")[0]
    assert row["flag"] is None and row["flag_state"] is None and row["flagged_at"] is None


def test_unavailable_requests_and_other_unavailable_accounts_are_listed_once(two):
    sid = an.resolve(two, None, None)["id"]
    for key in ("ivan", "hank", "bob"):  # a request, someone you follow, a mutual
        two.execute("UPDATE accounts SET status='unavailable' WHERE username_key=?", (key,))
    assert names(two, sid, "pending_unavailable") == {"ivan"}
    assert names(two, sid, "unfollowed_unavailable") == {"hank", "bob"}
    assert names(two, sid, "pending_sent") == {"judy"}
    assert "bob" not in names(two, sid, "mutual")


@pytest.mark.parametrize(
    ("followers", "following", "ratio", "expected"),
    [
        (50_000, 400, 10, True),
        (4_000, 400, 10, False),  # exactly 10 times is not "more than"
        (50_000, 0, 10, True),  # follows nobody
        (900, 1, 10, False),  # too few followers to count
        (None, 400, 10, False),  # never fetched
        (50_000, 400, None, False),  # switched off
    ],
)
def test_high_ratio(followers, following, ratio, expected):
    assert an.high_ratio(followers, following, ratio) is expected


def test_rows_carry_counts_and_the_ratio_mark(two):
    two.execute(
        "UPDATE accounts SET followers_count=50000, following_count=300, status='live'"
        " WHERE username_key='hank'"
    )
    sid = an.resolve(two, None, None)["id"]
    rows = {r["key"]: r for r in an.build_rows(two, sid, "not_following_back", high_ratio_at=10)}
    assert rows["hank"]["high_ratio"] and rows["hank"]["followers_count"] == 50000
    assert not rows["newguy"]["high_ratio"] and rows["newguy"]["followers_count"] is None


def test_profile_of_an_account(two):
    snap = an.resolve(two, None, None)
    p = an.profile(two, snap, "Zoe")  # any capitalisation
    assert p["username"] == "zoe" and p["relation"] == {
        "following": False,
        "follows_you": True,
        "pending_sent": False,
        "pending_received": False,
        "follows_you_since": 1760000000,
        "following_since": None,
        "you_unfollowed_at": None,
    }
    assert [s["state"] for s in p["timeline"]["segments"]] == ["none", "fan"]
    assert p["timeline"]["dates"] == ["2024-03-02", "2026-10-09"]
    assert p["timeline"]["events"] == [
        {"kind": "they_followed", "from": "2024-03-02", "to": "2026-10-09", "ts": 1760000000}
    ]
    assert p["incomplete"] is True  # never fetched
    assert an.profile(two, snap, "nobody_here") is None


def test_profile_of_an_unavailable_account_is_complete(two):
    two.execute("UPDATE accounts SET status='unavailable' WHERE username_key='hank'")
    assert an.profile(two, an.resolve(two, None, None), "hank")["incomplete"] is False


def test_requests_you_received_have_their_own_list_and_share_the_unavailable_one(two):
    snap = an.resolve(two, None, None)
    sid = snap["id"]
    for key in ("zoe", "erin"):
        two.execute(
            "INSERT INTO membership(owner, kind, username_key, username, ts, first_snapshot)"
            " VALUES (?, 'pending_received', ?, ?, 1760000000, ?)",
            (snap["owner"], key, key, sid),
        )
    assert names(two, sid, "pending_received") == {"zoe", "erin"}
    assert names(two, sid, "pending_sent") == {"ivan", "judy"}  # unchanged
    two.execute("UPDATE accounts SET status='unavailable' WHERE username_key IN ('zoe', 'ivan')")
    assert names(two, sid, "pending_received") == {"erin"}
    assert names(two, sid, "pending_unavailable") == {"zoe", "ivan"}  # sent and received together
    assert "zoe" not in names(two, sid, "unfollowed_unavailable")  # a request has its own list
    assert an.profile(two, snap, "erin")["relation"]["pending_received"] is True
    assert an.summary(two, snap)["counts"]["pending_received"] == 1
