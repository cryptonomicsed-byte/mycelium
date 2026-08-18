#!/usr/bin/env python3
"""Patch collector.py: use gmgn_pool (rotating keys/proxies) for smartmoney+kol,
falling back to the CLI if the pool returns nothing."""
import re

path = "/opt/ares/wallet_intel/collector.py"
src = open(path).read()

old = '''def gmgn_trades(kind: str, conn) -> list:
    """gmgn-cli track smartmoney|kol --chain sol --limit 50 --raw -> normalized trades.
    Respects the IP ban cooldown: on RATE_LIMIT_BANNED, skip GMGN for 1h."""
    try:
        out = subprocess.run([GMGN, "track", kind, "--chain", "sol", "--limit", "50", "--raw"],
                             capture_output=True, text=True, timeout=60)
    except Exception as e:
        log(f"  [gmgn] {kind} exec error: {e}")
        return []'''
new = '''def gmgn_trades(kind: str, conn) -> list:
    """smartmoney|kol trades. PRIMARY: gmgn_pool direct API (rotating keys +
    proxies, per-key cooldown). FALLBACK: gmgn-cli (reads ~/.config/gmgn/.env).
    Respects the IP ban cooldown: on RATE_LIMIT_BANNED, skip GMGN."""
    try:
        import gmgn_pool as _pool
        raw = _pool.smartmoney_trades(50) if kind == "smartmoney" else _pool.kol_trades(50)
        if raw:
            return _pool.normalize_trades(raw, kind)
    except Exception as e:
        log(f"  [gmgn] pool {kind} error: {e}")
    try:
        out = subprocess.run([GMGN, "track", kind, "--chain", "sol", "--limit", "50", "--raw"],
                             capture_output=True, text=True, timeout=60)
    except Exception as e:
        log(f"  [gmgn] {kind} exec error: {e}")
        return []'''
assert src.count(old) == 1, f'anchor count {src.count(old)}'
src = src.replace(old, new)
open(path, "w").write(src)
print("collector.py patched: gmgn_trades -> pool-first")
