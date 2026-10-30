---
name: yashi-windows-bsod-analysis
description: 分析 Windows 蓝屏（BSOD）内核转储：识别完整/内核/活动/小内存（triage）转储，从 dump 头直接读出 BugCheck 码与参数，用 WinDbg/cdb 加载匹配符号还原真实异常栈与责任模块，并区分「蓝屏路径」与「故障路径」，最后给出可疑驱动排序与下一步取证方案。适用于「蓝屏」「BSOD」「minidump」「MEMORY.DMP」「BugCheck 0x1E / 0x50 / 0xD1 / 0x139」「WRONG_SYMBOLS」「KiFatalExceptionHandler」「蓝屏重启」等场景。注意：缺少调试工具时只告知用户下载渠道，不自行下载或安装，除非用户明确要求。
---

<!--
触发示例：
- 帮我看看这个蓝屏 dump：C:\Windows\Minidump\102826-15593-01.dmp
- 电脑今天蓝屏了，帮我分析 MEMORY.DMP
- BugCheck 0x1E 是什么意思，严重吗
- 0xFFFFF800BDA19BDF 这个地址属于哪个驱动
- !analyze 报 WRONG_SYMBOLS 怎么处理
- 连续蓝屏两次，把这两个 dump 都过一遍
- 帮我看看是不是内存条有问题
-->

# Windows 蓝屏转储分析

## 核心规则（先读）

1. **缺工具不自动装**：本机没有 `cdb.exe` / `kd.exe` / WinDbg 时，**只告知用户去哪里下载，并把命令原样给出，等用户决定**；不要自行下载、解包或安装调试器，也不要擅自把符号缓存拉到本地，除非用户明确要求「你帮我装 / 你下好」。
2. **先判转储类型**，再决定结论能写到什么程度（见第 2 节）。小内存转储（triage）不含池页与镜像页，注定钉不死具体驱动，不要硬编一个出来。
3. **区分两条路径**：`!analyze -v` 的 `FAILURE_BUCKET_ID` / `SYMBOL_NAME` 经常指向**蓝屏路径**（`KeBugCheckEx`、`KiFatalExceptionHandler`、`nt_wrong_symbols`），不是故障点。必须另取「异常发生时的上下文」才能得到真实栈。
4. **先拿不需要符号的信息**：BugCheck 参数、故障 IP 的模块归属、`lm k` 模块表，这些在符号缺位时也能拿到，先用它们压缩范围。
5. **输出要克制**：不要整块打印内存或乱码 repr；手工解析一律写脚本、只回统计与命中行。
6. **隐私**：dump 可能含密码、令牌、聊天记录等内存残留。不要上传第三方服务，不要整段贴进公开 issue。

## 1. 前置条件

| 项 | 说明 |
|----|------|
| 分析机 | 任意 Windows 机器都行，dump 可以拷过来分析 |
| 调试器 | `cdb.exe` / `kd.exe` / WinDbg 任一；先用 `where cdb` 检查 |
| 符号 | Microsoft 公共符号服务器 `https://msdl.microsoft.com/download/symbols` |
| 网络 | 首次解符号要联网，之后用本地缓存离线跑 |

找不到 dump 文件时按顺序查：

```powershell
Get-ChildItem C:\Windows\Minidump\*.dmp | Sort-Object LastWriteTime -Descending
Get-ChildItem C:\Windows\MEMORY.DMP
Get-WinEvent -FilterHashtable @{LogName='System'; Id=1001} -MaxEvents 10 |
  Select-Object TimeCreated, Message
```

事件 ID 1001 的 `BugcheckCode` / `BugcheckParameter` 字段可以直接和 dump 头互相印证。

## 2. 转储类型与能力边界

内核转储的文件头固定以 `PAGE` + `DU64`（32 位为 `PAGE` + `DUMP`）开头。

| 类型 | 大小量级 | 能得出 | 不能得出 |
|------|----------|--------|----------|
| 完整内存转储 | 约等于物理内存 | 全内存，可 `!pool` / 对象属主 | 分析慢 |
| 内核内存转储 | 数百 MB 到数 GB | 内核内存、被破坏对象的属主 | 用户态内存 |
| 活动内存转储 | 中等 | 内核 + 活动进程内存 | 冷数据 |
| **小内存转储 minidump / triage** | 256KB 到数 MB | BugCheck、寄存器、栈、模块表、故障代码页 | **池页与镜像页 → 无法判定被破坏对象的属主** |

判断是否 triage：cdb 打开时会明确打印
`Mini Kernel Dump File: Only registers and stack trace are available`。
看到这句就不要期待 `!pool` / `dt _KQUEUE` 这类需要池内存的命令。

## 3. 流程

### 3.1 读 dump 头（不需要任何工具）

`_DUMP_HEADER64` 关键偏移（已实测核对）：

