---
name: yashi-dsh-update
description: 一句话更新 DeepSeek Harness（dsh）全家桶——npm 全局包（@deepseek-ai/dsh、@deepseek-harness-tui/dsh-tui）及 $DSH_HOME/profiles 下的所有 profile 插件，安装/补全缺失依赖（pnpm update/install + allowBuilds/approve-builds），先检查 profile .npmrc 的 store-dir（dsh 更新功能历史上会写坏，报 ERR_PNPM_UNEXPECTED_STORE），处理 git 依赖与不兼容/被跳过插件，实测 `dsh web --port 3088` 启动（带 token 返回 200/303、无 token 401 校验）并整棵进程树退出、清理临时文件，最后输出当前全局包与插件信息表格。适用于「更新 dsh」「升级 DeepSeek Harness」「dsh 更新后启动失败」「dsh web 起不来」「插件依赖未安装」「pnpm install 报错」「ERR_PNPM_UNEXPECTED_STORE」「插件被跳过/不兼容」等场景。代理与端口已由用户固化（http://127.0.0.1:23333，备用 socks5://127.0.0.1:23332；strict-ssl=false；测试端口 3088），无需再问。
---

<!--
触发示例：
- 更新一下 DeepSeek Harness 和它的所有插件
- dsh 一键更新
- dsh web 启动失败了，帮我看看
- 更新 dsh 后测试 dsh web 能否启动
- 给我一份当前已安装的 dsh 插件信息表格
- 更新 dsh 报 ERR_PNPM_UNEXPECTED_STORE 了
- dsh 启动时提示某个插件 incompatible / skipping profile bundle
-->

# yashi-dsh-update — DeepSeek Harness 一句话更新

## 核心规则（先读）

1. **代理（用户已固化，不必再问）**：所有下载统一走本机代理 `http://127.0.0.1:23333`
   （备用 `socks5://127.0.0.1:23332`；http 失败再试 socks5）。本机代理会 MITM 重签 TLS，
   npm/pnpm 报 `SELF_SIGNED_CERT_IN_CHAIN` 时：命令级临时设
   `$env:npm_config_strict_ssl="false"`（只影响当前命令，**勿写入全局 .npmrc**）。
   pnpm 侧代理若没配过，先 `pnpm.cmd config set proxy/https-proxy http://127.0.0.1:23333`
   （pnpm 没有 strict-ssl 配置项，证书靠上面的 npm_config 环境变量）。
