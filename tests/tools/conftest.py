"""Shared setup for the tools merged from mycelium-tools (P2-01).

Puts the repo root (the real `mycelium` package) and every packages/<pkg>
directory on sys.path so the tool tests run without a pip install, and
snapshots mycelium.core.DB_PATH / MYCELIUM_DB so tests that repoint the
substrate DB can't leak into the rest of the suite.
"""
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PACKAGES = REPO / "packages"

for p in [REPO, *sorted(d for d in PACKAGES.iterdir() if d.is_dir())]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture(autouse=True)
def _restore_substrate_db():
    import mycelium.core as core

    saved_path = core.DB_PATH
    saved_env = os.environ.get("MYCELIUM_DB")
    yield
    core.DB_PATH = saved_path
    if saved_env is None:
        os.environ.pop("MYCELIUM_DB", None)
    else:
        os.environ["MYCELIUM_DB"] = saved_env
