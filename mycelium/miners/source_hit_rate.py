"""signal-outcome domain — per-source hit rate mined from the resolver's
signal_outcome traces (P2-03) joined against the mirrored signal_record
traces (P1-08).

Trace contract (established by mycelium/miners/signal_ledger.py):
    signal_record   agent "vantage_ledger", action "signal_record",
                    target = record id, payload has {id, source, conviction,
                    direction, ...}
    signal_outcome  agent "vantage_ledger", action "signal_outcome",
                    target = record id, payload has {window, hit, scored, ...}

One `source_hit_rate` finding per (source, window). The hit rate is null
until MIN_SAMPLES scored outcomes exist — a source with five lucky calls is
not a reliable source, and reporting 80% on n=5 would say it is. brier is the
mean squared error between each record's conviction and its realised outcome;
buckets are the calibration table that decomposes; heldout is the most recent
20% held back from the point estimate. This is what feeds the Vantage shadow
rank (P2-05): conviction x (hits+15)/(n+30), in shadow only.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from . import registry

registry.register_domain(
    "signal-outcome",
    "Per-source signal quality mined from the resolver's signal_outcome traces "
    "joined against mirrored signal_record traces: hit rate, Brier score, "
    "calibration buckets and a held-out estimate. The raw material for the "
    "shadow rank (P2-05).",
)

MIN_SAMPLES = 30
HELDOUT_FRACTION = 0.2
BUCKET_EDGES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
ECHO_BUCKET_S = 3600  # P3-02: same token + direction within an hour = one call


def _echo_key(rec: dict) -> Tuple[str, str, int]:
    """P3-02: records a source repeats about the same token, same direction,
    in the same hour are one call, not independent evidence."""
    token = str(rec.get("address") or rec.get("symbol") or rec.get("id") or "").upper()
    ts = rec.get("created_at") or 0
    try:
        bucket = int(float(ts)) // ECHO_BUCKET_S
    except (TypeError, ValueError):
        bucket = 0
    return token, str(rec.get("direction") or ""), bucket


def _effective(keyed: List[Tuple[Tuple[str, str, int], bool]]) -> Tuple[int, float]:
    """n_eff = distinct calls; hits_eff = sum of each call's mean hit."""
    clusters: Dict[Tuple[str, str, int], List[bool]] = defaultdict(list)
    for k, h in keyed:
        clusters[k].append(h)
    return len(clusters), sum(sum(v) / len(v) for v in clusters.values())


def _parse(p: Any) -> dict:
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except (TypeError, ValueError):
            return {}
    return p if isinstance(p, dict) else {}


def _record_index(traces: List[Dict[str, Any]]) -> Dict[str, dict]:
    idx: Dict[str, dict] = {}
    for t in traces:
        if t.get("agent") != "vantage_ledger" or t.get("action") != "signal_record":
            continue
        p = _parse(t.get("payload"))
        rid = p.get("id") or t.get("target")
        if rid:
            idx[rid] = p
    return idx


def _scored_outcomes(traces: List[Dict[str, Any]]) -> List[Tuple[str, dict]]:
    out = []
    for t in traces:
        if t.get("agent") != "vantage_ledger" or t.get("action") != "signal_outcome":
            continue
        p = _parse(t.get("payload"))
        rid = t.get("target")
        if rid and p.get("scored") and p.get("hit") is not None:
            out.append((rid, p))
    return out


def source_hit_rate(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    records = _record_index(traces)
    outcomes = _scored_outcomes(traces)
    if not outcomes:
        return []

    grouped: Dict[Tuple[str, str], List[Tuple[float, bool]]] = defaultdict(list)
    echo_keys: Dict[Tuple[str, str], List[Tuple[Tuple[str, str, int], bool]]] = defaultdict(list)
    for rid, p in outcomes:
        rec = records.get(rid)
        if not rec:
            continue
        source = rec.get("source") or "?"
        window = p.get("window") or "?"
        conviction = rec.get("conviction")
        if not isinstance(conviction, (int, float)):
            continue
        grouped[(source, window)].append((float(conviction), bool(p["hit"])))
        echo_keys[(source, window)].append((_echo_key(rec), bool(p["hit"])))

    findings: List[Dict[str, Any]] = []
    for (source, window), pairs in sorted(grouped.items()):
        n = len(pairs)
        hits = sum(1 for _, h in pairs if h)
        min_met = n >= MIN_SAMPLES
        n_eff, hits_eff = _effective(echo_keys[(source, window)])
        hit_rate = (hits / n) if min_met else None

        brier = sum((c - (1.0 if h else 0.0)) ** 2 for c, h in pairs) / n if n else None

        buckets = []
        for lo, hi in zip(BUCKET_EDGES, BUCKET_EDGES[1:]):
            in_bucket = [(c, h) for c, h in pairs if lo <= c < hi]
            if in_bucket:
                bn = len(in_bucket)
                bh = sum(1 for _, h in in_bucket if h)
                buckets.append({"conviction_lo": lo, "conviction_hi": hi,
                                "n": bn, "hits": bh, "hit_rate": round(bh / bn, 4)})

        k = max(1, int(n * HELDOUT_FRACTION))
        held_pairs = pairs[-k:]
        held_n = len(held_pairs)
        held_hits = sum(1 for _, h in held_pairs if h)
        held = {"n": held_n, "hits": held_hits,
                "hit_rate": round(held_hits / held_n, 4) if held_n else None}

        title = (
            f"source {source} {window}: {hit_rate:.1%} hit rate ({n} scored)"
            if min_met
            else f"source {source} {window}: insufficient samples ({n} scored, need {MIN_SAMPLES})"
        )
        findings.append({
            "miner": "source_hit_rate",
            "confidence": min(0.95, 0.4 + n / 100.0),
            "title": title,
            "evidence": (
                f"{hits}/{n} hits; brier={brier:.3f}; "
                f"held-out {held['n']} @ {held['hit_rate'] or 0:.1%}"
                if min_met
                else f"{hits}/{n} hits — below the {MIN_SAMPLES}-sample bar, hit_rate withheld"
            ),
            "suggestion": "shadow",
            "payload": {
                "source": source, "window": window, "n": n, "hits": hits,
                "n_eff": n_eff, "hits_eff": round(hits_eff, 4),
                "hit_rate": hit_rate,
                "brier": round(brier, 4) if brier is not None else None,
                "buckets": buckets, "heldout": held,
                "min_samples_met": min_met, "updated_at": int(time.time()),
            },
        })
    return findings


registry.register_miner("signal-outcome", "source_hit_rate", source_hit_rate)
