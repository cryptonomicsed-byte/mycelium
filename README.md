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
the Termux box this ships to never runs a Node build step). Six views,
hash-routed:

- **`#/live`** — SSE-fed live trace feed ("pheromone trail": colored by
  outcome, opacity decays with age), filterable by agent/kind/action/outcome.
  Holds a Screen Wake Lock while visible.
- **`#/findings`** — findings grouped by state, filterable by miner and
  confidence threshold, Apply/Dismiss wired to the gateway. A new finding
  ≥80% confidence vibrates the device (only while the tab is focused).
- **`#/provenance`** — the literal hash chain, link by link, with a broken
  link highlighted exactly where it diverges; falls back to the last
  known-good chain plus the divergence reason on tamper.
- **`#/wallets`** — dedicated tables for the `wallet_activity` /
  `wallet_correlation` / `wallet_anomaly` miners' payloads, plus a
  force-directed (d3-force) graph of co-buying wallet clusters. Web Share
  (clipboard fallback) on findings.
- **`#/miners`** — `GET /api/miners`, all 7 registered miners (including
  ones with zero findings yet), with "force mine cycle" / "force WASM mine"
  buttons.
- **`#/ondevice`** — the WebNN/CPU-fallback anomaly scorer from
  `web/webnn_miner.html`, now sharing its exact scoring code
  (`web/shared/webnn_score.js`) with this panel so the model can't drift
  between the two surfaces.

Live updates ride a new `GET /api/stream` (Server-Sent Events) rather than
the existing WebTransport pipe (`gateway/wt.go`, :8812) — WebTransport
needs a browser-trusted cert, and `serverCertificateHashes` pinning (the
only way to trust a self-signed one) caps validity at 14 days against a
cert issued for 10 years with no rotation story. SSE works everywhere,
degrades to reconnect-with-backoff, and needed no new infra.

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
│   ├── main_test.go          gateway handler tests (temp DB, no subprocess mocking)
│   ├── auth_test.go          session/ceremony/middleware tests (no browser needed)
│   ├── provenance_key.json   Ed25519 keypair (0600)
│   ├── webauthn_credentials.json   paired-device public keys (0600), auth-only
│   └── chain_state.jsonl     append-only anchor log
├── provenance/          Rust verifier (cargo build --release)
├── web/
│   ├── webnn_miner.html      standalone WebNN debug harness (zero build step)
│   ├── shared/webnn_score.js MLP scoring, shared by webnn_miner.html + #/ondevice
│   └── dashboard/             the dashboard (see "Dashboard" above)
│       ├── src/                TypeScript source (views/, components/, ...)
│       ├── dist/                esbuild output, committed (no on-device Node build)
│       ├── index.html, manifest.json, sw.js, icons/
│       └── package.json, tsconfig.json, esbuild.config.mjs
├── scripts/
│   ├── demo_seed.py     REAL session traces
│   └── cron_cycle.sh    watchdog cycle (installed at ~/.hermes/scripts/)
├── tests/test_core.py, test_mcp_server.py   E2E sanity (stdlib unittest)
├── chain.json           provenance export
└── generated-skills/    skills born from discovered patterns
```
