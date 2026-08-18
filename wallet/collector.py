#!/usr/bin/env python3
"""Mycelium Wallet Intel collector — senses what wallets are buying and emits
observation traces into the Mycelium substrate.

Sources:
  1. GMGN smart money + KOL trades (gmgn-cli track smartmoney/kol --raw) — LIVE, primary
  2. Vantage wallet_trades (Helius Enhanced Transactions) — best-effort historical

Emits traces (agent=wallet_intel, kind=observation):
  action=wallet_buy / wallet_sell, target=<wallet>,
  payload={token, symbol, amount_usd, price_usd, ts, source, tags, tx, price_change}

Local registry: /opt/ares/wallet_intel/wallet_intel.db
  wallets(address, tags, buys, sells, volume_usd, distinct_tokens, edge, first_seen, last_seen)
  token_stats(mint, symbol, distinct_wallets, buy_volume, first_buy_ts, first_buyers)
  seen(tx, source) — dedupe; state(k, v) — last-seen cursors

Usage:
  python3 wallet/collector.py --once        # single collection cycle
  python3 wallet/collector.py --daemon 300  # loop every 5 min
  python3 wallet/collector.py --status      # registry snapshot
"""
import json, os, sqlite3, subprocess, sys, time, urllib.request
from datetime import datetime, timezone
from typing import Any, Dict

BASE = "/opt/ares/wallet_intel"
os.makedirs(BASE, exist_ok=True)
DB = f"{BASE}/wallet_intel.db"
MYCELIUM = os.environ.get("MYCELIUM_URL", "http://127.0.0.1:8811")
VANTAGE_DB = "/opt/ares/Vantage/data/vantage.db"
GMGN = "/usr/local/bin/gmgn-cli"
GEKKO = "https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}"

PRICE_LOOKUP_CAP = 20     # per cycle, GeckoTerminal token-price lookups
EDGE_WINDOW_H = 6         # buys older than this get outcome-scored
EDGE_LOOKUP_AGE_S = 6 * 3600


