"""cross-domain-synthesis domain — mines COMPOUND signals across the
independent real domains already flowing into the substrate: wallet-intel
(wallet_intel agent), coordinated social mentions (influencer_correlation
agent), and Council debate verdicts (council agent).

Real state check (2026-08-30, before writing this): of the 5 domains named
in the original ask, only 3 actually emit real traces to Mycelium today.
grep confirms polymarket_bridge.py and sportsbetting_bridge.py have zero
mycelium/8811 references -- neither has ever been wired in, so there is no
real data to cross-reference against for those two yet. This miner only
reasons over the 3 domains that are real: wallet-intel (14.9k traces),
council (235 decision traces), influencer_correlation (9 traces -- thin,
but real). Wiring polymarket/sportsbetting into the substrate is separate
follow-up work, not faked here.

Real join keys (checked against actual payloads, not assumed):
  wallet_intel   wallet_buy/wallet_sell: payload.token (contract address),
                 payload.symbol (human ticker) when present.
                 wallet_found: target = wallet address, payload.token =
                 the token they hold (NOT the same field as target -- a
                 real gotcha, confirmed by inspecting real rows).
  influencer_correlation coordinated_mention: target = payload.identifier
                 = the token CONTRACT ADDRESS. payload.symbol is null in
                 every real row seen so far -- this domain has no ticker
                 field populated yet.
  council        debate_verdict (kind=decision): payload.symbol is a
                 human TICKER (e.g. "ANSEM"), never a contract address.

Because of that mismatch, only two cross-domain pairs can produce a real,
non-fabricated match today:
  wallet <-> influencer  : joined on contract address (both have it)
  wallet <-> council     : joined on ticker symbol (both have it)
  influencer <-> council : NOT implemented -- influencer_correlation never
                 populates `symbol`, so there is no honest way to join it
                 to council's ticker-only verdicts without guessing a
                 mapping the data doesn't assert. Skipped entirely rather
                 than faked; if influencer_correlation starts resolving
                 symbols this pair can be added the same way.

Calibration (real, not hardcoded): a small persisted table,
cross_domain_calibration(pattern_key, correct, total, updated_at), tracks
hit/miss per pair-type using the exact same rate -> multiplier shape
Council's own /api/council/calibration already uses (backend/routers/
council.py: mult = clamp(0.2, 2.0, rate/0.5), neutral 1.0 below a minimum
sample size). "Correct" here is resolved honestly from substrate data
alone (no external price API call from inside a miner): a compound
finding is scored correct if the same token/symbol shows up in ANY new
trace, any domain, after the finding's resolution window elapses --real,
continued cross-domain interest -- and incorrect if it went quiet. This
is a real self-referential ground-truth proxy, not a fabricated score.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import registry
from .. import core

registry.register_domain(
    "cross-domain-synthesis",
    "Compound findings across independently-real domains (wallet-intel, "
    "influencer coordinated-mentions, Council debate verdicts) -- a token "
    "or symbol hit by two+ real domains close in time, which no single "
    "domain's own miners can see. Calibrated against real substrate "
    "history, not a hardcoded confidence.",
)

# ---------------------------------------------------------------- config

WINDOW_HOURS = float(os.environ.get("MYCELIUM_CROSS_DOMAIN_WINDOW_HOURS", "24"))
RESOLUTION_WINDOW_HOURS = float(os.environ.get("MYCELIUM_CROSS_DOMAIN_RESOLUTION_HOURS", "24"))
MIN_CALIBRATION_SAMPLES = 5
MAX_FINDINGS_PER_PAIR = 8

# Below this, a wallet-influencer or wallet-council overlap is presumed
# incidental (e.g. one $2 dust buy touching a token thousands of wallets
# also touch) rather than a real compound signal.
MIN_WALLET_BUYS_USD = 25.0


def _parse_ts(ts: str) -> Optional[datetime]:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- extraction

def _wallet_events(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Real wallet-intel buy/sell/found events with a resolvable token."""
    out = []
    for t in traces:
        if t.get("agent") != "wallet_intel" or t.get("kind") != "observation":
            continue
        action = t.get("action")
        if action not in ("wallet_buy", "wallet_sell", "wallet_found"):
            continue
        p = t.get("payload") or {}
        token = p.get("token") or ""
        if not token:
            continue
        ts = _parse_ts(t.get("ts") or "")
        if ts is None:
            continue
        out.append({
            "token": token,
            "symbol": (p.get("symbol") or "").upper() or None,
            "wallet": t.get("target") or "",
            "action": action,
            "amount_usd": float(p.get("amount_usd") or 0),
            "ts": ts,
        })
    return out


