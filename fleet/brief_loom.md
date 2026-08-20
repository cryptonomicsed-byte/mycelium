# PANEL BRIEF — LOOM (trading signal strategies)

## Mission
Enhance LOOM's trading signal strategies to produce the highest-quality
actionable signals for the Vantage/Ares ecosystem. You are the SIGNAL
STRATEGIST panel.

## Verified facts (from orchestrator readback 2026-08-19)
- LOOM lives at /opt/ares/Loom (git repo, branch master, gitea remote
  gh-cryptonomicsed-byte/Loom). Core files: TransformerSignalStrategy.py,
  backtester.py, engines.py, fabric.py, fast_server.py (systemd-served).
- The ecosystem: ares-signal-fusion (VPS :8003 picks sidecar, /api/picks)
  ranks tokens 0-100 from S_signal + S_wallet + council + findings, mirrors
  top-3 into the Vantage pool for council debate. Mycelium wallet-intel
  feeds S_wallet (GMGN smart money + KOL trades, 6-key rotating pool).
- GMGN keys: 6 in pool (/opt/ares/.gmgn_keys.json), read via gmgn-cli or
  gmgn_pool.py; proxy pool /opt/ares/.gmgn_proxies.json (free, refreshed
  every 15 min by cron). VPS IP is per-IP banned periodically — use
  gmgn_cli_proxy.run_cli to route through proxies.
- Council: 6-persona LLM debate on Vantage :8001, PAPER verdicts only.
- DexScreener public API (no key): tokens/v1/solana/{mint},
  token-profiles/latest/v1. GeckoTerminal OHLCV ~30 req/min (pace!).

## Required work (pick the highest-value, verify, ship)
1. AUDIT TransformerSignalStrategy.py — identify weak signals, missing
   features (bundler detection, honeypot flags, liquidity depth), and
   concrete improvements. Document findings in LOOM/backtester notes.
2. IMPROVE the backtester — add real OHLCV (GeckoTerminal/DexScreener)
   with proper pacing, and a calibration report showing signal hit-rate.
3. ADD at least ONE new signal source or composite: e.g. whale-cluster
   detection from wallet-intel DB (wallet_intel.db), KOL-conviction score,
   or smart-money momentum. Wire it into engines.py or fabric.py.
4. Run the backtest on the last 30 days of real data and produce a
   before/after calibration comparison. Store results in LOOM/reports/.

## Rules
- Work ONLY on /opt/ares/Loom. Branch: loom-signal-enhance. Commit +
  push to your branch (origin = gitea gh-cryptonomicsed-byte/Loom).
- Never touch /opt/ares/Vantage, wallet_intel, or signal_fusion code —
  those are other panels' lanes. Read their DBs/APIs freely.
- Trading guardrails: PAPER only. No real trades, no order placement.
- Never print full API keys in logs or commits.
- Verify every change runs (python syntax + a live smoke test) before
  committing.

## Report back (end your session with this)
- What you audited, what you changed, what you added
- Backtest before/after numbers
- Commits pushed (SHAs)
- Top 3 recommended next enhancements
