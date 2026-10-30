---
name: yashi-flutter-debug
description: Flutter 调试工作流（Android 真机 / Windows、macOS、Linux 桌面 / Web）：建立稳定的 flutter run 调试会话，并获取 Widget Tree、应用日志输出与目标窗口截图。适用于「Flutter 调试」「Android 真机调试」「Flutter 桌面调试（Windows / macOS / Linux）」「查看 widget 树 / Widget Tree」「抓应用日志 / 控制台输出」「调试截图与热重载观测」等场景。
---

# Flutter 调试（Android 真机 / Windows 桌面 / Web）

在**任意** Flutter 项目中完成调试观测：启动会话 → 读 Widget Tree → 抓应用输出 → 截图，并支持"操作后观测"的闭环。

**支持的目标**：Android 真机、桌面（Windows / macOS / Linux）、Web（观测可用，截图会明确报错）。
**跨平台**：脚本可在 **Linux / macOS / Windows(Git Bash)** 运行，依赖 `bash` + `python3`；
`adb` 仅 Android 目标需要；窗口截图按系统分流（Windows: PowerShell+PrintWindow /
macOS: `screencapture -l` + `osascript` / Linux(X11): `xdotool` + ImageMagick）。
**Wayland 会话不支持窗口级截图**，会明确报错而不是截错。

> **已验证程度**：Android 真机与 Windows 桌面两条路径已实测跑通。
> **macOS / Linux 桌面路径已实现但尚未在真实机器上验证**——首次使用时请把它当成一次测试：
> 确认 `screen.png` 确实是那个应用窗口，而不是桌面、别的窗口或空图；异常时脚本会非零退出并说明原因。

**不要写死任何设备名/路径。** 目标一律由 `scripts/targets.sh` 动态发现。

## 前置检查

1. **先跑环境自检**：`bash scripts/preflight.sh`（**只读**，一次性报告工具链、`websocket-client`、许可证、
   可用目标、Android 点击注入权限、桌面截图能力），有问题时逐条打印 `[需询问用户]`；需要细节再用 `flutter doctor -v`。
2. `bash scripts/targets.sh` 列出可用目标，每行 `<id>|<kind>|<label>`（`kind` = `android` / `desktop` / `web`）。
3. `adb` 若不在 PATH，通常位于 `<ANDROID_SDK>/platform-tools/adb`（可用 `ADB=<绝对路径>` 指定）。
4. Python 3 依赖（VM Service 客户端，**所有目标都要用**）：`python3 -m pip install websocket-client`。
   自检：`python3 -c "import websocket; print(websocket.__version__)"`。
5. 脚本优先用 `python3`；若本机 `python3` 是特殊路径，用 `PY=<python 绝对路径>` 覆盖。

### 环境问题的处理原则（重要）

**发现任何环境问题时，不要静默绕过，也不要自作主张"想办法"把它糊过去：先停下来，把「哪一步被拒、影响什么、怎么解决」讲清楚，然后询问用户，等用户处理或明确授权后再继续。**

至少要主动检查、并在异常时停下来询问的项目：

| 检查项 | 命令 | 异常时的处理 |
| --- | --- | --- |
| Android 许可证未接受 | `flutter doctor`（`[!] Android toolchain ... Some Android licenses not accepted`） | 告知用户并由用户执行 `flutter doctor --android-licenses`（交互式逐条按 y）；仅当用户**明确授权**时才可用 `yes \| flutter doctor --android-licenses` 批量接受 |
| Python 依赖缺失 | `python3 -c "import websocket"` | 先问用户装到哪个解释器；注意 msys2 的 Python 常带 PEP 668 `EXTERNALLY-MANAGED` 标记，`pip` 与 `ensurepip` 都会失败，应改用发行版包管理器（如 `pacman -S mingw-w64-<env>-x86_64-python-websocket-client`） |
| Visual Studio 工具链告警 | `flutter doctor`（`[!] Visual Studio`） | Windows 桌面目标需要它才能构建；让用户按 `flutter doctor -v` 修复（`Desktop development with C++` 工作负载） |
| macOS 缺屏幕录制权限 | `scripts/shot_macos.sh` 报 `the capture is blank` | 指导用户在「系统设置 → 隐私与安全性 → 屏幕录制」里给终端/代理授权，然后重跑截图 |
| Linux 处于 Wayland 会话 | `scripts/shot_linux.sh` 退出码 5 | 让用户选：改用 X11/Xorg 会话、换一台 X11 机器、或手动截图。**不要**回退成全屏截图 |
| `flutter run` 构建失败 / 长时间卡住 | `flutter doctor -v`、`run_machine.jsonl` | 见 `references/troubleshooting.md` |
| 真机点击/输入无法注入 | `adb shell input keyevent 0` | 询问并指导用户开启权限，**不要尝试绕过**；见「闭环验证」。**仅 Android 目标有这一项** |
| 没有可用目标 / 多目标歧义 | `scripts/targets.sh` | Android：提示用户连接设备、开启 USB 调试并确认手机弹窗；桌面：确认工具链。多目标时让用户选定 `DEVICE=` |

