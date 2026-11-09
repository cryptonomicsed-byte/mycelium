"""P3-02: echoes of one call count once in n_eff; distinct calls each count."""
import json

from mycelium.miners.source_hit_rate import source_hit_rate


def _rec(rid, addr, ts, direction="long", source="s"):
    return {"agent": "vantage_ledger", "action": "signal_record", "target": rid,
            "payload": json.dumps({"id": rid, "source": source, "conviction": 0.6,
                                   "direction": direction, "address": addr, "created_at": ts})}


def _out(rid, hit):
    return {"agent": "vantage_ledger", "action": "signal_outcome", "target": rid,
            "payload": json.dumps({"window": "1h", "hit": hit, "scored": True})}


def _finding(traces):
    (f,) = source_hit_rate(traces)
    return f["payload"]


def test_ten_echoes_are_one_call():
    t = []
    for i in range(10):
        t += [_rec(f"r{i}", "MINT1", 1_000_000 + i * 60), _out(f"r{i}", True)]
    p = _finding(t)
    assert p["n"] == 10 and p["hits"] == 10
    assert p["n_eff"] == 1 and p["hits_eff"] == 1.0


def test_distinct_tokens_hours_and_directions_each_count():
    t = [_rec("a", "M1", 3600 * 10), _out("a", True),
         _rec("b", "M2", 3600 * 10), _out("b", False),       # other token
         _rec("c", "M1", 3600 * 12), _out("c", True),        # other hour
         _rec("d", "M1", 3600 * 10, "short"), _out("d", False)]  # other direction
    p = _finding(t)
    assert p["n_eff"] == 4 and p["hits_eff"] == 2.0


def test_mixed_cluster_counts_its_mean():
    t = [_rec("a", "M1", 7200), _out("a", True), _rec("b", "M1", 7300), _out("b", False)]
    p = _finding(t)
    assert p["n_eff"] == 1 and p["hits_eff"] == 0.5 and p["n"] == 2
