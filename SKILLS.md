# Available Skills

This file provides a lightweight index of available AI Agent skills.
For detailed instructions, always read the corresponding `SKILL.md`.

## yashi-agents-md-export

Purpose:
从规则数据库 ~/.agents/AGENTS.json 导出 AGENTS.md：按平台（windows/linux/macos/all）与语言（en/zh-CN）筛选条目，生成对应语言的 Markdown 列表。

Use when:
- [Add use-when scenarios from SKILL.md]

Location:
`skills/yashi-agents-md-export/`

## yashi-build-skills-index

Purpose:
生成或更新 skill 索引：**先建或更新一份 JSON 数据库**（默认 ~/.agents/SKILLS.json），为每个 skill 记录文件清单与修改时间、上游 description 及其哈希、中英文的用途与适用场景、提示词示例快照；再由数据库渲染出面向 AI 的 ~/.agents/SKILLS.md（默认英文的轻量 Markdown 目录）、面向人类的自包含页面 ~/.agents/SKILLS.html（默认简体中文，分类分组、侧边导航、关键词搜索、锚点、目录链接），以及**面向人类阅读的 Markdown**（--md-style human：分类分组、目录、一句话用途、适用场景、提示词示例、每条「打开目录」链接）；把 --output 指到名为 README.md 的文件时会自动切到人类可读模式 + 简体中文，可直接给 skill 仓库生成首页索引。

Use when:
- 创建 skill 索引
- 更新 SKILLS.md / SKILLS.html
- 给 skill 仓库生成 README.md
- 新增、删除或同步 skill 后重建索引
- 检查索引是否最新

Location:
`skills/yashi-build-skills-index/`

## yashi-dida-cli

Purpose:
用 `dida`（@suibiji/dida-cli）在终端管理滴答清单 dida365 的任务、清单、标签、习惯、专注与倒数日—— 涵盖 npm 全局安装/更新（代理 + strict-ssl + allow-scripts 的正确姿势）、OAuth 或 API 口令登录、以及查询与增删改的常用命令。

Use when:
- 更新 dida-cli
- 列出我的清单/任务/标签/习惯/专注/倒数日
- 建一个滴答任务
- Dida 没登录怎么办
- Dida auth login

Location:
`skills/yashi-dida-cli/`

## yashi-dsh-config-sync

Purpose:
一句话导出/导入 DeepSeek Harness（dsh）的配置（压缩包为 7z，**压缩级别 -mx0-9 与 AES-256 密码可选、默认用 -mhe=on 连文件名一起加密**；有图形会话时装了 7-Zip 就用 **7zG 弹图形进度窗口**，否则自动回退控制台 7z；导入侧兼容旧版 zip 并能自动/按参数处理加密包），**核心是「先让用户选范围」**：脚本内置 24 个可选范围类别（插件配置文件、npm 插件包本体、skills、profiles、会话列表、会话正文、主题/皮肤包、宠物包、表情包、凭证与 KEY、环境变量清单、锁文件、TUI 状态、本地插件、历史备份、其它杂项……），既可用 `pick` 弹出原生 GUI 勾选框让用户点选（AI 可用 `--only` 决定只显示哪些选项），也可在 GUI 不可用时由 AI 用 `pick --list` 拿到结构化目录后自行提问，再把结果用 `--scope` 传回导出/导入；还支持 `--profiles` 限定 profile 与 `sync --from web --to desktop` 在 profile 间直接同步插件与配置（依赖并集、bundle 注册、cordis.patch.yml 按 id 差集合并，Electron 独占的 profile 如实跳过启动测试）。

Use when:
- 导出 dsh 配置
- 备份 dsh 配置
- 迁移 dsh 到新机器
- 导入 dsh 配置
- 恢复 dsh 配置

Location:
`skills/yashi-dsh-config-sync/`

## yashi-dsh-desktop-migrate

Purpose:
把某个 dsh profile（通常是 web）的插件与配置整体迁移到桌面版 profile（desktop），让桌面版拥有与 web 版一致的插件集与设置——包括依赖清单（package.json 的 dependencies）、bundle 注册（dsh.profile.bundles）、pnpm 配置（pnpm-workspace.yaml / .npmrc / pnpm.cmd / pnpm-lock.yaml）与设置条目（cordis.patch.yml 的合并）。

Use when:
- 把 dsh web 的插件和配置导入桌面版
- 桌面版没有宠物/皮肤/侧边栏/通知器这些 web 插件
- 让 desktop profile 和 web profile 的插件集一致
- 桌面版插件迁移
- Dsh 桌面版功能比 web 版少

Location:
`skills/yashi-dsh-desktop-migrate/`

## yashi-dsh-env-sync

Purpose:
一句话导出/导入 DeepSeek Harness（dsh）的环境变量（API key 等）——自动扫描 $DSH_HOME/settings.yaml、各 profile 的 cordis.patch.yml 与 package.json、以及插件配置里所有 apiKeyEnv / process.env.X / ${VAR} 引用，导出到 ini 文件备份/迁移；或从 ini 导入恢复（Windows setx 写入用户环境，新终端生效；也可输出 shell / powershell export 语句）。

