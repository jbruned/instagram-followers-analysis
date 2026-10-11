"""Settings that can be changed from the UI.

Environment variables (see ``config.py``) give the defaults. Values saved from the settings
dialog are stored in the ``app_settings`` table and win over the defaults. Resetting a value
removes its override. Everything is validated here, so the database never holds a bad value.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from .config import Settings


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    group: str
    unit: str
    kind: type  # int or float
    minimum: float
    maximum: float
    help: str


FIELDS: tuple[Field, ...] = (
    Field(
        "snooze_days",
        "Snooze duration",
        "Snooze",
        "days",
        int,
        1,
        3650,
        "How long a snoozed account stays out of the other tabs before it comes back.",
    ),
    Field(
        "inactive_after_days",
        "Mark accounts inactive after",
        "Inactivity",
        "days",
        int,
        7,
        3650,
        "Accounts whose last post is older than this get an Inactive badge.",
    ),
    Field(
        "high_follower_ratio",
        "Mark a high follower ratio from",
        "High follower ratio",
        "times",
        float,
        2,
        1000,
        "Accounts with at least this many times more followers than accounts they follow get a "
        "microphone icon. They also need at least 1,000 followers.",
    ),
    Field(
        "old_snapshot_days",
        "Warn about snapshots older than",
        "Snapshots",
        "days",
        int,
        1,
        3650,
        "A snapshot exported longer ago than this shows an Old snapshot warning.",
    ),
    Field(
        "watch_interval_seconds",
        "Look for new exports every",
        "Data folder",
        "seconds",
        float,
        0,
        3600,
        "How often the data folder is checked for new exports, which are imported "
        "automatically. 0 turns it off.",
    ),
    Field(
        "check_interval_seconds",
        "Delay between requests",
        "Account checker pacing",
        "seconds",
        float,
        1,
        3600,
        "Base wait between two Instagram requests.",
    ),
    Field(
        "check_jitter_seconds",
        "Random extra delay",
        "Account checker pacing",
        "seconds",
        float,
        0,
        3600,
        "Up to this many random seconds are added to each wait.",
    ),
    Field(
        "daily_request_cap",
        "Daily request cap",
        "Account checker pacing",
        "requests",
        int,
        1,
        100000,
        "Hard limit on Instagram requests in any rolling 24 hours.",
    ),
    Field(
        "request_timeout_seconds",
        "Request timeout",
        "Requests",
        "seconds",
        float,
        1,
        300,
        "How long to wait for Instagram to answer before giving up on a request.",
    ),
    Field(
        "cooldown_base_minutes",
        "First cooldown",
        "Requests",
        "minutes",
        int,
        1,
        1440,
        "How long to stop after Instagram pushes back. It doubles on every repeat.",
    ),
    Field(
        "cooldown_max_hours",
        "Longest cooldown",
        "Requests",
        "hours",
        int,
        1,
        168,
        "Upper limit for the doubling cooldown.",
    ),
    Field(
        "recheck_live_days",
        "Re-check live accounts after",
        "Re-checking",
        "days",
        int,
        1,
        3650,
        "Accounts found live are looked up again after this long.",
    ),
    Field(
        "recheck_unavailable_days",
        "Confirm unavailable after",
        "Re-checking",
        "days",
        int,
        1,
        365,
        "An account needs two missing results at least this far apart to count as unavailable.",
    ),
    Field(
        "viewport_check_days",
        "Check what you look at after",
        "Re-checking",
        "days",
        int,
        1,
        3650,
        "An account that stays in view is checked sooner if its last check is older than this.",
    ),
    Field(
        "viewport_dwell_seconds",
        "Time in view before checking",
        "Re-checking",
        "seconds",
        float,
        0.2,
        30,
        "How long a row must stay on screen to count as looked at.",
    ),
)
BY_KEY = {f.key: f for f in FIELDS}


class InvalidSetting(ValueError):
    pass


def load_overrides(conn: sqlite3.Connection) -> dict:
    out = {}
    for row in conn.execute("SELECT key, value FROM app_settings"):
        if row["key"] in BY_KEY:
            try:
                out[row["key"]] = json.loads(row["value"])
            except ValueError:
                continue
    return out


def effective(base: Settings, conn: sqlite3.Connection) -> Settings:
    return base.model_copy(update=load_overrides(conn))


def validate(key: str, value: object) -> int | float:
    field = BY_KEY.get(key)
    if field is None:
        raise InvalidSetting(f"Unknown setting: {key}.")
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidSetting(f"{field.label} must be a number.")
    if field.kind is int:
        if value != int(value):
            raise InvalidSetting(f"{field.label} must be a whole number.")
        value = int(value)
    else:
        value = float(value)
    if not field.minimum <= value <= field.maximum:
        raise InvalidSetting(
            f"{field.label} must be between {field.minimum:g} and {field.maximum:g} {field.unit}."
        )
    return value


def update(conn: sqlite3.Connection, changes: dict) -> None:
    """Apply changes atomically. A value of ``None`` resets that setting to its default."""
    checked = {
        key: (None if value is None else validate(key, value)) for key, value in changes.items()
    }
    with conn:
        for key, value in checked.items():
            if value is None:
                conn.execute("DELETE FROM app_settings WHERE key=?", (key,))
            else:
                conn.execute(
                    "INSERT INTO app_settings(key, value) VALUES (?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value)),
                )


def describe(base: Settings, conn: sqlite3.Connection) -> list[dict]:
    overrides = load_overrides(conn)
    return [
        {
            "key": f.key,
            "label": f.label,
            "group": f.group,
            "unit": f.unit,
            "type": "integer" if f.kind is int else "number",
            "min": f.minimum,
            "max": f.maximum,
            "help": f.help,
            "default": getattr(base, f.key),
            "value": overrides.get(f.key, getattr(base, f.key)),
            "customised": f.key in overrides,
        }
        for f in FIELDS
    ]
