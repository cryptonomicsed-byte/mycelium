#!/bin/bash
# panel_supervisor.sh — ensure all named panels are alive; restart by name
# on crash. Run from cron every 2 min (no systemd tmux quirk needed).
# Panels: loom -> /opt/ares/Loom, axiom -> /opt/ares/axiom-repo,
#         omokoda -> /opt/ares/omokoda2-repo
set -u

# name|cwd|brief|branch
declare -A CWD=( [loom]="/opt/ares/Loom" [axiom]="/opt/ares/axiom-repo" [omokoda]="/opt/ares/omokoda2-repo" )
declare -A BRIEF=( [loom]="/root/brief_loom.md" [axiom]="/root/brief_axiom.md" [omokoda]="/root/brief_omokoda.md" )
declare -A BRANCH=( [loom]="loom-signal-enhance" [axiom]="axiom-whale-intel" [omokoda]="omokoda-platform" )

for NAME in loom axiom omokoda; do
  SESSION="panel-$NAME"
  if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "$(date +%H:%M:%S) panel-$NAME DEAD — restarting"
    bash /root/launch_panel.sh "$NAME" "${CWD[$NAME]}" "${BRIEF[$NAME]}" "${BRANCH[$NAME]}"
  fi
done
echo "$(date +%H:%M:%S) supervisor sweep done: $(tmux ls 2>/dev/null | grep -c panel-) panels up"
