"""Hard gates: any FAIL rejects the token THIS run, logged as a veto with
the gate's name — a rejection with no reason is forbidden by the spec.
Pure functions: snapshot + candidate metadata in, (passed, vetoes) out.

PAPER-ONLY is structural, not a gate: nothing in this module (or anywhere
in signal_fusion/) executes trades. Picks are candidates for the council;
LIVE execution stays behind the council's own Risk-veto path plus a manual
user flip. Non-negotiable per the spec.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple


def evaluate_gates(token_addr: str, snapshot: Dict[str, Any], cfg: Dict[str, Any],
                   recent_pick_ts: float | None = None,
                   sabbath_active: bool = False,
                   now: float | None = None) -> Tuple[bool, List[Dict[str, str]]]:
    """Returns (passed, vetoes). Every failed gate lands in vetoes with its
    name + a human-readable reason; an empty snapshot fails closed (no
    market data = no pick) rather than open."""
    g = cfg["gates"]
    now = now if now is not None else time.time()
    vetoes: List[Dict[str, str]] = []

    def veto(gate: str, reason: str):
        vetoes.append({"gate": gate, "reason": reason})

    blacklist = set(g.get("blacklist_mints") or [])
    if token_addr in blacklist:
        veto("blacklist", f"mint on engine blacklist ({token_addr[:10]}…)")

    if sabbath_active:
        veto("sabbath", "inside the configured Sabbath window — no picks")
        return False, vetoes

    if recent_pick_ts is not None and (now - recent_pick_ts) < g["dedupe_hours"] * 3600:
        veto("dedupe", f"already picked {((now - recent_pick_ts) / 3600):.1f}h ago (<{g['dedupe_hours']}h)")

    if not snapshot:
        veto("market_data", "no market snapshot — failing closed")
        return False, vetoes

    liq = float(snapshot.get("liquidity_usd") or 0)
    if liq < g["min_liquidity_usd"]:
        veto("liquidity", f"liquidity ${liq:,.0f} < ${g['min_liquidity_usd']:,}")

    top10 = float(snapshot.get("top10_share") or 0)
    if top10 >= g["max_top10_holder_share"]:
        veto("holder_concentration", f"top-10 holders {top10:.0%} ≥ {g['max_top10_holder_share']:.0%}")

    bundler = float(snapshot.get("bundler_rat_share") or 0)
    if bundler >= g["max_bundler_rat_share"]:
        veto("bundler_share", f"bundler+rat_trader {bundler:.0%} ≥ {g['max_bundler_rat_share']:.0%}")

    vol = float(snapshot.get("volume_24h_usd") or 0)
    if vol < g["min_volume_24h_usd"]:
        veto("volume", f"24h volume ${vol:,.0f} < ${g['min_volume_24h_usd']:,}")

    if snapshot.get("honeypot"):
        veto("honeypot", "flagged as honeypot")
    tax = float(snapshot.get("buy_tax") or 0)
    if tax > g["max_buy_tax"]:
        veto("buy_tax", f"buy tax {tax:.0%} > {g['max_buy_tax']:.0%}")

    age_h = (now - float(snapshot.get("created_ts") or now)) / 3600
    if age_h < g["min_token_age_hours"] and token_addr not in set(g.get("age_whitelist") or []):
        veto("token_age", f"token age {age_h:.1f}h < {g['min_token_age_hours']}h (not whitelisted)")

    return len(vetoes) == 0, vetoes
