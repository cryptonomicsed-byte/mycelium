"""sandbox — run pattern miners in isolated subprocesses.

Each miner executes in its own process with a wall-clock timeout and a memory
cap (resource.setrlimit). A crashed or hostile miner cannot corrupt the
substrate or starve the host. Wasm-sandboxed plugins (v0.3) replace the
interpreter boundary with a compile boundary — same interface, stronger cage.
"""
from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
import tempfile
from typing import Any, Dict, List

MINER_TIMEOUT_S = int(os.environ.get("MYCELIUM_MINER_TIMEOUT", "30"))
MINER_MEM_MB = int(os.environ.get("MYCELIUM_MINER_MEM_MB", "256"))


def run_miner_sandboxed(name: str) -> Dict[str, Any]:
    """Run one miner in a subprocess with rlimits. Returns findings list."""
    code = (
        "import sys,json; sys.path.insert(0, sys.argv[1]);"
        "from mycelium import core, miners;"
        "out = miners.run_miner(sys.argv[2]);"
        "print(json.dumps(out))"
    )
    env = dict(os.environ)
    env["MYCELIUM_DB"] = env.get("MYCELIUM_DB", os.path.expanduser("~/mycelium/mycelium.db"))
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code, os.path.dirname(os.path.dirname(os.path.abspath(__file__))), name],
            capture_output=True,
            text=True,
            timeout=MINER_TIMEOUT_S,
            env=env,
            preexec_fn=_set_rlimits,
        )
    except subprocess.TimeoutExpired:
        return {"miner": name, "error": f"timeout after {MINER_TIMEOUT_S}s", "findings": []}
    if proc.returncode != 0:
        return {"miner": name, "error": proc.stderr[-400:], "findings": []}
    try:
        findings = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"miner": name, "error": "bad stdout", "findings": []}
    return {"miner": name, "findings": findings}


def _set_rlimits() -> None:
    """Apply memory cap + no core dumps in the child before exec.

    ANDROID/BIONIC GOTCHA (discovered empirically 2026-08-16): RLIMIT_AS must
    never be set on Termux — bionic's loader reserves huge address-space
    regions and SIGABRTs the child at ANY soft limit (256MB .. 4GB tested),
    even when the parent limit is unlimited. RLIMIT_DATA (heap) is the real
    guard: CPython + sqlite start fine and runaway heap allocations still die.
    """
    import resource as _r

    def _try(fn):
        try:
            fn()
        except (ValueError, OSError):
            pass

    _try(lambda: _r.setrlimit(_r.RLIMIT_CORE, (0, 0)))
    _try(lambda: _r.setrlimit(
        _r.RLIMIT_DATA,
        (MINER_MEM_MB * 1024 * 1024, _r.RLIM_INFINITY),
    ))


def run_all_sandboxed(miners_registry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Run every registered miner sandboxed; collect their findings."""
    out: List[Dict[str, Any]] = []
    for name in miners_registry:
        res = run_miner_sandboxed(name)
        if res.get("error"):
            out.append(res)
            continue
        for f in res["findings"]:
            out.append(f)
    return out
