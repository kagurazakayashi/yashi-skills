#!/usr/bin/env bash
# Stop the `flutter run --machine` debug session started by start.sh.
# Cross-platform: Linux / macOS / Windows(Git Bash). Safe to run when none.
#   usage: scripts/stop.sh          (PROJECT_DIR=..., to locate the pidfile)
#
# On a DESKTOP target the app runs as its own executable rather than as a child
# of the flutter tool, so killing the tool alone leaves an orphaned window
# behind. We therefore also stop the project's own app process on every OS,
# named after the project's generated runner files (windows/CMakeLists.txt
# BINARY_NAME, linux/CMakeLists.txt BINARY_NAME, macOS AppInfo.xcconfig
# PRODUCT_NAME) - derived, never guessed, so an unrelated same-named process is
# not swept up.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

PROJECT_DIR="${PROJECT_DIR:-$PWD}"
PIDFILE="$PROJECT_DIR/.flutter_debug.pid"
killed=0

# 1) Kill the session PID recorded by start.sh (best effort) + its children.
if [ -f "$PIDFILE" ]; then
  pid="$(tr -dc '0-9' < "$PIDFILE" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    pkill -P "$pid" 2>/dev/null || true
    kill "$pid" 2>/dev/null || true
    killed=1
  fi
  rm -f "$PIDFILE" 2>/dev/null || true
fi

# 2) OS-specific sweep for the flutter tool process.
case "$(os_family)" in
  windows)
    if command -v powershell >/dev/null 2>&1; then
      cnt="$(powershell -NoProfile -Command "@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { \$_.Name -eq 'dart.exe' -and \$_.CommandLine -match 'flutter' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }).Count" 2>/dev/null | tr -d '\r' || true)"
      if [ -n "$cnt" ] && [ "$cnt" -gt 0 ] 2>/dev/null; then killed=1; fi
    else
      pkill -f "flutter run --machine" 2>/dev/null && killed=1 || true
    fi
    ;;
  *)
    # macOS / Linux: match the dart VM running flutter_tools, and the CLI form.
    pkill -f "flutter_tools.snapshot" 2>/dev/null && killed=1 || true
    pkill -f "flutter run --machine" 2>/dev/null && killed=1 || true
    ;;
esac

# 3) Desktop target: also close the app's own process (its window). A desktop
#    app is NOT a child of the flutter tool, so the sweeps above leave an
#    orphaned window behind. The name is derived from this project's generated
#    files (lib.sh:desktop_app_name), never guessed.
bin="$(desktop_app_name "$PROJECT_DIR")"
if [ -n "$bin" ]; then
  case "$(os_family)" in
    windows)
      if command -v powershell >/dev/null 2>&1; then
        cnt="$(powershell -NoProfile -Command "\$p = @(Get-Process -Name '$bin' -ErrorAction SilentlyContinue); \$p | Stop-Process -Force -ErrorAction SilentlyContinue; \$p.Count" 2>/dev/null | tr -d '\r' || true)"
        if [ -n "$cnt" ] && [ "$cnt" -gt 0 ] 2>/dev/null; then
          killed=1
          echo "stopped desktop app process: $bin"
        fi
      fi
      ;;
    *)
      # macOS: the app runs as <App>.app/Contents/MacOS/<PRODUCT_NAME>.
      # Linux: it runs as build/linux/.../bundle/<BINARY_NAME>.
      # `pkill -x` matches the short comm name (Linux truncates it at 15 chars),
      # so fall back to an anchored full-path match.
      if pkill -x "$bin" 2>/dev/null; then
        killed=1
        echo "stopped desktop app process: $bin"
      elif pkill -f "/${bin}\$" 2>/dev/null; then
        killed=1
        echo "stopped desktop app process: $bin"
      fi
      ;;
  esac
fi

if [ "$killed" -gt 0 ] 2>/dev/null; then
  echo "stopped debug session."
else
  echo "no running flutter debug session found."
fi
