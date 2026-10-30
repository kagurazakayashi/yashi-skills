---
name: yashi-agents-md-export
display_name: AGENTS.md 规则数据库导出
description: >-
  从规则数据库 ~/.agents/AGENTS.json 导出 AGENTS.md：按平台（windows/linux/macos/all）与语言
  （en/zh-CN）筛选条目，生成对应语言的 Markdown 列表。脚本自动侦测操作系统——未指定平台/语言时，
  按当前系统导出英文版；自动侦测环境，逐条校验规则里声明的命令是否真的存在，剔除含无效命令的条目
  并报告（含命令级可用性清单）。也负责说明数据库的字段格式与维护方式，供 AI 增删改规则。
  当用户说“导出 AGENTS.md”“重新生成全局规则”“AGENTS.json 加一条规则”“按平台生成 AGENTS”
  “这套规则里哪些命令没装”“重新规整 AGENTS 规则”时，调用本技能。
---

# AGENTS.md 规则数据库导出

把 `~/.agents/AGENTS.json`（唯一权威来源）按**平台 + 语言**导出成 `AGENTS.md`。
导出的文件都是产物，规则改动永远改数据库，不要直接改产物。

## 文件与路径

- 数据库：`~/.agents/AGENTS.json`（Windows 下即 `%UserProfile%\.agents\AGENTS.json`）。
- 导出脚本：本 skill 目录下的 `scripts/export_agents_md.py`，**按本 SKILL.md 所在目录拼路径**，
  不要假设当前工作目录；默认数据库路径由脚本自身位置推导（`scripts/` 的上两级）。
- Python：PATH 里的 `python` 即可；**只用标准库**（json / argparse / shutil / platform / datetime）。
- 与数据库对应的人类可读原始文件：`~/.agents/AGENTS.md`（英文）与 `~/.agents/AGENTS.zh-CN.md`
  （简体中文）。它们是本数据库最初的两份来源，目前仍逐条一致。

## 数据库格式（AGENTS.json）

顶层字段：

| 字段 | 含义 |
| --- | --- |
| `schema` | 固定 `yashi-agents-rules/2`；格式有不兼容改动时才升版本 |
| `description` | 数据库用途说明（面向 AI） |
| `platform_values` | 合法平台取值：`all`、`windows`、`linux`、`macos` |
| `language_values` | 合法语言取值：`en`、`zh-CN` |
| `timezone` | `updated_at` 所用时区，当前 `Asia/Shanghai` |
| `source_metadata` | 导出时写入产物顶部的说明注释，按语言分组（见下） |
| `rules` | 规则数组，顺序即导出顺序 |

`source_metadata` 的每个语言键（`en` / `zh-CN`）可有 `note_en` / `note_zh` 作为主说明，
续行用同后缀加序号（`note_zh_2`、`note_zh_3`…）按序拼接，整段以 HTML 注释
（`<!--` … `-->`）写在产物最前，便于人读又不影响 Markdown。某语言留空对象即不写注释
（英文版当前就没有注释，与原始文件保持一致）。

每条规则（`rules[]`，字段顺序固定）：

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `id` | string | 编号即 `rules` 中的位置，从 `AGENT-001` 起连续编排；按重要程度插入或删除规则后，其后编号整体顺延/前移，因此它是位置而不是永久身份，须同步更新正文交叉引用与导出产物（2026-10-20 起采用此方式） |
| `zh` | string | 简体中文版规则正文（单行、无列表符号） |
| `en` | string | 英语版规则正文（与 `zh` 逐条对应） |
| `platforms` | array | 适用平台，取值见 `platform_values`；`all` 表示全平台。短字符串数组写成一行 |
| `commands` | array | 规则涉及且**需要校验**的命令，元素为字符串；只在特定平台才有意义的写作 `{"cmd": "...", "os": "windows"}`。没有就写 `[]`。短数组写成一行 |
| `category` | string | 分类，见下 |
| `scope` | string | `global` 全局规则 / `project` 只在项目（git 仓库）中才有意义 |
| `updated_at` | string | 该条规则最后一次改动日期，`YYYY-MM-DD`，用 `timezone` 指定时区 |

`category` 既有取值：
`environment`、`environment-tools`、`language`、`decision`、`safety`、`privileges`、`interaction`、`screenshot`、
`assumptions`、`code-changes`、`testing`、`documentation`、`files`、`markdown`、`git`、`deletion`、
`agents-files`、`parallelism`、`debugging`、`skills`、`dependencies`、`network`。
新增分类前先想清楚是否真的不属于以上任一类。

