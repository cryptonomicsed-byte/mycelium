"""S_independence — the discount on manufactured wallet count.

Wallet count is the cheapest quantity in this system to manufacture and the
quantity the score leaned on hardest. `s_wallet` already collapsed cluster
members for the smart-agreement *count* and `gates.py` already vetoed a ring
that *dominated* the buyer set, but nothing answered the graded question: three
of seven wallets sharing a funder is not an agreement of seven, and 40% of
buyers in one ring is not a veto.

These tests pin the three collapses and, just as importantly, pin that an
honest crowd is not punished by any of them.
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_fusion import scoring  # noqa: E402
from signal_fusion.sources import Signal  # noqa: E402

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "signal_fusion", "config.json")
with open(CONFIG_PATH) as fh:
    CFG = json.load(fh)

NOW = 1_800_000_000.0


def buy(wallet: str, usd: float, funder: str = "", token: str = "0xtok") -> Signal:
    return Signal(
        token_addr=token, symbol="X", direction=1, strength=0.8,
        source="wallet_activity", source_ts=NOW - 60,
        meta={"wallet": wallet, "amount_usd": usd, "tags": ["smart"], "funder": funder},
    )


class TestSIndependence(unittest.TestCase):
    def test_an_honest_crowd_is_not_discounted(self):
        """Seven wallets, seven sizes, no relationship: the full count stands."""
        sigs = [buy(f"0xh{i}", 100.0 * (i + 1)) for i in range(7)]
        ind, drv = scoring.s_independence(sigs, CFG, NOW)
        self.assertAlmostEqual(ind, 1.0, places=4)
        self.assertEqual(drv[0]["wallets"], 7)
        self.assertEqual(drv[0]["entities"], 7)
        self.assertEqual(drv[0]["effective"], 7.0)

    def test_one_funder_collapses_the_crowd_to_one_actor(self):
        """The spray: N addresses paid for by one sender is one actor."""
        sigs = [buy(f"0xs{i}", 500.0 + i, funder="0xone") for i in range(7)]
        ind, drv = scoring.s_independence(sigs, CFG, NOW)
        self.assertEqual(drv[0]["largest_funder_group"], 7)
        self.assertEqual(drv[0]["funder"], "0xone")
        self.assertEqual(len(drv[0]["funder_wallets"]), 7)
        # 7 distinct / 7 wallets / 7 in the largest funder group
        self.assertAlmostEqual(ind, 1.0 / 7, places=4)
        self.assertLess(drv[0]["effective"], 1.5)

    def test_identical_amounts_read_as_a_distribution(self):
        """Independent buyers do not agree on a size to the cent."""
        spread = [buy(f"0xa{i}", 100.0 + i * 37) for i in range(7)]
        identical = [buy(f"0xb{i}", 100.0) for i in range(7)]
        ind_spread, _ = scoring.s_independence(spread, CFG, NOW)
        ind_same, drv = scoring.s_independence(identical, CFG, NOW)
        self.assertAlmostEqual(ind_spread, 1.0, places=4)
        self.assertLess(ind_same, ind_spread)
        self.assertEqual(drv[0]["identical_amount_wallets"], 7)
        self.assertLess(drv[0]["amount_penalty"], 1.0)

    def test_a_small_identical_group_is_not_penalised(self):
        """Below identical_amount_min it is a coincidence, not a distribution."""
        sigs = [buy(f"0xc{i}", 100.0) for i in range(3)]
        ind, drv = scoring.s_independence(sigs, CFG, NOW)
        self.assertAlmostEqual(ind, 1.0, places=4)
        self.assertEqual(drv[0]["amount_penalty"], 1.0)

    def test_an_entity_graph_ring_collapses_to_one_entity(self):
        sigs = [buy(f"0xr{i}", 100.0 * (i + 1)) for i in range(7)]
        ind, drv = scoring.s_independence(
            sigs, CFG, NOW, wallet_clusters=[[f"0xr{i}" for i in range(7)]])
        self.assertEqual(drv[0]["entities"], 1)
        self.assertAlmostEqual(ind, 1.0 / 7, places=4)

    def test_a_partial_funder_group_discounts_partially(self):
        """Four of seven sharing a funder is a discount, not a veto.

        The collapse removes the three redundant addresses, not the group: the
        four funded wallets are one actor, so 7 wallets are 4 actors and the
        discount is 4/7 -- not 1/4, which would charge for being a large group
        twice over.
        """
        sigs = ([buy(f"0xm{i}", 100.0 * (i + 1)) for i in range(3)]
                + [buy(f"0xn{i}", 50.0 + i, funder="0xtwo") for i in range(4)])
        ind, drv = scoring.s_independence(sigs, CFG, NOW)
        self.assertEqual(drv[0]["largest_funder_group"], 4)
        self.assertAlmostEqual(ind, 4.0 / 7, places=4)
        self.assertAlmostEqual(drv[0]["effective"], 4.0, places=2)
        self.assertGreater(ind, 0.0)
        self.assertLess(ind, 1.0)

    def test_the_graph_and_the_signal_both_supply_funders(self):
        """Either source alone is enough; the graph wins where both speak."""
        sigs = [buy(f"0xg{i}", 70.0 * (i + 1)) for i in range(4)]
        ind_from_arg, _ = scoring.s_independence(
            sigs, CFG, NOW, funder_of={f"0xg{i}": "0xgraph" for i in range(4)})
        self.assertAlmostEqual(ind_from_arg, 1.0 / 4, places=4)

        with_meta = [buy(f"0xg{i}", 70.0 * (i + 1), funder="0xgraph") for i in range(4)]
        ind_from_meta, _ = scoring.s_independence(with_meta, CFG, NOW)
        self.assertAlmostEqual(ind_from_meta, 1.0 / 4, places=4)

    def test_no_wallet_signals_is_no_opinion_not_a_zero(self):
        """Absent data must not be scored as if it were bad data."""
        sigs = [Signal(token_addr="0xtok", symbol="X", direction=1, strength=0.9,
                       source="council_verdict", source_ts=NOW - 60,
                       meta={"verdict_id": "v1", "live": True})]
        ind, drv = scoring.s_independence(sigs, CFG, NOW)
        self.assertEqual(drv, [])
        self.assertEqual(ind, 0.0)


class TestIndependenceInComposite(unittest.TestCase):
    """The component must be present, weighted, and hand-recomputable."""

    SNAP = {"liquidity_usd": 250_000, "volume_24h_usd": 900_000,
            "volume_trend": 0.4, "holder_growth": 0.5, "top10_share": 0.2}

    def _score(self, signals, **kw):
        return scoring.composite_score(signals, self.SNAP, CFG, now=NOW, **kw)

    def test_a_spray_scores_below_an_identical_honest_crowd(self):
        honest = [buy(f"0xh{i}", 100.0 * (i + 1)) for i in range(7)]
        spray = [buy(f"0xs{i}", 100.0, funder="0xone") for i in range(7)]
        r_honest = self._score(honest)
        r_spray = self._score(spray)
        self.assertLess(r_spray["score"], r_honest["score"])
        self.assertAlmostEqual(
            r_honest["components"]["S_independence"]["value"], 1.0, places=4)
        self.assertLess(
            r_spray["components"]["S_independence"]["value"], 0.2)

    def test_the_score_still_recomputes_by_hand_from_its_components(self):
        """The transparency contract, with the new component in the sum."""
        sigs = [buy(f"0xh{i}", 100.0 * (i + 1)) for i in range(7)]
        r = self._score(sigs)
        parts = r["components"]
        present = {k: p for k, p in parts.items() if p["present"]}
        total_w = sum(p["weight"] for p in present.values())
        weighted = sum(p["value"] * p["weight"] for p in present.values())
        self.assertAlmostEqual(r["score"], 100.0 * weighted / total_w, places=1)
        self.assertIn("S_independence", present)

    def test_absent_independence_does_not_cap_the_score(self):
        """A token with no wallet signals is scored on what it has."""
        sigs = [Signal(token_addr="0xtok", symbol="X", direction=1, strength=0.9,
                       source="council_verdict", source_ts=NOW - 60,
                       meta={"verdict_id": "v1", "live": True})]
        r = self._score(sigs)
        self.assertFalse(r["components"]["S_independence"]["present"])
        self.assertGreater(r["score"], 0.0)

    def test_low_independence_suppresses_the_correlated_whale_bonus(self):
        """The two components must not contradict each other.

        S_finding paid a flat bonus for a cluster of wallets hitting one token
        while S_independence discounts exactly that width. A single-funder
        cluster must not be paid the bonus.
        """
        correlation = Signal(
            token_addr="0xtok", symbol="X", direction=0, strength=0.6,
            source="mycelium_wallet_correlation", source_ts=NOW - 60,
            meta={"cluster_wallets": ["0xw1", "0xw2", "0xw3"], "finding_id": "f1"})

        honest = [buy(f"0xh{i}", 100.0 * (i + 1)) for i in range(7)] + [correlation]
        spray = [buy(f"0xs{i}", 100.0, funder="0xone") for i in range(7)] + [correlation]

        r_honest = self._score(honest)
        r_spray = self._score(spray)

        def bonus_driver(res):
            for d in res["components"]["S_finding"]["drivers"]:
                if d.get("source") == "mycelium_wallet_correlation":
                    return d
            return None

        d_honest = bonus_driver(r_honest)
        d_spray = bonus_driver(r_spray)
        self.assertIsNotNone(d_honest)
        self.assertIsNotNone(d_spray)
        # Honest: paid. Spray: named as suppressed, contributing nothing.
        self.assertNotIn("suppressed", d_honest)
        self.assertEqual(d_honest["contrib"], CFG["correlated_whale_bonus"])
        self.assertEqual(d_spray["suppressed"], "low_independence")
        self.assertEqual(d_spray["contrib"], 0.0)

    def test_unknown_independence_leaves_the_bonus_alone(self):
        """No wallet signals to judge by is not the same as known-bad."""
        correlation = Signal(
            token_addr="0xtok", symbol="X", direction=0, strength=0.6,
            source="mycelium_wallet_correlation", source_ts=NOW - 60,
            meta={"cluster_wallets": ["0xw1", "0xw2"], "finding_id": "f1"})
        r = self._score([correlation])
        drivers = r["components"]["S_finding"]["drivers"]
        bonus = [d for d in drivers if d.get("source") == "mycelium_wallet_correlation"]
        self.assertEqual(len(bonus), 1)
        self.assertEqual(bonus[0]["contrib"], CFG["correlated_whale_bonus"])
        self.assertIsNone(bonus[0]["independence"])


class TestIndependenceConfig(unittest.TestCase):
    def test_the_knobs_are_present_and_sane(self):
        self.assertIn("S_independence", CFG["component_weights"])
        self.assertGreater(CFG["component_weights"]["S_independence"], 0)
        self.assertGreaterEqual(CFG["identical_amount_min"], 2)
        self.assertTrue(0.0 < CFG["identical_amount_penalty"] <= 1.0)
        self.assertTrue(0.0 < CFG["correlated_whale_min_independence"] <= 1.0)


if __name__ == "__main__":
    unittest.main()
