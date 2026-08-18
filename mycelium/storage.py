"""storage — backend abstraction for the substrate.

Storage-agnostic by design: SQLite (default, embedded) or Postgres
(shared, multi-node). The interface mirrors core.py's public functions so
miners, CLI, and MCP are backend-agnostic.

Selection (env):
  MYCELIUM_BACKEND=sqlite  (default)   MYCELIUM_DB=/path/to.db
  MYCELIUM_BACKEND=postgres           MYCELIUM_DATABASE_URL=postgresql://...
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from . import core


# ------------------------------------------------------------------ protocol


class StorageBackend:
    """Common substrate protocol. Implementations must satisfy every method."""

    def init_db(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def emit(self, **kwargs) -> Dict[str, Any]:
        raise NotImplementedError

    def query_traces(self, **kwargs) -> List[Any]:
        raise NotImplementedError

    def add_finding(self, **kwargs) -> Dict[str, Any]:
        raise NotImplementedError

    def query_findings(self, **kwargs) -> List[Any]:
        raise NotImplementedError

    def get_finding(self, finding_id: str) -> Optional[Any]:
        raise NotImplementedError

    def set_finding_state(self, finding_id: str, state: str) -> bool:
        raise NotImplementedError

    def counts(self) -> Dict[str, int]:
        raise NotImplementedError


# ------------------------------------------------------------------ sqlite


class SQLiteBackend(StorageBackend):
    """Default embedded backend — wraps core (which owns the SQLite schema)."""

    def init_db(self) -> None:
        core.init_db()

    def emit(self, **kwargs) -> Dict[str, Any]:
        return core.emit(**kwargs)

    def query_traces(self, **kwargs) -> List[Any]:
        return core.query_traces(**kwargs)

    def add_finding(self, **kwargs) -> Dict[str, Any]:
        return core.add_finding(**kwargs)

    def query_findings(self, **kwargs) -> List[Any]:
        return core.query_findings(**kwargs)

    def get_finding(self, finding_id: str) -> Optional[Any]:
        return core.get_finding(finding_id)

    def set_finding_state(self, finding_id: str, state: str) -> bool:
        return core.set_finding_state(finding_id, state)

    def counts(self) -> Dict[str, int]:
        return {
            "traces": len(core.query_traces(limit=100000)),
            "findings": len(core.query_findings(limit=100000)),
        }


# ------------------------------------------------------------------ postgres


class PostgresBackend(StorageBackend):
    """Shared backend — Postgres via psycopg 3. Schema mirrors SQLite."""

    DDL = """
    CREATE TABLE IF NOT EXISTS traces (
        id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        agent TEXT NOT NULL,
        session TEXT NOT NULL,
        kind TEXT NOT NULL,
        action TEXT,
        target TEXT,
        outcome TEXT,
        duration_ms BIGINT,
        payload TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_traces_ts ON traces(ts);
    CREATE INDEX IF NOT EXISTS idx_traces_agent ON traces(agent);
    CREATE INDEX IF NOT EXISTS idx_traces_action ON traces(action);
    CREATE TABLE IF NOT EXISTS findings (
        id TEXT PRIMARY KEY,
        created_ts TEXT NOT NULL,
        miner TEXT NOT NULL,
        confidence DOUBLE PRECISION NOT NULL,
        title TEXT NOT NULL,
        evidence TEXT NOT NULL,
        suggestion TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'open',
        payload TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_findings_state ON findings(state);
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """

    def __init__(self, url: Optional[str] = None):
        self.url = url or os.environ.get(
            "MYCELIUM_DATABASE_URL",
            "postgresql://mycelium:mycelium@127.0.0.1:5432/mycelium",
        )
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise RuntimeError("psycopg not installed: pip install 'psycopg[binary]'") from exc
        self._psycopg = psycopg

    def _conn(self):
        return self._psycopg.connect(self.url)

    def init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(self.DDL)
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('schema_version',%s) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                ("1",),
            )
            conn.commit()

    def emit(self, **kwargs) -> Dict[str, Any]:
        from .core import _now
        import uuid

        row = {
            "id": str(uuid.uuid4()),
            "ts": kwargs.get("ts") or _now(),
            "agent": kwargs["agent"],
            "session": kwargs["session"],
            "kind": kwargs["kind"],
            "action": kwargs.get("action"),
            "target": kwargs.get("target"),
            "outcome": kwargs.get("outcome", "info"),
            "duration_ms": kwargs.get("duration_ms"),
            "payload": __import__("json").dumps(kwargs.get("payload") or {}),
        }
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO traces (id,ts,agent,session,kind,action,target,outcome,duration_ms,payload) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                tuple(row.values()),
            )
            conn.commit()
        return row

    def query_traces(self, **kwargs) -> List[Any]:
        import json as _json

        sql = "SELECT * FROM traces WHERE 1=1"
        args: List[Any] = []
        for col in ("agent", "kind", "action", "outcome", "session"):
            if kwargs.get(col):
                sql += f" AND {col}=%s"
                args.append(kwargs[col])
        sql += " ORDER BY ts LIMIT %s"
        args.append(kwargs.get("limit", 500))
        with self._conn() as conn:
            cur = conn.execute(sql, tuple(args))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            if r.get("payload"):
                try:
                    r["payload"] = _json.loads(r["payload"])
                except (TypeError, ValueError):
                    pass
        return rows

    def add_finding(self, **kwargs) -> Dict[str, Any]:
        import json as _json
        import uuid

        payload = kwargs.get("payload") or {}
        dedupe = kwargs.get("dedupe", True)
        if dedupe:
            with self._conn() as conn:
                cur = conn.execute(
                    "SELECT id,state FROM findings WHERE miner=%s AND title=%s AND state != 'dismissed'",
                    (kwargs["miner"], kwargs["title"]),
                )
                for rid, state in cur.fetchall():
                    row = conn.execute(
                        "SELECT payload FROM findings WHERE id=%s", (rid,)
                    ).fetchone()
                    try:
                        if _json.loads(row[0]) == payload:
                            return {"id": rid, "duplicate": True, "state": state}
                    except (TypeError, ValueError):
                        continue
        row = {
            "id": str(uuid.uuid4()),
            "created_ts": kwargs.get("created_ts") or core._now(),
            "miner": kwargs["miner"],
            "confidence": round(float(kwargs["confidence"]), 3),
            "title": kwargs["title"],
            "evidence": kwargs["evidence"],
            "suggestion": kwargs["suggestion"],
            "state": "open",
            "payload": _json.dumps(payload, sort_keys=True),
        }
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO findings (id,created_ts,miner,confidence,title,evidence,suggestion,state,payload) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                tuple(row.values()),
            )
            conn.commit()
        return row

    def query_findings(self, **kwargs) -> List[Any]:
        import json as _json

        sql = "SELECT * FROM findings WHERE 1=1"
        args: List[Any] = []
        for col in ("miner", "state"):
            if kwargs.get(col):
                sql += f" AND {col}=%s"
                args.append(kwargs[col])
        sql += " ORDER BY confidence DESC LIMIT %s"
        args.append(kwargs.get("limit", 100))
        with self._conn() as conn:
            cur = conn.execute(sql, tuple(args))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            if r.get("payload"):
                try:
                    r["payload"] = _json.loads(r["payload"])
                except (TypeError, ValueError):
                    pass
        return rows

    def get_finding(self, finding_id: str) -> Optional[Any]:
        import json as _json

        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM findings WHERE id=%s", (finding_id,)
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            d = dict(zip(cols, row))
        if d.get("payload"):
            try:
                d["payload"] = _json.loads(d["payload"])
            except (TypeError, ValueError):
                pass
        return d

    def set_finding_state(self, finding_id: str, state: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE findings SET state=%s WHERE id=%s", (state, finding_id)
            )
            conn.commit()
            return cur.rowcount > 0

    def counts(self) -> Dict[str, int]:
        with self._conn() as conn:
            t = conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0]
            f = conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0]
        return {"traces": t, "findings": f}


# ------------------------------------------------------------------ factory


def get_backend() -> StorageBackend:
    backend = os.environ.get("MYCELIUM_BACKEND", "sqlite").lower()
    if backend == "postgres":
        return PostgresBackend()
    return SQLiteBackend()
