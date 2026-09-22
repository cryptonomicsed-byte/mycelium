"""market-observation domain — reading a tape, not a wallet list.

Why this is its own domain rather than three more miners in wallet-intel:
the question it asks is different in kind. wallet-intel reasons over
*traces* the wallet_intel agent emitted -- one row per buy or sell it
observed. This domain reasons over a *market event tape*: many actors, one
instrument, and the shape of the crowd's participation in it.

The distinction that matters is the one `wallet_correlation` does not and
must not answer. That miner measures SIMILARITY -- which wallets move
together. This domain measures AUTHENTICITY -- whether a movement is real.
Two wallets co-buying eight of the same tokens are correlated and may be
entirely honest; seventy wallets credited the same amount by one sender in
one transaction are correlated precisely because they are not. Collapsing
those axes into one number destroys the difference between them, which is
the difference a signal has to carry to be worth acting on.

Ported from fomopulse apps/server/src/db/discover.ts, which does this in
SQL over a live tape. The thresholds are its thresholds, kept named and
kept together because each one is a judgement about a market rather than a
tuning knob: MAX_SPRAY is "handouts per real fill past which the token is
pushing itself", HONEYPOT_BUYS is "buys before no sell at all is a fact
about the token rather than about its age". The reasoning is in the names.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from . import registry

registry.register_domain(
    "market-observation",
    "Market-authenticity mining over a trade tape: spray/handout detection, "
    "wash-trade cancellation, honeypot shape and thin-liquidity refusal. "
    "Answers 'is this movement real', which is orthogonal to wallet-intel's "
    "'which wallets move together' -- an honest cluster and a spray are both "
    "correlated, and only one of them is a signal.",
)

# ---------------------------------------------------------------- thresholds
#
# fomopulse's discover.ts, ported with the reasoning that sat beside each one.

MAX_POOL_AGE_S = 3 * 86_400
MIN_POOL_USD = 10_000
MAX_CHURN = 20
MAX_QUOTE_AGE_S = 3_600
HONEYPOT_BUYS = 10
MAX_SPRAY = 5
WASH_SECONDS = 300
WASH_TOLERANCE = 0.05

# A cluster this small is not a crowd, and a cluster this large is the tape
# rather than an event. Between them, independence is the question worth asking.
MIN_CLUSTER_WALLETS = 4


def _market_trades(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize tape-shaped traces into flat trade records.

    Accepts what the tape adapter emits: kind='observation', an action of
    wallet_buy/wallet_sell, and a payload carrying the fields a fill has.
    `dust` is the tape's own verdict (0 trade, 1 dusted, 2 handout) and is
    carried through rather than recomputed -- the adapter has the receipt
    and this miner does not, so re-deciding it here would be guessing at
    evidence that already exists upstream.
    """
    out: List[Dict[str, Any]] = []
    for t in traces:
        if t.get("kind") != "observation":
            continue
        act = t.get("action", "")
        if act not in ("wallet_buy", "wallet_sell"):
            continue
        p = t.get("payload") or {}
        out.append({
            "wallet": t.get("target") or p.get("wallet") or "",
            "side": "buy" if act == "wallet_buy" else "sell",
            "token": p.get("token") or "",
            "symbol": (p.get("symbol") or "?")[:12],
            "amount": float(p.get("amount") or 0),
            "amount_usd": float(p.get("amount_usd") or 0),
            "ts": float(p.get("ts") or 0),
            "dust": int(p.get("dust") or 0),
            # Who paid the gas of the wallet that received the tokens, when the
            # adapter knows. One funder behind N recipients is the spray tell
            # that a per-wallet count cannot see.
            "funder": p.get("funder") or p.get("funded_by") or "",
            "liquidity_usd": p.get("liquidity_usd"),
            "pair_created_at": p.get("pair_created_at"),
            "quote_age_s": p.get("quote_age_s"),
            "buys24": p.get("buys24"),
            "sells24": p.get("sells24"),
            "volume24": p.get("volume24"),
        })
    return out


def _independence(buys: List[Dict[str, Any]]) -> Dict[str, Any]:
    """How much of a cluster's width survives contact with its causes.

    Returns the three numbers separately, never a single blended score: raw
    width, independence, and the product. A caller that only wants to know
    "was this 7 wallets or one sender with 7 wallets" reads `independence`;
    one that wants the honest strength of the signal reads `effective`.
    """
    wallets = [b["wallet"] for b in buys if b["wallet"]]
    n = len(wallets)
    if n == 0:
        return {"wallets": 0, "independence": 0.0, "effective": 0.0,
                "shared_funder": None, "same_amount": 0}

    # Causal, not statistical: wallets sharing a funder are one decision.
    by_funder: Dict[str, List[str]] = defaultdict(list)
    for b in buys:
        if b["wallet"] and b["funder"]:
            by_funder[b["funder"]].append(b["wallet"])
    shared_funder = None
    if by_funder:
        funder, recipients = max(by_funder.items(), key=lambda kv: len(set(kv[1])))
        if len(set(recipients)) > 1:
            shared_funder = {"funder": funder, "wallets": sorted(set(recipients))}

    # Distinct wallets over the largest single-causer group behind them: seven
    # wallets from one sender is one actor wearing seven addresses, and reads
    # as one. With no shared funder the group is one wallet, so this is 1.0.
    largest_group = len(set(shared_funder["wallets"])) if shared_funder else 1
    independence = round(1.0 / max(largest_group, 1), 3)

    # Identical size to the cent across many wallets is a distribution, not a
    # market: independent buyers do not agree on an amount.
    amounts = [round(b["amount"], 6) for b in buys if b["amount"]]
    same_amount = max((amounts.count(a) for a in set(amounts)), default=0)
    if len(amounts) >= MIN_CLUSTER_WALLETS and same_amount == len(amounts):
        independence = round(independence * 0.4, 3)

    return {
        "wallets": len(set(wallets)),
        "independence": independence,
        "effective": round(len(set(wallets)) * independence, 2),
        "shared_funder": shared_funder,
        "same_amount": same_amount,
    }


