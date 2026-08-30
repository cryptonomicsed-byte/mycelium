"""trade-source-performance domain — real per-source trading performance
mined over time, from Vantage's trade_outcome_learner.py (2026-08-29:
backend/mycelium_bridge.py, emits after every real cycle recompute).

Trace contract (established by mycelium_bridge.py, verified against the
real deployed gateway):
    agent: "trade_outcome_learner"
    kind: "observation"
    action: "source_performance"
    target: the source name (e.g. "strategy:9", "pine:14:rsi_cross",
        "manual_ui")
    payload: {window ("1h"/"24h"), n_trades, wins, win_rate, avg_pnl_pct,
        updated_at}

Same "own module, own registered domain" plugin shape wallet.py's own
docstring documents -- follows that pattern exactly, nothing wallet- or
signal-quality-specific reused or duplicated here.

Vantage's own GET /api/trading/source-performance already exposes this as
a live snapshot (one row per source+window, most-recent only). The real
value this domain adds, that a single live snapshot structurally cannot:
mining the SEQUENCE of observation traces (multiple emissions of the same
source+window as trade_outcome_learner.py's 10-minute cycle recomputes it
over time) for trend and reliability signal -- persistent underperformance,
a source trending worse cycle over cycle, and a source with too few real
trades to trust its own number yet. Every finding here is computed
directly from real payload fields already emitted; nothing fabricated or
estimated.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from . import registry

registry.register_domain(
    "trade-source-performance",
    "Trading signal-source quality mined over time from Vantage's "
    "trade_outcome_learner.py (kind='observation', action="
    "'source_performance', agent='trade_outcome_learner'): persistent "
    "underperformers, worsening trends, and low-sample-size sources -- "
    "sequence-over-time signal a single live API snapshot can't surface.",
)

MAX_FINDINGS = 5
# A source needs at least this many observation traces for a source+window
# pair before a trend finding is trustworthy -- two snapshots either side
# of one 10-minute cycle boundary is noise, not a trend.
MIN_SNAPSHOTS_FOR_TREND = 3
# Real, conservative "trust this number" bar -- fewer real marked trades
# than this and avg_pnl_pct is dominated by one or two outlier trades, not
# a real edge. Matches the spirit of Vantage's own degen_filters.py
# dust-floor reasoning (a number that technically exists but isn't
# meaningful yet), applied here to sample size instead of market cap.
MIN_TRADES_TO_TRUST = 5
PERSISTENT_LOSS_THRESHOLD_PCT = -2.0


def _source_traces(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for t in traces:
        if t.get("agent") != "trade_outcome_learner" or t.get("kind") != "observation":
            continue
        if t.get("action") != "source_performance":
            continue
        p = t.get("payload") or {}
        if p.get("avg_pnl_pct") is None or p.get("n_trades") is None:
            continue
        out.append(t)
    return out


def source_performance_trend(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Real findings mined from the (source, window) observation-trace
    sequence: persistent losers, worsening trends, and untrustworthy
    (too-few-trades) sources. Ordered oldest-to-newest per group by trace
    `ts` (mycelium's own real ingestion timestamp, not payload updated_at
    -- consistent ordering regardless of clock skew between Vantage and
    the gateway)."""
    signals = _source_traces(traces)
    if not signals:
        return []

    by_group: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    for t in signals:
        source = t.get("target") or "?"
        window = (t.get("payload") or {}).get("window") or "?"
        by_group[(source, window)].append(t)
    for group in by_group.values():
        group.sort(key=lambda t: t.get("ts") or "")

    findings: List[Dict[str, Any]] = []

    persistent_losers = []
    for (source, window), group in by_group.items():
        latest = (group[-1].get("payload") or {})
        avg = latest.get("avg_pnl_pct")
        n_trades = latest.get("n_trades") or 0
        if avg is not None and avg <= PERSISTENT_LOSS_THRESHOLD_PCT and n_trades >= MIN_TRADES_TO_TRUST:
            # "Persistent" = every snapshot in this window's real history
            # (not just the latest one) has been at or below the loss
            # threshold -- a single bad snapshot is noise, a source that's
            # never once cleared the bar across every real recompute is a
            # genuine pattern.
            all_bad = all((g.get("payload") or {}).get("avg_pnl_pct", 0) <= PERSISTENT_LOSS_THRESHOLD_PCT for g in group)
            if all_bad:
                persistent_losers.append((source, window, avg, n_trades, len(group)))

    persistent_losers.sort(key=lambda x: x[2])  # worst avg_pnl_pct first
    for source, window, avg, n_trades, snapshots in persistent_losers[:MAX_FINDINGS]:
        findings.append({
            "miner": "source_performance_trend",
            "confidence": min(0.9, 0.5 + abs(avg) / 20.0),
            "title": f"Persistently underperforming source: {source} ({window}, avg {avg:.2f}%)",
            "evidence": (
                f"{n_trades} real marked trades, avg_pnl_pct={avg:.2f}% -- every one of "
                f"{snapshots} real recompute snapshots for this source+window has been at "
                f"or below {PERSISTENT_LOSS_THRESHOLD_PCT}%"
            ),
            "suggestion": "review_source",
            "payload": {"source": source, "window": window, "avg_pnl_pct": avg, "n_trades": n_trades, "snapshots": snapshots},
        })

    worsening = []
    for (source, window), group in by_group.items():
        if len(group) < MIN_SNAPSHOTS_FOR_TREND:
            continue
        first_avg = (group[0].get("payload") or {}).get("avg_pnl_pct")
        last_avg = (group[-1].get("payload") or {}).get("avg_pnl_pct")
        if first_avg is None or last_avg is None:
            continue
        delta = last_avg - first_avg
        if delta < -3.0:  # a real, non-trivial worsening across the window's real history
            worsening.append((source, window, first_avg, last_avg, delta, len(group)))

    worsening.sort(key=lambda x: x[4])  # biggest decline first
    for source, window, first_avg, last_avg, delta, snapshots in worsening[:MAX_FINDINGS]:
        findings.append({
            "miner": "source_performance_trend",
            "confidence": min(0.85, 0.4 + abs(delta) / 20.0),
            "title": f"Worsening trend: {source} ({window}) {first_avg:.2f}% -> {last_avg:.2f}%",
            "evidence": f"avg_pnl_pct declined {abs(delta):.2f} points across {snapshots} real recompute snapshots",
            "suggestion": "watch",
            "payload": {"source": source, "window": window, "first_avg_pnl_pct": first_avg, "last_avg_pnl_pct": last_avg, "delta": delta},
        })

    low_sample = []
    for (source, window), group in by_group.items():
        latest = (group[-1].get("payload") or {})
        n_trades = latest.get("n_trades") or 0
        if 0 < n_trades < MIN_TRADES_TO_TRUST:
            low_sample.append((source, window, n_trades, latest.get("avg_pnl_pct")))
    low_sample.sort(key=lambda x: x[2])
    for source, window, n_trades, avg in low_sample[:MAX_FINDINGS]:
        findings.append({
            "miner": "source_performance_trend",
            "confidence": 0.4,
            "title": f"Low-sample-size source: {source} ({window}, only {n_trades} real trades)",
            "evidence": f"avg_pnl_pct={avg} is based on only {n_trades} real marked trades -- not yet trustworthy",
            "suggestion": "insufficient_data",
            "payload": {"source": source, "window": window, "n_trades": n_trades, "avg_pnl_pct": avg},
        })

    return findings


def _source_performance_alert_condition(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "metric": "source_avg_pnl_pct",
        "source": payload.get("source"),
        "window": payload.get("window"),
        "max_avg_pnl_pct": payload.get("avg_pnl_pct"),
    }


registry.register_miner(
    "trade-source-performance", "source_performance_trend", source_performance_trend,
    alert_condition=_source_performance_alert_condition,
)
