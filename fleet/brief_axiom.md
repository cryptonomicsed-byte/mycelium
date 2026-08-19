# PANEL BRIEF — AXIOM (whale / KOL / influencer intelligence)

## Mission
Build the ULTIMATE whale + KOL + influencer tracking layer for the Ares
ecosystem. You are the WHALE INTELLIGENCE panel. Your output feeds the
fusion engine's S_wallet component and the council's debate.

## Verified facts (from orchestrator readback 2026-08-19)
- AXIOM lives at /opt/ares/axiom (node server.mjs, started by
  /opt/ares/start_axiom.sh; gitea repo gh-cryptonomicsed-byte/Axiom,
  branch main — /opt/ares/axiom has NO .git, clone the gitea repo and
  work there, then deploy to /opt/ares/axiom).
- Data sources available:
  * GMGN smart money + KOL trades: gmgn-cli track smartmoney/kol --raw
    (6-key pool, proxies via gmgn_cli_proxy.py — VPS IP gets per-IP bans)
  * Mycelium wallet_intel.db (/opt/ares/wallet_intel/) — 149k wallets,
    token_stats, wallet_tokens; scanner harvests top holders/traders with
    role tags (deployer, top_holder, top_trader, first_buyer)
  * Vantage alpha_wallets + wallet_reputation tables
    (/opt/ares/Vantage/data/vantage.db)
  * DexScreener public API (no key)
  * Solscan: DORMANT/paywalled — do not build on it
- The Ares stack is on THIS host (2.25.70.156). wallet_intel daemon runs
  on a 5-min cycle; scanner every 10 min.

## Required work (pick the highest-value, verify, ship)
1. BUILD a KOL/whale watchlist: derive it from wallet_intel.db roles +
  Vantage reputation + GMGN kol track. Persist as a ranked list
  (e.g. /opt/ares/axiom/data/watchlist.json) with conviction scores.
2. ADD whale-cluster detection: when >= N watchlist wallets buy the same
  token within a window, emit a signal (JSON event) consumable by
  signal_fusion or the council. Look at wallet_intel.db wallet_tokens
  for recent buy clusters.
3. ADD KOL social amplification: if you can cheaply map KOL wallet
  addresses to X/Twitter handles (GMGN kol track output usually includes
  handle), store handle->address. Optional: use xurl skill if creds exist
  (check ~/.hermes/credential_vault.json for x/telegram keys).
4. Expose the watchlist + signals over AXIOM's server (server.mjs) or a
  small HTTP endpoint on :PORT so the dashboard/picks can consume.
5. Write a README documenting the watchlist derivation and signal schema.

## Rules
- Work on the Axiom gitea clone, branch: axiom-whale-intel. Commit +
  push to your branch. Deploy to /opt/ares/axiom after verified.
- Other panels own: Loom (signals), Vantage (council), signal_fusion
  (picks). Read their outputs freely; do not edit their code.
- PAPER only. No real trades.
- Never print full API keys in logs or commits.
- Verify: node server boots (node server.mjs smoke test), watchlist
  builds from real DB data, signals emit real events.

## Report back (end your session with this)
- Watchlist size + top 10 entries (anonymized address prefix + tag)
- Signal schema + example event
- Deployed endpoint/port if added
- Commits pushed (SHAs)
- Top 3 recommended next enhancements
