#!/usr/bin/env bash
# mycelium tools installer (merged from mycelium-tools, ticket P2-01).
#
# Installs into a virtualenv:
#   - mycelium-substrate : compatibility shim -> this repo's `mycelium` package
#                          (+ `mycelium`, `mycelium-mcp`, `mycelium-http` scripts)
#   - mycelium-collector : wallet-intel collector (U2)
#   - mycelium-cycle     : chained self-maintenance cycle (U3)
#   - substrate-tunnel   : reverse-SSH tunnel to a shared substrate (U4)
#   - picks-client       : picks digest client, `picks-digest` (U5)
# and offers to copy the systemd --user unit templates from systemd/.
#
# Never asks for root. Never installs into the system Python. Never enables
# or starts any unit -- copying templates is opt-in, enabling is up to you.
#
# Usage:
#   ./install.sh                 # install into ~/.venvs/mycelium-tools
#   ./install.sh /path/to/venv   # install into a specific venv path
#   MYCELIUM_INSTALL_UNITS=1 ./install.sh   # copy unit templates without asking
set -euo pipefail

VENV_DIR="${1:-$HOME/.venvs/mycelium-tools}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGES="mycelium-substrate mycelium-collector mycelium-cycle substrate-tunnel picks-client"

echo "mycelium tools installer"
echo "  repo: $REPO_DIR"
echo "  venv: $VENV_DIR"
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found on PATH — install it first (pkg install python on Termux, apt/brew elsewhere)." >&2
  exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
  echo "-> creating venv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

PIP="$VENV_DIR/bin/pip"
"$PIP" install -q --upgrade pip

echo "-> installing packages (editable)"
for pkg in $PACKAGES; do
  echo "   $pkg"
  "$PIP" install -q -e "$REPO_DIR/packages/$pkg"
done

# The shim re-exports the `mycelium` package at the repo root; make it
# importable from the venv regardless of the current directory.
SITE_DIR="$("$VENV_DIR/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
echo "$REPO_DIR" > "$SITE_DIR/mycelium-repo.pth"
"$VENV_DIR/bin/python" -c 'import mycelium_substrate.core' \
  && echo "-> mycelium_substrate shim resolves to $REPO_DIR/mycelium"

echo
echo "Done. Console scripts are in: $VENV_DIR/bin"
echo "Add that to PATH, or run tools directly, e.g.:"
echo "  $VENV_DIR/bin/mycelium init"
echo "  $VENV_DIR/bin/mycelium-http"
echo

reply="${MYCELIUM_INSTALL_UNITS:-}"
if [ -z "$reply" ] && [ -t 0 ]; then
  read -r -p "Install systemd --user unit templates to ~/.config/systemd/user/? [y/N] " reply || reply=""
fi
if [[ "$reply" =~ ^([Yy]|1)$ ]]; then
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl not found on this device (expected on Termux/macOS) — skipping." >&2
  else
    UNIT_DIR="$HOME/.config/systemd/user"
    mkdir -p "$UNIT_DIR"
    cp "$REPO_DIR"/systemd/*.service "$REPO_DIR"/systemd/*.timer "$UNIT_DIR/"
    echo "-> copied unit templates to $UNIT_DIR (nothing enabled)"
    echo "   Each ExecStart uses 'env <tool>' — either put $VENV_DIR/bin on PATH"
    echo "   system-wide for your user, or edit each unit's ExecStart= to the full"
    echo "   $VENV_DIR/bin/<tool> path."
    echo "   Edit each unit's commented Environment= lines for your config, then:"
    echo "     systemctl --user daemon-reload"
    echo "     systemctl --user enable --now <unit>"
    echo "     loginctl enable-linger \$USER   # keep running after logout / on boot"
  fi
fi

echo
echo "See packages/README.md for the tool overview and 'add your own device' walkthrough."
