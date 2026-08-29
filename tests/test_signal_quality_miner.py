"""Tests for mycelium/miners/signal_quality.py — structured prediction
extraction + verifiability scoring for free-text trading calls, ported
from HKUDS/AI-Trader per the 2026-08-29 pattern audit.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mycelium.miners import registry  # noqa: E402
from mycelium.miners.signal_quality import (  # noqa: E402
    extract_prediction,
    score_signal_quality,
    signal_quality,
)


def _signal(agent, content, **payload_extra):
    return {
        "agent": agent, "session": f"s-{agent}", "kind": "decision", "action": "signal_post",
        "payload": {"content": content, **payload_extra},
    }


class TestExtraction(unittest.TestCase):
    def test_extracts_bullish_direction(self):
        p = extract_prediction({"content": "This looks like a strong buy, breakout incoming"})
        self.assertEqual(p["direction"], "up")

    def test_extracts_bearish_direction(self):
        p = extract_prediction({"content": "I'd short this, clear breakdown pattern"})
        self.assertEqual(p["direction"], "down")

    def test_extracts_flat_direction(self):
        p = extract_prediction({"content": "Expect sideways action, hold for now"})
        self.assertEqual(p["direction"], "flat")

    def test_no_direction_keywords_yields_none(self):
        p = extract_prediction({"content": "the sky is blue today"})
        self.assertIsNone(p["direction"])

    def test_extracts_target_price(self):
        p = extract_prediction({"content": "target 75000 on this one"})
        self.assertEqual(p["target_price"], 75000.0)

    def test_extracts_probability_as_fraction(self):
        p = extract_prediction({"content": "80% chance this plays out"})
        self.assertAlmostEqual(p["target_probability"], 0.8)

    def test_extracts_confidence_normalizes_to_fraction(self):
        p = extract_prediction({"content": "confidence 80"})
        self.assertAlmostEqual(p["confidence"], 0.8)
        p2 = extract_prediction({"content": "confidence 0.8"})
        self.assertAlmostEqual(p2["confidence"], 0.8)

    def test_symbol_falls_back_to_symbols_csv(self):
        p = extract_prediction({"content": "x", "symbols": "ETH,BTC"})
        self.assertEqual(p["symbol"], "ETH")


class TestScoring(unittest.TestCase):
    def test_rich_signal_scores_higher_than_bare_one(self):
        rich = score_signal_quality(
            {"content": "BTC breaking out above resistance, target 75000, confidence 80%, "
                        "because volume is surging and on-chain data confirms accumulation",
             "symbol": "BTC", "tags": ["ta"]},
            duplicate_count=0,
        )
        bare = score_signal_quality({"content": "buy now trust me"}, duplicate_count=0)
        self.assertGreater(rich["overall_score"], bare["overall_score"])

    def test_all_component_scores_clamped_to_0_5(self):
        r = score_signal_quality(
            {"content": "target 1 " * 200, "symbol": "BTC", "tags": ["a", "b"]}, duplicate_count=0
        )
        for key in ("verifiability_score", "evidence_score", "specificity_score", "novelty_score", "review_score", "overall_score"):
            self.assertGreaterEqual(r[key], 0.0)
            self.assertLessEqual(r[key], 5.0)

    def test_duplicate_count_reduces_novelty_score(self):
        fresh = score_signal_quality({"content": "pump incoming"}, duplicate_count=0)
        stale = score_signal_quality({"content": "pump incoming"}, duplicate_count=4)
        self.assertLess(stale["novelty_score"], fresh["novelty_score"])

    def test_review_score_is_inert_placeholder_not_fabricated(self):
        r = score_signal_quality({"content": "anything"}, duplicate_count=0)
        self.assertEqual(r["review_score"], 1.0)


class TestMiner(unittest.TestCase):
    def test_ignores_non_signal_traces(self):
        traces = [{"agent": "a", "session": "s", "kind": "tool_call", "action": "patch", "payload": {}}]
        self.assertEqual(signal_quality(traces), [])

    def test_ignores_signal_post_with_no_content_or_title(self):
        traces = [{"agent": "a", "session": "s", "kind": "decision", "action": "signal_post", "payload": {}}]
        self.assertEqual(signal_quality(traces), [])

    def test_empty_window_returns_no_findings(self):
        self.assertEqual(signal_quality([]), [])

    def test_high_quality_signal_produces_a_finding(self):
        traces = [_signal(
            "agent-a",
            "BTC breaking out above resistance, target 75000, confidence 80%, "
            "because volume is surging and on-chain data confirms accumulation",
            symbol="BTC", tags=["ta"],
        )]
        found = signal_quality(traces)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["miner"], "signal_quality")
        self.assertEqual(found[0]["suggestion"], "alert")
        self.assertIn("agent-a", found[0]["title"])

    def test_caps_at_max_five_high_quality_findings(self):
        traces = [_signal(f"agent-{i}", f"unique call number {i} with real reasoning because data supports it") for i in range(9)]
        found = [f for f in signal_quality(traces) if "High-quality" in f["title"]]
        self.assertLessEqual(len(found), 5)

    def test_duplicate_spam_produces_a_separate_finding(self):
        traces = [_signal(f"agent-{i}", "pump incoming") for i in range(3)]
        found = signal_quality(traces)
        spam = [f for f in found if "Duplicate-content" in f["title"]]
        self.assertEqual(len(spam), 1)
        self.assertEqual(spam[0]["payload"]["count"], 3)
        self.assertEqual(sorted(spam[0]["payload"]["agents"]), ["agent-0", "agent-1", "agent-2"])

    def test_two_duplicates_below_flag_threshold_is_not_spam(self):
        # NOVELTY_FLAG_DUPLICATE_COUNT requires >=2 EXTRA duplicates (3+ total).
        traces = [_signal("agent-a", "same call"), _signal("agent-b", "same call")]
        found = signal_quality(traces)
        spam = [f for f in found if "Duplicate-content" in f["title"]]
        self.assertEqual(spam, [])


class TestRegistryWiring(unittest.TestCase):
    def test_domain_registered(self):
        domains = registry.list_domains()
        self.assertIn("signal-quality", domains)
        self.assertIn("signal_quality", domains["signal-quality"])

    def test_miner_domain_lookup(self):
        self.assertEqual(registry.domain_of("signal_quality"), "signal-quality")

    def test_alert_condition_uses_real_score_not_fallback(self):
        payload = {"overall_score": 3.4, "verifiability_score": 3.2, "novelty_score": 5.0}
        cond = registry.alert_condition_for("signal_quality", payload)
        self.assertEqual(cond["metric"], "signal_quality_score")
        self.assertEqual(cond["min_overall_score"], 3.4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