> 没有插手机**不等于**环境有问题：只接桌面或 Web 目标时，Android 专项检查会自动跳过，不应报成故障。

## 选择目标（关键规则，勿写死）

运行 `bash scripts/targets.sh`，每行输出 `<id>|<kind>|<label>`：

- **只有 1 台 Android 设备** → 自动使用它（历史默认行为）。
- **否则只有 1 个目标** → 使用它。
- **0 个目标** → 提示用户连接设备 / 确认工具链后再试。
- **其余情况（多个目标）** → 把列表展示给用户并让用户选择，不要擅自决定。

`DEVICE=` 既接受 **Android 序列号**（如 `5a42974d`），也接受 **目标 id**（如 `windows`、`chrome`）。
Android 请优先用序列号：`targets.sh` 的 `label`（如 `MI 9`）是型号名，同型号多台会歧义。

**典型：本机同时接了手机和桌面时，`start.sh` 不带 `DEVICE` 会选中手机**（因为它是唯一的 Android 目标）。
想调桌面就必须显式写 `DEVICE=windows`——这是刻意的：真机调试是主要场景，桌面要走显式路径。

## 标准流程

> 下文的 `scripts/` 指**本 skill 的 `scripts/` 目录**（可写成绝对路径）。
> 请在 **Flutter 项目根目录**（含 `pubspec.yaml`）执行；脚本用 `$0` 定位同级文件，故 skill 可放在任意位置。

```bash
# 1) 启动调试会话（后台；JSON 事件流写入 run_machine.jsonl；打印 VM Service wsUri）
bash scripts/start.sh                          # 只有 1 台 Android（或只有 1 个目标）时自动选
DEVICE=<deviceId> bash scripts/start.sh        # 多目标时（用户选定后）显式指定
DEVICE=windows bash scripts/start.sh           # 调 Windows 桌面版

# 2) 一键取三路产物 → artifacts/{widget_tree.txt, app_logs.txt, screen.png}
#    截图会按当前目标自动分流（Android: adb exec-out / 桌面: 只抓应用窗口）
bash scripts/pipeline.sh

# 3) 结束调试会话（桌面目标会连带关闭应用进程，不留孤儿窗口）
bash scripts/stop.sh
```

单独使用：

```bash
bash scripts/targets.sh                                           # 列出可用目标 <id>|<kind>|<label>
python3 scripts/devtools_probe.py --ws <wsUri> tree [--depth N]   # Widget Tree
python3 scripts/devtools_probe.py --ws <wsUri> logs --seconds 8   # 应用输出
python3 scripts/devtools_probe.py --ws <wsUri> isolate            # isolate 信息
python3 scripts/devtools_probe.py --ws <wsUri> rpc getVM          # 任意 RPC（VM 级，不带 isolateId）
python3 scripts/devtools_probe.py --ws <wsUri> rpc getIsolate     # isolate 级：自动补 isolateId
python3 scripts/devtools_probe.py --ws <wsUri> rpc <m> '<json>' --isolate   # 强制注入 isolateId
bash scripts/shot.sh out.png                                      # 截图（按目标分流 + 校验）
```

> `rpc` 只在方法**需要** `isolateId` 时才补它：先按用户给的参数原样调用，服务回报
> `expects the 'isolateId' parameter` 时才重试补参。VM 级方法（`getVM` / `getVersion` …）
> 携带 `isolateId` 会被判 `-32602 Invalid params`，所以**不要**无条件注入。

### 闭环验证（推荐做法，**顺序很重要**）

