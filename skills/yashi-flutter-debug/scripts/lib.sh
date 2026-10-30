#!/usr/bin/env bash
# Shared helpers for yashi-flutter-debug scripts.
# Sourced (never executed directly). Cross-platform: Linux / macOS / Windows(Git Bash).

# ---- tool / interpreter detection ----------------------------------------
# PY: python3 preferred; override with PY=<abs path>.
detect_py() {
  if [ -n "${PY:-}" ]; then printf '%s\n' "$PY"; return 0; fi
  command -v python3 2>/dev/null || command -v python 2>/dev/null || true
}

# ADB: override with ADB=<abs path>.
detect_adb() {
  if [ -n "${ADB:-}" ]; then printf '%s\n' "$ADB"; return 0; fi
  command -v adb 2>/dev/null || echo adb
}

# ---- target kind ---------------------------------------------------------
# Coarse target classification, used to pick the right screenshot / cleanup
# path. Args: a Flutter device id (e.g. 5a42974d / windows / chrome).
# Prints: android | desktop | web (empty for an empty id).
target_kind_of() {
  case "$1" in
    windows|macos|linux)        echo desktop ;;
    chrome|edge|web-server|web) echo web ;;
    "")                         echo "" ;;
    *)                          echo android ;;
  esac
}

# Read the device id recorded by start.sh's `flutter run --machine` session.
# Args: [log file] (default: ./run_machine.jsonl)
target_id_from_log() {
  local log="${1:-run_machine.jsonl}"
  grep -oE '"deviceId":"[^"]+"' "$log" 2>/dev/null | tail -1 | sed 's/.*"deviceId":"//; s/"$//'
}

# ---- desktop app name ----------------------------------------------------
# Derive the running desktop app's process/binary name for THIS project, per OS.
# It is always derived from the project's own generated files, never guessed:
#   windows : windows/CMakeLists.txt        set(BINARY_NAME "x")
#   linux   : linux/CMakeLists.txt          set(BINARY_NAME "x")
#   macos   : macos/Runner/Configs/AppInfo.xcconfig   PRODUCT_NAME = x
# Prints the name, or nothing when it cannot be derived (caller decides).
# Args: [project dir] (default: $PWD)
desktop_app_name() {
  local dir="${1:-$PWD}" f="" n=""
  case "$(os_family)" in
    macos)
      f="$dir/macos/Runner/Configs/AppInfo.xcconfig"
      if [ -f "$f" ]; then
        n="$(grep -E '^[[:space:]]*PRODUCT_NAME[[:space:]]*=' "$f" 2>/dev/null | head -1 | sed 's/^[^=]*=[[:space:]]*//' | tr -d '\r')"
      fi
      ;;
    linux)
      f="$dir/linux/CMakeLists.txt"
      if [ -f "$f" ]; then
        n="$(grep -oE 'set\(BINARY_NAME[[:space:]]+"[^"]+"' "$f" 2>/dev/null | head -1 | sed 's/.*"\(.*\)"/\1/')"
      fi
      ;;
    *)
      f="$dir/windows/CMakeLists.txt"
      if [ -f "$f" ]; then
        n="$(grep -oE 'set\(BINARY_NAME[[:space:]]+"[^"]+"' "$f" 2>/dev/null | head -1 | sed 's/.*"\(.*\)"/\1/')"
      fi
      ;;
  esac
  printf '%s\n' "$n"
}

# ---- OS ------------------------------------------------------------------
# prints: windows | macos | linux
os_family() {
  case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*) echo windows ;;
    Darwin)               echo macos ;;
    *)                    echo linux ;;
  esac
}

# ---- paths ---------------------------------------------------------------
# Portable absolute path. Windows Git Bash -> native form (F:\x\y);
# elsewhere -> POSIX. Uses cd+pwd, so it also works on macOS (no realpath).
abs_path() {
  local p="$1" dir base full
  case "$p" in
    /*|[A-Za-z]:*) ;;
    *) p="$(pwd)/$p" ;;
  esac
  dir="$(dirname "$p")"
  base="$(basename "$p")"
  full="$(cd "$dir" 2>/dev/null && pwd)/$base"
  if [ "$(os_family)" = windows ] && command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$full" 2>/dev/null || printf '%s\n' "$full"
  else
    printf '%s\n' "$full"
  fi
}

# ---- disk preflight (advisory only, never blocks) ------------------------
# Gradle 9 on Windows misreports a valid project dir as missing when the
# project sits on a RAM disk / non-NTFS (e.g. ReFS) / removable / network
# volume. This is Windows-only; a harmless no-op on macOS/Linux.
# Set SKIP_DISK_CHECK=1 to silence the warning.
check_project_disk() {
  local dir="$1" letter info fs dt vol free free_int warn=""

  if [ -n "${SKIP_DISK_CHECK:-}" ]; then return 0; fi
  if [ "$(os_family)" != windows ]; then return 0; fi
  if ! command -v powershell >/dev/null 2>&1; then return 0; fi

  # Map a MSYS/POSIX path (/f/foo) or a native path (F:\foo) to a drive letter.
  case "$dir" in
    /[A-Za-z]|[A-Za-z]:)   letter="$(printf '%s' "${dir:1:1}" | tr 'a-z' 'A-Z'):" ;;
    /[A-Za-z]/*)           letter="$(printf '%s' "${dir:1:1}" | tr 'a-z' 'A-Z'):" ;;
    [A-Za-z]:*)            letter="$(printf '%s' "${dir:0:2}" | tr 'a-z' 'A-Z')" ;;
    *) return 0 ;;
  esac

  info="$(powershell -NoProfile -Command "\$d = Get-CimInstance Win32_LogicalDisk -Filter \"DeviceID='$letter'\"; if (\$d) { '{0}|{1}|{2}|{3}' -f \$d.FileSystem, \$d.DriveType, \$d.VolumeName, [math]::Round(\$d.FreeSpace/1GB,1) }" 2>/dev/null | tr -d '\r' | head -1 || true)"
  if [ -z "$info" ]; then return 0; fi

  fs="$(printf '%s' "$info"  | cut -d'|' -f1)"
  dt="$(printf '%s' "$info"  | cut -d'|' -f2)"
  vol="$(printf '%s' "$info" | cut -d'|' -f3)"
  free="$(printf '%s' "$info"| cut -d'|' -f4)"

  if printf '%s' "$vol" | grep -qiE 'ram[ _-]?disk|ramdrive|imdisk'; then
    warn="卷名 '$vol' 疑似内存盘（RAM disk）"
  elif [ "$fs" != "NTFS" ]; then
    warn="文件系统为 $fs（非 NTFS，如 ReFS）"
  elif [ "$dt" = "2" ]; then
    warn="可移动磁盘"
  elif [ "$dt" = "4" ]; then
    warn="网络驱动器"
  elif [ "$dt" = "6" ]; then
    warn="内存盘"
  else
    free_int="${free%%.*}"
    if [ -n "$free_int" ] && [ "$free_int" -lt 2 ] 2>/dev/null; then
      warn="剩余空间仅 ${free}GB"
    fi
  fi

  if [ -n "$warn" ]; then
    {
      echo "WARNING: 项目所在卷可能有风险（$letter）：$warn"
      echo "         Gradle 9 可能误报 'projectDirectory ... does not exist'。"
      echo "         建议把项目移到本地 NTFS 固定盘（如 C:/D:/F:）再构建。"
      echo "         确认无碍时可设 SKIP_DISK_CHECK=1 忽略本提示。"
    } >&2
  fi
  return 0
}
