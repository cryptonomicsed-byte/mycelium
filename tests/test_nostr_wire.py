"""Tests for the Nostr wire channel (stdlib unittest).

Requires minipae on the path, since this module deliberately delegates every
wire primitive to it rather than reimplementing:

    PYTHONPATH=/path/to/minipae python3 -m unittest tests.test_nostr_wire

Skipped, not failed, when minipae is absent — a box with no Nostr channel
configured should still be able to run the substrate's own suite.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mycelium import nostr_wire  # noqa: E402

try:
    import minipae as m
    HAVE_MINIPAE = True
except ImportError:  # pragma: no cover
    m = None
    HAVE_MINIPAE = False


A_FINDING = {
    "id": "3f2a1b4c-0000-4000-8000-000000000001",
    "created_ts": 1700000000,
    "miner": "recurring_workflow",
    "confidence": 0.82,
    "title": "patch → grep ran 14 times across 4 files",
    "evidence": "traces 88..102",
    "suggestion": "generate a combined patch-and-verify skill",
    "state": "open",
    "payload": json.dumps({"tool_pair": ["patch", "grep"], "count": 14}, sort_keys=True),
}


class TestSlugNormalisation(unittest.TestCase):
    """Slug grammar is stricter than free-text miner names and titles."""

    def test_folds_case_and_diacritics(self):
        self.assertEqual(nostr_wire.normalize_slug_segment("Ọ̀rúnmìlà"), "orunmila")
        self.assertEqual(nostr_wire.normalize_slug_segment("Recurring Workflow"),
                         "recurring-workflow")
        self.assertEqual(nostr_wire.normalize_slug_segment("patch → grep"), "patch-grep")

    def test_truncates_on_bytes_not_characters(self):
        # minipae's 64 is a byte limit, and a multi-byte segment is longer in
        # bytes than in characters -- truncating on len() would let an
        # over-long slug through.
        seg = nostr_wire.normalize_slug_segment("a" * 100)
        self.assertLessEqual(len(seg.encode()), 64)

    def test_a_segment_that_normalises_to_nothing_is_refused(self):
        # Silently emitting an empty segment builds an invalid slug that only
        # fails later, in another process.
        with self.assertRaises(ValueError):
            nostr_wire.normalize_slug_segment("!!!")
        with self.assertRaises(ValueError):
            nostr_wire.normalize_slug_segment("")


@unittest.skipUnless(HAVE_MINIPAE, "minipae not on PYTHONPATH")
class TestSlugsAgainstMinipae(unittest.TestCase):
    """The grammar is minipae's, so minipae is the judge of it."""

    def test_finding_slug_is_accepted_by_minipae(self):
        slug = nostr_wire.slug_finding(A_FINDING["id"])
        self.assertTrue(slug.startswith("mem/mycelium/"))
        self.assertTrue(m.validate_slug(slug), slug)

    def test_a_free_text_trace_uri_still_yields_a_valid_slug(self):
        # Resource URIs carry colons and slashes, none of which the grammar
        # allows.
        slug = nostr_wire.slug_trace("repo://Omo-Koda2/src/Main.rs")
        self.assertTrue(m.validate_slug(slug), slug)


class TestFindingRecord(unittest.TestCase):
    def test_payload_is_returned_as_structure_not_an_escaped_blob(self):
        # core.add_finding stores payload as a JSON string. A reader in another
        # language should get an object, not a string containing JSON.
        rec = nostr_wire.finding_record(A_FINDING)
        self.assertIsInstance(rec["payload"], dict)
        self.assertEqual(rec["payload"]["count"], 14)

    def test_unparseable_payload_is_preserved_rather_than_dropped(self):
        rec = nostr_wire.finding_record({**A_FINDING, "payload": "not json"})
        self.assertEqual(rec["payload"], {"raw": "not json"})

    def test_record_is_flat_and_self_describing(self):
        rec = nostr_wire.finding_record(A_FINDING)
        for field in ("finding_id", "miner", "confidence", "title", "evidence",
                      "suggestion", "state", "created_ts", "payload"):
            self.assertIn(field, rec)


