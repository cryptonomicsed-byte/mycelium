"""Core substrate: event model, SQLite storage, findings store.

Schema v1. Storage-agnostic by design (SQLite now; Postgres/object store later).
Event envelope is versioned and extensible via payload JSON.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional

SCHEMA_VERSION = 2
DB_PATH = os.environ.get("MYCELIUM_DB", os.path.expanduser("~/mycelium/mycelium.db"))

# Columns added to the DDL after a database was first written. Each needs an
# ALTER of its own, because `CREATE TABLE IF NOT EXISTS` leaves an existing
# table exactly as it found it -- a column that appears only in the CREATE
# statement exists only on databases created since it was added.
#
# Mycelium is the only copy of what it holds: no upstream can re-derive a
# trace, and no rebuild can stand in for one. Rebuild-to-migrate (which is
# correct for a store derived from a source of truth, as fomopulse's tape is
# from the chain) is therefore unavailable here, and an ALTER is the only
# path from one schema to the next.
#
# This tuple exists because of one specific failure: `findings.direction` was
# added to the DDL, no ALTER was written, and `add_finding`'s INSERT is
# positional -- so every write against an older database failed, the caller
# saw an exception it did not surface, and the memory stopped growing while
# the table still held 82 rows from before the column was added. Nothing
# compared the code's schema to the database's, so nothing said so.
# `verify_schema()` below is that comparison.
_COLUMN_MIGRATIONS: "tuple[tuple[str, str, str], ...]" = (
    ("findings", "direction", "INTEGER NOT NULL DEFAULT 0"),
)

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


def _table_columns(conn: sqlite3.Connection, table: str) -> Optional[set]:
    """Column names of `table`, or None when the table does not exist yet."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if exists is None:
        return None
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def no_migrate() -> bool:
    return os.environ.get("MYCELIUM_NO_MIGRATE", "0").strip().lower() in ("1", "true", "yes", "on")


def apply_migrations(conn: sqlite3.Connection) -> List[str]:
    """Add every column a database written before it is missing. Idempotent.

    Returns the columns added, so a caller can say what it changed rather
    than claiming health it did not verify.
    """
    applied: List[str] = []
    for table, column, column_ddl in _COLUMN_MIGRATIONS:
        cols = _table_columns(conn, table)
        if cols is None or column in cols:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_ddl}")
        applied.append(f"{table}.{column}")
    return applied


def verify_schema(path: Optional[str] = None) -> Dict[str, Any]:
    """Compare the live database against the schema this module guarantees.

    Drift here does not mean a feature is missing -- it means a writer is
    failing. A column in `_COLUMN_MIGRATIONS` that the live table lacks makes
    `add_finding`'s positional INSERT fail on every call, which is the failure
    that went unnoticed. Run this before believing the substrate is healthy:
    "the process is up" is not "the writes are landing".

    SQLite only -- the Postgres backend runs the same migrations at init_db
    but against a shared server, so its drift is checked there, not here.
    """
    p = path or DB_PATH
    drift: List[str] = []
    stored = 0
    conn = sqlite3.connect(p)
    try:
        for table, column, _column_ddl in _COLUMN_MIGRATIONS:
            cols = _table_columns(conn, table)
            if cols is None:
                drift.append(f"table {table} missing entirely")
            elif column not in cols:
                drift.append(f"{table}.{column} missing")
        row = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        if row:
            try:
                stored = int(row[0])
            except (TypeError, ValueError):
                drift.append(f"schema_version not an integer: {row[0]!r}")
        else:
            drift.append("meta.schema_version absent")
    except sqlite3.Error as exc:
        drift.append(f"{type(exc).__name__}: {exc}")
    finally:
        conn.close()
    return {
        "ok": not drift and stored == SCHEMA_VERSION,
        "db": p,
        "schema_version_stored": stored,
        "schema_version_expected": SCHEMA_VERSION,
        "drift": drift,
    }


