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

## 3. 截图：`adb shell` vs `adb exec-out`

- `adb exec-out screencap -p > x.png` → 二进制安全，正常 PNG。
- `adb shell screencap -p > x.png` → 若经 shell/管道做 CRLF 转换，PNG 被破坏
  （签名从 `\x89PNG\r\n\x1a\n` 变成 `\x89PNG\r\r\n\x1a`）。

**始终用 `exec-out`**，并在落盘后校验签名（`scripts/shot.sh` 已内置）。

## 4. Widget Tree / 日志：协议细节

- 调用 `ext.flutter.*` 服务扩展前必须先拿 `isolateId`（`getVM` → `isolates[0].id`）。
- Widget Tree 用 `ext.flutter.debugDumpApp`（返回文本树）。
- 应用输出用 `streamListen` 的 `Stdout`/`Stderr`/`Logging`；事件 `kind == "WriteEvent"`
  的 `bytes` 字段是 **base64**，必须 `base64.b64decode` 再解码成文本，否则打印出乱码串。
- 需要在会话保持期间持续读取；WebSocket 收不到消息时用 `settimeout` 控制读取节奏。

### 抓不到日志？——先开监听，再操作

`streamListen` **只推送"监听建立之后"产生的事件**，不回放历史。因此：

- 要抓某次点击/输入的输出 → **先** `scripts/watch_logs.sh 60 out.txt &`，**再** `adb shell input tap ...`。
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
- 判断是否还在推进：看 `run_machine.jsonl` 是否在增长、以及
  `build/app/outputs/flutter-apk/app-debug.apk` 的时间戳是否更新。
- 实测：某次冷构建约 12 分钟才产出 APK，超出默认窗口；属正常现象，非失败。

## 6. 设备匹配

- `flutter devices` 输出 `name • deviceId • platform • sdk`；`name` 是**型号**，`deviceId` 是**序列号**。
- `-d` 同时接受名称和 deviceId；但**优先用 deviceId**（同型号多台时名称会歧义）。
- 多设备时不要猜：列出来让用户选。

## 7. 结束会话 / 清理

用 `scripts/stop.sh` **跨平台**结束会话：

- 优先读取 `<项目>/.flutter_debug.pid`（`start.sh` 写入）精确终止该次会话及其子进程；
- 再按平台兜底：
  - Windows(Git Bash)：PowerShell 终止命令行含 `flutter` 的 `dart.exe`；
    无 PowerShell 时回退 `pkill -f "flutter run --machine"`。
  - macOS / Linux：`pkill -f flutter_tools.snapshot` 与 `pkill -f "flutter run --machine"`。
- `start.sh` 启动新会话前会自动先跑 `stop.sh` 清理旧会话。

按模式手动 kill（不推荐，仅当脚本不可用时）：

- POSIX：`pkill -f "flutter run --machine"`
- Windows：`Get-CimInstance Win32_Process | ? { $_.Name -eq 'dart.exe' -and $_.CommandLine -match 'flutter' } | % { Stop-Process -Id $_.ProcessId -Force }`

> 若项目曾被放在内存盘/ReFS 并构建失败，残留目录可能被 Gradle 守护进程占用而
> 暂时无法删除；先结束会话（必要时 `android/gradlew --stop` 停掉 Gradle daemon）再删。

## 8. 跨平台与其它

- `--verbose` 只在排障时用；日常 `--debug` 足够，避免日志淹没。
- 依赖 `websocket-client`（`pip install websocket-client`）。
- 脚本优先 `python3`，回退 `python`；本机解释器名特殊时用 `PY=<绝对路径>` 覆盖。
- `adb` 不在 PATH 时用 `ADB=<绝对路径>` 覆盖。
- macOS 自带 bash 为 3.2，脚本已避免 `mapfile` 等 bash 4+ 特性；如遇 `mapfile: command not found`，说明用了旧版脚本或非本 skill 的脚本。
