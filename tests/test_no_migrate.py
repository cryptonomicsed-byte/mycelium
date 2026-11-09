"""MYCELIUM_NO_MIGRATE: a guest process never ALTERs a database it doesn't own.

Found live: the resolver, pointed at the gateway's DB, added findings.direction
and the gateway's own positional `INSERT INTO findings VALUES (9 values)` would
then have failed on every call."""
import importlib
import sqlite3


def _old_db(path):
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE findings (id TEXT PRIMARY KEY, created_ts TEXT, miner TEXT, confidence REAL,
            title TEXT, evidence TEXT, suggestion TEXT, state TEXT DEFAULT 'open', payload TEXT);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO meta VALUES ('schema_version', '1');
    """)
    c.commit()
    c.close()


def _cols(path):
    return [r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(findings)")]


def test_no_migrate_leaves_the_owners_schema_alone(tmp_path, monkeypatch):
    db = str(tmp_path / "gw.db")
    _old_db(db)
    monkeypatch.setenv("MYCELIUM_DB", db)
    monkeypatch.setenv("MYCELIUM_NO_MIGRATE", "1")
    from mycelium import core
    importlib.reload(core)
    core.init_db()
    assert "direction" not in _cols(db)
    assert sqlite3.connect(db).execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "1"
    # the owner's positional 9-value insert still works
    sqlite3.connect(db).execute("INSERT INTO findings VALUES ('x','t','m',0.5,'t','e','s','open','{}')")
    # and missing tables (traces) were created so the guest can still write
    assert sqlite3.connect(db).execute("SELECT name FROM sqlite_master WHERE name='traces'").fetchone()


def test_default_still_migrates(tmp_path, monkeypatch):
    db = str(tmp_path / "own.db")
    _old_db(db)
    monkeypatch.setenv("MYCELIUM_DB", db)
    monkeypatch.delenv("MYCELIUM_NO_MIGRATE", raising=False)
    from mycelium import core
    importlib.reload(core)
    core.init_db()
    assert "direction" in _cols(db)
