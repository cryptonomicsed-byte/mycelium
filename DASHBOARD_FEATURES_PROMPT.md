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
