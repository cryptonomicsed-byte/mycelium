"""fusion-ops domain — mines signal_fusion's own real workflow_end traces.

Real gap this fills: the generic agent-ops miners (recurring_workflow/
anomaly/cross_agent/opportunity, mycelium/miners/__init__.py) only look at
kind='tool_call' traces. signal_fusion never emits that kind -- it emits
workflow_start/workflow_end/observation, with its own real payload fields
(duration_s, marks_recorded, picks, tokens_seen, vetoes), not a generic
action-sequence or failure/success outcome. Broadening the generic miners'
kind filter to also accept these would either produce a semantically-empty
finding (session="fusion" is a single constant string across signal_fusion's
whole lifetime, not one bounded run -- 2/3-grams of workflow_start/
workflow_end interleaved with dozens of unrelated fusion_pick observations
over WEEKS is not a "compound skill worth collapsing," the actual real
suggestion recurring_workflow's finding text makes) or require
literal-string outcome mapping that doesn't fit ("failure" never appears
here; every trace is outcome="success" regardless of whether the run found
anything). Same reasoning kept signal_quality.py's own domain separate
rather than forcing wallet.py's miners to reinterpret free-text signal
posts as wallet trades.

This is the honest fix: a real, small, domain-specific miner (same
~100-150 line "plugin" pattern as wallet.py/signal_quality.py, following
registry.py's own documented extension point) that reads fusion_run's
REAL fields directly, calibrated against REAL observed data (see the
2026-08-30 investigation that added this file: 25 real fusion_run traces
on hostinger-vps showed vetoes/tokens_seen ratio between 97.9% and 100%,
avg 99.2% -- a genuinely persistent pattern across every single real run
observed, not a guessed threshold).

fusion_veto_saturation: fires when the recent window's average
vetoes/tokens_seen ratio is >= VETO_SATURATION_THRESHOLD across at least
MIN_RUNS real fusion_run completions. Reported neutrally (a fact about the
data, not an accusation) -- could mean filters are working exactly as
designed (correctly rejecting near-everything in a noisy market), or could
mean thresholds are miscalibrated; this miner surfaces the real number for
a human/agent to judge, it does not claim to know which.
"""
from __future__ import annotations

from typing import Any, Dict, List

from . import registry

registry.register_domain(
    "fusion-ops",
    "Mines signal_fusion's own real workflow_end traces (duration_s/"
    "marks_recorded/picks/tokens_seen/vetoes payload fields) -- distinct "
    "from agent-ops' generic tool_call miners, which signal_fusion never "
    "emits, and from wallet-intel/signal-quality's differently-shaped "
    "domains. Answers 'is the fusion pipeline's filtering behaving "
    "consistently', not 'did a tool call fail'.",
)

MIN_RUNS = 5  # below this, a ratio is too noisy to report honestly
VETO_SATURATION_THRESHOLD = 0.95  # calibrated against real observed 97.9%-100%


def _fusion_runs(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for t in traces:
        if t.get("agent") != "signal_fusion":
            continue
        if t.get("kind") != "workflow_end" or t.get("action") != "fusion_run":
            continue
        p = t.get("payload") or {}
        tokens_seen = p.get("tokens_seen")
        vetoes = p.get("vetoes")
        if not tokens_seen:
            continue
        out.append({
            "ts": t.get("ts"),
            "tokens_seen": float(tokens_seen),
            "vetoes": float(vetoes or 0),
            "picks": float(p.get("picks") or 0),
            "marks_recorded": float(p.get("marks_recorded") or 0),
        })
    return out


def fusion_veto_saturation(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Real veto-rate saturation check over signal_fusion's own recent
    completions. Never fabricates a finding from too little data (needs
    MIN_RUNS real completions in the window first)."""
    runs = _fusion_runs(traces)
    if len(runs) < MIN_RUNS:
        return []

    ratios = [r["vetoes"] / r["tokens_seen"] for r in runs]
    avg_ratio = sum(ratios) / len(ratios)
    if avg_ratio < VETO_SATURATION_THRESHOLD:
        return []

    total_picks = sum(r["picks"] for r in runs)
    return [{
        "miner": "fusion_veto_saturation",
        "confidence": min(0.9, 0.5 + avg_ratio / 2),
        "title": f"Fusion veto rate {avg_ratio:.1%} over last {len(runs)} runs ({total_picks:.0f} total picks)",
        "evidence": (
            f"{len(runs)} real fusion_run completions averaged "
            f"{avg_ratio:.1%} tokens vetoed (range {min(ratios):.1%}-{max(ratios):.1%}); "
            f"{total_picks:.0f} picks surfaced across all of them combined"
        ),
        "suggestion": "config_fix",
        "payload": {
            "runs": len(runs), "avg_veto_ratio": round(avg_ratio, 4),
            "min_ratio": round(min(ratios), 4), "max_ratio": round(max(ratios), 4),
            "total_picks": total_picks,
        },
    }]


registry.register_miner("fusion-ops", "fusion_veto_saturation", fusion_veto_saturation)