| 偏移 | 字段 | 说明 |
|------|------|------|
| 0x00 | Signature | `PAGE` |
| 0x04 | ValidDump | `DU64`（x64） |
| 0x08 / 0x0C | MajorVersion / MinorVersion | Win10 起 MinorVersion 就是 build 号（如 0x65F4 = 26100） |
| 0x10 | DirectoryTableBase | 页目录基址 |
| 0x20 | PsLoadedModuleList | 内核模块链表头，落在 ntoskrnl 内 |
| 0x30 | MachineImageType | 0x8664 = x64 |
| 0x34 | NumberProcessors | 逻辑处理器数 |
| 0x38 | BugCheckCode | 蓝屏码 |
| 0x40 / 0x48 / 0x50 / 0x58 | BugCheckParameter1..4 | 注意 0x38 与 0x40 之间有 4 字节填充 |

```python
import struct
d = open(r'C:\Windows\Minidump\sample.dmp', 'rb').read()
print('signature', d[:4], d[4:8], 'size', len(d))
print('build', struct.unpack_from('<I', d, 0x0C)[0])
mach, nproc, bc = struct.unpack_from('<III', d, 0x30)
p1, p2, p3, p4 = struct.unpack_from('<QQQQ', d, 0x40)
print('machine %x cpus %d' % (mach, nproc))
print('BugCheck 0x%x  P1=%016x P2=%016x P3=%016x P4=%016x' % (bc, p1, p2, p3, p4))
```

### 3.2 工具链决策（关键：不擅自安装）

先查本机：

```bat
where cdb & where kd & where windbg
dir "C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe"
```

**没有时：把下面渠道告诉用户，等用户确认，不要动手装。**

- Microsoft Store 搜 `WinDbg`（免费，装完带命令行 `cdb`）
- Windows SDK：<https://developer.microsoft.com/windows/downloads/windows-sdk/>，安装向导里只勾 `Debugging Tools for Windows`
- winget：`winget install --id Microsoft.WinDbg`
- 已装 SDK 但目录里只剩 `dbghelp.dll` / `symsrv.dll` / `dbgcore.dll` / `srcsrv.dll`：说明 Debugging Tools 组件没装或被删，让用户重装该组件

**只有用户明确要求**「不要装进系统」时，才提供免安装解包方案：

```bat
winsdksetup.exe /layout D:\dbgtools /features OptionId.WindowsDesktopDebuggers
msiexec /a "D:\dbgtools\Installers\X64 Debuggers And Tools-x64_en-us.msi" TARGETDIR=D:\dbgtools\debuggers /qn
```

### 3.3 符号：先确认有没有，再谈匹配

最小验证命令（注意 bash 里地址含反引号要整条用单引号）：

```bash
cdb.exe -z <dump> -y "srv*<cache>*https://msdl.microsoft.com/download/symbols" -c "lm m nt; q"
```

判定标准：输出里 `nt` 那一行必须是 `(pdb symbols)`，并且 `IMAGE_VERSION` 与系统版本一致。

**坑 A：`WRONG_SYMBOLS`（看起来正常，其实全是假的）**

出现以下任一，说明符号与内核不匹配，此时 `!analyze -v` 的 bucket 完全不可用：

- `PROCESS_NAME: ntoskrnl.wrong.symbols.exe`
- `MODULE_NAME: nt_wrong_symbols`
- `FAILURE_BUCKET_ID: WRONG_SYMBOLS_X64_TIMESTAMP_...`
- `WRONG_SYMBOLS_TIMESTAMP: xxxxxxxx` + `WRONG_SYMBOLS_SIZE: xxxxxxxx`

**坑 B：minidump 拿不到 PDB 签名**

小内存转储不含镜像页，读不到 PE 调试目录，无法直接算 PDB 键。用模块表里的
`timestamp + size` 去符号服务器找镜像（符号服务器对镜像也按这个键存）：

1. `lm v m nt` 取 `TimeDateStamp` 与 `SizeOfImage`，例如 `AFF6531F` + `1450000`
2. 下载镜像：`https://msdl.microsoft.com/download/symbols/ntkrnlmp.exe/AFF6531F1450000/ntkrnlmp.exe`
3. 从镜像的 PE 调试目录解析 `RSDS`，得到 PDB 的 `GUID + age`
4. 下载 PDB：`https://msdl.microsoft.com/download/symbols/ntkrnlmp.pdb/<GUID 32 位大写><age 十六进制>/ntkrnlmp.pdb`
5. 放进 symsrv 缓存：`<cache>\ntkrnlmp.pdb\<GUID><age>\ntkrnlmp.pdb`
6. 用 `-y "srv*<cache>*"`（末尾留空 = 纯离线，不再联网兜底）

**坑 C：全量拉符号会超时**

