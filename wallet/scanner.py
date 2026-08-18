#!/usr/bin/env python3
"""wallet/scanner.py — token-scope wallet scanner ("who's trading this token?").

Reads the TOP candidate tokens from the Vantage signal pool (the same tokens
the council is weighing), then pulls the REAL holder/trader roster for each
from GMGN's token_top_holders endpoint and classifies wallets into roles:

    whale          — top holders by % of supply (amount_percentage)
    major_trader   — top by buy_volume_cur
    top_profit     — top by unrealized_profit
    influencer     — wallet tagged renowned / smart_degen / axiom / padre
    early_buyer    — first_buyer rows already in Vantage token_wallet_roles
    deployer       — deployer rows already in Vantage token_wallet_roles

Every qualifying wallet is stored in BOTH places:
  1. Vantage graph — token_wallet_roles (typed edge wallet→token) +
     tracked_wallets (node), same record_role pattern as pumpfun_wallet_intel
     so /api/moneyflow sees it.
  2. Mycelium registry — wallet_intel.db (wallets/token_stats/wallet_tokens)
     plus observation traces to the substrate so miners can reason over it.

Also seeds the registry from Vantage's EXISTING wallet intelligence
(alpha_wallets, wallet_reputation, social_wallet_links, token_wallet_roles)
so the ~40k wallets Vantage already knows about become first-class citizens
in Mycelium — "use the wallets it has and the ones we gather".

GMGN safety: every call goes through gmgn_run() which honors the shared
gmgn_ban_until cooldown and — crucially — PARSES the server's real reset
time out of the RATE_LIMIT_BANNED message instead of a flat +1h, so a ban
that resets in 30s doesn't idle us for an hour.

Usage:
    python3 scanner.py --once          # seed registry + scan top tokens once
    python3 scanner.py --daemon 900    # loop every 900s
    python3 scanner.py --status        # registry snapshot
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, "/opt/ares")
import vantage_db_shim as _vshim  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "wallet_intel.db")
GMGN = "/usr/local/bin/gmgn-cli"
VANTAGE_DB = "/opt/ares/Vantage/data/vantage.db"
MYCELIUM_GATEWAY = os.environ.get("MYCELIUM_GATEWAY", "http://127.0.0.1:8811")

# Birdeye fallback (same shared key pool as every other Ares daemon)
sys.path.insert(0, "/opt/ares")
import api_key_pool  # noqa: E402

# ── tuning ──────────────────────────────────────────────────────────────
MAX_TOKENS_PER_CYCLE = 3          # GMGN quota is tight — 3 tokens/cycle
HOLDERS_LIMIT = 20                # top-N per order-by per token
ORDER_BYS = ("amount_percentage", "buy_volume_cur", "unrealized_profit")
TAG_FILTERS = ("renowned", "smart_degen", "axiom", "padre")
SEED_INTERVAL = 6 * 3600          # re-seed from Vantage every 6h
SCAN_INTERVAL = 15 * 60           # scan cycle every 15 min
ROLE_LABELS = {"deployer": "Deployer", "top_holder": "Top Holder",
               "top_trader": "Top Trader", "first_buyer": "First Buyer"}

# ── logging ─────────────────────────────────────────────────────────────
def log(msg):
    print(f"[scanner {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def db():
    conn = sqlite3.connect(DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS wallets (
        address TEXT PRIMARY KEY, tags TEXT, buys INTEGER DEFAULT 0,
        sells INTEGER DEFAULT 0, volume_usd REAL DEFAULT 0,
        distinct_tokens INTEGER DEFAULT 0, edge REAL DEFAULT 0,
        first_seen REAL, last_seen REAL, updated_at REAL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS token_stats (
        mint TEXT PRIMARY KEY, symbol TEXT, distinct_wallets INTEGER DEFAULT 0,
        buy_volume REAL DEFAULT 0, first_buy_ts REAL, first_buyers TEXT, updated_at REAL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS wallet_tokens (
        wallet TEXT, mint TEXT, first_buy_ts REAL, PRIMARY KEY(wallet, mint))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS seen (
        tx TEXT PRIMARY KEY, source TEXT, emitted_at REAL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS state (k TEXT PRIMARY KEY, v TEXT)""")
    return conn