def init_db(path: Optional[str] = None) -> None:
    """Create tables if missing, then bring an older database forward.

    Two steps, and the second is the one that was missing: `CREATE TABLE IF
    NOT EXISTS` is not a migration, it is a no-op against a database that
    already has the table. Postgres when backend selected.
    """
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
            direction INTEGER NOT NULL DEFAULT 0,
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

        -- predictions: a NEW table (not a column added to an existing one), so
        -- CREATE TABLE IF NOT EXISTS is sufficient on every database, fresh or
        -- old -- there is no prior shape to migrate. RECORD class (see
        -- SCHEMA_POLICY.md): a resolved outcome is not re-observable. Any column
        -- added to it later MUST also go into _COLUMN_MIGRATIONS. Shape matches
        -- the live gateway table and gateway/main.go insertPrediction exactly.
        CREATE TABLE IF NOT EXISTS predictions (
            id TEXT PRIMARY KEY,
            created_ts TEXT NOT NULL,
            source TEXT NOT NULL,
            question TEXT NOT NULL,
            symbol TEXT,
            probability REAL NOT NULL,
            resolution_date TEXT NOT NULL,
            resolution_criterion TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            outcome TEXT,
            resolved_ts TEXT,
            payload TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_predictions_status ON predictions(status);
        CREATE INDEX IF NOT EXISTS idx_predictions_source ON predictions(source);
        CREATE INDEX IF NOT EXISTS idx_predictions_resolution_date ON predictions(resolution_date);
        """
    )
    # MYCELIUM_NO_MIGRATE=1: this process is a guest on a database another
    # deployment owns (e.g. the resolver writing traces into the gateway's
    # DB, whose own writer INSERTs positionally). Missing tables are still
    # created -- that can't break anyone -- but existing tables are never
    # ALTERed and the owner's schema_version is left alone.
    if no_migrate():
        applied = []
    else:
        applied = apply_migrations(conn)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
    conn.commit()
    conn.close()
    if applied:
        print(
            f"mycelium: migrated {', '.join(applied)} -> schema v{SCHEMA_VERSION}",
            file=sys.stderr,
        )


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
    # Named columns for the same reason `add_finding` names them: the day a
    # column is added to `traces` by ALTER it lands physically at the end, and a
    # positional insert would start writing every field one column early.
    conn.execute(
        "INSERT INTO traces"
        " (id, ts, agent, session, kind, action, target, outcome, duration_ms, payload)"
        " VALUES"
        " (:id, :ts, :agent, :session, :kind, :action, :target, :outcome, :duration_ms, :payload)",
        row,
    )
    conn.commit()
    conn.close()
    return row


def recent_since(days: Optional[float] = None) -> str:
    """ISO-8601 cutoff `days` back from now (default MYCELIUM_MINER_WINDOW_DAYS,
    or 7) — traces store `ts` as `_now()`-formatted strings, which sort and
    compare correctly as plain strings, so this can be passed straight to
    `query_traces(since=...)`."""
    if days is None:
        days = float(os.environ.get("MYCELIUM_MINER_WINDOW_DAYS", "7"))
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days * 86400))


def query_traces(
    agent: Optional[str] = None,
    kind: Optional[str] = None,
    action: Optional[str] = None,
    outcome: Optional[str] = None,
    session: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 500,
) -> List[Any]:
    """`since`, if given, is an ISO-8601 ts string (see recent_since()) —
    only traces at or after it are returned. None (default) means
    unbounded, unchanged from before this param existed: callers that want
    a true full-table view (counts(), stats/publish snapshots) keep
    working exactly as before; only miners.run_miner() opts into a bounded
    window, since it's the one call site that re-scans the whole table on
    every mine cycle."""
    pg = _pg()
    if pg is not None:
        return pg.query_traces(agent=agent, kind=kind, action=action,
                               outcome=outcome, session=session, since=since, limit=limit)
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
    if since:
        sql += " AND ts>=?"; args.append(since)
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
    direction: int = 0,
    payload: Optional[Dict[str, Any]] = None,
    dedupe: bool = True,
) -> Dict[str, Any]:
    """Persist a finding. dedupe=True (default) skips identical open findings,
    so repeated mine cycles are idempotent (cron-safe).
    direction: -1=decline, 0=neutral, 1=improve (orthogonal to confidence)."""
    payload = payload or {}
    pg = _pg()
    if pg is not None:
        return pg.add_finding(miner=miner, confidence=confidence, direction=direction,
                              title=title, evidence=evidence, suggestion=suggestion,
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
        "direction": int(direction) if direction in (-1, 0, 1) else 0,
        "title": title,
        "evidence": evidence,
        "suggestion": suggestion,
        "state": "open",
        "payload": json.dumps(payload, sort_keys=True),
    }
    conn = _connect()
    # Columns are named, never positional. `ALTER TABLE ... ADD COLUMN` appends
    # physically while the DDL declares the column where it belongs, so a bare
    # `INSERT INTO findings VALUES (...)` writes every value into the wrong
    # column on a migrated database -- and into the right one on a fresh
    # database, which is why no test caught it. Naming the columns makes the
    # insert independent of physical order, which is the only order that varies.
    conn.execute(
        "INSERT INTO findings"
        " (id, created_ts, miner, confidence, direction, title, evidence, suggestion, state, payload)"
        " VALUES"
        " (:id, :created_ts, :miner, :confidence, :direction, :title, :evidence, :suggestion, :state, :payload)",
        row,
    )
    conn.commit()
    conn.close()
    return row


async def async_add_finding(
    miner: str,
    confidence: float,
    title: str,
    evidence: str,
    suggestion: str,
    direction: int = 0,
    payload: Optional[Dict[str, Any]] = None,
    dedupe: bool = True,
) -> Dict[str, Any]:
    """Async wrapper that optionally enriches via larql before storing.

    When LARQL_ENABLED=1, the finding is sent to the local larql server for
    enrichment (title, summary, severity, tags) and direction classification.
    The store call is always synchronous (SQLite/Postgres); larql is opt-in.
    """
    from .larql_client import enrich_finding, classify_finding_direction

    raw = {
        "miner": miner, "confidence": confidence, "title": title,
        "evidence": evidence, "suggestion": suggestion,
        "direction": direction, "payload": payload or {},
    }
    enriched = await enrich_finding(raw)
    if enriched.get("_larql_enriched"):
        title       = enriched.get("title", title)
        suggestion  = enriched.get("suggested_action", suggestion) or suggestion
        direction   = await classify_finding_direction(enriched)
        payload     = {**(payload or {}), "larql": {
            "summary":  enriched.get("summary"),
            "severity": enriched.get("severity"),
            "tags":     enriched.get("tags", []),
        }}

    return add_finding(
        miner=miner, confidence=confidence, title=title,
        evidence=evidence, suggestion=suggestion,
        direction=direction, payload=payload, dedupe=dedupe,
    )


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


def dismiss_finding(finding_id: str) -> Optional[Dict[str, str]]:
    """Dismiss an open finding. Same contract as apply.apply_finding: None if
    not found, {"error": ...} if not currently open, else a success dict.
    Only dismisses from "open" (mirrors the Go gateway's REST dismiss
    handler, which guards state='open' in its UPDATE) so this can't silently
    un-apply an already-applied finding."""
    row = get_finding(finding_id)
    if not row:
        return None
    f = row_to_dict(row)
    if f["state"] != "open":
        return {"error": f"finding already {f['state']}"}
    set_finding_state(finding_id, "dismissed")
    return {"status": "dismissed", "id": finding_id}


# ---------------------------------------------------------------- predictions
#
# A prediction is a first-class, dated, resolvable claim about the FUTURE --
# distinct from a finding (a miner observed X right now). Every forecasting
# layer (cross_domain_signal.py today; TimesFM numeric forecasting and the
# forecasting-tools qualitative layer next) logs its forward-looking claims
# here through add_prediction(), and resolve_predictions() (mycelium/
# resolver.py) is the ONE shared job that grades them against real
# subsequently-observed substrate data once resolution_date passes -- not
# something each layer reimplements. Calibration systems (elsewhere) read
# resolved outcomes from this table rather than re-deriving them.



def _pg_predictions():
    """The Postgres backend when selected, else None.

    PostgresBackend does not implement the predictions table yet (it did not
    in the pysrc fork either -- there it was an AttributeError). Raise a
    clear NotImplementedError rather than silently falling through to a
    SQLite file the operator did not select.
    """
    pg = _pg()
    if pg is not None and not hasattr(pg, "add_prediction"):
        raise NotImplementedError(
            "predictions are SQLite-only: PostgresBackend has no predictions table yet"
        )
    return pg

def add_prediction(
    source: str,
    question: str,
    probability: float,
    resolution_date: str,
    resolution_criterion: Dict[str, Any],
    symbol: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Log one real prediction. `resolution_criterion` is a structured dict
    (not free text) so resolve_predictions() can dispatch on its own --
    must include at least {"method": ..., "description": ...}; extra keys
    are method-specific (see resolver.py). `probability` is the 0-1 P(this
    resolves 'correct'), same convention findings' confidence already uses.
    Never raises on a malformed probability -- clamps rather than rejects,
    since a forecasting layer producing a slightly-out-of-range number
    shouldn't lose the whole prediction."""
    pg = _pg_predictions()
    if pg is not None:
        return pg.add_prediction(
            source=source, question=question, probability=probability,
            resolution_date=resolution_date, resolution_criterion=resolution_criterion,
            symbol=symbol, payload=payload,
        )
    row = {
        "id": str(uuid.uuid4()),
        "created_ts": _now(),
        "source": source,
        "question": question,
        "symbol": symbol,
        "probability": max(0.0, min(1.0, float(probability))),
        "resolution_date": resolution_date,
        "resolution_criterion": json.dumps(resolution_criterion, sort_keys=True),
        "status": "open",
        "outcome": None,
        "resolved_ts": None,
        "payload": json.dumps(payload or {}, sort_keys=True),
    }
    conn = _connect()
    conn.execute(
        """INSERT INTO predictions
           (id,created_ts,source,question,symbol,probability,resolution_date,
            resolution_criterion,status,outcome,resolved_ts,payload)
           VALUES (:id,:created_ts,:source,:question,:symbol,:probability,
                   :resolution_date,:resolution_criterion,:status,:outcome,
                   :resolved_ts,:payload)""",
        row,
    )
    conn.commit()
    conn.close()
    return row


def query_predictions(
    source: Optional[str] = None,
    status: Optional[str] = None,
    symbol: Optional[str] = None,
    limit: int = 100,
) -> List[Any]:
    pg = _pg_predictions()
    if pg is not None:
        return pg.query_predictions(source=source, status=status, symbol=symbol, limit=limit)
    conn = _connect()
    sql = "SELECT * FROM predictions WHERE 1=1"
    args: List[Any] = []
    if source:
        sql += " AND source=?"; args.append(source)
    if status:
        sql += " AND status=?"; args.append(status)
    if symbol:
        sql += " AND symbol=?"; args.append(symbol)
    sql += " ORDER BY created_ts DESC LIMIT ?"
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return rows


def get_prediction(prediction_id: str) -> Optional[Any]:
    pg = _pg_predictions()
    if pg is not None:
        return pg.get_prediction(prediction_id)
    conn = _connect()
    row = conn.execute("SELECT * FROM predictions WHERE id=?", (prediction_id,)).fetchone()
    conn.close()
    return row


def due_predictions(as_of: Optional[str] = None, limit: int = 10000) -> List[Any]:
    """Real open predictions whose resolution_date has passed as of `as_of`
    (default now) -- exactly what resolve_predictions() needs to grade this
    cycle, without every caller re-filtering query_predictions(status='open')
    by date itself."""
    pg = _pg_predictions()
    if pg is not None:
        return pg.due_predictions(as_of=as_of, limit=limit)
    conn = _connect()
    cutoff = as_of or _now()
    rows = conn.execute(
        "SELECT * FROM predictions WHERE status='open' AND resolution_date<=? "
        "ORDER BY resolution_date LIMIT ?",
        (cutoff, limit),
    ).fetchall()
    conn.close()
    return rows


def resolve_prediction(prediction_id: str, outcome: str) -> bool:
    """Grade one prediction. `outcome` is 'correct' or 'incorrect' (never
    fabricated 'unknown'-as-correct -- resolver.py returns a real third
    state for unrecognized criteria, which this still records honestly
    rather than silently dropping)."""
    pg = _pg_predictions()
    if pg is not None:
        return pg.resolve_prediction(prediction_id, outcome)
    conn = _connect()
    cur = conn.execute(
        "UPDATE predictions SET status='resolved', outcome=?, resolved_ts=? WHERE id=? AND status='open'",
        (outcome, _now(), prediction_id),
    )
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
    if d.get("resolution_criterion"):
        try:
            d["resolution_criterion"] = json.loads(d["resolution_criterion"])
        except (json.JSONDecodeError, TypeError):
            pass
    return d


def iter_rows(rows: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [row_to_dict(r) for r in rows]
