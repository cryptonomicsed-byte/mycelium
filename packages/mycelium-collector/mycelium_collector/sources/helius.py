"""Helius Enhanced Transactions source -- a REAL Helius API pull for a
configured wallet watch-list.

Replaces the phone-native version's "helius" source, which didn't call
Helius at all: it opened /opt/ares/Vantage/data/vantage.db directly as a
read-only SQLite file and read Vantage's own wallet_trades cache. That
only worked because the collector happened to run on the same filesystem
as that exact Vantage instance -- the definition of not-universal. This
calls Helius's Enhanced Transaction History API directly
(GET /v0/addresses/{address}/transactions), so it works from any device
with a HELIUS_API_KEY and no dependency on Vantage or any other
co-located service.

HONESTY NOTE: written against Helius's documented Enhanced Transactions
response shape (type/source/tokenTransfers/nativeTransfers/timestamp/
signature) but not exercised against a live Helius API key in this
environment -- no credentials were available to test with. Verify against
a real key before relying on this in production; the gmgn and
geckoterminal sources ARE live-verified (subprocess/HTTP against public
endpoints that need no auth).
"""
import json
import time
import urllib.error
import urllib.request

from .. import config
from ..log import log

HELIUS_TX_URL = "https://api.helius.xyz/v0/addresses/{wallet}/transactions"


def _http_json(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers={"User-Agent": "mycelium-collector/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, Exception) as e:
        log(f"  [helius] request error: {e}")
        return None


def helius_trades(conn) -> list:
    """New SWAP transactions for each configured tracked wallet, since the
    last-seen signature per wallet (cursor stored in the local registry)."""
    if not config.HELIUS_ENABLED:
        return []
    wallets = config.tracked_wallets()
    if not wallets:
        return []

    trades = []
    for wallet in wallets:
        cursor_key = f"helius_last_sig:{wallet}"
        row = conn.execute("SELECT v FROM state WHERE k=?", (cursor_key,)).fetchone()
        last_sig = row[0] if row else None

        url = HELIUS_TX_URL.format(wallet=wallet) + f"?api-key={config.HELIUS_API_KEY}&limit=50"
        if last_sig:
            url += f"&until={last_sig}"
        data = _http_json(url)
        if not isinstance(data, list) or not data:
            continue

        newest_sig = data[0].get("signature") if data else None
        for tx in data:
            if tx.get("type") != "SWAP":
                continue
            sig = tx.get("signature") or ""
            ts = tx.get("timestamp") or 0
            token_transfers = tx.get("tokenTransfers") or []
            native_transfers = tx.get("nativeTransfers") or []

            sol_change = 0.0
            for nt in native_transfers:
                if nt.get("fromUserAccount") == wallet:
                    sol_change -= float(nt.get("amount") or 0) / 1e9
                if nt.get("toUserAccount") == wallet:
                    sol_change += float(nt.get("amount") or 0) / 1e9

            mint, side = None, "?"
            for tt in token_transfers:
                if tt.get("toUserAccount") == wallet:
                    mint, side = tt.get("mint"), "buy"
                    break
                if tt.get("fromUserAccount") == wallet:
                    mint, side = tt.get("mint"), "sell"
                    break

            if side == "?" or not mint or str(mint).startswith("So111"):
                continue

            trades.append({
                "tx": sig, "wallet": wallet, "side": side, "token": mint,
                "symbol": (mint or "")[:8], "amount_usd": None,
                "price_usd": None, "ts": float(ts or 0),
                "tags": ["helius"], "source": "helius",
            })

        if newest_sig:
            conn.execute("INSERT OR REPLACE INTO state(k, v) VALUES (?, ?)", (cursor_key, newest_sig))
        conn.commit()

    return trades