### 维护约定（AI 增删改规则时遵守）

1. 只改 `AGENTS.json`；改完用本脚本重新导出 `AGENTS.md` / `AGENTS.zh-CN.md`，不要手工编辑产物。
2. 新增规则：按重要程度插入对应分块（不要机械追加到末尾），`zh`/`en` 必须同时写全且语义逐条对应；
   插入点之后的 `id` 整体顺延，并同步更新正文交叉引用、本文件与导出产物。
   `rules` 的顺序即导出顺序，分块顺序为：安全与授权 → 决策与交互 → 语言 → 环境与命令路由 →
   代码改动 → 构建测试与资源 → 文件与产物 → Git → 文档与记忆 → 依赖 → 其余主题。
3. 改规则：保留 `id` 不变；`zh`/`en` 都按面向 AI 可读的句式撰写（场景或条件 → 必做动作 → 禁止项与例外 → 相关规则引用），中英表达同一约束，不得增删要求或改变义务强度；改后把 `updated_at` 改成当天日期（`Asia/Shanghai`）。
4. 删除规则：**先问用户**（这是用户自己定义的规则）；确认后从数组移除，其后 `id` 整体前移，并同步更新正文交叉引用、本文件与导出产物。
5. `commands` 只放**要校验的**命令：缺了它这条规则就没有意义或无法执行，才算数（如
   `ECAPTURE.EXE`、msys2 相关条目的 `bash`、`AGENT-037` 的 `git`）。只是**被举例提到**的
   命令不要写进去（`AGENT-023` 的 `cargo`、`docker`；`AGENT-053` 的 `npm`），否则目标机器
   少一个工具就会把整条规则删掉 —— 漏导出比多导出更糟。
   特别注意：规则文字**自带条件**的（如 `AGENT-023`“如果环境中存在 `rtk`……若不存在就直接用
   原命令”）绝不能把它自己提到的命令写进 `commands`，那会在缺该工具的机器上把“退化用法”这条
   规则本身删掉，正好删掉最该保留的那条。
   只在某个平台存在的命令用 `{"cmd": "...", "os": "windows"}` 标注，这样导出其他平台时不会
   拿它去校验、也不会误删。
6. `platforms` 与 `commands` 这类**短字符串数组写成一行**（`["all"]`、`["git", "npm"]`），
   便于人工校对；嵌套结构（`{"cmd","os"}`）才展开多行。
7. 新增了某个平台专属的命令写法后，同步在本节与脚本的 `command_entries()` 里说明，两处都要更新。
6. 平台判定：只在某个系统成立（或使用该系统专有的 shell/工具）的规则写具体平台；跨系统通用写 `all`。
   拿不准时写 `all`，漏导出比多导出更糟。

## 导出脚本用法

在任意目录执行（脚本自己推导数据库路径）：

```bash
# 默认：自动侦测当前系统 -> 英文 AGENTS.md，输出到当前目录
python ~/.agents/skills/yashi-agents-md-export/scripts/export_agents_md.py

# 指定平台与语言、指定输出文件
python .../export_agents_md.py --platform windows --lang zh-CN -o ./AGENTS.md

# 两种语言一次导出到同一目录
python .../export_agents_md.py --lang both -o ./out

# 只看会导出哪些规则、跳过哪些、命令可用性，不写文件
python .../export_agents_md.py --dry-run --verbose

# 检查数据库自身完整性，并与现存 AGENTS.md / AGENTS.zh-CN.md 逐条比对
python .../export_agents_md.py --self-test

# 机器可读报告
python .../export_agents_md.py --dry-run --report-json report.json
```

参数要点：

- `--platform`：`auto`（默认，= 当前系统）/ `windows` / `linux` / `macos` / `all`。
  `all` 表示不过滤平台。
- `--lang`：`auto`（默认，= `en`）/ `en` / `zh-CN` / `both`。
- `--check`：`auto`（默认）/ `on` / `off`，是否校验命令是否存在。
  **只有「目标平台 == 当前平台」时才真的探测**：`auto` 在导出非当前系统时自动跳过并提示
  （本机装没装不能代表目标机器）；`--check on` 若目标平台不是当前平台，同样会跳过并告警，
  所以不会拿 Windows 的探测结果去删 Linux 的条目。
