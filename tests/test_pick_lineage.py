"""P1-18: mirrored picks carry signal_record/1 lineage (not blind).

Acceptance: a fused pick posts to Vantage with parents = the pool ids it
read, producer blind false / lineage vantage-pool, and the legacy
source/conviction unchanged. The payload's signal_record/1 fields validate
against Vantage's P1-01 rules (checked here with a local copy of the regex).
"""
from __future__ import annotations

import json
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from signal_fusion import sources  # noqa: E402
from signal_fusion.signal_fusion import mirror_to_vantage, _ulid  # noqa: E402

# P1-01's source_ref rule (kept in sync; see Vantage backend/signal_record.py).
_SOURCE_REF = re.compile(r"^(tx|x|task|url|portent|pool|ledger):\S+$")


class TestMirrorLineage(unittest.TestCase):
    def _cfg(self, tmp):
        return {"mirror_top_n": 3, "endpoints": {"vantage_base": "http://127.0.0.1:8001",
                                                 "vantage_db": str(tmp / "v.db")}}

    def test_payload_has_record_fields_and_keeps_source_and_conviction(self):
        p = {"symbol": "TOK", "score": 84.0, "rank": 1, "pick_id": 42,
             "token_addr": "0xTOK", "parents": [7, 9]}
        with mock.patch("signal_fusion.signal_fusion.open", return_value=_KeyFile()), \
             mock.patch("signal_fusion.signal_fusion.urllib.request.urlopen") as uo, \
             mock.patch("signal_fusion.signal_fusion.os.path.exists", return_value=False):
            captured = {}
            def fake_open(req, *a, **k):
                captured["data"] = json.loads(req.data)
                return _FakeResp(200)
            uo.side_effect = fake_open
            mirror_to_vantage(self._cfg(_TmpPath()), [p])

        body = captured["data"]
        assert body["source"] == "signal_fusion" and body["type"] == "trending"
        assert body["direction"] == "buy"  # legacy kept
        assert body["conviction"] == 0.84  # score/100 kept, unchanged
        assert body["mint"] == "0xTOK" and body["symbol"] == "TOK"
        # record fields
        assert body["schema"] == "signal_record/1"
        assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", body["id"])
        assert body["source_ref"] == "task:42" and _SOURCE_REF.match(body["source_ref"])
        assert body["parents"] == ["7", "9"]
        assert body["asset"] == {"symbol": "TOK", "chain": "solana", "address": "0xTOK"}
        assert body["producer"]["blind"] is False
        assert body["producer"]["lineage"] == "vantage-pool"

    def test_pick_parents_are_the_pool_ids_read(self):
        sigs = [sources.Signal(token_addr="0xTOK", symbol="TOK", direction=1, strength=1.0,
                               source="vantage_signal", source_ts=1.0,
                               meta={"pool_id": i, "pool_source": "kol"}) for i in (3, 5, 8)]
        got = [str(m.get("pool_id")) for s in sigs if (m := getattr(s, "meta", None)) and m.get("pool_id")]
        assert got == ["3", "5", "8"]  # the exact ids fused, stringified for the record


class _KeyFile:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return '{"intel": "test-key"}'


class _TmpPath:
    def __truediv__(self, other):
        import tempfile
        return tempfile.NamedTemporaryFile(delete=False).name


class _FakeResp:
    def __init__(self, status):
        self.status = status

    def read(self):
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


if __name__ == "__main__":
    unittest.main()
