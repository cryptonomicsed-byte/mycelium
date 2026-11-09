"""Tests for mycelium-substrate (U1), carried over from mycelium-tools.

P2-01: `mycelium_substrate` is now a shim over this repo's `mycelium`
package, so these original tests exercise the real substrate through the
old import name.
"""
import os
import sqlite3
import tempfile
import uuid

import pytest



def _tmp_db(tmp_path):
    path = str(tmp_path / "test.db")
    os.environ["MYCELIUM_DB"] = path
    # reset module-level DB_PATH
    import mycelium_substrate.core as core
    core.DB_PATH = path
    core.init_db(path)
    return path


# ── Smoke ──────────────────────────────────────────────────────────────────

def test_import():
    from mycelium_substrate import core, miners, apply  # noqa: F401


def test_init_db_idempotent(tmp_path):
    path = _tmp_db(tmp_path)
    import mycelium_substrate.core as core
    core.init_db(path)  # second call must not raise
    conn = sqlite3.connect(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "traces" in tables
    assert "findings" in tables


# ── Core logic ─────────────────────────────────────────────────────────────

def test_emit_and_query(tmp_path):
    _tmp_db(tmp_path)
    from mycelium_substrate import core

    trace_id = core.emit(
        agent="test-agent",
        session="sess-1",
        kind="tool_call",
        action="read_file",
        target="/tmp/foo",
        outcome="success",
        payload={"bytes": 42},
    )
    assert trace_id is not None

    rows = core.query_traces(agent="test-agent", limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row["agent"] == "test-agent"
    assert row["kind"] == "tool_call"
    assert row["outcome"] == "success"


def test_emit_invalid_kind_raises(tmp_path):
    _tmp_db(tmp_path)
    from mycelium_substrate import core
    with pytest.raises((ValueError, Exception)):
        core.emit(
            agent="a", session="s", kind="invalid_kind_xyz",
            action="x", target="y", outcome="success",
        )


# ── Findings store ─────────────────────────────────────────────────────────

def test_add_and_query_findings(tmp_path):
    _tmp_db(tmp_path)
    from mycelium_substrate import core

    result = core.add_finding(
        miner="test-miner",
        confidence=0.9,
        title="Always use context manager for DB",
        evidence="trace-abc shows bare connect()",
        suggestion="Use `with sqlite3.connect(...) as conn:`",
    )
    assert result is not None

    findings = core.query_findings(miner="test-miner", limit=10)
    assert any(f["title"] == "Always use context manager for DB" for f in findings)


# ── Miners smoke ───────────────────────────────────────────────────────────

def test_miners_importable():
    from mycelium_substrate.miners import MINERS
    assert len(MINERS) >= 1
    for name, fn in MINERS.items():
        assert isinstance(name, str)
        assert callable(fn)