def market_authenticity(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Is a crowd real? One finding per token whose buyers do not survive audit."""
    trades = _market_trades(traces)
    buys = [t for t in trades if t["side"] == "buy" and t["token"] and t["wallet"]]
    if not buys:
        return []

    by_token: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for b in buys:
        by_token[b["token"]].append(b)

    findings: List[Dict[str, Any]] = []
    for token, ts in by_token.items():
        symbol = ts[0]["symbol"]
        real = [t for t in ts if t["dust"] == 0]
        dusted = [t for t in ts if t["dust"] == 1]
        handouts = [t for t in ts if t["dust"] == 2]

        # ── spray: handouts outnumbering real fills. The token is buying its
        # way onto the tape, and a ranked-by-who-bought page is what it games.
        if real and len(dusted) + len(handouts) > len(real) * MAX_SPRAY:
            ind = _independence(ts)
            confidence = min(0.95, 0.6 + 0.03 * (len(dusted) + len(handouts)))
            findings.append({
                "miner": "market_authenticity",
                "confidence": round(confidence, 3),
                "title": (f"Spray: {symbol} shows {len(dusted) + len(handouts)} handout fills "
                          f"against {len(real)} real"),
                "evidence": (f"{token}: {len(dusted)} dusted + {len(handouts)} handout vs "
                             f"{len(real)} real fills (limit {MAX_SPRAY}x real). "
                             f"{ind['wallets']} distinct wallets, independence "
                             f"{ind['independence']}"
                             + (f", {len(ind['shared_funder']['wallets'])} funded by "
                                f"{ind['shared_funder']['funder'][:10]}.."
                                if ind["shared_funder"] else "")),
                "suggestion": "alert",
                "payload": {"token": token, "symbol": symbol, "kind": "spray",
                            "real": len(real), "dusted": len(dusted),
                            "handout": len(handouts), "max_spray": MAX_SPRAY,
                            **ind},
            })

        # ── wash: one wallet in and back out, same size, minutes apart. The
        # tape carries the volume either way -- this names it, never hides it.
        # Read from every trade in the token, not from `ts` (the buys): the sell
        # half of a round trip is by definition not in the buy list, and looking
        # for it there is why this check found nothing the first time it ran.
        token_trades = [t for t in trades if t["token"] == token]
        by_wallet: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for t in token_trades:
            if t["dust"] == 0 and t["wallet"]:
                by_wallet[t["wallet"]].append(t)
        wash = 0
        for w, ws in by_wallet.items():
            ws.sort(key=lambda x: x["ts"])
            for i, a in enumerate(ws):
                if a["side"] != "buy" or a["amount"] <= 0:
                    continue
                for b in ws[i + 1:]:
                    if b["ts"] - a["ts"] > WASH_SECONDS:
                        break
                    if (b["side"] == "sell"
                            and abs(b["amount"] - a["amount"]) <= a["amount"] * WASH_TOLERANCE):
                        wash += 1
                        break
        if wash:
            findings.append({
                "miner": "market_authenticity",
                "confidence": 0.75,
                "title": f"Wash: {symbol} shows {wash} round trips inside {WASH_SECONDS}s",
                "evidence": (f"{token}: {wash} wallet(s) bought and sold the same size within "
                             f"{WASH_SECONDS}s at ${ts[0]['amount_usd']:,.0f}-scale "
                             f"(tolerance {WASH_TOLERANCE:.0%}). Volume is carried either way; "
                             f"this counts it."),
                "suggestion": "alert",
                "payload": {"token": token, "symbol": symbol, "kind": "wash",
                            "round_trips": wash, "window_s": WASH_SECONDS,
                            "tolerance": WASH_TOLERANCE},
            })

        # ── honeypot shape: many buys reported, no exit at all. The buy works
        # for everyone and the sell works for nobody.
        sells = [t for t in trades if t["side"] == "sell" and t["token"] == token]
        buys24 = ts[0]["buys24"]
        sells24 = ts[0]["sells24"]
        if (not sells and isinstance(buys24, int) and buys24 >= HONEYPOT_BUYS
                and (sells24 is None or sells24 == 0)):
            findings.append({
                "miner": "market_authenticity",
                "confidence": 0.9,
                "title": f"Honeypot shape: {symbol} took {buys24} buys, no exit",
                "evidence": (f"{token}: feed reports {buys24} buys and "
                             f"{'no sells' if sells24 is None else f'{sells24} sells'}; "
                             f"this tape saw {len(real)} buys and 0 sells "
                             f"(threshold {HONEYPOT_BUYS})."),
                "suggestion": "alert",
                "payload": {"token": token, "symbol": symbol, "kind": "honeypot",
                            "buys24": buys24, "sells24": sells24,
                            "honeypot_buys": HONEYPOT_BUYS},
            })

        # ── thin liquidity: not a market. Refuses to let a real buy in a pool
        # too shallow to price be read as a signal.
        liq = ts[0]["liquidity_usd"]
        if isinstance(liq, (int, float)) and 0 < liq < MIN_POOL_USD:
            findings.append({
                "miner": "market_authenticity",
                "confidence": 0.65,
                "title": f"Thin pool: {symbol} at ${liq:,.0f} liquidity",
                "evidence": (f"{token}: pool ${liq:,.0f} is under the ${MIN_POOL_USD:,} floor, "
                             f"so {len(real)} buy(s) in it are not a market. Measured over the "
                             f"tape's first days, of young pools under the floor the median "
                             f"token sat at a ninth of the market cap the first wallet paid."),
                "suggestion": "alert",
                "payload": {"token": token, "symbol": symbol, "kind": "thin_liquidity",
                            "liquidity_usd": liq, "min_pool_usd": MIN_POOL_USD},
            })

        # ── stale quote: the feed stopped answering, it did not answer well.
        age = ts[0]["quote_age_s"]
        if isinstance(age, (int, float)) and age > MAX_QUOTE_AGE_S:
            findings.append({
                "miner": "market_authenticity",
                "confidence": 0.6,
                "title": f"Stale quote: {symbol} card {age / 3600:.1f}h old",
                "evidence": (f"{token}: the feed's card is {int(age)}s old against a "
                             f"{MAX_QUOTE_AGE_S}s ceiling. A pool that rugs stops being "
                             f"answered for rather than answered badly -- the card keeps the "
                             f"depth it had the hour it emptied, and that reads as a discovery."),
                "suggestion": "alert",
                "payload": {"token": token, "symbol": symbol, "kind": "stale_quote",
                            "quote_age_s": age, "max_quote_age_s": MAX_QUOTE_AGE_S},
            })

        # ── churn: a pool turning over its own depth is the shape wash leaves.
        liquidity = ts[0]["liquidity_usd"]
        volume = ts[0].get("volume24")
        if (isinstance(volume, (int, float)) and isinstance(liquidity, (int, float))
                and liquidity > 0 and volume > liquidity * MAX_CHURN):
            findings.append({
                "miner": "market_authenticity",
                "confidence": 0.7,
                "title": f"Churn: {symbol} turned over {volume / liquidity:.0f}x its depth",
                "evidence": (f"{token}: 24h volume ${volume:,.0f} over ${liquidity:,.0f} depth "
                             f"= {volume / liquidity:.1f}x, past the {MAX_CHURN}x ceiling. "
                             f"A pool turning over its own depth twenty times in a day is the "
                             f"shape wash trading leaves behind."),
                "suggestion": "alert",
                "payload": {"token": token, "symbol": symbol, "kind": "churn",
                            "volume24": volume, "liquidity_usd": liquidity,
                            "churn": round(volume / liquidity, 2), "max_churn": MAX_CHURN},
            })

        # ── independence, on its own: a crowd wide enough to matter whose width
        # does not survive its own funding. This is the finding that makes the
        # rest of the ecosystem's signals safe to weight by wallet count.
        ind = _independence(ts)
        if ind["wallets"] >= MIN_CLUSTER_WALLETS and ind["independence"] < 0.5:
            findings.append({
                "miner": "market_authenticity",
                "confidence": round(min(0.9, 0.55 + (0.5 - ind["independence"])), 3),
                "title": (f"Low independence: {symbol} reads {ind['wallets']} wallets, "
                          f"worth {ind['effective']}"),
                "evidence": (f"{token}: {ind['wallets']} wallets bought, independence "
                             f"{ind['independence']} (effective {ind['effective']}). "
                             + (f"{len(ind['shared_funder']['wallets'])} share funder "
                                f"{ind['shared_funder']['funder']}; "
                                if ind["shared_funder"] else "")
                             + (f"{ind['same_amount']} at identical size; "
                                if ind["same_amount"] > 1 else "")
                             + "raw wallet count overstates this signal."),
                "suggestion": "alert",
                "payload": {"token": token, "symbol": symbol, "kind": "low_independence",
                            **ind},
            })

    findings.sort(key=lambda f: f["confidence"], reverse=True)
    return findings[:10]


# No alert_condition builder registered: these payloads are per-token verdicts
# with six different shapes, none of them an error_rate condition. apply.py's
# fallback surfaces the real payload verbatim, which is honest -- unlike
# fabricating a metric shape these findings do not have.
registry.register_miner("market-observation", "market_authenticity", market_authenticity)