2. **先查 store-dir 再动依赖**：dsh 的「检查更新」历史上会把 `pnpm store path` 的实际值
   原样写进每个 profile 的 `.npmrc` 作为 `store-dir`，在旧版 pnpm（store v10）上会写成
   `...\store\v10\v10`，pnpm 再追加一层版本目录，报 `ERR_PNPM_UNEXPECTED_STORE`。
   每次更新前先看 `$DSH_HOME\profiles\<name>\.npmrc`（web、dsh-tui 都查）：
   - 若 `store-dir` 以重复的版本目录结尾（如 `store\v10\v10`）→ 去掉末尾一层，
     改回 `store-dir=C:\Users\yashi\AppData\Local\pnpm\store\v10`（Windows 用反斜杠 `\`）；
   - 中途再报 `ERR_PNPM_UNEXPECTED_STORE`：停止，重复上述修复后重试。
   > **pnpm 12 现状（2026-10 实测，pnpm 12.9.1）**：默认 store 已升到 **v11**
   > （`...\pnpm\store\v11`）。即使 profile `.npmrc` 写着 `store-dir=...\store\v10`，
   > `pnpm store path`（在 profile 目录内执行）仍输出 `...\store\v11`，且旧的 v10 目录已不存在；
   > `.modules.yaml` 记录的 `storeDir` 也是 v11。即 pnpm 12 会忽略/覆盖这条过期的 v10 配置，
   > 本次未再出现 store 报错。**所以：.npmrc 没写坏就不用动；报错时才按上面规则修。**
3. **新版 pnpm 没有 `--allow-scripts`**：直接加会报
   `error: unexpected argument '--allow-scripts' found`。原生模块（node-pty / ssh2 /
   cloudflared / cpu-features 等）的构建许可靠这两条：
   - profile `pnpm-workspace.yaml` 里的 `allowBuilds:`（正式配置项，值是 `包名: true`）；
   - 或装完后 `pnpm.cmd approve-builds`（交互选择）/ 新版 `--all`，再 `pnpm.cmd rebuild`。
   > 旧字段 `onlyBuiltDependencies` 已被 `allowBuilds` 取代（pnpm 12 下静默忽略）。
   > 实测 web profile 的 `pnpm-workspace.yaml` 已配好 `allowBuilds`（cloudflared/cpu-features/
   > node-pty/ssh2），`pnpm update` 时 cpu-features 直接用 VS2026/MSBuild 编译成功，无需手动 approve。
   > npm 全局包侧：npm 默认就会跑 install 脚本；用户 `~\.npmrc` 里的 `allow-scripts=...`
   > 是给 dsh 用的白名单，不是 npm 标准项，照原样保留即可。
4. **`dsh` / `npm` / `pnpm` 一律用 `.cmd`**：dsh 自带 shell 是 Windows PowerShell 5.1，
   `.ps1` shim 可能被执行策略拦截（`running scripts is disabled`）。命令统一写
   `dsh.cmd` / `npm.cmd` / `pnpm.cmd`；`curl` 用 `curl.exe`。
5. **优先排查「依赖没装」**：多数「启动失败」是 profile 的 `node_modules` 不全，
   先在该 profile 目录 `pnpm.cmd install`，再谈插件本身的问题。
6. **不兼容/被跳过插件**：dsh 0.2 启动时会校验每个 bundle 的 peerDependencies，不满足就
   打印 `dsh: skipping profile bundle "<名>" ... incompatible with dsh <版本>` 并**自动跳过**
   （插件仍安装、只是不加载），通常不阻止 web 启动。这类是「版本不兼容」，不是「多实例」：
   - 若 web 仍能正常启动 → 记录为问题插件即可，无需禁用；
   - 若某插件导致启动崩溃，或明确报「不允许多实例」→ 临时在该 profile 的
     `cordis.patch.yml` 对该 id 加 `disabled: true`（改前备份），测通后：
     能兼容就恢复启用并说明，不能就保持禁用并说明原因。
7. **测试进程必须整树清理**：`dsh.cmd`/`cmd.exe` 只是包装器，真正的 node 是**子/孙进程**；
   只 kill 包装器会留下孤儿 node 继续占端口。用 `taskkill /F /T /PID <包装器 pid>`
   （`/T` 连带子进程），再确认端口已释放。
8. **端口（用户已固化）**：测试用 **3088**；被占用就换一个空闲端口并在报告里说明。
   注意本机 3081 可能是正在跑的 GUI，勿动。
9. **更新默认升 `latest`**：dsh / dsh-tui 一律升到 `latest`，**不要**自发用 `@next`；
   只有用户明确要求「用 next / 要 rc」时才切并写明。若当前已装版本高于 `latest` tag
   （例如装的 rc.2 而 latest 是 rc.1），**不要降级**，保持现状并说明。
   全局包更新务必**串行**（一次一个 npm install），并发会互相清理导致 EPERM/ENOTEMPTY、
   把 node_modules 和 .cmd shim 删坏。

## 环境速览

| 项 | 位置 / 值 |
|---|---|
| 全局 npm root | **动态获取，禁止写死盘符**：`$npmPrefix=(npm.cmd config get prefix)`（prefix/shim 目录）、`$npmRoot=(npm.cmd root -g)`（全局 node_modules）。值由用户 .npmrc 的 prefix 决定 |
| 沙箱/多环境注意 | 若 `npm config get prefix` 返回 `...\sandbox_runtime\...` 之类路径，说明当前环境重定向了 userconfig；以命令实际输出为准，必要时向用户确认，**不要硬编码** D:\npm / C:\npm |
| dsh / dsh-tui CLI | `dsh.cmd` / `dsh-tui.cmd`（PATH 中；`.ps1` 可能被拦，一律用 `.cmd`） |
| 所有 profile | `$DSH_HOME\profiles\<name>`：web、dsh-tui，**用 Get-ChildItem 遍历，不要硬编码** |
| 插件清单 | profile `package.json` 的 `dependencies` + `dsh.profile.bundles` |
| 用户 patch | profile `cordis.patch.yml`（禁用插件、配置覆盖；web 里已设 web-runtime openBrowser:false） |
| pnpm 配置 | profile `pnpm-workspace.yaml`（`allowBuilds`、nodeLinker:hoisted、autoInstallPeers:false） |
| pnpm store | pnpm 12 → `C:\Users\yashi\AppData\Local\pnpm\store\v11`；`.npmrc` 里的 v10 为过期值（被忽略） |
| 代理 | `http://127.0.0.1:23333`（备用 socks5://127.0.0.1:23332）；strict-ssl 命令级 false |
| dsh web 测试端口 | 3088（3081 可能是 GUI，勿动） |

