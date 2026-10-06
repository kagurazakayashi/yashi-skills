---
name: yashi-global-skills-sync
description: 一句话同步用户全局 skill 目录 `~/.agents/skills/`——从六个固定 GitHub 仓库按前缀拉取/更新/删除 skill：dart-lang/skills（dart-*）、flutter/agent-plugins（flutter-*，且其中 dart-* 以此仓库为准覆盖 dart-lang）、samber/cc-skills-golang（golang-*，跳过 samber 自家 7 个库 golang-samber-*，只保留通用核心）、galiacheng/mindmap-skills（mindmap*）、nextlevelbuilder/ui-ux-pro-max-skill（ui-ux-pro-max，按官方 npm 全局装 ui-ux-pro-max-cli 后用 uipro init --ai universal --global）、kagurazakayashi/yashi-skills 的 skills/ 目录（yashi-*；该源原先取 CodeNotebook 的 .agents/skills，现已成为独立仓库，本 skill 自身也由它分发）。规则：线上有本地无则添加、本地有线上无则删除、更新一律以线上为准（源仓库为空/无匹配时只告警不删除）；并按各 SKILL.md 要求安装 CLI/MCP 依赖（go install 立即执行；下载优先 npm -g 而非 npx，除非 skill 明确指定 npx；统一走代理 http://127.0.0.1:23333，证书问题临时 strict-ssl=false，npm 包开 allow-scripts）。最后检查/更新多个全局 AGENTS.md（~/.dsh/AGENTS.md、~/.config/opencode/AGENTS.md、~/.qoder-cn/AGENTS.md）的引用并删除失效项，输出全部 skill 清单（一句介绍）与本次变更。附**跨平台 Python 一键脚本** `scripts/sync-skills.py`（仅标准库，按大小+SHA-256 内容比对，Windows/macOS/Linux 通用，支持 --include/--dry-run/--proxy）。适用于「同步全局 skill」「更新 skill」「检查 skill 有没有新版本」「拉取 dart/flutter/golang/mindmap/ui-ux/yashi skill」「skill 增删改」等场景。
---

<!--
触发示例：
- 帮我同步一下全局 skill
- 检查 .agents/skills 里的 skill 有没有更新
- 从那几个 GitHub 仓库更新 dart/flutter/golang 的 skill
- 全局 skill 有新版本吗，顺便装上缺的 CLI
- 同步完列一下所有 skill 和这次改了什么
-->

# yashi-global-skills-sync — 全局 skill 一句话同步

## 核心规则（先读）

1. **只动这六个前缀/仓库，其余 skill 一律不碰**：本地可能还有 banner-design、brand、design、
   design-system、slides、ui-styling 等不在同步范围内的 skill，**保持原样**，不删不改不更新。
2. **同步三原则（对每个前缀组）**：
   - 线上有、本地无 → **添加**；
   - 本地有、线上该前缀下全无 → **删除**；
   - 同名两边都有 → **以线上版本为准覆盖更新**（不要保留本地旧文件、不要做"合并"）。
   目录级镜像用 `robocopy <src> <dst> /MIR`（退出码 0=无变化，1=有拷贝，<8 都算成功；
   /MIR 会清掉本地多出的文件，正好满足"以线上为准"）。
3. **dart-* 以 flutter 仓库为准**：dart-lang/skills 与 flutter/agent-plugins 都含同名 dart-*，
   两边内容通常一致；**只要 flutter 仓库里有 dart-*，就以 flutter 仓库版本覆盖**，dart-lang
   仅作交叉核对，不要反过来用 dart-lang 覆盖 flutter。
4. **golang 跳过 samber 自家库**：samber/cc-skills-golang 里 `golang-samber-*`（do/hot/lo/mo/
   oops/ro/slog 共 7 个）是作者自我推销，**不添加**；本地若已有则删除。其余 golang-*（含
   spf13-cobra/viper、uber-dig/fx、stretchr-testify、google-wire 等第三方库教程）属通用核心，
   正常同步。
