# MYCELIUM AGENT WORK PACKAGE

Single source of truth for the next agent run. Contains all three task prompts
(Phase 1, Phase 2, Phase 3) plus the master checklist. Run in order — each phase
assumes the previous one is done, verified, and pushed.

## REPO / DEPLOY CHEAT-SHEET (shared by all phases)

- Repo: ~/mycelium (branch main). Remotes: origin = github.com/cryptonomicsed-byte/mycelium,
  gitea = vantage/mycelium @ 2.25.70.156:3001 (mirror). Push BOTH.
- Dashboard: web/dashboard/ — vanilla TS + Web Components, esbuild. dist/ IS COMMITTED
  (Termux gateway serves the Go binary only, no Node). Build: npm install && node esbuild.config.mjs.
- Gateway: gateway/main.go (+ stream.go SSE, wt.go WebTransport). Build: go build -o mycelium-gateway .
  Restart from ~/mycelium/gateway via a BACKGROUND PROCESS — never nohup.
- VPS council API: :8001, auth via ~/.vantage_key. Gateway proxies /api/council/* (same-origin,
  key never exposed to browser).
- Old dashboard REFERENCE (read, don't touch): /opt/ares/ares_council_dashboard.py on the VPS (:8870).
- Wallet intel daemon: /opt/ares/ares-wallet-intel (collector.py 5-min trades, scanner.py 15-min gate,
  gmgn_pool.py round-robin keys+proxies, Birdeye fallback). Module repo = same git repo as collector/scanner.
- Public URL: http://2.25.70.156:8811/web/ (tunnel binds 0.0.0.0:8811, firewall open).
- Secret hygiene: never commit keys/tokens/DBs/binaries. Secret-scan before every push.

## MASTER CHECKLIST — tick every box before reporting back

### Preflight
- [ ] cd ~/mycelium && git fetch origin && git status — working tree clean, HEAD == origin/main
- [ ] Gateway builds: cd gateway && go build -o mycelium-gateway .
- [ ] Dashboard builds: cd web/dashboard && npm install && node esbuild.config.mjs
- [ ] VPS reachable (ssh alias), old dashboard readable at /opt/ares/ares_council_dashboard.py

### Phase 1 — Restore old sections + surface Mycelium reality
- [ ] Verdicts view: #/symbol/dir/conv/entry-liq/outcome/mode/time + expandable votes (persona, direction, confidence, weight, rationale), PAPER/LIVE flag
- [ ] Calibration view: wins/tracked/win rate/multiplier/effective weight/veto per persona + weight formula
- [ ] Council view: personas table + gates explainer (risk veto, double dissent, liq floor $5K, conviction 0.60/0.70, PAPER default, 2 rounds)
- [ ] Substrate view: status badge, trace/finding counts, council traces (latest 30), council findings w/ state badges
- [ ] Wallets view merged: token buy table, role counts, classified wallets (addr/buys/volume/distinct tokens/edge/tags), wallet findings
- [ ] New views: Trace Explorer (filters agent/kind/outcome + search + JSON drawer), Findings detail (confidence bar, evidence, apply/dismiss), Loop (traces→mine→findings→skills), Provenance enhance (anchors, pubkey, verify history, WT cert hash), Miners enhance (counts by state, last-run, mine-now), Alerts, Agents, System
- [ ] Home status strip updates on every SSE event
- [ ] esc() on every agent-controlled string (XSS load-bearing)

### Phase 2 — Feature expansion (40 features / 6 parts)
- [ ] Part 0: unified store w/ selectors, virtualized tables, export CSV/JSON + copy-as-curl, localStorage persistence + deep links, error/empty/loading states, mobile bottom-nav, Cmd/Ctrl+K search
- [ ] Part 3' endpoints: /api/stats/timeseries, /api/agents, /api/alerts + ack, /api/logs, /api/skills, /api/wallet/{addr}, /api/token/{addr}, /api/council/verdicts/{id}, /api/vetoes, /api/prune, /api/webauthn/*, /api/status extension
- [ ] SSE event types wired: trace, finding, mine, alert, wallet
- [ ] Part 1: live activity wall, time-series charts, agent health panel, alert inbox, gateway log tail, request inspector
- [ ] Part 2: wallet drawer, token view, correlation graph, follow-the-money explorer, watchlist, funding-clusters panel
- [ ] Part 3: debate transcript per verdict, veto log, PAPER/LIVE split + per-persona performance, conviction heatmap
- [ ] Part 4: skill lineage, what-changed feed, miner run history, pattern browser
- [ ] Part 5: topology map, storage/retention panel, WebAuthn enrollment UI, cert/tunnel status, audit log viewer, security posture
- [ ] Part 6: PWA, WebGPU particle field, kiosk mode, theme/density, keyboard shortcuts, shareable views, empty-state guidance

### Phase 3 — Signal fusion engine (ares-signal-fusion)
- [ ] Module at /opt/ares/ares-signal-fusion: signal_fusion.py, sources.py, scoring.py, gates.py, store.py, config.json, backtest.py, systemd unit
- [ ] sources.py normalizes all 8 source types into common schema
- [ ] scoring.py: composite score Σ w_i·S_i, time decay (12h/48h/72h/24h), wallet quality weights, smart-agreement bonus
- [ ] gates.py: liq ≥ $5K, top-10 holders < 60%, bundler+rat < 30%, vol ≥ $10K, no honeypot/tax ≤ 15%, age ≥ 1h, 24h dedupe, Sabbath gate, PAPER-only execution
- [ ] store.py: picks + outcomes tables (entry price, +4h/+24h/+7d marks)
- [ ] config.json: all weights/thresholds/half-lives + SIGHUP hot-reload
- [ ] Top-3 mirrored into Vantage signal pool as source='signal_fusion' (append-only)
- [ ] Mycelium traces per run + finding at score ≥ 80
- [ ] Dashboard #/picks view + gateway proxy /api/picks
- [ ] Self-calibration: ≥20 resolved picks → per-component weight suggestions (report only)

### Build / deploy / verify
- [ ] esbuild build done, dist/ committed
- [ ] Gateway rebuilt (Phase 2/3 touch gateway/main.go) + restarted via background process
- [ ] VPS: systemctl enable --now ares-signal-fusion (Phase 3)
- [ ] curl /api/status — new fields present; /web/ → 200 w/ new main.js hash
- [ ] curl /api/council/overview — daemon_running true; /api/picks → rows (Phase 3)
- [ ] Public: curl -s -o /dev/null -w "%{http_code}" http://2.25.70.156:8811/web/ → 200
- [ ] Engine checks: picks rows w/ reproducible math; hand-recompute top pick score from cited wallets/signals; forced gate-veto test; pool mirror rows present; next council debate references a fusion signal; traces visible at /api/traces?agent=signal_fusion
- [ ] Browser sanity (if possible): every new view renders, no console errors, CSV export downloads, kiosk cycles, PWA installable

### Commit / push / report
- [ ] git add -A && git commit (clear message) && git push origin main
- [ ] git fetch gitea; force-push gitea main only if diverged (our history authoritative on origin)
- [ ] Report back: what shipped / deferred + why, first real top-10 picks WITH rationale, gate rejections observed, verification output, commit SHA(s)

---

=====================================================================
PHASE 1 — DASHBOARD RESTORE + MYCELIUM REALITY
(from DASHBOARD_ENHANCE_PROMPT.md)
=====================================================================
# AGENT TASK: Restore + expand the Mycelium dashboard to fully reflect what Mycelium does

You are working on the **Mycelium stigmergic-substrate** project. A claude-worker
dashboard merge replaced the old functional dashboard with a prettier but
INCOMPLETE one — several views and data panels the old dashboard had were dropped,
and the new one doesn't surface Mycelium's real capabilities. Your job: make the
new dashboard a superset of the old one plus new sections that reflect what the
system actually does.

## Repos / paths

- Working repo (source of truth, edit here): `~/mycelium` (branch `main`)
- Fresh clone reference: `~/mycelium-gh` (same content — pick one, keep them in sync at the end)
- New dashboard (the one to fix): `web/dashboard/` — vanilla TypeScript + Web Components,
  esbuild, `dist/` is COMMITTED (Termux gateway never runs Node; the Go gateway serves dist statically)
- Old dashboard (the REFERENCE with all the info): on the VPS at `/opt/ares/ares_council_dashboard.py`
  (port 8870) — read it, it has the complete render logic for every dropped section
- Go gateway: `gateway/main.go` (REST routes), `gateway/stream.go` (SSE), `gateway/wt.go`
  (WebTransport). Already has `handleCouncilProxy` → `/api/council/*` to the VPS Vantage API.
- Build: `cd web/dashboard && npm install && node esbuild.config.mjs` (dist committed after),
  then `cd gateway && go build -o mycelium-gateway .`

## What the OLD dashboard had (currently MISSING from the new one — restore ALL of it)

Old tabs: Verdicts / Calibration / Council / Substrate / Wallets. Exact render logic
in `/opt/ares/ares_council_dashboard.py` (functions renderVerdicts, renderCalibration,
renderCouncil, renderSubstrate, renderWallets, renderOverview). Data via the new
gateway proxy `/api/council/{overview,verdicts,calibration,substrate}` (already wired).

1. **Verdicts** — table: # / Symbol / Dir / Conv / Entry-liq / Outcome / Mode (PAPER/LIVE) /
   Time UTC / Votes. Votes = persona + direction + confidence + weight + full rationale
   (hover/expandable). Include the paper/live flag and outcome (pending/applied).
2. **Calibration** — per-persona: wins, tracked, win rate, multiplier, effective weight,
   veto flag, role description. Show the weight formula (base × multiplier, clamp 0.2–2.0).
3. **Council** — personas & objectives table (persona, base weight, veto) + the GATES
   explainer text (Risk veto, Contrarian double-dissent, liquidity floor $5K, conviction
   ≥0.60 paper / ≥0.70 live, PAPER default, two debate rounds).
4. **Substrate** — mycelium status badge (ok/down) + traces count + findings count +
   gateway-via-tunnel note; council traces table (ts/kind/action/target, latest 30);
   council findings list with state badges (applied/open).
5. **Wallets** — "What everyone is buying": token table (symbol, buy volume, distinct
   wallets); wallet role counts (badges); classified wallets table (addr, buys, volume,
   distinct tokens, edge, tags); mycelium wallet findings with state badges.
   (The new dashboard HAS a Wallets view — merge the old dashboard's richer data fields
   into it: buy volume, distinct wallets, role counts, edge scores, wallet findings.)

## What Mycelium ACTUALLY does — new sections that must surface it

Mycelium = agents emit traces → miners find patterns → findings → apply loop → skills.
The dashboard must make this visible. Inventory from the code:
- MCP tools (mycelium/mcp_server.py): trace, list_traces, mine, list_findings, get_finding,
  apply_finding, dismiss_finding, dashboard_url, publish, publish_findings, check_alerts
- Miners (mycelium/miners.py, 7 registered): recurring_workflow, anomaly, cross_agent,
  opportunity, wallet_activity, wallet_correlation, wallet_anomaly
- Gateway API: /api/status, /api/traces, /api/findings, /api/findings/{id}/apply|dismiss,
  /api/miners, /api/mine, /api/mine/wasm, /api/provenance, /api/provenance/verify,
  /api/stream (SSE), /api/webtransport/cert-hash, /api/council/*, /api/trace (POST)
- Provenance: anchored chain (chain_state.jsonl), pubkey, verify endpoint
- A2A publish: findings → Vantage feed (a2a.py, publish.py)
- WebTransport live-push (wt.go, :8812, rotating cert) + SSE stream
- WebAuthn gateway auth (auth.go, MYCELIUM_GATEWAY_AUTH=1)
- On-device WebNN anomaly mining (web/shared/webnn_score.js)
- Wallet intel pipeline: Vantage wallet seeding (149k rows), GMGN pool, token scanner,
  funding clusters, edge scoring — see mycelium/miners.py wallet_* and wallet/ dir

### New sections to ADD (beyond restoring the old five)

6. **Overview/Home header** — always-visible status strip (like old renderOverview):
   daemon badge (running/pid), verdict count, signal pool count + sources, trace buffer
   pending, mycelium status, gateway pubkey short. Update on every SSE event.
7. **Trace Explorer** (new view `#/traces`) — full trace table with filters: by agent
   (council, wallet_intel, mycelium-cron, etc.), by kind (decision/observation/tool_call/
   error/memory_write/workflow_start/end), by outcome (success/failure/partial/info),
   search box, click a row for the full JSON payload. Feed from /api/traces with
   pagination (since=ts cursor) — same filter pattern the gateway supports.
8. **Findings detail** — new dashboard has a Findings view; ADD: confidence bar,
   evidence preview (truncated + expandable), miner name link → miner registry filter,
   apply/dismiss buttons (POST /api/findings/{id}/apply|dismiss), applied-vs-open split.
9. **Self-improvement loop** (#/loop or section in Findings) — visualize the cycle:
   traces → mined → findings → applied → generated-skills (list generated skills from
   the generated-skills/ dir via the gateway or a static listing) → hot-swappable note.
   Show count of applied findings and the skills they produced (apply.py writes SKILL.md).
10. **Provenance** — the new dashboard HAS a Provenance view; ENHANCE with: chain anchor
    count, pubkey fingerprint, verify-result history, last anchored timestamp, and the
    WebTransport cert hash + expiry (GET /api/webtransport/cert-hash).
11. **Miner registry** — new dashboard HAS Miners view; ENHANCE with per-miner: finding
    counts by state (open/applied/dismissed), last-run time, a "mine now" button that
    reports the outcome, and wallet_* miner descriptions (what each detects).
12. **Alerts** (#/alerts) — surface mycelium.check_alerts: alert configs and current
    triggers from the substrate (generated alerts, gateway check_alerts MCP).
13. **Agents** (#/agents) — who writes to the substrate: GROUP BY agent over /api/traces
    → agent name, trace count, last active, error rate; click → filtered trace view.
    This shows the multi-agent reality (Hermes, council, wallet_intel, cron).
14. **System/About** (#/system) — the stack the dashboard sits on: gateway REST+SSE+WT,
    WebAuthn toggle, storage (SQLite → Postgres), miner sandbox (wasm), publish/A2A to
    Vantage, council-tunnel note. Honest "what's running where" panel.

## Quality bar

- Keep the visual language of the new dashboard (Web Components, light DOM, app.css
  variables, WebGPU live background, WebTransport/SSE live-update). Don't regress the
  animation/UX — ADD data, keep the polish.
- Every view must use the existing esc() for agent-controlled strings (trace targets,
  finding evidence, wallet addresses) — XSS is load-bearing here.
- Views auto-refresh: SSE (store.subscribe) or a 15–30s interval; status strip always
  updates on SSE.
- Keep chunked loading (dynamic import for heavy deps like Three.js — don't bloat main.js).

## Build + deploy + verify (must finish with all three green)

1. `cd ~/mycelium/web/dashboard && npm install && node esbuild.config.mjs` (commits dist)
2. `cd ~/mycelium/gateway && go build -o mycelium-gateway .` (only if gateway changed)
3. Restart the gateway (Termux): kill the running `mycelium-gateway` PID, start again from
   `~/mycelium/gateway` with `./mycelium-gateway <abs-path-to-binary>` — use a background
   process, NOT nohup. Verify: `curl -s http://127.0.0.1:8811/api/status`,
   `curl -s http://127.0.0.1:8811/web/` (HTTP 200, new main.js hash),
   `curl -s http://127.0.0.1:8811/api/council/overview` (daemon_running true),
   `curl -s http://127.0.0.1:8811/web/dashboard/dist/main.js | grep myc-council-view`.
4. Public check: `curl -s -o /dev/null -w "%{http_code}" http://2.25.70.156:8811/web/` → 200
   (tunnel binds 0.0.0.0:8811, firewall already open).
5. Commit + push: git add -A, commit with a clear message, push to BOTH
   `origin` (github cryptonomicsed-byte/mycelium) and `gitea` (vantage/mycelium on
   http://2.25.70.156:3001, token in ~/.hermes/credential_vault.json → gitea.token).
   If gitea/main has diverged, fetch then `git push --force gitea main` (our history is
   authoritative on origin; gitea is a mirror).
6. Do NOT touch: Vantage repo, /opt/ares daemons, wallet_intel data, GMGN pool keys,
   council.db, the 8870 standalone dashboard (leave it running as-is).

Report back: what was restored, what new views were added, final commit SHA, and the
verification output (status/web/council/public checks).

=====================================================================
PHASE 2 — FEATURE EXPANSION (40 features / 12 new endpoints)
(from DASHBOARD_FEATURES_PROMPT.md)
=====================================================================
# AGENT TASK (Phase 2): Feature expansion — make the Mycelium dashboard a world-class ops surface

Same repo/paths/build as Phase 1 (DASHBOARD_ENHANCE_PROMPT.md). If Phase 1 is not
done yet, do it first — this prompt assumes the old sections (Verdicts, Calibration,
Council, Substrate, Wallets) and the Phase-1 new views (Traces, Findings detail, Loop,
Provenance, Miners, Alerts, Agents, System) exist. This phase ADDS features on top;
do not regress Phase 1 data or the visual language (Web Components, light DOM,
app.css variables, SSE/WebTransport live-update, chunked loading).

## Part 0 — Engineering foundations (build FIRST; every later feature depends on these)

1. **Unified store with derived selectors** — centralize ALL fetching in store.ts:
   typed cache per resource (traces, findings, miners, council, wallets), TTL +
   invalidation on SSE events, selectors like selectTracesByAgent(), selectFindingsByState().
   Views read from the store, never fetch directly. This is what makes the rest cheap.
2. **Virtualized tables** — traces can hit 10k+ rows. Implement a windowed/virtual list
   (fixed row height + spacer divs, ~50 visible rows) for Trace Explorer, Findings,
   Wallets. Hand-rolled, ~100 lines, no library.
3. **Export everywhere** — every table gets a ⤓ button: CSV + JSON download
   (Blob + a[download], filename = view + date). Also "copy as curl" on any panel
   that maps to a gateway API call.
4. **State persistence + deep links** — filters/search/sort persist to localStorage;
   hash routes carry state: #/traces?agent=council&kind=error&outcome=failure.
5. **Error/empty/loading states** — every view: skeleton shimmer on first load, error
   banner with retry button, empty state that EXPLAINS why ("no errors yet — good"),
   stale-data indicator (last updated Xs ago, red if >60s).
6. **Mobile nav** — sidebar collapses to a bottom tab bar under 768px; the whole
   dashboard must be thumb-friendly (it gets demoed from a phone).
7. **Global search (Cmd/Ctrl+K)** — fuzzy search across traces, findings, wallets,
   verdicts, agents; keyboard-first; results grouped by type; Esc to dismiss.

## Part 1 — Live ops & observability

8. **Live activity wall** (#/live) — the substrate as a real-time feed: every trace
   streams in as an animated card (agent avatar, kind icon, target, outcome color
   flash), auto-pause on hover, click → full JSON drawer. SSE-driven via the store.
   This is the "the hive is alive" view.
9. **Time-series charts** (#/stats) — hand-rolled canvas sparkline/area charts (no
   chart lib): traces/hour stacked by kind, findings open vs applied over 24h/7d/30d,
   mining runs + findings per run, agent-activity heatmap (agent × hour).
   Data from GET /api/stats/timeseries (Part 3′).
10. **Agent health panel** — per-agent: last heartbeat (latest trace), trace rate,
    error rate, status badge (active/stale/dead after 10min silence), click → filtered
    Trace Explorer. Shown on the Home status strip and #/agents.
11. **Alert inbox** (#/alerts) — from check_alerts: config list (threshold, cooldown),
    triggered alerts with severity color, acknowledge (POST /api/alerts/{id}/ack),
    unacked count badge on nav. Optional sound + browser notification toggle.
12. **Gateway log tail** (#/logs) — tail the last N gateway log lines
    (GET /api/logs?lines=&level=), filter by level, auto-scroll toggle. This is the
    phone-side debugging tool.
13. **Request inspector** — dev-tools-style drawer: last 50 gateway requests (method,
    path, status, ms) via an /api/status extension; click → copy as curl.

## Part 2 — Wallet intel depth (this is the product)

14. **Wallet drawer** (#/wallets → click any row) — full profile: address + copy
    button, all roles/tags (deployer, whale_trader, alpha, smart_degen, sniper,
    bundler, wash_trader…), buys table (token, side, volume, ts), distinct tokens,
    total volume, edge score + history, funding-cluster members, GMGN-native tags,
    mycelium findings touching this wallet. GET /api/wallet/{addr}.
15. **Token view** (#/tokens) — per token: holder breakdown (whale %, top holders
    table), buy-volume timeline, wallet-role distribution, related findings,
    last-scan time. GET /api/token/{addr}.
16. **Correlation graph** (#/graph) — force-directed (d3-force as a lazy chunk):
    nodes = wallets (size = volume), tokens, agents; edges = buys/roles/traces;
    click node → wallet/token drawer; hover → tooltip; filters for min volume / role.
    This makes "follow the money" visual.
17. **Follow-the-money explorer** — from any wallet: cluster → what they buy → who
    else buys the same tokens → overlap score. Breadcrumb UI (wallet → token →
    wallets). Reuses /api/wallet/{addr} + /api/token/{addr}.
18. **Watchlist** — pin wallets/tokens (localStorage); panel on Home with last-move
    timestamps; badge flash when a watched wallet trades (SSE wallet events from
    wallet_intel traces).
19. **Funding-clusters panel** — visualize funding-source clusters (who funded whom)
    from the funding_cluster traces the wallet scanner emits: cluster tree with
    wallet addresses.

## Part 3 — Council & trading transparency

20. **Debate transcript view** (#/verdicts/{id}) — the full multi-round debate:
    each persona's stance, direction, confidence, weight, rationale, PLUS gate results
    (risk veto? double dissent? liquidity floor? conviction threshold?) rendered as a
    chat-like transcript. GET /api/council/verdicts/{id}.
21. **Veto log** (#/vetoes) — every trade that was BLOCKED and why: gate name, persona
    votes, conviction at veto time, reason. This is the trust layer — surface it.
22. **PAPER vs LIVE split** — everywhere verdicts appear: toggle (paper/live/all),
    per-persona performance (win rate, avg conviction at win/loss, best/worst persona)
    from the calibration data.
23. **Conviction heatmap** — personas × last N verdicts as a heat grid (color =
    confidence), sortable. Pure client-side from /api/council/verdicts.

## Part 4 — Self-improvement loop (the AI-native magic)

24. **Skill lineage** (#/loop) — finding → apply → generated SKILL.md → which agents
    have since loaded it. Two-column layout: applied findings (with skill filename) +
    skills detail. GET /api/skills.
25. **What-changed feed** — one chronological stream on Home: finding applied (with
    evidence diff), skill written, miner run summary, publish/A2A events.
26. **Miner run history** (#/miners) — per miner: last run ts, duration, findings
    produced (open/applied/dismissed), next-run estimate, "mine now" with live result
    flash (POST /api/mine), small per-miner success sparkline.
27. **Pattern browser** — every discovered pattern as cards (miner, title, confidence,
    evidence preview, state); filter by miner/state; opens the full finding.

## Part 5 — System, security, infrastructure

28. **Topology map** (#/system) — nodes: this gateway, VPS (council daemon, Vantage
    API), Gitea, GitHub, the tunnel; edges with live status (last check, latency ms).
    Static definition + live fields in /api/status.
29. **Storage & retention panel** — DB sizes (substrate.db, council.db, wallet
    registry), trace count + oldest/newest ts, WAL size; retention display;
    "prune traces before <date>" button (POST /api/prune, confirm dialog).
30. **WebAuthn enrollment UI** — register a passkey from the dashboard
    (navigator.credentials.create → POST /api/webauthn/register), list enrolled
    credentials, revoke. Session list + revoke.
31. **Cert & tunnel status** — WebTransport cert hash, expiry countdown, rotation
    history; tunnel public URL health (green/red + last-check time).
32. **Audit log viewer** — from the provenance chain: anchored entries (ts, pubkey,
    hash prefix), verify-history results, publish events. Ties into Provenance.
33. **Security posture panel** — which endpoints require WebAuthn vs open, auth mode
    banner (MYCELIUM_GATEWAY_AUTH on/off), last auth events.

## Part 6 — Polish, wow, mobile

34. **PWA** — manifest.json + service worker (cache shell, offline fallback),
    installable from phone; badge for alert counts. Include manifest/sw in the
    esbuild dist output (dist/ is committed).
35. **WebGPU trace-particle field** — upgrade the existing animated background: each
    particle = a live trace, colored by agent, collision flash on errors, click
    nearest particle → drawer. Keep the canvas fallback when WebGPU is absent.
36. **Kiosk/wall mode** — ?kiosk=1: hides nav, auto-rotates Home → Live → Graph every
    30s. For a TV/monitor.
37. **Theme + density** — light/dark/auto toggle (persisted), compact-rows toggle,
    font-size scale.
38. **Keyboard shortcuts** — / focus search, 1-9 switch views, g then letter
    (GitHub-style), ? cheat-sheet overlay.
39. **Shareable views** — "copy link to this exact state" (route + filters encoded);
    "export report" = one-page printable summary of the current view.
40. **Empty-state guidance everywhere** — every empty table explains why ("no live
    verdicts yet — PAPER mode needs 2 debate rounds") and what to do next.

## Part 3′ — NEW gateway endpoints to add (contracts)

Add to gateway/main.go (then go build + restart). All JSON, follow the existing auth
pattern:
- GET /api/stats/timeseries?range=24h|7d|30d&bucket=1h → { buckets: [{ ts,
  traces: { by_kind: {...}, total }, findings: { open, applied, dismissed },
  mine_runs }] }
- GET /api/agents → [{ name, trace_count, last_seen, error_rate, kinds: {...} }]
  (GROUP BY agent over the trace table)
- GET /api/alerts → { configs: [...], active: [{ id, severity, message, ts, acked }] }
  ; POST /api/alerts/{id}/ack
- GET /api/logs?lines=200&level=warn → { lines: [{ ts, level, msg }] } (tail captured
  gateway stderr/stdout)
- GET /api/skills → [{ name, path, mtime, size, source_finding }] (scan
  generated-skills/)
- GET /api/wallet/{addr} → { addr, roles, tags, buys: [...], volume, distinct_tokens,
  edge, clusters, findings: [...], last_seen }
- GET /api/token/{addr} → { symbol, holders_top, whale_pct, buy_volume, roles_dist,
  findings, last_scan }
- GET /api/council/verdicts/{id} → { verdict, debates: [{ round, persona, direction,
  confidence, weight, rationale }], gates: [{ name, passed, note }] } (extend
  handleCouncilProxy to pass the id through)
- GET /api/vetoes → [{ ts, token, gate, reason, votes }]
- POST /api/prune { before_ts } → { deleted }
- GET /api/webauthn/credentials → [...]; POST /api/webauthn/register;
  DELETE /api/webauthn/credential/{id}
- Extend GET /api/status: + agents[], + storage { db_bytes, trace_count, oldest_ts,
  newest_ts, wal_bytes }, + tunnel { public_url, last_check, ok }, + uptime_secs,
  + last_requests (last 50 for the inspector)

Wire new live event types into the existing SSE stream: trace, finding, mine, alert,
wallet — so Home, Live wall, and Alerts update without polling.

## Build/deploy/verify (this phase REQUIRES the gateway rebuild)

1. cd ~/mycelium/web/dashboard && npm install && node esbuild.config.mjs (commit dist)
2. cd ~/mycelium/gateway && go build -o mycelium-gateway .
3. Restart the gateway from ~/mycelium/gateway via a background process (NOT nohup).
4. Verify: curl /api/status (contains agents[], storage, tunnel, uptime),
   /api/stats/timeseries?range=24h, /api/agents, /api/skills, /api/logs?lines=50 →
   all 200 with sane JSON; /web/ 200 with new main.js hash; public
   curl -s -o /dev/null -w "%{http_code}" http://2.25.70.156:8811/web/ → 200.
5. Browser sanity (if possible): each new view renders, no console errors, export
   downloads a CSV, kiosk mode cycles, PWA installable.
6. Commit + push to BOTH origin (github cryptonomicsed-byte/mycelium) and gitea
   (vantage/mycelium, token in ~/.hermes/credential_vault.json → gitea.token).
   Force-push gitea main if diverged.
7. Do NOT touch: Vantage repo, /opt/ares daemons, wallet_intel data, GMGN pool keys,
   council.db, the 8870 standalone dashboard.

## Prioritization
Build in this order: Part 0 → Part 1 → Part 3′ (endpoints) → Part 2 → Part 3 →
Part 4 → Part 5 → Part 6. If time-boxed: Part 6 items 34-38 are the most droppable;
Parts 0-2 and the endpoint work are load-bearing. Report back: which items shipped,
which were deferred and why, final commit SHA, verification output, and any
endpoint contracts you had to change from the spec above.

=====================================================================
PHASE 3 — SIGNAL FUSION ENGINE (best tokens to trade)
(from SIGNAL_FUSION_PROMPT.md)
=====================================================================
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