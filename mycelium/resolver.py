"""resolve_predictions — the ONE shared job that grades every open
prediction whose resolution_date has passed, against real substrate trace
data. Every forecasting layer (cross_domain_signal.py today; TimesFM
numeric forecasting and the forecasting-tools qualitative layer next) logs
its forward-looking claims via core.add_prediction() and relies on THIS
module to grade them -- not something each layer reimplements. Calibration
systems read resolved outcomes (core.query_predictions(status='resolved'))
rather than re-deriving them.

Generalizes the informal 24h self-referential check that used to live
inline in mycelium/miners/cross_domain_signal.py's own _resolve_pending()
(mutating finding.payload directly, one-off, source-specific) into a real,
reusable, source-agnostic job keyed off the new predictions table.

Resolution methods (dispatched via resolution_criterion['method'], stored
as structured JSON, not free text -- see core.add_prediction()'s
docstring):

  continued_interest -- the only method implemented today. A prediction
    resolves 'correct' if the same token/symbol shows up in ANY real trace
    (any agent, any domain) timestamped after resolution_date; 'incorrect'
    if it went quiet. This is the real, substrate-only ground-truth proxy
    cross_domain_signal.py already used informally -- "did this compound
    signal keep attracting real independent interest, or was it a one-off
    coincidence" -- now shared infrastructure instead of one miner's
    private logic.

  volume_range -- added 2026-08-31 for daemons/timesfm_forecast_bridge.py's
    real zero-shot wallet-activity-volume forecasts. A prediction resolves
    'correct' if the real observed wallet_buy+wallet_sell trace COUNT in
    the specific future bucket the forecast targets (criterion's
    target_bucket_start/bucket_minutes) falls within a tolerance band
    around the forecasted point value (criterion's point_forecast/
    tolerance_pct, with a small absolute floor so a near-zero forecast
    isn't held to an unrealistically tight band); 'incorrect' otherwise.
    Computed entirely from real substrate trace counts, same "no external
    price oracle, only what Mycelium already observed" discipline
    continued_interest already uses.

  Unrecognized methods resolve to 'unknown' rather than being silently
  skipped or guessed at -- an honest third state for whatever
  forecasting-tools' qualitative-outcome method will need next, which
  this module doesn't implement yet and won't fake.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from . import core


def _parse_ts(ts: str) -> Optional[datetime]:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _resolve_continued_interest(criterion: Dict[str, Any], prediction: Dict[str, Any], traces: List[Dict[str, Any]]) -> str:
    key_field = criterion.get("key_field") or "symbol"
    key_value = criterion.get("key_value") or prediction.get("symbol")
    if not key_value:
        return "unknown"
    resolution_date = _parse_ts(prediction.get("resolution_date") or "")
    if resolution_date is None:
        return "unknown"

    for t in traces:
        ts = _parse_ts(t.get("ts") or "")
        if ts is None or ts <= resolution_date:
            continue
        p = t.get("payload") or {}
        if key_field == "token" and (p.get("token") == key_value or t.get("target") == key_value):
            return "correct"
        if key_field == "symbol" and (p.get("symbol") or "").upper() == str(key_value).upper():
            return "correct"
    return "incorrect"


def _resolve_volume_range(criterion: Dict[str, Any], prediction: Dict[str, Any], traces: List[Dict[str, Any]]) -> str:
    target_start = _parse_ts(criterion.get("target_bucket_start") or "")
    bucket_minutes = criterion.get("bucket_minutes")
    point_forecast = criterion.get("point_forecast")
    tolerance_pct = criterion.get("tolerance_pct")
    if target_start is None or bucket_minutes is None or point_forecast is None or tolerance_pct is None:
        return "unknown"
    try:
        bucket_minutes = float(bucket_minutes)
        point_forecast = float(point_forecast)
        tolerance_pct = float(tolerance_pct)
    except (TypeError, ValueError):
        return "unknown"
    target_end = target_start + timedelta(minutes=bucket_minutes)

    actual = 0
    for t in traces:
        if t.get("agent") != "wallet_intel" or t.get("action") not in ("wallet_buy", "wallet_sell"):
            continue
        ts = _parse_ts(t.get("ts") or "")
        if ts is None or not (target_start <= ts < target_end):
            continue
        actual += 1

    # Absolute floor so a near-zero forecast isn't held to an unrealistically
    # tight band (tolerance_pct of 0 real events is 0 real events) -- 2 is a
    # real, deliberately small floor, not a fabricated leniency.
    tolerance = max(point_forecast * tolerance_pct, 2.0)
    return "correct" if abs(actual - point_forecast) <= tolerance else "incorrect"


_METHODS = {
    "continued_interest": _resolve_continued_interest,
    "volume_range": _resolve_volume_range,
}


def evaluate_criterion(prediction: Dict[str, Any], traces: List[Dict[str, Any]]) -> str:
    """Real outcome ('correct'/'incorrect'/'unknown') for one prediction,
    dispatched on its own resolution_criterion['method']. Never raises --
    a malformed or unrecognized criterion resolves 'unknown', not a crash
    that would take down the whole resolution cycle for every other
    prediction."""
    criterion = prediction.get("resolution_criterion")
    if isinstance(criterion, str):
        import json
        try:
            criterion = json.loads(criterion)
        except (ValueError, TypeError):
            return "unknown"
    if not isinstance(criterion, dict):
        return "unknown"
    method = criterion.get("method")
    fn = _METHODS.get(method)
    if fn is None:
        return "unknown"
    try:
        return fn(criterion, prediction, traces)
    except Exception:
        return "unknown"


def resolve_predictions(traces: Optional[List[Dict[str, Any]]] = None, lookback_days: float = 14.0) -> Dict[str, int]:
    """Grade every real prediction whose resolution_date has passed.
    `traces` defaults to a real bounded substrate window (lookback_days,
    wide enough to cover any prediction's resolution_date plus the trace
    activity needed to grade it) -- callers already holding a trace list
    (e.g. a miner's own run_miner window) can pass it through instead of
    re-querying. Returns real counts by outcome, e.g.
    {'resolved': 3, 'correct': 1, 'incorrect': 2, 'unknown': 0} -- never
    fabricates a result for a prediction that isn't actually due yet."""
    if traces is None:
        traces = core.iter_rows(core.query_traces(since=core.recent_since(days=lookback_days), limit=200000))

    due = core.iter_rows(core.due_predictions())
    counts = {"resolved": 0, "correct": 0, "incorrect": 0, "unknown": 0}
    for prediction in due:
        outcome = evaluate_criterion(prediction, traces)
        if core.resolve_prediction(prediction["id"], outcome):
            counts["resolved"] += 1
            counts[outcome] = counts.get(outcome, 0) + 1
    return counts