**想抓到某次操作的输出，必须先开监听、再触发操作**（`logs` 只收到"监听之后"产生的事件）：

```bash
bash scripts/watch_logs.sh 60 out.txt &     # 1) 先开始监听（后台，持续 60s）
adb shell input tap <x> <y>                 # 2) 再操作（Android 目标）
sleep 3; cat out.txt                        # 3) 读输出
bash scripts/shot.sh after.png              # 4) 截图
python3 scripts/devtools_probe.py --ws <wsUri> tree   # 5) 复查 Widget Tree（应反映新状态）
```

> **桌面 / Web 目标没有 `adb shell input tap` 这条路。** 想触发一次操作时：
> 请用户直接在窗口里点（最简单可靠），或改用 `integration_test` / `flutter_driver` 在进程内驱动。
> **不要**用"截屏坐标 + 模拟鼠标"硬凑：窗口可能被遮挡、坐标会漂，得到的结论不可信。
>
> 抓**启动期日志**（`main()` / `initState` 里的输出）同理：**先起监听，再让 App 启动或热重启**。
>
> 补充：`flutter run --machine` 的 `run_machine.jsonl` 里**也包含** App 的输出行
> （含 `main()` / `initState` 的启动期输出），所以"启动日志抓不到"只对 `streamListen`
> 这条路径成立；排障时可以直接翻这个文件。

### 点击/输入无法注入时：先询问，不要绕过（Android 目标）

`adb shell input tap` / `keyevent` / `text` 需要 `INJECT_EVENTS` 权限。部分厂商 ROM
（尤其小米 MIUI / HyperOS）默认不把它授予 `shell`，报错形如：

```
java.lang.SecurityException: Injecting to another application requires INJECT_EVENTS permission
```

**遇到这个报错时：不要换别的注入手法硬凑，也不要悄悄跳过点击这一步、把闭环当成通过了。**
正确做法是：

1. 先判定确实是权限问题（任选其一）：

   ```bash
   adb shell dumpsys package com.android.shell | grep INJECT_EVENTS   # 期望：granted=true
   adb shell input keyevent 0                                        # 报 SecurityException 即被拒
   ```

2. **停下来问用户**：说明「哪一步被拒、会导致什么（点击闭环做不了）、需要在手机上开什么」。
3. 给出该机型的开启路径（见下），让用户操作；用户处理完再回来确认权限已授予。
4. 确认 `INJECT_EVENTS: granted=true` 后**重新验证**一次点击闭环，再继续。

常见厂商的开关位置：

- **小米 / Redmi（MIUI / HyperOS）**：设置 → 更多设置 → 开发者选项 → 打开
  「USB 调试（安全设置）」（部分版本写作「允许通过 USB 调试修改权限或模拟点击」）。
  已实测 MIUI 12.5（Android 11）：未开启时 `adb shell input tap` 直接抛上述
  `SecurityException`，开启后 `dumpsys` 显示 `INJECT_EVENTS: granted=true`、点击正常。
  部分版本还需登录小米账号、插入 SIM 卡才能打开该开关。
- **OPPO / 一加 / realme（ColorOS）**、**vivo / iQOO（OriginOS / Funtouch）**：开发者选项里
  同样有名字相近的「USB 调试（安全设置）」类开关。
- 找不到开关时，让用户顺带确认：已开启「USB 调试」、USB 连接模式不是「仅充电」、
  设备没有被 MDM / 企业管控。

> 只有当用户**明确拒绝**开启该权限时，才与他讨论替代方案（例如让 App 自身用定时器或
> 调试入口主动输出日志）。这类替代方案必须**先征得用户同意**，并说明它不是标准点击闭环。

## 目标平台差异

| 环节 | Android 真机 | 桌面（Windows / macOS / Linux） | Web |
| --- | --- | --- | --- |
| 启动会话 / wsUri | 可用 | 可用 | 可用 |
| Widget Tree / 日志 / RPC | 可用 | 可用 | 可用 |
| 截图 | `adb exec-out screencap -p` | 按系统分流，只抓应用窗口（见下） | **明确报错**（未实现） |
| 触发操作 | `adb shell input tap` | 由用户手点 / `integration_test` | 由用户手点 / `integration_test` |
| 结束会话 | `stop.sh` | `stop.sh`（并关闭应用进程） | `stop.sh` |

