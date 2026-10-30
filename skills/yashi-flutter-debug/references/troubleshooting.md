# 排障手册：Flutter Android 真机调试

本文件记录实战中真实踩到的坑与判定方法，供 `SKILL.md` 引用。
脚本适用于 Linux / macOS / Windows(Git Bash)；平台特有差异会单独标注。

## 1. 构建网络：`dl.google.com` 解析失败 / 卡住

**症状**：`flutter run` 报
`Plugin [id: 'com.android.application', version: 'X'] was not found` /
`Could not resolve ... Google` / 构建长时间卡在 Starting Build。

**判定**：分别测三条路（`curl`、Java、代理），常见结果是
`dl.google.com` 只有 IPv4 可达，IPv6 直接挂起；而本地 HTTP 代理虽能连通 maven，
却无法为 `dl.google.com` 建立 TLS 隧道。→ **代理方案无效。**

```bash
curl -sI --max-time 6 https://dl.google.com/ -o /dev/null -w "direct %{http_code}\n"
curl -sI --max-time 6 -x http://127.0.0.1:PORT https://dl.google.com/ -o /dev/null -w "proxy %{http_code}\n"
```

**解决**：改用可用的 Maven 镜像（示例为阿里云），插到各仓库列表最前面。

Kotlin DSL（`android/settings.gradle.kts` 的 `pluginManagement.repositories`
以及 `android/build.gradle.kts` 的 `allprojects.repositories`）：

```kotlin
repositories {
    maven("https://maven.aliyun.com/repository/google")
    maven("https://maven.aliyun.com/repository/public")
    maven("https://maven.aliyun.com/repository/gradle-plugin")
    google(); mavenCentral(); gradlePluginPortal()
}
```

Groovy 版：`maven { url 'https://maven.aliyun.com/repository/google' }`。

> 校验镜像是否真的提供该构件：直接取 marker/real pom，返回 200 即可用。
> `https://maven.aliyun.com/repository/google/com/android/application/com.android.application.gradle.plugin/<ver>/com.android.application.gradle.plugin-<ver>.pom`

## 2. 项目位置：Gradle 报 `projectDirectory ... does not exist`

**症状**：Gradle 9 在构建时报
`Configuring project ':' without an existing directory is not allowed.
The configured projectDirectory '<path>/android' does not exist ...`，
但该目录**确实存在**。

**根因（Windows 特有）**：项目放在了 **RAM 盘**或**非本地 NTFS 卷**上
（`fsutil fsinfo volumeinfo` 往往报 `Error 5: Access is denied`——
内存盘/ReFS 不支持大小写敏感等查询）。

**判定**：
```bash
# 一键查所有卷（Windows）
powershell -NoProfile -Command "Get-CimInstance Win32_LogicalDisk | Select DeviceID,FileSystem,DriveType,VolumeName"
```
- `VolumeName` 为 `RamDisk` 之类 → RAM 盘。
- `FileSystem` 为 `ReFS`（或网络/可移动卷）→ 也可能触发。

实测：某机器 `B:` 为 `VolumeName=RamDisk, FileSystem=NTFS, DriveType=3`，
大小仅 8GB；在此盘上构建必失败，换到本地 NTFS 固定盘（`F:`）后一次通过。

**解决**：把项目搬到常规固定盘（如 `C:` / `D:` / `F:`）再构建。
**不要在 RAM 盘 / ReFS / 网络盘上构建 Flutter/Gradle 项目。**

**已内置自动预检**：`scripts/start.sh` 在构建前调用 `check_project_disk`
（`scripts/lib.sh`），命中 RAM 盘（卷名匹配 `ramdisk`/`ramdrive`/`imdisk`）、
非 NTFS、可移动/网络盘或剩余空间 < 2GB 时打印 `WARNING` 并给出迁移建议。
该提示**不阻断**构建；确认无碍可设 `SKIP_DISK_CHECK=1` 静音。
> 注意：预检是启发式（Windows 专用），macOS/Linux 上自动跳过。

## 3. 截图：按目标分流，绝不静默截错

