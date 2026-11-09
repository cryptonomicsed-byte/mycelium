"""P2-04: source_hit_rate mined from signal_outcome + signal_record traces.

Acceptance: one finding per (source, window); hit_rate is null below 30 scored
samples and min_samples_met is false; brier is the conviction-vs-outcome MSE;
buckets are the calibration table; heldout is the most recent 20%; scored-only
(hold/exit/alert and null-hit outcomes are ignored); a record with no source
or conviction is skipped.
"""
import itertools
import json

from mycelium.miners.source_hit_rate import source_hit_rate

_IDS = itertools.count()


def _sr(rid, source, conviction, direction="long"):
    return {"agent": "vantage_ledger", "kind": "observation", "action": "signal_record",
            "target": rid, "payload": {"id": rid, "source": source,
                                       "conviction": conviction, "direction": direction}}


def _so(rid, window, hit, scored=True):
    return {"agent": "vantage_ledger", "kind": "observation", "action": "signal_outcome",
            "target": rid, "payload": {"window": window, "hit": hit, "scored": scored}}


def _make(n, source="src", window="1h", conviction=0.9, hits=None):
    """hits: None -> all hit; else a predicate(i)->bool."""
    traces = []
    for i in range(n):
        rid = f"r{next(_IDS)}"
        hit = True if hits is None else hits(i)
        traces.append(_sr(rid, source, conviction))
        traces.append(_so(rid, window, hit))
    return traces


def test_hit_rate_null_below_min_samples():
    out = source_hit_rate(_make(10, hits=lambda i: i % 3 != 0))  # 6 hits / 10
    assert len(out) == 1
    p = out[0]["payload"]
    assert p["source"] == "src" and p["window"] == "1h"
    assert p["n"] == 10 and p["hits"] == 6
    assert p["hit_rate"] is None and p["min_samples_met"] is False


def test_hit_rate_computed_at_and_above_min_samples():
    out = source_hit_rate(_make(30, hits=lambda i: i % 2 == 0))  # 15 hits / 30
    p = out[0]["payload"]
    assert p["n"] == 30 and p["min_samples_met"] is True
    assert p["hit_rate"] is not None and abs(p["hit_rate"] - 0.5) < 1e-9


def test_brier_is_conviction_mse():
    # all 10 hit at conviction 0.9 -> brier = mean((0.9-1)^2) = 0.01
    out = source_hit_rate(_make(10, conviction=0.9))  # all hit
    assert abs(out[0]["payload"]["brier"] - 0.01) < 1e-9


def test_buckets_and_heldout_present():
    out = source_hit_rate(_make(30))
    p = out[0]["payload"]
    assert isinstance(p["buckets"], list) and p["buckets"]
    assert p["buckets"][0]["n"] == 30  # single conviction bucket
    assert p["heldout"]["n"] == max(1, int(30 * 0.2))
    assert p["heldout"]["hit_rate"] is not None
    assert isinstance(p["updated_at"], int)


def test_unscored_outcomes_are_ignored():
    traces = [_sr("r1", "src", 0.8), _so("r1", "1h", None, scored=False)]
    assert source_hit_rate(traces) == []


def test_record_without_source_or_conviction_is_skipped():
    traces = [{"agent": "vantage_ledger", "kind": "observation", "action": "signal_record",
               "target": "r1", "payload": {"id": "r1", "direction": "long"}},
              _so("r1", "1h", True)]
    assert source_hit_rate(traces) == []


def test_groups_by_source_and_window():
    traces = _make(10, source="A", window="1h") + _make(10, source="B", window="1h") + \
             _make(10, source="A", window="6h")
    out = source_hit_rate(traces)
    keys = sorted((f["payload"]["source"], f["payload"]["window"]) for f in out)
    assert keys == [("A", "1h"), ("A", "6h"), ("B", "1h")]


def test_payload_str_is_parsed():
    def str_payload(t):
        t["payload"] = json.dumps(t["payload"])
        return t
    traces = [str_payload(_sr("r1", "src", 0.8)), str_payload(_so("r1", "1h", True))]
    out = source_hit_rate(traces)
    assert len(out) == 1 and out[0]["payload"]["source"] == "src"