def log(msg):
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}"
    print(line, flush=True)
    try:
        with open(f"{BASE}/wallet_intel.log", "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def db():
    conn = sqlite3.connect(DB)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS seen(tx TEXT PRIMARY KEY, source TEXT, emitted_at TEXT);
    CREATE TABLE IF NOT EXISTS wallets(
        address TEXT PRIMARY KEY, tags TEXT, buys INTEGER DEFAULT 0, sells INTEGER DEFAULT 0,
        volume_usd REAL DEFAULT 0, distinct_tokens INTEGER DEFAULT 0,
        edge REAL, first_seen TEXT, last_seen TEXT, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS token_stats(
        mint TEXT PRIMARY KEY, symbol TEXT, distinct_wallets INTEGER DEFAULT 0,
        buy_volume REAL DEFAULT 0, first_buy_ts TEXT, first_buyers TEXT, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS wallet_tokens(
        wallet TEXT, mint TEXT, first_buy_ts TEXT, PRIMARY KEY(wallet, mint));
    CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY, v TEXT);
    """)
    conn.commit()
    return conn


def http_json(url: str, timeout: int = 15) -> Dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return {"error": str(e)}


def trace(wallet, action, payload, session="wallet-intel"):
    body = {"agent": "wallet_intel", "session": session, "kind": "observation",
            "action": action, "target": wallet, "outcome": "success", "payload": payload}
    try:
        req = urllib.request.Request(f"{MYCELIUM}/api/trace",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status in (200, 201)
    except Exception as e:
        log(f"  [mycelium] trace failed: {e}")
        return False


# ---------------------------------------------------------------- GMGN source
def gmgn_allowed(conn) -> bool:
    row = conn.execute("SELECT v FROM state WHERE k='gmgn_ban_until'").fetchone()
    if not row:
        return True
    try:
        return time.time() > float(row[0])
    except Exception:
        return True


def gmgn_trades(kind: str, conn) -> list:
    """smartmoney|kol trades. PRIMARY: gmgn_pool direct API (rotating keys +
    proxies, per-key cooldown). FALLBACK: gmgn-cli (reads ~/.config/gmgn/.env).
    Respects the IP ban cooldown: on RATE_LIMIT_BANNED, skip GMGN."""
    try:
        import gmgn_pool as _pool
        raw = _pool.smartmoney_trades(50) if kind == "smartmoney" else _pool.kol_trades(50)
        if raw:
            return _pool.normalize_trades(raw, kind)
    except Exception as e:
        log(f"  [gmgn] pool {kind} error: {e}")
    try:
        out = subprocess.run([GMGN, "track", kind, "--chain", "sol", "--limit", "50", "--raw"],
                             capture_output=True, text=True, timeout=60)
    except Exception as e:
        log(f"  [gmgn] {kind} exec error: {e}")
        return []
    if out.returncode != 0:
        err = out.stderr or out.stdout or ""
        if "RATE_LIMIT_BANNED" in err or "banned" in err.lower():
            try:
                import scanner as _sc
                _sc._mark_ban(conn, err)
            except Exception:
                try:
                    until = _sc._parse_ban_reset(err) if 'scanner' in dir() else None
                except Exception:
                    until = None
                until = (until + 300) if until else (time.time() + 900)
                conn.execute("INSERT OR REPLACE INTO state(k, v) VALUES ('gmgn_ban_until', ?)",
                             (str(until),))
                conn.commit()
                log(f"  [gmgn] banned — resuming {time.strftime('%H:%M:%S', time.localtime(until))}")
        else:
            log(f"  [gmgn] {kind} exit {out.returncode}: {err[:150]}")
        return []
    try:
        data = json.loads(out.stdout)
    except Exception:
        log(f"  [gmgn] {kind} bad JSON")
        return []
    items = data.get("list", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    trades = []
    for it in items:
        if not isinstance(it, dict):
            continue
        tok = it.get("base_token") or {}
        mi = it.get("maker_info") or {}
        ts = it.get("timestamp") or 0
        trades.append({
            "tx": it.get("transaction_hash") or "",
            "wallet": it.get("maker") or "",
            "side": (it.get("side") or "").lower(),
            "token": it.get("base_address") or "",
            "symbol": tok.get("symbol") or "?",
            "amount_usd": float(it.get("amount_usd") or 0),
            "price_usd": it.get("price_usd"),
            "ts": float(ts) if ts else 0,
            "tags": mi.get("tags") or [kind],
            "source": f"gmgn:{kind}",
        })
    return trades


# ---------------------------------------------------------------- Helius source
def helius_trades() -> list:
    """New rows from Vantage wallet_trades since the last seen signature."""
    try:
        conn = sqlite3.connect(f"file:{VANTAGE_DB}?mode=ro", uri=True, timeout=5)
    except Exception as e:
        log(f"  [helius] open error: {e}")
        return []
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(wallet_trades)").fetchall()]
        if "signature" not in cols or "wallet" not in cols or "token_mint" not in cols:
            conn.close()
            return []
        rows = conn.execute(
            "SELECT signature, wallet, timestamp, type, source, token_mint, token_amount, "
            "sol_change, ts_iso FROM wallet_trades ORDER BY timestamp").fetchall()
    finally:
        conn.close()
    reg = db()
    last = reg.execute("SELECT v FROM state WHERE k='helius_last_sig'").fetchone()
    last_sig = last[0] if last else ""
    trades = []
    for sig, wallet, ts, typ, src, mint, amt, sol_chg, ts_iso in rows:
        if last_sig and sig <= last_sig:
            continue
        if typ not in ("SWAP", "TRANSFER"):
            continue
        side = "buy" if (sol_chg or 0) < 0 and amt and amt > 0 else ("sell" if (sol_chg or 0) > 0 else "?")
        if side == "?" or not mint or str(mint).startswith("So111"):
            continue
        trades.append({
            "tx": sig, "wallet": wallet, "side": side, "token": mint,
            "symbol": (mint or "")[:8], "amount_usd": abs(sol_chg or 0) * 150.0,  # SOL-price approx
            "price_usd": None, "ts": float(ts or 0),
            "tags": ["helius"], "source": "helius",
        })
    reg.close()
    return trades


# ---------------------------------------------------------------- registry update
def update_registry(conn, trades, emitted):
    now = datetime.now(timezone.utc).isoformat()
    for t in trades:
        if t["tx"]:
            conn.execute("INSERT OR IGNORE INTO seen(tx, source, emitted_at) VALUES (?,?,?)",
                         (t["tx"], t["source"], now))
        w = t["wallet"]
        if not w:
            continue
        conn.execute("""INSERT INTO wallets(address, tags, buys, sells, volume_usd,
                        distinct_tokens, first_seen, last_seen, updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(address) DO UPDATE SET
                          tags=excluded.tags, buys=wallets.buys+excluded.buys,
                          sells=wallets.sells+excluded.sells,
                          volume_usd=wallets.volume_usd+excluded.volume_usd,
                          last_seen=excluded.last_seen, updated_at=excluded.updated_at""",
                     (w, json.dumps(t["tags"]),
                      1 if t["side"] == "buy" else 0, 1 if t["side"] == "sell" else 0,
                      t["amount_usd"], 0, now, now, now))
        if t["token"] and t["side"] == "buy":
            conn.execute("INSERT OR IGNORE INTO wallet_tokens(wallet, mint, first_buy_ts) VALUES (?,?,?)",
                         (w, t["token"], now))
            conn.execute("""INSERT INTO token_stats(mint, symbol, distinct_wallets, buy_volume,
                            first_buy_ts, first_buyers, updated_at)
                            VALUES (?,?,1,?,?,?,?)
                            ON CONFLICT(mint) DO UPDATE SET
                              symbol=excluded.symbol, buy_volume=token_stats.buy_volume+excluded.buy_volume,
                              updated_at=excluded.updated_at""",
                         (t["token"], t["symbol"], t["amount_usd"], now, json.dumps([w]), now))
    conn.execute("""UPDATE wallets SET distinct_tokens =
                    (SELECT COUNT(*) FROM wallet_tokens wt WHERE wt.wallet = wallets.address)""")
    conn.commit()


def distinct_token_counts(conn):
    return {}


# ---------------------------------------------------------------- edge scoring
def score_edges(conn):
    """Score wallets: mean(current/entry) across their buys older than 6h.

    Reads trades from the mycelium substrate (payloads carry price_usd from
    GMGN), fetches current token prices via GeckoTerminal (capped), updates
    the registry edge column and emits wallet_score traces for wallets with
    >= 2 scored buys.
    """
    d = http_json(f"{MYCELIUM}/api/traces?agent=wallet_intel&limit=500", timeout=15)
    if "error" in d:
        return
    traces = d.get("traces", d.get("data", [])) if isinstance(d, dict) else []
    buys = []
    for t in traces:
        p = t.get("payload") or {}
        if isinstance(p, str):
            try:
                p = json.loads(p)
            except Exception:
                continue
        if t.get("action") != "wallet_buy" or not p.get("price_usd"):
            continue
        ts = float(p.get("ts") or 0)
        if ts and time.time() - ts < EDGE_LOOKUP_AGE_S:
            continue
        buys.append({"wallet": t.get("target"), "mint": p.get("token"),
                     "price": float(p["price_usd"]), "ts": ts})
    if not buys:
        return
    mints = list({b["mint"] for b in buys if b["mint"]})[:PRICE_LOOKUP_CAP]
    now_prices: Dict[str, float] = {}
    for mint in mints:
        d2 = http_json(GEKKO.format(mint=mint), timeout=12)
        try:
            now_prices[mint] = float(d2["data"]["attributes"]["price_usd"])
        except Exception:
            continue
    per_wallet: Dict[str, list] = {}
    for b in buys:
        now = now_prices.get(b["mint"])
        if not now or not b["price"]:
            continue
        per_wallet.setdefault(b["wallet"], []).append(now / b["price"])
    now_iso = datetime.now(timezone.utc).isoformat()
    for w, ratios in per_wallet.items():
        if len(ratios) < 2:
            continue
        edge = sum(ratios) / len(ratios)
        conn.execute("UPDATE wallets SET edge=? WHERE address=?", (round(edge, 3), w))
        trace(w, "wallet_score", {"edge": round(edge, 3), "n": len(ratios),
                                  "ts": time.time()}, session="wallet-intel")
    conn.commit()
    if per_wallet:
        log(f"[edge] scored {len(per_wallet)} wallets")


# ---------------------------------------------------------------- symbol resolution
def resolve_symbols(trades: list, cap: int = 10) -> None:
    """Fill in human-readable symbols for mints we haven't seen (GeckoTerminal)."""
    seen_mints = db().execute("SELECT mint FROM token_stats").fetchall()
    known = {r[0] for r in seen_mints}
    n = 0
    for t in trades:
        if n >= cap:
            break
        sym = t.get("symbol") or ""
        if sym and sym != "?" and len(sym) > 8:
            continue
        mint = t.get("token") or ""
        if not mint or mint in known:
            continue
        d = http_json(GEKKO.format(mint=mint), timeout=10)
        n += 1
        try:
            t["symbol"] = d["data"]["attributes"]["symbol"]
        except Exception:
            t["symbol"] = (mint or "")[:8]


# ---------------------------------------------------------------- cycle
def run_cycle():
    conn = db()
    trades = []
    if gmgn_allowed(conn):
        for kind in ("smartmoney", "kol"):
            trades.extend(gmgn_trades(kind, conn))
    trades.extend(helius_trades())
    resolve_symbols(trades)
    if not trades:
        log("[cycle] no new wallet trades")
        conn.close()
        return

    new_trades = []
    for t in trades:
        if t["tx"] and conn.execute("SELECT 1 FROM seen WHERE tx=?", (t["tx"],)).fetchone():
            continue
        new_trades.append(t)
    log(f"[cycle] {len(trades)} fetched, {len(new_trades)} new")

    emitted = 0
    for t in new_trades:
        action = "wallet_buy" if t["side"] == "buy" else ("wallet_sell" if t["side"] == "sell" else "wallet_trade")
        ok = trace(t["wallet"], action, {
            "token": t["token"], "symbol": t["symbol"], "amount_usd": t["amount_usd"],
            "price_usd": t["price_usd"], "ts": t["ts"], "source": t["source"],
            "tags": t["tags"], "tx": t["tx"],
        })
        if ok:
            emitted += 1
    update_registry(conn, new_trades, emitted)
    score_edges(conn)
    conn.close()
    log(f"[cycle] emitted {emitted} traces to mycelium")


def cmd_status():
    conn = db()
    print("=== Wallet Intel status ===")
    print(f"db: {DB}")
    w = conn.execute("SELECT COUNT(*), SUM(buys), SUM(volume_usd) FROM wallets").fetchone()
    print(f"wallets tracked: {w[0]} | total buys: {w[1]} | volume: ${w[2]:,.0f}")
    print("\ntop tokens by buy volume:")
    for r in conn.execute("SELECT symbol, buy_volume, distinct_wallets FROM token_stats ORDER BY buy_volume DESC LIMIT 8"):
        print(f"  {r[0]:12s} ${r[1]:>12,.0f}  wallets={r[2]}")
    print("\ntop wallets by volume:")
    for r in conn.execute("SELECT address, buys, volume_usd, tags FROM wallets ORDER BY volume_usd DESC LIMIT 8"):
        print(f"  {r[0][:12]}..  buys={r[1]} ${r[2]:>10,.0f}  {r[3][:60]}")
    conn.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--status" in args:
        cmd_status()
    elif "--once" in args:
        run_cycle()
    else:
        m = [x for x in args if x.startswith("--daemon")]
        interval = int(m[0].split()[1]) if len(m) and " " in m[0] else 300
        if "--daemon" in args:
            i = args.index("--daemon")
            if i + 1 < len(args):
                interval = int(args[i + 1])
        with open(f"{BASE}/wallet_intel.pid", "w") as f:
            f.write(str(os.getpid()))
        log(f"[daemon] starting interval={interval}s")
        while True:
            try:
                run_cycle()
            except Exception as e:
                log(f"[daemon] cycle error: {e}")
            try:
                import scanner as _sc
                _sc.run_cycle()
            except Exception as e:
                log(f"[daemon] scanner cycle error: {e}")
            time.sleep(interval)
