"""ares_picks.db: picks + outcomes + vetoes, plus the self-calibration
report. SQLite via stdlib — same storage ethos as the rest of the stack.

Outcome tracking is REAL data, no simulation: entry price recorded at pick
time, marks at +4h/+24h/+7d recorded by later runs (record_marks). After
>=20 resolved picks, calibration_report() compares the average return of
picks grouped by dominant component and REPORTS suggested weight nudges —
it never changes config.json itself (spec: report only).
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, List, Optional

MARKS = {"4h": 4 * 3600, "24h": 24 * 3600, "7d": 7 * 24 * 3600}

DDL = """
CREATE TABLE IF NOT EXISTS picks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    token_addr TEXT NOT NULL,
    symbol TEXT NOT NULL,
    score REAL NOT NULL,
    rank INTEGER NOT NULL,
    components TEXT NOT NULL,   -- JSON: every S_i + dominant drivers
    gates TEXT NOT NULL,        -- JSON: passed / vetoes
    entry_price REAL,
    status TEXT NOT NULL DEFAULT 'candidate'
);
CREATE INDEX IF NOT EXISTS idx_picks_token_ts ON picks(token_addr, ts);
CREATE TABLE IF NOT EXISTS outcomes (
    pick_id INTEGER NOT NULL REFERENCES picks(id),
    mark TEXT NOT NULL,         -- '4h' | '24h' | '7d'
    price REAL,
    return_pct REAL,
    ts REAL NOT NULL,
    PRIMARY KEY (pick_id, mark)
);
CREATE TABLE IF NOT EXISTS vetoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    token_addr TEXT NOT NULL,
    symbol TEXT,
    gate TEXT NOT NULL,
    reason TEXT NOT NULL
);
-- What was believed and why, frozen at the moment of the decision.
--
-- One row per pick, written in the same transaction as the pick itself: a pick
-- whose reasoning is missing is a number nobody can review, and a snapshot
-- without its pick is a claim with no outcome to test against.
--
-- The score columns are queryable projections of the record; `snapshot` is the
-- whole thing as JSON. That split is deliberate -- the projections are what a
-- trend query needs, and the JSON means the snapshot's shape can grow without
-- an ALTER on a table that already holds decisions. It is a RECORD table: this
-- is the only copy, and nothing upstream can re-derive what was believed on a
-- given day.
CREATE TABLE IF NOT EXISTS decision_snapshots (
    pick_id INTEGER PRIMARY KEY REFERENCES picks(id),
    ts REAL NOT NULL,
    token_addr TEXT NOT NULL,
    symbol TEXT NOT NULL,
    signal_score REAL,
    independence_score REAL,
    effective_actors REAL,
    raw_wallets INTEGER,
    largest_funder_group INTEGER,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    provenance_count INTEGER NOT NULL DEFAULT 0,
    authenticity_score REAL,
    scoring_version INTEGER NOT NULL DEFAULT 0,
    rule_version INTEGER,
    policy_version TEXT,
    snapshot TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_token_ts ON decision_snapshots(token_addr, ts);
CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON decision_snapshots(ts);
"""


class PickStore:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(DDL)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ------------------------------------------------------------- writes

    def record_pick(self, token_addr: str, symbol: str, score: float, rank: int,
                    components: Dict[str, Any], gates: Dict[str, Any],
                    entry_price: Optional[float], ts: float | None = None,
                    snapshot: Optional[Dict[str, Any]] = None) -> int:
        """Record a pick, and its epistemic snapshot, in one transaction.

        Both or neither. A pick without its snapshot cannot be reviewed later
        -- the question "was this belief justified by the evidence then" needs
        the evidence as it stood -- and a snapshot without a pick has no
        outcome to be scored against. Two statements, one commit, so the pair
        cannot come apart.
        """
        t = ts or time.time()
        with self.conn:  # one transaction: the pick and its reasoning land together
            cur = self.conn.execute(
                "INSERT INTO picks (ts, token_addr, symbol, score, rank, components, gates, entry_price)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (t, token_addr, symbol, score, rank,
                 json.dumps(components), json.dumps(gates), entry_price))
            pick_id = int(cur.lastrowid or 0)
            if snapshot is not None:
                record = dict(snapshot, pick_id=pick_id)
                self.conn.execute(
                    "INSERT INTO decision_snapshots"
                    " (pick_id, ts, token_addr, symbol, signal_score, independence_score,"
                    "  effective_actors, raw_wallets, largest_funder_group, evidence_count,"
                    "  provenance_count, authenticity_score, scoring_version, rule_version,"
                    "  policy_version, snapshot)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pick_id, t, token_addr, symbol,
                     record.get("signal_score"), record.get("independence_score"),
                     record.get("effective_actors"), record.get("raw_wallets"),
                     record.get("largest_funder_group"), record.get("evidence_count", 0),
                     record.get("provenance_count", 0), record.get("authenticity_score"),
                     record.get("scoring_version", 0), record.get("rule_version"),
                     record.get("policy_version"), json.dumps(record)))
        return pick_id

    def snapshot_for(self, pick_id: int) -> Optional[Dict[str, Any]]:
        """The decision record for one pick, and the outcome it eventually met.

        Returned together on purpose: the snapshot answers "why did we believe
        this", the marks answer "what happened", and neither is much use
        without the other.
        """
        row = self.conn.execute(
            "SELECT snapshot FROM decision_snapshots WHERE pick_id = ?", (pick_id,)).fetchone()
        if not row:
            return None
        out = json.loads(row["snapshot"])
        out["outcomes"] = [dict(r) for r in self.conn.execute(
            "SELECT mark, price, return_pct, ts FROM outcomes WHERE pick_id = ?"
            " ORDER BY ts", (pick_id,))]
        return out

    def record_veto(self, token_addr: str, symbol: str, vetoes: List[Dict[str, str]],
                    ts: float | None = None):
        t = ts or time.time()
        self.conn.executemany(
            "INSERT INTO vetoes (ts, token_addr, symbol, gate, reason) VALUES (?,?,?,?,?)",
            [(t, token_addr, symbol, v["gate"], v["reason"]) for v in vetoes])
        self.conn.commit()

    def last_pick_ts(self, token_addr: str) -> Optional[float]:
        row = self.conn.execute(
            "SELECT MAX(ts) AS ts FROM picks WHERE token_addr = ?", (token_addr,)).fetchone()
        return float(row["ts"]) if row and row["ts"] is not None else None

    # ---------------------------------------------------- outcome tracking

    def due_marks(self, now: float | None = None) -> List[Dict[str, Any]]:
        """Picks whose next mark window has arrived but isn't recorded yet."""
        now = now or time.time()
        due = []
        for row in self.conn.execute(
                "SELECT id, ts, token_addr, entry_price FROM picks WHERE entry_price IS NOT NULL"):
            recorded = {r["mark"] for r in self.conn.execute(
                "SELECT mark FROM outcomes WHERE pick_id = ?", (row["id"],))}
            for mark, offset in MARKS.items():
                if mark not in recorded and now >= row["ts"] + offset:
                    due.append({"pick_id": row["id"], "mark": mark,
                                "token_addr": row["token_addr"], "entry_price": row["entry_price"]})
        return due

    def record_mark(self, pick_id: int, mark: str, price: Optional[float],
                    entry_price: Optional[float], ts: float | None = None):
        ret = None
        if price is not None and entry_price:
            ret = 100.0 * (price - entry_price) / entry_price
        self.conn.execute(
            "INSERT OR REPLACE INTO outcomes (pick_id, mark, price, return_pct, ts) VALUES (?,?,?,?,?)",
            (pick_id, mark, price, ret, ts or time.time()))
        self.conn.commit()

    # ------------------------------------------------------------- reads

    def top_picks(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Latest run's picks, rank-ordered — the /picks API's row source."""
        row = self.conn.execute("SELECT MAX(ts) AS ts FROM picks").fetchone()
        if not row or row["ts"] is None:
            return []
        latest = float(row["ts"])
        rows = self.conn.execute(
            "SELECT * FROM picks WHERE ts >= ? - 60 ORDER BY rank LIMIT ?", (latest, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["components"] = json.loads(d["components"])
            d["gates"] = json.loads(d["gates"])
            out.append(d)
        return out

    def resolved_count(self, mark: str = "24h") -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM outcomes WHERE mark = ? AND return_pct IS NOT NULL",
            (mark,)).fetchone()[0])

    def calibration_report(self, mark: str = "24h", min_resolved: int = 20) -> Dict[str, Any]:
        """Per-dominant-component average return once enough picks resolved.
        Suggests weight direction (up/down/hold) by comparing each
        component's cohort return to the overall mean — REPORT ONLY."""
        n = self.resolved_count(mark)
        if n < min_resolved:
            return {"status": "insufficient_data", "resolved": n, "needed": min_resolved}
        rows = self.conn.execute(
            """SELECT p.components, o.return_pct FROM picks p
               JOIN outcomes o ON o.pick_id = p.id
               WHERE o.mark = ? AND o.return_pct IS NOT NULL""", (mark,)).fetchall()
        by_comp: Dict[str, List[float]] = {}
        all_returns: List[float] = []
        for r in rows:
            comps = json.loads(r["components"])
            # mirror scoring.composite_score's dominant rule: among PRESENT
            # components only (older rows without the flag count as present)
            present = {k: c for k, c in comps.items() if c.get("present", True)}
            if not present:
                continue
            dominant = max(present, key=lambda k: present[k]["value"] * present[k]["weight"])
            by_comp.setdefault(dominant, []).append(r["return_pct"])
            all_returns.append(r["return_pct"])
        overall = sum(all_returns) / len(all_returns)
        report = {}
        for comp, rets in by_comp.items():
            avg = sum(rets) / len(rets)
            report[comp] = {
                "picks": len(rets), "avg_return_pct": round(avg, 2),
                "vs_overall": round(avg - overall, 2),
                "suggestion": "increase weight" if avg > overall + 1
                else ("decrease weight" if avg < overall - 1 else "hold"),
            }
        return {"status": "ok", "resolved": n, "mark": mark,
                "overall_avg_return_pct": round(overall, 2), "components": report,
                "note": "suggestions only — config.json is never auto-modified"}
