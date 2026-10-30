#!/usr/bin/env bash
# Continuously append the running app's stdout/stderr to a file for N seconds.
# Use it to START LISTENING BEFORE you trigger an action, so the action's
# output is not missed (`logs` only receives events emitted after it attaches).
#   usage: scripts/watch_logs.sh [seconds] [outfile]
#   defaults: seconds=60  outfile=./app_logs_live.txt   (LOG=... to override log)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

PROJECT_DIR="${PROJECT_DIR:-$PWD}"
LOG="${LOG:-$PROJECT_DIR/run_machine.jsonl}"
SECS="${1:-60}"
OUT="${2:-$PROJECT_DIR/app_logs_live.txt}"
PY="$(detect_py)"

if [ -z "$PY" ]; then
  echo "ERROR: python not found. Set PY=<python path>." >&2
  exit 1
fi

WS="$(bash "$SCRIPT_DIR/ws_uri.sh" "$LOG")"
echo "listening ${SECS}s -> $OUT" >&2
"$PY" "$SCRIPT_DIR/devtools_probe.py" --ws "$WS" logs --seconds "$SECS" >> "$OUT"
