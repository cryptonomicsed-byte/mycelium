"""Pattern miners — sandboxed discovery agents over the substrate.

Each miner is a pure function over traces -> list of finding payloads.
New miners are hot-swappable: register in MINERS and they are discoverable
via CLI/MCP with zero other changes.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Callable, Dict, List, Tuple

from . import core

MinerFn = Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]


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


def cross_agent(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Same failing action+target hit by multiple distinct agents -> shared config issue."""
    keyed: Dict[Tuple[str, str], set] = defaultdict(set)  # (action,target) -> agents
    for t in traces:
        if t["kind"] == "tool_call" and t["outcome"] == "failure" and t.get("action"):
            keyed[(t["action"], _target_prefix(t.get("target")))] .add(t["agent"])
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


MINERS: Dict[str, MinerFn] = {}


def _wallet_trades(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize wallet_intel observation traces into flat trade records."""
    out = []
    for t in traces:
        if t.get("agent") != "wallet_intel" or t.get("kind") != "observation":
            continue
        act = t.get("action", "")
        if act not in ("wallet_buy", "wallet_sell"):
            continue
        p = t.get("payload") or {}
        out.append({
            "wallet": t.get("target") or p.get("wallet") or "",
            "action": act,
            "token": p.get("token") or "",
            "symbol": (p.get("symbol") or "?")[:12],
            "amount_usd": float(p.get("amount_usd") or 0),
            "price_usd": p.get("price_usd"),
            "ts": float(p.get("ts") or 0),
            "tags": p.get("tags") or [],
            "source": p.get("source") or "",
            "price_change": p.get("price_change"),
        })
    return out


def wallet_digest(traces: List[Dict[str, Any]], top: int = 5) -> Dict[str, Any]:
    """'What everyone is buying' digest — shared by the miner and the CLI."""
    buys = [t for t in _wallet_trades(traces) if t["action"] == "wallet_buy"]
    if not buys:
        return {"tokens": [], "wallets": []}
    by_token: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_wallet: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for b in buys:
        if b["token"]:
            by_token[b["token"]].append(b)
        if b["wallet"]:
            by_wallet[b["wallet"]].append(b)
    tokens = []
    for tok, ts in by_token.items():
        wallets = {b["wallet"] for b in ts if b["wallet"]}
        tokens.append({
            "token": tok, "symbol": ts[0]["symbol"],
            "distinct_wallets": len(wallets), "buys": len(ts),
            "volume_usd": round(sum(b["amount_usd"] for b in ts), 2),
            "wallets": sorted(wallets)[:8],
            "smart_wallets": sum(
                1 for b in ts
                if any("smart" in (x or "").lower() or "degen" in (x or "").lower()
                       for x in b["tags"])),
        })
    tokens.sort(key=lambda x: (x["distinct_wallets"], x["volume_usd"]), reverse=True)
    wallets = []
    for w, ws in by_wallet.items():
        syms = {b["symbol"] for b in ws if b["symbol"] and b["symbol"] != "?"}
        wallets.append({
            "wallet": w, "distinct_tokens": len(syms), "buys": len(ws),
            "volume_usd": round(sum(b["amount_usd"] for b in ws), 2),
            "tags": sorted({x for b in ws for x in (b["tags"] or [])}),
            "best_price_change": max([b["price_change"] for b in ws if b.get("price_change")] or [0]),
        })
    wallets.sort(key=lambda x: x["volume_usd"], reverse=True)
    return {"tokens": tokens[:top], "wallets": wallets[:top]}


def wallet_activity(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Money-flow digest: which tokens is everyone buying, who are the movers."""
    d = wallet_digest(traces, top=5)
    if not d["tokens"]:
        return []
    top_tok = d["tokens"][0]
    return [{
        "miner": "wallet_activity",
        "confidence": min(0.9, 0.5 + 0.08 * top_tok["distinct_wallets"]),
        "title": (f"Money flow: {len(d['tokens'])} tokens tracked, "
                  f"top = {top_tok['symbol']} ({top_tok['distinct_wallets']} wallets)"),
        "evidence": "Top tokens by distinct buying wallets: " + ", ".join(
            f"{t['symbol']}({t['distinct_wallets']}w/${t['volume_usd']})" for t in d["tokens"]),
        "suggestion": "alert",
        "payload": d,
    }]


def wallet_correlation(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Wallets co-buying >=2 of the same tokens -> co-movement clusters."""
    buys = [t for t in _wallet_trades(traces)
            if t["action"] == "wallet_buy" and t["wallet"] and t["token"]]
    wt: Dict[str, set] = defaultdict(set)
    for b in buys:
        wt[b["wallet"]].add(b["token"])
    wallets = sorted(wt)
    findings = []
    seen: set = set()
    for i in range(len(wallets)):
        for j in range(i + 1, len(wallets)):
            shared = wt[wallets[i]] & wt[wallets[j]]
            if len(shared) < 2:
                continue
            key = tuple(sorted((wallets[i], wallets[j])))
            if key in seen:
                continue
            seen.add(key)
            findings.append({
                "miner": "wallet_correlation",
                "confidence": min(0.92, 0.5 + 0.2 * len(shared)),
                "title": (f"Wallet cluster: {wallets[i][:6]}.. + {wallets[j][:6]}.. "
                          f"co-bought {len(shared)} tokens"),
                "evidence": (f"Wallets {wallets[i]} and {wallets[j]} both bought: "
                             f"{', '.join(sorted(shared)[:5])}"),
                "suggestion": "alert",
                "payload": {"wallet_a": wallets[i], "wallet_b": wallets[j],
                            "shared": sorted(shared)},
            })
    return findings[:10]


def wallet_anomaly(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Burst buyers and everything-buyers — standout behaviors worth watching."""
    buys = [t for t in _wallet_trades(traces)
            if t["action"] == "wallet_buy" and t["wallet"]]
    by_wallet: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for b in buys:
        by_wallet[b["wallet"]].append(b)
    findings = []
    for w, ws in by_wallet.items():
        ws.sort(key=lambda x: x["ts"])
        # burst: >=3 buys within 10 minutes
        for i in range(len(ws) - 2):
            if ws[i]["ts"] and ws[i + 2]["ts"] and (ws[i + 2]["ts"] - ws[i]["ts"]) <= 600:
                syms = [x["symbol"] for x in ws[i:i + 3]]
                findings.append({
                    "miner": "wallet_anomaly",
                    "confidence": 0.85,
                    "title": f"Buy burst: {w[:6]}.. hit {len(syms)} tokens in <10min",
                    "evidence": (f"{w} bought {' -> '.join(syms)} within 10 minutes "
                                 f"(${sum(x['amount_usd'] for x in ws[i:i + 3]):,.0f})"),
                    "suggestion": "alert",
                    "payload": {"wallet": w, "tokens": syms, "window_s": 600},
                })
                break
        # everything-buyer: >=4 distinct tokens
        distinct = {x["token"] for x in ws if x["token"]}
        if len(distinct) >= 4:
            findings.append({
                "miner": "wallet_anomaly",
                "confidence": 0.7,
                "title": f"Everything-buyer: {w[:6]}.. bought {len(distinct)} distinct tokens",
                "evidence": (f"{w} bought {len(distinct)} distinct tokens — "
                             f"spray-and-pray or active alpha hunter"),
                "suggestion": "alert",
                "payload": {"wallet": w, "distinct_tokens": len(distinct)},
            })
    return findings[:10]


MINERS: Dict[str, MinerFn] = {
    "recurring_workflow": recurring_workflow,
    "anomaly": anomaly,
    "cross_agent": cross_agent,
    "opportunity": opportunity,
    "wallet_activity": wallet_activity,
    "wallet_correlation": wallet_correlation,
    "wallet_anomaly": wallet_anomaly,
}


def run_miner(name: str) -> List[Dict[str, Any]]:
    """Run one miner over the whole substrate; returns finding dicts (unsaved)."""
    traces = core.iter_rows(core.query_traces(limit=100000))
    fn = MINERS[name]
    out = []
    for f in fn(traces):
        f.setdefault("miner", name)
        out.append(f)
    return out


def run_all() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name in MINERS:
        out.extend(run_miner(name))
    return out
