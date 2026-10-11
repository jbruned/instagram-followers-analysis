import pytest
from fastapi.testclient import TestClient

from follow_audit.app import create_app
from follow_audit.checker.avatars import allowed_url
from follow_audit.config import Settings
from follow_audit.security import host_name


@pytest.fixture
def make_client(make_data_dir, tmp_path):
    def make(base_url="http://localhost", **extra):
        settings = Settings(
            data_dir=make_data_dir("json_zip"),
            state_dir=tmp_path / "state",
            checker_enabled=False,
            **extra,
        )
        return TestClient(create_app(settings), base_url=base_url)

    return make


@pytest.mark.parametrize(
    "host,name",
    [
        ("localhost:8000", "localhost"),
        ("127.0.0.1", "127.0.0.1"),
        ("[::1]:8000", "::1"),
        ("A.b", "a.b"),
    ],
)
def test_host_name(host, name):
    assert host_name(host) == name


def test_local_names_are_served_and_get_safe_headers(make_client):
    for base in ("http://localhost", "http://127.0.0.1:8000", "http://[::1]:8000"):
        with make_client(base) as c:
            r = c.get("/api/health")
            assert r.status_code == 200
            assert r.headers["x-frame-options"] == "DENY"
            assert "default-src 'self'" in r.headers["content-security-policy"]


def test_a_foreign_host_name_is_refused_even_for_reads(make_client):
    with make_client("http://evil.example") as c:
        assert c.get("/api/owners").status_code == 403
        assert c.get("/").status_code == 403


def test_extra_host_names_can_be_allowed(make_client):
    with make_client("http://followers.lan", allowed_hosts="followers.lan, other.lan") as c:
        assert c.get("/api/health").status_code == 200


def test_cross_site_changes_are_refused(make_client):
    with make_client() as c:
        for headers in (
            {"Origin": "http://evil.example"},
            {"Origin": "http://localhost:9999"},
            {"Sec-Fetch-Site": "cross-site"},
        ):
            r = c.post("/api/upload", files=[("files", ("a.json", b"{}"))], headers=headers)
            assert r.status_code == 403, headers
        assert (
            c.put("/api/settings", json={}, headers={"Origin": "http://evil.example"}).status_code
            == 403
        )
        assert (
            c.delete("/api/snapshots/1", headers={"Origin": "http://evil.example"}).status_code
            == 403
        )


def test_same_origin_changes_are_allowed(make_client):
    with make_client() as c:
        r = c.put("/api/settings", json={}, headers={"Origin": "http://localhost"})
        assert r.status_code == 200


@pytest.mark.parametrize(
    "url,ok",
    [
        ("https://scontent-xyz1.cdninstagram.com/v/p.jpg", True),
        ("https://instagram.ffoo1-1.fbcdn.net/v/p.jpg", True),
        ("http://scontent.cdninstagram.com/p.jpg", False),
        ("https://evil.example/p.jpg", False),
        ("https://cdninstagram.com.evil.example/p.jpg", False),
        ("https://169.254.169.254/latest", False),
        ("file:///etc/passwd", False),
    ],
)
def test_only_instagram_image_hosts_are_fetched(url, ok):
    assert allowed_url(url) is ok


def test_leftovers_of_a_cut_short_upload_are_cleared_at_startup(make_data_dir, tmp_path):
    leftover = tmp_path / "state" / "incoming" / "abc.part"
    leftover.parent.mkdir(parents=True)
    leftover.write_bytes(b"half")
    settings = Settings(
        data_dir=make_data_dir("json_zip"), state_dir=tmp_path / "state", checker_enabled=False
    )
    with TestClient(create_app(settings), base_url="http://localhost"):
        assert not leftover.exists()
