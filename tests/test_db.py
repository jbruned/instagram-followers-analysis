import sqlite3

import pytest

from follow_audit import db


def test_new_database_is_version_one(tmp_path):
    path = tmp_path / "s" / "a.db"
    db.init_db(path)
    db.init_db(path)  # opening it again changes nothing
    conn = db.connect(path)
    assert db.version(conn) == 1
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='migrations'").fetchone()
    assert "fetch_version" in [r[1] for r in conn.execute("PRAGMA table_info(accounts)")]
    conn.close()
    assert not list(path.parent.glob("*.before-version-*"))  # nothing to back up


def test_migrations_run_in_order_and_back_the_database_up(tmp_path, monkeypatch):
    path = tmp_path / "s" / "a.db"
    db.init_db(path)

    def add_note(conn):
        conn.execute("CREATE TABLE notes (text TEXT)")

    def fill_note(conn):
        conn.execute("INSERT INTO notes VALUES ('hello')")

    monkeypatch.setattr(db, "MIGRATIONS", [add_note, fill_note])
    monkeypatch.setattr(db, "VERSION", 3)
    db.init_db(path)
    conn = db.connect(path)
    assert db.version(conn) == 3
    assert conn.execute("SELECT text FROM notes").fetchone()[0] == "hello"
    conn.close()
    assert (path.parent / "a.db.before-version-2").is_file()  # copy of what version 1 held


def test_database_from_a_newer_app_is_refused(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    db.migrate(conn)
    conn.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="newer than this app"):
        db.migrate(conn)


def test_older_development_database_is_refused(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE migrations (version INTEGER PRIMARY KEY)")
    conn.executemany("INSERT INTO migrations VALUES (?)", [(v,) for v in range(1, 6)])
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="development version"):
        db.init_db(path)
    assert path.is_file()  # untouched
