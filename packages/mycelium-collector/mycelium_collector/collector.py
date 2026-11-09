"""Collection cycle: pull trades from configured sources, dedupe, emit
observation traces to the configured Mycelium substrate, update the local
registry, score wallet edges."""
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict

from . import config
from .log import log
from .registry import db, update_registry
from .sources import gmgn as gmgn_source
from .sources import helius as helius_source
from .sources.geckoterminal import http_json, token_url


def trace(wallet, action, payload, session="wallet-intel") -> bool:
    body = {"agent": "wallet_intel", "session": session, "kind": "observation",
            "action": action, "target": wallet, "outcome": "success", "payload": payload}
    try:
        req = urllib.request.Request(
            f"{config.MYCELIUM_URL}/api/trace",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status in (200, 201)
    except Exception as e:
        log(f"  [mycelium] trace failed: {e}")
        return False


def resolve_symbols(conn, trades: list, cap: int = 10) -> None:
    known = {r[0] for r in conn.execute("SELECT mint FROM token_stats").fetchall()}
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
        d = http_json(token_url(mint), timeout=10)
        n += 1
        try:
            t["symbol"] = d["data"]["attributes"]["symbol"]
        except Exception:
            t["symbol"] = (mint or "")[:8]


def score_edges(conn) -> None:
    """Score wallets: mean(current/entry) across their buys older than
    config.EDGE_LOOKUP_AGE_S. Reads back trades from the substrate itself
    (not the local registry) so this works even if this process didn't
    emit every trade being scored."""
    d = http_json(f"{config.MYCELIUM_URL}/api/traces?agent=wallet_intel&limit=500", timeout=15)
    if not isinstance(d, dict) or "error" in d:
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
        if ts and time.time() - ts < config.EDGE_LOOKUP_AGE_S:
            continue
        buys.append({"wallet": t.get("target"), "mint": p.get("token"),
                     "price": float(p["price_usd"]), "ts": ts})
    if not buys:
        return
    mints = list({b["mint"] for b in buys if b["mint"]})[:config.PRICE_LOOKUP_CAP]
    now_prices: Dict[str, float] = {}
    for mint in mints:
        d2 = http_json(token_url(mint), timeout=12)
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
    for w, ratios in per_wallet.items():
        if len(ratios) < 2:
            continue
        edge = sum(ratios) / len(ratios)
        conn.execute("UPDATE wallets SET edge=? WHERE address=?", (round(edge, 3), w))
        trace(w, "wallet_score", {"edge": round(edge, 3), "n": len(ratios), "ts": time.time()},
              session="wallet-intel")
    conn.commit()
    if per_wallet:
        log(f"[edge] scored {len(per_wallet)} wallets")


def run_cycle() -> Dict[str, Any]:
    conn = db()
    trades = []
    if gmgn_source.gmgn_allowed(conn):
        for kind in ("smartmoney", "kol"):
            trades.extend(gmgn_source.gmgn_trades(kind, conn))
    trades.extend(helius_source.helius_trades(conn))
    resolve_symbols(conn, trades)

    if not trades:
        log("[cycle] no new wallet trades")
        conn.close()
        return {"fetched": 0, "new": 0, "emitted": 0}

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

    now_iso = datetime.now(timezone.utc).isoformat()
    update_registry(conn, new_trades, now_iso)
    score_edges(conn)
    conn.close()
    log(f"[cycle] emitted {emitted} traces to {config.MYCELIUM_URL}")
    return {"fetched": len(trades), "new": len(new_trades), "emitted": emitted}


def status() -> Dict[str, Any]:
    conn = db()
    w = conn.execute("SELECT COUNT(*), SUM(buys), SUM(volume_usd) FROM wallets").fetchone()
    top_tokens = conn.execute(
        "SELECT symbol, buy_volume, distinct_wallets FROM token_stats ORDER BY buy_volume DESC LIMIT 8"
    ).fetchall()
    top_wallets = conn.execute(
        "SELECT address, buys, volume_usd, tags FROM wallets ORDER BY volume_usd DESC LIMIT 8"
    ).fetchall()
    conn.close()
    return {
        "db": config.DB_PATH,
        "wallets_tracked": w[0] or 0,
        "total_buys": w[1] or 0,
        "total_volume_usd": w[2] or 0.0,
        "top_tokens": [{"symbol": r[0], "buy_volume": r[1], "distinct_wallets": r[2]} for r in top_tokens],
        "top_wallets": [{"address": r[0], "buys": r[1], "volume_usd": r[2], "tags": r[3]} for r in top_wallets],
    }