## 工作流

### 0. 检查各 profile 的 store-dir（任何 pnpm 操作之前）
```powershell
$profiles = Get-ChildItem "$env:USERPROFILE\.dsh\profiles" -Directory
foreach ($p in $profiles) {
  $npmrc = Join-Path $p.FullName '.npmrc'
  if (Test-Path $npmrc) {
    $c = Get-Content $npmrc -Raw
    # 命中重复版本目录（v10\v10、v11\v11 等）才修；把末尾一层版本目录去掉
    if ($c -match 'store-dir=(.*\\store\\v\d+)\\v\d+\s*$') {
      $fixed = $Matches[1]
      Set-Content $npmrc -Value "store-dir=$fixed" -Encoding UTF8 -NoNewline
      Write-Output "已修复 store-dir: $($p.Name) -> $fixed"
    }
  }
  Push-Location $p.FullName
  Write-Output "$($p.Name) store path: $(pnpm.cmd store path)"   # pnpm 12 预期 ...\store\v11
  Pop-Location
}
```

### 1. 设置代理（用户已固化，直接设）
```powershell
$env:HTTP_PROXY  = "http://127.0.0.1:23333"
$env:HTTPS_PROXY = "http://127.0.0.1:23333"
$env:ALL_PROXY   = "http://127.0.0.1:23333"
$env:npm_config_strict_ssl = "false"      # 本机代理 MITM，仅命令级
# pnpm 首次使用若代理为空：
pnpm.cmd config set proxy http://127.0.0.1:23333
pnpm.cmd config set https-proxy http://127.0.0.1:23333
pnpm.cmd config set registry https://registry.npmmirror.com/
# 连通性：curl.exe -s -o NUL -w "%{http_code}" --max-time 10 https://registry.npmmirror.com
```

### 2. 更新 npm 全局包（串行）
```powershell
dsh.cmd --version
npm.cmd view @deepseek-ai/dsh dist-tags                       # 仅核对，不据此切 tag
npm.cmd view @deepseek-harness-tui/dsh-tui dist-tags

# npm 根动态获取，禁止写死盘符（不要出现 D:\npm / C:\npm）
$npmPrefix = (npm.cmd config get prefix).Trim()
$npmRoot   = (npm.cmd root -g).Trim()
Write-Output "npm prefix=$npmPrefix; global root=$npmRoot"
npm.cmd install -g --prefix "$npmPrefix" @deepseek-ai/dsh@latest @deepseek-harness-tui/dsh-tui@latest
# prefix 已正确配置时也可省略 --prefix；当前版本 > latest tag 且用户没要求切：保持不降级并说明
```

