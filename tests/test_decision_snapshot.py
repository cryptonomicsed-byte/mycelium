"""The decision snapshot: what was believed, why, and under which rules.

`components` already stored the score breakdown. What nothing recorded was the
*epistemic* state around it — which signals were used, how independent they
were, which versions interpreted them. Without that, a pick is a number:

    "What did we believe?"          <- answerable before this
    "Why did we believe it?"        <- needs the snapshot
    "Was that belief justified by
     the evidence available then?"  <- needs the snapshot AND the outcome

The third question only works if the evidence is frozen at decision time.
Re-scoring today's signals answers whether we would believe it *now*, which is
a different question, and must not overwrite the first.
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_fusion import gates, scoring  # noqa: E402
from signal_fusion.sources import Signal  # noqa: E402
from signal_fusion.store import PickStore  # noqa: E402

CFG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "signal_fusion", "config.json")
with open(CFG_PATH) as fh:
    CFG = json.load(fh)

NOW = 1_800_000_000.0


def wallet_buy(w: str, usd: float, funder: str = "", token: str = "0xtok") -> Signal:
    return Signal(token_addr=token, symbol="TST", direction=1, strength=0.8,
                  source="wallet_activity", source_ts=NOW - 60,
                  meta={"wallet": w, "amount_usd": usd, "tags": ["smart"], "funder": funder})


def scored(signals, **kw):
    snap = {"liquidity_usd": 50_000, "volume_24h_usd": 100_000, "volume_trend": 0.5,
            "holder_growth": 0.4, "top10_share": 0.3, "bundler_rat_share": 0.1,
            "created_ts": NOW - 7200, "price_usd": 1.0}
    return scoring.composite_score(signals, snap, CFG, now=NOW, **kw)


class TestDecisionSnapshot(unittest.TestCase):
    def _snap(self, signals, **kw):
        return scoring.decision_snapshot("0xtok", "TST", scored(signals, **kw),
                                         signals, None, NOW,
                                         policy_version=gates.policy_version(CFG))

    def test_it_records_how_many_actors_the_evidence_really_was(self):
        honest = [wallet_buy(f"0xh{i}", 100.0 * (i + 1)) for i in range(7)]
        spray = [wallet_buy(f"0xs{i}", 100.0, funder="0xone") for i in range(7)]
        s_honest = self._snap(honest)
        s_spray = self._snap(spray)
        self.assertEqual(s_honest["raw_wallets"], 7)
        self.assertEqual(s_honest["effective_actors"], 7.0)
        self.assertEqual(s_spray["raw_wallets"], 7)
        self.assertLess(s_spray["effective_actors"], 1.0)
        self.assertAlmostEqual(s_spray["independence_score"],
                               s_spray["effective_actors"] / 7, places=2)

    def test_evidence_is_named_not_counted(self):
        """A count is what multiplication looks like; a list is not."""
        signals = [wallet_buy(f"0xh{i}", 100.0 * (i + 1)) for i in range(3)]
        s = self._snap(signals)
        self.assertEqual(s["evidence_count"], 3)
        self.assertEqual(len(s["evidence_refs"]), 3)
        self.assertEqual({e["source"] for e in s["evidence_refs"]}, {"wallet_activity"})
        self.assertTrue(all(e["ref"].startswith("0xh") for e in s["evidence_refs"]))

    def test_provenance_collapses_shared_causes(self):
        """Three wallets, one funder: three evidence refs, but whose provenance?"""
        signals = [wallet_buy(f"0xs{i}", 500.0 + i, funder="0xone") for i in range(3)]
        s = self._snap(signals)
        kinds = {p["kind"] for p in s["provenance"]}
        self.assertIn("funder", kinds)
        funders = [p["ref"] for p in s["provenance"] if p["kind"] == "funder"]
        self.assertEqual(funders, ["0xone"])
        wallets = [p["ref"] for p in s["provenance"] if p["kind"] == "wallet"]
        self.assertEqual(len(wallets), 3)

    def test_it_carries_the_versions_that_interpreted_it(self):
        s = self._snap([wallet_buy("0xh1", 100.0)])
        self.assertEqual(s["scoring_version"], scoring.SCORING_VERSION)
        self.assertEqual(s["policy_version"], gates.policy_version(CFG))
        # rule_version is genuinely unknown here, and says so rather than
        # borrowing the scoring version's number.
        self.assertIsNone(s["rule_version"])

    def test_authenticity_is_declared_not_invented(self):
        """Nothing computes it yet; a fabricated number is worse than None."""
        s = self._snap([wallet_buy("0xh1", 100.0)])
        self.assertIsNone(s["authenticity_score"])
        self.assertEqual(s["authenticity_gates"], [])

    def test_policy_version_moves_when_a_threshold_moves(self):
        before = gates.policy_version(CFG)
        changed = json.loads(json.dumps(CFG))
        changed["gates"]["min_liquidity_usd"] += 1
        self.assertNotEqual(before, gates.policy_version(changed))

    def test_policy_version_is_stable_across_key_order(self):
        shuffled = json.loads(json.dumps(CFG))
        shuffled["gates"] = dict(reversed(list(shuffled["gates"].items())))
        self.assertEqual(gates.policy_version(CFG), gates.policy_version(shuffled))


class TestSnapshotStorage(unittest.TestCase):
    def _store(self):
        return PickStore(":memory:")

    def _snapshot(self, signals):
        return scoring.decision_snapshot(
            "0xtok", "TST", scored(signals), signals, None, NOW,
            policy_version=gates.policy_version(CFG))

    def test_pick_and_snapshot_land_together(self):
        """Both or neither -- the pair must not be able to come apart."""
        st = self._store()
        try:
            signals = [wallet_buy(f"0xh{i}", 100.0 * (i + 1)) for i in range(7)]
            pick_id = st.record_pick("0xtok", "TST", 80.0, 1, {"S_wallet": {}},
                                     {"passed": True}, 1.0, ts=NOW,
                                     snapshot=self._snapshot(signals))
            n_picks = st.conn.execute("SELECT COUNT(*) FROM picks").fetchone()[0]
            n_snaps = st.conn.execute("SELECT COUNT(*) FROM decision_snapshots").fetchone()[0]
            self.assertEqual((n_picks, n_snaps), (1, 1))
            self.assertIsNotNone(st.snapshot_for(pick_id))
        finally:
            st.close()

    def test_a_pick_without_a_snapshot_is_still_valid(self):
        """Older callers pass no snapshot; that must not raise."""
        st = self._store()
        try:
            pick_id = st.record_pick("0xtok", "TST", 80.0, 1, {}, {"passed": True}, 1.0, ts=NOW)
            self.assertGreater(pick_id, 0)
            self.assertIsNone(st.snapshot_for(pick_id))
        finally:
            st.close()

    def test_the_snapshot_survives_a_round_trip_intact(self):
        st = self._store()
        try:
            signals = [wallet_buy(f"0xs{i}", 100.0, funder="0xone") for i in range(7)]
            snap = self._snapshot(signals)
            pick_id = st.record_pick("0xtok", "TST", 55.0, 1, {}, {"passed": True},
                                     1.0, ts=NOW, snapshot=snap)
            back = st.snapshot_for(pick_id)
            self.assertIsNotNone(back)
            assert back is not None
            self.assertEqual(back["pick_id"], pick_id)
            self.assertEqual(back["scoring_version"], snap["scoring_version"])
            self.assertEqual(back["policy_version"], snap["policy_version"])
            self.assertEqual(back["effective_actors"], snap["effective_actors"])
            self.assertEqual(len(back["evidence_refs"]), len(snap["evidence_refs"]))
            self.assertEqual(back["provenance"], snap["provenance"])
        finally:
            st.close()

    def test_reviewing_a_pick_also_returns_what_happened(self):
        """Why we believed it and what became of it, together."""
        st = self._store()
        try:
            signals = [wallet_buy("0xh1", 100.0)]
            pick_id = st.record_pick("0xtok", "TST", 70.0, 1, {}, {"passed": True},
                                     1.0, ts=NOW, snapshot=self._snapshot(signals))
            first = st.snapshot_for(pick_id)
            self.assertIsNotNone(first)
            assert first is not None
            self.assertEqual(first["outcomes"], [])
            st.conn.execute(
                "INSERT INTO outcomes (pick_id, mark, price, return_pct, ts)"
                " VALUES (?,?,?,?,?)", (pick_id, "24h", 1.5, 50.0, NOW + 86_400))
            back = st.snapshot_for(pick_id)
            self.assertIsNotNone(back)
            assert back is not None
            self.assertEqual(len(back["outcomes"]), 1)
            self.assertEqual(back["outcomes"][0]["return_pct"], 50.0)
        finally:
            st.close()

    def test_queryable_projection_matches_the_json(self):
        """The columns are projections of the record, so they must agree."""
        st = self._store()
        try:
            signals = [wallet_buy(f"0xh{i}", 100.0 * (i + 1)) for i in range(4)]
            snap = self._snapshot(signals)
            pick_id = st.record_pick("0xtok", "TST", 70.0, 1, {}, {"passed": True},
                                     1.0, ts=NOW, snapshot=snap)
            row = st.conn.execute(
                "SELECT effective_actors, raw_wallets, scoring_version, policy_version"
                " FROM decision_snapshots WHERE pick_id = ?", (pick_id,)).fetchone()
            self.assertEqual(row["effective_actors"], snap["effective_actors"])
            self.assertEqual(row["raw_wallets"], snap["raw_wallets"])
            self.assertEqual(row["scoring_version"], snap["scoring_version"])
            self.assertEqual(row["policy_version"], snap["policy_version"])
        finally:
            st.close()


if __name__ == "__main__":
    unittest.main()
