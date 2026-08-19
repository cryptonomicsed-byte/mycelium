"""Unit tests for the signal-fusion engine (stdlib unittest, no network).

The scoring tests use hand-recomputable numbers on purpose: the fusion
spec's verification requires recomputing a pick's score by hand from its
stored components, so these tests ARE that recomputation, pinned.
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from signal_fusion import gates, scoring, sources  # noqa: E402
from signal_fusion.signal_fusion import run_once  # noqa: E402
from signal_fusion.store import PickStore  # noqa: E402

CFG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "signal_fusion", "config.json")
NOW = 1_760_000_000.0  # pinned clock: decay factors are exact, tests never rot


def cfg():
    with open(CFG_PATH) as fh:
        return json.load(fh)


def sig(**kw):
    base = dict(token_addr="0xTOK", symbol="TOK", direction=1, strength=1.0,
                source="vantage_signal", source_ts=NOW, meta={})
    base.update(kw)
    return sources.Signal(**base)


GOOD_SNAPSHOT = {
    "token": "0xTOK", "symbol": "TOK", "price_usd": 1.0,
    "liquidity_usd": 50_000, "volume_24h_usd": 100_000, "volume_trend": 0.5,
    "holder_growth": 0.4, "top10_share": 0.25, "bundler_rat_share": 0.05,
    "honeypot": False, "buy_tax": 0.02, "created_ts": NOW - 48 * 3600,
}


class TestDecay(unittest.TestCase):
    def test_half_life_is_exactly_half(self):
        self.assertAlmostEqual(scoring.decay(12 * 3600, 12), 0.5, places=9)

    def test_zero_age_is_one(self):
        self.assertEqual(scoring.decay(0, 12), 1.0)

    def test_zero_half_life_disables_decay(self):
        self.assertEqual(scoring.decay(999999, 0), 1.0)


class TestSSignal(unittest.TestCase):
    def test_hand_recomputable(self):
        c = cfg()
        # one fresh smart_money buy at strength 0.8:
        # net = 0.90 * 0.8 * 1 * 1.0 = 0.72 -> logistic(0.72)
        s, drivers = scoring.s_signal(
            [sig(strength=0.8, meta={"pool_source": "smart_money"})], c, NOW)
        self.assertAlmostEqual(s, 1 / (1 + math.exp(-0.72)), places=9)
        self.assertEqual(drivers[0]["trust"], 0.90)

    def test_sell_signals_suppress(self):
        c = cfg()
        s_buy, _ = scoring.s_signal([sig(meta={"pool_source": "smart_money"})], c, NOW)
        s_mixed, _ = scoring.s_signal([
            sig(meta={"pool_source": "smart_money"}),
            sig(direction=-1, meta={"pool_source": "smart_money"}),
        ], c, NOW)
        self.assertLess(s_mixed, s_buy)
        self.assertAlmostEqual(s_mixed, 0.5, places=9)  # perfectly cancelled

    def test_no_signals_is_zero(self):
        self.assertEqual(scoring.s_signal([], cfg(), NOW), (0.0, []))


class TestSWallet(unittest.TestCase):
    def wallet_sig(self, wallet, tags, usd, direction=1, ts=NOW):
        return sig(source="wallet_activity", direction=direction, source_ts=ts,
                   meta={"wallet": wallet, "tags": tags, "amount_usd": usd})

    def test_negative_tag_beats_positive(self):
        c = cfg()
        # smart + bundler wallet: quality must be the bundler's -0.70, so a
        # solo buy from it must land BELOW neutral (0.5)
        s, drivers = scoring.s_wallet(
            [self.wallet_sig("0xW", ["smart", "bundler"], 5_000)], c, NOW, 100_000)
        self.assertEqual(drivers[0]["quality"], -0.70)
        self.assertLess(s, 0.5)

    def test_smart_agreement_bonus_applies(self):
        c = cfg()
        three = [self.wallet_sig(f"0xW{i}", ["alpha"], 5_000) for i in range(3)]
        two = three[:2]
        s3, _ = scoring.s_wallet(three, c, NOW, 100_000)
        s2, _ = scoring.s_wallet(two, c, NOW, 100_000)
        # recompute s3 by hand: per-wallet contrib = 1.0 * size_norm * 1 * 1
        size_norm = math.log1p(5_000) / math.log1p(100_000)
        net3 = 3 * size_norm * c["smart_agreement_bonus"]
        self.assertAlmostEqual(s3, 1 / (1 + math.exp(-net3)), places=9)
        self.assertGreater(s3, s2)

    def test_junk_only_buyers_penalized(self):
        c = cfg()
        junk, _ = scoring.s_wallet(
            [self.wallet_sig("0xS", ["sniper"], 5_000)], c, NOW, 100_000)
        clean, _ = scoring.s_wallet(
            [self.wallet_sig("0xA", ["alpha"], 5_000)], c, NOW, 100_000)
        self.assertLess(junk, clean)


class TestSCouncil(unittest.TestCase):
    def test_live_outweighs_paper(self):
        c = cfg()
        paper = [sig(source="council_verdict", strength=0.8, meta={"live": False})]
        live = [sig(source="council_verdict", strength=0.8, meta={"live": True})]
        sp, _ = scoring.s_council(paper, c, NOW)
        sl, _ = scoring.s_council(live, c, NOW)
        self.assertGreater(sl, sp)
        # hand recompute: paper value = 0.8 -> 0.5 + 0.8/(2*1.5)
        self.assertAlmostEqual(sp, 0.5 + 0.8 / 3.0, places=9)

    def test_no_verdicts_is_neutral(self):
        s, drivers = scoring.s_council([], cfg(), NOW)
        self.assertEqual((s, drivers), (0.5, []))


class TestSFinding(unittest.TestCase):
    def test_opportunity_adds_anomaly_subtracts(self):
        c = cfg()
        opp = [sig(source="mycelium_opportunity", strength=0.9, meta={"finding_id": "f1"})]
        ano = [sig(source="mycelium_anomaly", strength=0.9, meta={"finding_id": "f2"})]
        so, _ = scoring.s_finding(opp, c, NOW)
        sa, _ = scoring.s_finding(ano, c, NOW)
        self.assertGreater(so, 0.5)
        self.assertLess(sa, 0.5)

    def test_correlated_whale_bonus(self):
        c = cfg()
        corr = [sig(source="mycelium_wallet_correlation",
                    meta={"cluster_wallets": ["0xA", "0xB"]})]
        s, drivers = scoring.s_finding(corr, c, NOW)
        # bonus alone: logistic(0.6)
        self.assertAlmostEqual(s, 1 / (1 + math.exp(-0.6)), places=9)
        self.assertEqual(drivers[0]["cluster_wallets"], ["0xA", "0xB"])


class TestGates(unittest.TestCase):
    def test_good_snapshot_passes(self):
        passed, vetoes = gates.evaluate_gates("0xTOK", GOOD_SNAPSHOT, cfg(), now=NOW)
        self.assertTrue(passed, vetoes)

    def test_every_gate_fires_with_its_name(self):
        c = cfg()
        cases = [
            ({"liquidity_usd": 1_000}, "liquidity"),
            ({"top10_share": 0.75}, "holder_concentration"),
            ({"bundler_rat_share": 0.40}, "bundler_share"),
            ({"volume_24h_usd": 500}, "volume"),
            ({"honeypot": True}, "honeypot"),
            ({"buy_tax": 0.30}, "buy_tax"),
            ({"created_ts": NOW - 60}, "token_age"),
        ]
        for override, gate_name in cases:
            snap = dict(GOOD_SNAPSHOT, **override)
            passed, vetoes = gates.evaluate_gates("0xTOK", snap, c, now=NOW)
            self.assertFalse(passed, f"{gate_name} should veto")
            self.assertIn(gate_name, [v["gate"] for v in vetoes])

    def test_missing_snapshot_fails_closed(self):
        passed, vetoes = gates.evaluate_gates("0xTOK", {}, cfg(), now=NOW)
        self.assertFalse(passed)
        self.assertEqual(vetoes[0]["gate"], "market_data")

    def test_dedupe_and_sabbath(self):
        c = cfg()
        passed, vetoes = gates.evaluate_gates(
            "0xTOK", GOOD_SNAPSHOT, c, recent_pick_ts=NOW - 3600, now=NOW)
        self.assertFalse(passed)
        self.assertIn("dedupe", [v["gate"] for v in vetoes])
        passed, vetoes = gates.evaluate_gates(
            "0xTOK", GOOD_SNAPSHOT, c, sabbath_active=True, now=NOW)
        self.assertFalse(passed)
        self.assertEqual(vetoes[0]["gate"], "sabbath")

    def test_age_whitelist_bypasses_age_gate_only(self):
        c = cfg()
        c["gates"]["age_whitelist"] = ["0xTOK"]
        snap = dict(GOOD_SNAPSHOT, created_ts=NOW - 60)
        passed, vetoes = gates.evaluate_gates("0xTOK", snap, c, now=NOW)
        self.assertTrue(passed, vetoes)


class TestStore(unittest.TestCase):
    def test_pick_roundtrip_marks_and_report(self):
        store = PickStore(":memory:")
        pid = store.record_pick("0xTOK", "TOK", 87.5, 1,
                                {"S_signal": {"value": 0.9, "weight": 1.0}},
                                {"passed": True, "vetoes": []}, entry_price=1.0, ts=NOW)
        self.assertEqual(store.last_pick_ts("0xTOK"), NOW)
        top = store.top_picks()
        self.assertEqual(top[0]["symbol"], "TOK")
        self.assertEqual(top[0]["components"]["S_signal"]["value"], 0.9)

        # +4h mark due after 4h, not before
        self.assertEqual(store.due_marks(now=NOW + 3600), [])
        due = store.due_marks(now=NOW + 5 * 3600)
        self.assertEqual([(d["pick_id"], d["mark"]) for d in due], [(pid, "4h")])
        store.record_mark(pid, "4h", price=1.5, entry_price=1.0, ts=NOW + 5 * 3600)
        row = store.conn.execute("SELECT return_pct FROM outcomes WHERE pick_id=?", (pid,)).fetchone()
        self.assertAlmostEqual(row["return_pct"], 50.0)

        report = store.calibration_report(mark="4h", min_resolved=1)
        self.assertEqual(report["status"], "ok")
        self.assertIn("S_signal", report["components"])

    def test_calibration_needs_min_resolved(self):
        store = PickStore(":memory:")
        self.assertEqual(store.calibration_report()["status"], "insufficient_data")


class TestRunOnce(unittest.TestCase):
    def test_full_pass_over_synthetic_signals(self):
        c = cfg()
        c["endpoints"]["mycelium_gateway"] = ""  # no tracing in tests
        c["endpoints"]["vantage_base"] = ""  # no mirroring in tests
        store = PickStore(":memory:")
        signals = [
            # good token: strong smart-money + wallet buys + market snapshot
            sig(meta={"pool_source": "smart_money"}, strength=0.9),
            sig(source="wallet_activity",
                meta={"wallet": "0xA", "tags": ["alpha"], "amount_usd": 8_000}),
            sig(source="market_momentum", strength=0.5,
                meta={"snapshot": GOOD_SNAPSHOT}),
            # bad token: no market snapshot at all -> gate fails closed
            sig(token_addr="0xBAD", symbol="BAD", meta={"pool_source": "news"}),
        ]
        result = run_once(c, store, signals=signals, now=NOW)
        self.assertEqual(result["tokens_seen"], 2)
        self.assertEqual(result["vetoes"], 1)
        self.assertEqual(len(result["picks"]), 1)
        pick = result["picks"][0]
        self.assertEqual(pick["symbol"], "TOK")
        self.assertEqual(pick["rank"], 1)
        self.assertGreater(pick["score"], 50)
        # the stored row must carry the same reproducible components -- and
        # the score must be recomputable BY HAND from them (verification
        # spec item 3): 100 x sum(v*w | present) / sum(w | present)
        stored = store.top_picks()[0]
        self.assertEqual(stored["score"], pick["score"])
        self.assertIn("drivers", stored["components"]["S_wallet"])
        present = [p for p in stored["components"].values() if p["present"]]
        recomputed = 100.0 * sum(p["value"] * p["weight"] for p in present) \
            / sum(p["weight"] for p in present)
        self.assertAlmostEqual(stored["score"], round(recomputed, 2), places=2)
        # absent components (no council verdicts, no findings here) are
        # flagged not-present and excluded from the mean
        self.assertFalse(stored["components"]["S_council"]["present"])
        self.assertFalse(stored["components"]["S_finding"]["present"])
        # the veto is in the vetoes table with its gate name
        veto = store.conn.execute("SELECT gate FROM vetoes WHERE token_addr='0xBAD'").fetchone()
        self.assertEqual(veto["gate"], "market_data")

    def test_dedupe_across_runs(self):
        c = cfg()
        c["endpoints"]["mycelium_gateway"] = ""
        c["endpoints"]["vantage_base"] = ""
        store = PickStore(":memory:")
        signals = [
            sig(meta={"pool_source": "smart_money"}, strength=0.9),
            sig(source="market_momentum", strength=0.5, meta={"snapshot": GOOD_SNAPSHOT}),
        ]
        r1 = run_once(c, store, signals=signals, now=NOW)
        self.assertEqual(len(r1["picks"]), 1)
        # 1h later: dedupe gate must veto the same token
        r2 = run_once(c, store, signals=signals, now=NOW + 3600)
        self.assertEqual(len(r2["picks"]), 0)
        self.assertEqual(r2["vetoes"], 1)
        # 25h later: eligible again
        r3 = run_once(c, store, signals=[
            sig(meta={"pool_source": "smart_money"}, strength=0.9, source_ts=NOW + 25 * 3600),
            sig(source="market_momentum", strength=0.5, source_ts=NOW + 25 * 3600,
                meta={"snapshot": GOOD_SNAPSHOT}),
        ], now=NOW + 25 * 3600)
        self.assertEqual(len(r3["picks"]), 1)


class TestNormalizers(unittest.TestCase):
    def test_vantage_rows(self):
        got = sources.normalize_vantage_signals([
            {"token_addr": "0xT", "symbol": "T", "direction": "buy",
             "strength": 0.7, "source": "kol", "ts": NOW, "id": 5},
            {"symbol": "no-addr-skipped"},
        ])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].meta["pool_source"], "kol")

    def test_wallet_trades_sell_direction(self):
        got = sources.normalize_wallet_trades([
            {"token": "0xT", "wallet": "0xW", "action": "wallet_sell",
             "amount_usd": 100, "tags": ["kol"], "ts": NOW},
        ])
        self.assertEqual(got[0].direction, -1)

    def test_council_skips_gate_failed(self):
        got = sources.normalize_council_verdicts([
            {"token_addr": "0xT", "direction": "BUY", "conviction": 0.8, "gates_passed": False},
            {"token_addr": "0xT", "direction": "BUY", "conviction": 0.8, "paper": 0},
        ])
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].meta["live"])

    def test_finding_correlation_fans_out_per_token(self):
        got = sources.normalize_mycelium_findings([
            {"miner": "wallet_correlation", "confidence": 0.9, "created_ts": NOW, "id": "f9",
             "payload": {"wallet_a": "0xA", "wallet_b": "0xB", "shared": ["0xT1", "0xT2"]}},
        ])
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0].source, "mycelium_wallet_correlation")


if __name__ == "__main__":
    unittest.main(verbosity=2)
