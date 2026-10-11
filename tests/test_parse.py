import json

import pytest

from follow_audit.ingest import parse


def test_username_validation():
    assert parse.valid_username("a.b_c9")
    assert not parse.valid_username("bad name")
    assert not parse.valid_username("x" * 31)
    assert not parse.valid_username("")
    assert not parse.valid_username(None)


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("https://www.instagram.com/alice", "alice"),
        ("https://www.instagram.com/_u/carol", "carol"),
        ("https://www.instagram.com/accounts/login", None),
        ("https://www.instagram.com/p/abc", None),
        ("", None),
    ],
)
def test_username_from_href(href, expected):
    assert parse.username_from_href(href) == expected


def test_followers_bare_array_value_first():
    data = json.dumps(
        [
            {
                "title": "",
                "string_list_data": [
                    {"href": "https://www.instagram.com/other", "value": "Alice", "timestamp": 5}
                ],
            }
        ]
    )
    out = parse.parse_file("x/followers_1.json", data.encode())
    assert [(e.username, e.key, e.ts) for e in out["followers"]] == [("Alice", "alice", 5)]


def test_title_only_and_u_href():
    data = json.dumps(
        {
            "relationships_following": [
                {
                    "title": "carol",
                    "string_list_data": [{"href": "https://www.instagram.com/_u/dave"}],
                },
                {"title": "only_title"},
                {"title": "bad name!", "string_list_data": []},
            ]
        }
    )
    out = parse.parse_file("following.json", data.encode())
    # href username wins over title; title is the last resort; invalid is skipped
    assert [e.username for e in out["following"]] == ["dave", "only_title"]
    assert out["following"][0].ts is None


def test_label_values_format_and_single_object():
    item = {
        "timestamp": 9,
        "label_values": [
            {"label": "URL", "value": ""},
            {"label": "Name", "value": "Bob B"},
            {"label": "Username", "value": "bob"},
        ],
    }
    out = parse.parse_file("close_friends.json", json.dumps([item]).encode())
    assert out["close_friends"][0].full_name == "Bob B"
    single = parse.parse_file("blocked_profiles.json", json.dumps(item).encode())
    assert [e.username for e in single["blocked"]] == ["bob"]


def test_kind_from_key_beats_filename_and_unknown_key_kept():
    data = json.dumps(
        {
            "relationships_follow_requests_sent": [{"title": "a"}],
            "relationships_weird_new_thing": [{"title": "b"}],
        }
    )
    out = parse.parse_file("followers_1.json", data.encode())
    assert "pending_sent" in out and "relationships_weird_new_thing" in out
    assert "followers" not in out


def test_filename_kinds():
    assert parse.kind_from_filename("a/follow_requests_you've_received.json") == "pending_received"
    assert parse.kind_from_filename("recently_unfollowed_accounts.json") == "recently_unfollowed"
    assert parse.kind_from_filename("following_2.json") == "following"
    assert parse.kind_from_filename("custom_lists.json") is None
    assert parse.kind_from_filename("followers_1.txt") is None


def test_html():
    html = (
        '<div><div>a</div><a href="https://www.instagram.com/alice">u</a><div>Jun 01, 2024 2:21 pm</div></div>'
        '<a href="https://www.instagram.com/explore">x</a>'
        '<div><a href="https://www.instagram.com/_u/bob">bob</a></div><div>nodate</div>'
    )
    out = parse.parse_file("followers_1.html", html.encode())["followers"]
    assert [e.username for e in out] == ["alice", "bob"]
    assert out[0].ts == 1717251660
    assert out[1].ts is None


def test_legacy_connections():
    data = json.dumps(
        {
            "followers": {"a": "2019-05-01T12:00:00+00:00"},
            "follow_requests_sent": {"b": "2019-05-01T12:00:00+00:00"},
            "following_hashtags": {"t": "x"},
            "blocked_users": {},
        }
    )
    out = parse.parse_file("connections.json", data.encode())
    assert out["followers"][0].ts == 1556712000
    assert "pending_sent" in out and "blocked" not in out or out["blocked"] == []


def test_malformed_json_raises():
    with pytest.raises(ValueError):
        parse.parse_file("followers_1.json", b"{not json")


@pytest.mark.parametrize(
    ("raw", "fixed"),
    [
        ("Mart\u00c3\u00adn", "Martín"),  # UTF-8 bytes read as Latin-1
        ("Jos\u00c3\u00a9 \u00c3\u0091", "José Ñ"),
        ("\u00f0\u009f\u0098\u0080 hi", "\U0001f600 hi"),
        ("Martín", "Martín"),  # already right
        ("Zoë", "Zoë"),  # a lone accented letter is not that pattern
        ("日本語", "日本語"),
        ("Bob", "Bob"),
    ],
)
def test_names_with_bytes_misread_as_latin1_are_repaired(raw, fixed):
    from follow_audit.ingest.parse import repair_text

    assert repair_text(raw) == fixed


def test_repaired_in_label_values_exports():
    doc = {
        "relationships_close_friends": [
            {
                "timestamp": 1700000000,
                "label_values": [
                    {"label": "Username", "value": "martin"},
                    {"label": "Name", "value": "Mart\u00c3\u00adn"},
                ],
            }
        ]
    }
    out = parse.parse_json("x/close_friends.json", json.dumps(doc).encode())
    assert out["close_friends"][0].full_name == "Martín"
