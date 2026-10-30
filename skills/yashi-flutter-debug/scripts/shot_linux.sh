#!/usr/bin/env bash
# Screenshot of the debugged Flutter DESKTOP app on Linux.
#   usage: scripts/shot_linux.sh [out.png]
#
# Normally reached through scripts/shot.sh, which dispatches on the target kind.
#
# X11   : xdotool locates the app's own window, then ImageMagick `import
#         -window <id>` (or gnome-screenshot, or xwd+convert) captures THAT
#         window - not a screen region, so an overlapping window cannot leak in.
# Wayland: window-targeted capture has no portable mechanism (the compositor
#         owns the pixels and the portals are interactive). This script REFUSES
#         instead of grabbing the whole screen or the focused window, because a
#         misleading artifact is worse than an error. It prints the options.
#
# NOT VERIFIED ON A REAL LINUX BOX YET: this path was written on a Windows
# machine. Treat the first real run as a test.
#
# Overridable: APP=<process/title>, PROJECT_DIR=..., PY=...
# Exit codes: 1 = cannot derive app name, 2 = no window found,
#             3 = capture or validation failed, 4 = missing tool,
#             5 = unsupported session (Wayland / headless).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

out="${1:-shot.png}"
PROJECT_DIR="${PROJECT_DIR:-$PWD}"
PY="${PY:-$(command -v python3 || command -v python || true)}"

if [ "$(os_family)" != linux ]; then
  echo "ERROR: shot_linux.sh only runs on Linux (detected: $(os_family))." >&2
  exit 4
fi

# --- refuse Wayland / headless rather than capture the wrong thing -----------
session="${XDG_SESSION_TYPE:-}"
if [ "$session" = "wayland" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
  {
    echo "ERROR: Wayland session detected - window-targeted screenshot has no"
    echo "       portable mechanism there, and this skill will not fall back to"
    echo "       grabbing the whole screen (an occluded or unfocused window would"
    echo "       make the artifact misleading)."
    echo "       Options, to be chosen BY THE USER:"
    echo "         * log out and pick an X11/Xorg session at the login screen, or"
    echo "         * run the desktop app on an X11 machine, or"
    echo "         * take the screenshot by hand (crop it to the app window) and"
    echo "           save it to the expected output path."
  } >&2
  exit 5
fi
if [ -z "${DISPLAY:-}" ]; then
  echo "ERROR: no DISPLAY - this looks headless (SSH without X forwarding?)." >&2
  echo "       Run the debug session on the desktop machine instead." >&2
  exit 5
fi

APP="${APP:-$(desktop_app_name "$PROJECT_DIR")}"
if [ -z "$APP" ]; then
  {
    echo "ERROR: cannot derive the app name from linux/CMakeLists.txt (BINARY_NAME)."
    echo "       Pass it explicitly:  APP=<window title/class> bash scripts/shot_linux.sh $out"
  } >&2
  exit 1
fi

if ! command -v xdotool >/dev/null 2>&1; then
  {
    echo "ERROR: xdotool is required to locate the app window."
    echo "       Debian/Ubuntu: sudo apt install xdotool"
    echo "       Fedora:        sudo dnf install xdotool"
    echo "       Arch:          sudo pacman -S xdotool"
  } >&2
  exit 4
fi

# --- find the largest visible window whose name/class matches APP ------------
WID=""
best_area=0
for id in $(xdotool search --onlyvisible --name "$APP" 2>/dev/null || true) \
          $(xdotool search --onlyvisible --class "$APP" 2>/dev/null || true) \
          $(xdotool search --onlyvisible --classname "$APP" 2>/dev/null || true); do
  geom="$(xdotool getwindowgeometry --shell "$id" 2>/dev/null || true)"
  w="$(printf '%s\n' "$geom" | sed -n 's/^WIDTH=//p')"
  h="$(printf '%s\n' "$geom" | sed -n 's/^HEIGHT=//p')"
  area=$(( ${w:-0} * ${h:-0} ))
  if [ "$area" -gt "$best_area" ]; then
    best_area="$area"
    WID="$id"
  fi
done

if [ -z "$WID" ]; then
  {
    echo "ERROR: no visible window matching '$APP'."
    echo "       List candidates with:  xdotool search --onlyvisible --name ."
    echo "       Then re-run with:      APP=<exact title or class> $0 $out"
  } >&2
  exit 2
fi

# --- capture THAT window ----------------------------------------------------
if command -v import >/dev/null 2>&1; then
  import -window "$WID" "$out"
elif command -v gnome-screenshot >/dev/null 2>&1; then
  # gnome-screenshot -w grabs the FOCUSED window, so raise ours first.
  xdotool windowactivate --sync "$WID" 2>/dev/null || true
  sleep 1
  gnome-screenshot -w -f "$out"
elif command -v xwd >/dev/null 2>&1 && command -v convert >/dev/null 2>&1; then
  xwd -silent -id "$WID" | convert xwd:- "$out"
else
  {
    echo "ERROR: no usable capture tool. Install one of:"
    echo "       imagemagick (provides import/convert), gnome-screenshot, x11-apps (xwd)"
  } >&2
  exit 4
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
      echo "ERROR: the capture is blank."
      echo "       On a composited X11 desktop a window capture can come back"
      echo "       empty; try raising the window first, or capture with a"
      echo "       different tool (gnome-screenshot -w / xwd+convert)."
    } >&2
    exit 3
    ;;
  *)
    echo "WARNING: could not verify the capture (png_check.py exit $rc); leaving $out as is." >&2
    exit 0
    ;;
esac
