"""Predictions lifecycle, ported from the pysrc fork (ares-mycelium-cycle tree).

Covers: the table exists on fresh AND pre-existing databases (it is a new
table, so CREATE TABLE IF NOT EXISTS is the whole migration), the table shape
matches what gateway/main.go insertPrediction writes, add -> query -> due ->
resolve round-trips every field, the shared resolver grades both methods and
returns 'unknown' (never a guess) for anything it does not understand, and a
re-run resolves nothing twice.
"""
import json
import sqlite3

import pytest

from mycelium import cli, core, resolver

GATEWAY_COLUMNS = [
    "id", "created_ts", "source", "question", "symbol", "probability",
    "resolution_date", "resolution_criterion", "status", "outcome",
    "resolved_ts", "payload",
]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.delenv("MYCELIUM_BACKEND", raising=False)
    path = str(tmp_path / "p.db")
    core.init_db(path)
    return path


def _cols(path):
    conn = sqlite3.connect(path)
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info(predictions)")]
    finally:
        conn.close()


def test_table_matches_gateway_insert(db):
    assert _cols(db) == GATEWAY_COLUMNS


def test_table_added_to_preexisting_db(tmp_path, monkeypatch):
    """A database written before predictions existed gains the table."""
    monkeypatch.delenv("MYCELIUM_BACKEND", raising=False)
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE traces (id TEXT PRIMARY KEY, ts TEXT NOT NULL, agent TEXT NOT NULL,"
        " session TEXT NOT NULL, kind TEXT NOT NULL, action TEXT, target TEXT, outcome TEXT,"
        " duration_ms INTEGER, payload TEXT);"
        "CREATE TABLE findings (id TEXT PRIMARY KEY, created_ts TEXT NOT NULL, miner TEXT NOT NULL,"
        " confidence REAL NOT NULL, title TEXT NOT NULL, evidence TEXT NOT NULL,"
        " suggestion TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'open', payload TEXT);"
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "INSERT INTO meta VALUES ('schema_version','1');"
    )
    conn.commit()
    conn.close()
    core.init_db(path)
    assert _cols(path) == GATEWAY_COLUMNS
    assert core.verify_schema(path)["ok"] is True


def test_no_migrate_still_creates_predictions(tmp_path, monkeypatch):
    monkeypatch.delenv("MYCELIUM_BACKEND", raising=False)
    monkeypatch.setenv("MYCELIUM_NO_MIGRATE", "1")
    path = str(tmp_path / "guest.db")
    core.init_db(path)
    assert _cols(path) == GATEWAY_COLUMNS


def test_add_query_get_roundtrip_every_field(db):
    crit = {"method": "continued_interest", "description": "d", "key_field": "symbol"}
    row = core.add_prediction(source="unit", question="q?", probability=1.7,
                              resolution_date="2026-01-01T00:00:00Z",
                              resolution_criterion=crit, symbol="TOK",
                              payload={"k": 1})
    assert row["probability"] == 1.0  # clamped, not rejected
    got = core.row_to_dict(core.get_prediction(row["id"]))
    assert got["source"] == "unit"
    assert got["question"] == "q?"
    assert got["symbol"] == "TOK"
    assert got["probability"] == 1.0
    assert got["resolution_date"] == "2026-01-01T00:00:00Z"
    assert got["resolution_criterion"] == crit
    assert got["status"] == "open"
    assert got["outcome"] is None and got["resolved_ts"] is None
    assert got["payload"] == {"k": 1}
    listed = core.iter_rows(core.query_predictions(source="unit", status="open", symbol="TOK"))
    assert [p["id"] for p in listed] == [row["id"]]


def test_due_and_resolve_once(db):
    past = core.add_prediction("unit", "past", 0.5, "2020-01-01T00:00:00Z",
                               {"method": "continued_interest", "description": "d"}, symbol="X")
    core.add_prediction("unit", "future", 0.5, "2999-01-01T00:00:00Z",
                        {"method": "continued_interest", "description": "d"}, symbol="X")
    due = core.iter_rows(core.due_predictions())
    assert [p["id"] for p in due] == [past["id"]]
    assert core.resolve_prediction(past["id"], "correct") is True
    assert core.resolve_prediction(past["id"], "incorrect") is False  # already resolved
    got = core.row_to_dict(core.get_prediction(past["id"]))
    assert got["status"] == "resolved" and got["outcome"] == "correct" and got["resolved_ts"]


def test_resolver_methods_and_unknown(db):
    traces = [
        {"ts": "2026-01-02T00:00:00Z", "agent": "a", "action": "x", "target": "",
         "payload": {"symbol": "tok"}},
        {"ts": "2026-01-01T00:10:00Z", "agent": "wallet_intel", "action": "wallet_buy",
         "target": "w", "payload": {}},
    ]
    ci = core.add_prediction("unit", "ci", 0.5, "2026-01-01T00:00:00Z",
                             {"method": "continued_interest", "description": "d"}, symbol="TOK")
    quiet = core.add_prediction("unit", "quiet", 0.5, "2026-01-01T00:00:00Z",
                                {"method": "continued_interest", "description": "d"}, symbol="ZZZ")
    vol = core.add_prediction("unit", "vol", 0.5, "2026-01-01T00:00:00Z",
                              {"method": "volume_range", "description": "d",
                               "target_bucket_start": "2026-01-01T00:00:00Z",
                               "bucket_minutes": 60, "point_forecast": 1, "tolerance_pct": 0.1})
    unk = core.add_prediction("unit", "unk", 0.5, "2026-01-01T00:00:00Z",
                              {"method": "not_a_method", "description": "d"})
    counts = resolver.resolve_predictions(traces=traces)
    assert counts == {"resolved": 4, "correct": 2, "incorrect": 1, "unknown": 1}
    out = {p["question"]: p["outcome"]
           for p in core.iter_rows(core.query_predictions(status="resolved"))}
    assert out == {"ci": "correct", "quiet": "incorrect", "vol": "correct", "unk": "unknown"}
    assert resolver.resolve_predictions(traces=traces)["resolved"] == 0
    del ci, quiet, vol, unk


def test_cli_predictions_and_resolve(db, capsys):
    core.add_prediction("unit", "q", 0.4, "2020-01-01T00:00:00Z",
                        {"method": "continued_interest", "description": "d"}, symbol="TOK")
    cli.main(["predictions", "--status", "open"])
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 1 and out["predictions"][0]["symbol"] == "TOK"
    cli.main(["resolve"])
    res = json.loads(capsys.readouterr().out)
    assert res["resolved"] == 1


def test_postgres_backend_fails_loudly(monkeypatch, db):
    class FakePg:  # PostgresBackend has no prediction methods yet
        pass
    monkeypatch.setattr(core, "_pg", lambda: FakePg())
    with pytest.raises(NotImplementedError):
        core.query_predictions()
    # ...but the cycle's resolver pass reports it instead of aborting the cycle
    assert "error" in cli._resolve_predictions_for_cycle()
