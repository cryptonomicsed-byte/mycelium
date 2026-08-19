# MYCELIUM — Stigmergic Substrate for Agents

> "AI can create things we haven't thought of, and see patterns we haven't
> noticed." Mycelium is the substrate where agents leave traces and the
> network learns — coordination without conversation, intelligence without
> a central brain.

## What it is

In biology, **stigmergy** is how ants coordinate: each ant leaves pheromone
traces in the shared environment; no ant talks to another, yet the colony
builds, forages, and adapts. Agents today have no equivalent — every tool
call, decision, and failure evaporates into per-session logs no other agent
reads. Mycelium is that missing shared environment:

```
  AGENTS (Hermes, Codex, herdr panels, ...)
    │  emit traces via MCP tools (mycelium.trace)
    ▼
  ┌──────────────────────────────────────────────┐
  │  SUBSTRATE (SQLite, schema v1, versioned)    │  ← the pheromone trail
  └──────────────────────────────────────────────┘
    │
    ▼  sandboxed MINER agents (hot-swappable, registry)
  recurring_workflow · anomaly · cross_agent · opportunity
    │
    ▼
  FINDINGS (evidence + confidence + suggestion)
    │
    ▼  apply_finding (self-improvement loop)
  generated-skills/*/SKILL.md  →  hot-swappable capabilities
```

The loop closes itself: agents behave → traces accumulate → miners find
patterns no single agent could see (recurring workflows, failure bursts,
cross-agent correlations, automation ROI) → findings are applied as new
skills → future agents are more capable. **No human in the loop.**

## Why it disturbs the AI space (in a good way)

1. **Designed for agents, from the ground up.** Every surface is MCP. The
   CLI is a debugging mirror, not the product.
2. **Sees patterns humans don't.** A human never notices "patch → grep"
   ran 14 times across 4 files. The substrate does, and turns it into a skill.
3. **Self-improving tool ecosystem.** Skills are born from observed
   behavior, hot-swapped without restart.
4. **Future-proof.** Event schema is versioned + extensible (payload JSON);
   storage is swappable (SQLite → Postgres/object store); miners are a
   registry (add one = register one); transport is MCP (rides the ecosystem).
5. **Real data, always.** Traces are real operations, never simulated.
   The bundled seed is this session's actual work.

## Quickstart

```bash
cd ~/mycelium
python3 -m mycelium.cli init                     # create substrate DB
python3 scripts/demo_seed.py --wipe              # real session traces
python3 -m mycelium.cli list --limit 5           # peek at the trail
python3 -m mycelium.cli mine --miner all         # run the miner swarm
python3 -m mycelium.cli findings                 # read what was discovered
python3 -m mycelium.cli apply <finding_id>       # auto-generate a skill
```

## MCP exposure (the primary surface)

`python3 -m mycelium.mcp_server` speaks MCP over stdio (JSON-RPC 2.0,
newline-delimited). Register in `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  mycelium:
    command: "python3"
    args: ["/data/data/com.termux/files/home/mycelium/mycelium/mcp_server.py"]
```

Tools (prefixed `mcp_mycelium_*` in Hermes): `trace`, `list_traces`, `mine`,
`list_findings`, `get_finding`, `apply_finding`.

The gateway also serves an agent-native dashboard at `/web/` (see "Dashboard"
below) — a live, filterable view over everything the tools above expose.
It isn't wrapped in its own MCP tool: it's a single, already-documented
static address, not an action an agent needs to invoke. `POST
/api/findings/{id}/dismiss`, added for the dashboard, is REST-only for now
(no MCP twin yet) since the dashboard is its only consumer so far.

The gateway's ops/observability endpoints (`/api/agents`, `/api/skills`,
`/api/alerts`, `/api/logs`, `/api/stats/timeseries`, `/api/prune`,
`/api/picks`, `/api/council/*`) are likewise REST-only, dashboard-only
surfaces — they expose gateway-internal state (in-memory log/request
rings, on-disk skills, the signal-fusion picks store) that has no MCP
analog to mirror, unlike the substrate primitives above.

