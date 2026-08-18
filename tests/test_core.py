"""E2E sanity tests for the Mycelium substrate (stdlib unittest)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, "/data/data/com.termux/files/home/mycelium")
from mycelium import core, miners  # noqa: E402
from mycelium.apply import apply_finding  # noqa: E402

DB = os.path.join(tempfile.mkdtemp(), "test.db")


class TestSubstrate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        core.init_db(DB)

    def setUp(self):
        # wipe traces/findings between tests
        import sqlite3
        conn = sqlite3.connect(DB)
        conn.execute("DELETE FROM traces")
        conn.execute("DELETE FROM findings")
        conn.commit()
        conn.close()

    def test_emit_and_query(self):
        core.emit("agent-a", "s1", "tool_call", "patch", "f.md", "success", 100)
        rows = core.query_traces(agent="agent-a")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "patch")

    def test_invalid_kind_rejected(self):
        with self.assertRaises(ValueError):
            core.emit("a", "s", "not_a_kind")

    def test_finding_lifecycle(self):
        f = core.add_finding("anomaly", 0.8, "t", "e", "alert")
        self.assertEqual(core.get_finding(f["id"])["state"], "open")
        self.assertTrue(core.set_finding_state(f["id"], "applied"))
        self.assertEqual(core.get_finding(f["id"])["state"], "applied")

    def test_recurring_workflow_miner(self):
        for i in range(4):
            core.emit("a", f"x{i}", "tool_call", "patch", "f.md", "success")
            core.emit("a", f"x{i}", "tool_call", "grep", "f.md", "success")
        found = miners.run_miner("recurring_workflow")
        self.assertTrue(any(f["payload"]["sequence"] == ["patch", "grep"] for f in found))

    def test_cross_agent_miner(self):
        for agent in ("a1", "a2"):
            core.emit(agent, f"s-{agent}", "tool_call", "pull", "repo", "failure")
        found = miners.run_miner("cross_agent")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["payload"]["target"], "repo")

    def test_apply_generates_skill(self):
        f = core.add_finding("opportunity", 0.9, "t", "e", "skill",
                             {"slug": "patch_grep_verify", "sequence": ["patch", "grep"]})
        info = apply_finding(f["id"])
        self.assertIsNotNone(info)
        self.assertTrue(info["path"].endswith("patch_grep_verify/SKILL.md"))
        self.assertTrue(os.path.exists(info["path"]))
        self.assertEqual(core.get_finding(f["id"])["state"], "applied")


if __name__ == "__main__":
    unittest.main(verbosity=2)
