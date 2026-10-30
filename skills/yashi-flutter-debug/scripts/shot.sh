#!/usr/bin/env bash
# Screenshot the app under debug, dispatching on the CURRENT TARGET KIND.
#   usage: scripts/shot.sh [out.png]
#
#   android -> adb exec-out screencap -p      (scripts/shot_android.sh)
#   desktop -> own-window capture             (shot_windows.ps1 / shot_macos.sh /
#                                              shot_linux.sh)
#   anything else -> FAIL LOUDLY
#
# The target is read from the `flutter run --machine` log (`deviceId` of the
# last app.start), so this works no matter which target start.sh picked.
#
# Why fail instead of falling back: `adb exec-out` succeeds whenever ANY Android
# device is attached, so on a desktop target it silently returns the PHONE's
# screen while still printing "OK PNG". A wrong screenshot that reports success
# is worse than an error - never guess here.
#
# Overridable: TARGET=<deviceId>, TARGET_KIND=android|desktop|web, LOG=<log>,
#              ADB=..., PY=..., PROJECT_DIR=...
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

PROJECT_DIR="${PROJECT_DIR:-$PWD}"
LOG="${LOG:-$PROJECT_DIR/run_machine.jsonl}"
out="${1:-shot.png}"

TARGET="${TARGET:-$(target_id_from_log "$LOG" 2>/dev/null || true)}"
KIND="${TARGET_KIND:-}"
[ -n "$KIND" ] || KIND="$(target_kind_of "$TARGET")"

case "$KIND" in
  android)
    bash "$SCRIPT_DIR/shot_android.sh" "$out"
    ;;
  desktop)
    case "$(os_family)" in
      windows)
        if ! command -v powershell >/dev/null 2>&1; then
          echo "ERROR: PowerShell not found; cannot capture a Windows desktop window." >&2
          exit 4
        fi
        powershell -NoProfile -ExecutionPolicy Bypass \
          -File "$SCRIPT_DIR/shot_windows.ps1" -Out "$out" -ProjectDir "$PROJECT_DIR"
        ;;
      macos)
        bash "$SCRIPT_DIR/shot_macos.sh" "$out"
        ;;
      linux)
        bash "$SCRIPT_DIR/shot_linux.sh" "$out"
        ;;
      *)
        echo "ERROR: unsupported desktop OS: '$(os_family)'." >&2
        exit 4
        ;;
    esac
    ;;
  *)
    {
      echo "ERROR: cannot determine the target kind (target='${TARGET:-<none>}', log='$LOG')."
      echo "       Refusing to guess: on a non-Android target 'adb exec-out' would"
      echo "       silently capture an attached phone instead of the app under debug."
      echo "       Fix by: running scripts/start.sh (so $LOG records the target), or"
      echo "       passing TARGET_KIND=android|desktop (and LOG=... if not the default)."
    } >&2
    exit 3
    ;;
esac
