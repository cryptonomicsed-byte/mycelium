"""Independence at the agent layer.

The wallet version of this question lives in tests/test_signal_independence.py:
seven addresses one funder paid for are one actor. The same question has a
second home, and it is the more dangerous one, because there is no funding graph
to read it off:

    seven agents report X
        !=
    seven independent observations of X

An agent's "evidence" is whatever it puts in its payload, so the only honest
signal available is whether two agents reported the *same thing*. `cross_agent`
counted agents, which is multiplicity, and multiplied a single fact by the
number of witnesses -- while its own evidence text concluded the observations
were not independent.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mycelium.miners import _evidence_fingerprint, cross_agent  # noqa: E402


def fail(agent: str, payload, action: str = "push", target: str = "repo") -> dict:
    return {"agent": agent, "kind": "tool_call", "outcome": "failure",
            "action": action, "target": target, "payload": payload}


SHARED = {"error": "auth expired", "repo": "shared"}


class TestEvidenceFingerprint(unittest.TestCase):
    def test_identical_payloads_share_a_fingerprint(self):
        self.assertEqual(_evidence_fingerprint(dict(SHARED)),
                         _evidence_fingerprint(dict(SHARED)))

    def test_key_order_does_not_matter(self):
        self.assertEqual(_evidence_fingerprint({"a": 1, "b": 2}),
                         _evidence_fingerprint({"b": 2, "a": 1}))

    def test_different_payloads_differ(self):
        self.assertNotEqual(_evidence_fingerprint({"error": "auth expired"}),
                            _evidence_fingerprint({"error": "disk full"}))

    def test_absence_of_evidence_is_not_shared_evidence(self):
        """A blank payload establishes nothing, so it must not collapse agents."""
        for empty in ({}, None, "", [], 0):
            self.assertEqual(_evidence_fingerprint(empty), "", repr(empty))

    def test_any_payload_with_content_is_a_claim(self):
        """No arbitrary richness threshold: content is content.

        An envelope mistaken for a claim is a real risk, but it is an emitter
        problem -- a length cutoff would only hide it while looking principled.
        """
        self.assertNotEqual(_evidence_fingerprint({"x": 1}), "")
        self.assertNotEqual(_evidence_fingerprint({"status": "ok"}), "")


class TestCrossAgentIndependence(unittest.TestCase):
    def test_one_payload_seen_by_three_agents_is_one_observation(self):
        """The §2 case, measured: multiplicity 3, independence 1/3."""
        found = cross_agent([fail(a, dict(SHARED))
                             for a in ("codex-worker", "herdr-panel", "hermes-default")])
        self.assertEqual(len(found), 1)
        p = found[0]["payload"]
        self.assertEqual(len(p["agents"]), 3)
        self.assertEqual(p["distinct_evidence"], 1)
        self.assertAlmostEqual(p["independence"], 1 / 3, places=3)
        self.assertAlmostEqual(p["effective_agents"], 1.0, places=2)

    def test_three_disagreeing_agents_stay_three(self):
        found = cross_agent([fail("a1", {"error": "auth expired", "attempt": 1}),
                             fail("a2", {"error": "disk full", "attempt": 2}),
                             fail("a3", {"error": "timeout", "attempt": 3})])
        p = found[0]["payload"]
        self.assertEqual(p["distinct_evidence"], 3)
        self.assertAlmostEqual(p["independence"], 1.0, places=3)
        self.assertAlmostEqual(p["effective_agents"], 3.0, places=2)

    def test_shared_evidence_is_named_not_just_counted(self):
        found = cross_agent([fail("a1", dict(SHARED)),
                             fail("a2", dict(SHARED)),
                             fail("a3", {"error": "disk full"})])
        p = found[0]["payload"]
        self.assertEqual(sorted(p["shared_evidence_agents"]), ["a1", "a2"])
        self.assertIn("carriers of identical evidence", found[0]["evidence"])

    def test_confidence_follows_effective_agents_not_raw_count(self):
        """Two extra witnesses to one fact must not raise confidence."""
        one = cross_agent([fail("a1", dict(SHARED)), fail("a2", dict(SHARED))])[0]
        three = cross_agent([fail(a, dict(SHARED)) for a in ("a1", "a2", "a3")])[0]
        self.assertAlmostEqual(one["confidence"], three["confidence"], places=4)
        # ...whereas three genuinely separate failures should raise it.
        separate = cross_agent([fail(a, {"error": e, "n": i}) for i, (a, e) in
                                enumerate([("a1", "x"), ("a2", "y"), ("a3", "z")])])[0]
        self.assertGreater(separate["confidence"], one["confidence"])

    def test_no_payloads_means_no_opinion_not_one_source(self):
        """Empty payloads leave the agents independent -- absence, not sharing."""
        found = cross_agent([fail("a1", {}), fail("a2", {})])
        self.assertEqual(len(found), 1)
        p = found[0]["payload"]
        self.assertEqual(p["distinct_evidence"], 0)
        self.assertAlmostEqual(p["independence"], 1.0, places=3)
        self.assertEqual(p["shared_evidence_agents"], [])

    def test_a_single_agent_is_not_a_cross_agent_finding(self):
        self.assertEqual(cross_agent([fail("a1", dict(SHARED))]), [])

    def test_the_finding_carries_direction_for_the_feed(self):
        """findings.direction is a column now; a miner omitting it writes 0."""
        found = cross_agent([fail("a1", dict(SHARED)), fail("a2", dict(SHARED))])
        self.assertIn("direction", found[0])


if __name__ == "__main__":
    unittest.main()
