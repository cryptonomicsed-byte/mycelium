"""GeckoTerminal OHLCV backfill for fusion picks — REAL price history for the
outcome / self-calibration loop (not spot marks). Daily candles per pick:
pair address resolved via the DexScreener tokens API (max-liquidity pair),
then GeckoTerminal daily OHLCV stored in ares_picks.db (table `ohlcv`).

Run: python3 -m signal_fusion.ohlc --limit 20
"""

import argparse
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request

UA = {"User-Agent": "mycelium-signal-fusion/1.0"}


def db_path(cfg):
    return os.path.expanduser(cfg["endpoints"]["picks_db"])


def ensure_table(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ohlcv (
               pick_id INTEGER, ts INTEGER, o REAL, h REAL, l REAL, c REAL, vol REAL,
               PRIMARY KEY (pick_id, ts))"""
    )


def resolve_pair(token_addr, timeout=12):
    """Max-liquidity DexScreener pair address for a token ('' on failure)."""
    try:
        req = urllib.request.Request(
            f"https://api.dexscreener.com/tokens/v1/solana/{token_addr}", headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            pairs = json.loads(r.read().decode())
        if not isinstance(pairs, list) or not pairs:
            return ""
        return max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0)).get(
            "pairAddress") or ""
    except Exception:  # noqa: BLE001
        return ""


def fetch_daily(pair_addr, limit=60, timeout=15):
    """GeckoTerminal daily OHLCV: [[ts, o, h, l, c, vol], ...] oldest→newest."""
    url = (f"https://api.geckoterminal.com/api/v2/networks/solana/pools/"
           f"{pair_addr}/ohlcv/day?limit={limit}")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    return (d.get("data") or {}).get("attributes", {}).get("ohlcv_list", [])


def backfill(cfg, limit=5, per_pick=60):
    """Recent picks -> daily candles. Paced for GeckoTerminal's free tier
    (~30 req/min): max `limit` picks per run, sleep between, one 429 retry.
    Returns (rows_stored, pick_ids_done)."""
    conn = sqlite3.connect(db_path(cfg))
    ensure_table(conn)
    picks = conn.execute(
        "SELECT id, token_addr, ts FROM picks ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    stored = 0
    done = []
    for pick_id, token_addr, pick_ts in picks:
        if not token_addr:
            continue
        pair = resolve_pair(token_addr)
        if not pair:
            continue
        try:
            candles = fetch_daily(pair, limit=per_pick)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                time.sleep(6)  # rate limit: one retry
                try:
                    candles = fetch_daily(pair, limit=per_pick)
                except Exception:  # noqa: BLE001
                    print(f"  pick {pick_id}: OHLCV rate-limited, skipped")
                    continue
            else:
                print(f"  pick {pick_id}: OHLCV failed: {exc}")
                continue
        except Exception as exc:  # noqa: BLE001
            print(f"  pick {pick_id} ({token_addr[:8]}…): OHLCV failed: {exc}")
            continue
        n = 0
        for c in candles:
            try:
                ts, o, h, l, cl, vol = c[:6]
                conn.execute(
                    "INSERT OR REPLACE INTO ohlcv (pick_id, ts, o, h, l, c, vol) VALUES (?,?,?,?,?,?,?)",
                    (pick_id, int(ts), float(o), float(h), float(l), float(cl), float(vol)))
                n += 1
            except (TypeError, ValueError):
                continue
        conn.commit()
        stored += n
        done.append(pick_id)
        if n:
            # realized: first close after the pick vs latest close
            first = conn.execute(
                "SELECT c FROM ohlcv WHERE pick_id=? AND ts>=? ORDER BY ts LIMIT 1",
                (pick_id, int(pick_ts))).fetchone()
            last = conn.execute(
                "SELECT c FROM ohlcv WHERE pick_id=? ORDER BY ts DESC LIMIT 1", (pick_id,)).fetchone()
            if first and last and first[0]:
                ret = (last[0] - first[0]) / first[0] * 100
                print(f"  pick {pick_id}: {n} candles | realized {ret:+.1f}% "
                      f"({first[0]:.8g} → {last[0]:.8g})")
        time.sleep(2)  # politeness for the free tier
    conn.close()
    return stored, done


def main():
    parser = argparse.ArgumentParser(description="fusion pick OHLCV backfill")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--config", default=os.environ.get(
        "SIGNAL_FUSION_CONFIG", "/opt/ares/ares-signal-fusion/config.json"))
    args = parser.parse_args()
    with open(args.config) as f:
        cfg = json.load(f)
    stored, done = backfill(cfg, limit=args.limit)
    print(f"backfill done: {stored} candles for {len(done)} picks")


if __name__ == "__main__":
    main()