`scripts/shot.sh` 是**分流器**：按当前目标（从 `run_machine.jsonl` 的 `deviceId` 读出）决定怎么截。
目标无法判定时它**直接报错退出**，不会回退到 adb。

### Android 目标：`adb shell` vs `adb exec-out`

- `adb exec-out screencap -p > x.png` → 二进制安全，正常 PNG。
- `adb shell screencap -p > x.png` → 若经 shell/管道做 CRLF 转换，PNG 被破坏
  （签名从 `\x89PNG\r\n\x1a\n` 变成 `\x89PNG\r\r\n\x1a`）。

**始终用 `exec-out`**，并在落盘后校验签名（`scripts/shot_android.sh` 已内置，
且会用会话记录的序列号加 `-s`，避免多台手机时 `adb` 报 "more than one device"）。

### 桌面目标：只抓应用自己的窗口（三系统各自的做法）

共同原则：抓**窗口自身内容**，不要抓屏幕区域。

- **Windows**：`user32.PrintWindow(hWnd, hdc, PW_RENDERFULLCONTENT=2)`，不受遮挡影响。
  进程名 = `windows/CMakeLists.txt` 的 `BINARY_NAME`（默认即项目名），窗口句柄取 `MainWindowHandle`
  （窗口刚创建时可能为 0，需要重试几轮）。
- **macOS**：`screencapture -x -o -l <CGWindowID>`。窗口号由 `osascript`（JXA）经 CoreGraphics
  `CGWindowListCopyWindowInfo` 查出（取该进程最大的 layer 0 窗口），不需要 pyobjc / Homebrew。
  **前提**：运行它的终端/代理要有「屏幕录制」权限；没权限时 `screencapture` 会产出空图，脚本按失败处理。
- **Linux (X11)**：`xdotool search --onlyvisible --name|--class|--classname <APP>` 找窗口（取面积最大的），
  再用 ImageMagick `import -window <id>`；没有 `import` 时退回 `gnome-screenshot -w`
  （先 `xdotool windowactivate --sync`）或 `xwd -silent -id <id> | convert xwd:- out.png`。
- **Linux (Wayland)**：**不做**。合成器掌握像素、门户是交互式的，没有可移植的窗口级截图手段；
  脚本 `exit 5` 并列出选项（改用 X11/Xorg 会话 / 换 X11 机器 / 手动截图），而不是回退成全屏。

**不要用 BitBlt / `Graphics.CopyFromScreen` / `screencapture -R` 区域截图**：它们复制窗口矩形内的
屏幕像素，窗口被别的窗口盖住时会把遮挡者截进去（实测抓到过无关的前台程序），而且那本质是截屏。

**落盘后必须复核**（`png_check.py`）：是不是真 PNG、是不是整块纯色。纯色＝抓空，按失败处理；
脚本解析不了该 PNG 变体时返回 3，此时只警告、不谎报空白。

> 为什么不"两者都试"：Android 目标旁边常常真的插着手机，而 `adb exec-out` 只要**有**手机就必然成功。
> 于是桌面目标下会静默产出手机的截图并打印 `OK PNG`。**宁可报错，也不要给错的产物。**

## 4. Widget Tree / 日志：协议细节

- 调用 `ext.flutter.*` 服务扩展前必须先拿 `isolateId`（`getVM` → `isolates[0].id`）。
- Widget Tree 用 `ext.flutter.debugDumpApp`（返回文本树）。
- 应用输出用 `streamListen` 的 `Stdout`/`Stderr`/`Logging`；事件 `kind == "WriteEvent"`
  的 `bytes` 字段是 **base64**，必须 `base64.b64decode` 再解码成文本，否则打印出乱码串。
- 需要在会话保持期间持续读取；WebSocket 收不到消息时用 `settimeout` 控制读取节奏。

### 抓不到日志？——先开监听，再操作

`streamListen` **只推送"监听建立之后"产生的事件**，不回放历史。因此：

