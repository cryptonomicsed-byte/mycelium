#!/usr/bin/env python3
"""GMGN round-robin client — rotate API keys AND proxies across calls.

Read endpoints (holders/traders/smartmoney/kol) need only:
    X-APIKEY: <key>
    query:    timestamp=<unix> & client_id=<uuid>
No signature required (that's only for swap/order). This lets us hit
https://openapi.gmgn.ai directly with a rotating key — no .env, no CLI,
no per-call env-var clobbering.

Key pool:  /opt/ares/.gmgn_keys.json   [{"api_key","privkey_pem"}, ...]
Proxy pool: /opt/ares/.gmgn_proxies.json  ["http://host:port", ...]  (optional)

Rate-limit model (from gmgn-skills):
  - RATE_LIMIT_EXCEEDED — per-key quota hit; rotate to next key
  - RATE_LIMIT_BANNED   — PER-IP ban (repeated violations); rotating keys
    from the same IP will NOT lift it. Needs proxy rotation. Each request
    during a ban extends it +5s (up to +5min). NEVER hammer.
  - reset_at in the error body = unix ts when the ban lifts.

Per-key cooldown state persists in /opt/ares/.gmgn_pool_state.json so a
cooldown survives restarts (same pattern as api_key_pool.py).
"""
import json
import os
import random
import time
import uuid
import urllib.request
import urllib.error

HOST = "https://openapi.gmgn.ai"
KEYS_FILE = "/opt/ares/.gmgn_keys.json"
PROXIES_FILE = "/opt/ares/.gmgn_proxies.json"
STATE_FILE = "/opt/ares/.gmgn_pool_state.json"
COOLDOWN_EXCEEDED = 120    # per-key quota reset ~2 min
COOLDOWN_BANNED = 300      # IP ban default 5 min (overridden by reset_at)


def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _state():
    return _load_json(STATE_FILE, {})


def _save_state(st):
    _save_json(STATE_FILE, st)


def keys():
    return _load_json(KEYS_FILE, [])


def proxies():
    return _load_json(PROXIES_FILE, [])


def cooling(key):
    st = _state()
    until = st.get(key, 0)
    return until > time.time(), until


def ip_banned() -> float:
    """Read the shared IP-ban cooldown from wallet_intel.db (set by scanner/
    collector with escalating backoff). Returns 0 if not banned."""
    try:
        db_path = "/opt/ares/wallet_intel/wallet_intel.db"
        import sqlite3
        c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        try:
            row = c.execute("SELECT v FROM state WHERE k='gmgn_ban_until'").fetchone()
            return float(row[0]) if row and row[0] else 0.0
        finally:
            c.close()
    except Exception:
        return 0.0


def mark_cooldown(key, seconds):
    st = _state()
    st[key] = time.time() + seconds
    _save_state(st)


def parse_reset_at(err_body: str):
    """Pull reset_at (unix) out of a GMGN error body if present."""
    try:
        d = json.loads(err_body)
        ra = d.get("reset_at")
        if ra:
            return float(ra)
    except Exception:
        pass
    import re
    m = re.search(r'"reset_at"\s*:\s*(\d+)', err_body)
    if m:
        return float(m.group(1))
    return None


def pick_key():
    """Round-robin-ish: prefer a non-cooling key, random among available."""
    ks = keys()
    if not ks:
        return None, "no keys in pool"
    avail = []
    for k in ks:
        api = k.get("api_key", "")
        if not api:
            continue
        c, until = cooling(api)
        if not c:
            avail.append(api)
    if not avail:
        # everything cooling — pick the one that cools soonest
        best, best_t = None, float("inf")
        for k in ks:
            api = k.get("api_key", "")
            if not api:
                continue
            c, until = cooling(api)
            if until < best_t:
                best, best_t = api, until
        return best, f"all keys cooling, soonest at {time.strftime('%H:%M:%S', time.localtime(best_t))}"
    return random.choice(avail), "ok"


def pick_proxy():
    ps = proxies()
    if not ps:
        return None
    return random.choice(ps)


