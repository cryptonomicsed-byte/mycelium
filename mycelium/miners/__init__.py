"""Pattern miners — sandboxed discovery agents over the substrate.

Each miner is a pure function over traces -> list of finding payloads.
Miners are grouped into Domains (see registry.py) so the substrate stays
universal while individual domains stay pluggable. This module defines
the "agent-ops" domain: recurring_workflow/anomaly/cross_agent/opportunity,
which reason over ANY agent's generic tool_call traces (kind, action,
target, outcome — nothing wallet/trading-specific). It then imports
mycelium.miners.wallet, which registers the "wallet-intel" domain
(wallet_activity/wallet_correlation/wallet_anomaly) the same way.

Back-compat surface (unchanged for cli.py/mcp_server.py/sandbox.py):
MINERS is still a flat {name: fn} dict populated by every domain via the
registry, and run_miner/run_all still work exactly as before. New:
run_domain() and list_domains() for domain-scoped mining.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from . import registry
from .registry import (  # noqa: F401 -- re-exported for back-compat + external use
    DOMAINS,
    MINERS,
    MinerFn,
    domain_of,
    list_domains,
    register_domain,
    register_miner,
)
from .. import core

registry.register_domain(
    "agent-ops",
    "Domain-agnostic mining over any agent's generic tool_call traces: "
    "recurring workflows, failure anomalies, cross-agent shared-cause "
    "failures, and automation-ROI opportunities. Reasons only over the "
    "universal trace fields (kind/action/target/outcome) -- no assumption "
    "about what any agent is actually doing.",
)


# ---------------------------------------------------------------- helpers

def _seq_key(t: Dict[str, Any]) -> Tuple[str, str]:
    return (t["agent"], t["session"])


def _build_sequences(traces: List[Dict[str, Any]], n: int) -> Counter:
    """Ordered action n-grams per (agent, session)."""
    by_session: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for t in traces:
        if t["kind"] == "tool_call" and t.get("action"):
            by_session[_seq_key(t)].append(t["action"])
    grams: Counter = Counter()
    for seq in by_session.values():
        for i in range(len(seq) - n + 1):
            grams[tuple(seq[i:i + n])] += 1
    return grams


def _target_prefix(target: Optional[str]) -> str:
    if not target:
        return ""
    # normalize to a coarse resource: docs/xxx.md -> docs/xxx.md, keep full
    return target


# ---------------------------------------------------------------- miners

def recurring_workflow(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Detect tool sequences repeated across sessions -> compound-skill candidates."""
    findings: List[Dict[str, Any]] = []
    for n in (2, 3):
        grams = _build_sequences(traces, n)
        for seq, count in grams.most_common(8):
            if count < 3:
                continue
            seq_str = " -> ".join(seq)
            agents = sorted({t["agent"] for t in traces if t["kind"] == "tool_call"})
            findings.append({
                "miner": "recurring_workflow",
                "confidence": min(0.95, 0.5 + 0.08 * count),
                "title": f"Recurring workflow ({count}x): {seq_str}",
                "evidence": (
                    f"n={count} sessions share the sequence [{seq_str}]; "
                    f"agents={len(agents)}; extracting as a compound skill "
                    f"would collapse {count * n} calls into 1"
                ),
                "suggestion": "skill",
                "payload": {"sequence": list(seq), "count": count, "n": n},
            })
    return findings