## Polyglot architecture (v0.1 → v0.2 — v0.2 DELIVERED 2026-08-16)

| Layer | v0.1 (this build) | v0.2 (delivered) | Why |
|---|---|---|---|
| Orchestration | Python (stdlib-only) | Python | agent loops, miner logic |
| Substrate ingest | SQLite via stdlib | Go HTTP gateway (:8811, modernc.org/sqlite) | concurrency, fan-out |
| Provenance | — | Go Ed25519-signed hash chain + append-only anchor log (chain_state.jsonl); Rust verifier | tamper-evident trace chain, independent audit |
| Miners | Python registry | + subprocess sandbox (RLIMIT_DATA, timeout) | bounded execution |
| On-device mining | — | WebNN (origin-trial, roadmap) | private, offline pattern mining |
| Surface | CLI + MCP | + REST (/api/*) | agents first, humans spectators |
| Telemetry pipe | stdio | WebTransport (HTTP/3, roadmap) | real-time agent telemetry |

### Provenance design (tamper-evident chain)

- Every trace gets an envelope: index, trace_id, ts, action, target, outcome,
  payload_sha, prev_hash → SHA-256 → Ed25519 signature (gateway keypair at
  gateway/provenance_key.json).
- Anchor log (gateway/chain_state.jsonl) is append-only; the DB-derived chain
  must MATCH it exactly and only ever extend it. Divergence ⇒ tamper/corruption.
- Verify: GET /api/provenance/verify (Go) or the Rust verifier
  (`provenance/target/release/mycelium-provenance chain.json chain_state.jsonl`).
- External anchoring (publishing checkpoints to Gitea/object store) = v0.3.

### Tamper test (performed 2026-08-16)

```
clean:   chain valid: 67 envelopes verified (67 anchored)
tamper:  {"valid": false, "reason": "chain diverged from anchor log (tamper/corruption)"}
```

### Cron self-maintenance

`mycelium-cycle` cron job (every 30m, no_agent) runs
~/.hermes/scripts/mycelium_cycle.sh — a 4-stage pipeline:
1. `mycelium cycle`: trace → sandboxed mine (idempotent dedupe) → auto-apply
   findings with confidence >= 0.9
2. `a2a-publish --limit 1`: newest open finding → Vantage gossip feed
   (POST /api/agents/me/publish-event, channel "feed") so other agents see it
3. `alerts`: evaluate generated-alerts/*.json watchdogs; report NEW trips
   (diffed against .alerts_seen)
4. `publish`: local checkpoint + Gitea push (vantage/mycelium-anchors)

Watchdog semantics: silent unless new findings/applications, sandbox errors,
A2A failure, a new tripped alert, or a failed anchor push.

## Dashboard

`web/dashboard/` — a real, working dashboard at `/web/` (vanilla TypeScript
+ native Web Components, no framework runtime; `dist/` is committed since
the Termux box this ships to never runs a Node build step). Fourteen views,
hash-routed, several deep-linkable via a query string
(`#/traces?agent=council&kind=error`):

- **`#/live`** — SSE-fed live trace feed ("pheromone trail": colored by
  outcome, opacity decays with age), filterable by agent/kind/action/outcome.
  Holds a Screen Wake Lock while visible.
- **`#/traces`** — Trace Explorer: the full trace history (not just the
  live tail), with agent/kind/outcome filters synced to the URL hash, a
  client-side search box, a "load older" cursor (`before`, ts-descending,
  dedupe-by-id) alongside `#/live`'s `since` cursor, and a per-row JSON
  drawer for the raw envelope.
- **`#/findings`** — findings grouped by state, filterable by miner and
  confidence threshold, Apply/Dismiss wired to the gateway, each card now
  showing a confidence bar. A new finding ≥80% confidence vibrates the
  device (only while the tab is focused).
- **`#/loop`** — the self-improvement loop made visible: trace → finding →
  applied → skill counts as a pipeline strip, plus the join between applied
  findings and the actual on-disk `generated-skills/*/SKILL.md` they
  produced (by slug), so "did this finding really become a skill" is one
  glance, not a grep.
- **`#/provenance`** — the literal hash chain, link by link, with a broken
  link highlighted exactly where it diverges; falls back to the last
  known-good chain plus the divergence reason on tamper. Also shows the
  current WebTransport cert hash/expiry (`GET /api/webtransport/cert-hash`)
  and a session-local verify-history drawer (last 10 checks).
- **`#/wallets`** — dedicated tables for the `wallet_activity` /
  `wallet_correlation` / `wallet_anomaly` miners' payloads, plus a
  force-directed (d3-force) graph of co-buying wallet clusters. Web Share
  (clipboard fallback) on findings.
- **`#/miners`** — `GET /api/miners`, all 7 registered miners (including
  ones with zero findings yet) with a "what it detects" column, an
  Open/Applied/Dismissed badge breakdown per miner, and "force mine cycle" /
  "force WASM mine" buttons.
- **`#/council`** — the AI trading council's live state, proxied read-only
  from the VPS council daemon through the gateway
  (`gateway/main.go`'s `handleCouncilProxy`, `MYCELIUM_COUNCIL_BASE`):
  recent verdicts (direction, conviction, entry liquidity, PAPER/LIVE
  badge, full per-persona votes + rationale), a calibration table (each
  persona's hit rate → effective weight multiplier, veto personas flagged),
  a persona/gate explainer, and a substrate cross-section (council-side
  traces + findings). Export-to-CSV/JSON and "copy as curl" on every table.
- **`#/picks`** — the `ares-signal-fusion` engine's ranked top-10 output
  (`GET /api/picks`, proxied the same way as `#/council`): score, symbol,
  gate status, and an expandable rationale drawer showing the exact
  component breakdown (`S_signal`/`S_wallet`/`S_council`/`S_finding`/
  `S_market`, weights, presence flags, drivers) the score was computed
  from — see "Signal fusion & picks" below.
- **`#/alerts`** — `GET /api/alerts`, the same watchdog config
  `mycelium.cli alerts` evaluates, rendered as tripped/untripped rows with
  the configured thresholds shown alongside.
- **`#/agents`** — per-agent rollup (trace count, error rate, last-seen,
  a stale/dead health badge by inactivity threshold) derived from the
  trace table; clicking a row deep-links into `#/traces` pre-filtered to
  that agent.
- **`#/stats`** — hand-rolled canvas charts (no charting library) over
  `GET /api/stats/timeseries`: stacked trace volume by kind, findings by
  state over time.
- **`#/system`** — architecture-at-a-glance (static stack description),
  live gateway status cards (uptime, auth state, storage size), a prune
  control (deletes traces older than a cutoff, re-anchors the provenance
  chain, guarded by a confirm dialog), the last-N-requests inspector, and
  a tail of the in-memory log ring — all sourced from the gateway's own
  bounded in-memory rings (`gateway/ops.go`), no journald dependency
  (Termux has none).
- **`#/ondevice`** — the WebNN/CPU-fallback anomaly scorer from
  `web/webnn_miner.html`, now sharing its exact scoring code
  (`web/shared/webnn_score.js`) with this panel so the model can't drift
  between the two surfaces.

Live updates default to `GET /api/stream` (Server-Sent Events) — works
everywhere, degrades to reconnect-with-backoff, needs no new infra. The
status bar also offers an experimental toggle to switch to the
WebTransport pipe instead (`gateway/wt.go`, :8812; see "WebTransport live
pipe" below) — Chromium-only, and exclusive with SSE (never both at once).

**Self-improving UI, made concrete:** the dashboard traces its own usage
(`agent="dashboard-ui"` — viewed/applied/dismissed a finding, changed a
filter, forced a mine cycle) into the same substrate every other agent
writes to. Since `recurring_workflow` / `anomaly` / `cross_agent` /
`opportunity` already mine *any* traces regardless of source, this closes
the self-improvement loop with zero new miner code — those four miners
start finding patterns in dashboard usage for free.

Installable as a PWA (`manifest.json` + `sw.js`: cache-first shell,
stale-while-revalidate on `/api/*` GETs, SSE explicitly untouched —
"offline" shows in the UI rather than the worker faking a live stream).

## Auth (optional, opt-in)

The gateway has no auth model by default — same loopback-trust posture as
always. Set `MYCELIUM_GATEWAY_AUTH=1` to gate every `/api/*` route (except
`/api/auth/*` itself, and `/web/*` static files — the lock screen has to
load before login) behind a WebAuthn session. Single-user "pair this
device" model: no username/password, `POST /api/auth/register/begin` +
`/register/finish` register a new authenticator (any number of devices can
be paired), `/api/auth/login/begin` + `/login/finish` prove possession and
set a session cookie, `POST /api/auth/logout` clears it. The dashboard
shows a lock screen automatically on any 401 (`web/dashboard/src/auth.ts`,
`components/lock-screen.ts`).

Two things worth knowing:

- **The gateway's advertised hostname must be `localhost`, not an IP
  literal.** Chrome's WebAuthn implementation rejects an IP-literal RP ID
  outright (Firefox/Edge tolerate it) — `MYCELIUM_ADDR` (default
  `localhost:8811`) is what both the gateway and `mycelium.dashboard_url()`
  derive their URLs from for exactly this reason; visiting the gateway via
  `http://127.0.0.1:8811/` still works for everything else, just not the
  `/api/auth/*` ceremony endpoints (they 400 with a clear message pointing
  at the `localhost` URL instead of failing inside browser JS).
- **`gateway/wt.go`'s WebTransport pipe (`:8812`) is not covered by this
  flag.** It's a separate listener from the REST/SSE gateway (`:8811`);
  cookie-based sessions don't carry over QUIC, and building that bridge is
  out of scope for now — WT stays loopback-trust-only regardless of
  `MYCELIUM_GATEWAY_AUTH`.

Credentials persist to `gateway/webauthn_credentials.json` (0600,
gitignored, same pattern as `provenance_key.json`); sessions are in-memory
only (the gateway is a long-running process, manually restarted — losing
sessions on a restart is an acceptable rare inconvenience, not a gap).

## WebTransport live pipe (experimental)

`gateway/wt.go`'s QUIC/UDP pipe (`:8812`) originally only accepted inbound
telemetry from agents (`POST`-equivalent traces over streams/datagrams,
same `insertTrace` path as `POST /api/trace`). It now also pushes live
updates back out — one dedicated outbound uni-stream per session, carrying
the same `trace`/`finding`/`provenance` events as `GET /api/stream` (SSE),
on the identical tick cadence, via a shared `streamSink` interface so the
DB-polling logic isn't forked into two copies. Length-prefixed JSON
(4-byte big-endian length + body), not datagrams — QUIC datagrams are
capped by path MTU (~1200-1450 bytes), well under a real finding's
evidence text or a provenance snapshot, so datagrams would silently
truncate large payloads.

The dashboard's status-bar toggle (Chromium-only, feature-detected) opts
into this instead of SSE — never both at once, since concurrent push would
double-insert into the live trace buffer. `GET /api/webtransport/cert-hash`
exposes the current cert's SHA-256 + expiry for the browser to pin via
`serverCertificateHashes`; the dashboard fetches it fresh before every
connection attempt rather than caching it, since pinning is only checked
at connection establishment and the cert rotates in place. Speaking of
which: the cert is now issued for 13 days (under the 14-day
`serverCertificateHashes` cap, versus the original 10-year unrotated
cert), and an in-process hourly ticker regenerates it once it's within a
day of expiry — via `tls.Config.GetCertificate`, so the swap takes effect
on the next handshake without restarting the listener or dropping open
sessions.

This pipe is loopback-trust-only regardless of `MYCELIUM_GATEWAY_AUTH` (see
"Auth" above) — it's a separate listener, and cookie sessions don't carry
over QUIC. `gateway/cmd/wt-smoke` is the integration test: connects, pushes
a trace via stream and datagram, confirms both landed via the REST API,
and confirms a `provenance` event arrives on the new outbound broadcast
stream, all against a real QUIC connection.

## Immersive polish

Four additions, each feature-detected and gracefully absent rather than
broken when the underlying browser API isn't there:

- **Generative WebGPU background on `#/live`** (`src/shaders/`) — a
  domain-warped value-noise field rendered behind the trace feed, ridge-
  sharpened into thin glowing threads that reinforce the "pheromone
  trail"/mycelial-growth metaphor the trace rows themselves already carry.
  Dynamically imported only when `navigator.gpu` is present; falls back to
  the plain background otherwise. Respects `prefers-reduced-motion` (a
  single static frame, no animation loop).
- **Gyroscope tilt parallax**, feeding the same shader's `tilt` uniform —
  not literal tilt-to-navigate (accidental-navigation risk on a dashboard
  with a fixed nav bar), a parallax depth cue instead. Auto-attaches on
  Android/desktop; iOS 13+'s permission gate
  (`DeviceOrientationEvent.requestPermission()`) needs a tap, so an
  "Enable tilt parallax" button appears only there.
- **Spatial audio tamper alert** (`src/audio.ts`) — a synthesized
  descending tone (`OscillatorNode`, no audio asset files) panned
  left-to-right (`StereoPannerNode`) when the provenance badge flips to
  tampered. Gated behind an explicit "Enable sound alerts" click in the
  status bar — that's what actually unlocks `AudioContext` playback under
  browser autoplay policy, and an unannounced sound starting on its own
  would be bad behavior regardless.
- **WebXR/AR mode on `#/wallets`** (`src/ar/`) — "Enter AR" walks the
  operator into the same wallet-correlation cluster the 2D d3-force graph
  already renders, as glowing nodes/edges positioned in space via Three.js
  (a second justified dependency past d3-force: hand-rolling raw WebGL
  immersive-AR — reference spaces, frame-loop pose math — from scratch is
  real risk for one view). The button only ever appears once
  `navigator.xr.isSessionSupported('immersive-ar')` confirms it, and
  Three.js (~1.1 MB) is dynamically imported only on click — `src/ar/
  xr-detect.ts` is a deliberately separate, dependency-free module so the
  up-front feature-detect itself doesn't drag Three.js into every
  `#/wallets` page load. A caught real bug during verification: an earlier
  version put the feature-detect in the same file as the Three.js import,
  which pulled the whole 1.1 MB chunk into every load of `#/wallets`
  regardless of whether AR was ever used — confirmed via a network-request
  check in headless Chromium, not just by reading the code.

This project's dev sandbox has no AR-capable device and no functioning
WebGPU adapter (`navigator.gpu` is present but `requestAdapter()` returns
null — confirmed directly, not assumed), so the actual shader-renders and
AR-session-succeeds paths ship as spec-correct, typechecked code, unverified
end-to-end in this environment — the same bar the already-shipped WebNN
feature ships under. What *is* verified here, in real headless Chromium:
every fallback path (no canvas drawn when WebGPU is unsupported/adapter-less,
no AR button when `navigator.xr` is absent, the existing 2D wallet graph
completely unchanged either way), the spatial audio path end-to-end
(`AudioContext` unlock → tamper detection → alert plays, zero errors), the
gyroscope handler surviving a synthetic `DeviceOrientationEvent`, and the
code-splitting boundary itself (confirmed via network-request logging that
neither the shader nor Three.js chunk loads until its feature actually
activates).

## Signal fusion & picks

`signal_fusion/` (`ares-signal-fusion`) is a VPS-side Python daemon,
separate from the gateway/mycelium substrate, that fuses every available
intelligence source — the Vantage signal pool, classified wallet intel,
market snapshots, council verdicts, and Mycelium miner findings — into one
ranked, explainable list of the best tokens to trade. It writes only to its
own `ares_picks.db`; PAPER only, structurally — nothing in the module
executes trades. It implements `SIGNAL_FUSION_PROMPT.md` (repo root).

- **Composite score** = weighted average of five 0..1 normalized
  components (`S_signal`, `S_wallet`, `S_council`, `S_finding`, `S_market`),
  scaled to 0..100. Each component carries a `present` flag — a token with
  no council verdict or no findings isn't penalized for "no opinion" on
  those axes; the score normalizes over present components only
  (`score = 100 × Σ(value×weight | present) / Σ(weight | present)`), and
  the same `present` filter is used later by calibration so historical
  grouping stays consistent with how picks were actually scored.
- **Time decay** on every signal is `exp(-ln2 × age / half_life)` — chosen
  specifically so the decay factor is exactly 0.5 at `age == half_life`,
  keeping every score hand-recomputable from its stored component drivers
  (the transparency contract the spec requires).
- **Hard gates** (`gates.py`) run before scoring and are fail-closed: min
  liquidity, max top-10 holder share, max bundler/rat share, min volume,
  honeypot/tax checks, min token age, a 24h per-token dedupe, a
  configurable "Sabbath" quiet window, and a missing-market-snapshot veto
  (no data ⇒ no pick, never benefit-of-the-doubt).
- **Outcome tracking is real, not simulated**: entry price recorded per
  pick, later runs record marks at +4h/+24h/+7d; once ≥20 picks have
  resolved, `--report` compares average return per dominant component
  against the overall mean and suggests weight nudges — report only,
  `config.json` is never auto-edited.
- The dashboard's `#/picks` view and the gateway's `GET /api/picks` proxy
  are the only mycelium-side integration points; see `signal_fusion/README.md`
  for the VPS deploy steps (systemd unit, config, manual verification
  commands) — none of which run inside this repo's own CI/dev flow.

## Roadmap

- [x] v0.1 substrate + 4 miners + MCP server + skill self-generation
- [x] v0.2 Go gateway (:8811, REST + provenance), Ed25519 hash chain + anchor log
- [x] v0.2 Rust provenance verifier (independent audit surface)
- [x] v0.2 sandboxed miners (subprocess, RLIMIT_DATA, timeout) + cron cycle
- [x] v0.3 Go WebTransport telemetry pipe (:8812, QUIC/UDP) + Postgres backend
      (MYCELIUM_BACKEND=postgres, server on :5433, psycopg 3)
- [x] v0.3 external anchor publishing (Gitea vantage/mycelium-anchors, creds in vault)
- [x] v0.3 Wasm-sandboxed miners (wasip1 + wazero, POST /api/mine/wasm)
- [x] v0.3 WebNN on-device miner (web/webnn_miner.html, CPU fallback; needs
      browser with WebNN origin trial)
- [x] v0.3 alert/config_fix suggestions wired (watchdogs, patch drafts)
- [x] v0.3 A2A: findings feed into agent negotiation (Vantage feed, gossip channel)
- [x] v0.4 real dashboard (web/dashboard/): live/findings/provenance/wallets/
      miners/ondevice views, SSE live updates, self-improving-UI trace loop, PWA
- [x] v0.5 mycelium.dismiss_finding / mycelium.dashboard_url MCP tools
- [x] v0.5 optional WebAuthn gateway auth (MYCELIUM_GATEWAY_AUTH=1)
- [x] v0.5 WebTransport live-push (outbound broadcast, rotating cert,
      dashboard toggle) -- the :8812 pipe now pushes updates, not just
      ingests telemetry
- [x] v0.5 immersive polish: WebGPU shader background + gyroscope
      parallax (#/live), spatial audio tamper alert, WebXR/AR wallet
      graph (#/wallets, Three.js)
- [x] v0.6 dashboard restore/expansion: Trace Explorer, Self-improvement
      Loop, Alerts, Agents, Stats, System views; council verdicts/
      calibration/substrate proxy (#/council); gateway ops surface
      (agents/skills/alerts/logs/stats/prune) with bounded in-memory rings
- [x] v0.6 ares-signal-fusion: composite scoring engine (5 weighted
      components, presence-aware normalization, half-life decay, 7 hard
      gates, outcome tracking + self-calibration report) and the
      dashboard's #/picks view (GET /api/picks proxy)

## Layout

```
mycelium/
├── mycelium/
│   ├── __init__.py      version
│   ├── core.py          substrate: events, SQLite, findings (dedupe)
│   ├── miners.py        registry of pattern miners (hot-swappable)
│   ├── sandbox.py       subprocess miner isolation (rlimits + timeout)
│   ├── apply.py         finding → SKILL.md self-improvement
│   ├── cli.py           CLI mirror (+ `cycle` for cron)
│   └── mcp_server.py    MCP stdio server (primary surface)
├── gateway/             Go: HTTP API :8811 + provenance (main.go, binary)
│   ├── stream.go             SSE broadcaster (/api/stream) for the dashboard
│   ├── auth.go               optional WebAuthn auth gate (MYCELIUM_GATEWAY_AUTH=1)
│   ├── ops.go                 log/request rings, agents/skills/alerts/stats/prune,
│   │                          /api/council/* + /api/picks proxies (handleCouncilProxy,
│   │                          handlePicksProxy)
│   ├── main_test.go          gateway handler tests (temp DB, no subprocess mocking)
│   ├── ops_test.go           ops.go handler tests (rings, prune re-anchor, proxies)
│   ├── auth_test.go          session/ceremony/middleware tests (no browser needed)
│   ├── provenance_key.json   Ed25519 keypair (0600)
│   ├── webauthn_credentials.json   paired-device public keys (0600), auth-only
│   └── chain_state.jsonl     append-only anchor log
├── provenance/          Rust verifier (cargo build --release)
├── signal_fusion/       ares-signal-fusion: VPS-side pick engine (see
│   │                    "Signal fusion & picks" above), stdlib-only Python
│   ├── signal_fusion.py       main loop: --once / --daemon / --report, SIGHUP reload
│   ├── sources.py             signal normalizers + defensive live fetchers
│   ├── scoring.py             composite_score: 5 weighted components, decay
│   ├── gates.py                7 hard vetoes, fail-closed
│   ├── store.py                ares_picks.db: picks/outcomes/vetoes, calibration
│   ├── backtest.py             replay-over-history / forward-PAPER-tracking
│   ├── config.json             all weights/thresholds/half-lives, hot-reloaded
│   └── ares-signal-fusion.service   systemd unit (VPS deploy)
├── web/
│   ├── webnn_miner.html      standalone WebNN debug harness (zero build step)
│   ├── shared/webnn_score.js MLP scoring, shared by webnn_miner.html + #/ondevice
│   └── dashboard/             the dashboard (see "Dashboard" above)
│       ├── src/                TypeScript source (views/, components/, shaders/, ar/, ...)
│       ├── dist/                esbuild output, committed (no on-device Node build)
│       ├── index.html, manifest.json, sw.js, icons/
│       └── package.json, tsconfig.json, esbuild.config.mjs
├── scripts/
│   ├── demo_seed.py     REAL session traces
│   └── cron_cycle.sh    watchdog cycle (installed at ~/.hermes/scripts/)
├── tests/test_core.py, test_mcp_server.py, test_signal_fusion.py   E2E sanity (stdlib unittest)
├── AGENT_WORK_PACKAGE.md   master spec: dashboard restore/expansion + signal fusion
├── chain.json           provenance export
└── generated-skills/    skills born from discovered patterns
```
