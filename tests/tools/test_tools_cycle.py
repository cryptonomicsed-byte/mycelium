"""Tests for mycelium-cycle (U3), picks-client (U4), substrate-tunnel (U5)."""
import pytest
from unittest.mock import MagicMock, patch


# ── Smoke imports ──────────────────────────────────────────────────────────

def test_import_cycle():
    from mycelium_cycle import cli  # noqa: F401
    from mycelium_cycle.cli import run_full_cycle  # noqa: F401


def test_import_picks_client():
    from picks_client import cli  # noqa: F401


def test_import_substrate_tunnel():
    from substrate_tunnel import cli  # noqa: F401


# ── run_full_cycle mock ────────────────────────────────────────────────────

def test_run_full_cycle_returns_summary():
    """run_full_cycle must return a dict with mine_and_apply, publish, a2a keys."""
    from mycelium_cycle.cli import run_full_cycle

    fake_mine_output = '{"applied": [], "findings": [], "errors": []}\n'

    with patch("mycelium_cycle.cli._run_mine_and_apply") as mock_mine, \
         patch("mycelium_cycle.cli.publish_mod") as mock_pub, \
         patch("mycelium_cycle.cli.a2a_mod") as mock_a2a:

        # redirect_stdout captures output from _run_mine_and_apply
        import io
        from contextlib import redirect_stdout

        def fake_mine_run(args):
            # Simulate output that run_full_cycle captures via redirect_stdout
            import sys
            sys.stdout.write(fake_mine_output)

        mock_mine.side_effect = fake_mine_run
        mock_pub.publish.return_value = {"pushed": False}
        mock_a2a.publish_findings.return_value = {"published": 0}

        result = run_full_cycle(a2a_limit=1)

    assert "mine_and_apply" in result
    assert "publish" in result
    assert "a2a" in result
    assert "errors" in result


def test_run_full_cycle_publish_failure_does_not_raise():
    """A publish failure should be captured in errors[], not raised."""
    from mycelium_cycle.cli import run_full_cycle

    fake_mine_output = '{"applied": [], "findings": [], "errors": []}\n'

    with patch("mycelium_cycle.cli._run_mine_and_apply") as mock_mine, \
         patch("mycelium_cycle.cli.publish_mod") as mock_pub, \
         patch("mycelium_cycle.cli.a2a_mod") as mock_a2a:

        def fake_mine_run(args):
            import sys
            sys.stdout.write(fake_mine_output)

        mock_mine.side_effect = fake_mine_run
        mock_pub.publish.side_effect = ConnectionError("gitea down")
        mock_a2a.publish_findings.return_value = {}

        result = run_full_cycle()  # must not raise

    assert any("publish" in e for e in result["errors"])


# ── picks-client build_command ─────────────────────────────────────────────

def test_picks_client_fetch_picks_importable():
    """picks_client.cli.fetch_picks must exist and be callable."""
    from picks_client.cli import fetch_picks
    assert callable(fetch_picks)


# ── substrate-tunnel build_command ────────────────────────────────────────

def test_substrate_tunnel_build_command_importable():
    """substrate_tunnel.cli.build_command must exist and be callable."""
    from substrate_tunnel.cli import build_command
    assert callable(build_command)