def merge_tags(conn, wallet, new_tags):
    """Add tags to a wallet, deduped, preserving order. Pure-Python merge —
    SQL json concatenation produces malformed JSON."""
    if not new_tags:
        return
    row = conn.execute("SELECT tags FROM wallets WHERE address=?", (wallet,)).fetchone()
    cur = []
    if row and row[0]:
        try:
            cur = json.loads(row[0])
        except Exception:
            cur = []
    merged = list(dict.fromkeys(cur + list(new_tags)))  # dedupe, keep order
    conn.execute("UPDATE wallets SET tags=?, updated_at=? WHERE address=?",
                 (json.dumps(merged), time.time(), wallet))


# ── GMGN with real ban-time parsing ────────────────────────────────────
BAN_RESET_RE = re.compile(r"resets at (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) GMT")


def gmgn_allowed(conn):
    row = conn.execute("SELECT v FROM state WHERE k='gmgn_ban_until'").fetchone()
    if not row:
        return True
    try:
        return time.time() > float(row[0])
    except Exception:
        return True


def _parse_ban_reset(err: str):
    """Pull the server's real reset time out of a RATE_LIMIT_BANNED message."""
    m = BAN_RESET_RE.search(err or "")
    if not m:
        return None
    try:
        import calendar
        return calendar.timegm(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return None


def _mark_ban(conn, err):
    reset = _parse_ban_reset(err)
    # Escalating backoff: GMGN remembers recent violations past the stated
    # reset — firing exactly at reset re-triggers the ban. Track ban_count and
    # grow the buffer: 60s, 180s, 300s, 600s, then cap at 30 min.
    row = conn.execute("SELECT v FROM state WHERE k='gmgn_ban_count'").fetchone()
    count = int(float(row[0])) if row else 0
    count += 1
    conn.execute("INSERT OR REPLACE INTO state(k, v) VALUES ('gmgn_ban_count', ?)",
                 (str(count),))
    buffer_s = min(60 * (2 ** min(count - 1, 4)), 1800)
    until = (reset + buffer_s) if reset else (time.time() + buffer_s)
    conn.execute("INSERT OR REPLACE INTO state(k, v) VALUES ('gmgn_ban_until', ?)",
                 (str(until),))
    conn.commit()
    log(f"[gmgn] banned (x{count}) — resuming {time.strftime('%H:%M:%S', time.localtime(until))} (+{buffer_s}s buffer)")


def gmgn_holders(mint: str, order_by: str, conn, tag: str = "") -> list:
    """Token holders — PRIMARY: gmgn_pool direct API (rotating keys + proxies).
    FALLBACK: gmgn-cli. Honors the shared IP-ban cooldown."""
    if not gmgn_allowed(conn):
        return []
    try:
        import gmgn_pool as _pool
        out = _pool.holders(mint, order_by=order_by, limit=HOLDERS_LIMIT, tag=tag)
        if out:
            return out
    except Exception as e:
        log(f"  [holders] pool error: {e}")
    cmd = [GMGN, "token", "holders", "--chain", "sol", "--address", mint,
           "--limit", str(HOLDERS_LIMIT), "--order-by", order_by, "--raw"]
    if tag:
        cmd += ["--tag", tag]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:
        log(f"  [holders] exec error: {e}")
        return []
    if out.returncode != 0:
        err = out.stderr or out.stdout or ""
        if "RATE_LIMIT_BANNED" in err or "banned" in err.lower():
            _mark_ban(conn, err)
        else:
            log(f"  [holders] {order_by} exit {out.returncode}: {err[:120]}")
        return []
    try:
        data = json.loads(out.stdout)
    except Exception:
        log(f"  [holders] bad JSON for {order_by}")
        return []
    if isinstance(data, dict):
        data = data.get("list") or data.get("data") or data.get("result") or []
    return data if isinstance(data, list) else []


def gmgn_traders(mint: str, order_by: str, conn, tag: str = "") -> list:
    """Token traders — PRIMARY: gmgn_pool direct API. FALLBACK: gmgn-cli."""
    if not gmgn_allowed(conn):
        return []
    try:
        import gmgn_pool as _pool
        out = _pool.traders(mint, order_by=order_by, limit=HOLDERS_LIMIT, tag=tag)
        if out:
            return out
    except Exception as e:
        log(f"  [traders] pool error: {e}")
    cmd = [GMGN, "token", "traders", "--chain", "sol", "--address", mint,
           "--limit", str(HOLDERS_LIMIT), "--order-by", order_by, "--raw"]
    if tag:
        cmd += ["--tag", tag]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:
        log(f"  [traders] exec error: {e}")
        return []
    if out.returncode != 0:
        err = out.stderr or out.stdout or ""
        if "RATE_LIMIT_BANNED" in err or "banned" in err.lower():
            _mark_ban(conn, err)
        else:
            log(f"  [traders] {order_by} exit {out.returncode}: {err[:120]}")
        return []
    try:
        data = json.loads(out.stdout)
    except Exception:
        log(f"  [traders] bad JSON for {order_by}")
        return []
    if isinstance(data, dict):
        data = data.get("list") or data.get("data") or data.get("result") or []
    return data if isinstance(data, list) else []


def _holder_wallet(h):
    if not isinstance(h, dict):
        return None
    return (h.get("address") or h.get("wallet") or h.get("wallet_address")
            or (h.get("user") or {}).get("address") if isinstance(h.get("user"), dict) else None)


def _holder_metric(h, key):
    if not isinstance(h, dict):
        return 0.0
    v = h.get(key)
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except Exception:
            return 0.0
    return 0.0


def _holder_tags(h):
    """GMGN's native wallet classification — maker_token_tags (bundler, rat_trader,
    sniper, whale, top_holder, transfer_in, dev_team, creator) + tags
    (smart_degen, pump_smart, renowned, fresh_wallet, wash_trader, fomo, kol)."""
    if not isinstance(h, dict):
        return []
    out = []
    for t in (h.get("maker_token_tags") or []):
        if isinstance(t, str) and t:
            out.append(t)
    for t in (h.get("tags") or []):
        if isinstance(t, str) and t:
            out.append(t)
    return list(dict.fromkeys(out))


def _holder_funding(h):
    """Funding-source address (native_transfer.from_address) — used to detect
    same-source wallet clusters (GMGN holder-analysis 'related wallets')."""
    if not isinstance(h, dict):
        return ""
    nt = h.get("native_transfer")
    if isinstance(nt, dict):
        return nt.get("from_address") or ""
    return ""


# ── Birdeye fallback (when GMGN is cooling down) ──────────────────────
def birdeye_holders(mint: str, limit: int = 20) -> list:
    """Top holders via Birdeye v3 — same endpoint pumpfun_wallet_intel uses.
    Returns [{wallet, pct}] or [] on failure."""
    key = api_key_pool.get_key("birdeye", "wallet_scanner") or os.environ.get("BIRDEYE_KEY", "")
    if not key:
        return []
    try:
        req = urllib.request.Request(
            f"https://public-api.birdeye.so/defi/v3/token/holder?address={mint}&limit={limit}",
            headers={"X-API-KEY": key, "accept": "application/json"})
        with urllib.request.urlopen(req, timeout=12) as r:
            d = json.loads(r.read().decode())
        items = (d.get("data") or {}).get("items", [])
        out = []
        for h in items:
            if not isinstance(h, dict):
                continue
            w = h.get("owner") or h.get("address") or ""
            if not w:
                continue
            out.append({"wallet": w, "pct": _holder_metric(h, "percentage") or _holder_metric(h, "pct")})
        return out
    except Exception as e:
        log(f"  [birdeye] holders failed: {e}")
        return []


# ── trace to mycelium substrate ────────────────────────────────────────
def trace(wallet, action, payload, session="wallet-scan"):
    try:
        body = json.dumps({
            "agent": "wallet_intel", "session": session, "kind": "observation",
            "action": action, "target": wallet, "outcome": "success", "payload": payload,
        }).encode()
        req = urllib.request.Request(f"{MYCELIUM_GATEWAY}/api/trace", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status in (200, 201)
    except Exception:
        return False


# ── seed registry from Vantage's existing wallet intelligence ──────────
def seed_from_vantage(conn):
    """Pull alpha_wallets / wallet_reputation / social_wallet_links /
    token_wallet_roles from Vantage into the Mycelium registry so the
    wallets Vantage already tracks become first-class here."""
    if not os.path.exists(VANTAGE_DB):
        log("[seed] vantage.db not found — skip")
        return 0
    v = sqlite3.connect(VANTAGE_DB)
    v.row_factory = sqlite3.Row
    now = time.time()
    n = 0
    try:
        # 1. alpha_wallets — the 1,093 scored smart wallets (win_rate, tier, style)
        try:
            rows = v.execute(
                "SELECT chain, address, tags, win_rate, pnl_30d, total_trades, score, tier, style "
                "FROM alpha_wallets WHERE address != ''").fetchall()
            for r in rows:
                tags = json.loads(r["tags"]) if r["tags"] else []
                tags += [f"alpha:{r['tier'] or '?'}", f"style:{r['style'] or '?'}"]
                if r["win_rate"] and r["win_rate"] > 0.3:
                    tags.append("smart")
                conn.execute(
                    """INSERT OR REPLACE INTO wallets (address, tags, volume_usd, distinct_tokens,
                       first_seen, last_seen, updated_at)
                       VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(address) DO UPDATE SET tags=excluded.tags,
                         volume_usd=MAX(volume_usd, excluded.volume_usd),
                         updated_at=excluded.updated_at""",
                    (r["address"], json.dumps(list(set(tags))),
                     float(r["pnl_30d"] or 0), int(r["total_trades"] or 0),
                     now, now, now))
                n += 1
        except sqlite3.OperationalError as e:
            log(f"[seed] alpha_wallets: {e}")
        # 2. wallet_reputation — copy_trade_score >= 10 (proven traders)
        try:
            rows = v.execute(
                "SELECT wallet_address, copy_trade_score, first_buyer_count, top_trader_count, "
                "top_holder_count, tokens_tracked FROM wallet_reputation "
                "WHERE copy_trade_score >= 10 ORDER BY copy_trade_score DESC LIMIT 3000").fetchall()
            for r in rows:
                tags = []
                if r["copy_trade_score"] and r["copy_trade_score"] >= 30:
                    tags.append("whale_trader")
                elif r["copy_trade_score"] and r["copy_trade_score"] >= 20:
                    tags.append("proven_trader")
                if r["top_trader_count"]:
                    tags.append("top_trader")
                if r["top_holder_count"]:
                    tags.append("top_holder")
                if r["first_buyer_count"]:
                    tags.append("early_buyer")
                if not tags:
                    continue
                conn.execute(
                    """INSERT OR IGNORE INTO wallets (address, tags, first_seen, last_seen, updated_at)
                       VALUES (?,?,?,?,?)""",
                    (r["wallet_address"], json.dumps(tags), now, now, now))
                merge_tags(conn, r["wallet_address"], tags)
                n += 1
        except sqlite3.OperationalError as e:
            log(f"[seed] wallet_reputation: {e}")
        # 3. social_wallet_links — KOL/influencer wallets
        try:
            rows = v.execute(
                "SELECT wallet_address, platform, username FROM social_wallet_links "
                "WHERE wallet_address != ''").fetchall()
            for r in rows:
                conn.execute(
                    """INSERT OR IGNORE INTO wallets (address, tags, first_seen, last_seen, updated_at)
                       VALUES (?,?,?,?,?)""",
                    (r["wallet_address"], json.dumps([f"kol:{r['platform']}", f"@{r['username']}"]),
                     now, now, now))
                merge_tags(conn, r["wallet_address"], [f"kol:{r['platform']}", f"@{r['username']}"])
                n += 1
        except sqlite3.OperationalError as e:
            log(f"[seed] social_wallet_links: {e}")
        # 4. token_wallet_roles — first_buyer + deployer per mint (early-buyer intel)
        try:
            rows = v.execute(
                "SELECT mint, symbol, wallet_address, role FROM token_wallet_roles "
                "WHERE role IN ('first_buyer','deployer')").fetchall()
            for r in rows:
                tag = "early_buyer" if r["role"] == "first_buyer" else "deployer"
                conn.execute(
                    """INSERT OR IGNORE INTO wallets (address, tags, first_seen, last_seen, updated_at)
                       VALUES (?,?,?,?,?)""",
                    (r["wallet_address"], json.dumps([tag]), now, now, now))
                merge_tags(conn, r["wallet_address"], [tag])
                n += 1
        except sqlite3.OperationalError as e:
            log(f"[seed] token_wallet_roles: {e}")
        conn.commit()
        log(f"[seed] synced {n} wallets from Vantage")
    finally:
        v.close()
    return n


# ── top candidate tokens from the signal pool ─────────────────────────
def top_tokens(limit=10):
    """The tokens the council is actually weighing: degen/pump signals with
    mints, highest conviction, most recent."""
    if not os.path.exists(VANTAGE_DB):
        return []
    v = sqlite3.connect(VANTAGE_DB)
    try:
        rows = v.execute(
            """SELECT mint, symbol, MAX(conviction) conviction, COUNT(*) signals
               FROM signal_pool
               WHERE mint IS NOT NULL AND mint != '' AND mint != '?'
                 AND type IN ('degen','pump','trending','alpha')
               GROUP BY mint, symbol
               ORDER BY signals DESC, conviction DESC
               LIMIT ?""", (limit,)).fetchall()
        return [(r[0], r[1] or r[0][:8]) for r in rows]
    finally:
        v.close()


# ── persist into Vantage graph (same pattern as pumpfun_wallet_intel) ──
def record_role(mint, symbol, wallet, role, rank=None, metric=None, metric_label=None):
    if not wallet:
        return
    try:
        v = _vshim.get_sync_db()
        try:
            v.execute(
                """INSERT INTO token_wallet_roles (mint, symbol, wallet_address, role, rank, metric, metric_label)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(mint, wallet_address, role) DO UPDATE SET
                     rank=excluded.rank, metric=excluded.metric, discovered_at=datetime('now')""",
                (mint, symbol, wallet, role, rank, metric, metric_label))
            v.execute(
                """INSERT INTO tracked_wallets (chain, address, label, address_type)
                   VALUES ('solana', ?, ?, 'wallet')
                   ON CONFLICT(chain, address) DO UPDATE SET
                     label=CASE WHEN label='' THEN excluded.label ELSE label END""",
                (wallet, f"{ROLE_LABELS.get(role, role)}: {symbol or mint[:8]}"))
            v.commit()
        finally:
            v.close()
    except Exception as e:
        log(f"  [vantage] record_role failed: {e}")


# ── update local registry with a scanned wallet ───────────────────────
def update_registry(conn, wallet, mint, symbol, tags, metric):
    now = time.time()
    conn.execute(
        """INSERT OR IGNORE INTO wallets (address, tags, first_seen, last_seen, updated_at)
           VALUES (?,?,?,?,?)""",
        (wallet, json.dumps(tags), now, now, now))
    merge_tags(conn, wallet, tags)
    conn.execute(
        """INSERT OR IGNORE INTO wallet_tokens (wallet, mint, first_buy_ts) VALUES (?,?,?)""",
        (wallet, mint, now))
    conn.execute(
        """UPDATE token_stats SET distinct_wallets = (
             SELECT COUNT(DISTINCT wallet) FROM wallet_tokens WHERE mint=?)
           WHERE mint=?""", (mint, mint))
    conn.commit()


# ── scan one token ────────────────────────────────────────────────────
def scan_token(conn, mint, symbol):
    """Pull holders + traders (GMGN), classify with GMGN's NATIVE wallet tags
    (bundler/rat_trader/sniper/whale/smart_degen/renowned/fresh_wallet...),
    capture funding-source clusters, persist qualifying wallets into the
    Vantage graph + Mycelium registry + substrate traces."""
    log(f"[scan] {symbol} ({mint[:8]}…)")
    found = 0
    seen_wallets = {}
    clusters = {}   # funding_source -> [wallets]

    # 1) holder role scans: whale / major trader / top profit
    gmgn_ok = False
    for ob in ORDER_BYS:
        holders = gmgn_holders(mint, ob, conn)
        if holders:
            gmgn_ok = True
        if not holders:
            continue
        role = {"amount_percentage": "top_holder",
                "buy_volume_cur": "top_trader",
                "unrealized_profit": "top_profit"}[ob]
        for rank, h in enumerate(holders, 1):
            w = _holder_wallet(h)
            if not w:
                continue
            metric = _holder_metric(h, ob)
            if w not in seen_wallets:
                seen_wallets[w] = {"roles": [], "tags": [], "best_metric": 0.0, "funding": ""}
            seen_wallets[w]["roles"].append((role, rank, metric))
            seen_wallets[w]["best_metric"] = max(seen_wallets[w]["best_metric"], metric)
            seen_wallets[w]["tags"] += _holder_tags(h)
            src = _holder_funding(h)
            if src:
                seen_wallets[w]["funding"] = src
                clusters.setdefault(src, []).append(w)
            # persist every top-N holder as a typed graph edge
            record_role(mint, symbol, w, role, rank, metric, ob)
            found += 1
        time.sleep(1.2)  # be gentle with GMGN

    # 2) top TRADERS endpoint (the "major traders" list — never used before)
    for ob in ("buy_volume_cur", "profit"):
        traders = gmgn_traders(mint, ob, conn)
        if not traders:
            continue
        for rank, h in enumerate(traders, 1):
            w = _holder_wallet(h)
            if not w:
                continue
            metric = _holder_metric(h, ob)
            if w not in seen_wallets:
                seen_wallets[w] = {"roles": [], "tags": [], "best_metric": 0.0, "funding": ""}
            seen_wallets[w]["roles"].append(("top_trader", rank, metric))
            seen_wallets[w]["best_metric"] = max(seen_wallets[w]["best_metric"], metric)
            seen_wallets[w]["tags"] += _holder_tags(h)
            src = _holder_funding(h)
            if src:
                seen_wallets[w]["funding"] = src
                clusters.setdefault(src, []).append(w)
            record_role(mint, symbol, w, "top_trader", rank, metric, ob)
            found += 1
        time.sleep(1.2)

    # 3) Birdeye fallback: GMGN down (ban/quota) → at least get whales
    if not gmgn_ok:
        holders = birdeye_holders(mint, HOLDERS_LIMIT)
        for rank, h in enumerate(holders, 1):
            w = h.get("wallet")
            if not w:
                continue
            pct = h.get("pct") or 0.0
            if w not in seen_wallets:
                seen_wallets[w] = {"roles": [], "tags": [], "best_metric": 0.0, "funding": ""}
            seen_wallets[w]["roles"].append(("top_holder", rank, pct))
            seen_wallets[w]["best_metric"] = max(seen_wallets[w]["best_metric"], pct)
            record_role(mint, symbol, w, "top_holder", rank, pct, "pct_supply")
            found += 1
        if holders:
            log(f"  [scan] {symbol}: birdeye fallback {len(holders)} holders")

    # 4) influencer tag scans (renowned / smart_degen / axiom / padre)
    for tag in TAG_FILTERS:
        holders = gmgn_holders(mint, "amount_percentage", conn, tag=tag)
        if not holders:
            continue
        for rank, h in enumerate(holders, 1):
            w = _holder_wallet(h)
            if not w:
                continue
            if w not in seen_wallets:
                seen_wallets[w] = {"roles": [], "tags": [], "best_metric": 0.0, "funding": ""}
            seen_wallets[w]["roles"].append(("influencer", rank, 0))
            seen_wallets[w]["tags"] += _holder_tags(h)
            record_role(mint, symbol, w, "top_holder", rank, None, f"influencer:{tag}")
            found += 1
        time.sleep(1.2)

    # 5) classify + store locally + emit substrate traces
    GMGN_ROLE_MAP = {"rat_trader": "rat_trader", "sniper": "sniper", "bundler": "bundler",
                     "whale": "whale", "smart_degen": "smart", "pump_smart": "smart",
                     "renowned": "kol", "kol": "kol", "fresh_wallet": "fresh",
                     "wash_trader": "wash", "creator": "dev", "dev_team": "dev",
                     "top_holder": "top_holder", "top_trader": "top_trader",
                     "fomo": "fomo", "transfer_in": "transfer_in"}
    for w, info in seen_wallets.items():
        roles = [r[0] for r in info["roles"]]
        tags = list(set(roles))
        for t in info["tags"]:
            mapped = GMGN_ROLE_MAP.get(t)
            if mapped:
                tags.append(mapped)
        if "top_profit" in roles:
            tags.append("profitable")
        if "top_holder" in roles and info["best_metric"] >= 1.0:
            tags.append("whale")
        update_registry(conn, w, mint, symbol, tags, info["best_metric"])
        payload = {
            "token": mint, "symbol": symbol, "roles": roles,
            "tags": list(dict.fromkeys(info["tags"])),
            "best_metric": info["best_metric"], "source": "gmgn:token_top_holders",
        }
        if info["funding"]:
            payload["funding_source"] = info["funding"]
        trace(w, "wallet_found", payload)

    # 6) funding clusters — emit a trace per same-source group (correlation fuel)
    for src, wallets in clusters.items():
        if len(wallets) >= 2:
            trace(src, "wallet_cluster", {
                "token": mint, "symbol": symbol,
                "members": wallets[:20], "count": len(wallets),
                "source": "gmgn:native_transfer",
            })
            log(f"  [scan] {symbol}: cluster {src[:10]}.. -> {len(wallets)} wallets")

    # token_stats: first buyers + distinct wallets
    conn.execute(
        """INSERT OR REPLACE INTO token_stats (mint, symbol, distinct_wallets, first_buy_ts, updated_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(mint) DO UPDATE SET distinct_wallets=excluded.distinct_wallets,
             symbol=excluded.symbol, updated_at=excluded.updated_at""",
        (mint, symbol, len(seen_wallets), time.time(), time.time()))
    conn.commit()
    log(f"[scan] {symbol}: {len(seen_wallets)} wallets, {found} role rows")
    return len(seen_wallets)


# ── cycle ─────────────────────────────────────────────────────────────
def run_cycle():
    conn = db()
    # seed from Vantage on first run / every SEED_INTERVAL
    row = conn.execute("SELECT v FROM state WHERE k='last_seed_ts'").fetchone()
    last_seed = float(row[0]) if row else 0.0
    if time.time() - last_seed > SEED_INTERVAL:
        seed_from_vantage(conn)
        conn.execute("INSERT OR REPLACE INTO state(k, v) VALUES ('last_seed_ts', ?)",
                     (str(time.time()),))
        conn.commit()

    # scan top tokens (rate-limited by SCAN_INTERVAL + MAX_TOKENS_PER_CYCLE)
    row = conn.execute("SELECT v FROM state WHERE k='last_scan_ts'").fetchone()
    last_scan = float(row[0]) if row else 0.0
    if time.time() - last_scan < SCAN_INTERVAL:
        log("[cycle] within scan interval — skip")
        conn.close()
        return
    tokens = top_tokens(MAX_TOKENS_PER_CYCLE)
    log(f"[cycle] scanning {len(tokens)} top tokens")
    total = 0
    for mint, symbol in tokens:
        if not gmgn_allowed(conn):
            log("[cycle] GMGN cooling down — stop")
            break
        total += scan_token(conn, mint, symbol)
    conn.execute("INSERT OR REPLACE INTO state(k, v) VALUES ('last_scan_ts', ?)",
                 (str(time.time()),))
    conn.commit()
    log(f"[cycle] done: {total} wallets recorded")
    conn.close()


def cmd_status():
    conn = db()
    n_w = conn.execute("SELECT COUNT(*) FROM wallets").fetchone()[0]
    n_t = conn.execute("SELECT COUNT(*) FROM token_stats").fetchone()[0]
    n_wt = conn.execute("SELECT COUNT(*) FROM wallet_tokens").fetchone()[0]
    ban = conn.execute("SELECT v FROM state WHERE k='gmgn_ban_until'").fetchone()
    print(f"wallets: {n_w}   token_stats: {n_t}   wallet_tokens: {n_wt}")
    print(f"gmgn_ban_until: {ban[0] if ban else 'none'}")
    print("top tokens by signal count:")
    for mint, symbol in top_tokens(10):
        print(f"  {symbol:12s} {mint}")
    conn.close()


if __name__ == "__main__":
    if "--status" in sys.argv:
        cmd_status()
    elif "--daemon" in sys.argv:
        i = sys.argv.index("--daemon")
        interval = int(sys.argv[i + 1]) if i + 1 < len(sys.argv) else 900
        log(f"scanner daemon — every {interval}s")
        while True:
            try:
                run_cycle()
            except Exception as e:
                log(f"cycle error: {e}")
            time.sleep(interval)
    else:
        run_cycle()
