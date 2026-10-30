#!/usr/bin/env bash
# One-shot debug pipeline against a RUNNING Flutter app (any target kind:
# android / desktop / web - the screenshot step dispatches on the target).
# RUN THIS FROM THE FLUTTER PROJECT ROOT (cwd = project). Cross-platform.
#   usage: scripts/pipeline.sh          # reads $LOG (default ./run_machine.jsonl)
# Produces artifacts/{widget_tree.txt, app_logs.txt, screen.png}
# and prints OUTPUT=<absolute path> for each artifact.
# If the screenshot cannot be produced for this target, shot.sh exits non-zero
# and the pipeline fails loudly instead of emitting a wrong screen.png.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

PROJECT_DIR="${PROJECT_DIR:-$PWD}"
LOG="${LOG:-$PROJECT_DIR/run_machine.jsonl}"
OUTDIR="${OUTDIR:-$PROJECT_DIR/artifacts}"
LOGSECS="${LOGSECS:-6}"
PY="$(detect_py)"

if [ -z "$PY" ]; then
  echo "ERROR: python not found. Set PY=<python path>." >&2
  exit 1
fi

WS="$(bash "$SCRIPT_DIR/ws_uri.sh" "$LOG")"
echo "VM Service: $WS"
mkdir -p "$OUTDIR"

"$PY" "$SCRIPT_DIR/devtools_probe.py" --ws "$WS" tree                   > "$OUTDIR/widget_tree.txt"
"$PY" "$SCRIPT_DIR/devtools_probe.py" --ws "$WS" logs --seconds "$LOGSECS" > "$OUTDIR/app_logs.txt"
# Pass LOG/PROJECT_DIR through so shot.sh resolves the same target as this run.
LOG="$LOG" PROJECT_DIR="$PROJECT_DIR" bash "$SCRIPT_DIR/shot.sh" "$OUTDIR/screen.png" > /dev/null

for f in widget_tree.txt app_logs.txt screen.png; do
  echo "OUTPUT=$(abs_path "$OUTDIR/$f")"
done
