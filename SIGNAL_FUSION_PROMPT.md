# AGENT TASK (Phase 3): Signal fusion engine — find the best tokens to trade from EVERY source

Goal: build a module that fuses ALL available intelligence — Vantage signal pool,
wallet intel (149k classified wallets), GMGN market data, council verdicts, Mycelium
miner findings — into ONE ranked, transparent list of the best tokens to trade.
Output must be explainable: every pick shows WHY (which signals, which wallets,
which findings drove the score). Runs continuously on the VPS, feeds the council,
logs everything to the Mycelium substrate, and surfaces as a dashboard view.

## Context — what already exists (reuse ALL of it, don't rebuild)

- Vantage signal pool: ~43k+ signals with sources; 43,200 tracked_wallets;
  145,224 token_wallet_roles; 1,093 alpha_wallets; 62 KOL social links.
  VPS-side API at :8001 (auth via ~/.vantage_key). The council daemon reads this pool.
- ares-wallet-intel daemon (VPS, /opt/ares/ares-wallet-intel): collector.py
  (smart-money + KOL trades, 5-min), scanner.py (token-scope holder/trader scans,
  15-min gate, GMGN token_top_holders), wallet_registry DB with classifications
  (deployer/whale_trader/alpha/smart_degen/smart/early_buyer/KOL) + GMGN-native tags
  (bundler/rat_trader/sniper/wash_trader/renowned/fresh_wallet/dev) + funding clusters.
- gmgn_pool.py: direct-API round-robin client — rotating X-APIKEYs, rotating proxies,
  per-key cooldown, shared IP-ban state with escalating backoff. Birdeye v3 holder
  fallback. READ endpoints need only X-APIKEY + timestamp + client_id.
- Mycelium: substrate traces, miners (wallet_activity, wallet_correlation,
  wallet_anomaly, opportunity, anomaly, cross_agent, recurring_workflow), findings +
  apply loop. Gateway :8811 with /api/trace (POST), /api/findings, /api/mine,
  SSE /api/stream.
- Council: 6 personas, two debate rounds, gates (Risk veto, Contrarian double
  dissent, liquidity floor $5K, conviction ≥0.60 paper / ≥0.70 live), PAPER mode
  default for 2-4 weeks, Sabbath gate. Verdicts table + calibration.
