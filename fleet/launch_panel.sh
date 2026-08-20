#!/bin/bash
# launch_panel.sh <name> <cwd> <brief-file> <branch> [--probe]
# Spawns a named hermes panel on this VPS inside a tmux session called
# "panel-<name>" so it survives SSH disconnects and can be restarted by
# name:  tmux kill-session -t panel-<name> && bash launch_panel.sh ...
set -u
NAME="$1"; CWD="$2"; BRIEF="$3"; BRANCH="$4"
PROBE="${5:-}"
SESSION="panel-$NAME"
TMUX_BIN="$(command -v tmux)"

if [ -z "$TMUX_BIN" ]; then
  echo "FATAL: tmux not installed"
  exit 2
fi

# brain sanity: hermes must answer on deepseek
if [ -n "$PROBE" ]; then
  BRAIN=$(timeout 60 /usr/local/bin/hermes chat -q "Reply with exactly: PANEL-BRAIN-OK" -Q 2>&1 | tail -1)
  echo "BRAIN=$BRAIN"
  case "$BRAIN" in
    *PANEL-BRAIN-OK*) echo "brain OK" ;;
    *) echo "FATAL: panel brain not working"; exit 2 ;;
  esac
fi

# ensure branch exists on the repo (create if missing)
if [ -d "$CWD/.git" ]; then
  git -C "$CWD" fetch origin "$BRANCH" 2>/dev/null || true
  if git -C "$CWD" rev-parse --verify "$BRANCH" >/dev/null 2>&1; then
    git -C "$CWD" checkout "$BRANCH" 2>/dev/null || git -C "$CWD" checkout -b "$BRANCH"
  else
    git -C "$CWD" checkout -b "$BRANCH"
  fi
fi

# spawn the panel
tmux kill-session -t "$SESSION" 2>/dev/null
tmux new-session -d -s "$SESSION" -x 160 -y 45 "cd $CWD && /usr/local/bin/hermes"
sleep 6
tmux send-keys -t "$SESSION" "Read the brief at $BRIEF in full, then execute. Work on branch $BRANCH. Commit and push your work to your branch when done." Enter

echo "SESSION=$SESSION NAME=$NAME CWD=$CWD BRANCH=$BRANCH"
tmux ls 2>/dev/null | grep "panel-"