def anomaly(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flag action error rates far above baseline (burst detection)."""
    by_action: Dict[str, List[bool]] = defaultdict(list)
    for t in traces:
        if t["kind"] == "tool_call":
            by_action[t.get("action") or "?"].append(t["outcome"] == "failure")
    findings = []
    for action, outcomes in by_action.items():
        if len(outcomes) < 4:
            continue
        rate = sum(outcomes) / len(outcomes)
        if rate >= 0.5:
            findings.append({
                "miner": "anomaly",
                "confidence": min(0.95, 0.4 + rate),
                "title": f"Failure burst on '{action}' ({rate:.0%})",
                "evidence": (
                    f"{sum(outcomes)}/{len(outcomes)} calls to '{action}' failed; "
                    f"baseline expectation is <10% — a shared root cause is likely"
                ),
                "suggestion": "alert",
                "payload": {"action": action, "failures": sum(outcomes), "total": len(outcomes), "rate": rate},
            })
    return findings


def _anomaly_alert_condition(payload: Dict[str, Any]) -> Dict[str, Any]:
    """The error_rate condition shape apply.py has always generated for
    'anomaly' findings -- moved here unchanged (same fields/defaults) so
    existing generated-alerts/*.json consumers see no behavior change."""
    return {
        "metric": "error_rate",
        "action": payload.get("action") or "?",
        "min_failures": int(payload.get("failures", 3)),
        "min_rate": float(payload.get("rate", 0.5)),
    }


def cross_agent(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Same failing action+target hit by multiple distinct agents -> shared config issue."""
    keyed: Dict[Tuple[str, str], set] = defaultdict(set)  # (action,target) -> agents
    for t in traces:
        if t["kind"] == "tool_call" and t["outcome"] == "failure" and t.get("action"):
            keyed[(t["action"], _target_prefix(t.get("target")))].add(t["agent"])
    findings = []
    for (action, target), agents in keyed.items():
        if len(agents) >= 2:
            findings.append({
                "miner": "cross_agent",
                "confidence": min(0.9, 0.5 + 0.15 * len(agents)),
                "title": f"Cross-agent failure: {action} on {target or '?'}",
                "evidence": (
                    f"{len(agents)} distinct agents ({', '.join(sorted(agents))}) "
                    f"failed on '{action}' target='{target}'; "
                    f"points to shared config/credential, not per-agent code"
                ),
                "suggestion": "config_fix",
                "payload": {"action": action, "target": target, "agents": sorted(agents)},
            })
    return findings


def opportunity(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rank compound workflows by saved calls — the strongest automation ROI."""
    findings: List[Dict[str, Any]] = []
    for n in (2, 3):
        grams = _build_sequences(traces, n)
        for seq, count in grams.most_common(8):
            if count < 3:
                continue
            saved = count * n - 1  # 1 call to the compound tool vs n*count raw calls
            if saved < 5:
                continue
            seq_str = " -> ".join(seq)
            slug = "_".join(seq)[:48].lower()
            findings.append({
                "miner": "opportunity",
                "confidence": min(0.97, 0.55 + 0.05 * count),
                "title": f"Automation opportunity: {saved} calls saved via '{slug}'",
                "evidence": (
                    f"sequence [{seq_str}] ran {count}x; a compound tool "
                    f"'{slug}' replaces {count * n} calls with 1 (net {saved} saved)"
                ),
                "suggestion": "skill",
                "payload": {"slug": slug, "sequence": list(seq), "count": count, "saved": saved},
            })
    return findings


register_miner("agent-ops", "recurring_workflow", recurring_workflow)
register_miner("agent-ops", "anomaly", anomaly, alert_condition=_anomaly_alert_condition)
register_miner("agent-ops", "cross_agent", cross_agent)
register_miner("agent-ops", "opportunity", opportunity)

# Registers the "wallet-intel" domain (wallet_activity/wallet_correlation/
# wallet_anomaly) into the same MINERS/registry -- a plugin, not a special
# case. See mycelium/miners/wallet.py's module docstring.
from . import wallet as _wallet_domain  # noqa: E402,F401

# Registers the "signal-quality" domain (structured prediction extraction +
# verifiability scoring for free-text trading calls) -- a third example of
# the same plugin pattern. See mycelium/miners/signal_quality.py's module
# docstring, including the new trace contract it establishes.
from . import signal_quality as _signal_quality_domain  # noqa: E402,F401

# Registers the "trade-source-performance" domain (persistent underperformer/
# worsening-trend/low-sample-size mining over Vantage's real
# trade_outcome_learner.py source_performance recomputes) -- a fourth
# example of the same plugin pattern. See
# mycelium/miners/trade_source_performance.py's module docstring, and
# Vantage's backend/mycelium_bridge.py for the real emitter.
from . import trade_source_performance as _trade_source_performance_domain  # noqa: E402,F401


# ---------------------------------------------------------------- runners

def run_miner(name: str) -> List[Dict[str, Any]]:
    """Run one miner over a bounded recent trace window (default last 7
    days, MYCELIUM_MINER_WINDOW_DAYS-overridable via core.recent_since) —
    was an unbounded core.query_traces(limit=100000) full-table scan on
    every mine cycle, fine at today's low trace count but not something
    that stays fine. Returns finding dicts (unsaved)."""
    traces = core.iter_rows(core.query_traces(since=core.recent_since(), limit=100000))
    fn = MINERS[name]
    out = []
    for f in fn(traces):
        f.setdefault("miner", name)
        out.append(f)
    return out


def run_domain(domain: str) -> List[Dict[str, Any]]:
    """Run every miner registered under one domain (e.g. 'wallet-intel')."""
    if domain not in DOMAINS:
        raise ValueError(f"unknown domain {domain!r}; known: {sorted(DOMAINS)}")
    out: List[Dict[str, Any]] = []
    for name in DOMAINS[domain].miners:
        out.extend(run_miner(name))
    return out


def run_all() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name in MINERS:
        out.extend(run_miner(name))
    return out
