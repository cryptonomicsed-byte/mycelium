"""P2-03: the resolver marks signal_record traces at +1h/+6h/+24h.

Acceptance: long/short are scored (hit from signed return vs threshold),
hold/exit/alert are logged but not scored, late marks are flagged, an
unavailable price yields a null return (never invented), and a re-run emits
nothing new. A fake price function stands in for DexScreener/Vantage.
"""
import time

import pytest

from mycelium import core
from mycelium.miners import signal_ledger as sl

NOW = 1_794_000_000.0  # pinned clock; tests never rot


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.delenv("MYCELIUM_DB", raising=False)
    monkeypatch.setenv("MYCELIUM_RESOLVER_ENABLED", "1")
    core.init_db(str(tmp_path / "test.db"))
    yield
    core.init_db(str(tmp_path / "test.db"))  # leave a clean handle


def _seed_record(rid, ingested, direction="long", entry=1.0, chain="solana",
                 address="mint", symbol="TOK"):
    core.emit(agent="vantage_ledger", session="ledger", kind="observation",
              action="signal_record", target=rid, outcome="info",
              payload={"id": rid, "ingested_at": ingested, "direction": direction,
                       "entry_price_usd": entry, "chain": chain, "address": address,
                       "symbol": symbol})


def _outcomes():
    return [t for t in core.iter_rows(core.query_traces(agent="vantage_ledger", action="signal_outcome", limit=1000))]


def _by_window(rows):
    out = {}
    for t in rows:
        p = t["payload"]
        import json as _j
        p = _j.loads(p) if isinstance(p, str) else p
        out[p["window"]] = p
    return out


def test_marks_long_at_three_windows_and_scores_hit():
    _seed_record("r1", int(NOW) - 26 * 3600, direction="long", entry=1.0)
    fake = lambda c, a, s: (1.10, "fake")  # +10% everywhere
    res = sl.resolve_due(now=NOW, fetch=fake)
    assert res["emitted"] == 3  # 1h, 6h, 24h
    wins = _by_window(_outcomes())
    assert set(wins) == {"1h", "6h", "24h"}
    for label in ("1h", "6h", "24h"):
        p = wins[label]
        assert p["ret_pct"] == 10.0 and p["signed_ret_pct"] == 10.0
        assert p["hit"] is True and p["scored"] is True
        assert p["price_src"] == "fake"
        # a 26h-old record is >15min past every window, so all three are late
        assert p["late"] is True


def test_short_inverts_the_sign():
    _seed_record("r2", int(NOW) - 2 * 3600, direction="short", entry=2.0)
    fake = lambda c, a, s: (1.80, "fake")  # price fell 10% -> short wins
    sl.resolve_due(now=NOW, fetch=fake)
    wins = _by_window(_outcomes())
    assert wins["1h"]["ret_pct"] == -10.0 and wins["1h"]["signed_ret_pct"] == 10.0
    assert wins["1h"]["hit"] is True


def test_hold_exit_alert_are_logged_not_scored():
    _seed_record("r3", int(NOW) - 2 * 3600, direction="hold", entry=1.0)
    sl.resolve_due(now=NOW, fetch=lambda c, a, s: (1.20, "fake"))
    p = _by_window(_outcomes())["1h"]
    assert p["scored"] is False and p["hit"] is None
    assert p["ret_pct"] == 20.0  # still logged


def test_late_flag_after_fifteen_minutes():
    _seed_record("r4", int(NOW) - 3600 - 16 * 60, direction="long", entry=1.0)
    sl.resolve_due(now=NOW, fetch=lambda c, a, s: (1.0, "fake"))
    assert _by_window(_outcomes())["1h"]["late"] is True


def test_unavailable_price_is_null_not_invented():
    _seed_record("r5", int(NOW) - 2 * 3600, direction="long", entry=1.0)
    sl.resolve_due(now=NOW, fetch=lambda c, a, s: (None, "none"))
    p = _by_window(_outcomes())["1h"]
    assert p["ret_pct"] is None and p["signed_ret_pct"] is None and p["hit"] is None
    assert p["price_src"] == "none"


