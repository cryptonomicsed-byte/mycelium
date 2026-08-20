"""Trade memo: signal_fusion's pick rationale as a typed object.

Pattern borrowed from TradingAgents' typed structured-output decision schema
(research finding: forcing a decision into a strict schema beats hand-
formatted strings duplicated at every call site) -- applied here to turn a
pick's dominant-driver evidence into one reusable object instead of ad-hoc
string-building wherever a signal_fusion-triggered order needs a human-
readable rationale. Feeds Vantage's JournalCreate.entry_reasoning /
conviction_score fields directly; also usable anywhere else a pick's "why"
needs to render as text (dashboard, alerts).

Pure, no I/O -- same discipline as scoring.py: every field traces back to
the pick's own stored `components`, so a memo is exactly as hand-verifiable
as the score it explains.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class TradeMemo:
    symbol: str
    token_addr: str
    score: float
    dominant: str
    top_drivers: List[Dict[str, Any]]
    gates_passed: bool
    pick_id: Optional[int] = None

    @property
    def conviction_score(self) -> float:
        """0..1, matching Vantage JournalCreate.conviction_score's scale
        (signal_fusion scores 0..100)."""
        return round(self.score / 100.0, 4)

    def entry_reasoning(self, max_drivers: int = 3) -> str:
        """Human-readable rationale for Vantage's journal entry_reasoning
        field -- one line per top driver, from the SAME components a pick's
        score is hand-recomputable from (signal_fusion's transparency
        contract), not a re-derived summary."""
        lines = [f"signal_fusion: {self.symbol} scored {self.score:.1f}/100 "
                 f"(dominant: {self.dominant or 'none'})"]
        for d in self.top_drivers[:max_drivers]:
            lines.append(f"  - {_describe_driver(d)}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol, "token_addr": self.token_addr,
            "score": self.score, "dominant": self.dominant,
            "conviction_score": self.conviction_score,
            "entry_reasoning": self.entry_reasoning(),
            "gates_passed": self.gates_passed, "pick_id": self.pick_id,
        }


def _describe_driver(d: Dict[str, Any]) -> str:
    """One driver dict (from any of scoring.py's s_* driver lists) -> one
    readable line. Driver shapes differ by component (wallet/pool_source/
    verdict/finding/market) -- this switches on the keys actually present
    rather than requiring scoring.py to add a `kind` tag it doesn't
    produce today."""
    if "wallet" in d:
        tags = ",".join(d.get("tags") or []) or "untagged"
        return (f"wallet {str(d.get('wallet') or '?')[:10]}… "
                f"({tags}, quality={d.get('quality')}) contributed {d.get('contrib')}")
    if "pool_source" in d:
        return f"{d.get('pool_source')} signal (trust={d.get('trust')}) contributed {d.get('contrib')}"
    if "verdict_id" in d:
        mode = "LIVE" if d.get("live") else "paper"
        return f"council verdict #{d.get('verdict_id')} ({mode}, conviction={d.get('conviction')})"
    if "finding_id" in d:
        return f"{d.get('source')} finding #{d.get('finding_id')} (confidence={d.get('confidence')})"
    if "liquidity_usd" in d:
        return f"market: liquidity=${d.get('liquidity_usd'):,.0f}, volume_trend={d.get('volume_trend')}"
    if "cluster_wallets" in d:
        return f"correlated whale cluster: {len(d.get('cluster_wallets') or [])} wallets"
    return str(d)


def build_trade_memo(pick: Dict[str, Any]) -> TradeMemo:
    """A signal_fusion pick row (PickStore.top_picks()/record_pick shape) ->
    TradeMemo. Uses the single dominant component's top drivers, since
    that's what actually drove the score per composite_score()'s own
    `dominant` field -- not an arbitrary re-ranking."""
    components = pick.get("components") or {}
    dominant_key = pick.get("dominant") or ""
    dominant_component = components.get(dominant_key) or {}
    drivers = dominant_component.get("drivers") or []
    gates = pick.get("gates") or {}
    return TradeMemo(
        symbol=pick.get("symbol", "?"),
        token_addr=pick.get("token_addr", ""),
        score=float(pick.get("score") or 0),
        dominant=dominant_key,
        top_drivers=drivers,
        gates_passed=bool(gates.get("passed", True)),
        pick_id=pick.get("pick_id") or pick.get("id"),
    )
