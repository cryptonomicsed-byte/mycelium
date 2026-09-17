"""agent_birth_provisioner.py — Credential provisioning at agent birth (Phase 9.4).

Called once at agent birth. Returns instantly with TIER 0 keys (derived
from identity, no signup required), then kicks off an async background
task (TIER 1) that provisions account-farm credentials for configured
services.

Tier model:
  TIER 0 — instantly available, derived from vault: Nostr identity, Sui wallet,
            ETH wallet, SOL wallet. No network calls, no signups, no blocking.
  TIER 1 — async background provisioning via account_farm.signup_async().
            Non-blocking: agent birth returns before these complete.
            Results land in account_farm's credential store and can be
            retrieved later via account_farm.get_credentials().

Usage:
    bundle = await provision_at_birth(agent_id, vault_data)
    # bundle.tier0 keys are immediately usable
    # bundle.tier1_task runs in the background (asyncio.Task or None)
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Comma-separated list of account-farm services to provision at birth.
# Default: all Priority 1 services (critical APIs for agent function).
# Set AGENT_BIRTH_TIER1_SERVICES=none to skip background provisioning.
_DEFAULT_TIER1_SERVICES = "huggingface,tavily,serper"
_TIER1_SERVICES: list[str] = [
    s.strip()
    for s in os.environ.get("AGENT_BIRTH_TIER1_SERVICES", _DEFAULT_TIER1_SERVICES).split(",")
    if s.strip() and s.strip().lower() != "none"
]


@dataclass
class BirthCredentialBundle:
    """Result of provision_at_birth().

    tier0: dict of immediately-available identity credentials derived from
           the agent's vault (Nostr keys, wallet addresses, etc.)
           NEVER includes raw private keys — only public-facing addresses
           and protocol-level identifiers.

    tier1_task: asyncio.Task running background account provisioning,
                or None if AGENT_BIRTH_TIER1_SERVICES=none.
    """
    tier0: dict[str, Any]
    tier1_task: Optional[asyncio.Task] = field(default=None, repr=False)


def extract_tier0_from_vault(vault_data: dict) -> dict[str, Any]:
    """Extract TIER 0 credentials from the agent's birth vault data.

    These are derived from existing identity material — no network calls,
    no signups, no latency. Returns public-facing identifiers only.

    vault_data is the dict returned by Omo-Koda2's /v1/birth endpoint or
    equivalent identity derivation. Field names match the BIPON39/sovereign
    key structure.
    """
    tier0: dict[str, Any] = {}

    # Nostr identity
    nostr = vault_data.get("nostr") or vault_data.get("keys", {}).get("nostr", {})
    if nostr:
        tier0["nostr_npub"] = nostr.get("npub") or nostr.get("public_key") or ""
        # Never include nsec/private_key in tier0

    # Sui wallet (public address only)
    sui = vault_data.get("sui") or vault_data.get("keys", {}).get("sui", {})
    if sui:
        tier0["sui_address"] = sui.get("address") or sui.get("public_key") or ""

    # ETH / EVM wallet (public address only)
    eth = (
        vault_data.get("eth")
        or vault_data.get("ethereum")
        or vault_data.get("keys", {}).get("bip44", {}).get("ethereum", {})
    )
    if eth:
        tier0["eth_address"] = eth.get("address") or eth.get("public_key_hex") or ""

    # Solana wallet (public key only)
    sol = (
        vault_data.get("sol")
        or vault_data.get("solana")
        or vault_data.get("keys", {}).get("bip44", {}).get("solana", {})
    )
    if sol:
        tier0["sol_address"] = sol.get("address") or sol.get("public_key") or ""

    # BIPON39 mnemonic phrase (this is a public identifier, not a secret)
    bipon39 = vault_data.get("bipon39_phrase") or vault_data.get("phrase") or ""
    if bipon39:
        tier0["bipon39_phrase"] = bipon39

    # Odù index
    odu = vault_data.get("odu_index") or vault_data.get("odu") or 0
    if odu:
        tier0["odu_index"] = int(odu)

    return tier0


async def _provision_tier1_async(agent_id: str) -> dict[str, Any]:
    """Background coroutine: sign up for TIER 1 services via account_farm.

    Runs non-blocking after birth. Results stored in account_farm's
    credential store (CREDENTIALS_FILE). Errors are logged, never raised
    (agent birth must never fail due to background provisioning).
    """
    if not _TIER1_SERVICES:
        logger.debug("agent_birth_provisioner: TIER 1 provisioning disabled (empty service list)")
        return {}

    results: dict[str, Any] = {}

    try:
        from account_farm import signup_async, SITE_PROFILES
    except ImportError:
        try:
            import sys, os as _os
            sys.path.insert(0, _os.path.dirname(__file__))
            from account_farm import signup_async, SITE_PROFILES
        except ImportError:
            logger.warning("agent_birth_provisioner: account_farm not importable; TIER 1 skipped")
            return {}

    for service in _TIER1_SERVICES:
        if service not in SITE_PROFILES:
            logger.debug("agent_birth_provisioner: skipping unknown service %r", service)
            continue
        try:
            reason = (
                f"Sovereign agent {agent_id!r} birth provisioning — "
                f"autonomous tool access for {service}"
            )
            result = await signup_async(service, agent_id, reason)
            results[service] = result
            if result.get("error"):
                logger.info(
                    "agent_birth_provisioner: %s provisioning for agent %s: %s",
                    service, agent_id, result["error"],
                )
            else:
                logger.info(
                    "agent_birth_provisioner: %s provisioned for agent %s",
                    service, agent_id,
                )
        except Exception as exc:
            logger.warning(
                "agent_birth_provisioner: unhandled error provisioning %s for %s: %s",
                service, agent_id, exc,
            )
            results[service] = {"error": str(exc)}

    return results


async def provision_at_birth(
    agent_id: str,
    vault_data: dict,
) -> BirthCredentialBundle:
    """Called at agent birth. Returns immediately with TIER 0 credentials.

    TIER 1 (account signups) runs as a background asyncio.Task and does
    not block the birth response. The task is returned in the bundle so
    callers can await it if they want to wait, but they never need to.

    Args:
        agent_id: The agent's canonical name/id (used as the farm's agent
                  identifier for rate limiting and audit logging).
        vault_data: The raw vault dict from birth (Omo-Koda2 /v1/birth
                    output or equivalent). Only public-facing fields are
                    extracted; private keys are never touched or forwarded.

    Returns:
        BirthCredentialBundle with tier0 (instant) and tier1_task (async).
    """
    # TIER 0: instant, derived from vault, never blocks
    tier0 = extract_tier0_from_vault(vault_data)
    logger.info(
        "agent_birth_provisioner: TIER 0 complete for agent %s (%d keys)",
        agent_id, len(tier0),
    )

    # TIER 1: async background provisioning
    tier1_task: Optional[asyncio.Task] = None
    if _TIER1_SERVICES:
        try:
            tier1_task = asyncio.create_task(
                _provision_tier1_async(agent_id),
                name=f"birth_tier1_{agent_id}",
            )
            logger.info(
                "agent_birth_provisioner: TIER 1 task launched for agent %s (services: %s)",
                agent_id, ", ".join(_TIER1_SERVICES),
            )
        except RuntimeError:
            # No running event loop (e.g., called in a sync context / test)
            logger.debug("agent_birth_provisioner: no event loop for TIER 1 task — skipping")

    return BirthCredentialBundle(tier0=tier0, tier1_task=tier1_task)