- 要抓某次点击/输入的输出 → **先** `scripts/watch_logs.sh 60 out.txt &`，**再**触发操作。
  Android 用 `adb shell input tap ...`；**桌面 / Web 目标没有这条路**（见第 11 节），
  请用户直接手点，或用 `integration_test` / `flutter_driver` 在进程内驱动。
- 要抓启动期日志（`main()` / `initState`）→ 先起监听，再启动/热重启 App；
  若先跑到首帧再监听，启动日志已错过，属预期行为，不是 bug。
- `scripts/pipeline.sh` 的 `logs` 步骤同理：它是"抓这几秒内"的输出，不是历史日志。
  可用 `LOGSECS=<秒>` 调整抓取时长。

## 5. 会话保持：后台跑 `flutter run --machine`

`--machine` 模式下 `flutter run` 会从 stdin 读取 JSON 命令。若后台运行且 stdin 关闭，
进程可能退出。用 `tail -f /dev/null | flutter run ...` 保持 stdin 打开
（Linux / macOS / Git Bash 通用）。
VM Service URI 从 `app.debugPort` 事件的 `"wsUri"` 取。

### 构建很慢 / `start.sh` 报 "no VM Service URI"

首次构建（尤其 Gradle 冷启动、磁盘较慢、或需要下载依赖时）可能耗时十几分钟。
`start.sh` 默认只等 `WAIT_SECS=900` 秒，超时会打印 `ERROR: no VM Service URI` 并 `exit 3`，
但 **后台的 `flutter run` 仍在继续跑**：

- 稍后再取 URI：`bash scripts/ws_uri.sh run_machine.jsonl`；
  直接跑 `scripts/pipeline.sh` 也行（它自己会读 URI）。
- 想等更久：`WAIT_SECS=1800 bash scripts/start.sh`。
- 判断是否还在推进：看 `run_machine.jsonl` 是否在增长，以及构建产物的时间戳
  （Android：`build/app/outputs/flutter-apk/app-debug.apk`；
  Windows：`build/windows/x64/runner/Debug/<BINARY_NAME>.exe`；
  macOS：`build/macos/Build/Products/Debug/<PRODUCT_NAME>.app`；
  Linux：`build/linux/x64/debug/bundle/<BINARY_NAME>`）。
- 实测：某次冷构建约 12 分钟才产出 APK，超出默认窗口；属正常现象，非失败。

## 6. 目标匹配

- `bash scripts/targets.sh` 输出 `<id>|<kind>|<label>`，`kind` = `android` / `desktop` / `web`，
  由 `flutter devices --machine` 的 `targetPlatform` 判定（`android-*` / `windows-*` / `web-javascript`）。
- `flutter devices` 输出 `name • deviceId • platform • sdk`；`name` 是**型号**，`deviceId` 是**序列号**。
- `start.sh` 的 `DEVICE=` 同时接受序列号和目标 id（`windows` / `chrome`）；
  **Android 优先用 deviceId**（同型号多台时名称会歧义）。
- 选择规则：唯一一台 Android → 自动选；否则唯一目标 → 选它；其余 → 列给用户选。**多目标时不要猜。**
- 本机通常同时列出 `windows` / `chrome` / `edge`，所以"接了手机就自动选手机"是刻意的：
  要调桌面必须显式 `DEVICE=windows`。

## 7. 结束会话 / 清理

用 `scripts/stop.sh` **跨平台**结束会话：

- 优先读取 `<项目>/.flutter_debug.pid`（`start.sh` 写入）精确终止该次会话及其子进程；
- 再按平台兜底：
  - Windows(Git Bash)：PowerShell 终止命令行含 `flutter` 的 `dart.exe`；
    无 PowerShell 时回退 `pkill -f "flutter run --machine"`。
  - macOS / Linux：`pkill -f flutter_tools.snapshot` 与 `pkill -f "flutter run --machine"`。