@unittest.skipUnless(HAVE_MINIPAE, "minipae not on PYTHONPATH")
class TestEvents(unittest.TestCase):
    SECKEY = bytes([0x22]) * 32

    def _pubkey(self):
        return m.pubkey_from_secret(int.from_bytes(self.SECKEY, "big"))

    def test_engram_is_a_valid_signed_nip_ae_event(self):
        ev = nostr_wire.build_finding_engram(A_FINDING, self.SECKEY, self._pubkey())
        self.assertEqual(ev["kind"], nostr_wire.KIND_AGENT_ENGRAM)
        self.assertEqual(ev["id"], m.event_id(ev))
        self.assertTrue(
            m.schnorr_verify(bytes.fromhex(ev["id"]), self._pubkey(),
                             bytes.fromhex(ev["sig"]))
        )

    def test_engram_content_decrypts_back_to_the_finding(self):
        # The round trip that matters: another runtime holding the same key
        # must recover the record, not just a blob.
        pub = self._pubkey()
        ev = nostr_wire.build_finding_engram(A_FINDING, self.SECKEY, pub)
        kc = m.conversation_key(self.SECKEY, pub)
        recovered = json.loads(m.nip44_decrypt(ev["content"], kc))
        self.assertEqual(recovered["finding_id"], A_FINDING["id"])
        self.assertEqual(recovered["confidence"], 0.82)

    def test_engram_carries_the_d_and_p_tags(self):
        ev = nostr_wire.build_finding_engram(A_FINDING, self.SECKEY, self._pubkey())
        names = [t[0] for t in ev["tags"]]
        self.assertIn("d", names)
        self.assertIn("p", names)

    def test_the_raw_slug_never_reaches_the_wire(self):
        # The d tag is HMAC'd so a relay operator cannot enumerate what this
        # substrate has been mining.
        ev = nostr_wire.build_finding_engram(A_FINDING, self.SECKEY, self._pubkey())
        self.assertNotIn(nostr_wire.slug_finding(A_FINDING["id"]), json.dumps(ev))

    def test_claim_is_a_valid_signed_crucible_event(self):
        ev = nostr_wire.build_finding_claim(A_FINDING, "sha256:abc", self.SECKEY)
        self.assertEqual(ev["kind"], nostr_wire.KIND_CLAIM)
        self.assertEqual(ev["id"], m.event_id(ev))
        self.assertTrue(
            m.schnorr_verify(bytes.fromhex(ev["id"]), self._pubkey(),
                             bytes.fromhex(ev["sig"]))
        )

    def test_claim_carries_the_miners_own_confidence(self):
        # Crucible prices confidence: being wrong at 0.99 costs more than being
        # wrong at 0.55, so the miner's own number has to travel with the claim
        # rather than being flattened to a default.
        ev = nostr_wire.build_finding_claim(A_FINDING, "sha256:abc", self.SECKEY)
        body = json.loads(ev["content"])
        self.assertEqual(body["confidence"], 0.82)
        self.assertEqual(body["falsifier"], "sha256:abc")

    def test_a_claim_without_a_falsifier_is_refused(self):
        # Crucible rejects such a claim at parse time; failing here gives a
        # clear error instead of a silent bounce at the relay.
        with self.assertRaises(ValueError):
            nostr_wire.build_finding_claim(A_FINDING, "", self.SECKEY)

    def test_claim_content_is_not_ascii_escaped(self):
        # The id is hashed over this content. Python's json.dumps default
        # escapes non-ASCII and yields an id no other implementation agrees
        # with, so the title must survive as raw UTF-8.
        finding = {**A_FINDING, "title": "Òrìṣà pattern detected"}
        ev = nostr_wire.build_finding_claim(finding, "sha256:abc", self.SECKEY)
        self.assertIn("Òrìṣà", ev["content"])
        self.assertNotIn("\\u00", ev["content"])
        self.assertEqual(ev["id"], m.event_id(ev))

    def test_build_finding_events_omits_the_claim_without_a_falsifier(self):
        pub = self._pubkey()
        only = nostr_wire.build_finding_events(A_FINDING, self.SECKEY, pub)
        self.assertEqual(set(only), {"engram"})

        both = nostr_wire.build_finding_events(A_FINDING, self.SECKEY, pub,
                                               falsifier="sha256:abc")
        self.assertEqual(set(both), {"engram", "claim"})


if __name__ == "__main__":
    unittest.main()