Use when:
- 导出 dsh 环境变量
- 备份 API key
- 迁移 dsh 到新机器需要恢复 key
- 从 ini 导入环境变量
- Dsh 换机器后 API key 恢复

Location:
`skills/yashi-dsh-env-sync/`

## yashi-dsh-ssh-otp-patch

Purpose:
给 @linxin666/dsh-ssh 插件补上 keyboard-interactive 2FA 支持（TOTP / 短信验证码，典型场景是服务器启用了 libpam-google-authenticator）：把 onKeyboardInteractive 沿 exec 链透传进 connectClient，并给 ssh_exec 增加可选 otp 参数，让 dsh 的 agent 工具能连上要求二次验证的主机；首次验证成功后连接池复用，同一 alias 后续调用不再需要验证码。

Use when:
- Dsh ssh 连不上 2FA 主机
- Ssh_exec 报 Authentication failed (keyboard-interactive): Verification code
- 插件升级后 otp 参数消失
- 重新打 2FA 补丁
- Ssh 主机要谷歌验证码

Location:
`skills/yashi-dsh-ssh-otp-patch/`

## yashi-dsh-update

Purpose:
一句话更新 DeepSeek Harness（dsh）全家桶——npm 全局包（@deepseek-ai/dsh、@deepseek-harness-tui/dsh-tui）及 $DSH_HOME/profiles 下的所有 profile 插件，安装/补全缺失依赖（pnpm update/install + allowBuilds/approve-builds），先检查 profile .npmrc 的 store-dir（dsh 更新功能历史上会写坏，报 ERR_PNPM_UNEXPECTED_STORE），处理 git 依赖与不兼容/被跳过插件，实测 `dsh web --port 3088` 启动（带 token 返回 200/303、无 token 401 校验）并整棵进程树退出、清理临时文件，最后输出当前全局包与插件信息表格。

Use when:
- 更新 dsh
- 升级 DeepSeek Harness
- Dsh 更新后启动失败
- Dsh web 起不来
- 插件依赖未安装

Location:
`skills/yashi-dsh-update/`

## yashi-encoding-convert

Purpose:
文本编码转换工具：读取任意编码的文件（ANSI/GBK、Big5、Shift-JIS、UTF-16 等），输出为 UTF-8（默认）或指定编码。

Use when:
- 适用于 AI 读取 ANSI/GBK 等非 UTF-8 文件时先转码，或用户需要以特定编码（如 GBK）保存/读取文件时。
- 当用户说"编码转换""ANSI 文件""GBK 编码""转成 UTF-8""读取乱码文件" "以 GBK 保存"或需要处理非 UTF-8 文本文件时，调用本技能。

Location:
`skills/yashi-encoding-convert/`

## yashi-evernight-capture

Purpose:
Capture window or full-screen images on Windows with ECAPTURE.EXE (EvernightCapture) by selecting targets through conditions.

Use when:
- Taking a screenshot
- Grabbing one specific app / window / dialog
- Discovering which windows match (--list / --inspect as structured JSON)
- Naming a monitor by a stable identifier (--screens)
- Capturing several windows at once

Location:
`skills/yashi-evernight-capture/`

## yashi-flutter-debug

Purpose:
Flutter 调试工作流（Android 真机 / Windows、macOS、Linux 桌面 / Web）：建立稳定的 flutter run 调试会话，并获取 Widget Tree、应用日志输出与目标窗口截图。

Use when:
- Flutter 调试
- Android 真机调试
- Flutter 桌面调试（Windows / macOS / Linux）
- 查看 widget 树 / Widget Tree
- 抓应用日志 / 控制台输出
- 调试截图与热重载观测

Location:
`skills/yashi-flutter-debug/`

## yashi-github-read-contributions

Purpose:
讀取 GitHub 使用者的 contributions 熱力圖（貢獻日曆），分析活躍度，找出最早無貢獻日期、計算連續貢獻天數等。

Use when:
- 適用於任意 GitHub 使用者。

Location:
`skills/yashi-github-read-contributions/`

## yashi-global-skills-sync

Purpose:
一句话同步用户全局 skill 目录 `~/.agents/skills/`——从六个固定 GitHub 仓库按前缀拉取/更新/删除 skill：dart-lang/skills（dart-*）、flutter/agent-plugins（flutter-*，且其中 dart-* 以此仓库为准覆盖 dart-lang）、samber/cc-skills-golang（golang-*，跳过 samber 自家 7 个库 golang-samber-*，只保留通用核心）、galiacheng/mindmap-skills（mindmap*）、nextlevelbuilder/ui-ux-pro-max-skill（ui-ux-pro-max，按官方 npm 全局装 ui-ux-pro-max-cli 后用 uipro init --ai universal --global）、kagurazakayashi/yashi-skills 的 skills/ 目录（yashi-*；该源原先取 CodeNotebook 的 .agents/skills，现已成为独立仓库，本 skill 自身也由它分发）。

Use when:
- 同步全局 skill
- 更新 skill
- 检查 skill 有没有新版本
- 拉取 dart/flutter/golang/mindmap/ui-ux/yashi skill
- Skill 增删改

