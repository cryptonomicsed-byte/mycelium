"""council-quality domain — mines the Council agent's own real debate_verdict
decisions.

Same real gap and same reasoning as fusion_ops.py (see that module's
docstring for the full argument): the generic agent-ops miners only read
kind='tool_call' traces, council emits kind='decision' with a real,
differently-shaped payload (conviction/direction/passed/votes/reasons),
and its real outcome vocabulary is "success"/"partial" -- never the
literal "failure" string anomaly()/cross_agent() check for. Forcing those
miners to reinterpret "partial" as a failure would be guessing at a
mapping the data doesn't actually assert (a partial/ABSTAIN verdict is a
correct, intentional outcome of insufficient conviction, not an error).

council_persistent_abstain: fires when a symbol has been debated
>= MIN_DEBATES times in the window and NONE of those debates passed
(reached the real conviction threshold already visible in each verdict's
own `reasons` field, e.g. "CONVICTION 0.00 < 0.6"). Calibrated against
real observed data (2026-08-30 investigation on hostinger-vps): ANSEM had
been debated 29 times with 0 passes ever; RICHAPE 29 times with only 2
passes -- both real, not hypothetical.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from . import registry

registry.register_domain(
    "council-quality",
    "Mines the Council agent's real debate_verdict decisions (conviction/"
    "direction/passed/votes payload) -- distinct from agent-ops' generic "
    "tool_call miners, which Council never emits. Answers 'does this "
    "symbol ever reach actionable conviction', not 'did a tool call fail'.",
)

MIN_DEBATES = 5  # below this, an all-abstain streak is too short to be meaningful


def _verdicts(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for t in traces:
        if t.get("agent") != "council":
            continue
        if t.get("kind") != "decision" or t.get("action") != "debate_verdict":
            continue
        p = t.get("payload") or {}
        symbol = p.get("symbol") or t.get("target") or ""
        if not symbol:
            continue
        out.append({
            "symbol": symbol,
            "passed": bool(p.get("passed")),
            "conviction": float(p.get("conviction") or 0),
            "ts": t.get("ts"),
        })
    return out


def council_persistent_abstain(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Real per-symbol pass-rate check over Council's own recent verdicts.
    Never fabricates a finding from too little history (needs MIN_DEBATES
    real verdicts for that symbol in the window first)."""
    verdicts = _verdicts(traces)
    if not verdicts:
        return []

    by_symbol: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for v in verdicts:
        by_symbol[v["symbol"]].append(v)

    findings: List[Dict[str, Any]] = []
    for symbol, vs in sorted(by_symbol.items(), key=lambda kv: -len(kv[1])):
        if len(vs) < MIN_DEBATES:
            continue
        passes = sum(1 for v in vs if v["passed"])
        if passes > 0:
            continue
        max_conviction = max(v["conviction"] for v in vs)
        findings.append({
            "miner": "council_persistent_abstain",
            "confidence": min(0.95, 0.5 + 0.015 * len(vs)),
            "title": f"Council never reaches conviction on {symbol} ({len(vs)}x debated, 0 passes)",
            "evidence": (
                f"{len(vs)} real debate_verdict traces for {symbol}, all abstained; "
                f"highest conviction reached was {max_conviction:.3f} (needs >= 0.6 to pass)"
            ),
            "suggestion": "config_fix",
            "payload": {"symbol": symbol, "debates": len(vs), "passes": passes, "max_conviction": round(max_conviction, 4)},
        })
    return findings[:10]


registry.register_miner("council-quality", "council_persistent_abstain", council_persistent_abstain)
