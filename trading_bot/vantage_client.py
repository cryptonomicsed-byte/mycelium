"""Thin HTTP client for the Vantage trading API -- the ONLY place
trading_bot/ talks to Vantage. No wallet system, no ledger, no risk-gate
logic of its own here: every fund-safety-relevant primitive (wallet
custody, order lifecycle, paper-fill, safety caps) lives in Vantage's own
execution_engine.py / routers/trading.py and is reused verbatim through
these calls.

Every call can fail (network, auth, Vantage down) -- callers are expected
to catch and degrade, same discipline as signal_fusion/sources.py's
fetchers. This module does not swallow errors itself; it raises, so a
caller always knows whether an order was actually created before deciding
what to do next (never guess in a fund-adjacent path).
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, List, Optional


class VantageClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={"Content-Type": "application/json", "X-Agent-Key": self.api_key})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode())

    def generate_wallet(self, chain: str = "solana", system: str = "bip39",
                        label: str = "") -> Dict[str, Any]:
        """POST /api/trading/wallets/generate -- key generation/storage
        only. Does not trade; safe to call independent of any execution
        flag. Returns {id, label, chain, address, ...} per trading.py."""
        return self._request("POST", "/api/trading/wallets/generate",
                             {"chain": chain, "system": system, "label": label})

    def list_wallets(self) -> List[Dict[str, Any]]:
        return self._request("GET", "/api/trading/wallets")

    def create_order(self, wallet_id: int, symbol: str, side: str, chain: str,
                     quantity: float, trigger_reason: str = "signal_fusion",
                     signal_id: Optional[int] = None, notes: str = "") -> Dict[str, Any]:
        """POST /api/trading/orders -- creates a 'pending' row. This alone
        never moves funds: Vantage's execution_engine.py only acts on
        pending orders when TRADING_ENGINE_ENABLED (and separately
        TRADING_LIVE_ENABLED) are on, which this bot never sets."""
        return self._request("POST", "/api/trading/orders", {
            "symbol": symbol, "side": side, "chain": chain, "quantity": quantity,
            "wallet_id": wallet_id, "trigger_reason": trigger_reason,
            "signal_id": signal_id, "notes": notes,
        })

    def paper_fill_order(self, order_id: int) -> Dict[str, Any]:
        """POST /api/trading/orders/{id}/paper-fill -- Vantage's own
        simulated fill (tx_hash='paper:<uuid>', journaled 'simulated').
        This is the entire paper-trading ledger this bot uses; no new
        accounting code exists anywhere in trading_bot/."""
        return self._request("POST", f"/api/trading/orders/{order_id}/paper-fill")

    def add_journal(self, order_id: int, entry_reasoning: str,
                    conviction_score: float, tags: Optional[List[str]] = None) -> Dict[str, Any]:
        """POST /api/trading/orders/{id}/journal -- entry_reasoning/
        conviction_score come from signal_fusion.memo.TradeMemo, never
        hand-formatted per call site."""
        return self._request("POST", f"/api/trading/orders/{order_id}/journal", {
            "entry_reasoning": entry_reasoning, "conviction_score": conviction_score,
            "tags": tags or [],
        })
