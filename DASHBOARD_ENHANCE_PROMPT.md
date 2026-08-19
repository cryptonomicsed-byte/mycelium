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
