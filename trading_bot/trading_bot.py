#!/usr/bin/env python3
"""trading_bot -- signal_fusion picks -> capped, paper-mode-first Vantage
orders. See decide.py for the actual decision logic; this is wiring + CLI,
the same shape as signal_fusion/signal_fusion.py.

    python3 -m trading_bot.trading_bot --once
    python3 -m trading_bot.trading_bot --daemon

Paper mode is the only mode until an owner-authorized restart sets
MYCELIUM_BOT_LIVE_ENABLED=1 in the process environment (see decide.py).
Nothing in this repository sets that env var.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from signal_fusion.store import PickStore  # noqa: E402
from trading_bot import decide  # noqa: E402
from trading_bot.state import BotState  # noqa: E402
from trading_bot.vantage_client import VantageClient  # noqa: E402

log = logging.getLogger("trading_bot")
CONFIG_PATH = os.environ.get(
    "TRADING_BOT_CONFIG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))

_config: Dict[str, Any] = {}


def load_config() -> Dict[str, Any]:
    global _config
    with open(CONFIG_PATH) as fh:
        _config = json.load(fh)
    log.info("config loaded from %s", CONFIG_PATH)
    return _config


def _on_sighup(signum, frame):  # noqa: ARG001
    log.info("SIGHUP: hot-reloading config")
    try:
        load_config()
    except (OSError, ValueError) as exc:
        log.error("config reload failed, keeping previous: %s", exc)


def _read_key(path: str) -> str:
    try:
        return open(os.path.expanduser(path)).read().strip()
    except OSError:
        return ""


def _read_wallet_id(path: str) -> Optional[int]:
    try:
        return int(open(os.path.expanduser(path)).read().strip())
    except (OSError, ValueError):
        return None


def main():
    parser = argparse.ArgumentParser(description="mycelium trading bot")
    parser.add_argument("--once", action="store_true", help="single cycle, then exit")
    parser.add_argument("--daemon", action="store_true", help="run every cadence_minutes")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = load_config()
    signal.signal(signal.SIGHUP, _on_sighup)

    ep = cfg.get("endpoints", {})
    api_key = _read_key(ep.get("vantage_key_file", ""))
    wallet_id = _read_wallet_id(ep.get("wallet_id_file", ""))
    if not api_key or wallet_id is None:
        log.error("missing vantage_key_file or wallet_id_file -- generate a wallet first "
                  "(VantageClient.generate_wallet) and populate both files before running")
        sys.exit(1)

    mode = "LIVE-CAPABLE" if decide.live_enabled() else "PAPER-ONLY"
    log.info("trading_bot starting (mode=%s)", mode)

    client = VantageClient(ep.get("vantage_base", ""), api_key)
    pick_store = PickStore(os.path.expanduser(ep.get("picks_db", "")))
    bot_state = BotState(os.path.expanduser(ep.get("state_db", "")))

    if args.daemon:
        log.info("daemon mode: every %d min", cfg.get("cadence_minutes", 5))
        while True:
            try:
                result = decide.run_once(_config, pick_store, bot_state, client, wallet_id)
                log.info("cycle: considered=%d acted=%d skipped=%d kill_switch=%s",
                         result["considered"], len(result["acted"]), len(result["skipped"]),
                         result["kill_switch_active"])
            except Exception:  # noqa: BLE001
                log.exception("cycle failed -- retrying next interval")
            time.sleep(_config.get("cadence_minutes", 5) * 60)
    else:
        result = decide.run_once(cfg, pick_store, bot_state, client, wallet_id)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
