"""P2-01: the mycelium_substrate compatibility shim and the merged layout."""
import importlib
import json
import os
import subprocess
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

OLD_SUBSTRATE_MODULES = [
    "a2a", "apply", "cli", "core", "mcp_server", "miners",
    "nostr_wire", "publish", "sandbox", "storage",
]


# ── Shim: identity re-export ───────────────────────────────────────────────

@pytest.mark.parametrize("name", OLD_SUBSTRATE_MODULES)
def test_every_old_module_is_the_real_mycelium_module(name):
    old = importlib.import_module(f"mycelium_substrate.{name}")
    real = importlib.import_module(f"mycelium.{name}")
    assert old is real
    # the alias must not rename the real module
    assert real.__name__ == f"mycelium.{name}"
    assert real.__spec__.name == f"mycelium.{name}"


def test_from_import_and_attribute_access():
    import mycelium_substrate
    from mycelium_substrate import core as via_from
    import mycelium.core

    assert via_from is mycelium.core
    assert mycelium_substrate.core is mycelium.core
    assert mycelium_substrate.miners.MINERS is importlib.import_module("mycelium.miners").MINERS


def test_nested_submodule_alias():
    import mycelium_substrate.miners.registry as old_reg
    import mycelium.miners.registry as real_reg
    assert old_reg is real_reg


def test_module_state_is_shared(tmp_path):
    from mycelium_substrate import core as old_core
    import mycelium.core as real_core

    path = str(tmp_path / "shared.db")
    old_core.init_db(path)
    assert real_core.DB_PATH == path
    old_core.emit(agent="shim", session="s", kind="tool_call", action="a", outcome="success")
    rows = real_core.iter_rows(real_core.query_traces(agent="shim"))
    assert len(rows) == 1


def test_cycle_entry_point_is_mycelium_cmd_cycle():
    from mycelium_cycle import cli as cycle_cli
    import mycelium.cli
    assert cycle_cli._run_mine_and_apply is mycelium.cli.cmd_cycle


def test_unknown_attribute_raises_attribute_error():
    import mycelium_substrate
    with pytest.raises(AttributeError):
        mycelium_substrate.definitely_not_a_module  # noqa: B018
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("mycelium_substrate.definitely_not_a_module")


def test_old_core_fork_was_not_copied():
    shim = REPO / "packages" / "mycelium-substrate" / "mycelium_substrate"
    present = {p.stem for p in shim.glob("*.py")}
    assert present == {"__init__", "http_server"}


def test_shim_bootstraps_mycelium_from_repo_without_path(tmp_path):
    """Fresh interpreter, cwd outside the repo, only the shim dir on the path:
    the shim must locate the repo's mycelium package itself."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "MYCELIUM_REPO")}
    env["PYTHONPATH"] = str(REPO / "packages" / "mycelium-substrate")
    code = ("import mycelium_substrate.core as c, mycelium.core as m; "
            "print(c is m, m.__file__)")
    out = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    same, path = out.stdout.split()
    assert same == "True"
    assert Path(path).resolve() == (REPO / "mycelium" / "core.py").resolve()


# ── http_server (mycelium-http) on top of the real core ────────────────────

def test_http_server_trace_roundtrip(tmp_path):
    from mycelium_substrate import core
    from mycelium_substrate.http_server import _Handler

    core.init_db(str(tmp_path / "http.db"))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        body = json.dumps({"agent": "collector", "session": "s1", "kind": "observation",
                           "action": "wallet_trade", "outcome": "info",
                           "payload": {"k": 1}}).encode()
        req = urllib.request.Request(base + "/api/trace", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            assert r.status == 201
            assert json.loads(r.read())["id"]
        with urllib.request.urlopen(base + "/api/traces?agent=collector&limit=5", timeout=10) as r:
            got = json.loads(r.read())
        assert got["count"] == 1
        assert got["traces"][0]["action"] == "wallet_trade"
        with urllib.request.urlopen(base + "/api/status", timeout=10) as r:
            assert json.loads(r.read())["status"] == "ok"
    finally:
        httpd.shutdown()
        httpd.server_close()


# ── Merged layout: units + installer ───────────────────────────────────────

EXPECTED_UNITS = {
    "mycelium-collector.service", "mycelium-cycle.service", "mycelium-cycle.timer",
    "mycelium-substrate.service", "picks-digest.service", "picks-digest.timer",
    "substrate-tunnel.service",
}


def test_systemd_units_present_and_user_scoped():
    units = {p.name for p in (REPO / "systemd").iterdir()}
    assert EXPECTED_UNITS <= units
    for name in EXPECTED_UNITS:
        text = (REPO / "systemd" / name).read_text()
        assert "\nUser=" not in text  # --user templates, never root
        if name.endswith(".service"):
            assert "ExecStart=/usr/bin/env " in text


def test_install_sh_parses_and_lists_all_packages():
    script = REPO / "install.sh"
    assert os.access(script, os.X_OK)
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0
    text = script.read_text()
    for pkg in ["mycelium-substrate", "mycelium-collector", "mycelium-cycle",
                "substrate-tunnel", "picks-client"]:
        assert pkg in text
        assert (REPO / "packages" / pkg / "pyproject.toml").is_file()
    assert "sudo" not in text
