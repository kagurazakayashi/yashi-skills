# 可用 Skill 索引 · yashi-skills

共 16 个 skill

## 目录

**个人工具**（16 个）

- [yashi-build-skills-index](#yashi-build-skills-index)
- [yashi-dida-cli](#yashi-dida-cli)
- [yashi-dsh-config-sync](#yashi-dsh-config-sync)
- [yashi-dsh-desktop-migrate](#yashi-dsh-desktop-migrate)
- [yashi-dsh-env-sync](#yashi-dsh-env-sync)
- [yashi-dsh-update](#yashi-dsh-update)
- [yashi-encoding-convert](#yashi-encoding-convert)
- [yashi-github-read-contributions](#yashi-github-read-contributions)
- [yashi-global-skills-sync](#yashi-global-skills-sync)
- [yashi-mcp-proxy-injection](#yashi-mcp-proxy-injection)
- [yashi-md-table-format](#yashi-md-table-format)
- [yashi-node-tls-trust-fix](#yashi-node-tls-trust-fix)
- [yashi-opencc-convert](#yashi-opencc-convert)
- [yashi-opencode-config-portability](#yashi-opencode-config-portability)
- [yashi-rtk-token-saver](#yashi-rtk-token-saver)
- [yashi-windows-check-update](#yashi-windows-check-update)

---

**个人工具**（16 个）

## yashi-build-skills-index

用途:
生成或更新 skill 索引，产出 AI 用的 `SKILLS.md`、人类用的 `SKILLS.html` 与给 skill 仓库当首页的 `README.md`，并校验 name/description 缺失、目录名不一致与重名等问题。

适用场景:

- 创建或重建 skill 索引
- 更新 SKILLS.md / SKILLS.html
- 新增、删除或同步 skill 后重建索引
- 给 skill 仓库生成 README.md
- 检查索引是否与 skills 目录一致

位置:
`skills/yashi-build-skills-index/` · [打开目录](skills/yashi-build-skills-index/)

提示词示例:

```text
重建一下全局 skill 索引
新增了几个 skill，更新 SKILLS.md 和 SKILLS.html
```

## yashi-dida-cli

用途:
用 `dida`（@suibiji/dida-cli）在终端管理滴答清单 dida365 的任务、清单、标签、习惯、专注与倒数日，涵盖全局安装/更新、登录与常用增删改命令。

适用场景:

- 列出我的清单 / 任务 / 标签 / 习惯 / 专注 / 倒数日
- 新建或修改滴答任务、给任务打标签
- 更新或安装 dida-cli（代理 + strict-ssl + allow-scripts）
- dida 没登录，需要走 dida auth login
- 按关键词搜索滴答任务

位置:
`skills/yashi-dida-cli/` · [打开目录](skills/yashi-dida-cli/)

提示词示例:

```text
列出我的滴答清单和今天的任务
新建一个滴答任务并打上标签
```

## yashi-dsh-config-sync

用途:
一句话导出或导入 DeepSeek Harness（dsh）的完整配置，并支持只处理指定 profile、以及在 profile 之间直接同步插件与配置。

适用场景:

- 导出或备份 dsh 配置
- 导入或恢复 dsh 配置
- 迁移 dsh 到新机器
- 只处理指定 profile（--profiles web,desktop）
- 把一个 profile 的插件与配置同步到另一个 profile（sync --from web --to desktop）

位置:
`skills/yashi-dsh-config-sync/` · [打开目录](skills/yashi-dsh-config-sync/)

提示词示例:

```text
把我的 dsh 配置导出一份备份
换机器了，把 dsh 配置导进去
把 web 的插件和配置同步到桌面版
```

## yashi-dsh-desktop-migrate

用途:
把某个 dsh profile（通常是 web）的插件与配置整体迁移到桌面版 profile（desktop），让两者的插件集与设置保持一致。

适用场景:

- 把 dsh web 的插件和配置导入桌面版
- 桌面版缺少宠物、皮肤、侧边栏、通知器这些 web 插件
- 让 desktop profile 与 web profile 的插件集一致
- 桌面版插件迁移

位置:
`skills/yashi-dsh-desktop-migrate/` · [打开目录](skills/yashi-dsh-desktop-migrate/)

提示词示例:

```text
把 dsh web 的插件和配置都导入到桌面版
桌面版怎么没有宠物和皮肤这些插件
```

## yashi-dsh-env-sync

用途:
一句话导出或导入 DeepSeek Harness（dsh）的环境变量（API key 等），自动扫描配置里所有 apiKeyEnv / process.env.X / ${VAR} 引用。

适用场景:

- 导出或备份 dsh 环境变量与 API key
- 从 ini 导入环境变量恢复（Windows setx 写入用户环境，新终端生效）
- 迁移 dsh 到新机器后恢复 key
- 检查 dsh 的 key 是否配齐
- 需要输出 shell / powershell 的 export 语句

位置:
`skills/yashi-dsh-env-sync/` · [打开目录](skills/yashi-dsh-env-sync/)

提示词示例:

```text
导出我 dsh 的所有环境变量
检查一下 dsh 的 API key 有没有配齐
```

## yashi-dsh-update

用途:
一句话更新 DeepSeek Harness（dsh）全家桶：npm 全局包与 `$DSH_HOME/profiles` 下的所有 profile 插件，并实测 `dsh web` 能否正常启动。

适用场景:

- 更新 dsh 与全部 profile 插件
- 插件依赖未安装、不兼容或被跳过（pnpm update/install + allowBuilds）
- dsh 更新后启动失败、dsh web 起不来
- pnpm install 报错（含 ERR_PNPM_UNEXPECTED_STORE）或需要处理 git 依赖

位置:
`skills/yashi-dsh-update/` · [打开目录](skills/yashi-dsh-update/)

提示词示例:

```text
把 dsh 和所有插件更新到最新
dsh 更新后起不来了，帮我排查
```

## yashi-encoding-convert

用途:
文本编码转换：读取任意编码的文件（ANSI/GBK、Big5、Shift-JIS、UTF-16 等），输出为 UTF-8（默认）或指定编码，支持自动检测、管道输入输出与就地改写。

适用场景:

- AI 读到 ANSI/GBK 等非 UTF-8 文件，需要先转码
- 需要以指定编码（如 GBK）保存或读取文件
- 用户说「编码转换」「ANSI 文件」「GBK 编码」「转成 UTF-8」时

位置:
`skills/yashi-encoding-convert/` · [打开目录](skills/yashi-encoding-convert/)

提示词示例:

```text
这个文件是 GBK 的，读出来乱码，帮我转成 UTF-8
把这段文本以 GBK 保存
```

## yashi-github-read-contributions

用途:
讀取 GitHub 使用者的 contributions 熱力圖（貢獻日曆），分析活躍度，找出最早無貢獻日期、計算連續貢獻天數。

适用场景:

- 讀取任意 GitHub 使用者的貢獻熱力圖
- 計算連續貢獻天數、找出範圍內最早無貢獻日期

位置:
`skills/yashi-github-read-contributions/` · [打开目录](skills/yashi-github-read-contributions/)

提示词示例:

```text
看一下某个 GitHub 用户的贡献热力图
算一下他连续贡献了多少天
```

## yashi-global-skills-sync

用途:
一句话同步用户全局 skill 目录 `~/.agents/skills/`：从六个固定 GitHub 仓库按前缀拉取、更新或删除 skill（dart / flutter / golang / mindmap / ui-ux-pro-max / yashi）。

适用场景:

- 同步全局 skill、更新 skill
- 检查 skill 有没有新版本
- 拉取 dart / flutter / golang / mindmap / ui-ux / yashi skill
- skill 增删改后安装随之而来的 CLI 依赖

位置:
`skills/yashi-global-skills-sync/` · [打开目录](skills/yashi-global-skills-sync/)

提示词示例:

```text
同步一下我全局的 skill
检查和更新 dart、flutter、golang 这些 skill
```

## yashi-mcp-proxy-injection

用途:
为读取网址或走非中国搜索引擎的本地 MCP 服务器注入代理，解决其联网失败（fetch failed、chromium ERR_TIMED_OUT / ERR_CONNECTION_TIMED_OUT）。

适用场景:

- MCP 服务器联网失败、chromium 连不上被墙的站点或 API
- MCP host 的 env 代理设置无效，需要排查
- npm update -g 覆盖补丁后需要重新注入代理
- 安装这类 MCP 服务器前，先确认是否打补丁、代理到哪个地址

位置:
`skills/yashi-mcp-proxy-injection/` · [打开目录](skills/yashi-mcp-proxy-injection/)

提示词示例:

```text
这个 MCP 服务器联网失败，帮我注入代理
chromium 连不上 Google，给 MCP 配个代理
```

## yashi-md-table-format

用途:
格式化 Markdown 表格，按显示宽度对齐（全角字符占 2 格、半角字符占 1 格），保留每列原有的左/右/居中对齐方式。

适用场景:

- 把 .md 文件里的表格列宽对齐
- 整理代码注释里的表格文本
- 中英文混排的表格需要工整对齐
- 需要「表格对齐」「让表格列宽一致」时（禁止手工拼空格对齐）

位置:
`skills/yashi-md-table-format/` · [打开目录](skills/yashi-md-table-format/)

提示词示例:

```text
把这份 Markdown 的表格对齐一下
表格列宽不齐，整理成工整的
```

## yashi-node-tls-trust-fix

用途:
诊断并修复杀毒软件、企业代理对 HTTPS 做中间人（MITM）替换证书后，Node 类工具 TLS 校验失败的问题。

适用场景:

- 报 SELF_SIGNED_CERT_IN_CHAIN / UNABLE_TO_VERIFY_LEAF_SIGNATURE / fetch failed
- 怀疑杀毒软件或企业代理拦截 HTTPS、做加密连接扫描
- 模型请求频繁重试
- 反查某个域名为什么偶发失败

位置:
`skills/yashi-node-tls-trust-fix/` · [打开目录](skills/yashi-node-tls-trust-fix/)

提示词示例:

```text
模型老是重试，查一下是不是证书被换了
fetch failed，怀疑杀毒软件拦截了 HTTPS
把卡巴斯基的根证书加进 Node 的信任列表
```

## yashi-opencc-convert

用途:
用 OpenCC 做中文简繁转换，支持简体↔繁体、台湾/香港繁体变体与日本新字体，默认自动检测转换方向。

适用场景:

- 简体转繁体、繁体转简体，或转成台湾/香港繁体
- 统一 .md 文件、代码注释、对话内容的简繁风格
- 用户说「简繁转换」「转成繁体/简体」时

位置:
`skills/yashi-opencc-convert/` · [打开目录](skills/yashi-opencc-convert/)

提示词示例:

```text
把这份文档转成繁体中文
这段繁体转成简体
```

## yashi-opencode-config-portability

用途:
一句话导入或导出 opencode 的全部设置（备份 / 还原 / 迁移 / 同步），打包 `~/.config/opencode` 与 `~/.agents` 并用 7z 压缩。

适用场景:

- 导出或备份 opencode 设置
- 导入或还原 opencode 配置
- 迁移 opencode 到新机器
- 用户提到 opencode_config.7z 时

位置:
`skills/yashi-opencode-config-portability/` · [打开目录](skills/yashi-opencode-config-portability/)

提示词示例:

```text
导出我的 opencode 全部设置
把 opencode 配置迁移到新机器
```

## yashi-rtk-token-saver

用途:
在终端执行开发命令时优先改用 rtk 包装，压缩输出、节省 60-90% 的 LLM token。

适用场景:

- 运行测试、构建、lint，或查看 git 状态/差异/日志时优先走 rtk
- 覆盖 git / cargo / go / npm / pnpm / python(pytest,ruff,mypy) / docker 等命令
- 覆盖 ls / cat / head / tail / grep / rg / find / wc / diff / json 等文件操作

位置:
`skills/yashi-rtk-token-saver/` · [打开目录](skills/yashi-rtk-token-saver/)

提示词示例:

```text
跑一下测试，用 rtk 压缩输出
看下 git 状态和最近的提交
```

## yashi-windows-check-update

用途:
检查 Windows 11/10/Server 的最新功能更新，从 Microsoft 发布健康页面与 Update Catalog 提取 KB 信息及下载链接。

适用场景:

- 查 Windows 11/10/Server 的最新功能更新
- 需要对应的 KB 号与 Update Catalog 下载链接
- 按 x64 / ARM64 架构区分

位置:
`skills/yashi-windows-check-update/` · [打开目录](skills/yashi-windows-check-update/)

提示词示例:

```text
查一下 Windows 11 最新的功能更新
有没有新的累积更新 KB 和下载链接
```