### 3. 更新/补全各 profile 插件依赖（重点）
```powershell
foreach ($p in (Get-ChildItem "$env:USERPROFILE\.dsh\profiles" -Directory)) {
  Push-Location $p.FullName
  pnpm.cmd update --latest     # 升所有插件（git 依赖也会重解析）；allowBuilds 已配好时原生模块直接编译
  pnpm.cmd install             # 补全缺失依赖（node_modules 不全时的关键一步）
  # 仅当有原生模块没被许可、没编译时才：
  # pnpm.cmd approve-builds; pnpm.cmd rebuild
  Pop-Location
}
```
git 依赖不可达（记录 `git@github.com:` SSH 地址、codeload 挂起）：
```powershell
git ls-remote https://github.com/<owner>/<repo>.git HEAD   # 先验证 HTTPS
# 需要重写 SSH→HTTPS（全局配置长期生效，先告知用户）：
git config --global url."https://github.com/".insteadOf "git@github.com:"
git config --global url."https://github.com/".insteadOf "ssh://git@github.com/"
```
> 若依赖已装好、lockfile 未变而 git 解析持续挂起，可跳过 install，`pnpm.cmd outdated` 确认无更新后继续。

### 4. 实测 dsh web 启动
```powershell
$port = 3088
if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
  $port = 3090 + (Get-Random -Max 9)     # 被占就换一个，并在报告里写明
}
$out = Join-Path $env:TEMP "dsh-web-test.out.log"
$err = Join-Path $env:TEMP "dsh-web-test.err.log"
Remove-Item $out,$err -ErrorAction SilentlyContinue

# 方式 A：Start-Process 直接拉起 dsh.cmd，分别重定向 stdout/stderr（实测可行）
$p = Start-Process -FilePath "dsh.cmd" -ArgumentList "web","--port","$port" `
      -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 20
