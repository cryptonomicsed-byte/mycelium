"""E2E sanity tests for the MCP server's dismiss/dashboard_url tools
(stdlib unittest, same style as tests/test_core.py)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, "/data/data/com.termux/files/home/mycelium")
from mycelium import core  # noqa: E402
from mycelium import mcp_server  # noqa: E402

DB = os.path.join(tempfile.mkdtemp(), "test.db")


class TestMCPServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        core.init_db(DB)

    def setUp(self):
        import sqlite3
        conn = sqlite3.connect(DB)
        conn.execute("DELETE FROM traces")
        conn.execute("DELETE FROM findings")
        conn.commit()
        conn.close()

    def test_tools_list_includes_new_tools(self):
        names = [t["name"] for t in mcp_server.TOOLS]
        self.assertIn("mycelium.dismiss_finding", names)
        self.assertIn("mycelium.dashboard_url", names)

    def test_dismiss_finding_not_found(self):
        result = mcp_server._call_tool("mycelium.dismiss_finding", {"finding_id": "nope"})
        self.assertEqual(result, {"error": "not found"})

    def test_dismiss_finding_open_then_idempotency_guard(self):
        f = core.add_finding("anomaly", 0.8, "t", "e", "alert")
        result = mcp_server._call_tool("mycelium.dismiss_finding", {"finding_id": f["id"]})
        self.assertEqual(result, {"status": "dismissed", "id": f["id"]})
        self.assertEqual(core.get_finding(f["id"])["state"], "dismissed")

        # dismissing again must not silently no-op as success -- matches the
        # Go gateway's REST dismiss handler, which only ever UPDATEs rows
        # currently in state='open'.
        again = mcp_server._call_tool("mycelium.dismiss_finding", {"finding_id": f["id"]})
        self.assertEqual(again, {"error": "finding already dismissed"})

    def test_dismiss_finding_refuses_applied(self):
        f = core.add_finding("anomaly", 0.8, "t", "e", "alert")
        core.set_finding_state(f["id"], "applied")
        result = mcp_server._call_tool("mycelium.dismiss_finding", {"finding_id": f["id"]})
        self.assertEqual(result, {"error": "finding already applied"})
        self.assertEqual(core.get_finding(f["id"])["state"], "applied")

    def test_dashboard_url_shape(self):
        result = mcp_server._call_tool("mycelium.dashboard_url", {})
        self.assertEqual(result, {"url": "http://localhost:8811/web/"})

    def test_dashboard_url_honors_mycelium_addr_env(self):
        os.environ["MYCELIUM_ADDR"] = "localhost:9999"
        try:
            result = mcp_server._call_tool("mycelium.dashboard_url", {})
            self.assertEqual(result, {"url": "http://localhost:9999/web/"})
        finally:
            del os.environ["MYCELIUM_ADDR"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