def test_rerun_emits_nothing_new():
    _seed_record("r6", int(NOW) - 2 * 3600, direction="long", entry=1.0)
    fake = lambda c, a, s: (1.05, "fake")
    first = sl.resolve_due(now=NOW, fetch=fake)
    second = sl.resolve_due(now=NOW, fetch=fake)
    assert first["emitted"] == 1 and second["emitted"] == 0
    assert len(_outcomes()) == 1


def test_not_due_yet_is_skipped():
    _seed_record("r7", int(NOW) - 100, direction="long", entry=1.0)  # < 1h old
    res = sl.resolve_due(now=NOW, fetch=lambda c, a, s: (2.0, "fake"))
    assert res["emitted"] == 0


def test_fetch_price_solana_uses_dexscreener_and_major_uses_vantage(monkeypatch):
    # solana: the mint is what matters
    got = {}
    def fake_get(url, timeout=8):
        got["url"] = url
        return {"pairs": [{"priceUsd": "3.5", "liquidity": {"usd": 100}}]}
    monkeypatch.setattr(sl, "_get", fake_get)
    assert sl.fetch_price("solana", "mint", "TOK") == (3.5, "dexscreener")
    assert "dexscreener.com" in got["url"] and "mint" in got["url"]
    # major: symbol endpoint, price 0 means fetch failure -> None
    def fake_vantage(url, timeout=8):
        return {"price": 0}
    monkeypatch.setattr(sl, "_get", fake_vantage)
    assert sl.fetch_price("robinhood", "", "BTC") == (None, "none")


def test_legacy_directions_from_the_live_ledger_are_scored():
    """The live ledger stores BUY / SELL / NEUTRAL as producers sent them."""
    _seed_record("rB", int(NOW) - 2 * 3600, direction="BUY", entry=1.0)
    sl.resolve_due(now=NOW, fetch=lambda c, a, s: (1.10, "fake"))
    p = _by_window(_outcomes())["1h"]
    assert p["direction"] == "long" and p["scored"] is True and p["hit"] is True


def test_legacy_sell_inverts_and_neutral_is_not_scored():
    _seed_record("rS", int(NOW) - 2 * 3600, direction="SELL", entry=2.0)
    _seed_record("rN", int(NOW) - 2 * 3600, direction="NEUTRAL", entry=2.0)
    sl.resolve_due(now=NOW, fetch=lambda c, a, s: (1.80, "fake"))
    import json as _j
    by = {}
    for t in _outcomes():
        p = t.get("payload"); p = _j.loads(p) if isinstance(p, str) else p
        if p["window"] == "1h":
            by[p["direction"]] = p
    assert by["short"]["signed_ret_pct"] == 10.0 and by["short"]["hit"] is True
    assert by["hold"]["scored"] is False and by["hold"]["hit"] is None


def test_a_mint_is_never_priced_by_its_ticker(monkeypatch):
    """Live: pool:1715450, a pump.fun token named BTC with chain '' -- the
    resolver priced it as Bitcoin (entry 0.00095 -> 74,852 = +9e9 %)."""
    calls = []

    def fake_get(url, timeout=10):
        calls.append(url)
        if "dexscreener" in url and "MEME" in url:
            return {"pairs": [{"chainId": "solana", "priceUsd": "0.0011", "liquidity": {"usd": 5000}}]}
        if "dexscreener" in url:
            return {"pairs": []}
        return {"price": 74852.2}
    monkeypatch.setattr(sl, "_get", fake_get)
    assert sl.fetch_price("", "MEMEpump", "BTC") == (0.0011, "dexscreener")      # empty chain still by mint
    assert sl.fetch_price("solana", "DEADpump", "BTC") == (None, "none")         # no pairs -> no price, not BTC
    assert not any("markets/BTC" in u for u in calls)
    assert sl.fetch_price("", "", "BTC") == (74852.2, "vantage")                 # a real major, no address
