"""Unit tests for wallet/account_farm.py's guardrails and registry.

No camoufox/browser involved on purpose -- these test the parts that don't
require a real signup: the SiteProfile allowlist, the reason/rate-limit
gates, and the audit log / credential store. The teamorouter profile's
actual browser flow is exercised live (farm_teamorouter.py), not here.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "wallet"))
import account_farm as af  # noqa: E402


class TestAccountFarmGuardrails(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        af.STATE_DIR = self.tmp
        af.RATE_STATE_FILE = os.path.join(self.tmp, "rate.json")
        af.AUDIT_LOG_FILE = os.path.join(self.tmp, "audit.jsonl")
        af.CREDENTIALS_FILE = os.path.join(self.tmp, "creds.json")
        af.MAX_PER_AGENT_PER_DAY = 2
        af.MAX_GLOBAL_PER_DAY = 3
        af.MIN_REASON_CHARS = 15

    def test_teamorouter_is_registered(self):
        names = [s["name"] for s in af.list_services()]
        self.assertIn("teamorouter", names)

    def test_unknown_service_is_rejected(self):
        result = af.signup("not-a-real-service", agent="a", reason="a real reason here")
        self.assertIn("error", result)
        self.assertIn("unknown service", result["error"])

    def test_short_reason_is_rejected(self):
        result = af.signup("teamorouter", agent="a", reason="hi")
        self.assertIn("error", result)
        self.assertIn("reason", result["error"])

    def test_per_agent_rate_limit(self):
        af._rate_record("teamorouter", "agent-a")
        af._rate_record("teamorouter", "agent-a")
        err = af._rate_check("teamorouter", "agent-a")
        self.assertIsNotNone(err)
        self.assertIn("agent-a", err)

    def test_global_rate_limit_independent_of_per_agent(self):
        af._rate_record("teamorouter", "agent-a")
        af._rate_record("teamorouter", "agent-b")
        af._rate_record("teamorouter", "agent-c")
        # agent-d has no per-agent history but the global cap (3) is hit
        err = af._rate_check("teamorouter", "agent-d")
        self.assertIsNotNone(err)
        self.assertIn("global", err)

    def test_credential_store_roundtrip(self):
        af._store_credential("teamorouter", "x@y.com", "sk-teamo-FAKE", "api_key", "agent-a", "needs its own key")
        rows = af.get_credentials("teamorouter")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["credential"], "sk-teamo-FAKE")
        self.assertEqual(rows[0]["agent"], "agent-a")
        # dedupes on the credential itself
        af._store_credential("teamorouter", "x@y.com", "sk-teamo-FAKE", "api_key", "agent-a", "needs its own key")
        self.assertEqual(len(af.get_credentials("teamorouter")), 1)

    def test_audit_log_roundtrip_and_filtering(self):
        af._audit("teamorouter", "agent-a", "reason one", "success", email="a@x.com")
        af._audit("teamorouter", "agent-b", "reason two", "failed", email="b@x.com")
        all_entries = af.recent_audit("teamorouter")
        self.assertEqual(len(all_entries), 2)
        only_a = af.recent_audit("teamorouter", agent="agent-a")
        self.assertEqual(len(only_a), 1)
        self.assertEqual(only_a[0]["agent"], "agent-a")
        self.assertEqual(only_a[0]["reason"], "reason one")


if __name__ == "__main__":
    unittest.main(verbosity=2)