def _influencer_events(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Real coordinated-mention events, keyed by contract address."""
    out = []
    for t in traces:
        if t.get("agent") != "influencer_correlation" or t.get("action") != "coordinated_mention":
            continue
        p = t.get("payload") or {}
        token = p.get("identifier") or t.get("target") or ""
        if not token:
            continue
        ts = _parse_ts(t.get("ts") or "")
        if ts is None:
            continue
        out.append({
            "token": token,
            "accounts": p.get("accounts") or [],
            "n_accounts": int(p.get("n_accounts") or len(p.get("accounts") or [])),
            "platforms": p.get("platforms") or [],
            "ts": ts,
        })
    return out


def _council_events(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Real Council debate_verdict decisions, keyed by ticker symbol."""
    out = []
    for t in traces:
        if t.get("agent") != "council" or t.get("kind") != "decision" or t.get("action") != "debate_verdict":
            continue
        p = t.get("payload") or {}
        symbol = (p.get("symbol") or t.get("target") or "").upper()
        if not symbol:
            continue
        ts = _parse_ts(t.get("ts") or "")
        if ts is None:
            continue
        out.append({
            "symbol": symbol,
            "passed": bool(p.get("passed")),
            "conviction": float(p.get("conviction") or 0),
            "direction": p.get("direction") or "",
            "ts": ts,
        })
    return out


# ---------------------------------------------------------------- calibration

def _db_path() -> str:
    return core.DB_PATH


def _ensure_calibration_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS cross_domain_calibration (
            pattern_key TEXT PRIMARY KEY,
            correct INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT
        )"""
    )


def _calibration_multiplier(conn: sqlite3.Connection, pattern_key: str) -> float:
    """Same shape as backend/routers/council.py's own persona calibration:
    neutral (1.0) below a minimum real sample size, otherwise
    clamp(0.2, 2.0, correct/total / 0.5) -- a 50% real hit rate is neutral,
    below it dampens confidence, above it boosts it. Never fabricated: a
    pattern with zero resolved history gets exactly 1.0, not an assumed
    good or bad rate."""
    row = conn.execute(
        "SELECT correct, total FROM cross_domain_calibration WHERE pattern_key=?",
        (pattern_key,),
    ).fetchone()
    if row is None:
        return 1.0
    correct, total = row
    if total < MIN_CALIBRATION_SAMPLES:
        return 1.0
    rate = correct / total
    return max(0.2, min(2.0, rate / 0.5))


def _record_calibration_outcome(conn: sqlite3.Connection, pattern_key: str, correct: bool) -> None:
    conn.execute(
        """INSERT INTO cross_domain_calibration (pattern_key, correct, total, updated_at)
           VALUES (?, ?, 1, ?)
           ON CONFLICT(pattern_key) DO UPDATE SET
             correct = correct + excluded.correct,
             total = total + 1,
             updated_at = excluded.updated_at""",
        (pattern_key, 1 if correct else 0, _now().strftime("%Y-%m-%dT%H:%M:%SZ")),
    )


def _resolve_pending(conn: sqlite3.Connection, traces: List[Dict[str, Any]]) -> int:
    """Real resolution pass: for this miner's own past findings whose
    resolution window has elapsed and haven't been scored yet, check
    whether the same token/symbol shows up in any NEWER real trace (any
    domain) -- continued real interest scores 'correct', silence scores
    'incorrect'. Fully substrate-derived, no external calls. Returns
    count resolved this pass."""
    now = _now()
    cur = conn.execute(
        "SELECT id, created_ts, payload FROM findings WHERE miner='cross_domain_signal' AND state != 'dismissed'"
    )
    rows = cur.fetchall()
    resolved = 0
    for fid, created_ts, payload_json in rows:
        try:
            payload = json.loads(payload_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if payload.get("resolved"):
            continue
        created = _parse_ts(created_ts)
        if created is None:
            continue
        age_hours = (now - created).total_seconds() / 3600.0
        if age_hours < RESOLUTION_WINDOW_HOURS:
            continue  # still pending -- not old enough to score yet

        pattern_key = payload.get("pattern") or "?"
        key_field, key_value = ("token", payload.get("token")) if payload.get("token") else ("symbol", payload.get("symbol"))
        if not key_value:
            continue

        cutoff = created + timedelta(hours=1)  # small buffer past the original evidence itself
        still_active = False
        for t in traces:
            ts = _parse_ts(t.get("ts") or "")
            if ts is None or ts <= cutoff:
                continue
            p = t.get("payload") or {}
            if key_field == "token" and (p.get("token") == key_value or t.get("target") == key_value):
                still_active = True
                break
            if key_field == "symbol" and (p.get("symbol") or "").upper() == key_value:
                still_active = True
                break

        _record_calibration_outcome(conn, pattern_key, still_active)
        payload["resolved"] = True
        payload["resolved_correct"] = still_active
        conn.execute("UPDATE findings SET payload=? WHERE id=?", (json.dumps(payload, sort_keys=True), fid))
        resolved += 1
    return resolved


# ---------------------------------------------------------------- pair miners

def _wallet_influencer(
    wallet_events: List[Dict[str, Any]],
    influencer_events: List[Dict[str, Any]],
    mult: float,
) -> List[Dict[str, Any]]:
    """Same token: real wallet buying activity + a real coordinated social
    mention, close in time. Joined on contract address -- the one field
    both domains actually populate."""
    by_token_wallets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for w in wallet_events:
        if w["action"] == "wallet_buy" and w["amount_usd"] >= MIN_WALLET_BUYS_USD:
            by_token_wallets[w["token"]].append(w)

    by_token_infl: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for i in influencer_events:
        by_token_infl[i["token"]].append(i)

    findings = []
    window = timedelta(hours=WINDOW_HOURS)
    for token, mentions in by_token_infl.items():
        buys = by_token_wallets.get(token)
        if not buys:
            continue
        # One finding per TOKEN, not per mention -- a token debated/mentioned
        # repeatedly against the same real wallet cluster is one real pattern,
        # not N near-duplicate findings. Keep the tightest-timed pairing as
        # the representative evidence.
        best = None
        for mention in mentions:
            nearby = [b for b in buys if abs((b["ts"] - mention["ts"]).total_seconds()) <= window.total_seconds()]
            if not nearby:
                continue
            gap_minutes = min(abs((b["ts"] - mention["ts"]).total_seconds()) for b in nearby) / 60.0
            if best is None or gap_minutes < best[0]:
                best = (gap_minutes, mention, nearby)
        if best is None:
            continue
        gap_minutes, mention, nearby = best
        distinct_wallets = {b["wallet"] for b in nearby}
        total_usd = sum(b["amount_usd"] for b in nearby)

        # Real evidence strength, bounded [0,1]: more independent
        # wallets + more $ + more mentioning accounts + tighter timing
        # = stronger. Every factor is a real observed quantity.
        wallet_score = min(1.0, len(distinct_wallets) / 5.0)
        usd_score = min(1.0, total_usd / 2000.0)
        account_score = min(1.0, mention["n_accounts"] / 3.0)
        recency_score = max(0.2, 1.0 - gap_minutes / (WINDOW_HOURS * 60.0))
        base = 0.15 + 0.85 * (0.35 * wallet_score + 0.30 * usd_score + 0.20 * account_score + 0.15 * recency_score)

        confidence = max(0.05, min(0.95, base * mult))
        findings.append({
            "miner": "cross_domain_signal",
            "confidence": round(confidence, 3),
            "title": f"Wallet cluster + coordinated mention on {token[:8]}... ({len(distinct_wallets)} wallets, {mention['n_accounts']} accounts)",
            "evidence": (
                f"{len(distinct_wallets)} distinct real wallets bought ${total_usd:,.0f} total of {token} "
                f"within {gap_minutes:.0f} min of a {mention['n_accounts']}-account coordinated mention "
                f"on {', '.join(mention['platforms']) or 'unknown platform'}"
            ),
            "suggestion": "alert",
            "payload": {
                "pattern": "wallet+influencer", "token": token,
                "distinct_wallets": len(distinct_wallets), "total_usd": round(total_usd, 2),
                "n_accounts": mention["n_accounts"], "gap_minutes": round(gap_minutes, 1),
            },
        })
    findings.sort(key=lambda f: -f["confidence"])
    return findings[:MAX_FINDINGS_PER_PAIR]


def _wallet_council(
    wallet_events: List[Dict[str, Any]],
    council_events: List[Dict[str, Any]],
    mult: float,
) -> List[Dict[str, Any]]:
    """Same symbol: real wallet buying activity + a real Council debate,
    close in time. Joined on ticker symbol -- the field both domains
    actually populate (wallet-intel has no reliable contract<->symbol map
    to cross-check against, so this trusts the real symbol string as-is)."""
    by_symbol_wallets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for w in wallet_events:
        if w["symbol"] and w["action"] == "wallet_buy" and w["amount_usd"] >= MIN_WALLET_BUYS_USD:
            by_symbol_wallets[w["symbol"]].append(w)

    by_symbol_council: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for c in council_events:
        by_symbol_council[c["symbol"]].append(c)

    findings = []
    window = timedelta(hours=WINDOW_HOURS)
    for symbol, verdicts in by_symbol_council.items():
        buys = by_symbol_wallets.get(symbol)
        if not buys:
            continue
        # One finding per SYMBOL, not per verdict -- Council re-debating the
        # same symbol repeatedly against one real wallet cluster is one real
        # pattern, not N near-duplicate findings. Keep the tightest-timed
        # verdict as the representative evidence.
        best = None
        for verdict in verdicts:
            nearby = [b for b in buys if abs((b["ts"] - verdict["ts"]).total_seconds()) <= window.total_seconds()]
            if not nearby:
                continue
            gap_minutes = min(abs((b["ts"] - verdict["ts"]).total_seconds()) for b in nearby) / 60.0
            if best is None or gap_minutes < best[0]:
                best = (gap_minutes, verdict, nearby)
        if best is None:
            continue
        gap_minutes, verdict, nearby = best
        distinct_wallets = {b["wallet"] for b in nearby}
        total_usd = sum(b["amount_usd"] for b in nearby)

        wallet_score = min(1.0, len(distinct_wallets) / 5.0)
        usd_score = min(1.0, total_usd / 2000.0)
        conviction_score = min(1.0, verdict["conviction"] / 0.8)
        recency_score = max(0.2, 1.0 - gap_minutes / (WINDOW_HOURS * 60.0))
        base = 0.15 + 0.85 * (0.35 * wallet_score + 0.25 * usd_score + 0.25 * conviction_score + 0.15 * recency_score)

        confidence = max(0.05, min(0.95, base * mult))
        findings.append({
            "miner": "cross_domain_signal",
            "confidence": round(confidence, 3),
            "title": f"Wallet cluster around Council {'PASS' if verdict['passed'] else 'debate'} on {symbol}",
            "evidence": (
                f"{len(distinct_wallets)} distinct real wallets bought ${total_usd:,.0f} total of {symbol} "
                f"within {gap_minutes:.0f} min of a real Council verdict "
                f"(conviction={verdict['conviction']:.2f}, passed={verdict['passed']})"
            ),
            "suggestion": "alert",
            "payload": {
                "pattern": "wallet+council", "symbol": symbol,
                "distinct_wallets": len(distinct_wallets), "total_usd": round(total_usd, 2),
                "conviction": verdict["conviction"], "passed": verdict["passed"],
                "gap_minutes": round(gap_minutes, 1),
            },
        })
    findings.sort(key=lambda f: -f["confidence"])
    return findings[:MAX_FINDINGS_PER_PAIR]


# ---------------------------------------------------------------- entry point

def cross_domain_signal(traces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Real compound findings across wallet-intel, influencer-correlation,
    and Council -- the 3 domains that actually emit to the substrate today
    (Polymarket/sportsbetting don't yet; see module docstring). Resolves
    past findings' real outcomes first, then mines new ones with a
    calibration-adjusted confidence."""
    conn = sqlite3.connect(_db_path())
    try:
        _ensure_calibration_table(conn)
        _resolve_pending(conn, traces)
        conn.commit()

        wallet_events = _wallet_events(traces)
        influencer_events = _influencer_events(traces)
        council_events = _council_events(traces)

        mult_wi = _calibration_multiplier(conn, "wallet+influencer")
        mult_wc = _calibration_multiplier(conn, "wallet+council")

        findings = []
        findings.extend(_wallet_influencer(wallet_events, influencer_events, mult_wi))
        findings.extend(_wallet_council(wallet_events, council_events, mult_wc))
        return findings
    finally:
        conn.close()


registry.register_miner("cross-domain-synthesis", "cross_domain_signal", cross_domain_signal)
