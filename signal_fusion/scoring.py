"""Composite scoring: score = Σ w_i × S_i, each S_i normalized 0..1,
final scaled to 0..100. Pure functions over normalized Signals — no I/O,
no clocks except the `now` parameter, so every score is reproducible by
hand from the stored components (verification spec item 3 requires exactly
that: recompute the top pick's score from its cited drivers).

Components (SIGNAL_FUSION_PROMPT.md):
  S_signal  — net weighted pool agreement, logistic-normalized
  S_wallet  — net smart flow: Σ quality × size_norm × direction × decay
  S_council — max |conviction| × direction over recent gates-passed verdicts
  S_finding — opportunity +confidence, anomaly −, correlated whales +bonus
  S_market  — liquidity/volume/holder-growth minus concentration penalties
Time decay on everything: exp(-Δt / half_life), per-source half-lives.
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Tuple

from .sources import Signal


def decay(age_seconds: float, half_life_hours: float) -> float:
    """exp-decay with a true half-life: at age == half_life the weight is
    exactly 0.5 (exp(-ln2 · age/hl)), which keeps hand-recomputation easy."""
    if half_life_hours <= 0:
        return 1.0
    return math.exp(-math.log(2) * age_seconds / (half_life_hours * 3600))


def _logistic(x: float, scale: float = 1.0) -> float:
    """Symmetric logistic squash of a net (possibly negative) sum into 0..1
    with 0.5 at x=0 — a handful of strong agreeing signals saturates toward
    1, strong disagreement toward 0."""
    return 1.0 / (1.0 + math.exp(-x / scale))


def s_signal(signals: List[Signal], cfg: Dict[str, Any], now: float) -> Tuple[float, List[Dict[str, Any]]]:
    """Net weighted agreement of vantage pool signals for one token."""
    trust_map = cfg["source_trust"]["vantage_signal"]
    hl = cfg["half_life_hours"]["trades"]
    net = 0.0
    drivers = []
    for s in signals:
        if s.source != "vantage_signal" or s.direction == 0:
            continue
        trust = trust_map.get(s.meta.get("pool_source", "_default"), trust_map["_default"])
        d = decay(now - s.source_ts, hl)
        contrib = trust * s.strength * s.direction * d
        net += contrib
        drivers.append({"pool_source": s.meta.get("pool_source"), "pool_id": s.meta.get("pool_id"),
                        "strength": s.strength, "direction": s.direction,
                        "trust": trust, "decay": round(d, 4), "contrib": round(contrib, 4)})
    if not drivers:
        return 0.0, []
    drivers.sort(key=lambda x: -abs(x["contrib"]))
    return _logistic(net), drivers[:5]


def _wallet_to_cluster_map(wallet_clusters: List[List[str]] | None) -> Dict[str, str]:
    """[[w1,w2,...], ...] -> {wallet: 'cluster:N'}, deterministic index
    order so the same cluster set always yields the same ids (needed for
    hand-recomputable drivers)."""
    out: Dict[str, str] = {}
    for i, members in enumerate(wallet_clusters or []):
        cid = f"cluster:{i}"
        for w in members:
            out[w] = cid
    return out


def s_wallet(signals: List[Signal], cfg: Dict[str, Any], now: float,
             token_volume_24h: float, wallet_reputation: Dict[str, float] | None = None,
             wallet_clusters: List[List[str]] | None = None) -> Tuple[float, List[Dict[str, Any]]]:
    """Net smart flow. size_norm = log1p(usd)/log1p(token_24h_volume);
    ×smart_agreement_bonus when ≥N distinct smart wallets agree on buy;
    ×junk_only_penalty when the ONLY buyers carry junk tags.

    wallet_reputation (wallet_learner.py's copy_trade_score, Vantage) boosts
    a wallet's positive quality up to wallet_reputation_max_quality -- it can
    only ever raise the positive side (q_pos), never launder an explicit
    negative tag, same invariant as the tag-based quality lookup.

    wallet_clusters (ares_entity_graph.py's cluster_with rings) collapses
    every wallet in one ring to a single canonical id BEFORE counting
    distinct buyers for smart_agreement_bonus -- a 5-wallet bundler ring
    that all buy is one buyer, not five, closing the gaming vector where a
    ring fakes smart-money agreement."""
    quality = cfg["wallet_quality"]
    hl = cfg["half_life_hours"]["wallet_accumulation"]
    junk = set(cfg["junk_tags"])
    rep_cap = cfg.get("wallet_reputation_log_cap", 2000)
    rep_max_q = cfg.get("wallet_reputation_max_quality", 0.9)
    wallet_to_cluster = _wallet_to_cluster_map(wallet_clusters)
    vol_log = math.log1p(max(token_volume_24h, 1.0))
    net = 0.0
    drivers = []
    buyers: set = set()
    smart_buyers: set = set()
    non_junk_buyer_seen = False
    for s in signals:
        if s.source != "wallet_activity" or s.direction == 0:
            continue
        wallet = s.meta.get("wallet")
        tags = [t for t in (s.meta.get("tags") or [])]
        # a wallet's quality = its best positive tag unless a negative tag
        # drags it down -- min() of negatives wins over max() of positives
        # so a "smart" tag can't launder a bundler
        q_pos = max((quality.get(t, quality["_default"]) for t in tags), default=quality["_default"])
        q_neg = min((quality.get(t, 0.0) for t in tags if quality.get(t, 0.0) < 0), default=0.0)
        rep = (wallet_reputation or {}).get(wallet)
        if rep is not None:
            rep_norm = min(1.0, math.log1p(max(rep, 0.0)) / math.log1p(rep_cap)) if rep_cap > 0 else 0.0
            q_pos = max(q_pos, rep_norm * rep_max_q)
        q = q_neg if q_neg < 0 else q_pos
        size_norm = math.log1p(max(s.meta.get("amount_usd", 0.0), 0.0)) / vol_log if vol_log > 0 else 0.0
        d = decay(now - s.source_ts, hl)
        contrib = q * min(size_norm, 1.0) * s.direction * d
        net += contrib
        cluster = wallet_to_cluster.get(wallet)
        canonical = cluster or wallet
        if s.direction > 0:
            buyers.add(canonical)
            if q >= quality.get("smart", 0.6):
                smart_buyers.add(canonical)
            if not (set(tags) and set(tags).issubset(junk)):
                non_junk_buyer_seen = True
        drivers.append({"wallet": wallet, "tags": tags, "quality": q, "cluster": cluster,
                        "reputation": rep, "amount_usd": s.meta.get("amount_usd"),
                        "size_norm": round(size_norm, 4), "direction": s.direction,
                        "decay": round(d, 4), "contrib": round(contrib, 4)})
    if not drivers:
        return 0.0, []
    if len(smart_buyers) >= cfg["smart_agreement_min_wallets"]:
        net *= cfg["smart_agreement_bonus"]
    if buyers and not non_junk_buyer_seen:
        net *= cfg["junk_only_penalty"]
    drivers.sort(key=lambda x: -abs(x["contrib"]))
    return _logistic(net), drivers[:5]


def wallet_cluster_share(signals: List[Signal],
                         wallet_clusters: List[List[str]] | None) -> Tuple[float, List[str]]:
    """Max fraction of a token's DISTINCT buying wallets that belong to one
    entity-graph cluster. Feeds gates.py's bundler_ring gate: a ring using
    N addresses to fake N independent buyers should veto once it dominates
    the buyer set, not just contribute N inflated wallet_activity signals."""
    wallet_to_cluster = _wallet_to_cluster_map(wallet_clusters)
    buyers = {s.meta.get("wallet") for s in signals
             if s.source == "wallet_activity" and s.direction > 0 and s.meta.get("wallet")}
    if not buyers:
        return 0.0, []
    by_cluster: Dict[str, List[str]] = {}
    for w in buyers:
        cid = wallet_to_cluster.get(w)
        if cid:
            by_cluster.setdefault(cid, []).append(w)
    if not by_cluster:
        return 0.0, []
    dominant = max(by_cluster.values(), key=len)
    return len(dominant) / len(buyers), sorted(dominant)


def s_council(signals: List[Signal], cfg: Dict[str, Any], now: float) -> Tuple[float, List[Dict[str, Any]]]:
    """max |conviction| × direction over recent gates-passed verdicts, decayed.
    LIVE verdicts weigh council_live_multiplier× paper ones. 0 if none."""
    hl = cfg["half_life_hours"]["verdicts"]
    live_mult = cfg["council_live_multiplier"]
    best = 0.0
    driver = None
    for s in signals:
        if s.source != "council_verdict" or s.direction == 0:
            continue
        mult = live_mult if s.meta.get("live") else 1.0
        d = decay(now - s.source_ts, hl)
        val = s.strength * s.direction * mult * d
        if abs(val) > abs(best):
            best = val
            driver = {"verdict_id": s.meta.get("verdict_id"), "conviction": s.strength,
                      "direction": s.direction, "live": bool(s.meta.get("live")),
                      "decay": round(d, 4), "value": round(val, 4)}
    # best is in [-live_mult, +live_mult]; map to 0..1 with 0.5 neutral
    return 0.5 + max(-0.5, min(0.5, best / (2 * live_mult))), ([driver] if driver else [])


def s_finding(signals: List[Signal], cfg: Dict[str, Any], now: float) -> Tuple[float, List[Dict[str, Any]]]:
    """Mycelium findings: opportunity adds confidence, anomaly subtracts,
    correlated-whale accumulation adds a flat bonus when ≥N whales from one
    cluster hit the token this window."""
    trust = cfg["source_trust"]
    hl = cfg["half_life_hours"]["findings"]
    net = 0.0
    drivers = []
    cluster_wallets: set = set()
    for s in signals:
        d = decay(now - s.source_ts, hl)
        if s.source == "mycelium_opportunity":
            contrib = trust["mycelium_opportunity"] * s.strength * d
        elif s.source == "mycelium_anomaly":
            contrib = -trust["mycelium_anomaly"] * s.strength * d
        elif s.source == "mycelium_wallet_correlation":
            for w in s.meta.get("cluster_wallets") or []:
                if w:
                    cluster_wallets.add(w)
            continue
        else:
            continue
        net += contrib
        drivers.append({"finding_id": s.meta.get("finding_id"), "source": s.source,
                        "confidence": s.strength, "decay": round(d, 4), "contrib": round(contrib, 4)})
    if len(cluster_wallets) >= cfg["correlated_whale_min"]:
        net += cfg["correlated_whale_bonus"]
        drivers.append({"source": "mycelium_wallet_correlation",
                        "cluster_wallets": sorted(cluster_wallets),
                        "contrib": cfg["correlated_whale_bonus"]})
    if not drivers:
        return 0.0, []
    return _logistic(net), drivers


def s_market(snapshot: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[float, List[Dict[str, Any]]]:
    """Liquidity (log-scale vs the $5k floor), volume trend, holder growth,
    MINUS whale-concentration and bundler-share penalties. All inputs from
    the market snapshot the momentum source carried in — no second fetch."""
    if not snapshot:
        return 0.0, []
    liq = float(snapshot.get("liquidity_usd") or 0)
    liq_score = min(1.0, math.log10(max(liq, 1.0) / 5000) / 2) if liq >= 5000 else 0.0
    vol_trend = max(-1.0, min(1.0, float(snapshot.get("volume_trend") or 0)))
    holder_growth = max(0.0, min(1.0, float(snapshot.get("holder_growth") or 0)))
    whale_pct = max(0.0, min(1.0, float(snapshot.get("top10_share") or 0)))
    bundler_pct = max(0.0, min(1.0, float(snapshot.get("bundler_rat_share") or 0)))
    raw = 0.4 * liq_score + 0.3 * (vol_trend + 1) / 2 + 0.3 * holder_growth \
        - 0.5 * max(0.0, whale_pct - 0.3) - 0.5 * bundler_pct
    score = max(0.0, min(1.0, raw))
    return score, [{"liquidity_usd": liq, "liq_score": round(liq_score, 4),
                    "volume_trend": vol_trend, "holder_growth": holder_growth,
                    "top10_share": whale_pct, "bundler_rat_share": bundler_pct,
                    "raw": round(raw, 4)}]


def composite_score(token_signals: List[Signal], snapshot: Dict[str, Any],
                    cfg: Dict[str, Any], now: float | None = None,
                    wallet_reputation: Dict[str, float] | None = None,
                    wallet_clusters: List[List[str]] | None = None) -> Dict[str, Any]:
    """Everything for one token -> {score 0..100, components{...}}. The
    components dict stores each S_i, its weight, its `present` flag, AND its
    dominant drivers — the transparency contract: every pick shows WHY.

    Normalization is over PRESENT components only (those with any data):
    a component with zero signals is "no opinion", not negative evidence —
    averaging absent components as 0.0 would cap every token without a
    council verdict + findings well below the finding_score_threshold, and
    the market gates already reject data-poor garbage separately. The
    stored `present` flags keep the score hand-recomputable:
    score = 100 × Σ(value×weight | present) / Σ(weight | present)."""
    now = now if now is not None else time.time()
    trust = cfg["source_trust"]
    weights = cfg["component_weights"]

    sig, sig_drv = s_signal(token_signals, cfg, now)
    vol_24h = float((snapshot or {}).get("volume_24h_usd") or 0)
    wal, wal_drv = s_wallet(token_signals, cfg, now, vol_24h,
                            wallet_reputation=wallet_reputation, wallet_clusters=wallet_clusters)
    cou, cou_drv = s_council(token_signals, cfg, now)
    fin, fin_drv = s_finding(token_signals, cfg, now)
    mkt, mkt_drv = s_market(snapshot, cfg)

    parts = {
        "S_signal": {"value": round(sig, 4), "weight": weights["S_signal"],
                     "present": bool(sig_drv), "drivers": sig_drv},
        "S_wallet": {"value": round(wal, 4), "weight": weights["S_wallet"], "present": bool(wal_drv),
                     "trust": trust["wallet_activity"], "drivers": wal_drv},
        "S_council": {"value": round(cou, 4), "weight": weights["S_council"], "present": bool(cou_drv),
                      "trust": trust["council_verdict"], "drivers": cou_drv},
        "S_finding": {"value": round(fin, 4), "weight": weights["S_finding"],
                      "present": bool(fin_drv), "drivers": fin_drv},
        "S_market": {"value": round(mkt, 4), "weight": weights["S_market"], "present": bool(mkt_drv),
                     "trust": trust["market_momentum"], "drivers": mkt_drv},
    }
    total_w = sum(p["weight"] for p in parts.values() if p["present"])
    weighted = sum(p["value"] * p["weight"] for p in parts.values() if p["present"])
    score = 100.0 * weighted / total_w if total_w > 0 else 0.0
    present = {k: p for k, p in parts.items() if p["present"]}
    dominant = max(present, key=lambda k: present[k]["value"] * present[k]["weight"]) if present else "none"
    return {"score": round(score, 2), "components": parts, "dominant": dominant}
