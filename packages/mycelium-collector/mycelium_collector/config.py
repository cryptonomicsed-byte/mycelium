"""All configuration for mycelium-collector, in one place, all env-driven.

Was hardcoded to a single box's layout in the original phone-native
version: BASE = "/opt/ares/wallet_intel", and a "helius" source that
opened /opt/ares/Vantage/data/vantage.db directly as a SQLite file --
which only ever worked when this process happened to be co-located with
that exact Vantage instance on the same filesystem. Neither survives
being installed on a different device. Every path/URL/credential below
is env-configurable with a working local-file default, same pattern as
mycelium-substrate.
"""
import os

# Local registry (dedupe + wallet/token stats) — this collector's own
# small state, not the substrate itself.
DATA_DIR = os.environ.get("MYCELIUM_COLLECTOR_DIR", os.path.expanduser("~/.mycelium-collector"))
DB_PATH = os.environ.get("WALLET_INTEL_DB", os.path.join(DATA_DIR, "wallet_intel.db"))
LOG_PATH = os.environ.get("WALLET_INTEL_LOG", os.path.join(DATA_DIR, "wallet_intel.log"))
PID_PATH = os.environ.get("WALLET_INTEL_PID", os.path.join(DATA_DIR, "wallet_intel.pid"))

# Where traces get emitted -- the substrate's HTTP gateway, local or remote.
# Point this at a Contabo/hostinger substrate to collect from anywhere
# without running your own substrate DB.
MYCELIUM_URL = os.environ.get("MYCELIUM_URL", "http://127.0.0.1:8811")

# GMGN source: plain `gmgn-cli` subprocess, found on PATH by default.
# The phone-native version additionally imported undeclared sibling
# modules (gmgn_pool, gmgn_cli_proxy, scanner) for key-rotation/proxy
# rate-limit evasion that live outside this repo and were never part of
# the public U1-U5 spec -- not ported. Advanced users can still get that
# behavior: GMGN_CLI can point at any wrapper script that speaks the same
# `gmgn-cli track <kind> --chain sol --limit N --raw` interface.
GMGN_CLI = os.environ.get("GMGN_CLI", "gmgn-cli")
GMGN_ENABLED = os.environ.get("GMGN_ENABLED", "1") not in ("0", "false", "False", "")

# Helius source: a REAL Helius Enhanced Transactions API pull for a
# configured wallet watch-list -- replaces the old direct SQLite read of
# a co-located Vantage instance's wallet_trades table, which only ever
# worked by filesystem coincidence and is exactly the kind of coupling
# "universal tool" rules out.
HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY", "")
HELIUS_ENABLED = bool(HELIUS_API_KEY)
# Comma-separated wallet addresses, and/or a newline-delimited file of
# addresses (one per line, # comments allowed) -- both are merged if set.
TRACKED_WALLETS = [w.strip() for w in os.environ.get("TRACKED_WALLETS", "").split(",") if w.strip()]
TRACKED_WALLETS_FILE = os.environ.get("TRACKED_WALLETS_FILE", "")

# GeckoTerminal: price/symbol enrichment, no credentials needed.
GECKOTERMINAL_URL = os.environ.get(
    "GECKOTERMINAL_URL",
    "https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}",
)

PRICE_LOOKUP_CAP = int(os.environ.get("WALLET_INTEL_PRICE_LOOKUP_CAP", "20"))
EDGE_LOOKUP_AGE_S = int(os.environ.get("WALLET_INTEL_EDGE_LOOKUP_AGE_S", str(6 * 3600)))

os.makedirs(DATA_DIR, exist_ok=True)


def tracked_wallets() -> list:
    wallets = list(TRACKED_WALLETS)
    if TRACKED_WALLETS_FILE and os.path.exists(TRACKED_WALLETS_FILE):
        with open(TRACKED_WALLETS_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    wallets.append(line)
    # de-dupe, preserve order
    seen = set()
    out = []
    for w in wallets:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out