5. **ui-ux-pro-max 走官方 npm 安装，不要手工拷文件**：官方 README 的方式是
   `npm install -g ui-ux-pro-max-cli` 然后 `uipro init --ai universal --global`（装到
   ~/.agents/skills/）。uipro 遇到已存在的 SKILL.md 会 "Skipped (already exists)"，
   需**先删除旧目录再 init**（或用其官方更新命令 `uipro update --global`，但该命令可能联网
   挂起，优先"删目录→重新 init"）。
6. **依赖安装**：同步完逐份扫描 SKILL.md 的 prerequisite / install 说明——
   - `go install ...` → **立即执行**；
   - npm 包 → **优先 `npm install -g` 全局装**，不要用 `npx`，**除非该 SKILL.md 明确写了用
     npx / 明确说"无需全局安装"**（例如 mindmap 明确用 `npx markmap-cli`，就不要全局装
     markmap-cli）；
   - MCP 服务器写进 host 配置（如 opencode.json）属配置变更，**不要擅自写入**，在报告里提示。
7. **网络（用户已固化，不必再问）**：所有下载统一走代理 `http://127.0.0.1:23333`。
   - git：`git -c http.proxy=... clone --depth=1`（或临时设全局 http.proxy/https.proxy）；
   - npm：设 `proxy/https-proxy`，代理 MITM 重签导致证书错时临时 `strict-ssl=false`，
     npm 包要 `allow-scripts`（新版 npm 用 `--allow-scripts` 或 config `allow-scripts=true`）；
   - go：`GOPROXY=https://goproxy.cn,direct`，必要时叠加 `HTTPS_PROXY`；
   - pip：走华为云镜像（本机已配），失败再挂代理。
8. **删除优先回收站**（遵循用户全局规则）：删 skill 目录时若环境有回收站优先回收站；
   PowerShell 直接 `Remove-Item -Recurse -Force` 前确认目标路径正确，避免误删。
9. **全程记录变更**：每个 skill 标记 ADDED / UPDATED / UNCHANGED / DELETED，最后汇总。

## 源仓库与前缀对照表

| 仓库 | 克隆 URL | 取哪些前缀 | 特殊处理 |
|---|---|---|---|
| dart-lang/skills | https://github.com/dart-lang/skills.git | `dart-*`（skills/ 下） | 仅交叉核对；被 flutter 版本覆盖 |
| flutter/agent-plugins | https://github.com/flutter/agent-plugins.git | `flutter-*` + `dart-*`（skills/ 下） | **dart-* 以此为准** |
| samber/cc-skills-golang | https://github.com/samber/cc-skills-golang.git | `golang-*`（skills/ 下） | **排除 `golang-samber-*` 7 个** |
| galiacheng/mindmap-skills | https://github.com/galiacheng/mindmap-skills.git | `mindmap*`（skills/ 下） | — |
| nextlevelbuilder/ui-ux-pro-max-skill | https://github.com/nextlevelbuilder/ui-ux-pro-max-skill.git | `ui-ux-pro-max` | **官方 npm**：装 ui-ux-pro-max-cli 后 `uipro init --ai universal --global` |
| kagurazakayashi/yashi-skills | https://github.com/kagurazakayashi/yashi-skills.git | `yashi-*`（**skills/** 下；仓库根另放 README.md 与 `.gitignore`） | 本 skill 自己也在其中；取代原先的 CodeNotebook 源 |

> 仓库结构会变，**以"目录里实际存在的 skill 文件夹名"为准动态发现**，不要硬编码 skill 名单；
> 新出现的同前缀 skill 自动纳入，消失的自动删除。

## 环境速览

| 项 | 位置 / 值 |
|---|---|
| 全局 skill 目录 | `~/.agents/skills/`（Windows 即 `C:\Users\<user>\.agents\skills\`） |
| 运行时 | **Python 3.8+**（一键脚本用，仅标准库，跨平台）；另需 `git`，golang 组需 `go` |
| 代理 | `http://127.0.0.1:23333`（用户固化） |
| Go 工具安装位置 | `go env GOPATH` 的 `bin`（本机 `P:\go\bin`），需在 PATH |
| Go 模块代理 | `https://goproxy.cn,direct` |
| 临时工作目录 | 系统临时目录下 `skill-sync/`（Windows `%TEMP%`，Linux/macOS `/tmp`），浅克隆仓库，结束可清理 |
| 全局 AGENTS.md | `~/.dsh/AGENTS.md`、`~/.config/opencode/AGENTS.md`、`~/.qoder-cn/AGENTS.md`（可能缺失） |

