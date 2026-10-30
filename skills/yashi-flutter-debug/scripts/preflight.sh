#!/usr/bin/env bash
# Environment preflight for the yashi-flutter-debug skill.
#
# READ-ONLY: it never installs, accepts, or changes anything. It only reports,
# and marks every problem with "[需询问用户]" so the agent knows it must stop
# and ask the user instead of silently working around it.
#
# Target-aware: Android-only checks (adb / licenses / INJECT_EVENTS) are skipped
# when no Android target exists, so a desktop-only or web-only machine is not
# reported as broken just because no phone is attached.
#
#   usage: bash scripts/preflight.sh          (PY=/ADB= overridable, see lib.sh)
#
# Exit code is always 0: the report is advisory, a non-zero code here would be
# mistaken for a broken script. Read the PREFLIGHT: summary line instead.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"

problems=0
say()    { printf '  %s\n' "$1"; }
action() { problems=$((problems + 1)); printf '  [需询问用户] %s\n' "$1"; }

echo "== 工具链 =="
have_flutter=0
if command -v flutter >/dev/null 2>&1; then
  say "flutter : $(command -v flutter)"; have_flutter=1
else
  action "未找到 flutter，请安装或加入 PATH 后再继续。"
fi

ADB_BIN="$(detect_adb)"
have_adb=0
if command -v "$ADB_BIN" >/dev/null 2>&1; then
  say "adb     : $ADB_BIN"; have_adb=1
else
  say "adb     : （未找到；只有 Android 目标才需要）"
fi

PY_BIN="$(detect_py)"
if [ -z "$PY_BIN" ]; then
  action "未找到 python3/python，可用 PY=<python 绝对路径> 指定。"
else
  say "python  : $PY_BIN"
  if "$PY_BIN" -c "import websocket" 2>/dev/null; then
    say "websocket-client : $("$PY_BIN" -c 'import websocket; print(websocket.__version__)' 2>/dev/null)"
  else
    action "该 python 缺 websocket-client（VM Service 客户端必需，Android / 桌面 / Web 目标都要用）。先问用户装到哪个解释器；注意 msys2 的 Python 带 PEP 668 EXTERNALLY-MANAGED，pip 与 ensurepip 都会被拒，应改用 pacman -S mingw-w64-<env>-x86_64-python-websocket-client。"
  fi
fi

echo "== flutter doctor =="
if [ "$have_flutter" -eq 1 ]; then
  doc="$(flutter doctor 2>&1 || true)"
  printf '%s\n' "$doc" | grep -E '^\[.\]' | sed 's/^/  /'
  if printf '%s\n' "$doc" | grep -qi 'licenses not accepted'; then
    action "Android 许可证未全部接受。让用户执行 flutter doctor --android-licenses（交互式逐条按 y）；仅当用户明确授权时才用 yes | flutter doctor --android-licenses 批量接受。未接受不一定会立刻阻塞构建，不要夸大也不要谎报成功。"
  fi
  if printf '%s\n' "$doc" | grep -qE '^\[!\] Visual Studio'; then
    action "Visual Studio 工具链有告警，而 Windows 桌面目标需要它才能构建。让用户按 flutter doctor -v 的提示修复（需要 Desktop development with C++ 工作负载）。"
  fi
else
  say "（跳过：没有 flutter）"
fi

echo "== 可用目标 =="
targets=""
if [ "$have_flutter" -eq 1 ]; then targets="$(bash "$SCRIPT_DIR/targets.sh" 2>/dev/null || true)"; fi
android_n=0; desktop_n=0; total=0
if [ -n "$targets" ]; then
  printf '%s\n' "$targets" | sed 's/^/  /'
  total="$(printf '%s\n' "$targets" | grep -c . || true)"
  android_n="$(printf '%s\n' "$targets" | awk -F'|' '$2=="android"' | grep -c . || true)"
  desktop_n="$(printf '%s\n' "$targets" | awk -F'|' '$2=="desktop"' | grep -c . || true)"
  if [ "$total" -gt 1 ]; then
    say "有多种目标可选：启动调试时用 DEVICE=<id> 明确指定（例如 DEVICE=windows）；只有唯一一台 Android 时才会自动选中。"
  fi
