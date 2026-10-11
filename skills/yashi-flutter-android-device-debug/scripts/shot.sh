#!/usr/bin/env bash
# Binary-safe screenshot from the connected Android device.
#   usage: scripts/shot.sh [out.png]      (ADB=..., PY=... overridable)
# NOTE: must use `adb exec-out`; `adb shell screencap -p` corrupts the PNG
#       because of CRLF translation (signature becomes \x89PNG\r\r\n).
set -euo pipefail
ADB="${ADB:-$(command -v adb || echo adb)}"
PY="${PY:-$(command -v python3 || command -v python || true)}"
out="${1:-shot.png}"

"$ADB" exec-out screencap -p > "$out"

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
