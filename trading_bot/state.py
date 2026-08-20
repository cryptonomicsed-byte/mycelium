"""trading_bot's own tiny state: which signal_fusion picks it has already
acted on, and how much SOL-equivalent it has committed in the last 24h.

This is NOT a paper-ledger -- Vantage's own orders/journal/portfolio tables
(reached via vantage_client.py) are the ledger of record for every order
this bot creates. This is purely local bookkeeping so the bot's own
pre-call dedupe/cap checks are fast and don't require a network round-trip
to Vantage on every decision cycle.
"""
from __future__ import annotations

import sqlite3
import time
from typing import Optional

DDL = """
CREATE TABLE IF NOT EXISTS acted_picks (
    pick_id INTEGER PRIMARY KEY,
    token_addr TEXT NOT NULL,
    ts REAL NOT NULL,
    quantity_sol REAL NOT NULL,
    order_id INTEGER,
    mode TEXT NOT NULL  -- 'paper' | 'live'
);
CREATE INDEX IF NOT EXISTS idx_acted_ts ON acted_picks(ts);
"""


class BotState:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(DDL)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def already_acted(self, pick_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM acted_picks WHERE pick_id=?", (pick_id,)).fetchone()
        return row is not None

    def sol_committed_today(self, now: Optional[float] = None) -> float:
        now = now if now is not None else time.time()
        since = now - 24 * 3600
        row = self.conn.execute(
            "SELECT COALESCE(SUM(quantity_sol),0) FROM acted_picks WHERE ts >= ?", (since,)
        ).fetchone()
        return float(row[0]) if row else 0.0

    def record_action(self, pick_id: int, token_addr: str, quantity_sol: float,
                      order_id: Optional[int], mode: str, ts: Optional[float] = None):
        self.conn.execute(
            "INSERT OR REPLACE INTO acted_picks "
            "(pick_id, token_addr, ts, quantity_sol, order_id, mode) VALUES (?,?,?,?,?,?)",
            (pick_id, token_addr, ts if ts is not None else time.time(),
             quantity_sol, order_id, mode))
        self.conn.commit()