Get-Content $out; Get-Content $err
Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
```
判定标准：
- stdout 出现 `dsh web: http://127.0.0.1:<port>/?token=...` 且端口在 Listen → 启动成功；
- 带 token 访问返回 **200**（实测首页 ~46 KB；部分版本为 303 跳转），无 token 返回 **401** → 服务正常：
```powershell
$line = (Select-String -Path $out -Pattern 'dsh web: (http://\S+)').Matches.Groups[1].Value
curl.exe -s -o NUL -w "%{http_code}`n" "$line"                 # 带 token：200/303
curl.exe -s -o NUL -w "%{http_code}`n" "http://127.0.0.1:$port/"  # 无 token：401
```
- stderr 出现 `skipping profile bundle`：记为不兼容插件（见第 5 节），web 能起就不必处理；
- 出现 `SyntaxError` / `does not provide an export named` / `Cannot find module` /
  「不允许多实例」：按核心规则 5、6 定位并临时禁用问题插件后重测。

### 5. 已知不兼容插件（实测，dsh 0.2.0-rc.2）
启动时被 dsh 自动跳过（installed but not loaded），web 仍正常启动：
- **@kagurazakayashi/dsh-delete-session@1.1.0**：peerDeps 要求一组 `@deepseek-ai/dsh-*`
  `>=0.1.3-alpha.2 <0.2.0-0`，与 dsh 0.2.0-rc.2 冲突。需等作者发兼容版，或
  `dsh plugin allow-version` 显式豁免（自担风险）。
- **@kagurazakayashi/dsh-startup-command@1.0.1**：peerDeps 要求
  `@deepseek-ai/dsh-settings ^0.1.2-rc.1`、`@deepseek-ai/dsh-host-webserver ^0.1.2-rc.1`。
> 历史：`dsh-get-balance` 曾因 `import { settingsNamespace } from dsh-settings`（新版已移除该导出）
> 导致启动即崩，已卸载；若又出现，禁用并报告。`dshmarket` 仅在注释里提到该符号，无影响，不用动。

### 6. 退出测试进程 + 清理
```powershell
taskkill /F /T /PID $p.Id                 # /T 关键：带走 node 子/孙进程
Start-Sleep -Seconds 2
Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue  # 应为空
# 兜底：仍有监听则按端口反查 PID 强杀
# Get-NetTCPConnection -LocalPort $port -State Listen |
#   ForEach-Object { taskkill /F /PID $_.OwningProcess }
Remove-Item $out,$err -ErrorAction SilentlyContinue
# 临时改过 cordis.patch.yml 必须恢复（改前已备份）；清理本次其它 TEMP 临时文件
```

### 7. 输出插件信息表格
报告至少包含：
- **全局包**：更新前 → 更新后版本、是否成功（含是否本就 latest）；
- **store-dir**：各 profile `.npmrc` 是否被写坏、修复情况、`pnpm store path` 结果；
- **各 profile**：profile 名、`pnpm update/install` 结果；
- **插件表**：插件名 / 旧版本 → 新版本 / 来源（npm 或 git）/ 状态（✅正常加载 / ⚠️被跳过·不兼容 / 🔧已更新）；
- **问题与处理**：问题、原因、处理方式（尤其被跳过插件、store、证书、多实例）；
- **测试结论**：`dsh web --port <port>` 启动结果、HTTP 状态码、进程与端口是否已清理。

## 联动

| Skill | 路径 | 何时用 |
|---|---|---|
| `yashi-dsh-config-sync` | `~/.agents/skills/yashi-dsh-config-sync/SKILL.md` | 插件清单/配置丢失或被改乱、备份或迁移 dsh 配置、导入后补依赖与实测启停 |
| `yashi-dsh-env-sync` | `~/.agents/skills/yashi-dsh-env-sync/SKILL.md` | 启动正常但缺 API key、换机后密钥恢复 |
| `yashi-global-skills-sync` | `~/.agents/skills/yashi-global-skills-sync/SKILL.md` | 把本机 `.agents/skills` 同步/备份到其它位置 |

## 常见坑

| 现象 | 原因 | 处理 |
|---|---|---|
| `ERR_PNPM_UNEXPECTED_STORE` | `.npmrc` store-dir 被写成实际 store 路径（版本目录重复），pnpm 再追加一层 | 去掉末尾重复版本目录；pnpm 12 下默认已用 v11、通常不再复现（核心规则 2） |
| `SELF_SIGNED_CERT_IN_CHAIN` | 本机代理 MITM 重签 TLS | 命令级 `$env:npm_config_strict_ssl="false"`，勿写全局 |
| `unexpected argument '--allow-scripts'` | 新版 pnpm 移除该参数 | 用 `pnpm-workspace.yaml` 的 `allowBuilds:` 或 `pnpm approve-builds` |
| `npm.ps1/dsh.ps1 cannot be loaded ... scripts is disabled` | PS 5.1 执行策略拦截 | 改用 `.cmd` |
| 全局包装完 dsh 命令消失、node_modules 残缺 | 多个 npm install 并发互相清理 | 全局更新串行；损坏则删残缺目录逐个重装 |
| 全局包装错位置 / 想知道装到哪 | 凭盘符猜 / 写死 prefix | 动态获取：`$npmPrefix=(npm.cmd config get prefix)`、`npm.cmd root -g`，再 `--prefix "$npmPrefix"` |
| `skipping profile bundle ... incompatible` | 插件 peerDeps 与当前 dsh 不满足 | web 能起则记录；崩溃/多实例才临时禁用（核心规则 6） |
| 启动即崩，日志有 `settingsNamespace` 等 SyntaxError | 插件引用新版 core 已移除导出 | 定位并禁用该插件，测通后报告 |
| pnpm install git 依赖挂起/不可达 | SSH 地址或 codeload 网络问题 | git insteadOf 重写 HTTPS（先告知）；装好可跳过 |
| 插件全报找不到模块 | node_modules 几乎为空 | profile 目录 `pnpm install` |
| 原生模块运行时缺 `.node` | 构建未被许可 | `allowBuilds` 配好或 `approve-builds` → `rebuild` |
| 测试后端口仍占用 | 只 kill 包装器，node 成孤儿 | `taskkill /F /T /PID`，必要时按端口反查 |
| 3088 被占 | 前次进程未退或他用 | 换空闲端口并说明；勿占 3081 |

## 环境备注

- shell：Windows PowerShell 5.1；工具用 `.cmd`，curl 用 `curl.exe`，临时目录用 `$env:TEMP`。
- 全局 npm 根不要写死：先 `$npmPrefix=(npm.cmd config get prefix)`，需要显式指定时 `--prefix "$npmPrefix"`。
- 路径一律反斜杠 `\`；pnpm 12 store 为 `...\pnpm\store\v11`。
- 测试产生的持久运行数据（`storages/`、用量账本等）属正常数据，不清理。
