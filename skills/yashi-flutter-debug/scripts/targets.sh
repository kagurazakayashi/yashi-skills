#!/usr/bin/env bash
# List the Flutter run targets available on this machine, one per line:
#
#   <id>|<kind>|<label>          kind = android | desktop | web
#
# Examples:
#   5a42974d|android|MI 9 (Android 11 (API 30))
#   windows|desktop|Windows (windows-x64)
#   chrome|web|Chrome (web-javascript)
#
# Feeds scripts/start.sh (target selection) and scripts/preflight.sh.
# Prefers `flutter devices --machine`; falls back to `adb devices` (Android only).
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


def kind_of(d):
    """Classify a `flutter devices --machine` entry.

    Observed shapes: android-arm64 / windows-x64 / web-javascript (-macos,
    -linux and darwin variants share the same prefixes).
    """
    tp = (d.get("targetPlatform") or "").lower()
    sdk = (d.get("sdk") or "").lower()
    if tp.startswith("android") or sdk.startswith("android"):
        return "android"
    if tp.startswith("web") or sdk.startswith("web"):
        return "web"
    if tp.startswith(("windows", "darwin", "macos", "linux")) or "windows" in sdk:
        return "desktop"
    return "unknown"


for d in data:
    did = d.get("id", "")
    label = d.get("model") or d.get("name") or ""
    sdk = d.get("sdk") or ""
    if sdk and sdk.lower() not in label.lower() and label.lower() not in sdk.lower():
        label = (label + " (" + sdk + ")").strip()
    print("|".join([did, kind_of(d), label]))
' 2>/dev/null | tr -d "\r" || true)
fi

if [ -z "$raw" ]; then
  # fallback: adb serials only (kind/label inferred)
  raw=$("$ADB" devices 2>/dev/null | awk '/\tdevice$/{print $1"|android|"}' || true)
fi

printf '%s\n' "$raw" | tr -d '\r' | while IFS='|' read -r id kind label; do
  [ -n "$id" ] || continue
  if [ "$kind" = "android" ] && [ -z "$label" ]; then
    # NOTE: </dev/null is required - `adb shell` would otherwise consume the
    # loop's stdin (the remaining device lines) and truncate the listing.
    label=$("$ADB" -s "$id" shell getprop ro.product.model </dev/null 2>/dev/null | tr -d '\r\n' || true)
    [ -n "$label" ] || label="$id"
  fi
  [ -n "$label" ] || label="$id"
  [ -n "$kind" ] || kind="unknown"
  printf '%s|%s|%s\n' "$id" "$kind" "$label"
done
