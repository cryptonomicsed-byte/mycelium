"""Core substrate: event model, SQLite storage, findings store.

Schema v1. Storage-agnostic by design (SQLite now; Postgres/object store later).
Event envelope is versioned and extensible via payload JSON.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional

SCHEMA_VERSION = 1
DB_PATH = os.environ.get("MYCELIUM_DB", os.path.expanduser("~/mycelium/mycelium.db"))

VALID_KINDS = {
    "tool_call", "decision", "memory_write",
    "error", "workflow_start", "workflow_end", "observation",
}
VALID_OUTCOMES = {"success", "failure", "partial", "info"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _pg():
    """Return a PostgresBackend when MYCELIUM_BACKEND=postgres, else None.

    The single switch for storage: every public function below delegates to
    the Postgres backend when set, otherwise keeps the SQLite path. Results
    are dicts either way (PostgresBackend returns dicts; SQLite rows flow
    through row_to_dict/iter_rows at the call sites).
    """
    if os.environ.get("MYCELIUM_BACKEND") == "postgres":
        from .storage import PostgresBackend
        return PostgresBackend()
    return None


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(path: Optional[str] = None) -> None:
    """Create tables if missing. Idempotent. Postgres when backend selected."""
    global DB_PATH
    if path:
        DB_PATH = path
    pg = _pg()
    if pg is not None:
        pg.init_db()
        return
    conn = _connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS traces (
            id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            agent TEXT NOT NULL,
            session TEXT NOT NULL,
            kind TEXT NOT NULL,
            action TEXT,
            target TEXT,
            outcome TEXT,
            duration_ms INTEGER,
            payload TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_traces_ts ON traces(ts);
        CREATE INDEX IF NOT EXISTS idx_traces_agent ON traces(agent);
        CREATE INDEX IF NOT EXISTS idx_traces_action ON traces(action);

        CREATE TABLE IF NOT EXISTS findings (
            id TEXT PRIMARY KEY,
            created_ts TEXT NOT NULL,
            miner TEXT NOT NULL,
            confidence REAL NOT NULL,
            title TEXT NOT NULL,
            evidence TEXT NOT NULL,
            suggestion TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'open',
            payload TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_findings_state ON findings(state);

        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    conn.close()


def emit(
    agent: str,
    session: str,
    kind: str,
    action: Optional[str] = None,
    target: Optional[str] = None,
    outcome: str = "info",
    duration_ms: Optional[int] = None,
    payload: Optional[Dict[str, Any]] = None,
    ts: Optional[str] = None,
) -> Dict[str, Any]:
    """Append one trace event to the substrate. Returns the stored row."""
    if kind not in VALID_KINDS:
        raise ValueError(f"invalid kind {kind!r}; valid: {sorted(VALID_KINDS)}")
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"invalid outcome {outcome!r}")
    pg = _pg()
    if pg is not None:
        return pg.emit(agent=agent, session=session, kind=kind, action=action,
                       target=target, outcome=outcome, duration_ms=duration_ms,
                       payload=payload, ts=ts)
    row = {
        "id": str(uuid.uuid4()),
        "ts": ts or _now(),
        "agent": agent,
        "session": session,
        "kind": kind,
        "action": action,
        "target": target,
        "outcome": outcome,
        "duration_ms": duration_ms,
        "payload": json.dumps(payload or {}),
    }
    conn = _connect()
    conn.execute(
        "INSERT INTO traces VALUES (:id,:ts,:agent,:session,:kind,:action,:target,:outcome,:duration_ms,:payload)",
        row,
    )
    conn.commit()
    conn.close()
    return row


def query_traces(
    agent: Optional[str] = None,
    kind: Optional[str] = None,
    action: Optional[str] = None,
    outcome: Optional[str] = None,
    session: Optional[str] = None,
    limit: int = 500,
) -> List[Any]:
    pg = _pg()
    if pg is not None:
        return pg.query_traces(agent=agent, kind=kind, action=action,
                               outcome=outcome, session=session, limit=limit)
    conn = _connect()
    sql = "SELECT * FROM traces WHERE 1=1"
    args: List[Any] = []
    if agent:
        sql += " AND agent=?"; args.append(agent)
    if kind:
        sql += " AND kind=?"; args.append(kind)
    if action:
        sql += " AND action=?"; args.append(action)
    if outcome:
        sql += " AND outcome=?"; args.append(outcome)
    if session:
        sql += " AND session=?"; args.append(session)
    sql += " ORDER BY ts LIMIT ?"
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return rows


def add_finding(
    miner: str,
    confidence: float,
    title: str,
    evidence: str,
    suggestion: str,
    payload: Optional[Dict[str, Any]] = None,
    dedupe: bool = True,
) -> Dict[str, Any]:
    """Persist a finding. dedupe=True (default) skips identical open findings,
    so repeated mine cycles are idempotent (cron-safe)."""
    payload = payload or {}
    pg = _pg()
    if pg is not None:
        return pg.add_finding(miner=miner, confidence=confidence, title=title,
                              evidence=evidence, suggestion=suggestion,
                              payload=payload, dedupe=dedupe)
    if dedupe:
        conn = _connect()
        rows = conn.execute(
            "SELECT id, state, payload FROM findings WHERE miner=? AND title=? AND state != 'dismissed'",
            (miner, title),
        ).fetchall()
        conn.close()
        for r in rows:
            try:
                existing = json.loads(r["payload"])
            except (json.JSONDecodeError, TypeError):
                continue
            if existing == payload:
                return {"id": r["id"], "duplicate": True, "state": r["state"]}
    row = {
        "id": str(uuid.uuid4()),
        "created_ts": _now(),
        "miner": miner,
        "confidence": round(float(confidence), 3),
        "title": title,
        "evidence": evidence,
        "suggestion": suggestion,
        "state": "open",
        "payload": json.dumps(payload, sort_keys=True),
    }
    conn = _connect()
    conn.execute(
        "INSERT INTO findings VALUES (:id,:created_ts,:miner,:confidence,:title,:evidence,:suggestion,:state,:payload)",
        row,
    )
    conn.commit()
    conn.close()
    return row


def query_findings(
    miner: Optional[str] = None,
    state: Optional[str] = None,
    limit: int = 100,
) -> List[Any]:
    pg = _pg()
    if pg is not None:
        return pg.query_findings(miner=miner, state=state, limit=limit)
    conn = _connect()
    sql = "SELECT * FROM findings WHERE 1=1"
    args: List[Any] = []
    if miner:
        sql += " AND miner=?"; args.append(miner)
    if state:
        sql += " AND state=?"; args.append(state)
    sql += " ORDER BY confidence DESC LIMIT ?"
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return rows


def get_finding(finding_id: str) -> Optional[Any]:
    pg = _pg()
    if pg is not None:
        return pg.get_finding(finding_id)
    conn = _connect()
    row = conn.execute("SELECT * FROM findings WHERE id=?", (finding_id,)).fetchone()
    conn.close()
    return row


def set_finding_state(finding_id: str, state: str) -> bool:
    pg = _pg()
    if pg is not None:
        return pg.set_finding_state(finding_id, state)
    conn = _connect()
    cur = conn.execute("UPDATE findings SET state=? WHERE id=?", (state, finding_id))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    if d.get("payload"):
        try:
            d["payload"] = json.loads(d["payload"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d


def iter_rows(rows: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [row_to_dict(r) for r in rows]