- **桌面目标额外清理**：桌面应用的进程**不是** flutter 工具的子进程，只杀工具会留下孤儿窗口。
  `stop.sh` 会再结束该应用进程，进程名按各平台的生成文件推导
  （Windows/Linux `CMakeLists.txt` 的 `BINARY_NAME`；macOS `AppInfo.xcconfig` 的 `PRODUCT_NAME`），
  是推导出来的、不是猜的，避免误杀同名进程。Windows 用 PowerShell 按进程名结束；
  macOS / Linux 用 `pkill -x`（`comm` 名可能被截断，失败则回退到锚定全路径的 `pkill -f "/<name>$"`）。
  实测（Windows）：不加这一步时 `dart.exe` 归零、VM Service 已拒连，但 `fl_probe.exe` 仍在运行。
- `start.sh` 启动新会话前会自动先跑 `stop.sh` 清理旧会话。

按模式手动 kill（不推荐，仅当脚本不可用时）：

- POSIX：`pkill -f "flutter run --machine"`
- Windows：`Get-CimInstance Win32_Process | ? { $_.Name -eq 'dart.exe' -and $_.CommandLine -match 'flutter' } | % { Stop-Process -Id $_.ProcessId -Force }`

> 若项目曾被放在内存盘/ReFS 并构建失败，残留目录可能被 Gradle 守护进程占用而
> 暂时无法删除；先结束会话（必要时 `android/gradlew --stop` 停掉 Gradle daemon）再删。

## 8. 跨平台与其它

- `--verbose` 只在排障时用；日常 `--debug` 足够，避免日志淹没。
- 依赖 `websocket-client`（`pip install websocket-client`）；msys2 的 Python 上会撞 PEP 668
  `EXTERNALLY-MANAGED`，处理办法见第 10 节。
- 脚本优先 `python3`，回退 `python`；本机解释器名特殊时用 `PY=<绝对路径>` 覆盖。
- `adb` 不在 PATH 时用 `ADB=<绝对路径>` 覆盖。
- macOS 自带 bash 为 3.2，脚本已避免 `mapfile` 等 bash 4+ 特性；如遇 `mapfile: command not found`，说明用了旧版脚本或非本 skill 的脚本。

## 9. 真机输入注入被拒：`INJECT_EVENTS`（仅 Android 目标）

**症状**：`adb shell input tap` / `keyevent` / `text` 报

```
java.lang.SecurityException: Injecting to another application requires INJECT_EVENTS permission
```

**根因**：注入输入事件需要 `INJECT_EVENTS` 权限。部分厂商 ROM（尤其小米 MIUI / HyperOS）
默认不把它授予 `shell`（uid 2000），设备未 root 时无法自行提权。
**这是手机设置问题，不是 skill 的问题。**

**判定**：

```bash
adb shell dumpsys package com.android.shell | grep INJECT_EVENTS   # 期望 granted=true
adb shell input keyevent 0                                        # 被拒则抛 SecurityException
```

实测（小米 9 / MIUI 12.5 / Android 11）：未开启时第二条抛上述 `SecurityException`；
开启后 `dumpsys` 显示 `android.permission.INJECT_EVENTS: granted=true`，点击恢复。

**处理：不要绕过。** 不要换别的注入手法硬凑，也不要跳过点击、把闭环当成通过了。
停下来问用户，并指导用户开启开关：

- **小米 / Redmi（MIUI / HyperOS）**：开发者选项 → 「USB 调试（安全设置）」
  （部分版本写作「允许通过 USB 调试修改权限或模拟点击」；有些版本要求登录小米账号、插入 SIM 卡）。
- **OPPO / 一加 / realme（ColorOS）**、**vivo / iQOO（OriginOS / Funtouch）**：开发者选项里
  有名字相近的「USB 调试（安全设置）」类开关。
- 顺带让用户确认：已开启「USB 调试」、USB 模式不是「仅充电」、设备未被 MDM / 企业管控。

开完确认 `granted=true` 后复测一次点击闭环再继续。只有在用户**明确拒绝**开启时，
才与他讨论替代方案（App 自身定时器 / 调试入口输出日志，或 `integration_test` / `flutter_driver`
在进程内驱动），并说明那不是标准点击闭环。

**影响范围**：只有点击那一步受影响。`watch_logs.sh`、`logs`、`tree`、`shot.sh` 都不需要该权限，
可正常验证；需要它的是「先开监听 → `adb shell input tap` → 读输出」这一环。

