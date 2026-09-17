#!/data/data/com.termux/files/usr/bin/bash
# Poll the env-probe kernel, then print its full output.
K="bino85/mycelium-env-probe"
OUT="$HOME/mycelium/kaggle-probe-out"
LOG="$HOME/mycelium/kaggle-probe-run.log"
mkdir -p "$OUT"

for i in $(seq 1 30); do
  S="$(kaggle kernels status "$K" 2>&1 | tr -d '\r')"
  echo "$(date +%H:%M:%S) [$i] $S" | tee -a "$LOG"
  LC="$(printf '%s' "$S" | tr 'A-Z' 'a-z')"
  case "$LC" in
    *complete*|*error*|*fail*|*cancel*) break ;;
  esac
  sleep 30
done

echo "=== pulling probe output ===" | tee -a "$LOG"
kaggle kernels output "$K" -p "$OUT" 2>&1 | tee -a "$LOG"
ls -la "$OUT" 2>&1 | tee -a "$LOG"

echo
echo "########## PROBE RESULTS ##########"
for f in "$OUT"/*.log; do
  [ -e "$f" ] || continue
  echo "=== $f ==="
  python3 - "$f" <<'PY'
import json, sys
for ln in open(sys.argv[1]):
    ln = ln.strip().lstrip(',')
    if not ln:
        continue
    try:
        o = json.loads(ln)
    except Exception:
        continue
    sys.stdout.write(o.get("data", ""))
PY
done
