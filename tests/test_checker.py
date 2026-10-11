import json
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from follow_audit import db
from follow_audit.checker.client import IG_EPOCH_MS, parse_profile_page
from follow_audit.checker.worker import CACHE_VERSION, DAY, CheckerWorker
from follow_audit.config import Settings
from follow_audit.ingest.snapshot import scan

PIC = "https://scontent.cdninstagram.com/v/pic.jpg?x=1&y=2"


def make_pk(when):
    """A post ID that decodes to ``when`` (Instagram IDs carry the creation time)."""
    return ((int(when.timestamp() * 1000) - IG_EPOCH_MS) << 23) | 4242


def live_html(name="Some Name", private=False, verified=True, uid="123456789", posts=(), og=None):
    post_json = "".join(
        f'{{"pk":"{make_pk(when)}","code":"CODE{i:07d}","accessibility_caption":"x"}},'
        for i, when in enumerate(posts)
    )
    meta = f'<meta property="og:description" content="{og}" />' if og else ""
    user = (
        f'{{"id":"{uid}","username":"USER","full_name":"{name}",'
        f'"profile_pic_url":"https:\\/\\/scontent.cdninstagram.com\\/v\\/pic.jpg?x=1\\u0026y=2",'
        f'"is_private":{str(private).lower()},"is_verified":{str(verified).lower()}}}'
    )
    return f'<html><title>x</title>{meta}"pageID":"profilePage" {post_json}{user}</html>'


ERROR_HTML = '<html><title>Instagram</title>"pageID":"httpErrorPage"</html>'
UNKNOWN_HTML = "<html><title>Instagram</title>something new</html>"


def bump(worker, username):
    """Put an account at the front of the queue, as a viewport visit would."""
    conn = worker._conn()
    try:
        with conn:
            conn.execute("UPDATE accounts SET priority_bump=1 WHERE username_key=?", (username,))
    finally:
        conn.close()


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def env(make_data_dir, tmp_path):
    settings = Settings(
        data_dir=make_data_dir("json_folder"),
        state_dir=tmp_path / "state",
        checker_enabled=False,
        check_interval_seconds=0,
        check_jitter_seconds=0,
    )
    db.init_db(settings.db_path)
    conn = db.connect(settings.db_path)
    scan(conn, settings.data_dir)
    conn.close()
    clock = Clock()
    return settings, clock, CheckerWorker(settings, clock=clock)


def account(settings, key):
    conn = db.connect(settings.db_path)
    try:
        return dict(conn.execute("SELECT * FROM accounts WHERE username_key=?", (key,)).fetchone())
    finally:
        conn.close()


