"""Domain registry — the pluggable-substrate abstraction.

This is the answer to "how does a new domain plug into Mycelium." The
substrate itself (core.py's trace/finding schema, storage.py) already has
zero domain-specific fields — traces and findings are generic, with all
domain meaning carried in the free-form `payload` JSON and the `miner`
name string. What was missing was a registration pattern: miners.py used
to hold a single flat MINERS dict with agent-ops miners (recurring_workflow,
anomaly, cross_agent, opportunity — domain-agnostic, they reason over any
agent's tool_call traces) and wallet-intel miners (wallet_activity,
wallet_correlation, wallet_anomaly — real, valuable, but specific to the
wallet-scanning domain: wallet_intel agent, wallet_buy/wallet_sell actions)
side by side with no grouping, no way to say "these three belong together
and mean something different from those four."

A Domain is that grouping: a name, a description, and the miners that
belong to it. Two live today — "agent-ops" (mycelium/miners/__init__.py)
and "wallet-intel" (mycelium/miners/wallet.py). Adding domain #3 (say,
narrative-detection) means: write its miners in their own module, call
register_domain() + register_miner() for each, done — no change to core.py,
storage.py, apply.py's dispatch, sandbox.py, cli.py, or mcp_server.py. All
of those already only ever touch the flat MINERS dict (for back-compat)
or, where they care about domain grouping, this registry.

Findings from different domains often need different alert-condition
shapes when applied (see apply.py's generate_alert): agent-ops' `anomaly`
miner findings are naturally an error_rate/action/min_failures condition;
a wallet_activity finding's payload is a token/wallet volume digest, and
forcing it through the same error_rate shape produces a nonsensical
condition (a real bug this refactor fixes — see apply.py). So
alert-condition builders are registered per-miner (the grain that actually
varies), not per-domain: most miners don't need one and get a generic
"here's the raw payload" fallback, which is honest rather than fabricated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

MinerFn = Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]
AlertConditionFn = Callable[[Dict[str, Any]], Dict[str, Any]]


@dataclass
class Domain:
    name: str
    description: str
    miners: Dict[str, MinerFn] = field(default_factory=dict)


DOMAINS: Dict[str, Domain] = {}
MINERS: Dict[str, MinerFn] = {}
MINER_DOMAIN: Dict[str, str] = {}
_ALERT_CONDITION_BUILDERS: Dict[str, AlertConditionFn] = {}


def register_domain(name: str, description: str) -> Domain:
    """Idempotent: safe to call once per domain module at import time."""
    existing = DOMAINS.get(name)
    if existing is not None:
        return existing
    d = Domain(name=name, description=description)
    DOMAINS[name] = d
    return d


def register_miner(
    domain: str,
    name: str,
    fn: MinerFn,
    alert_condition: Optional[AlertConditionFn] = None,
) -> None:
    """Register one miner into a domain (must already be registered).

    `alert_condition`, if given, is a `payload -> condition dict` builder
    used by apply.py's generate_alert when a finding from this miner is
    applied as an 'alert'. Miners whose findings aren't naturally an
    alert-worthy condition (most 'skill'/'config_fix' miners) simply omit
    it; apply.py falls back to surfacing the raw payload rather than
    guessing a shape that doesn't fit.
    """
    if domain not in DOMAINS:
        raise ValueError(f"domain {domain!r} not registered -- call register_domain() first")
    DOMAINS[domain].miners[name] = fn
    MINERS[name] = fn
    MINER_DOMAIN[name] = domain
    if alert_condition is not None:
        _ALERT_CONDITION_BUILDERS[name] = alert_condition


def domain_of(miner_name: str) -> Optional[str]:
    return MINER_DOMAIN.get(miner_name)


def list_domains() -> Dict[str, List[str]]:
    """Domain name -> sorted list of its registered miner names."""
    return {name: sorted(d.miners) for name, d in DOMAINS.items()}


def alert_condition_for(miner_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build the 'condition' block for a generated alert (see apply.py).

    Delegates to the miner's registered builder if one exists; otherwise
    returns the finding's own payload verbatim under a 'raw' metric --
    never a fabricated error_rate/min_failures shape that doesn't apply
    to this miner's actual data (the bug this registry replaces).
    """
    builder = _ALERT_CONDITION_BUILDERS.get(miner_name)
    if builder is not None:
        return builder(payload)
    return {"metric": "raw", "payload": payload}