- Dashboard (web/dashboard): Phase 1 restored old sections, Phase 2 added
  observability/wallet views. Gateway proxies /api/council/* → VPS :8001.

## Architecture — new module on the VPS

/opt/ares/ares-signal-fusion/  (repo: the same git repo that holds collector.py /
scanner.py — check `git remote -v`; sync + commit + push both remotes at the end)

- signal_fusion.py      — main loop, --daemon mode + --once flag, systemd
- sources.py            — normalizers for every signal source → common schema
- scoring.py            — composite score (spec below)
- gates.py              — hard filters (spec below)
- store.py              — SQLite ares_picks.db (picks + outcomes + audit)
- config.json           — ALL weights/thresholds/half-lives (SIGHUP hot-reload)
- backtest.py           — replay from history if it exists (see §Backtest)
- ares-signal-fusion.service (systemd, Restart=always)

Cadence: every 15 min (aligned after scanner/collector runs) + on-demand via
`--once` and a local trigger endpoint. Each run emits Mycelium traces
(workflow_start → scoring steps → workflow_end) so the live wall shows it working.

## Common schema (normalize EVERYTHING into this)

{ token_addr, symbol, direction (+1/-1/0), strength 0..1, source, source_ts,
  meta: {...} } — sources.py maps each raw source to this. direction=-1 (sell/dump)
signals are kept and scored too — they suppress a token's rank.

## Signal sources & trust weights (config.json, initial values)

- vantage_signal (from pool, sub-weight by pool source): smart_money 0.90,
  kol 0.80, onchain 0.70, social 0.50, news 0.40
- wallet_activity (collector smart-money/KOL trades): 0.85
- wallet_role (scanner classifications, aggregated): 0.80
- council_verdict (gates-passed, conviction-weighted): 0.90
- mycelium_opportunity finding: 0.70
- mycelium_anomaly finding: ±0.50 (direction-aware)
- mycelium_wallet_correlation (correlated whales accumulating): 0.60 bonus
- market_momentum (volume/price trend, directionless): 0.50

## Wallet quality weights (config.json — used inside wallet signals)

alpha 1.00 | whale_trader 0.85 | smart_degen 0.75 | kol 0.65 | renowned 0.60 |
smart 0.60 | early_buyer 0.50 | sniper 0.20 (direction matters) | fresh_wallet 0.00
(extra scrutiny, never a positive driver alone) | deployer -0.40 | rat_trader -0.50 |
wash_trader -0.60 | bundler -0.70

## Composite score (scoring.py)

score = Σ w_i × S_i  (each S_i normalized 0..1; final 0..100)

- S_signal: net weighted agreement of pool signals for the token (Σ trust ×
  strength × direction × decay), normalized by logistic so a handful of strong
  signals saturate near 1.
- S_wallet: net smart flow = Σ (quality × size_norm × direction × decay) over buys
  and sells in window. size_norm = log1p(usd) / log1p(token_24h_volume). Bonus ×1.2
  if ≥3 distinct smart wallets agree; ×0.5 penalty if the only buyers are
  sniper/fresh_wallet tags.
- S_council: max |conviction| × direction over recent gates-passed verdicts; 0 if
  none. LIVE verdicts weigh 1.5× paper verdicts (config).
- S_finding: opportunity findings add their confidence; anomaly findings subtract
  when direction-aware negative; wallet_correlation adds 0.6 when ≥2 whales in the
  same cluster accumulated the token this window.
- S_market: liquidity score = min(1, log10(liq/5k)/2), volume trend (rising 24h
  volume = +), holder growth, minus whale-concentration and bundler-ratio penalties.

Time decay on ALL signals: exp(-Δt / half_life) with per-source half-lives
(config): trades 12h, wallet accumulations 48h, verdicts 72h, findings 24h.

## Hard gates (gates.py — any FAIL rejects the token this run, logged as veto)

- Liquidity ≥ $5,000 (matches council floor; config)
- Top-10 holders share < 60% (config)
- bundler + rat_trader share of top holders < 30%
- 24h volume ≥ $10,000
- No honeypot; buy tax ≤ 15% (from GMGN token data when available)
- Token age ≥ 1h unless whitelisted (no-rug-new-listing filter)
- Not already picked in the last 24h (dedupe)
- Sabbath gate: no picks during the gate window (read from Vantage config)
- PAPER MODE: picks are candidates for the council — NEVER auto-execute in LIVE.
  LIVE execution requires the existing council path (Risk veto + max size) AND a
  manual user flip. This is non-negotiable.

## Outputs & integrations

1. Ranked top-10 (score desc) written to ares_picks.db — table `picks`
   (id, ts, token_addr, symbol, score, rank, components JSON — each S_i + the
   dominant signals/wallets behind it, gates JSON — passed/failed, status).
2. Top-3 mirrored into the Vantage signal pool as source='signal_fusion',
   strength = score/100, so the COUNCIL picks them up naturally — no council code
   changes. Mark them (meta.source_pick_id) so outcome tracking can attribute.
3. Mycelium traces per run (agent='signal_fusion', kinds workflow_start/
   observation/workflow_end) + a finding via the opportunity miner path when a
   token crosses score ≥ 80 (config) so the apply-loop/dashboard picks it up.
4. Dashboard view #/picks — new gateway proxy /api/picks → VPS (extend the
   handleCouncilProxy pattern; the VPS side serves GET /picks from ares_picks.db
   on the :8001 API or a small :8003 HTTP endpoint the gateway proxies).
   Columns: rank, symbol, score, top-3 drivers (click → rationale drawer with the
   exact signals/wallets), gates, status. This is a Phase-2-style dashboard view —
   reuse the existing Web Components + store + virtualized table.

## Outcome tracking & self-calibration (real data, no simulation)

- On pick: record entry price. On subsequent runs (and a 4h/24h/7d cron), store
  price and computed return in `outcomes` (pick_id, mark, price, return_pct).
- After ≥20 resolved picks: for each component S_i, compare avg return of picks
  where that component was the dominant driver → produce a weight-adjustment
  suggestion (report only — never auto-change weights).
- Backtest: if Vantage/wallet tables retain historical rows (check
  wallet_registry.last_seen history, signal history, Vantage signal timestamps),
  replay the scoring over past windows and compare top-pick returns vs. random
  picks from the same window. If no history exists, forward-test in PAPER mode
  (the outcome tracking IS the backtest).

## Verification (all real, all must pass)

1. systemctl enable --now ares-signal-fusion; journalctl -u ares-signal-fusion -f
   shows a clean run (no exceptions, sane durations).
2. Run --once manually: picks table has ≥1 row, scores 0..100, components JSON
   non-empty, every pick has ≥1 cited driver.
3. Spot-check the TOP pick: query wallet_registry + Vantage signals for the cited
   wallets/signals — they must actually exist and the math must reproduce
   (recompute the score by hand from the components).
4. Gate test: force a low-liquidity or bundler-heavy token through --debug and
   confirm it's vetoed with the right gate name.
5. Mirroring: Vantage signal pool contains source='signal_fusion' rows; next
   council run's verdict rationale or logs reference a fusion-sourced signal.
6. Traces: curl -s http://127.0.0.1:8811/api/traces?agent=signal_fusion returns
   the run's traces (gateway on the VPS side of the tunnel or via the public URL).
7. Dashboard: #/picks renders, rows match ares_picks.db, rationale drawer opens.
8. Commit + push to BOTH remotes (same repo as collector/scanner; check
   `git remote -v` — origin + gitea). Report commit SHA.

## Don't touch

Vantage core schema (append-only additions are fine — mirroring adds rows, never
ALTERs), GMGN pool keys, existing collector/scanner behavior, council persona
weights and gates, the LIVE execution path (stays PAPER until the user flips),
the 8870 standalone dashboard, Mycelium miner logic (the fusion engine consumes
findings, it doesn't modify miners).

## Report back

Module layout, chosen config values, the first real top-10 picks WITH their
rationale (top drivers per pick), gate rejections observed, verification output
(7 checks above), outcome-tracking row count, commit SHA.