Location:
`skills/yashi-global-skills-sync/`

## yashi-mcp-proxy-injection

Purpose:
为任意读取网址或通过非中国搜索引擎（Google、Bing 国际版等）搜索的本地 MCP 服务器注入代理，解决其联网失败(fetch failed、chromium ERR_TIMED_OUT / ERR_CONNECTION_TIMED_OUT、无法访问被墙的 API 或搜索引擎)。

Use when:
- 通用方法论：适用于任意此类 MCP 服务器、任意 MCP host（opencode / Claude Code / Claude Desktop / Cline 等）与任意本地代理服务器（HTTP CONNECT 或 SOCKS5），代理地址通过环境变量可配置，不绑定特定插件。
- 安装此类 MCP 服务器时，先询问用户是否需要代理补丁、以及代理到哪个地址，确认后再动手。
- 适用于安装、修复或排查无法联网的 MCP 服务器、MCP host 的 env 代理设置无效、以及 npm update -g 覆盖补丁后需要重新注入的场景。

Location:
`skills/yashi-mcp-proxy-injection/`

## yashi-md-table-format

Purpose:
格式化 Markdown 表格，按显示宽度对齐（全角字符占 2 格、半角字符占 1 格），保留每列左/右/居中对齐方式。

Use when:
- 适用于 .md 文件中的表格、代码文件注释中的表格文本、以及任何中英文混排需要工整对齐的表格。
- 当需要"表格对齐""整理表格""格式化表格" "表格工整""让表格列宽一致"，或准备手工拼空格对齐表格时，调用本技能；禁止手工拼空格对齐表格。

Location:
`skills/yashi-md-table-format/`

## yashi-node-tls-trust-fix

Purpose:
诊断并修复杀毒软件、企业代理等对 HTTPS 做中间人（MITM）替换证书后，Node 类工具 TLS 校验失败的问题：报 SELF_SIGNED_CERT_IN_CHAIN、UNABLE_TO_VERIFY_LEAF_SIGNATURE、 fetch failed、模型请求频繁重试等。

Use when:
- 某个域名为什么偶发失败
- 当遇到"证书错误""证书校验失败""self-signed certificate in certificate chain" "HTTPS 被杀毒软件拦截""加密连接扫描""模型经常重试""fetch failed"时调用本技能。

Location:
`skills/yashi-node-tls-trust-fix/`

## yashi-opencc-convert

Purpose:
使用 OpenCC 进行中文简繁转换，支持简体↔繁体、台湾/香港繁体变体、日本新字体。

Use when:
- 适用于 .md 文件、代码注释、对话内容中的简繁转换需求。
- 当用户说"简繁转换" "简体转繁体""繁体转简体""转成台湾繁体""转成香港繁体""把这段转成繁体/简体" 或需要统一文本简繁风格时，调用本技能。

Location:
`skills/yashi-opencc-convert/`

## yashi-opencode-config-portability

Purpose:
一句话导入或导出 opencode 的全部设置（备份/还原/迁移/同步 opencode 配置）。

Use when:
- The user says 导出/备份/导入/还原/迁移/同步 opencode 设置 或 提到 opencode_config.7z。

Location:
`skills/yashi-opencode-config-portability/`

## yashi-rtk-token-saver

Purpose:
在终端执行开发命令时优先改用 rtk 包装，以压缩输出、节省 60-90% 的 LLM token。

Use when:
- 适用于 git / gh / glab / gt / cargo / go / golangci-lint / npm / npx / pnpm / bun / deno / python(pytest,ruff,mypy,pip,uv) / jest / vitest / playwright / ctest / tsc / eslint / prettier / next / prisma / dotnet / mvn / sbt / gradlew / docker / kubectl / oc / aws / psql / php(phpunit,phpstan,pest,pint 等) / ruby(rspec,rubocop,rake) 等命令，以及 ls / tree / cat / head / tail / grep / rg / find / wc / diff / json / log / env / deps 等文件操作。
- 当需要运行测试、构建、lint、查看 git 状态/差异/日志、列目录、搜索文件内容、查看日志或 JSON 时使用。

Location:
`skills/yashi-rtk-token-saver/`

## yashi-windows-bsod-analysis

Purpose:
分析 Windows 蓝屏（BSOD）内核转储：识别完整/内核/活动/小内存（triage）转储，从 dump 头直接读出 BugCheck 码与参数，用 WinDbg/cdb 加载匹配符号还原真实异常栈与责任模块，并区分「蓝屏路径」与「故障路径」，最后给出可疑驱动排序与下一步取证方案。

Use when:
- 蓝屏
- BSOD
- Minidump
- MEMORY.DMP
- BugCheck 0x1E / 0x50 / 0xD1 / 0x139

Location:
`skills/yashi-windows-bsod-analysis/`

## yashi-windows-check-update

Purpose:
检查 Windows 11/10/Server 的最新功能更新，从 Microsoft 发布健康页面和 Update Catalog 提取 KB 信息及下载链接。

Use when:
- 适用于 x64/ARM64 架构。

Location:
`skills/yashi-windows-check-update/`