def requests_made(settings):
    conn = db.connect(settings.db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    finally:
        conn.close()


def state(settings, key):
    conn = db.connect(settings.db_path)
    try:
        row = conn.execute("SELECT value FROM checker_state WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None
    finally:
        conn.close()


def mock_profiles(router, mapping, default=None):
    """Route /<user>/ to per-user bodies; the avatar URL returns a tiny jpeg."""

    default = live_html() if default is None else default

    def handler(request):
        user = request.url.path.strip("/")
        body = mapping.get(user, default)
        if isinstance(body, httpx.Response):
            return body
        return httpx.Response(200, text=body.replace('"username":"USER"', f'"username":"{user}"'))

    router.get(url__regex=r"https://www\.instagram\.com/[^/]+/$").mock(side_effect=handler)
    router.get(url__regex=r"https://scontent\.cdninstagram\.com/.*").mock(
        return_value=httpx.Response(
            200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"}
        )
    )


def profile_calls(router):
    return [
        c.request.url.path.strip("/")
        for c in router.calls
        if c.request.url.host == "www.instagram.com"
    ]


# ------------------------------------------------------------------ parsing


def test_parse_live_page():
    r = parse_profile_page("user", 200, "https://www.instagram.com/user/", live_html(private=True))
    assert r.outcome == "live" and r.full_name == "Some Name"
    assert r.is_private is True and r.is_verified is True
    assert r.ig_user_id == "123456789"
    assert r.picture_url == PIC  # JSON escapes removed


@pytest.mark.parametrize(
    ("status", "url", "body", "outcome"),
    [
        (200, "https://www.instagram.com/u/", ERROR_HTML, "missing"),
        (404, "https://www.instagram.com/u/", "", "rate_limited"),
        (302, "https://www.instagram.com/u/", "", "rate_limited"),
        (429, "https://www.instagram.com/u/", "", "rate_limited"),
        (401, "https://www.instagram.com/u/", "", "rate_limited"),
        (200, "https://www.instagram.com/accounts/login/?next=/u/", "", "rate_limited"),
        (200, "https://www.instagram.com/u/", "Please wait a few minutes", "rate_limited"),
        (200, "https://www.instagram.com/u/", UNKNOWN_HTML, "error"),
        (500, "https://www.instagram.com/u/", "", "rate_limited"),
    ],
)
def test_parse_classification(status, url, body, outcome):
    assert parse_profile_page("u", status, url, body).outcome == outcome


# ------------------------------------------------------------------ worker


async def test_live_lookup_stores_info_and_avatar(env, respx_mock):
    settings, clock, worker = env
    mock_profiles(respx_mock, {})
    assert await worker.run_once() == "live"
    a = account(settings, "hank")  # first in tier 1
    assert a["status"] == "live" and a["full_name"] == "Some Name" and a["is_verified"] == 1
    assert a["ig_user_id"] == "123456789"
    assert (settings.avatar_dir / "hank.jpg").read_bytes().startswith(b"\xff\xd8")
    assert a["avatar_path"] == "avatars/hank.jpg"
    assert a["next_check_at"] > a["checked_at"]
    assert requests_made(settings) == 2  # page + avatar both count


async def test_live_lookup_stores_follower_counts(env, respx_mock):
    settings, clock, worker = env
    og = "27K Followers, 228 Following, 2,395 Posts - See Instagram"
    mock_profiles(respx_mock, {"hank": live_html(og=og)})
    await worker.run_once()
    a = account(settings, "hank")
    assert (a["followers_count"], a["following_count"]) == (27000, 228)
    # a later page that doesn't show them keeps what was known
    clock.advance(30 * 86400)
    mock_profiles(respx_mock, {"hank": live_html()})
    bump(worker, "hank")
    await worker.run_once()
    assert account(settings, "hank")["followers_count"] == 27000


async def test_queue_priority_order(env, respx_mock):
    settings, clock, worker = env
    mock_profiles(respx_mock, {})
    conn = db.connect(settings.db_path)
    conn.execute(
        "INSERT INTO decisions VALUES ('alice', 'newguy', 'ignored', '2026-10-01T10:00:00', NULL)"
    )
    conn.commit()
    conn.close()
    for _ in range(7):
        await worker.run_once()
        clock.advance(30)
    # tier 1 (not following back, kept skipped), 2 (requests sent), 3 (fans), 4 (mutual)
    assert profile_calls(respx_mock) == ["hank", "ivan", "judy", "erin", "zoe", "bob", "carol"]
    assert await worker.run_once() == "idle"
    assert worker.status()["state"] == "idle"


async def test_missing_needs_canary_then_two_spaced_results(env, respx_mock):
    settings, clock, worker = env
    mock_profiles(respx_mock, {"hank": ERROR_HTML, "instagram": live_html()})
    assert await worker.run_once() == "missing"
    assert profile_calls(respx_mock) == ["hank", "instagram"]  # canary ran
    a = account(settings, "hank")
    assert a["status"] == "possibly_unavailable" and a["unavailable_hits"] == 1

    clock.advance(3600)  # too soon: a forced re-check must not confirm
    bump(worker, "hank")
    await worker.run_once()
    assert account(settings, "hank")["status"] == "possibly_unavailable"

    clock.advance(3 * 86400)
    bump(worker, "hank")
    await worker.run_once()
    a = account(settings, "hank")
    assert a["status"] == "unavailable" and a["unavailable_hits"] == 2


async def test_live_result_clears_possible_unavailability(env, respx_mock):
    settings, clock, worker = env
    mapping = {"hank": ERROR_HTML}
    mock_profiles(respx_mock, mapping)
    await worker.run_once()
    assert account(settings, "hank")["status"] == "possibly_unavailable"
    mapping["hank"] = live_html()
    clock.advance(4 * 86400)
    bump(worker, "hank")
    await worker.run_once()
    a = account(settings, "hank")
    assert (
        a["status"] == "live" and a["unavailable_hits"] == 0 and a["first_unavailable_at"] is None
    )


async def test_failed_canary_is_a_cooldown_not_unavailable(env, respx_mock):
    settings, clock, worker = env
    mock_profiles(respx_mock, {}, default=ERROR_HTML)  # everything looks "missing"
    assert await worker.run_once() == "cooldown"
    assert account(settings, "hank")["status"] == "unchecked"
    assert state(settings, "cooldown_until") > clock()
    assert await worker.run_once() == "cooldown"
    assert len(profile_calls(respx_mock)) == 2  # no new requests while cooling down


@pytest.mark.parametrize("status", [404, 500, 503, 302])
async def test_any_non_200_pauses_the_checker_and_leaves_the_account_alone(env, respx_mock, status):
    settings, clock, worker = env
    mock_profiles(respx_mock, {}, default=httpx.Response(status))
    before = account(settings, "hank")
    assert await worker.run_once() == "rate_limited"
    assert account(settings, "hank") == before  # not unavailable, not an error: just not checked
    assert state(settings, "cooldown_until") > clock()
    assert await worker.run_once() == "cooldown"
    assert len(profile_calls(respx_mock)) == 1


async def test_outdated_fetches_go_after_everything_unchecked_or_due(env, respx_mock):
    settings, clock, worker = env
    conn = db.connect(settings.db_path)
    keys = [r[0] for r in conn.execute("SELECT username_key FROM accounts ORDER BY username_key")]
    old, fresh, due = keys[0], keys[1], keys[2]
    later = "2099-01-01T00:00:00+00:00"
    with conn:
        conn.execute("UPDATE accounts SET status='unchecked'")
        # live and not expired, but fetched with an older version
        conn.execute(
            "UPDATE accounts SET status='live', next_check_at=?, checked_at='2026-01-01T00:00:00+00:00',"
            " fetch_version=0 WHERE username_key=?",
            (later, old),
        )
        # live, not expired, current version: not due at all
        conn.execute(
            "UPDATE accounts SET status='live', next_check_at=?, fetch_version=?"
            " WHERE username_key=?",
            (later, CACHE_VERSION, fresh),
        )
        # live and expired, current version
        conn.execute(
            "UPDATE accounts SET status='live', next_check_at='2000-01-01T00:00:00+00:00',"
            " checked_at='2026-06-01T00:00:00+00:00', fetch_version=? WHERE username_key=?",
            (CACHE_VERSION, due),
        )
    order = [d["key"] for d in worker.queue(conn)["due"]]
    conn.close()
    assert fresh not in order
    assert order[-1] == old  # behind the unchecked ones and the expired one
    assert order.index(due) < order.index(old)


async def test_a_live_fetch_records_the_cache_version(env, respx_mock):
    settings, clock, worker = env
    mock_profiles(respx_mock, {})
    assert await worker.run_once() == "live"
    assert account(settings, "hank")["fetch_version"] == CACHE_VERSION


async def test_requests_older_than_a_day_are_forgotten(env, respx_mock):
    settings, clock, worker = env
    mock_profiles(respx_mock, {})
    await worker.run_once()
    assert requests_made(settings) == 2
    clock.advance(DAY + 1)
    await worker.run_once()
    assert requests_made(settings) == 2  # the old two were deleted, two new ones added


async def test_rate_limit_cooldown_doubles_then_resets(env, respx_mock):
    settings, clock, worker = env
    mapping = {}
    mock_profiles(respx_mock, mapping, default=httpx.Response(429))
    waits = []
    for _ in range(6):
        assert await worker.run_once() == "rate_limited"
        waits.append(state(settings, "cooldown_until") - clock())
        assert await worker.run_once() == "cooldown"
        clock.advance(waits[-1] + 1)
    assert waits == [900, 1800, 3600, 7200, 14400, 14400]  # 15 min doubling, capped at 4 h
    assert len(profile_calls(respx_mock)) == 6
    respx_mock.reset()
    mock_profiles(respx_mock, {})
    assert await worker.run_once() == "live"
    assert state(settings, "backoff_level") == 0


async def test_cooldown_survives_restart(env, respx_mock):
    settings, clock, worker = env
    mock_profiles(respx_mock, {}, default=httpx.Response(429))
    await worker.run_once()
    fresh = CheckerWorker(settings, clock=clock)  # simulated restart
    assert await fresh.run_once() == "cooldown"


async def test_ambiguous_response_is_error_and_retried_with_backoff(env, respx_mock):
    settings, clock, worker = env
    mock_profiles(respx_mock, {"hank": UNKNOWN_HTML})
    assert await worker.run_once() == "error"
    a = account(settings, "hank")
    assert a["status"] == "error" and a["attempts"] == 1 and "unrecognised" in a["last_error"]
    assert a["unavailable_hits"] == 0
    first_retry = a["next_check_at"]
    bump(worker, "hank")
    await worker.run_once()
    assert account(settings, "hank")["attempts"] == 2
    assert account(settings, "hank")["next_check_at"] > first_retry


async def test_daily_cap_rolling_window(make_data_dir, tmp_path, respx_mock):
    settings = Settings(
        data_dir=make_data_dir("json_folder"),
        state_dir=tmp_path / "s",
        checker_enabled=False,
        daily_request_cap=3,
    )
    db.init_db(settings.db_path)
    conn = db.connect(settings.db_path)
    scan(conn, settings.data_dir)
    conn.close()
    clock = Clock()
    worker = CheckerWorker(settings, clock=clock)
    mock_profiles(respx_mock, {})
    assert await worker.run_once() == "live"  # 2 requests (page + avatar)
    clock.advance(60)
    assert await worker.run_once() == "live"  # 3rd request is the page; avatar is skipped at cap
    assert await worker.run_once() == "cap"
    assert worker.status()["state"] == "cap_reached"
    clock.advance(86400 + 1)
    assert await worker.run_once() == "live"


async def test_no_cookies_ever_sent(env, respx_mock):
    settings, clock, worker = env
    route_headers = {"set-cookie": "csrftoken=abc; Path=/; Secure"}
    respx_mock.get(url__regex=r"https://www\.instagram\.com/[^/]+/$").mock(
        return_value=httpx.Response(200, text=live_html(), headers=route_headers)
    )
    respx_mock.get(url__regex=r"https://scontent\.cdninstagram\.com/.*").mock(
        return_value=httpx.Response(
            200, content=b"x", headers={"content-type": "image/jpeg", **route_headers}
        )
    )
    for _ in range(3):
        await worker.run_once()
        clock.advance(30)
    assert len(respx_mock.calls) >= 4
    for call in respx_mock.calls:
        assert "cookie" not in {k.lower() for k in call.request.headers}


async def test_requests_look_like_a_browser_navigation(env, respx_mock):
    _, _, worker = env
    mock_profiles(respx_mock, {})
    await worker.run_once()
    h = respx_mock.calls[0].request.headers
    assert h["sec-fetch-mode"] == "navigate" and "Chrome" in h["user-agent"]


async def test_pause_resume_and_status(env, respx_mock):
    settings, clock, worker = env
    mock_profiles(respx_mock, {})
    worker.pause()
    assert await worker.run_once() == "paused"
    assert CheckerWorker(settings, clock=clock).status()["state"] == "paused"  # persisted
    assert not respx_mock.calls
    worker.resume()
    status = worker.status()
    assert status["state"] == "running" and status["queue_size"] == 8
    assert status["tiers"]["1"]["total"] == 2 and status["daily_cap"] == settings.daily_request_cap
    assert re.match(r"\d{4}-", (await _after(worker))["last_result"]["at"])


async def _after(worker):
    await worker.run_once()
    return worker.status()


def test_defaults():
    s = Settings()
    assert (s.check_interval_seconds, s.check_jitter_seconds) == (3, 3)
    assert s.daily_request_cap == 5000 and s.viewport_check_days == 10


async def test_viewport_bump_only_unchecked_or_stale(env, respx_mock):
    settings, clock, worker = env
    mock_profiles(respx_mock, {})
    # unchecked accounts are queued
    assert worker.bump_stale(["Zoe", "carol"]) == 2
    assert worker.bump_stale(["zoe"]) == 0  # already queued
    # bumped accounts jump ahead of tier 1 (hank), keeping tier order among themselves
    await worker.run_once()
    clock.advance(30)
    await worker.run_once()
    assert profile_calls(respx_mock) == ["zoe", "carol"]
    # freshly checked: not stale, so ignored
    assert worker.bump_stale(["carol", "zoe"]) == 0
    # stale after viewport_check_days
    clock.advance(10 * 86400 + 1)
    assert worker.bump_stale(["carol"]) == 1
    # invalid or unknown names are ignored, and a batch is capped
    assert worker.bump_stale(["nobody"]) == 0


async def test_viewport_bump_respects_cooldown(env, respx_mock):
    settings, clock, worker = env
    mock_profiles(respx_mock, {}, default=httpx.Response(429))
    await worker.run_once()  # enters cooldown
    worker.bump_stale(["zoe"])
    assert await worker.run_once() == "cooldown"
    assert len(profile_calls(respx_mock)) == 1


async def test_saved_settings_reach_the_worker(env, respx_mock):
    settings, clock, worker = env
    mock_profiles(respx_mock, {}, default=httpx.Response(429))
    conn = db.connect(settings.db_path)
    from follow_audit import runtime_settings

    runtime_settings.update(
        conn, {"cooldown_base_minutes": 2, "cooldown_max_hours": 1, "request_timeout_seconds": 7}
    )
    conn.close()
    await worker.run_once()
    assert state(settings, "cooldown_until") - clock() == 120  # 2 minutes, not the default 15
    assert worker.strategy.timeout == 7
    assert worker.status()["viewport_dwell_seconds"] == 1.0


async def test_snoozed_accounts_are_not_checked(env, respx_mock):
    settings, clock, worker = env
    mock_profiles(respx_mock, {})
    from datetime import UTC, datetime, timedelta

    until = (datetime.fromtimestamp(clock(), UTC) + timedelta(days=5)).isoformat(timespec="seconds")
    conn = db.connect(settings.db_path)
    conn.execute(
        "INSERT INTO decisions VALUES ('alice', 'hank', 'snoozed', '2026-10-01T10:00:00', ?)",
        (until,),
    )
    conn.commit()
    conn.close()
    await worker.run_once()
    assert profile_calls(respx_mock) == ["newguy"]  # hank would otherwise be first
    clock.advance(6 * 86400)  # snooze over: hank is back in the queue
    await worker.run_once()
    assert "hank" in profile_calls(respx_mock)


# ------------------------------------------------------------------ last post date


def days_ago(n):
    return (datetime.now(UTC) - timedelta(days=n)).replace(microsecond=0)


def parse(html, username="user"):
    return parse_profile_page(username, 200, f"https://www.instagram.com/{username}/", html)


def test_last_post_is_the_newest_embedded_post():
    # pinned posts come first and can be old; the newest of all is the last post
    r = parse(live_html(posts=[days_ago(900), days_ago(400), days_ago(12), days_ago(30)]))
    assert r.posts_state == "known"
    assert datetime.fromisoformat(r.last_post_at).date() == days_ago(12).date()


def test_post_date_decoding_roundtrip():
    when = datetime(2026, 10, 4, 15, 35, 23, tzinfo=UTC)
    r = parse(live_html(posts=[when]))
    assert r.last_post_at.startswith("2026-10-04T15:35")


def test_private_account_hides_posts():
    r = parse(live_html(private=True, posts=[days_ago(3)], og="10 Followers, 5 Posts - x"))
    assert (r.posts_state, r.last_post_at) == ("hidden", None)


def test_zero_posts_is_known_but_has_no_date():
    r = parse(live_html(og="10 Followers, 2 Following, 0 Posts - See Instagram photos"))
    assert (r.posts_state, r.last_post_at) == ("known", None)


def test_posts_exist_but_none_readable_is_hidden_not_zero():
    r = parse(live_html(og="27K Followers, 228 Following, 2,395 Posts - See Instagram photos"))
    assert (r.posts_state, r.last_post_at) == ("hidden", None)


@pytest.mark.parametrize(
    ("og", "counts"),
    [
        ("27K Followers, 228 Following, 2,395 Posts - See Instagram", (27000, 228)),
        ("1.2M Followers, 1,034 Following, 88 Posts", (1_200_000, 1034)),
        ("10 Followers, 2 Following, 0 Posts", (10, 2)),
        ("10 Followers, 5 Posts - x", (10, None)),
        ("2.395 Followers, 3.1K Following", (None, 3100)),  # a bare dot is ambiguous
        (None, (None, None)),
    ],
)
def test_counts_are_read_from_the_description(og, counts):
    r = parse(live_html(og=og))
    assert (r.followers_count, r.following_count) == counts


def test_implausible_ids_are_ignored():
    html = live_html().replace(
        '"pageID":"profilePage"',
        '"pageID":"profilePage" "pk":"99999999999999999999","code":"AAAAAAAA1",',
    )
    assert parse(html).last_post_at is None


async def test_worker_stores_last_post_and_state(env, respx_mock):
    settings, clock, worker = env
    mock_profiles(respx_mock, {"hank": live_html(posts=[days_ago(40), days_ago(5)])})
    await worker.run_once()
    a = account(settings, "hank")
    assert a["posts_state"] == "known"
    assert datetime.fromisoformat(a["last_post_at"]).date() == days_ago(5).date()


async def test_private_account_is_hidden_and_not_rechecked_by_viewport(env, respx_mock):
    settings, clock, worker = env
    mock_profiles(respx_mock, {"hank": live_html(private=True)})
    await worker.run_once()
    a = account(settings, "hank")
    assert (a["posts_state"], a["last_post_at"]) == ("hidden", None)
    assert worker.bump_stale(["hank"]) == 0  # looked at already; nothing more to learn


async def test_new_post_info_replaces_old_and_zero_posts_clears_it(env, respx_mock):
    settings, clock, worker = env
    mapping = {"hank": live_html(posts=[days_ago(200)])}
    mock_profiles(respx_mock, mapping)
    await worker.run_once()
    mapping["hank"] = live_html(posts=[days_ago(2)])
    clock.advance(20 * 86400)
    bump(worker, "hank")
    await worker.run_once()
    assert (
        datetime.fromisoformat(account(settings, "hank")["last_post_at"]).date()
        == days_ago(2).date()
    )


async def test_live_accounts_without_post_info_are_picked_up_by_viewport(env, respx_mock):
    settings, clock, worker = env
    mock_profiles(respx_mock, {})
    await worker.run_once()  # hank checked now, with post info state set (hidden: no posts in page)
    conn = db.connect(settings.db_path)
    conn.execute(
        "UPDATE accounts SET posts_state=NULL WHERE username_key='hank'"
    )  # as before this feature
    conn.commit()
    conn.close()
    assert worker.bump_stale(["hank"]) == 1


async def test_recent_avatar_is_not_downloaded_again(env, respx_mock):
    settings, clock, worker = env
    mock_profiles(respx_mock, {})
    await worker.run_once()

    def pics():
        return [c for c in respx_mock.calls if c.request.url.host == "scontent.cdninstagram.com"]

    assert len(pics()) == 1
    clock.now = (
        settings.avatar_dir / "hank.jpg"
    ).stat().st_mtime + 5 * 86400  # picture is 5 days old
    bump(worker, "hank")
    await worker.run_once()
    assert len(pics()) == 1  # not fetched again
    clock.advance(40 * 86400)  # now older than 30 days
    bump(worker, "hank")
    await worker.run_once()
    assert len(pics()) == 2


def test_decisions_constraints(tmp_path):
    import sqlite3

    import pytest

    conn = db.connect(tmp_path / "new.db")
    db.migrate(conn)
    conn.execute("INSERT INTO accounts(username_key, username) VALUES ('a', 'a')")
    ins = "INSERT INTO decisions VALUES (?, 'a', ?, 't', ?)"
    conn.execute(ins, ("alice", "ignored", None))
    for args in (
        ("alice", "ignored", None),  # one decision per owner and account
        ("bob", "bogus", None),  # unknown kind
        ("bob", "snoozed", None),  # a snooze needs an end
        ("bob", "ignored", "2999"),  # only a snooze has an end
    ):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(ins, args)
    with pytest.raises(sqlite3.IntegrityError):  # must point at a known account
        conn.execute("INSERT INTO decisions VALUES ('alice', 'zz', 'ignored', 't', NULL)")
    conn.execute(ins, ("bob", "ignored", None))  # the same account, another owner: fine
    conn.execute("DELETE FROM accounts WHERE username_key='a'")
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    conn.close()


async def test_check_now_skips_queue_pause_and_cap_but_not_a_cooldown(env, respx_mock):
    settings, clock, worker = env
    mock_profiles(respx_mock, {"zed": live_html(og="9K Followers, 3 Following, 1 Posts")})
    conn = db.connect(settings.db_path)
    conn.execute("INSERT INTO accounts(username_key, username) VALUES ('zed', 'zed')")
    conn.commit()
    conn.close()
    worker.pause()
    out = await worker.check_now("Zed")
    assert out == {"outcome": "live"}
    a = account(settings, "zed")
    assert a["status"] == "live" and a["followers_count"] == 9000
    # while Instagram has asked for a pause, nothing is requested
    conn = db.connect(settings.db_path)
    worker._set(conn, "cooldown_until", clock() + 600)
    conn.close()
    calls = len(profile_calls(respx_mock))
    out = await worker.check_now("zed")
    assert out["outcome"] == "cooldown" and out["retry_in"] > 0
    assert len(profile_calls(respx_mock)) == calls
    assert (await worker.check_now("nobody_at_all")) == {"outcome": "unknown"}


async def test_picture_already_on_disk_is_linked_to_the_account(env, respx_mock):
    settings, clock, worker = env
    mock_profiles(respx_mock, {})
    settings.avatar_dir.mkdir(parents=True, exist_ok=True)
    (settings.avatar_dir / "hank.jpg").write_bytes(b"\xff\xd8old")
    import os

    os.utime(settings.avatar_dir / "hank.jpg", (clock(), clock()))  # fresh
    await worker.run_once()
    assert account(settings, "hank")["avatar_path"] == "avatars/hank.jpg"


async def test_check_now_fetches_the_picture_even_at_the_daily_cap(env, respx_mock):
    settings, clock, worker = env
    mock_profiles(respx_mock, {})
    conn = db.connect(settings.db_path)
    with conn:
        conn.executemany(
            "INSERT INTO requests VALUES (?)", [(clock() - 10,)] * settings.daily_request_cap
        )
    conn.close()
    assert await worker.run_once() == "cap"  # the queue stops at the cap...
    assert (await worker.check_now("hank")) == {"outcome": "live"}  # ...a person waiting doesn't
    assert (settings.avatar_dir / "hank.jpg").is_file()
    assert account(settings, "hank")["avatar_path"] == "avatars/hank.jpg"