## 工作流

> 下列命令以 POSIX shell 风格书写（Linux/macOS 直接用，Windows 在 Git Bash / WSL 下用；
> 路径用 `~` 与 `/`）。一键 Python 脚本会自动处理平台差异，手工执行时再参考本节。

### 1. 准备：代理 + 临时目录 + 浅克隆
```bash
work="${TMPDIR:-/tmp}/skill-sync"; mkdir -p "$work"
export HTTPS_PROXY='http://127.0.0.1:23333' HTTP_PROXY='http://127.0.0.1:23333'
git clone --depth=1 https://github.com/flutter/agent-plugins.git   "$work/flutter"
git clone --depth=1 https://github.com/dart-lang/skills.git        "$work/dart"
git clone --depth=1 https://github.com/samber/cc-skills-golang.git "$work/golang"
git clone --depth=1 https://github.com/galiacheng/mindmap-skills.git "$work/mindmap"
git clone --depth=1 https://github.com/nextlevelbuilder/ui-ux-pro-max-skill.git "$work/uipro-src"
git clone --depth=1 https://github.com/kagurazakayashi/yashi-skills.git "$work/yashi-skills"
```
（git 把进度写 stderr，任何系统下都可能显示成"红字/报错"，**不是真错误**；以目录下有无
`.git` 判断成功。）

### 2. 镜像各前缀组（以内容为准，不依赖 robocopy）
- dart-* + flutter-*：源 `$work/flutter/skills/`；
- golang-*：源 `$work/golang/skills/`，**跳过 `golang-samber-*`**；
- mindmap*：源 `$work/mindmap/skills/`；
- yashi-*：源 `$work/yashi-skills/skills/`（**skills/ 子目录**，与其它源仓库同一约定；仓库根另有
  `README.md`、`.gitignore`、`.git/`，按 `yashi-*` 前缀筛选，别整体镜像）。

逐文件比对（**大小相同再比 SHA-256**，内容一致就不动，避免 git 全新检出导致时间戳不同而
误判为更新）：目标缺失/大小不同/哈希不同 → 拷贝；目标里有、源里没有的文件/目录 → 删除。
一键脚本的 `mirror_tree()` 已实现这套逻辑；手工时可用 `rsync -a --delete`（Linux/macOS）
或 `robocopy /MIR`（Windows），但这两者按时间戳判定，全新克隆后可能多拷一次（无害）。

### 3. 删除"线上无、本地有"的 skill
- 对每个前缀，取线上目录名集合，凡本地同前缀但不在集合内的 → 删除；
- golang 额外：删除任何本地 `golang-samber-*`；
- **安全阀**：某个前缀组在源目录里一个匹配都没有（源仓库还没推送、刚建库、克隆失败）时，
  **只告警、跳过该组的删除**——否则会把本地同前缀的 skill 连同本 skill 一起清空；
- 例：yashi-skills 线上无 `yashi-github-contribution-heatmap` 而本地有 → 删除。

### 4. ui-ux-pro-max 官方安装
```bash
npm install -g ui-ux-pro-max-cli
rm -rf ~/.agents/skills/ui-ux-pro-max        # Windows PowerShell: Remove-Item -Recurse -Force
uipro init --ai universal --global
```

### 5. 扫描并安装依赖
- grep 全部 SKILL.md：`go install` / `npm install -g` / `pip install` / `npx` / `mcp`；
- 常见 go install（按实际文档为准）：
  - `golang.org/x/tools/gopls@latest`
  - `github.com/go-delve/delve/cmd/dlv@latest`
  - `github.com/swaggo/swag/cmd/swag@latest`
  - `github.com/cweill/gotests/gotests@latest`
  - `golang.org/x/vuln/cmd/govulncheck@latest`
