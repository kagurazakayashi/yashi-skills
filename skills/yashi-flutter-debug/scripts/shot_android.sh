#!/usr/bin/env bash
# Binary-safe screenshot of a CONNECTED ANDROID DEVICE.
#   usage: scripts/shot_android.sh [out.png]      (ADB=..., PY=..., TARGET=... overridable)
#
# Normally reached through scripts/shot.sh, which dispatches by target kind.
# It is also fine to call directly when the target is definitely Android.
#
# NOTE: must use `adb exec-out`; `adb shell screencap -p` corrupts the PNG
#       because of CRLF translation (signature becomes \x89PNG\r\r\n).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

ADB="${ADB:-$(command -v adb || echo adb)}"
PY="${PY:-$(command -v python3 || command -v python || true)}"
out="${1:-shot.png}"

# Target the device of the CURRENT debug session when known, so a second
# attached phone cannot make `adb exec-out` fail with "more than one device".
TARGET="${TARGET:-$(target_id_from_log "${LOG:-run_machine.jsonl}" 2>/dev/null || true)}"

if [ -n "$TARGET" ]; then
  "$ADB" -s "$TARGET" exec-out screencap -p > "$out"
else
  "$ADB" exec-out screencap -p > "$out"
fi

if [ -n "$PY" ]; then
  "$PY" - "$out" <<'PY'
import sys
sig = open(sys.argv[1], "rb").read(8)
if sig != b"\x89PNG\r\n\x1a\n":
    sys.exit("BROKEN PNG (%r) - did you use `adb shell` instead of `adb exec-out`?" % sig)
print("OK PNG", sys.argv[1])
PY
else
  echo "OK (no python; skipped PNG validation): $out"
fi
