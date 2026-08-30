"""Tests for mycelium/miners/trade_source_performance.py -- persistent-
underperformer/worsening-trend/low-sample-size mining over Vantage's real
trade_outcome_learner.py source_performance recomputes (emitted via
backend/mycelium_bridge.py). Trace contract verified against the real
deployed gateway 2026-08-29.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mycelium.miners import registry  # noqa: E402
from mycelium.miners.trade_source_performance import (  # noqa: E402
    MIN_SNAPSHOTS_FOR_TREND,
    MIN_TRADES_TO_TRUST,
    source_performance_trend,
)


def _trace(source, window, n_trades, wins, avg_pnl_pct, ts):
    return {
        "agent": "trade_outcome_learner", "session": "source-performance-cycle",
        "kind": "observation", "action": "source_performance", "target": source, "ts": ts,
        "payload": {
            "window": window, "n_trades": n_trades, "wins": wins,
            "win_rate": (wins / n_trades) if n_trades else None,
            "avg_pnl_pct": avg_pnl_pct, "updated_at": ts,
        },
    }


class TestMinerFiltering(unittest.TestCase):
    def test_ignores_non_source_performance_traces(self):
        traces = [{"agent": "trade_outcome_learner", "kind": "observation", "action": "other", "payload": {}}]
        self.assertEqual(source_performance_trend(traces), [])

    def test_ignores_traces_from_other_agents(self):
        traces = [{"agent": "wallet_intel", "kind": "observation", "action": "source_performance",
                   "target": "s", "payload": {"n_trades": 10, "avg_pnl_pct": -5.0}}]
        self.assertEqual(source_performance_trend(traces), [])

    def test_ignores_traces_missing_required_payload_fields(self):
        traces = [{"agent": "trade_outcome_learner", "kind": "observation", "action": "source_performance",
                   "target": "s", "payload": {}}]
        self.assertEqual(source_performance_trend(traces), [])

    def test_empty_window_returns_no_findings(self):
        self.assertEqual(source_performance_trend([]), [])


class TestPersistentLoser(unittest.TestCase):
    def test_source_bad_at_every_snapshot_is_flagged(self):
        traces = [
            _trace("bad-source", "1h", MIN_TRADES_TO_TRUST, 1, -3.0, "t1"),
            _trace("bad-source", "1h", MIN_TRADES_TO_TRUST, 1, -4.0, "t2"),
        ]
        found = source_performance_trend(traces)
        losers = [f for f in found if "Persistently underperforming" in f["title"]]
        self.assertEqual(len(losers), 1)
        self.assertEqual(losers[0]["payload"]["source"], "bad-source")

    def test_one_good_snapshot_disqualifies_persistent_loser_finding(self):
        traces = [
            _trace("mixed-source", "1h", MIN_TRADES_TO_TRUST, 1, -3.0, "t1"),
            _trace("mixed-source", "1h", MIN_TRADES_TO_TRUST, 3, 1.0, "t2"),  # one good mark
        ]
        found = source_performance_trend(traces)
        losers = [f for f in found if "Persistently underperforming" in f["title"]]
        self.assertEqual(losers, [])

    def test_below_min_trades_is_not_flagged_as_persistent_loser(self):
        traces = [_trace("thin-source", "1h", MIN_TRADES_TO_TRUST - 1, 0, -5.0, "t1")]
        found = source_performance_trend(traces)
        losers = [f for f in found if "Persistently underperforming" in f["title"]]
        self.assertEqual(losers, [])

    def test_good_source_is_never_flagged(self):
        traces = [_trace("good-source", "1h", 10, 8, 5.0, "t1"), _trace("good-source", "1h", 10, 9, 6.0, "t2")]
        found = source_performance_trend(traces)
        self.assertEqual([f for f in found if "Persistently underperforming" in f["title"]], [])


class TestWorseningTrend(unittest.TestCase):
    def test_a_real_decline_across_enough_snapshots_is_flagged(self):
        traces = [
            _trace("declining-source", "24h", 10, 7, 4.0, "t1"),
            _trace("declining-source", "24h", 10, 6, 2.0, "t2"),
            _trace("declining-source", "24h", 10, 3, -1.0, "t3"),
        ]
        self.assertEqual(len(traces), MIN_SNAPSHOTS_FOR_TREND)
        found = source_performance_trend(traces)
        trend = [f for f in found if "Worsening trend" in f["title"]]
        self.assertEqual(len(trend), 1)
        self.assertAlmostEqual(trend[0]["payload"]["delta"], -5.0)

    def test_fewer_than_min_snapshots_is_not_a_trend_finding(self):
        traces = [
            _trace("short-history", "24h", 10, 7, 4.0, "t1"),
            _trace("short-history", "24h", 10, 3, -1.0, "t2"),
        ]
        self.assertLess(len(traces), MIN_SNAPSHOTS_FOR_TREND)
        found = source_performance_trend(traces)
        self.assertEqual([f for f in found if "Worsening trend" in f["title"]], [])

    def test_a_small_decline_below_threshold_is_not_flagged(self):
        traces = [
            _trace("stable-source", "24h", 10, 7, 4.0, "t1"),
            _trace("stable-source", "24h", 10, 7, 3.5, "t2"),
            _trace("stable-source", "24h", 10, 7, 3.2, "t3"),
        ]
        found = source_performance_trend(traces)
        self.assertEqual([f for f in found if "Worsening trend" in f["title"]], [])

    def test_ordered_by_ts_not_insertion_order(self):
        # Later ts inserted first -- must still compute first->last correctly.
        traces = [
            _trace("s", "1h", 10, 5, -1.0, "t3"),
            _trace("s", "1h", 10, 8, 5.0, "t1"),
            _trace("s", "1h", 10, 6, 2.0, "t2"),
        ]
        found = source_performance_trend(traces)
        trend = [f for f in found if "Worsening trend" in f["title"]]
        self.assertEqual(len(trend), 1)
        self.assertEqual(trend[0]["payload"]["first_avg_pnl_pct"], 5.0)
        self.assertEqual(trend[0]["payload"]["last_avg_pnl_pct"], -1.0)


class TestLowSampleSize(unittest.TestCase):
    def test_source_below_trust_threshold_is_flagged(self):
        traces = [_trace("new-source", "1h", 2, 1, 10.0, "t1")]
        found = source_performance_trend(traces)
        low = [f for f in found if "Low-sample-size" in f["title"]]
        self.assertEqual(len(low), 1)
        self.assertEqual(low[0]["payload"]["n_trades"], 2)

    def test_zero_trades_is_not_flagged_as_low_sample(self):
        # n_trades=0 is "no data yet", a different state than "some data,
        # not enough to trust" -- the miner shouldn't conflate them.
        traces = [_trace("empty-source", "1h", 0, 0, 0.0, "t1")]
        found = source_performance_trend(traces)
        self.assertEqual([f for f in found if "Low-sample-size" in f["title"]], [])

    def test_source_at_or_above_trust_threshold_is_not_flagged(self):
        traces = [_trace("trusted-source", "1h", MIN_TRADES_TO_TRUST, 3, 2.0, "t1")]
        found = source_performance_trend(traces)
        self.assertEqual([f for f in found if "Low-sample-size" in f["title"]], [])


class TestRegistryWiring(unittest.TestCase):
    def test_domain_registered(self):
        domains = registry.list_domains()
        self.assertIn("trade-source-performance", domains)
        self.assertIn("source_performance_trend", domains["trade-source-performance"])

    def test_miner_domain_lookup(self):
        self.assertEqual(registry.domain_of("source_performance_trend"), "trade-source-performance")

    def test_alert_condition_uses_real_fields(self):
        payload = {"source": "strategy:9", "window": "1h", "avg_pnl_pct": -4.0}
        cond = registry.alert_condition_for("source_performance_trend", payload)
        self.assertEqual(cond["metric"], "source_avg_pnl_pct")
        self.assertEqual(cond["source"], "strategy:9")
        self.assertEqual(cond["max_avg_pnl_pct"], -4.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
