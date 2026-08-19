"""ares-signal-gate — new signal sources for the Vantage pool + Mycelium.

Two feeds, one daemon (15-min cadence, stdlib only):
  1. dex_new     — DexScreener token-profiles/latest (public, no key):
                   NEW token listings -> pool signals (source='dex_new') with
                   liquidity/volume/age context from the tokens API.
  2. whale_scan  — whalecli (tools-venv) scans tracked whale wallets on
                   HL / ETH / BTC -> pool signals (source='whalecli_<chain>').
                   Wallets are seeded from the HL leaderboard when reachable,
                   otherwise added manually (whalecli wallet add).

Signals land in the Vantage intel signal pool via /api/intel/signals/ingest
(system intel tool key, same local-only key file the signal-fusion engine
uses), so the council consumes them with zero changes. Every event also
emits a Mycelium trace through the gateway tunnel.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("SIGNAL_GATE_CONFIG", os.path.join(HERE, "config.json"))
TOOL_KEYS_PATH = os.path.join(os.path.dirname(HERE), "ares-signal-fusion", ".vantage_tool_keys.json")
STATE_PATH = os.path.join(HERE, "state.json")

log = logging.getLogger("signal_gate")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def _state(cfg):
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"seen_profiles": [], "last_dex_ts": 0.0}


def _save_state(st):
    with open(STATE_PATH, "w") as f:
        json.dump(st, f)


def _tool_key(cfg):
    try:
        with open(TOOL_KEYS_PATH) as f:
            return json.load(f).get("intel", "")
    except OSError:
        return ""


def _http_json(url, key="", timeout=12, method="GET", body=None):
    headers = {"User-Agent": "ares-signal-gate/1.0"}
    if key:
        headers["X-Agent-Key"] = key
    data = json.dumps(body).encode() if body is not None else None
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def post_signal(cfg, symbol, source, direction, conviction, detail, mint, ts=None):
    """POST one signal to the Vantage intel pool (system intel tool)."""
    ep = cfg["endpoints"]
    tool_key = _tool_key(cfg)
    if not tool_key:
        log.warning("no intel tool key — skipping signal post")
        return False
    payload = {
        "symbol": str(symbol)[:20], "source": source, "type": "trending",
        "conviction": max(0.0, min(1.0, float(conviction))),
        "direction": direction, "detail": detail[:200],
        "mint": str(mint)[:64], "ts": int(ts or time.time()),
    }
    try:
        req = urllib.request.Request(
            f"{ep['vantage_base']}/api/intel/signals/ingest",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "X-Vantage-Tool": "intel",
                     "X-Vantage-Tool-Key": tool_key})
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("signal post failed (%s): %s", source, exc)
        return False


def trace_mycelium(cfg, agent, action, target, payload):
    try:
        body = json.dumps({"agent": agent, "session": "signal-gate", "kind": "observation",
                           "action": action, "target": target, "outcome": "success",
                           "payload": payload}).encode()
        req = urllib.request.Request(f"{cfg['endpoints']['mycelium_gateway']}/api/trace",
                                     data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ dex_new

def dex_new(cfg, st):
    """New token listings from DexScreener token-profiles/latest."""
    try:
        profiles = _http_json("https://api.dexscreener.com/token-profiles/latest/v1")
    except Exception as exc:  # noqa: BLE001
        log.warning("dex profiles unavailable: %s", exc)
        return 0
    if not isinstance(profiles, list):
        return 0
    seen = set(st.get("seen_profiles", []))
    posted = 0
    cap = int(cfg.get("dex_new_cap", 10))
    for prof in profiles:
        addr = (prof or {}).get("tokenAddress", "")
        url = (prof or {}).get("url", "")
        if not addr or addr in seen or posted >= cap:
            continue
        # enrich with pair data (liquidity/volume/age) — best pair wins
        snap = {}
        try:
            pairs = _http_json(f"https://api.dexscreener.com/tokens/v1/solana/{addr}")
            if isinstance(pairs, list) and pairs:
                best = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
                liq = float((best.get("liquidity") or {}).get("usd") or 0)
                vol = float((best.get("volume") or {}).get("h24") or 0)
                age_h = (time.time() - (best.get("pairCreatedAt") or 0) / 1000) / 3600
                price = float(best.get("priceUsd") or 0)
                snap = {"liq": liq, "vol": vol, "age_h": round(age_h, 2), "price": price,
                        "symbol": str((best.get("baseToken") or {}).get("symbol") or "?")[:16]}
        except Exception:  # noqa: BLE001
            pass
        conv = min(1.0, max(0.05, (snap.get("liq") or 0) / 50000.0))
        symbol = snap.get("symbol") or addr[:6]
        detail = (f"dex_new {symbol}: liq=${snap.get('liq', 0):,.0f} vol=${snap.get('vol', 0):,.0f} "
                  f"age={snap.get('age_h', 0)}h" + (f" {url[:60]}" if url else ""))
        ok = post_signal(cfg, symbol, "dex_new", "buy", conv, detail, addr)
        if ok:
            posted += 1
            trace_mycelium(cfg, "signal_gate", "dex_new_listing", addr,
                           {"symbol": symbol, "liquidity_usd": snap.get("liq"),
                            "volume_24h_usd": snap.get("vol"), "age_hours": snap.get("age_h")})
            seen.add(addr)
        else:
            log.warning("dex_new post failed for %s — keeping unseen for retry", addr[:10])
    st["seen_profiles"] = list(seen)[-400:]
    st["last_dex_ts"] = time.time()
    return posted


# -------------------------------------------------------------- whale_scan

def _parse_whale_line(line, chain):
    """Defensive parse of one whalecli JSONL event -> signal dict or None."""
    try:
        e = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(e, dict):
        return None
    addr = e.get("address") or e.get("wallet") or ""
    flow = float(e.get("flow_usd") or e.get("amount_usd") or e.get("usd_value") or 0)
    if not flow:
        flow = float(e.get("flow") or 0)
    side = str(e.get("side") or e.get("direction") or e.get("action") or "buy").lower()
    symbol = str(e.get("token") or e.get("symbol") or e.get("asset") or chain)[:16]
    ts = e.get("ts") or e.get("timestamp") or time.time()
    return {"addr": addr, "flow": abs(flow), "side": side, "symbol": symbol, "ts": ts,
            "raw": json.dumps(e)[:200]}


def whale_scan(cfg):
    """Run whalecli scans on tracked wallets; post signals per event."""
    wc = cfg["endpoints"].get("whalecli", "/opt/ares/tools-venv/bin/whalecli")
    chains = cfg.get("whale_chains", ["HL", "ETH", "BTC"])
    posted = 0
    for chain in chains:
        try:
            proc = subprocess.run(
                [wc, "scan", "--chain", chain, "--hours", "24", "--format", "jsonl"],
                capture_output=True, text=True, timeout=int(cfg.get("whale_timeout_s", 90)))
        except (subprocess.TimeoutExpired, OSError) as exc:
            log.warning("whalecli %s scan failed: %s", chain, exc)
            continue
        out = proc.stdout.strip()
        if not out:
            log.info("whalecli %s: no output (no tracked wallets? run 'whalecli wallet add')", chain)
            continue
        for line in out.splitlines()[: int(cfg.get("whale_cap", 10))]:
            ev = _parse_whale_line(line, chain)
            if not ev or not ev["flow"]:
                continue
            direction = "buy" if "sell" not in ev["side"] else "sell"
            conv = min(1.0, ev["flow"] / 1000000.0) or 0.1  # $1M+ = 1.0
            detail = f"whalecli {chain} {ev['symbol']} {direction} ${ev['flow']:,.0f}"
            if ev["addr"]:
                detail += f" ({ev['addr'][:8]}…)"
            ok = post_signal(cfg, ev["symbol"], f"whalecli_{chain.lower()}", direction,
                             conv, detail, ev["addr"], ev["ts"])
            if ok:
                posted += 1
                trace_mycelium(cfg, "signal_gate", f"whale_{chain.lower()}",
                               ev["addr"][:20], {"symbol": ev["symbol"], "flow_usd": ev["flow"],
                                                 "side": direction})
    return posted


# ---------------------------------------------------------------- seed_hl

def seed_hl_wallets(cfg):
    """Best-effort: pull HL leaderboard top traders into whalecli tracking.
    Falls back to a manual-add hint — never blocks the daemon."""
    wc = cfg["endpoints"].get("whalecli", "/opt/ares/tools-venv/bin/whalecli")
    try:
        rows = _http_json("https://api.hyperliquid.xyz/info", method="POST",
                          body={"type": "leaderboard", "timeWindow": "7d"}, timeout=12)
    except Exception as exc:  # noqa: BLE001
        log.info("HL leaderboard unavailable (%s) — add whales manually: "
                 "whalecli wallet add <addr> --chain HL", exc)
        return 0
    addrs = []
    if isinstance(rows, list):
        for r in rows[: int(cfg.get("hl_seed_count", 10))]:
            if isinstance(r, dict) and r.get("address"):
                addrs.append(r["address"])
    if not addrs:
        return 0
    added = 0
    for a in addrs:
        proc = subprocess.run([wc, "wallet", "add", a, "--chain", "HL"],
                              capture_output=True, text=True, timeout=30)
        if proc.returncode == 0:
            added += 1
    log.info("seeded %d HL whale wallets from leaderboard", added)
    return added


# ------------------------------------------------------------------- main

def run_once(cfg):
    st = _state(cfg)
    posted = 0
    try:
        posted += dex_new(cfg, st)
    except Exception as exc:  # noqa: BLE001
        log.warning("dex_new failed: %s", exc)
    try:
        posted += whale_scan(cfg)
    except Exception as exc:  # noqa: BLE001
        log.warning("whale_scan failed: %s", exc)
    _save_state(st)
    return posted


def main():
    parser = argparse.ArgumentParser(description="ares-signal-gate")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--seed-hl", action="store_true", help="seed HL leaderboard wallets, then exit")
    args = parser.parse_args()

    cfg = load_config()
    if args.seed_hl:
        return seed_hl_wallets(cfg)
    if args.daemon:
        log.info("daemon mode: every %d min", cfg.get("cadence_minutes", 15))
        while True:
            try:
                n = run_once(cfg)
                log.info("cycle done: %d signals posted", n)
            except Exception:  # noqa: BLE001
                log.exception("cycle failed")
            time.sleep(cfg.get("cadence_minutes", 15) * 60)
    else:
        n = run_once(cfg)
        log.info("cycle done: %d signals posted", n)


if __name__ == "__main__":
    main()
