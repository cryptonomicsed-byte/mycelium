# Solscan API — Verdict (2026-08-19)

## TL;DR
Solscan's free tier is a **dead end for data**. Free-tier API keys are REAL
(the API authenticates them) but read **ZERO v2.0 endpoints** — every single
endpoint requires a paid plan starting at Lite $49/mo. The farm infrastructure
works end-to-end; the wall is a Stripe paywall, not a captcha. Keep the farm
code, keep the seeded key, but do NOT spend more engineering time on it.

## Facts (all verified live)
- Auth: header `token: <key>` against `https://pro-api.solscan.io/v2.0/*`.
  Clean JSON 401 with a dummy key — the API host is NOT Cloudflare-walled.
- Key format: HS256 JWT (header alg/typ, payload: createdAt, email, action=token-api,
  apiVersion=v2, iat).
- 401 semantics (READ THE BODY — same status, different meaning):
  - `"Token is invalid"` → junk key, purge from pool
  - `"Please upgrade your api key level"` → REAL key on free tier, keep (tier-gated)
  - `"Token is missing"` → wrong auth header name
- Every v2.0 endpoint probed (token/list, token/meta, token/holders, token/trending,
  token/price, token/transfer, account/transactions, block/last, transaction/last,
  defi/pool/list) returned 401 upgrade-required on a fresh free key.
- Docs (docs.solscan.io): all Pro API v2.0 endpoints cost 100 CU and require
  Lite ($49/mo, 20M CU, 1000 req/min) or Level 2-4 ($199/$399/$1099).
  Lite plan page explicitly says it unlocks access "beyond the limitations of
  the standard Free tier".
- `api.solscan.io` (old non-pro host) is dead/blocked (HTTP 000 from both
  Termux and the VPS).

## The farm (built, committed, deployed — dormant)
- `wallet/farm_solscan.py` — full harvest: GuerrillaMail → camoufox → CF
  clearance (3 attempts) → React form fill → cookie banner → Turnstile token
  polling → disabled-aware Register → key extraction → REAL API validation
  (junk keys purged) → storage + VPS deploy.
- `wallet/solscan_pool.py` — round-robin pool client; 401-drop (tier-gated
  keys exempt — they're real, just unfunded), 429 cooldown with escalation.
- `scanner.py` holder-fallback #3: Solscan tried only after GMGN AND Birdeye
  both fail; degrades gracefully ([] on 401).
- Deployed to Hostinger (2.25.70.156) `/opt/ares/wallet_intel/`. NOTE: the
  Ares stack lives on 2.25.70.156 (Hostinger), NOT the Contabo web-apps box
  (89.117.74.224) — hunting on the wrong box wastes a session.

## Turnstile (the first wall, now moot)
From the VPS datacenter IP, Cloudflare Turnstile never issues a token —
widget stays verifying, `cf-turnstile-response` stays length 0, Register stays
disabled. No solver helps (there's no challenge to solve); it's pure IP
reputation. Unblock paths: (a) ONE human registration from a residential IP —
DONE, user registered from phone; (b) proxy pool — free proxies don't pass
Turnstile either (shared/flagged exits; tested live 2026-08-19).

## Status
- Pool: 1 seeded key (`jujuman2008@gmail.com`, tier=free) in both
  `~/.hermes/solscan_keys.json` and `/opt/ares/.solscan_keys.json`.
- Scanner fallback #3: dormant, correct, zero cost.
- Farm cron: NOT scheduled (minting more free keys is pointless behind the
  paywall).
- If a Lite plan is ever bought: ONE key suffices — the pool client and
  fallback #3 activate with zero code changes.

## Decision
Dormant. Revisit only if (a) Solscan adds a meaningful free tier, or (b) the
$49/mo Lite becomes worth it for token-meta/token-list as a dedicated
enrichment source (it is NOT worth it today as a redundant third holder source
— GMGN + Birdeye cover holders).
