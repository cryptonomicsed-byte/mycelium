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

Calibration upgrade (2026-08-30): the rate->multiplier scheme above is
crude -- it scales a heuristic score by a clamped ratio rather than
producing a genuine calibrated probability, and the multiplier's own
denominator (a flat "50% is neutral") is asserted, not fit. Once enough
REAL resolved history exists per pattern, this file now fits a real
scikit-learn CalibratedClassifierCV(method="sigmoid") (Platt scaling)
over that pattern's actual (base_heuristic_score, resolved_correct)
pairs, and uses ITS calibrated probability as the finding's confidence
directly -- not multiplied into the old heuristic, which would be a
double-correction.

Sigmoid (Platt) was chosen over isotonic deliberately: isotonic is a
non-parametric, unconstrained monotonic fit that needs substantially more
data to avoid overfitting to noise, while sigmoid fits only 2 parameters
(a logistic curve) and is the standard recommendation for the small-N
regime this pattern will realistically sit in for a long time -- the
self-referential resolver only scores a finding 24h after it's created,
and cross_domain_calibration is CONFIRMED EMPTY as of this writing (2026-
08-30, checked live on hostinger-vps: zero rows, 8 pending unresolved
findings from this miner's very first mining passes). Real minimums
enforced before ANY sklearn fit is attempted (MIN_SKLEARN_SAMPLES=20
total, MIN_SAMPLES_PER_CLASS=5 per outcome class -- CalibratedClassifierCV's
default 5-fold stratified CV needs at least 5 of the minority class or it
raises outright, confirmed by triggering that exact failure live before
picking this threshold) -- fail SOFT below that, falling back to the
original rate->multiplier scheme exactly as before. This is not
theoretical: given the real current data volume, the fallback path is
what actually runs today and will keep running for a while. Every
finding's payload records which method actually produced its confidence
(calibration_method: "sklearn-sigmoid" | "simple-rate"), so this is
auditable, not a silent switch.

scikit-learn has no built-in "calibrate this raw scalar score I already
computed" entry point -- CalibratedClassifierCV wraps a CLASSIFIER's
decision_function/predict_proba output. The real, sklearn-documented
pattern for calibrating an already-computed score (see their own
"Calibrating a non-probabilistic classifier" guidance) is to wrap it in a
trivial passthrough estimator via sklearn.frozen.FrozenEstimator, which
this file does (_PassthroughScoreEstimator below) -- confirmed working
end-to-end against the real installed sklearn 1.8.0 on hostinger-vps
before being written into this file (mixin order and an explicit
__sklearn_tags__ override turned out to be required in this version;
verified by testing, not assumed from documentation alone).

Real deployment gotcha found + fixed while wiring this in: sandbox.py
runs every miner in its own subprocess under a real memory cap
(RLIMIT_DATA, default 256MB -- see MINER_MEM_MB), and mycelium/miners/
__init__.py imports EVERY registered domain module (this one included) at
package-import time, in EVERY one of those subprocesses, regardless of
which single miner that subprocess is actually running. A module-level
`import numpy, sklearn` here was measured live to blow that cap for ALL
12 miners at once (sandbox_errors: 12/12, confirmed by testing the
original file against the same real DB copy afterward and getting
sandbox_errors: 0 -- isolating the cause to this file's own import, not a
pre-existing issue). Fixed by moving the numpy/sklearn imports (and the
_PassthroughScoreEstimator class definition, which needs sklearn's base
classes) inside _lazy_import_sklearn(), called only from
_fit_sklearn_calibrator() AFTER the cheap sample-count guard already
passed. Given the real current data volume (calibration table empty),
that guard fails first every time, so sklearn is never even imported
under real current conditions -- zero behavior change to the live system
until real data volume actually justifies paying that import cost.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import registry
from .. import core

logger = logging.getLogger(__name__)

# scikit-learn is a real, already-installed dependency in this venv
# (confirmed live: /opt/ares/venv has 1.8.0), but it is deliberately NOT
# imported at module level -- see the module docstring's "Real deployment
# gotcha" section: every sandboxed miner subprocess imports this whole
# module regardless of which miner it's actually running, and numpy+
# sklearn's import footprint alone blows the real 256MB per-miner memory
# cap for ALL of them, not just this one. _lazy_import_sklearn() is called
# only from _fit_sklearn_calibrator(), only after the cheap sample-count
# guard already passed -- under the real current (thin) data volume, this
# import never fires at all.
_SKLEARN_IMPORT_CACHE: Optional[tuple] = None
_SKLEARN_IMPORT_FAILED = False


def _lazy_import_sklearn():
    """Returns (np, _PassthroughScoreEstimator, CalibratedClassifierCV,
    FrozenEstimator) on success, or None if sklearn/numpy aren't
    importable in this environment -- callers fall back to the simple
    rate-based method exactly as if there weren't enough real data yet.
    Cached after the first real attempt so a real cycle only pays the
    import cost once per process, not once per candidate finding."""
    global _SKLEARN_IMPORT_CACHE, _SKLEARN_IMPORT_FAILED
    if _SKLEARN_IMPORT_CACHE is not None:
        return _SKLEARN_IMPORT_CACHE
    if _SKLEARN_IMPORT_FAILED:
        return None
    try:
        import numpy as np
        from sklearn.base import BaseEstimator, ClassifierMixin
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.frozen import FrozenEstimator
    except ImportError as e:
        logger.warning("cross_domain_signal: sklearn/numpy not importable (%s) -- using simple-rate calibration only", e)
        _SKLEARN_IMPORT_FAILED = True
        return None

    class _PassthroughScoreEstimator(ClassifierMixin, BaseEstimator):
        """A "classifier" that isn't one -- it wraps an already-computed
        raw heuristic score (the miner's own base confidence) as if it
        were a real classifier's decision_function output, purely so
        CalibratedClassifierCV has something to calibrate. This is the
        real, sklearn-documented pattern for calibrating a precomputed
        score (their "Calibrating a non-probabilistic classifier" guide),
        not an invented workaround. fit() is a no-op beyond recording the
        two real classes -- there is nothing to learn here, the score is
        already given; FrozenEstimator (see _fit_sklearn_calibrator)
        prevents CalibratedClassifierCV from ever trying to re-fit it.

        Mixin order (ClassifierMixin before BaseEstimator) and the
        explicit __sklearn_tags__ override are both required for sklearn
        1.8.0 to recognize this as a real classifier -- confirmed by
        testing against the actual installed version, not assumed from
        older-version documentation."""

        def fit(self, X, y):
            self.classes_ = np.array([0, 1])
            return self

        def decision_function(self, X):
            return np.asarray(X).ravel()

        def predict(self, X):
            return (np.asarray(X).ravel() >= 0.5).astype(int)

        def __sklearn_tags__(self):
            tags = super().__sklearn_tags__()
            tags.estimator_type = "classifier"
            return tags

    _SKLEARN_IMPORT_CACHE = (np, _PassthroughScoreEstimator, CalibratedClassifierCV, FrozenEstimator)
    return _SKLEARN_IMPORT_CACHE

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

# Real sklearn-fit thresholds -- see module docstring's "Calibration
# upgrade" section for why these exact numbers (not arbitrary): 20 total
# is a conventional floor for a stable 2-parameter Platt/sigmoid fit;
# 5-per-class is what CalibratedClassifierCV's default 5-fold stratified
# CV requires to not raise outright (confirmed by triggering that real
# failure before picking this number). Below either, fall back.
MIN_SKLEARN_SAMPLES = 20
MIN_SAMPLES_PER_CLASS = 5

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


def _ensure_calibration_samples_table(conn: sqlite3.Connection) -> None:
    """Per-resolution (not aggregate) history: one real row per resolved
    finding, keeping the raw pre-calibration heuristic score alongside its
    real correct/incorrect outcome. The aggregate cross_domain_calibration
    table above can't feed a real sklearn fit -- a fit needs the actual
    (score, outcome) pairs, not just a correct/total ratio. This table is
    purely additive: the old table and its fallback logic are untouched."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS cross_domain_calibration_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_key TEXT NOT NULL,
            base_confidence REAL NOT NULL,
            correct INTEGER NOT NULL,
            resolved_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_cdcs_pattern ON cross_domain_calibration_samples(pattern_key)"
    )


def _fit_sklearn_calibrator(conn: sqlite3.Connection, pattern_key: str) -> Optional[Callable[[float], float]]:
    """Real CalibratedClassifierCV(method="sigmoid") fit over this
    pattern's actual resolved (base_confidence, correct) history. Returns
    None -- never a fabricated curve -- whenever there isn't real enough
    data yet (too few total samples, one outcome class too thin for a
    stable fit, or sklearn genuinely unavailable), or if the real fit
    itself raises for any reason. Callers fall back to the simple
    rate-based method on None, exactly as the rest of this file already
    does for thin data.

    The cheap sample-count check runs BEFORE the lazy sklearn import (see
    module docstring's "Real deployment gotcha") -- under real current
    data volume this returns None immediately without ever importing
    numpy/sklearn at all."""
    rows = conn.execute(
        "SELECT base_confidence, correct FROM cross_domain_calibration_samples WHERE pattern_key=?",
        (pattern_key,),
    ).fetchall()
    if len(rows) < MIN_SKLEARN_SAMPLES:
        return None

    n_pos_raw = sum(1 for r in rows if r[1])
    n_neg_raw = len(rows) - n_pos_raw
    if n_pos_raw < MIN_SAMPLES_PER_CLASS or n_neg_raw < MIN_SAMPLES_PER_CLASS:
        return None  # a sigmoid fit on a near-single-class sample is unstable/meaningless

    imported = _lazy_import_sklearn()
    if imported is None:
        return None
    np, _PassthroughScoreEstimator, CalibratedClassifierCV, FrozenEstimator = imported

    X = np.array([[r[0]] for r in rows])
    y = np.array([int(r[1]) for r in rows])

    try:
        base_est = _PassthroughScoreEstimator().fit(X, y)
        calibrated = CalibratedClassifierCV(estimator=FrozenEstimator(base_est), method="sigmoid")
        calibrated.fit(X, y)
    except Exception as e:
        logger.warning("cross_domain_signal: real sklearn calibration fit failed for %s (%d samples): %s -- falling back", pattern_key, len(rows), e)
        return None

    def calibrate(raw_score: float) -> float:
        return float(calibrated.predict_proba([[raw_score]])[0][1])

    return calibrate


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


def _confidence_for(conn: sqlite3.Connection, pattern_key: str, base_score: float) -> Tuple[float, str]:
    """The real confidence for one candidate finding, and which real
    method actually produced it (recorded in the finding's own payload,
    never silent). Tries a genuine sklearn Platt-scaling fit over this
    pattern's real resolved history first; on ANY reason that isn't
    available yet (see _fit_sklearn_calibrator), falls back to the
    original rate->multiplier scheme applied to the same base heuristic
    score, unchanged from before this upgrade."""
    calibrate = _fit_sklearn_calibrator(conn, pattern_key)
    if calibrate is not None:
        try:
            return max(0.05, min(0.95, calibrate(base_score))), "sklearn-sigmoid"
        except Exception as e:
            logger.warning("cross_domain_signal: sklearn predict_proba failed for %s: %s -- falling back", pattern_key, e)
    mult = _calibration_multiplier(conn, pattern_key)
    return max(0.05, min(0.95, base_score * mult)), "simple-rate"


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
        # Real per-sample record for the sklearn fit -- only when this
        # finding actually carries the raw pre-calibration score (added
        # alongside this upgrade). Older findings resolved before this
        # change simply don't contribute a sample row -- honest, not
        # backfilled/fabricated for history that was never recorded.
        base_conf = payload.get("base_confidence")
        if base_conf is not None:
            conn.execute(
                "INSERT INTO cross_domain_calibration_samples (pattern_key, base_confidence, correct, resolved_at) VALUES (?,?,?,?)",
                (pattern_key, float(base_conf), 1 if still_active else 0, now.strftime("%Y-%m-%dT%H:%M:%SZ")),
            )
        payload["resolved"] = True
        payload["resolved_correct"] = still_active
        conn.execute("UPDATE findings SET payload=? WHERE id=?", (json.dumps(payload, sort_keys=True), fid))
        resolved += 1
    return resolved


# ---------------------------------------------------------------- pair miners

def _wallet_influencer(
    wallet_events: List[Dict[str, Any]],
    influencer_events: List[Dict[str, Any]],
    conn: sqlite3.Connection,
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

        confidence, calibration_method = _confidence_for(conn, "wallet+influencer", base)
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
                "base_confidence": round(base, 4), "calibration_method": calibration_method,
            },
        })
    findings.sort(key=lambda f: -f["confidence"])
    return findings[:MAX_FINDINGS_PER_PAIR]


def _wallet_council(
    wallet_events: List[Dict[str, Any]],
    council_events: List[Dict[str, Any]],
    conn: sqlite3.Connection,
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

        confidence, calibration_method = _confidence_for(conn, "wallet+council", base)
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
                "base_confidence": round(base, 4), "calibration_method": calibration_method,
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
        _ensure_calibration_samples_table(conn)
        _resolve_pending(conn, traces)
        conn.commit()

        wallet_events = _wallet_events(traces)
        influencer_events = _influencer_events(traces)
        council_events = _council_events(traces)

        findings = []
        findings.extend(_wallet_influencer(wallet_events, influencer_events, conn))
        findings.extend(_wallet_council(wallet_events, council_events, conn))
        conn.commit()
        return findings
    finally:
        conn.close()


registry.register_miner("cross-domain-synthesis", "cross_domain_signal", cross_domain_signal)