def gmgn_get(path: str, params: dict, timeout: int = 20):
    """GET https://openapi.gmgn.ai{path}?params&timestamp&client_id with a
    rotating key (+ optional rotating proxy). Returns (status, body_dict)
    or (0, {error}) on transport failure."""
    # respect the shared IP ban (escalating backoff from scanner/collector)
    ip_until = ip_banned()
    if ip_until > time.time():
        return 0, {"error": "IP_BANNED", "reset_at": ip_until}
    key, _ = pick_key()
    if not key:
        return 0, {"error": "no gmgn key available"}

    q = dict(params)
    q["timestamp"] = int(time.time())
    q["client_id"] = str(uuid.uuid4())
    qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in q.items())
    url = f"{HOST}{path}?{qs}"

    proxy = pick_proxy()
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        opener = urllib.request.build_opener()

    req = urllib.request.Request(url, headers={
        "X-APIKEY": key,
        "Content-Type": "application/json",
        "User-Agent": "gmgn-wallet-intel/1.0",
    })
    try:
        with opener.open(req, timeout=timeout) as r:
            raw = r.read().decode(errors="replace")
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, {"raw": raw[:500]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            body = json.loads(raw)
        except Exception:
            body = {"raw": raw[:500]}
        if e.code == 429:
            err = str(body.get("error", "")).upper()
            reset = parse_reset_at(raw)
            if "BANNED" in err:
                mark_cooldown(key, COOLDOWN_BANNED)
                return e.code, {"error": "RATE_LIMIT_BANNED", "reset_at": reset, "raw": raw[:200]}
            mark_cooldown(key, COOLDOWN_EXCEEDED)
            return e.code, {"error": "RATE_LIMIT_EXCEEDED", "reset_at": reset, "raw": raw[:200]}
        return e.code, body
    except Exception as e:
        return 0, {"error": str(e)}


def holders(mint: str, order_by: str = "amount_percentage", limit: int = 20, tag: str = "") -> list:
    """Top holders for a token. Returns list of holder dicts (GMGN shape)."""
    params = {"chain": "sol", "address": mint, "limit": limit, "order_by": order_by}
    if tag:
        params["tag"] = tag
    st, body = gmgn_get("/v1/market/token_top_holders", params)
    if st != 200 or not isinstance(body, dict):
        return []
    lst = body.get("list") or body.get("data") or []
    return lst if isinstance(lst, list) else []


def traders(mint: str, order_by: str = "buy_volume_cur", limit: int = 20, tag: str = "") -> list:
    """Top traders for a token (the endpoint our scanner never used)."""
    params = {"chain": "sol", "address": mint, "limit": limit, "order_by": order_by}
    if tag:
        params["tag"] = tag
    st, body = gmgn_get("/v1/market/token_top_traders", params)
    if st != 200 or not isinstance(body, dict):
        return []
    lst = body.get("list") or body.get("data") or []
    return lst if isinstance(lst, list) else []


def token_snapshot(mint: str) -> dict:
    """Best-effort market snapshot for one token from the endpoints we HAVE
    (GMGN openapi has NO token-info endpoint — derived from top holders +
    top traders). Returns {token, symbol, top10_share, bundler_rat_share,
    holder_count, buy_volume_24h, sell_volume_24h} or {} on failure.
    Liquidity/price come from the Vantage signal pool (signal_fusion's
    vantage_market_provider) — GMGN can't supply them here."""
    snap: dict = {"token": mint, "symbol": "?"}
    try:
        hs = holders(mint, limit=20) or []
        if hs:
            pcts = []
            for h in hs:
                p = h.get("amount_percentage") or h.get("percentage") or h.get("pct")
                try:
                    pcts.append(float(p))
                except (TypeError, ValueError):
                    pass
            top10 = sum(pcts[:10])
            snap["top10_share"] = min(1.0, top10 / 100.0)
            snap["holder_count"] = len(hs)
        trs = traders(mint, limit=20) or []
        if trs:
            buy = sell = 0.0
            bad = 0
            for t in trs:
                tag = str(t.get("tag") or "").lower()
                if any(x in tag for x in ("bundler", "rat", "wash")):
                    bad += 1
                try:
                    buy += float(t.get("buy_volume_cur") or 0)
                    sell += float(t.get("sell_volume_cur") or 0)
                except (TypeError, ValueError):
                    pass
            snap["bundler_rat_share"] = bad / max(1, len(trs))
            snap["buy_volume_24h"] = buy
            snap["sell_volume_24h"] = sell
            snap["volume_24h_usd"] = buy + sell
            sym = trs[0].get("symbol") or ""
            if sym:
                snap["symbol"] = str(sym)[:16]
    except Exception:  # noqa: BLE001 — snapshot is best-effort
        return {}
    return snap


def smartmoney_trades(limit: int = 50):
    st, body = gmgn_get("/v1/user/smartmoney", {"limit": limit})
    if st != 200 or not isinstance(body, dict):
        return []
    lst = body.get("list") or body.get("data") or []
    return lst if isinstance(lst, list) else []


def kol_trades(limit: int = 50):
    st, body = gmgn_get("/v1/user/kol", {"limit": limit})
    if st != 200 or not isinstance(body, dict):
        return []
    lst = body.get("list") or body.get("data") or []
    return lst if isinstance(lst, list) else []


def normalize_trades(items: list, kind: str) -> list:
    """Normalize raw smartmoney/kol items into the collector's trade shape."""
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


import urllib.parse  # noqa: E402  (used above in qs build)


def status():
    ks = keys()
    ps = proxies()
    st = _state()
    print(f"keys: {len(ks)}   proxies: {len(ps)}")
    for k in ks:
        api = k.get("api_key", "")
        until = st.get(api, 0)
        state = "cooling" if until > time.time() else "ready"
        print(f"  {api[:12]}...  {state}" + (f" until {time.strftime('%H:%M:%S', time.localtime(until))}" if state == "cooling" else ""))
    return len(ks), len(ps)


if __name__ == "__main__":
    status()
