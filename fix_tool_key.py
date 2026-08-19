#!/usr/bin/env python3
"""Extract VANTAGE_TOOL_INTEL from the vantage systemd unit (incl. drop-ins)
and write /opt/ares/ares-signal-fusion/.vantage_tool_keys.json (0600)."""
import json
import os
import re
import subprocess

out = subprocess.run(["systemctl", "cat", "vantage"], capture_output=True, text=True).stdout
m = re.search(r'Environment="?VANTAGE_TOOL_INTEL=([^"]+)', out)
if not m:
    print("ERROR: VANTAGE_TOOL_INTEL not found in unit")
    raise SystemExit(1)
key = m.group(1)
path = "/opt/ares/ares-signal-fusion/.vantage_tool_keys.json"
with open(path, "w") as f:
    json.dump({"intel": key}, f)
os.chmod(path, 0o600)
print("wrote", path, "| intel len:", len(key))
