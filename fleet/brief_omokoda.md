# PANEL BRIEF — OMOKODA (ecosystem platform / agent-native surface)

## Mission
Enhance the Omokoda2 platform as the agent-native front door of the Ares
trading ecosystem. You are the PLATFORM panel. Make the intelligence
(signals, whales, council verdicts) consumable by agents AND humans.

## Verified facts (from orchestrator readback 2026-08-19)
- Omo-Koda2 lives at /opt/ares/Omo-Koda2 (no .git — check gitea for an
  omokoda repo; if absent, init a repo and push to gitea under
  gh-cryptonomicsed-byte/Omo-Koda2 on branch omokoda-platform).
- Ecosystem services on this host (2.25.70.156):
  * Vantage council :8001 (X-Agent-Key auth, key at /root/.vantage_key)
    — /api/council/overview, /verdicts, /calibration
  * ares-signal-fusion picks sidecar :8003 — /api/picks
  * ares-poolhealth :8004 — /api/poolhealth (key pools + proxy state)
  * wallet_intel DB at /opt/ares/wallet_intel/wallet_intel.db
  * Mycelium gateway serves the dashboard (Fold4-hosted) with /api/*
- The user's philosophy: agent-native architecture — every module should
  expose REST endpoints / MCP tools / web surfaces. Reuse existing infra
  (Vantage pool, gmgn_pool, signal fusion, council).

## Required work (pick the highest-value, verify, ship)
1. INVENTORY Omo-Koda2: what it currently is (read README/docs, list
   routes/entrypoints). Write an architecture summary.
2. BUILD an ecosystem status/aggregator surface: one page or endpoint
   that shows live state across the playground — council verdicts,
   top picks, pool health, wallet-intel stats. Options:
   a. If Omo-Koda2 is a web app: add a /status or /ecosystem route that
      proxies/aggregates :8001/:8003/:8004 + wallet_intel.db stats.
   b. If it's agent-facing: add an MCP tool or REST endpoint that
      returns the same aggregation as JSON.
3. ADD an agent-ready intake: e.g. an endpoint that accepts a token
   address and returns the full intelligence bundle (picks score,
   council verdict, whale activity from wallet_intel.db, pool health)
   in one call — the "ask Omokoda about token X" surface.
4. Document the new surface in README (curl examples).

## Rules
- Work in /opt/ares/Omo-Koda2 (init git if needed). Branch:
  omokoda-platform. Commit + push to your branch on gitea.
- Do NOT edit: /opt/ares/Loom, /opt/ares/axiom, /opt/ares/Vantage,
  wallet_intel, signal_fusion, gateway. Read their APIs/DBs freely.
- Reuse existing endpoints — the ecosystem already exposes a lot; your
  job is aggregation + surface, not duplication.
- PAPER only. No real trades.
- Never print full API keys in logs or commits.
- Verify: your new route/endpoint returns real live data (curl it), not
  stubs.

## Report back (end your session with this)
- Architecture summary (what Omo-Koda2 is)
- What you built + how to call it (curl examples)
- Live verification output
- Commits pushed (SHAs)
- Top 3 recommended next enhancements
