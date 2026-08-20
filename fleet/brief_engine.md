# ENSEMBLE BRIEF — SIGNAL ENGINE (panel-loom, branch: engine)

You are LANE 1 of a three-panel ENSEMBLE. Your lane: the early-entry
signal math. THE mission: detect what to buy BEFORE it hits the crowds.

## Your repo
/opt/ares/strategy-lab (gitea vantage/strategy-lab), branch ENGINE.
Read /opt/ares/strategy-lab/STRATEGY.md FIRST — the doctrine is the
contract. Your lane directory: engine/.

## Your question
What measurable signals fire BEFORE the crowd buys? For each candidate
signal, you must show LEAD TIME (how many minutes before crowd volume it
fires) using REAL historical data.

## Candidate signals (evaluate, parameterize, backtest — pick the best)
1. New pool creation / pump.fun graduation within last N minutes
2. Bundler behavior: high bundler_rat_share at launch (snipers preloaded)
   — data: gmgn_pool.token_snapshot / gmgn-cli
3. Dev wallet: liquidity seeding, dev sell risk, token transfer patterns
4. Holder concentration curve: top-10 share over time at launch
5. Volume-to-holders ratio rising BEFORE price (not after)
6. Candle micro-structure at launch (GeckoTerminal/DexScreener OHLCV —
   PACE ~30 req/min, one 429 retry)
7. GMGN signal stream: smart money buys / price spikes

## Deliverables (all in engine/, on your branch, pushed)
- engine/params.json — scored early-entry signal stack: each signal with
  {weight 0-1, thresholds, min_confidence, data_source}
- engine/backtest.md — for each chosen signal: lead time (minutes before
  crowd volume), precision at 3/6/12h horizons, on real data (min 20
  historical launches you can source from DexScreener token-profiles or
  GeckoTerminal)
- engine/README.md — how to re-run your backtest

## Data access (read freely)
wallet_intel.db, Vantage db, gmgn-cli (6-key pool; use
gmgn_cli_proxy.run_cli when the VPS IP is per-IP banned), DexScreener
public API, GeckoTerminal (paced). Solscan = dormant, skip.

## Ground rules
- PAPER ONLY. No real trades, no order placement.
- Never print full API keys in logs or commits.
- Every claim backed by real query output.
- You own engine/ only. Read radar/ and hub/ outputs, don't edit them.
- Coordinate: the HUB panel (omokoda) defines the EARLY-ENTRY SCORE in
  hub/score_schema.md — align your params.json JSON shape to it once it
  appears; propose changes via proposals/ if you disagree.
- Verify everything runs (python syntax + live smoke) before committing.

## Report back
Signals chosen + lead times + precision numbers, params.json content
summary, commits (SHAs), and your recommended next iteration.
