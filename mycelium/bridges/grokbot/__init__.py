"""grokbot-pumpfun -> Mycelium bridge (merged from grokbot-mycelium, P2-02).

Public surface:
  bridge.sync          tail grokbot's JSONL tape into the substrate
  bridge.trace_for     map one grokbot record onto one substrate trace
  calibrate.build_report / format_report / emit_finding
  ceiling.conviction_for / assert_not_execution_target

The bridge is OFF until GROKBOT_BRIDGE_ENABLED=1 and grokbot's trades.jsonl
exists (tools/bridge_daemon.py checks both).
"""
from . import bridge, calibrate, ceiling  # noqa: F401

__all__ = ["bridge", "calibrate", "ceiling"]
