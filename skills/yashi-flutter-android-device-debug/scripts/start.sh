#!/usr/bin/env bash
# Start (or restart) a machine-readable `flutter run` debug session.
# Cross-platform: Linux / macOS / Windows(Git Bash).
# RUN THIS FROM THE FLUTTER PROJECT ROOT (cwd = project). Sibling scripts are
# located via $0, so the skill can live anywhere on disk.
# Device selection:
#   * $DEVICE set          -> use it
#   * exactly ONE Android  -> use it automatically
#   * ZERO Android         -> error
#   * SEVERAL Android      -> error + list (the AI must let the user choose)
# On success prints the VM Service ws URI; JSON event stream -> $LOG.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

PROJECT_DIR="${PROJECT_DIR:-$PWD}"
LOG="${LOG:-$PROJECT_DIR/run_machine.jsonl}"
PIDFILE="$PROJECT_DIR/.flutter_debug.pid"
cd "$PROJECT_DIR"

if [ ! -f pubspec.yaml ]; then
  echo "ERROR: no pubspec.yaml in $PROJECT_DIR - run from a Flutter project root (or set PROJECT_DIR=)." >&2
  exit 1
fi

# Advisory preflight (Windows): warn if the project sits on a RAM disk / ReFS /
# removable / network volume, where Gradle 9 misreports the project dir.
check_project_disk "$PROJECT_DIR"

# --- pick the device ------------------------------------------------------
if [ -z "${DEVICE:-}" ]; then
  devs=()
  while IFS= read -r line; do
    [ -n "$line" ] && devs+=("$line")
  done < <(bash "$SCRIPT_DIR/android_devices.sh" || true)
  n="${#devs[@]}"
  if [ "$n" -eq 1 ]; then
    DEVICE="${devs[0]%%|*}"
  elif [ "$n" -eq 0 ]; then
    echo "ERROR: no Android device detected. Connect a device (USB debugging on) and retry." >&2
    exit 1
  else
    echo "ERROR: multiple Android devices found. Ask the USER which one to use, then re-run:" >&2
    for d in ${devs[@]+"${devs[@]}"}; do
      id="${d%%|*}"; rest="${d#*|}"; nm="${rest%%|*}"; md="${rest#*|}"
      echo "  DEVICE=$id   name=${nm:-?} model=${md:-?}" >&2
    done
    exit 2
  fi
fi
echo "device: $DEVICE"

# stop any previous session (best effort)
bash "$SCRIPT_DIR/stop.sh" >/dev/null 2>&1 || true
sleep 2

: > "$LOG"
nohup bash -c "cd '$PROJECT_DIR' && tail -f /dev/null | flutter run --machine -d '$DEVICE' --debug" > "$LOG" 2>&1 &
echo $! > "$PIDFILE"
WAIT_SECS="${WAIT_SECS:-900}"
echo "started; waiting for VM Service (first build can take minutes; WAIT_SECS=${WAIT_SECS}s) ..."
waited=0
while [ "$waited" -lt "$WAIT_SECS" ]; do
  sleep 3
  waited=$((waited + 3))
  grep -q '"wsUri"' "$LOG" 2>/dev/null && break
done

if ! grep -q '"wsUri"' "$LOG" 2>/dev/null; then
  {
    echo "ERROR: no VM Service URI within ${WAIT_SECS}s. Tail of $LOG:"
    tail -20 "$LOG"
    echo "HINT: the build may just be slow - the session keeps running in the background."
    echo "      When it finishes, re-read the URI directly:  bash scripts/ws_uri.sh \"$LOG\""
    echo "      (or simply run scripts/pipeline.sh). Enlarge the window with WAIT_SECS=<seconds>."
  } >&2
  exit 3
fi
bash "$SCRIPT_DIR/ws_uri.sh" "$LOG"
