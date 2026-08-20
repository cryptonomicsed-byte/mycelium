"""Unit tests for trading_bot (stdlib unittest, no network -- VantageClient
is a recording stub here, same discipline as signal_fusion's tests never
touching real HTTP)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from signal_fusion.store import PickStore  # noqa: E402
from trading_bot import decide  # noqa: E402
from trading_bot.state import BotState  # noqa: E402

NOW = 1_760_000_000.0


class FakeVantageClient:
    """Records every call instead of hitting a network. Orders auto-
    increment ids like Vantage's real SQLite AUTOINCREMENT would."""

    def __init__(self, fail_create_for=(), fail_paper_fill_for=()):
        self.orders = []
        self.paper_fills = []
        self.journals = []
        self._next_id = 1
        self._fail_create_for = set(fail_create_for)
        self._fail_paper_fill_for = set(fail_paper_fill_for)

    def create_order(self, wallet_id, symbol, side, chain, quantity,
                     trigger_reason="signal_fusion", signal_id=None, notes=""):
        if symbol in self._fail_create_for:
            raise RuntimeError(f"simulated failure for {symbol}")
        oid = self._next_id
        self._next_id += 1
        self.orders.append({"id": oid, "wallet_id": wallet_id, "symbol": symbol,
                            "side": side, "chain": chain, "quantity": quantity,
                            "trigger_reason": trigger_reason, "signal_id": signal_id})
        return {"id": oid, "symbol": symbol}

    def paper_fill_order(self, order_id):
        if order_id in self._fail_paper_fill_for:
            raise RuntimeError("simulated paper-fill failure")
        self.paper_fills.append(order_id)
        return {"status": "filled"}

    def add_journal(self, order_id, entry_reasoning, conviction_score, tags=None):
        self.journals.append({"order_id": order_id, "entry_reasoning": entry_reasoning,
                              "conviction_score": conviction_score, "tags": tags})
        return {"ok": True}


def _seed_pick(store: PickStore, symbol: str, score: float, token_addr: str = None,
               ts: float = NOW) -> int:
    token_addr = token_addr or f"0x{symbol}"
    return store.record_pick(
        token_addr, symbol, score, 1,
        {"S_signal": {"value": score / 100.0, "weight": 1.0, "present": True,
                      "drivers": [{"pool_source": "smart_money", "trust": 0.9, "contrib": 0.5}]}},
        {"passed": True, "vetoes": []}, entry_price=1.0, ts=ts)


def _cfg(**overrides):
    base = {
        "bot_kill_switch": False, "bot_score_threshold": 75, "top_n_considered": 10,
        "bot_max_sol_per_order": 0.01, "bot_daily_sol_cap": 0.1,
    }
    base.update(overrides)
    return base


class TestSizing(unittest.TestCase):
    def test_within_cap(self):
        self.assertEqual(decide.size_order(_cfg(), sol_committed_today=0.0), 0.01)

    def test_exhausted_cap_skips(self):
        self.assertIsNone(decide.size_order(_cfg(), sol_committed_today=0.1))

    def test_partial_headroom_still_blocked_if_full_order_wont_fit(self):
        # 0.095 committed + 0.01 per-order = 0.105 > 0.1 cap -> skip, never
        # a partial/reduced order (fixed sizing only, see decide.py docstring)
        self.assertIsNone(decide.size_order(_cfg(), sol_committed_today=0.095))


class TestLiveEnabled(unittest.TestCase):
    def test_default_off(self):
        os.environ.pop("MYCELIUM_BOT_LIVE_ENABLED", None)
        self.assertFalse(decide.live_enabled())

    def test_true_values(self):
        for v in ("1", "true", "True", "yes", "on"):
            os.environ["MYCELIUM_BOT_LIVE_ENABLED"] = v
            self.assertTrue(decide.live_enabled(), v)
        os.environ.pop("MYCELIUM_BOT_LIVE_ENABLED", None)

    def test_false_values(self):
        for v in ("0", "false", "", "nah"):
            os.environ["MYCELIUM_BOT_LIVE_ENABLED"] = v
            self.assertFalse(decide.live_enabled(), v)
        os.environ.pop("MYCELIUM_BOT_LIVE_ENABLED", None)


class TestBotState(unittest.TestCase):
    def test_dedupe_and_commitment_window(self):
        state = BotState(":memory:")
        self.assertFalse(state.already_acted(1))
        state.record_action(1, "0xTOK", 0.01, order_id=5, mode="paper", ts=NOW)
        self.assertTrue(state.already_acted(1))
        self.assertAlmostEqual(state.sol_committed_today(now=NOW + 3600), 0.01)
        # 25h later: falls out of the 24h window
        self.assertAlmostEqual(state.sol_committed_today(now=NOW + 25 * 3600), 0.0)
        # but dedupe is permanent (pick_id is a primary key, not time-windowed)
        self.assertTrue(state.already_acted(1))


