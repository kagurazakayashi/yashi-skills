#!/usr/bin/env bash
# Start (or restart) a machine-readable `flutter run` debug session.
# Cross-platform: Linux / macOS / Windows(Git Bash).
# RUN THIS FROM THE FLUTTER PROJECT ROOT (cwd = project). Sibling scripts are
# located via $0, so the skill can live anywhere on disk.
# Target selection (scripts/targets.sh classifies each target as
# android | desktop | web):
#   * $DEVICE set              -> use it
#   * exactly ONE Android      -> use it automatically (historical default)
#   * else exactly ONE target  -> use it
#   * anything else            -> error + list (the AI must let the user choose)
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

# --- pick the target ------------------------------------------------------
# The rule order keeps the historical Android behaviour (a lone phone is picked
# automatically, desktop/web entries are ignored for that purpose) while letting
# a desktop-only or web-only machine work with no DEVICE at all. Anything
# ambiguous is reported and left to the user.
if [ -z "${DEVICE:-}" ]; then
  list="$(bash "$SCRIPT_DIR/targets.sh" || true)"
  if [ -z "$list" ]; then
    {
      echo "ERROR: no Flutter target found ('flutter devices' listed nothing)."
      echo "       Connect an Android device with USB debugging on, or check the"
      echo "       desktop/web toolchain with 'flutter doctor'."
    } >&2
    exit 1
  fi
  total="$(printf '%s\n' "$list" | grep -c . || true)"
  android_n="$(printf '%s\n' "$list" | awk -F'|' '$2=="android"' | grep -c . || true)"
  if [ "$android_n" -eq 1 ]; then
    DEVICE="$(printf '%s\n' "$list" | awk -F'|' '$2=="android"{print $1; exit}')"
  elif [ "$total" -eq 1 ]; then
    DEVICE="$(printf '%s\n' "$list" | head -1 | cut -d'|' -f1)"
  else
    echo "ERROR: several targets found. Ask the USER which one to use, then re-run:" >&2
    printf '%s\n' "$list" | while IFS='|' read -r id kind label; do
      echo "  DEVICE=$id   kind=$kind   label=${label:-?}" >&2
    done
    echo "  (examples: DEVICE=5a42974d for a phone, DEVICE=windows for the desktop app)" >&2
    exit 2
  fi
fi
KIND="$(target_kind_of "$DEVICE")"
echo "device: $DEVICE  (kind: ${KIND:-unknown})"

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