桌面截图按系统分流：

| 系统 | 实现 | 前提 / 限制 |
| --- | --- | --- |
| Windows | `shot_windows.ps1`：`PrintWindow(..., PW_RENDERFULLCONTENT)` | 需要 PowerShell（系统自带） |
| macOS | `shot_macos.sh`：`screencapture -l <CGWindowID>`，窗口号由 `osascript`(JXA) 查 CoreGraphics | 需给终端/代理授予**屏幕录制**权限；未授权会得到空图，脚本会报错 |
| Linux (X11) | `shot_linux.sh`：`xdotool` 定位窗口 + `import -window`（或 `gnome-screenshot -w` / `xwd`+`convert`） | 需 `xdotool` 与任一截图工具 |
| Linux (Wayland) | **不支持**，直接 `exit 5` 并给出选项 | 合成器掌握像素，门户是交互式的；不提供回退，以免截错 |

> 截图分流由 `run_machine.jsonl` 里的 `deviceId` 决定。目标无法判定时 `shot.sh` **直接报错退出**，
> 绝不回退到 `adb`——否则会静默截到手机。抓到**空图/纯色图**同样按失败处理（`png_check.py`）。

## 原理速查

- 一切观测都走 **Dart VM Service**（地址形如 `ws://127.0.0.1:<port>/<token>/ws`），DevTools 底层也是它。
  **这一层与目标平台无关**，Android / 桌面 / Web 完全一致。
- **Widget Tree** = RPC `ext.flutter.debugDumpApp`（返回文本树）。
- **应用输出** = `streamListen` 的 `Stdout` / `Stderr` / `Logging` 流；事件的 `bytes` 字段是 **base64**，必须解码后再打印。
- **VM Service URI** 从 `flutter run --machine` 的 `app.debugPort` 事件里取（字段 `"wsUri"`）；用 `--machine` 比 `--verbose` 更好解析。
- **截图**：Android 必须用 `adb exec-out screencap -p`（`adb shell screencap -p` 会被 CRLF 转义破坏 PNG）；
  桌面一律**只抓应用自己的窗口**：Windows 用 `user32.PrintWindow(..., 2)`，macOS 用 `screencapture -l <CGWindowID>`，
  Linux(X11) 用 `xdotool` + `import -window <id>`。三者都能拿到窗口自身内容，不受遮挡影响。
  **不要用 BitBlt / `CopyFromScreen` / `screencapture -R` 区域截图**：窗口被遮挡时会抓到遮挡它的窗口，
  而且那等于截屏。落盘后用 `png_check.py` 复核「是不是真 PNG、是不是整块纯色」（纯色＝抓空）。
- 需要先拿到 `isolateId`（`getVM` → `isolates[0].id`）才能调用 `ext.flutter.*` 服务扩展。
  但 `getVM` / `getVersion` 这类 **VM 级** RPC 本身**不接受** `isolateId`，只有 isolate 级方法才需要它。

## 脚本清单

| 脚本 | 作用 |
| --- | --- |
| `scripts/targets.sh` | 列出可用目标：`<id>\|kind\|label`（`kind` = android / desktop / web） |
| `scripts/preflight.sh` | **只读**环境自检（按目标类型分支）；问题逐条标 `[需询问用户]` |
| `scripts/start.sh` | 选择目标并后台启动 `flutter run --machine`，输出 wsUri（含盘符预检） |
| `scripts/stop.sh` | 结束调试会话（跨平台：PowerShell / `pkill`，优先按 pidfile 清理；Windows 桌面目标连带关闭应用进程） |
| `scripts/ws_uri.sh` | 从事件流提取 VM Service wsUri |
| `scripts/devtools_probe.py` | VM Service 客户端：`tree` / `logs` / `isolate` / `rpc` |
| `scripts/watch_logs.sh` | 先开监听、后操作的持续日志捕获（避免漏掉触发瞬间的输出） |
| `scripts/shot.sh` | 截图**分流器**：按目标类型与系统分派，无法判定时明确报错 |
| `scripts/shot_android.sh` | Android 截图：`adb exec-out` + PNG 签名校验 |
| `scripts/shot_windows.ps1` | Windows 桌面截图：只抓应用窗口（PrintWindow），空图/找不到窗口一律报错 |
| `scripts/shot_macos.sh` | macOS 桌面截图：`screencapture -l` + osascript(JXA) 查窗口号（**未在真机验证**） |
| `scripts/shot_linux.sh` | Linux 桌面截图：X11 用 xdotool+import；Wayland 明确拒绝（**未在真机验证**） |
| `scripts/png_check.py` | 校验截图：是否真 PNG、是否整块纯色（抓空）；无法分析时返回 3 而不是谎报空白 |
| `scripts/pipeline.sh` | 一键产出三路产物并打印 `OUTPUT=` 绝对路径 |
| `scripts/lib.sh` | 公共函数（PY/ADB 探测、OS 判定、目标分类、跨平台绝对路径、盘符预检），被其它脚本 source |

