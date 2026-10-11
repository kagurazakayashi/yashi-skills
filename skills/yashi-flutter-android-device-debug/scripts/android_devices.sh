#!/usr/bin/env bash
# List Android devices as "<deviceId>|<name>|<model>" (one per line).
# Prefers `flutter devices --machine`; model is enriched via `adb getprop`;
# falls back to `adb devices` (id only) when flutter is unavailable.
# Overridable: PY=<python path>, ADB=<adb path>
#
# NOTE: on Windows, Python's stdout translates "\n" -> "\r\n"; that trailing CR
# would pollute the last field, so we disable newline translation AND strip CR.
set -euo pipefail
PY="${PY:-$(command -v python3 || command -v python || true)}"
ADB="${ADB:-$(command -v adb || echo adb)}"

raw=""
if command -v flutter >/dev/null 2>&1 && [ -n "$PY" ]; then
  raw=$(flutter devices --machine 2>/dev/null | "$PY" -c '
import json, sys
try:
    sys.stdout.reconfigure(newline="\n")   # avoid Windows CRLF pollution
except Exception:
    pass
try:
    data = json.load(sys.stdin)
except Exception:
    data = []
for d in data:
    tp = (d.get("targetPlatform") or "")
    sdk = (d.get("sdk") or "")
    if tp.startswith("android") or sdk.startswith("Android"):
        print("|".join([d.get("id", ""), d.get("name", ""), d.get("model", "")]))
' 2>/dev/null | tr -d "\r" || true)
fi

if [ -z "$raw" ]; then
  # fallback: adb serials only (name/model unknown)
  raw=$("$ADB" devices 2>/dev/null | awk '/\tdevice$/{print $1"||"}' || true)
fi

printf '%s\n' "$raw" | tr -d '\r' | while IFS='|' read -r id name model; do
  [ -n "$id" ] || continue
  if [ -z "$model" ]; then
    # NOTE: </dev/null is required - `adb shell` would otherwise consume the
    # loop's stdin (the remaining device lines) and truncate the listing.
    model=$("$ADB" -s "$id" shell getprop ro.product.model </dev/null 2>/dev/null | tr -d '\r\n' || true)
  fi
  printf '%s|%s|%s\n' "$id" "$name" "$model"
done
