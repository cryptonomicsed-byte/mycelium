# EARLY-ENTRY SCORE — Canonical Definition

**STATUS: DRAFT** — becomes LOCKED when all three lanes align outputs to this schema and
`hub/score_schema.md` is merged to main with `STATUS: LOCKED`.

## Score object

```json
{
  "token_address": "string (Solana pubkey)",
  "symbol": "string",
  "score": 72,
  "grade": "B+",
  "components": {
    "signal_engine": 65,
    "whale_radar": 88,
    "fusion_picks": 60
  },
  "lane_weights": {
    "signal_engine": 0.40,
    "whale_radar": 0.40,
    "fusion_picks": 0.20
  },
  "signals_fired": ["new_pool", "vol_before_price", "gmgn_smart_buy", "wallet_cluster"],
  "hard_gates_passed": true,
  "gate_failures": [],
  "entry_recommendation": "PAPER",
  "conviction": 0.72,
  "scored_at": "2026-09-17T12:00:00Z",
  "data_age_s": 45,
  "explanation": "Strong whale cluster (3 watchlist wallets, 8min ago) + rising volume before price. Dev holding, low bundler share. Recommend paper entry."
}
```

## Composite formula

```
score = clamp(
  (signal_engine_score * 0.40) +
  (whale_radar_score   * 0.40) +
  (fusion_picks_score  * 0.20),
  0, 100
)
```

- `signal_engine_score` — Lane 1 output, 0-100 (from params.json formula)
- `whale_radar_score` — Lane 2 output, 0-100 (watchlist cluster strength)
- `fusion_picks_score` — existing signal_fusion picks score, 0-100

## Grade bands

| Score | Grade | Recommendation |
|-------|-------|----------------|
| 80-100 | A | PAPER (high conviction) |
| 65-79 | B | PAPER |
| 50-64 | C | Watch only |
| 35-49 | D | Skip |
| 0-34 | F | Skip |

Live trading requires score ≥ 80 AND hard_gates_passed = true. Always PAPER by default.

## Hard gates (any failure → hard_gates_passed=false, recommendation=SKIP)

1. Liquidity < $5,000 → SKIP
2. Top holder concentration > 85% → SKIP (rug risk)
3. Volume < $500 in last 5min → SKIP (no interest yet)
4. Token age > 72h → SKIP (missed window)
5. Bundler rat share > 60% → SKIP (snipers dumping)
6. Dedup: same token scored in last 10min → return cached result
7. Sabbath gate: no trades during Sabbath window (from signal_fusion/gates.py)
8. PAPER-only override: if LIVE_ENABLED env not set, cap at PAPER regardless of score

## Convergence endpoint

```
GET /api/score/{token_address}
→ EarlyEntryScore object above

POST /api/score/batch
body: {"addresses": ["addr1", "addr2", ...]}
→ {"scores": [EarlyEntryScore, ...]}
```

Implemented in: `fleet/hub/convergence.py` (TODO: implement after score_schema is LOCKED)