`!analyze -v` 会为栈上每个模块去拉符号，桌面系统常见 250 个以上模块，很容易几百秒跑不完。
对策：只对 `nt` 解符号（其余保持 deferred），或者预先把关键模块的镜像与 PDB 拉进缓存。

**坑 D：下载慢**

`msdl.microsoft.com` 的大文件会跳转到 Azure Blob，速度可能只有几十 KB/s。
先用 `curl -I` 看返回码确认文件存在（302 属正常跳转），再决定是否继续；不要因为一次失败就断言「这版没有符号」。

### 3.4 取真实异常栈（最容易被忽略的一步）

小内存转储里同时存在两套上下文：

- **蓝屏路径上下文**：`KeBugCheckEx` 附近，`Rip` 指向 `int 3`，
  `.exr -1` 会显示 `ExceptionCode 80000003 (Break instruction exception)`。这不是故障点。
- **异常路径上下文**：`Rip` 是真正出错的指令，需要 `.trap <KTRAP_FRAME 地址>` 才能切过去。

找到 trap frame 的办法：

1. 看 `!analyze -v` 输出里的 `STACK_COMMAND` 提示；
2. 或在 dump 里搜 `CONTEXT` 结构：`ContextFlags = 0x0010001F`（位于结构 +0x30），且 `Rip` 是内核地址；
3. 拿到地址后：`.trap <addr>` → `r` → `kb`。

`CONTEXT64` 实测偏移（注意 Dr4/Dr5 是别名，实际只有 Dr0-3 与 Dr6/Dr7，不要多算 16 字节）：

| 偏移 | 字段 |
|------|------|
| +0x30 | ContextFlags |
| +0x38 | SegCs（之后 SegDs/SegEs/SegFs/SegGs/SegSs 各 2 字节） |
| +0x44 | EFlags |
| +0x78 | Rax（Rcx/Rdx/Rbx/Rsp/Rbp/Rsi/Rdi 依次 +8） |
| +0xB8 | R8（R8-R15 依次 +8） |
| +0xF8 | Rip |

### 3.5 最小命令集

```bash
# 模块表（不需要符号）
cdb.exe -z <dump> -y "srv*<cache>*" -c "lm k; q"

# 完整分析
cdb.exe -z <dump> -y "srv*<cache>*" -c "!analyze -v; q"

# 真实异常上下文与栈
cdb.exe -z <dump> -y "srv*<cache>*" -c ".trap <trapFrame>; r; kb; q"

# 地址归属、反汇编故障指令、扫栈
cdb.exe -z <dump> -y "srv*<cache>*" -c "ln <ip>; u <ip> L12; dps @rsp L80; q"

# 补充证据
cdb.exe -z <dump> -y "srv*<cache>*" -c "!blackboxbsd; !blackboxpnp; !blackboxwinlogon; q"
```

没有符号也能用的：`lm k`、`ln`、`dps`、`u`（前提是转储里含该代码页，triage 通常含故障页）。
`ln <addr>` 给出「模块 + 偏移」，是判断地址归属最快的手段。

### 3.6 判读要点

- **故障 IP 落在 nt 内不等于内核的错**：先看真实栈，找出第一个不属于 nt/hal 的第三方 `.sys`。
- **`!analyze` 的 `Args to Child` 不是调用栈**。它只是入参，别把其中的地址当成某一帧「调用了故障函数」。
- **`0xC0000005` + 地址 `0xFFFFFFFFFFFFFFFF`**：这是 x64 **非规范地址**访问被 `#GP` 转换后的表现，
  意味着指针本身被写坏（常见于链表项、对象头），不是「空指针」。
- **`FAST_FAIL_CORRUPT_LIST_ENTRY` / 链表校验失败**：`nt!KiInsertQueueInternal`、
  `ExpInsertTimer` 一类代码会对 `LIST_ENTRY` 做 Flink/Blink 互指校验，失败即 `int 29h` 主动蓝屏。
  这类蓝屏本质是**内存损坏**，需要往「谁写坏了内存」方向查（驱动越界、UAF、RAM/超频）。
- 栈上全 nt 且是 DPC/定时器路径时，不要停在这里：继续查「崩溃前有没有驱动被卸载」、
  「有没有 ring0 硬件读写类工具」（CPU-Z / 华硕 AI Suite / 各类监控与超频软件）、
  「是否有虚拟化与抓包驱动」。

### 3.7 BugCheck 速查

