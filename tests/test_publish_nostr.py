"""End-to-end coverage for publish.py's Nostr channel: a finding really
flows from core.add_finding through publish.nostr_publish() to a live
WebSocket relay, which independently verifies the signature. Not a mock of
nostr_wire — a real relay implementing the NIP-01 EVENT/OK subset.

Skipped, not failed, when minipae is absent (same contract as
test_nostr_wire.py) since a box with no Nostr channel configured should
still run the substrate's own suite.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import minipae as m
    import websockets
    HAVE_MINIPAE = True
except ImportError:  # pragma: no cover
    m = None
    HAVE_MINIPAE = False

from mycelium import core, publish, nostr_wire  # noqa: E402


class _TestRelay:
    """A real NIP-01 relay: verifies id + schnorr sig, replies OK, records
    what it accepted so the test can assert on it."""

    def __init__(self):
        self.accepted = []
        self._loop = None
        self._server = None
        self._thread = None

    async def _handler(self, ws):
        async for raw in ws:
            msg = json.loads(raw)
            if msg[0] == "EVENT":
                ev = msg[1]
                ok = ev.get("id") == m.event_id(ev) and m.schnorr_verify(
                    bytes.fromhex(ev["id"]), bytes.fromhex(ev["pubkey"]), bytes.fromhex(ev["sig"])
                )
                if ok:
                    self.accepted.append(ev)
                await ws.send(json.dumps(["OK", ev.get("id", ""), ok, "" if ok else "invalid"]))

    def start(self) -> str:
        ready = threading.Event()

        def run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            async def serve():
                self._server = await websockets.serve(self._handler, "127.0.0.1", 0)
                ready.set()
                await asyncio.Future()

            try:
                self._loop.run_until_complete(serve())
            except RuntimeError:
                pass  # loop.stop() during the never-ending serve() future, expected on teardown

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        ready.wait(timeout=5)
        port = self._server.sockets[0].getsockname()[1]
        return f"ws://127.0.0.1:{port}"

    def stop(self):
        if not self._loop:
            return

        async def shutdown():
            self._server.close()
            await self._server.wait_closed()
            self._loop.stop()

        self._loop.call_soon_threadsafe(lambda: self._loop.create_task(shutdown()))
        self._thread.join(timeout=5)


@unittest.skipUnless(HAVE_MINIPAE, "minipae not on PYTHONPATH")
class TestPublishNostrChannel(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "mycelium.db")
        self.anchor_path = os.path.join(self.tmpdir, "chain_state.jsonl")
        with open(self.anchor_path, "w") as fh:
            fh.write('{"ts":"2026-01-01T00:00:00Z"}\n')

        # publish.py reads its paths as module-level constants at import
        # time, so a plain os.environ change after the module is already
        # imported (e.g. by another test file) would be silently ignored —
        # patch the module's constants directly instead.
        core.init_db(self.db_path)
        publish.CHECKPOINT_DIR = os.path.join(self.tmpdir, "checkpoints")
        publish.ANCHOR_PATH = self.anchor_path
        publish.PUBKEY_PATH = os.path.join(self.tmpdir, "no-such-key.json")
        publish.NOSTR_STATE_PATH = os.path.join(self.tmpdir, "nostr_published.json")

        import secrets
        seckey = secrets.token_bytes(32)
        os.environ["NOSTR_SECKEY"] = seckey.hex()

        self.relay = _TestRelay()
        os.environ["NOSTR_RELAY"] = self.relay.start()

    def tearDown(self):
        self.relay.stop()
        for key in ("NOSTR_SECKEY", "NOSTR_RELAY"):
            os.environ.pop(key, None)

    def test_finding_flows_through_publish_to_a_real_relay(self):
        core.add_finding(
            miner="test_miner",
            confidence=0.9,
            title="publish.py nostr wiring, verified end-to-end",
            evidence="e2e test",
            suggestion="n/a",
            payload={"k": "v"},
        )

        result = publish.publish()

        self.assertEqual(result["nostr"]["status"], "ok")
        self.assertEqual(result["nostr"]["published"], 1)
        time.sleep(0.2)  # let the relay's async handler finish appending
        self.assertEqual(len(self.relay.accepted), 1)
        self.assertEqual(self.relay.accepted[0]["kind"], nostr_wire.KIND_AGENT_ENGRAM)

    def test_second_run_does_not_republish_the_same_finding(self):
        core.add_finding(
            miner="test_miner", confidence=0.5, title="only once",
            evidence="e2e test", suggestion="n/a", payload={},
        )
        first = publish.nostr_publish()
        second = publish.nostr_publish()

        self.assertEqual(first["published"], 1)
        self.assertEqual(second["published"], 0)

    def test_skips_cleanly_when_no_nostr_creds_are_configured(self):
        os.environ.pop("NOSTR_SECKEY")
        os.environ.pop("NOSTR_RELAY")
        result = publish.nostr_publish()
        self.assertEqual(result["status"], "skipped")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
