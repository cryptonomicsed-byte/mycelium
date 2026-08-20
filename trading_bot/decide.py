"""Decision loop: signal_fusion picks -> sized, capped, paper-mode-first
Vantage orders. This is the only genuinely new logic in trading_bot/ --
the signal source (signal_fusion), risk gates (signal_fusion's gates.py,
already applied before a pick ever reaches PickStore.top_picks()), wallet
custody, order lifecycle, and paper ledger (all Vantage) are reused as-is
through vantage_client.py.

Two independent safety layers sit on top of everything signal_fusion and
Vantage already enforce:

  - live_enabled(): MYCELIUM_BOT_LIVE_ENABLED, a process env var, checked
    at startup only -- deliberately NOT a config.json field (which
    hot-reloads on SIGHUP). Real-money execution must require a real
    restart with a deliberately-set env var, never a casual config edit.
    Nothing in this repository sets this to true. Even when true, this
    bot NEVER signs or submits anything -- it only leaves the order
    'pending', which only advances further if Vantage's OWN
    TRADING_ENGINE_ENABLED/TRADING_LIVE_ENABLED are ALSO independently on.
  - kill_switch_active(): cfg['bot_kill_switch'], a config.json field,
    hot-reloaded on SIGHUP like every other tunable here -- checked first,
    every cycle, so an operator can pause the bot without a restart.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

from signal_fusion.memo import build_trade_memo
from signal_fusion.store import PickStore
from trading_bot.state import BotState
from trading_bot.vantage_client import VantageClient

log = logging.getLogger("trading_bot.decide")


def kill_switch_active(cfg: Dict[str, Any]) -> bool:
    return bool(cfg.get("bot_kill_switch", False))


def live_enabled() -> bool:
    return os.environ.get("MYCELIUM_BOT_LIVE_ENABLED", "").lower() in ("1", "true", "yes", "on")


def size_order(cfg: Dict[str, Any], sol_committed_today: float) -> Optional[float]:
    """Fixed per-order size (bot_max_sol_per_order), unless today's cap is
    already exhausted -- then None (skip this pick this cycle). No
    conviction-scaled sizing yet: fixed size is the easiest to reason
    about and verify, and picks reaching here are already score-
    thresholded, so this stays deliberately simple rather than adding a
    second, harder-to-audit sizing formula on day one."""
    max_per_order = float(cfg.get("bot_max_sol_per_order", 0.01))
    daily_cap = float(cfg.get("bot_daily_sol_cap", 0.1))
    if sol_committed_today + max_per_order > daily_cap:
        return None
    return max_per_order


def run_once(cfg: Dict[str, Any], pick_store: PickStore, bot_state: BotState,
            client: VantageClient, wallet_id: int, now: Optional[float] = None) -> Dict[str, Any]:
    now = now if now is not None else time.time()
    result: Dict[str, Any] = {"considered": 0, "kill_switch_active": False,
                              "acted": [], "skipped": []}

    if kill_switch_active(cfg):
        result["kill_switch_active"] = True
        log.warning("bot_kill_switch active -- skipping this cycle entirely")
        return result

    threshold = float(cfg.get("bot_score_threshold", 75))
    picks = pick_store.top_picks(limit=cfg.get("top_n_considered", 10))
    result["considered"] = len(picks)
    mode = "live" if live_enabled() else "paper"

    for pick in picks:
        pick_id = pick.get("id")
        symbol = pick.get("symbol")

        if pick.get("score", 0) < threshold:
            continue
        if bot_state.already_acted(pick_id):
            continue
        gates = pick.get("gates") or {}
        if not gates.get("passed", True):
            # top_picks() only ever contains gate-passed rows today (see
            # signal_fusion.py::run_once -- vetoed tokens never reach
            # store.record_pick). This check stays explicit rather than
            # assumed, so a future change to what top_picks() returns
            # can't silently let a vetoed token through here.
            continue

        quantity = size_order(cfg, bot_state.sol_committed_today(now=now))
        if quantity is None:
            result["skipped"].append({"pick_id": pick_id, "symbol": symbol, "reason": "daily_cap"})
            continue

        memo = build_trade_memo(pick)
        try:
            order = client.create_order(
                wallet_id=wallet_id, symbol=symbol, side="BUY", chain="solana",
                quantity=quantity, trigger_reason="signal_fusion", signal_id=pick_id,
                notes=f"signal_fusion pick #{pick_id}, score={pick.get('score')}")
            order_id = order.get("id")
        except Exception as exc:  # noqa: BLE001 -- degrade per-pick, never crash the cycle
            log.error("order create failed for pick %s (%s): %s", pick_id, symbol, exc)
            result["skipped"].append({"pick_id": pick_id, "symbol": symbol,
                                      "reason": f"order_create_failed: {exc}"})
            continue

        if mode == "paper":
            try:
                client.paper_fill_order(order_id)
            except Exception as exc:  # noqa: BLE001
                log.error("paper-fill failed for order %s: %s", order_id, exc)
        # mode == "live": the order is left exactly as created ('pending').
        # This bot does nothing further -- only Vantage's own
        # execution_engine.py, under its own independent
        # TRADING_ENGINE_ENABLED/TRADING_LIVE_ENABLED gates, can advance a
        # pending order any further than this.

        try:
            client.add_journal(order_id, memo.entry_reasoning(), memo.conviction_score,
                               tags=["signal_fusion", mode])
        except Exception as exc:  # noqa: BLE001 -- journal failure never blocks bookkeeping below
            log.warning("journal write failed for order %s: %s", order_id, exc)

        bot_state.record_action(pick_id, pick["token_addr"], quantity, order_id, mode, ts=now)
        result["acted"].append({"pick_id": pick_id, "symbol": symbol, "order_id": order_id,
                                "quantity": quantity, "mode": mode})

    return result