- `--scope`：`all`（默认）/ `global` / `project`，按 `scope` 过滤（导出到某项目时可用 `project`）。
- `--newline`：`auto`（默认，Windows 用 CRLF、其余平台 LF）/ `lf` / `crlf`。
  产物换行只受此项控制，与当前终端无关。
- 输出：`-o` 以 `.md` 结尾视为文件，否则视为目录（目录里写 `AGENTS.md`）；
  `--lang both` 时同目录另写 `AGENTS.zh-CN.md`（`-o` 给的是文件名时，以它为英文名、
  推导出 `<名字>.zh-CN.md`）；
  目录已存在同名文件时先生成 `AGENTS.md.YYYYMMDD_HHMMSS.bak` 再覆盖（`--no-backup` 可关闭）。
- 退出码：`0` 成功；`1` 参数或数据库错误；`2` 有规则的命令缺失（已剔除，输出仍生成）。

## 默认行为（用户没提要求时）

- 平台 = 当前系统；语言 = **英文**（符合全局规则“新建 AGENTS.md 默认英文”）；
- 输出到**当前工作目录**的 `AGENTS.md`；
- 开启命令校验，剔除并报告无效条目。

## 判定“命令是否存在”的口径

`auto` 检查器按命令名分派（`shutil.which` 收到 `.exe`/带点名字时会自动去掉后缀再查，所以
`ECAPTURE.EXE` 能查到）：

- **已知自带目录优先**：`ECAPTURE.EXE`、`rtk`、msys2 这类按规则就以绝对路径直接调用的程序
  不在 PATH 里，只看 PATH 会**误判成缺失**（实测 `ECAPTURE.EXE` 就在 skill 目录里）。
  故脚本内置了 `TOOL_HOMES` 表，先查这些已知位置，再退回 PATH。新增同类工具时把它加进
  `TOOL_HOMES`，否则对应的规则会被错误剔除。
- Windows 专名：`msys2`/`bash` → 必须找到 msys2 的 `bash.exe`（**PATH 里排在前面的是 WSL 的
  `bash.exe`，不可用，脚本会跳过它**）；`cmd`/`powershell`/`pwsh` → 对应 exe；`sh` 走 PATH。
- PowerShell 命令（`*-*` 形式，如 `Get-Command`）→ 用可用的 `pwsh`（其次 `powershell`）执行
  `Get-Command <name>` 判定；两者都不在时该命令视为缺失。
- POSIX shell 内建（`read`、`cd` 等）→ 能找到 msys2 bash 即视为可用。
- 其余 → `shutil.which`。

## 测试现状

脚本自带 `--self-test`（结构校验 + 与现存源文件逐条比对）；日常回归跑它，再加一次
`--dry-run --verbose` 看过滤与命令报告。

已完成且可复现的测试：

1. 参数矩阵：`--platform windows|linux|macos|all`、`--lang auto|en|zh-CN|both`、
   `--scope all|global|project`、`--check auto|on|off`、`--newline auto|lf|crlf`、
   `--dry-run`、`--report-json`、`--db`（临时库）、`-o`（文件/目录）。
2. 命令缺失：把某条规则的 `commands` 指向本机不存在的 `ark`（字符串形式与
   `{"cmd","os"}` 平台形式都测过），脚本剔除该条、打印缺失命令与原因、退出码 `2`；
   `--check off` 时全部保留；目标平台非本机时自动跳过校验。
   `{"cmd","os"}` 条目只在对应平台参与校验：导出 `--platform windows` 时校验，
   导出 `--platform linux` 时忽略。
3. 安全网：若目标平台正是本机、却一条命令都取不到（例如 `commands` 又被改名），
   脚本停用检查而不是把 45 条全删，并打印警告。
4. 回环一致性：`--lang zh-CN --newline lf` 的产物与 `~/.agents/AGENTS.zh-CN.md`
   **逐字节相同**；`--lang en --newline crlf` 的产物与 `~/.agents/AGENTS.md`
   **内容逐字节相同，仅文件末尾多一个换行**（原始英文文件缺尾换行、且 45 行里
   44 行 CRLF 混 1 行 LF，属原始文件的行尾瑕疵；数据库内容 100% 一致）。
5. 边界：数据库不存在、非法 JSON、schema 不符、字段缺失、平台/scope 取值非法、
   `commands` 元素不是字符串或 `{"cmd","os"}` 对象（含未知键、缺 `cmd`、`os` 非法），
   均给出明确错误并退出 `1`。
