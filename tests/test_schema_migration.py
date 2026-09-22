"""The migration guard.

`findings.direction` was added to the DDL with no ALTER. `CREATE TABLE IF NOT
EXISTS` is a no-op on a database that already has the table, `add_finding`'s
INSERT is positional, and the live table still held 82 rows from before the
column existed -- so the table looked populated while every write failed.

These tests fix the shape of the failure so it cannot recur silently: a
database built from an older DDL must be brought forward by `init_db`, the
write must land afterwards, and `verify_schema` must report the drift *before*
the migration rather than only after.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mycelium import core  # noqa: E402

# The findings table exactly as it stood before `direction` was added -- the
# shape every database written before that edit still has.
PRE_DIRECTION_FINDINGS = """
CREATE TABLE findings (
    id TEXT PRIMARY KEY,
    created_ts TEXT NOT NULL,
    miner TEXT NOT NULL,
    confidence REAL NOT NULL,
    title TEXT NOT NULL,
    evidence TEXT NOT NULL,
    suggestion TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',
    payload TEXT
)
"""


def _make_old_db(path: str) -> None:
    """A database written before the column: table present, column absent."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """CREATE TABLE traces (id TEXT PRIMARY KEY, ts TEXT NOT NULL, agent TEXT NOT NULL,
             session TEXT NOT NULL, kind TEXT NOT NULL, action TEXT, target TEXT,
             outcome TEXT, duration_ms INTEGER, payload TEXT);
           CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"""
        + PRE_DIRECTION_FINDINGS
        + ";"
        + "INSERT INTO meta(key,value) VALUES('schema_version','1');"
    )
    # Rows that predate the column, which is why the table looked alive.
    conn.execute(
        "INSERT INTO findings (id,created_ts,miner,confidence,title,evidence,suggestion,state,payload)"
        " VALUES ('old-1','2026-01-01T00:00:00Z','anomaly',0.5,'legacy','e','s','open','{}')"
    )
    conn.commit()
    conn.close()


def test_verify_schema_reports_the_drift_before_migration(tmp_path):
    p = str(tmp_path / "old.db")
    _make_old_db(p)
    report = core.verify_schema(p)
    assert report["ok"] is False
    assert "findings.direction missing" in report["drift"]
    assert report["schema_version_stored"] == 1


def test_init_db_migrates_an_old_database(tmp_path):
    p = str(tmp_path / "old.db")
    _make_old_db(p)
    core.init_db(p)
    report = core.verify_schema(p)
    assert report["drift"] == []
    assert report["schema_version_stored"] == core.SCHEMA_VERSION
    assert report["ok"] is True


def test_the_write_path_works_after_migration(tmp_path):
    """The assertion the original outage would have failed.

    Against the un-migrated database this raises -- ten values into a
    nine-column table -- and that exception was the whole bug.
    """
    p = str(tmp_path / "old.db")
    _make_old_db(p)
    core.init_db(p)
    core.add_finding(
        miner="market_authenticity",
        confidence=0.9,
        title="post-migration write",
        evidence="e",
        suggestion="alert",
        direction=1,
        payload={"token": "0xtok"},
    )
    rows = core.query_findings(miner="market_authenticity")
    assert len(rows) == 1
    assert core.row_to_dict(rows[0])["direction"] == 1


def test_migration_preserves_pre_existing_rows(tmp_path):
    """A migration adds a column; it must not cost the rows already there."""
    p = str(tmp_path / "old.db")
    _make_old_db(p)
    core.init_db(p)
    conn = sqlite3.connect(p)
    n = conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
    legacy = conn.execute("SELECT direction FROM findings WHERE id='old-1'").fetchone()[0]
    conn.close()
    assert n == 1
    # DEFAULT 0 is a guess at the old rows, not a measurement -- the policy doc
    # says so, and pinning it here keeps anyone from later assuming otherwise.
    assert legacy == 0


def test_init_db_is_idempotent(tmp_path):
    p = str(tmp_path / "new.db")
    core.init_db(p)
    core.init_db(p)
    core.init_db(p)
    assert core.verify_schema(p)["ok"] is True


def test_migrations_are_declared_and_pairs_are_well_formed():
    """A registry entry is (table, column, column_ddl) and nothing else."""
    assert core._COLUMN_MIGRATIONS, "the registry must not be empty"
    for entry in core._COLUMN_MIGRATIONS:
        assert len(entry) == 3, entry
        table, column, column_ddl = entry
        assert table and column and column_ddl
        assert " " not in table and " " not in column
        assert ";" not in column_ddl, "no statement terminator in a column ddl"


def test_every_registry_column_is_also_in_the_ddl():
    """The failure this guards against, in the other direction.

    A column registered for migration but absent from the CREATE statement is
    fine on an old database and missing on a brand-new one -- the same bug with
    a longer fuse.
    """
    import inspect

    ddl = inspect.getsource(core.init_db)
    for table, column, _ddl in core._COLUMN_MIGRATIONS:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl, f"{table} not in init_db"
        assert column in ddl, f"{table}.{column} not in the CREATE statement"


def test_a_migrated_database_writes_every_field_into_its_own_column(tmp_path):
    """The corruption a positional INSERT causes, and only on migrated databases.

    `ALTER TABLE ... ADD COLUMN` appends the column physically last, while the
    DDL declares it fourth. A bare `INSERT INTO findings VALUES (...)` therefore
    assigns by physical position: on a database created from the current DDL it
    is correct, and on a migrated one every field from `direction` onward lands
    one column early. So the live database was quietly scrambling rows while the
    test suite -- which builds fresh databases -- stayed green.

    This asserts every field, not just the one that was added, because the
    damage was to all of them.
    """
    p = str(tmp_path / "old.db")
    _make_old_db(p)
    core.init_db(p)

    core.add_finding(
        miner="market_authenticity",
        confidence=0.913,
        direction=1,
        title="title-sentinel",
        evidence="evidence-sentinel",
        suggestion="suggestion-sentinel",
        payload={"payload": "sentinel"},
    )

    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM findings WHERE title = 'title-sentinel'").fetchone()
    conn.close()
    assert row is not None, "every field shifted, so title is not where the title went"

    got = dict(row)
    assert got["miner"] == "market_authenticity"
    assert got["confidence"] == 0.913
    assert got["direction"] == 1
    assert got["title"] == "title-sentinel"
    assert got["evidence"] == "evidence-sentinel"
    assert got["suggestion"] == "suggestion-sentinel"
    assert got["state"] == "open"
    assert got["payload"] == '{"payload": "sentinel"}'


def test_emit_names_its_columns_too(tmp_path):
    """`traces` has never had a column added, so this is a landmine rather than
    a bug -- but it is the same shape and would fire the day one is."""
    p = str(tmp_path / "old.db")
    _make_old_db(p)
    core.init_db(p)
    core.emit(agent="probe", session="s1", kind="observation",
              action="wallet_buy", target="0xwallet", outcome="success",
              duration_ms=12, payload={"k": "v"})
    rows = core.query_traces(agent="probe")
    assert len(rows) == 1
    got = dict(rows[0])
    assert got["action"] == "wallet_buy"
    assert got["target"] == "0xwallet"
    assert got["outcome"] == "success"
    assert got["duration_ms"] == 12
    assert json.loads(got["payload"]) == {"k": "v"}
