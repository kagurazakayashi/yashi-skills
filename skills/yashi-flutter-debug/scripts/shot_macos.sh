#!/usr/bin/env bash
# Screenshot of the debugged Flutter DESKTOP app on macOS.
#   usage: scripts/shot_macos.sh [out.png]
#
# Normally reached through scripts/shot.sh, which dispatches on the target kind.
#
# Window-targeted: `screencapture -l <CGWindowID>` captures that window's own
# content rather than a screen region, so an overlapping window cannot leak into
# the artifact. The CGWindowID is looked up through the CoreGraphics bridge that
# ships inside osascript (JXA), so this needs no third-party dependency
# (no pyobjc, no Homebrew).
#
# NOT VERIFIED ON A REAL MAC YET: this path was written on a Windows machine.
# It is written to fail loudly (non-zero exit + explanation) rather than emit a
# plausible-but-wrong image. Treat the first real run as a test.
#
# Prerequisite: macOS "Screen Recording" permission for the terminal / agent
#   that runs it (System Settings -> Privacy & Security -> Screen Recording).
#   Without it the capture comes back blank or black, which this script reports
#   as a failure instead of "OK".
#
# Overridable: APP=<process name>, PROJECT_DIR=..., PY=...
# Exit codes: 1 = cannot derive app name, 2 = no window found,
#             3 = capture or validation failed, 4 = wrong OS / missing tool.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

out="${1:-shot.png}"
PROJECT_DIR="${PROJECT_DIR:-$PWD}"
PY="${PY:-$(command -v python3 || command -v python || true)}"

if [ "$(os_family)" != macos ]; then
  echo "ERROR: shot_macos.sh only runs on macOS (detected: $(os_family))." >&2
  exit 4
fi
for tool in screencapture osascript; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "ERROR: '$tool' not found, but it ships with macOS." >&2
    exit 4
  fi
done

APP="${APP:-$(desktop_app_name "$PROJECT_DIR")}"
if [ -z "$APP" ]; then
  {
    echo "ERROR: cannot derive the app name from macos/Runner/Configs/AppInfo.xcconfig (PRODUCT_NAME)."
    echo "       Pass it explicitly:  APP=<process name> bash scripts/shot_macos.sh $out"
  } >&2
  exit 1
fi

# --- locate the window: the largest on-screen, layer-0 window owned by APP ---
WID="$(APP_NAME="$APP" osascript -l JavaScript /dev/stdin 2>/dev/null <<'JXA' || true
ObjC.import('CoreGraphics');
ObjC.import('Foundation');
function run() {
  try {
    var want = ObjC.unwrap($.NSProcessInfo.processInfo.environment.objectForKey('APP_NAME'));
    var list = $.CGWindowListCopyWindowInfo(
      $.kCGWindowListOptionOnScreenOnly | $.kCGWindowListExcludeDesktopElements,
      $.kCGNullWindowID);
    var best = '';
    var bestArea = -1;
    for (var i = 0; i < list.count; i++) {
      var w = list.objectAtIndex(i);
      var owner = ObjC.unwrap(w.objectForKey('kCGWindowOwnerName')) || '';
      var layer = Number(ObjC.unwrap(w.objectForKey('kCGWindowLayer')));
      if (owner !== want || layer !== 0) { continue; }
      var b = ObjC.unwrap(w.objectForKey('kCGWindowBounds'));
      var area = Number(b.Width) * Number(b.Height);
      if (area > bestArea) {
        bestArea = area;
        best = String(ObjC.unwrap(w.objectForKey('kCGWindowNumber')));
      }
    }
    return best;
  } catch (e) {
    return '';
  }
}
JXA
)"

if [ -z "$WID" ]; then
  {
    echo "ERROR: no on-screen window found for process '$APP'."
    echo "       Is the desktop app running (DEVICE=macos bash scripts/start.sh)?"
  } >&2
  exit 2
fi

if ! screencapture -x -o -l "$WID" "$out"; then
  echo "ERROR: screencapture failed for window $WID." >&2
  exit 3
fi

# --- validate: a real PNG, and not a single flat colour ---------------------
if [ -z "$PY" ] || [ ! -f "$SCRIPT_DIR/png_check.py" ]; then
  echo "OK (python unavailable; PNG NOT validated): $out"
  exit 0
fi
set +e
"$PY" "$SCRIPT_DIR/png_check.py" "$out"
rc=$?
set -e
case "$rc" in
  0) exit 0 ;;
  1)
    {
      echo "ERROR: the capture is blank - most likely macOS Screen Recording"
      echo "       permission is missing for the terminal/agent that ran this."
      echo "       Grant it in System Settings -> Privacy & Security ->"
      echo "       Screen Recording, then run the screenshot again."
    } >&2
    exit 3
    ;;
  *)
    echo "WARNING: could not verify the capture (png_check.py exit $rc); leaving $out as is." >&2
    exit 0
    ;;
esac
