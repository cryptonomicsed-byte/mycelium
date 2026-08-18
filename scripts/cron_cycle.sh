#!/data/data/com.termux/files/usr/bin/bash
# Mycelium self-maintenance cycle — silent unless something notable.
# Watchdog semantics: empty stdout = nothing to report (cron stays quiet).
# Uses direct script invocation (import bootstrap in cli.py handles the path).
CLI=/data/data/com.termux/files/home/mycelium/mycelium/cli.py
OUT=$(python3 "$CLI" cycle 2>/dev/null)
RC=$?
if [ $RC -ne 0 ]; then
  echo "mycelium cycle FAILED (exit $RC)"
  exit 1
fi
NEW=$(echo "$OUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('new_findings',0))" 2>/dev/null)
APPLIED=$(echo "$OUT" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('auto_applied',[])))" 2>/dev/null)
ERRORS=$(echo "$OUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('sandbox_errors',0))" 2>/dev/null)
if [ "${NEW:-0}" -gt 0 ] || [ "${APPLIED:-0}" -gt 0 ] || [ "${ERRORS:-0}" -gt 0 ]; then
  echo "mycelium: $NEW new finding(s), $APPLIED auto-applied, $ERRORS sandbox error(s)"
  echo "$OUT"
fi