## 10. 环境自检与 Android 许可证

**先跑 `flutter doctor`**：它会一次性暴露 Android 工具链、许可证、设备与网络资源的问题。

**许可证未接受的典型输出**：

```
[!] Android toolchain - develop for Android devices (Android SDK version XX)
    ! Some Android licenses not accepted. To resolve this, run: flutter doctor --android-licenses
```

**处理原则：这是环境问题，先告知用户并询问，不要静默忽略。**

- 默认做法：让用户自己执行 `flutter doctor --android-licenses`（交互式，逐条输入 `y`）。
- 仅当用户**明确授权**时才用 `yes | flutter doctor --android-licenses` 批量接受
  （它会改变 Android SDK 的许可证状态，属需用户确认的操作）。
- 未接受的许可证**不一定会立刻阻塞** `flutter run`：本次实测在许可证未全部接受的情况下
  APK 构建与真机安装仍成功；真正缺组件、Gradle 需要自动下载时才会失败。
  所以既不要谎报成功，也不要夸大成「无法构建」。

**Python 依赖自检**：`python3 -c "import websocket; print(websocket.__version__)"`。

msys2 的 Python 通常带 PEP 668 `EXTERNALLY-MANAGED` 标记，直接 `pip install` 与
`python3 -m ensurepip` 都会被拒绝（`error: externally-managed-environment`）。
此时改用发行版包管理器：

```bash
pacman -S --noconfirm --needed mingw-w64-clang-x86_64-python-websocket-client   # clang64
pacman -S --noconfirm --needed mingw-w64-ucrt-x86_64-python-websocket-client    # ucrt64
pacman -S --noconfirm --needed mingw-w64-x86_64-python-websocket-client         # mingw64
```

若某环境仓库里没有该包（如 mingw32），可下载官方 wheel 后展开到该解释器的 `site-packages`
（websocket-client 是纯 Python 包，无编译依赖）。

> 注意 Git Bash 里 `python3` 常解析到 msys2 的解释器，而它可能**没有 pip**。
> 不想动 msys2 时，用 `PY=<绝对路径>` 指向系统 Python 即可（实测可行，
> Git Bash 会把 POSIX 路径参数自动转成 Windows 路径）。

## 11. 桌面目标补充（Windows / macOS / Linux）

**已验证程度（重要）**：Android 真机与 Windows 桌面两条路径已在真实设备上实测跑通；
**macOS 与 Linux 的桌面截图路径是按同样原则写出来的，但尚未在真实 mac/Linux 上执行过**。
首次在这些系统上使用时，请把它当成一次测试，并确认产物确实是那个应用窗口。

- **构建/运行**：`DEVICE=windows|macos|linux bash scripts/start.sh`（目标 id 以 `scripts/targets.sh` 为准）。
  Windows 首次构建需要 Visual Studio 的 `Desktop development with C++` 工作负载；macOS 需要 Xcode
  命令行工具；Linux 需要 GTK/CMake/Ninja 等（`flutter doctor` 会逐条提示）。
- **观测链路与 Android 完全一致**：VM Service 与目标平台无关，
  `tree` / `logs` / `isolate` / `rpc` / `watch_logs.sh` 都是同一套（Windows 目标实测：273 行
  Widget Tree、周期日志、`rpc getVM` 均正常）。
- **截图**：见第 3 节，按系统分流，只抓应用自己的窗口；空图会明确报错。
  macOS 需要「屏幕录制」权限；Linux 需要 X11 会话，外加 `xdotool` 与任一截图工具。
- **触发操作**：桌面没有 `adb shell input tap` 的等价物。**别用"截屏坐标 + 模拟鼠标"硬凑**
  （窗口可能被遮挡、坐标会漂，结论不可信）。请用户手点，或改用 `integration_test` /
  `flutter_driver` 在进程内驱动。
- **结束会话**：`stop.sh` 会连带关闭应用进程（见第 7 节），否则留下孤儿窗口。
- **Web 目标**：观测可用，截图未实现（`shot.sh` 明确报错），不会静默截错。