- 常见 npm 全局：`@suibiji/dida-cli`（yashi-dida-cli 用）、`ui-ux-pro-max-cli`；
- pip：`opencc-python-reimplemented`（yashi-opencc-convert 用）；
- 已存在的命令先探测（`command -v <name>` / Windows `Get-Command`，Python 用
  `shutil.which("<name>")`），缺了再装，不要重复装。

### 6. 检查全局 AGENTS.md
- 逐个检查三个路径（存在才处理）：
  - 已有"~/.agents/skills/ 通用引用"且不点名具体 skill → 通常**无需改**（增删 skill 不会失效）；
  - 若点名了某个已删除/改名的 skill → 删除该失效引用；
  - 若某 skill 的 SKILL.md 明确要求写全局引用 → 按其要求添加；
  - 缺失的 AGENTS.md **不要为了同步而新建**（除非用户要求）。
- 注意 golang-how-to 的 Configure 模式写的是**项目级** CLAUDE.md/AGENTS.md，不是全局。

### 7. 输出
1. 全部 skill 列表（按前缀分组，每个一句用途，用途取自该 skill frontmatter 的 description）；
2. 本次变更：ADDED / UPDATED / DELETED 名单 + 安装的 CLI/依赖 + AGENTS.md 改动；
3. 清理临时工作目录（可选，先问或按用户习惯）。

## 一键脚本（跨平台 Python）

运行 `scripts/sync-skills.py` 完成第 1~3、5 步（克隆、内容镜像、删除、go 依赖），仅依赖
Python 3 标准库，Windows / macOS / Linux 通用；ui-ux-pro-max 与 AGENTS.md 因涉及官方
安装器/配置核对，按脚本末尾提示手工确认。用法：
```bash
# 默认全部同步（Windows 若没有 python3 就用 python）
python3 scripts/sync-skills.py
# 只同步某几组
python3 scripts/sync-skills.py --include flutter golang
# 只看会改什么，不写盘
python3 scripts/sync-skills.py --dry-run
# 换代理
python3 scripts/sync-skills.py --proxy http://127.0.0.1:23333
```

## 排障

| 现象 | 原因 / 处理 |
|---|---|
| git clone 一片红字但目录在 | 进度走 stderr，任何系统都正常；看 `.git` 是否存在 |
| 克隆中断、目录里只有 `.git` | 脚本会判定为半成品并自动删除重克隆；手工时删目录重 clone |
| npm 报 SELF_SIGNED_CERT_IN_CHAIN | 代理 MITM 重签；临时 `npm config set strict-ssl false`（或 `--strict-ssl=false`） |
| uipro init 提示 already exists | 先删 `~/.agents/skills/ui-ux-pro-max` 再 init |
| `uipro update --global` 挂起 | 它在联网拉取；改用"删目录→uipro init" |
| go install 拉不下来 | 设 `GOPROXY=https://goproxy.cn,direct` 并挂 `HTTPS_PROXY` |
| Python 报找不到 git/go | 确认已安装且在 PATH；脚本对缺失的 go 会跳过 Go 依赖步骤 |
| 内容一致却被判定更新 | 脚本按大小+SHA-256 判定，不应发生；若用 rsync/robocopy 手工跑则可能因时间戳多拷一次（无害） |
| 某组报 `no <group> skill found ...; skipping deletions` | 源仓库为空或该前缀下无匹配（例如 yashi-skills 还没 push）；只跳过删除，本地保持不动 |
| yashi-* 组克隆成功但一个 skill 都没有 | 源是本仓库的 `skills/` 子目录，确认远端已推送、且 skill 目录名以 `yashi-` 开头（`sub` 是 `skills`，不是 `.agents/skills`） |
| 想确认取的是哪一层 | 六个源仓库统一取各自的 `skills/` 子目录；yashi 组取 `yashi-skills/skills/`，仓库根的 README.md 不会被当成 skill |
| Windows 执行策略/编码问题 | Python 脚本不涉及；直接 `python scripts\sync-skills.py`，终端用 UTF-8 |

