---
name: yashi-node-tls-trust-fix
display_name: Node TLS 证书信任修复
description: >-
  诊断并修复杀毒软件、企业代理等对 HTTPS 做中间人（MITM）替换证书后，Node 类工具 TLS
  校验失败的问题：报 SELF_SIGNED_CERT_IN_CHAIN、UNABLE_TO_VERIFY_LEAF_SIGNATURE、
  fetch failed、模型请求频繁重试等。做法是识别拦截方自己的根证书并把它加入 Node 的信任
  列表（NODE_EXTRA_CA_CERTS），在保留证书校验的前提下修好，而不是关掉校验。适用于 DSH、
  opencode、Qoder CLI、pnpm、npm、MCP 等所有 Node 程序；也用于反查「某个域名为什么偶发
  失败」。当遇到"证书错误""证书校验失败""self-signed certificate in certificate chain"
  "HTTPS 被杀毒软件拦截""加密连接扫描""模型经常重试""fetch failed"时调用本技能。
  不适用：证书确实过期或域名不匹配（应修服务端），以及纯粹的网络不可达。
---

# Node TLS 证书信任修复

杀毒软件（卡巴斯基、ESET 等）与企业代理的「加密连接扫描 / HTTPS 检查」会对流量做中间人替换，
换上自己的根证书。Node 使用内置的 CA 列表，不认识这个根证书，于是 TLS 校验失败。

本技能的做法是**识别拦截方，把它自己的根证书加入 Node 的信任列表**，而不是关掉校验。

## 何时用 / 何时不用

用：

- 报 `SELF_SIGNED_CERT_IN_CHAIN`、`UNABLE_TO_VERIFY_LEAF_SIGNATURE`、`fetch failed`
- 同一请求时而成功时而失败（拦截往往是**间歇性**的，这是关键特征）
- DSH、opencode、Qoder CLI、pnpm、npm、MCP 等所有 Node 程序出现上述症状

不用：

- 证书真的过期或域名不匹配 → 修服务端，不要加信任
- 纯网络不可达（DNS 失败、连接超时、connection refused）→ 查网络与代理
- 只想「先让它跑起来」而不在乎安全 → 见文末退路，但不要用 `NODE_TLS_REJECT_UNAUTHORIZED=0`

## 核心原则

1. **先证明存在拦截，再动手。** 装了杀软不等于一定是它干的。
2. **让 Node 信任拦截方的根证书**，而不是关闭校验。两者安全影响差一个量级。
3. **验证必须跑多次**，并且要看签发者：若修复后仍能看到拦截方签发，说明内容扫描没被削弱；
   如果拦截消失了，说明你改的是别的东西，结论要重新审视。

## 诊断

### 第 1 步：两侧对比

系统信任存储（Windows/.NET、浏览器、curl 走系统存储）会信任拦截方的根证书，所以一切正常；
Node 用内置列表，所以失败。**两者对同一主机看到不同签发者，就是拦截的确证。**

批量检查是否有中间人（Windows，能看到系统信任存储里的完整证书链）：

```powershell
# 默认只检测，不修改任何东西
.\scripts\export-intercept-ca.ps1 -TargetHost api.deepseek.com
```

### 第 2 步：量化失败率

拦截常是间歇性的：同一域名的签发者在真实 CA 与拦截方之间随机切换。单次成功会被误判为
"没问题"，所以必须跑多次。用 Node 侧视角看真实签发者与失败次数：

```bash
node scripts/tls_probe.mjs api.deepseek.com --runs 12 --fetch -v
```

输出里同时出现两个签发者（一个公开 CA、一个本地软件），就是间歇性拦截的直接证据。
`--fetch` 会额外走一次 Node 内置 fetch（含 undici 全局 dispatcher 与环境代理变量），
复现应用真实的请求路径。

### 第 3 步：确认该根不在 Node 的内置列表里

只有「Node 不认识它」才说明它就是需要加入的那个根，避免盲目导入：

```powershell
# 检测模式已经包含这一步：会打印根的 SHA-256 并与 Node 内置列表比对
.\scripts\export-intercept-ca.ps1 -TargetHost api.deepseek.com
```

## 修复

### 方案 A（推荐）：NODE_EXTRA_CA_CERTS 指向拦截方根证书

```powershell
# 导出 + 写入用户级环境变量 + 当场验证，一步到位
.\scripts\export-intercept-ca.ps1 -TargetHost api.deepseek.com -Export -Apply -Verify
```

原理：`NODE_EXTRA_CA_CERTS` 指向一个 PEM 文件，Node 启动时把它追加到内置 CA 列表。
只多信任这一个根，其余校验行为完全不变。

PEM 默认写到 `%USERPROFILE%\.agent-certs\intercept-root-ca.pem`，可用 `-PemPath` 改。
**不要放在 `%TEMP%`**：那里会被清理，环境变量随之失效。

### 方案 B：让 Node 使用系统信任存储

Node 22.15+ / 23.2+ 提供 `--use-system-ca`，让它直接采用操作系统信任存储：

```powershell
$env:NODE_OPTIONS = '--use-system-ca'   # 当前会话
[Environment]::SetEnvironmentVariable('NODE_OPTIONS','--use-system-ca','User')  # 持久化
```

与本机实测：方案 A 与方案 B 都能把失败率降到 0。

两者取舍：

| | 方案 A（NODE_EXTRA_CA_CERTS） | 方案 B（--use-system-ca） |
| --- | --- | --- |
| 信任面 | 只增加拦截方那一个根 | 采用整个系统存储（数量多，但都是操作系统本就信任的） |
| 版本要求 | 无，所有 Node 版本都支持 | Node 22.15+ / 23.2+ |
| 拦截方轮换根证书 | 需重新导出 PEM | 自动跟随系统存储，无需维护 |
| 适用场景 | 默认选择，改动最小、最明确 | 根证书导不出来、或拦截方频繁轮换证书时 |