## 常见陷阱

详见 `references/troubleshooting.md`。最高频的几条：

1. **构建网络**：`dl.google.com` 在部分网络（尤其国内）下 IPv6 会挂起，而 HTTP 代理也未必能为它建 TLS 隧道。优先改用 Maven 镜像（如阿里云 `repository/{google,public,gradle-plugin}`），或换到能直连 IPv4 的网络。
2. **项目位置**：**不要**把项目放在 RAM 盘或非本地 NTFS 卷上，Gradle 9 会误报 `configured projectDirectory ... does not exist`（目录其实存在）。
   `start.sh` 已内置**盘符预检**：命中内存盘（卷名如 `RamDisk`）/ ReFS / 可移动 / 网络盘 / 空间过小会打印 `WARNING`（**不阻断**，可 `SKIP_DISK_CHECK=1` 静音）。看到告警就应把项目挪到本地 NTFS 固定盘再构建。
3. **`--verbose` 非必要**：正常调试用 `--debug` 即可，`--verbose` 日志量巨大，仅在排障时使用。
4. **真机点击注入被拒**：`adb shell input tap` 报 `INJECT_EVENTS` 权限错误时，**先询问并指导用户开启该权限**（小米 / Redmi 见「闭环验证」），不要绕过。
5. **桌面目标下别用 adb 截图**：桌面目标没有"设备"，`adb exec-out` 会抓到插着的手机或直接失败。本 skill 的 `shot.sh` 已按目标分流并在无法判定时报错——**不要**自己拼 adb 命令替代它。
6. **桌面截图要看权限与会话类型**：macOS 缺「屏幕录制」权限会得到空图（脚本按失败处理）；Linux 在 Wayland 下没有通用的窗口截图手段（脚本直接拒绝）。两者都不要试图"先截个全屏再说"。
7. **`__pycache__` 会干扰判断**：`scripts/` 下运行过 Python 会生成 `__pycache__`；它不影响功能，但会让索引工具把该 skill 判成"文件有变"，需要时删掉即可。

## 使用要点

- 在 **Flutter 项目根目录**运行脚本（cwd = 项目）；脚本用 `$0` 定位同级文件，故 skill 可放在任意位置。也可用 `PROJECT_DIR=<path>` 指定项目目录。
- 多目标场景：先 `targets.sh` 列目标 → 问用户 → 记下 `DEVICE=` → 再执行后续命令。
- **结束调试**统一用 `bash scripts/stop.sh`（跨平台）；`start.sh` 启动新会话前也会自动调用它清理旧会话。会话 PID 记录在 `<项目>/.flutter_debug.pid`。
- **构建很慢时**：`start.sh` 默认等待 `WAIT_SECS=900` 秒（15 分钟）拿 VM Service URI。超时**不会中断后台会话**；构建完成后可直接 `bash scripts/ws_uri.sh run_machine.jsonl` 取 URI，或直接跑 `scripts/pipeline.sh`。需要更久用 `WAIT_SECS=<秒>`。
- **平台差异**：Linux / macOS 直接用 `python3`、`adb`、`pkill`；Windows 用 **Git Bash** 运行（无 `pkill` 时自动走 PowerShell）。若系统没有 `python3` 命令，用 `PY=<python 绝对路径>` 覆盖。
- 日志类需求牢记：**先 `watch_logs.sh`，后操作**。
- **环境问题先问用户**：`flutter doctor` 报出的许可证问题、依赖缺失、权限被拒、构建失败等任何异常，都先说明影响并询问用户，由用户处理或明确授权后再继续，不要静默绕过或假装成功。
