#!/usr/bin/env python3
"""cluster_detect.py — scan wallet_intel.db for buy clusters.

Emits ClusterEvent JSON objects (schema: radar/cluster_schema.md) when
≥N watchlist wallets bought the same token within a time window.

Usage:
  # One-shot scan (last 30 minutes)
  python3 cluster_detect.py --once

  # Daemon mode: scan every 60s, emit to stdout
  python3 cluster_detect.py --daemon --interval 60

  # Mirror to signal pool (requires VANTAGE_KEY)
  python3 cluster_detect.py --daemon --mirror

DB path: /opt/ares/wallet_intel/wallet_intel.db (VPS) or WALLET_INTEL_DB env var.
Watchlist: fleet/radar/watchlist.json (populated by radar/build_watchlist.py).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

SCRIPT_DIR = Path(__file__).parent
WATCHLIST_PATH = SCRIPT_DIR / "watchlist.json"
DB_PATH = Path(os.environ.get(
    "WALLET_INTEL_DB",
    "/opt/ares/wallet_intel/wallet_intel.db",
))

# Tunable thresholds (override via env)
MIN_CLUSTER_SIZE   = int(os.environ.get("CLUSTER_MIN_SIZE", "3"))
WINDOW_MINUTES     = int(os.environ.get("CLUSTER_WINDOW_MIN", "10"))
MIN_CONVICTION     = float(os.environ.get("CLUSTER_MIN_CONVICTION", "0.50"))
MIN_VOLUME_USD     = float(os.environ.get("CLUSTER_MIN_VOLUME", "200.0"))


def _load_watchlist() -> dict[str, dict]:
    """Load watchlist.json → {address: {tag, conviction, ...}}."""
    if not WATCHLIST_PATH.exists():
        return {}
    try:
        return {w["address"]: w for w in json.loads(WATCHLIST_PATH.read_text())}
    except Exception:
        return {}


def _pseudonymize(address: str) -> str:
    import hashlib, hmac
    key = os.environ.get("MYCELIUM_HMAC_KEY", "00" * 32)
    digest = hmac.new(bytes.fromhex(key), address.encode(), hashlib.sha256).hexdigest()
    return f"w_{digest[:8]}"


def detect_clusters(
    since_ts: float | None = None,
    watchlist: dict[str, dict] | None = None,
) -> Iterator[dict]:
    """Yield ClusterEvent dicts for clusters detected since `since_ts`."""
    if not DB_PATH.exists():
        return

    wl = watchlist if watchlist is not None else _load_watchlist()
    if not wl:
        return

    if since_ts is None:
        since_ts = time.time() - WINDOW_MINUTES * 60

    since_iso = datetime.fromtimestamp(since_ts, tz=timezone.utc).isoformat()

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
    except Exception:
        return

    try:
        # Pull recent buys by watchlist wallets
        placeholders = ",".join("?" * len(wl))
        rows = conn.execute(
            f"""
            SELECT wallet_address, token_address, symbol,
                   buy_volume_usd, created_at
            FROM wallet_buy_events
            WHERE wallet_address IN ({placeholders})
              AND side = 'buy'
              AND created_at >= ?
            ORDER BY token_address, created_at
            """,
            (*wl.keys(), since_iso),
        ).fetchall()
    except sqlite3.OperationalError:
        # Table name may differ — try alternate schema
        try:
            rows = conn.execute(
                f"""
                SELECT address AS wallet_address,
                       token AS token_address,
                       token AS symbol,
                       volume AS buy_volume_usd,
                       ts AS created_at
                FROM wallet_intel
                WHERE address IN ({placeholders})
                  AND action = 'buy'
                  AND ts >= ?
                ORDER BY token, ts
                """,
                (*wl.keys(), since_iso),
            ).fetchall()
        except Exception:
            conn.close()
            return
    finally:
        pass

    conn.close()

    # Group by token_address
    by_token: dict[str, list] = {}
    for row in rows:
        by_token.setdefault(row["token_address"], []).append(dict(row))

    now = datetime.now(tz=timezone.utc)

    for token_address, buys in by_token.items():
        # Filter by time window
        window_cutoff = now - timedelta(minutes=WINDOW_MINUTES)
        window_buys = [
            b for b in buys
            if datetime.fromisoformat(b["created_at"].replace("Z", "+00:00")) >= window_cutoff
        ]

        # Filter by conviction and volume
        qualified = [
            b for b in window_buys
            if wl.get(b["wallet_address"], {}).get("conviction", 0) >= MIN_CONVICTION
            and float(b.get("buy_volume_usd") or 0) >= MIN_VOLUME_USD
        ]

        if len(qualified) < MIN_CLUSTER_SIZE:
            continue

        wallets_detail = []
        total_volume = 0.0
        for b in qualified:
            wl_entry = wl.get(b["wallet_address"], {})
            vol = float(b.get("buy_volume_usd") or 0)
            total_volume += vol
            wallets_detail.append({
                "address": _pseudonymize(b["wallet_address"]),
                "tag": wl_entry.get("tag", "watchlist"),
                "conviction": wl_entry.get("conviction", 0.5),
                "buy_ts": b["created_at"],
                "buy_volume_usd": vol,
            })

        avg_conviction = sum(w["conviction"] for w in wallets_detail) / len(wallets_detail)
        cluster_size = len(wallets_detail)
        radar_score = min(100, int(
            (cluster_size / 5) * 40 +
            avg_conviction * 40 +
            (total_volume / 10_000) * 20
        ))

        ts_list = [w["buy_ts"] for w in wallets_detail]
        yield {
            "event": "buy_cluster",
            "token_address": token_address,
            "symbol": qualified[0].get("symbol", "?"),
            "cluster_size": cluster_size,
            "wallets": wallets_detail,
            "first_buy_ts": min(ts_list),
            "last_buy_ts": max(ts_list),
            "window_minutes": WINDOW_MINUTES,
            "total_volume_usd": round(total_volume, 2),
            "radar_score": radar_score,
            "detected_at": now.isoformat(),
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="one-shot scan and exit")
    parser.add_argument("--daemon", action="store_true", help="scan on interval")
    parser.add_argument("--interval", type=int, default=60, help="scan interval (seconds)")
    parser.add_argument("--mirror", action="store_true", help="POST clusters to Vantage signal pool")
    args = parser.parse_args()

    wl = _load_watchlist()
    print(f"Loaded watchlist: {len(wl)} wallets", flush=True)

    def scan():
        since = time.time() - WINDOW_MINUTES * 60
        clusters = list(detect_clusters(since_ts=since, watchlist=wl))
        for c in clusters:
            print(json.dumps(c), flush=True)
        if args.mirror and clusters:
            _mirror_to_vantage(clusters)
        return len(clusters)

    if args.once or not args.daemon:
        n = scan()
        print(f"Found {n} clusters", flush=True)
        return

    print(f"Daemon mode: scanning every {args.interval}s", flush=True)
    while True:
        try:
            scan()
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"scan error: {e}", flush=True)
        time.sleep(args.interval)


def _mirror_to_vantage(clusters: list[dict]) -> None:
    import urllib.request
    key = os.environ.get("VANTAGE_KEY", "")
    url = os.environ.get("VANTAGE_URL", "https://omokoda.duckdns.org")
    if not key:
        return
    for cluster in clusters:
        try:
            data = json.dumps({"kind": "buy_cluster", "payload": cluster}).encode()
            req = urllib.request.Request(
                f"{url}/api/signals/pool",
                data=data,
                headers={"X-Agent-Key": key, "Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            print(f"mirror error: {e}", flush=True)


if __name__ == "__main__":
    main()