| 码 | 名称 | 第一反应 |
|----|------|----------|
| 0x1E | KMODE_EXCEPTION_NOT_HANDLED | 内核态未处理异常，看 P2 归属与真实栈 |
| 0x3B | SYSTEM_SERVICE_EXCEPTION | 系统调用中异常，多为第三方驱动 |
| 0x50 | PAGE_FAULT_IN_NONPAGED_AREA | 访问无效或已释放的非分页内存 |
| 0xD1 | DRIVER_IRQL_NOT_LESS_OR_EQUAL | 驱动在错误 IRQL 访问分页内存，几乎必是驱动 |
| 0x7E | SYSTEM_THREAD_EXCEPTION_NOT_HANDLED | 系统线程未处理异常，P2 即故障 IP |
| 0x9F | DRIVER_POWER_STATE_FAILURE | 电源状态转换超时，查设备与驱动 |
| 0x133 | DPC_WATCHDOG_VIOLATION | DPC/ISR 超时，查驱动与固件 |
| 0x139 | KERNEL_SECURITY_CHECK_FAILURE | 内核安全检查失败（FAST_FAIL），常为链表或对象损坏 |
| 0x101 | CLOCK_WATCHDOG_TIMEOUT | 某核无响应，硬件/固件/驱动 |
| 0xC2 | BAD_POOL_CALLER | 池操作错误 |
| 0xA | IRQL_NOT_LESS_OR_EQUAL | 高 IRQL 访问分页内存 |
| 0xEF | CRITICAL_PROCESS_DIED | 关键进程被杀 |
| 0x4E | PFN_LIST_CORRUPT | 内存管理结构损坏，考虑 RAM |
| 0x1A | MEMORY_MANAGEMENT | 内存管理错误，考虑 RAM 与驱动 |
| 0x124 | WHEA_UNCORRECTABLE_ERROR | 硬件错误，查 WHEA-Logger 与厂商日志 |

### 3.8 报告模板

1. **结论**：一句话说清蓝屏性质（哪类异常、在做什么时触发、是否指向具体驱动）。
2. **关键证据表**：BugCheck 与 4 个参数、故障 IP → 模块 + 偏移、受损的值、真实栈、系统版本与 uptime。
3. **机制**：为什么会崩（把反汇编与校验逻辑讲清楚）。
4. **责任模块**：能钉死就写模块名与依据；钉不死就写候选排序，并说明为什么这份 dump 不能定责。
5. **下一步**：改内核或活动内存转储、Driver Verifier 参数、MemTest、驱动更新、卸载高危 ring0 工具。
6. **产物**：把 cdb 路径、符号缓存、完整日志路径列出来，方便下次复用。

## 4. 常见失败模式

| 现象 | 原因 | 对策 |
|------|------|------|
| `where cdb` 为空，Debuggers 目录只剩几个 dll | 组件没装或被删 | 告知下载渠道，不要自动安装（核心规则 1） |
| `timeout 120 ./cdb.exe ...` 报 `Invalid syntax` | MSYS2 下撞到 Windows 自带的 `TIMEOUT.EXE` | 去掉外层 timeout，或用 `/usr/bin/timeout` |
| `!analyze -v` 卡几分钟后超时 | 它在为所有模块拉符号 | `-y "srv*<cache>*"` 离线 + 只解 nt |
| `WRONG_SYMBOLS_*` | PDB 与内核不匹配 | 按 timestamp+size 找镜像 → RSDS 取键 → 下 PDB |
| 手工解析出 `Rip=0` 或寄存器全乱 | CONTEXT64 偏移算错（Dr4/Dr5 是别名） | 按 3.4 节偏移表重算 |
| `kb` 只有 5 帧且全是 nt | 用的是蓝屏路径上下文 | `.trap <trapFrame>` 取真实上下文 |
| 把某个外设驱动当成调用链 | 误读 `Args to Child` | 只看 `kb` / `dps` 解出来的帧 |
| 一次把十几万字节内存打进上下文 | 输出没有节流 | 写脚本、只打命中行 |
| 结论写「可能是内存条坏了」但没有依据 | 越过了证据 | 只有出现非规范指针、链表损坏等多处迹象时才提硬件，并给出 MemTest 建议 |

## 5. 命令行注意

- 内核地址里的反引号（如 `0xfffff800\`bda19bdf`）在 bash 里会被当成命令替换，**整条 cdb 命令用单引号包住**。
- MSYS2 下调用 Windows 程序：`tasklist //FI "IMAGENAME eq cdb.exe"`（双斜杠），路径含空格加引号。
- 分析结束前确认没有残留 `cdb.exe` 进程，尤其是超时被中断的那次。
- 输出统一 UTF-8，避免中文路径与中文日志乱码。

## 6. 参考

- WinDbg 文档：<https://learn.microsoft.com/windows-hardware/drivers/debugger/>
- Bug Check Code Reference：<https://learn.microsoft.com/windows-hardware/drivers/debugger/bug-check-code-reference2>
- 符号服务器与 `symsrv`：<https://learn.microsoft.com/windows-hardware/drivers/debugger/symbols>
- 配置崩溃转储：<https://learn.microsoft.com/troubleshoot/windows-client/performance/generate-a-kernel-or-complete-crash-dump>