class TestRunOnce(unittest.TestCase):
    def test_kill_switch_blocks_everything(self):
        store = PickStore(":memory:")
        _seed_pick(store, "BONK", 90)
        state = BotState(":memory:")
        client = FakeVantageClient()

        os.environ.pop("MYCELIUM_BOT_LIVE_ENABLED", None)
        result = decide.run_once(_cfg(bot_kill_switch=True), store, state, client,
                                 wallet_id=1, now=NOW)

        self.assertTrue(result["kill_switch_active"])
        self.assertEqual(result["acted"], [])
        self.assertEqual(client.orders, [])

    def test_paper_mode_full_cycle(self):
        os.environ.pop("MYCELIUM_BOT_LIVE_ENABLED", None)
        store = PickStore(":memory:")
        high_id = _seed_pick(store, "BONK", 90)
        low_id = _seed_pick(store, "WIF", 40, token_addr="0xWIF")  # below threshold
        state = BotState(":memory:")
        client = FakeVantageClient()

        result = decide.run_once(_cfg(), store, state, client, wallet_id=7, now=NOW)

        self.assertEqual(len(result["acted"]), 1)
        acted = result["acted"][0]
        self.assertEqual(acted["pick_id"], high_id)
        self.assertEqual(acted["mode"], "paper")
        self.assertEqual(client.orders[0]["wallet_id"], 7)
        self.assertEqual(client.orders[0]["side"], "BUY")
        self.assertEqual(client.paper_fills, [client.orders[0]["id"]])  # paper-filled, once
        self.assertEqual(len(client.journals), 1)
        self.assertIn("signal_fusion: BONK", client.journals[0]["entry_reasoning"])
        self.assertTrue(state.already_acted(high_id))
        self.assertFalse(state.already_acted(low_id))  # never acted on, below threshold

    def test_live_mode_creates_order_but_never_paper_fills(self):
        os.environ["MYCELIUM_BOT_LIVE_ENABLED"] = "1"
        try:
            store = PickStore(":memory:")
            pick_id = _seed_pick(store, "BONK", 90)
            state = BotState(":memory:")
            client = FakeVantageClient()

            result = decide.run_once(_cfg(), store, state, client, wallet_id=1, now=NOW)

            self.assertEqual(result["acted"][0]["mode"], "live")
            self.assertEqual(len(client.orders), 1)   # order WAS created
            self.assertEqual(client.paper_fills, [])  # but never paper-filled or otherwise advanced
        finally:
            os.environ.pop("MYCELIUM_BOT_LIVE_ENABLED", None)

    def test_dedupe_across_cycles(self):
        os.environ.pop("MYCELIUM_BOT_LIVE_ENABLED", None)
        store = PickStore(":memory:")
        _seed_pick(store, "BONK", 90)
        state = BotState(":memory:")
        client = FakeVantageClient()

        r1 = decide.run_once(_cfg(), store, state, client, wallet_id=1, now=NOW)
        r2 = decide.run_once(_cfg(), store, state, client, wallet_id=1, now=NOW + 60)

        self.assertEqual(len(r1["acted"]), 1)
        self.assertEqual(len(r2["acted"]), 0)  # same pick, already acted on
        self.assertEqual(len(client.orders), 1)

    def test_daily_cap_skips_second_pick(self):
        os.environ.pop("MYCELIUM_BOT_LIVE_ENABLED", None)
        store = PickStore(":memory:")
        p1 = _seed_pick(store, "BONK", 90, token_addr="0xBONK")
        # top_picks() dedupes rows within the same run window, so give the
        # second pick its own token/run so it's a distinct row
        store.conn.execute("UPDATE picks SET ts = ts + 1 WHERE token_addr = '0xBONK'")
        p2_ts = NOW + 1
        p2_id = store.record_pick("0xWIF2", "WIF2", 85, 2,
                                  {"S_signal": {"value": 0.8, "weight": 1.0, "present": True,
                                                "drivers": []}},
                                  {"passed": True, "vetoes": []}, entry_price=1.0, ts=p2_ts)
        state = BotState(":memory:")
        client = FakeVantageClient()

        # cap = one order's worth exactly -> the second pick this cycle must be skipped
        cfg = _cfg(bot_max_sol_per_order=0.05, bot_daily_sol_cap=0.05)
        result = decide.run_once(cfg, store, state, client, wallet_id=1, now=p2_ts)

        self.assertEqual(len(result["acted"]), 1)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["reason"], "daily_cap")
        self.assertEqual(result["skipped"][0]["pick_id"], p2_id)

    def test_order_create_failure_is_recorded_not_raised(self):
        os.environ.pop("MYCELIUM_BOT_LIVE_ENABLED", None)
        store = PickStore(":memory:")
        _seed_pick(store, "BONK", 90)
        state = BotState(":memory:")
        client = FakeVantageClient(fail_create_for={"BONK"})

        result = decide.run_once(_cfg(), store, state, client, wallet_id=1, now=NOW)

        self.assertEqual(result["acted"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("order_create_failed", result["skipped"][0]["reason"])
        self.assertFalse(state.already_acted(1))  # never recorded -- eligible to retry next cycle


if __name__ == "__main__":
    unittest.main(verbosity=2)
