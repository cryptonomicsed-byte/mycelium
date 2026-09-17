#!/data/data/com.termux/files/usr/bin/bash
# Poll a Kaggle kernel until it finishes, then pull outputs.
# Auth comes from ~/.kaggle/access_token (no env var needed).
#
# FIX 2026-09-16: the status match was case-sensitive. Kaggle returns
#   bino85/x has status "KernelWorkerStatus.ERROR"
# with UPPERCASE ERROR, so the old  case "$S" in *error*  never matched --
# it silently polled all 60 iterations on a kernel that had already died at
# 23:22 and then exited 2 with "timeout". Now the status is lowercased before
# matching, and the terminal states are checked first.

K="${1:-bino85/mycelium-finetune}"
OUT="$HOME/mycelium/kaggle-output"
LOG="$HOME/mycelium/kaggle-run.log"
INTERVAL="${INTERVAL:-90}"
TRIES="${TRIES:-60}"
mkdir -p "$OUT"

echo "watching $K — started $(date)" | tee -a "$LOG"

status_of() {
  kaggle kernels status "$K" 2>&1 | tr -d '\r'
}

for i in $(seq 1 "$TRIES"); do
  S="$(status_of)"
  echo "$(date +%H:%M:%S) [$i] $S" | tee -a "$LOG"

  S_LC="$(printf '%s' "$S" | tr 'A-Z' 'a-z')"

  case "$S_LC" in
    *complete*)
      echo "=== COMPLETE — pulling outputs ===" | tee -a "$LOG"
      kaggle kernels output "$K" -p "$OUT" 2>&1 | tee -a "$LOG"
      echo "=== files retrieved ===" | tee -a "$LOG"
      ls -la "$OUT" 2>&1 | tee -a "$LOG"
      exit 0
      ;;
    *error*|*cancel*|*fail*)
      echo "=== RUN FAILED — pulling whatever exists for debugging ===" | tee -a "$LOG"
      kaggle kernels output "$K" -p "$OUT" 2>&1 | tee -a "$LOG"
      echo "=== tail of kernel log ===" | tee -a "$LOG"
      # Kaggle returns the run log alongside outputs; surface the real error.
      for f in "$OUT"/*.log; do
        [ -e "$f" ] || continue
        echo "--- $f ---" | tee -a "$LOG"
        tail -c 4000 "$f" | tee -a "$LOG"
      done
      exit 1
      ;;
  esac
  sleep "$INTERVAL"
done

echo "=== timeout after $((TRIES*INTERVAL/60)) min — still running, check manually ===" | tee -a "$LOG"
exit 2
