---
name: yashi-flutter-android-device-debug
description: Flutter 的 Android 真机调试工作流：建立稳定的 flutter run 调试会话，并获取 Widget Tree、应用日志输出与设备截图。适用于「Flutter / Android 真机调试」「查看 widget 树 / Widget Tree」「抓应用日志 / 控制台输出」「真机截图与热重载观测」等场景。
---

# Flutter Android 真机调试

在**任意** Flutter 项目中，让 AI 稳定完成 Android 真机调试：启动会话 → 读 Widget Tree → 抓应用输出 → 截图，并支持"操作后观测"的闭环。

**跨平台**：脚本可在 **Linux / macOS / Windows(Git Bash)** 运行，只依赖 `bash` + `python3` + `adb`。
仅 Windows 上的进程清理会用到 PowerShell；缺失时自动回退 `pkill`。

**不要写死任何设备名/路径。** 设备一律按下面的规则动态选择。

## 前置检查

1. `flutter devices`（或 `flutter devices --machine`）确认设备；`adb devices` 兜底。
2. `adb` 若不在 PATH，通常位于 `<ANDROID_SDK>/platform-tools/adb`（可用 `ADB=<绝对路径>` 指定）。
3. Python 3 依赖（VM Service 客户端需要）：`python3 -m pip install websocket-client`。
4. 脚本优先用 `python3`；若本机 `python3` 是特殊路径，用 `PY=<python 绝对路径>` 覆盖。

## 选择设备（关键规则，勿写死）

运行 `bash scripts/android_devices.sh`，每行输出 `<deviceId>|<name>|<model>`：

- **只有 1 台 Android 设备** → 直接使用它。
- **有 0 台** → 提示用户连接设备 / 开启 USB 调试后再试。
- **有多台** → 把列表展示给用户并让用户选择，不要擅自决定。

始终用 **deviceId（序列号，如 `cba12b50`）** 作为 `-d` 的值。
`flutter devices` 里的 `name`（如 `RMX1901`）是**型号名**，同型号多台会歧义；`-d` 虽也接受名称，但优先用 deviceId。

## 标准流程

> 下文的 `scripts/` 指**本 skill 的 `scripts/` 目录**（可写成绝对路径）。
> 请在 **Flutter 项目根目录**（含 `pubspec.yaml`）执行；脚本用 `$0` 定位同级文件，故 skill 可放在任意位置。

```bash
# 1) 启动调试会话（后台；JSON 事件流写入 run_machine.jsonl；打印 VM Service wsUri）
bash scripts/start.sh                          # 唯一设备时自动选
DEVICE=<deviceId> bash scripts/start.sh        # 多设备时（用户选定后）显式指定

# 2) 一键取三路产物 → artifacts/{widget_tree.txt, app_logs.txt, screen.png}
bash scripts/pipeline.sh

# 3) 结束调试会话
bash scripts/stop.sh
```

单独使用：

```bash
python3 scripts/devtools_probe.py --ws <wsUri> tree [--depth N]   # Widget Tree
python3 scripts/devtools_probe.py --ws <wsUri> logs --seconds 8   # 应用输出
python3 scripts/devtools_probe.py --ws <wsUri> isolate            # isolate 信息
python3 scripts/devtools_probe.py --ws <wsUri> rpc getVM          # 任意 RPC
bash scripts/shot.sh out.png                                      # 截图（含 PNG 校验）
```

### 闭环验证（推荐做法，**顺序很重要**）

**想抓到某次操作的输出，必须先开监听、再触发操作**（`logs` 只收到"监听之后"产生的事件）：

```bash
bash scripts/watch_logs.sh 60 out.txt &     # 1) 先开始监听（后台，持续 60s）
adb shell input tap <x> <y>                 # 2) 再操作
sleep 3; cat out.txt                        # 3) 读输出
bash scripts/shot.sh after.png              # 4) 截图
python3 scripts/devtools_probe.py --ws <wsUri> tree   # 5) 复查 Widget Tree（应反映新状态）
```

> 抓**启动期日志**（`main()` / `initState` 里的输出）同理：**先起监听，再让 App 启动或热重启**。

## 原理速查

