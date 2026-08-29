"""wallet-intel domain — one plugin among many, not the substrate's identity.

This is real, valuable work (wallet/scanner.py's GMGN/Birdeye scanning
feeds it), but it is domain-specific: it only makes sense of traces
emitted by the wallet_intel agent with wallet_buy/wallet_sell actions and
a wallet-shaped payload (wallet/token/symbol/amount_usd/...). It used to
live inline in the same flat file as the domain-agnostic agent-ops miners
with no separation; this module is the actual plugin boundary — everything
below is wallet-specific, and nothing in core.py/storage.py/apply.py/
mycelium/miners/__init__.py needs to know any of it exists beyond the
registry.register_miner() calls at the bottom.

Following this same shape (own module, own registered domain, own miners)
is the documented pattern for any future domain -- narrative-detection,
agent-ops-for-a-different-surface, whatever comes next plugs in exactly
like this, not by copying wallet-specific code.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from . import registry

registry.register_domain(
    "wallet-intel",
    "Wallet/trading-intel mining: money-flow digests, wallet co-buying "
    "clusters, and standout wallet behaviors. Reasons over wallet_intel "
    "agent 'observation' traces with wallet_buy/wallet_sell actions and a "
    "wallet-shaped payload (wallet/token/symbol/amount_usd/...) -- specific "
    "to this domain by design, unlike agent-ops' generic tool_call miners.",
)


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


# No alert_condition builders registered here (deliberately): each of these
# three miners' payloads has a genuinely different shape (a token/wallet
# digest, a wallet-pair cluster, a single-wallet burst) and none of them is
# an error_rate condition. apply.py's generate_alert falls back to
# surfacing the real payload verbatim via registry.alert_condition_for --
# honest, versus the previous behavior of fabricating a nonsensical
# error_rate/min_failures condition for every wallet finding regardless of
# what it actually found.
registry.register_miner("wallet-intel", "wallet_activity", wallet_activity)
registry.register_miner("wallet-intel", "wallet_correlation", wallet_correlation)
registry.register_miner("wallet-intel", "wallet_anomaly", wallet_anomaly)
