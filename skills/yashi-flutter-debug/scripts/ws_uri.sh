#!/usr/bin/env bash
# Extract the Dart VM Service ws URI from a `flutter run --machine` JSON log.
# usage: scripts/ws_uri.sh [run_machine.jsonl]
set -euo pipefail
log="${1:-run_machine.jsonl}"
uri=$(grep -oE '"wsUri":"[^"]+"' "$log" 2>/dev/null | tail -1 | sed 's/.*"wsUri":"//; s/"$//')
if [ -z "$uri" ]; then
  echo "no wsUri found in $log" >&2
  exit 1
fi
echo "$uri"