- 一切观测都走 **Dart VM Service**（地址形如 `ws://127.0.0.1:<port>/<token>/ws`），DevTools 底层也是它。
- **Widget Tree** = RPC `ext.flutter.debugDumpApp`（返回文本树）。
- **应用输出** = `streamListen` 的 `Stdout` / `Stderr` / `Logging` 流；事件的 `bytes` 字段是 **base64**，必须解码后再打印。
- **VM Service URI** 从 `flutter run --machine` 的 `app.debugPort` 事件里取（字段 `"wsUri"`）；用 `--machine` 比 `--verbose` 更好解析。
- **截图** 必须用 `adb exec-out screencap -p`；`adb shell screencap -p` 会被 CRLF 转义破坏 PNG（签名会变成 `\x89PNG\r\r\n`）。
- 需要先拿到 `isolateId`（`getVM` → `isolates[0].id`）才能调用 `ext.flutter.*` 服务扩展。

## 脚本清单

| 脚本 | 作用 |
| --- | --- |
| `scripts/android_devices.sh` | 列出 Android 设备：`deviceId\|name\|model` |
| `scripts/start.sh` | 选择设备并后台启动 `flutter run --machine`，输出 wsUri（含盘符预检） |
| `scripts/stop.sh` | 结束调试会话（跨平台：PowerShell / `pkill`，优先按 pidfile 精确清理） |
| `scripts/ws_uri.sh` | 从事件流提取 VM Service wsUri |
| `scripts/devtools_probe.py` | VM Service 客户端：`tree` / `logs` / `isolate` / `rpc` |
| `scripts/watch_logs.sh` | 先开监听、后操作的持续日志捕获（避免漏掉触发瞬间的输出） |
| `scripts/shot.sh` | 二进制安全截图 + PNG 签名校验 |
| `scripts/pipeline.sh` | 一键产出三路产物并打印 `OUTPUT=` 绝对路径 |
| `scripts/lib.sh` | 公共函数（PY/ADB 探测、OS 判定、跨平台绝对路径、盘符预检），被其它脚本 source |

## 常见陷阱

详见 `references/troubleshooting.md`。最高频的三条：

1. **构建网络**：`dl.google.com` 在部分网络（尤其国内）下 IPv6 会挂起，而 HTTP 代理也未必能为它建 TLS 隧道。优先改用 Maven 镜像（如阿里云 `repository/{google,public,gradle-plugin}`），或换到能直连 IPv4 的网络。
2. **项目位置**：**不要**把项目放在 RAM 盘或非本地 NTFS 卷上，Gradle 9 会误报 `configured projectDirectory ... does not exist`（目录其实存在）。
   `start.sh` 已内置**盘符预检**：命中内存盘（卷名如 `RamDisk`）/ ReFS / 可移动 / 网络盘 / 空间过小会打印 `WARNING`（**不阻断**，可 `SKIP_DISK_CHECK=1` 静音）。看到告警就应把项目挪到本地 NTFS 固定盘再构建。
3. **`--verbose` 非必要**：正常调试用 `--debug` 即可，`--verbose` 日志量巨大，仅在排障时使用。

## 使用要点

- 在 **Flutter 项目根目录**运行脚本（cwd = 项目）；脚本用 `$0` 定位同级文件，故 skill 可放在任意位置。也可用 `PROJECT_DIR=<path>` 指定项目目录。
- 多设备场景：先列设备 → 问用户 → 记下 deviceId → 再用 `DEVICE=<id>` 执行后续命令。
- **结束调试**统一用 `bash scripts/stop.sh`（跨平台）；`start.sh` 启动新会话前也会自动调用它清理旧会话。会话 PID 记录在 `<项目>/.flutter_debug.pid`。
- **构建很慢时**：`start.sh` 默认等待 `WAIT_SECS=900` 秒（15 分钟）拿 VM Service URI。超时**不会中断后台会话**；构建完成后可直接 `bash scripts/ws_uri.sh run_machine.jsonl` 取 URI，或直接跑 `scripts/pipeline.sh`。需要更久用 `WAIT_SECS=<秒>`。
- **平台差异**：Linux / macOS 直接用 `python3`、`adb`、`pkill`；Windows 用 **Git Bash** 运行（无 `pkill` 时自动走 PowerShell）。若系统没有 `python3` 命令，用 `PY=<python 绝对路径>` 覆盖。
- 日志类需求牢记：**先 `watch_logs.sh`，后操作**。