### 方案 C（不推荐）：关闭校验

`NODE_TLS_REJECT_UNAUTHORIZED=0` 确实能立即消除症状，但它使该进程内**所有** HTTPS 都不再
校验——包括抓取任意网页、插件安装、MCP 连接、遥测上报、云同步。除非临时排障，否则不要用，
也不要把这个结论写进任何长期配置。

## 退路：改在拦截方软件里排除域名

如果根证书导不出来（例如拦截方使用动态证书），退而求其次是在拦截方软件里按域名排除加密扫描：

- 从配置与源码里找出实际端点域名，**不要猜**
- 只加必需的域名，不要整段关闭「加密连接扫描」
- 代价：这些域名的流量内容不再被扫描
- 若拦截方是卡巴斯基，位置是「设置 → 安全威胁与排除项 → 加密连接扫描 → 管理排除项」，
  注意这与「文件/目录排除项」是两个不同的位置

## 已知坑

1. **不要写进 `.env`（DSH 等宿主会拒绝启动）。** DSH 的 `dsh-app-boot` 会检查 `.env` 的键，
   命中 `NODE_EXTRA_CA_CERTS`、`NODE_OPTIONS`、`NODE_TLS_REJECT_UNAUTHORIZED`、`SSL_CERT_FILE`、
   `SSL_CERT_DIR`、`CURL_CA_BUNDLE`、`REQUESTS_CA_BUNDLE` 等引导变量就直接抛错。必须用
   `[Environment]::SetEnvironmentVariable(...,'User')` 或 setx 写真实环境变量。
2. **当前终端可能已经继承了该变量**，于是探测看着一切正常，而你以为「本来就没问题」。
   判断前先确认 `$env:NODE_EXTRA_CA_CERTS` 是否为空；脚本检测到这种情况会主动提示。
3. **只在进程启动时读取**：改完必须重启终端与目标应用，只刷新网页无效。
4. **作用域是全部 Node 程序**，不只是出问题的那个。
5. **拦截方轮换根证书后 PEM 会失配**，需重新导出（方案 B 无此问题）。
6. 证书链可能有多级：要导出的是**自签根**，不是中间 CA。
7. 判断「Node 是否信任某根」只能用 `tls.rootCertificates`（内置列表，本机 Node 25.9.0 为 145 个，
   且**不受** `NODE_EXTRA_CA_CERTS` 影响）。不要用 `tls.getCACertificates('default')`——它会把
   extra 也算进去；也不要用 `getCACertificates('system')`——那是系统存储的内容，与 Node 当前是否
   使用系统存储无关，会得出「操作系统信任 = Node 信任」的错误结论。

## 脚本

脚本与本 SKILL.md 同目录下的 `scripts/`，请按**本 SKILL.md 所在目录**拼路径，不要假设当前工作目录。
Node 脚本无任何依赖（仅标准库），跨平台；`.ps1` 的部分依赖 Windows 证书存储。

| 脚本 | 作用 |
| --- | --- |
| `tls_probe.mjs` | Node 侧探测：逐次握手，报告授权结果、真实签发者、失败率；`--fetch` 走真实请求路径 |
| `node-trusted-roots.mjs` | 打印 Node 内置根证书的 SHA-256；`--fingerprint <pem>` 打印指定 PEM 内每张证书的指纹 |
| `export-intercept-ca.ps1` | Windows：取回证书链、识别根、核对 Node 是否信任、导出 PEM、写环境变量、实测验证 |

`export-intercept-ca.ps1` 的退出码：`0` = 无中间人，或已修复并验证通过；`1` = 存在中间人但未修复；
`2` = 参数或运行错误。

## 维护注意

1. **`export-intercept-ca.ps1` 必须保存为「UTF-8 带 BOM」。** 本机是 Windows PowerShell 5.1，
   它按 ANSI/GBK 读取无 BOM 的 `.ps1`，中文注释会被解码成乱码并破坏字符串终结符，报出一堆
   莫名其妙的解析错误。已实测：多数编辑工具（含自动化改写）会去掉 BOM，改完必须补回：

   ```powershell
   $f = '路径\export-intercept-ca.ps1'
   $b = [System.IO.File]::ReadAllBytes($f)
   if (-not ($b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF)) {
     $bom = [byte[]](0xEF,0xBB,0xBF); $new = New-Object byte[] ($bom.Length + $b.Length)
     [Array]::Copy($bom,0,$new,0,$bom.Length); [Array]::Copy($b,0,$new,$bom.Length,$b.Length)
     [System.IO.File]::WriteAllBytes($f,$new)
   }
   ```

2. **不要用 `node -e "…含引号的代码…"` 从 PowerShell 5.1 调用 Node。** PowerShell 5.1 向原生
   程序传参时会吞掉引号，代码会被破坏（已实测）。本技能的做法是把代码放进 `.mjs` 文件再执行，
   这也是 `node-trusted-roots.mjs` 存在的原因。改成 `.ps1` 内联 Node 代码前请先想清楚这一点。

## 规则

1. 没有证明存在拦截之前，不要加任何信任
2. 永远优先加根证书，而不是关闭校验
3. 验证必须跑多次并检查签发者，不能只看一次成功
4. PEM 不要放 `%TEMP%`；配置文件不要放 `.env`
5. 报告结论时给出失败率数字，不要只说「修好了」