else
  action "没有检测到任何 Flutter 目标。Android：让用户连接设备、开启 USB 调试并确认手机上的授权弹窗；桌面：确认工具链（Windows 需要 Visual Studio 的 C++ 桌面工作负载）。"
fi

echo "== Android 专项 =="
if [ "$android_n" -eq 0 ]; then
  say "（跳过：当前没有 Android 目标）"
elif [ "$have_adb" -eq 0 ]; then
  action "有 Android 目标但找不到 adb。通常在 <ANDROID_SDK>/platform-tools/adb，可用 ADB=<绝对路径> 指定。"
else
  dev="$(printf '%s\n' "$targets" | awk -F'|' '$2=="android"{print $1; exit}')"
  # KEYCODE_UNKNOWN(0) 是空操作按键，仅用来触发一次注入以探测权限，不会改变 App 状态。
  probe="$("$ADB_BIN" -s "$dev" shell input keyevent 0 2>&1 || true)"
  if printf '%s' "$probe" | grep -q 'INJECT_EVENTS'; then
    action "shell 未获 INJECT_EVENTS：adb shell input tap/keyevent/text 会被拒（$dev）。不要绕过——询问并指导用户开启该权限。小米 / Redmi：开发者选项 → 「USB 调试（安全设置）」（部分版本写作「允许通过 USB 调试修改权限或模拟点击」）。"
  else
    say "adb shell input 自检通过（$dev）"
  fi
fi

echo "== 桌面专项 =="
if [ "$desktop_n" -eq 0 ]; then
  say "（跳过：当前没有桌面目标）"
else
  case "$(os_family)" in
    windows)
      if command -v powershell >/dev/null 2>&1; then
        say "截图走 PowerShell + PrintWindow，只抓应用自己的窗口"
      else
        action "Windows 桌面目标需要 PowerShell 才能截图（scripts/shot_windows.ps1）。请用户确认 PowerShell 可用。"
      fi
      ;;
    macos)
      if command -v screencapture >/dev/null 2>&1 && command -v osascript >/dev/null 2>&1; then
        say "截图走 screencapture -l + osascript(JXA)，只抓应用自己的窗口"
        say "前提：终端/代理已获「屏幕录制」权限（系统设置 → 隐私与安全性 → 屏幕录制）；未授权会抓到空图，脚本会报错而不是当成功"
      else
        action "macOS 桌面目标需要系统自带的 screencapture 与 osascript（正常都有）——缺失说明系统异常，请用户确认。"
      fi
      ;;
    linux)
      if [ "${XDG_SESSION_TYPE:-}" = "wayland" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
        action "当前是 Wayland 会话：窗口级截图没有通用做法，shot_linux.sh 会明确报错（exit 5）而不是截错。请用户选：改用 X11/Xorg 会话、换一台 X11 机器、或手动截图。"
      elif [ -z "${DISPLAY:-}" ]; then
        action "没有 DISPLAY（像是 headless / SSH 无 X 转发）：桌面截图做不了。请在桌面机器上跑调试会话。"
      else
        if command -v xdotool >/dev/null 2>&1; then
          say "X11 会话：xdotool 已就绪（用于定位应用窗口）"
        else
          action "X11 会话需要 xdotool 才能定位窗口。Debian/Ubuntu: sudo apt install xdotool；Fedora: sudo dnf install xdotool；Arch: sudo pacman -S xdotool"
        fi
        if command -v import >/dev/null 2>&1 || command -v gnome-screenshot >/dev/null 2>&1 || command -v xwd >/dev/null 2>&1; then
          say "截图工具已就绪（import / gnome-screenshot / xwd 至少有一个）"
        else
          action "缺少截图工具。装其一即可：imagemagick（含 import/convert）、gnome-screenshot、或 x11-apps（xwd）"
        fi
      fi
      ;;
    *)
      say "（未知桌面平台：$(os_family)）"
      ;;
  esac
fi

echo
if [ "$problems" -eq 0 ]; then
  echo "PREFLIGHT: 全部通过，可以继续。"
else
  echo "PREFLIGHT: 有 $problems 项需要处理 —— 请把上面每条 [需询问用户] 的内容讲清楚（哪一步被拒、影响什么、怎么解决）并询问用户，等用户处理或授权后再继续。"
fi
exit 0
