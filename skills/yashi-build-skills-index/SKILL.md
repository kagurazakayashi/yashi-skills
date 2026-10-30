---
name: yashi-build-skills-index
description: 生成或更新 skill 索引：**先建或更新一份 JSON 数据库**（默认 ~/.agents/SKILLS.json），为每个 skill 记录文件清单与修改时间、上游 description 及其哈希、中英文的用途与适用场景、提示词示例快照；再由数据库渲染出面向 AI 的 ~/.agents/SKILLS.md（默认英文的轻量 Markdown 目录）、面向人类的自包含页面 ~/.agents/SKILLS.html（默认简体中文，分类分组、侧边导航、关键词搜索、锚点、目录链接），以及**面向人类阅读的 Markdown**（--md-style human：分类分组、目录、一句话用途、适用场景、提示词示例、每条「打开目录」链接）；把 --output 指到名为 README.md 的文件时会自动切到人类可读模式 + 简体中文，可直接给 skill 仓库生成首页索引。每次运行都按文件列表与修改时间做增量比对：内容没变的条目直接复用已有的说明与译文，SKILL.md 变了才重写源文并把译文标为待校对，--rebuild 才强制全部重写。遍历 skills 目录下所有 skill，解析各自 SKILL.md 的 YAML front matter（name、description），并校验缺失 SKILL.md、name/description 缺失或用途无法判断、name 与目录名不一致、skill 重名等问题；发现问题不猜测，列出并询问用户。附跨平台、仅标准库的 Python 脚本 scripts/build_skills_index.py，支持 --dry-run、--check、--force、--rebuild、--mark-reviewed、--db、--db-only、--render-only、--ignore-ci、--collection、--md-style、--html-only、--no-html、--output、--html、--skills-dir、--include、--md-lang、--html-lang、--emit-draft、--draft-path、--keep-draft。检测到仓库里存在调用本脚本的 GitHub Action 时，本地运行自动改为只更新数据库，其余产物交给 CI 渲染；附 Action 模板 templates/skills-index.yml（CI 从数据库组装 SKILLS.md、SKILLS.html 与中文 README.md）与仓库 AGENTS.md 模板 templates/repo-AGENTS.md。适用于「创建 skill 索引」「更新 SKILLS.md / SKILLS.html」「给 skill 仓库生成 README.md」「新增、删除或同步 skill 后重建索引」「检查索引是否最新」等场景。
---

<!--
触发示例：
- 帮我重建 ~/.agents/SKILLS.md 和 SKILLS.html
- 新增了几个 skill，更新一下全局索引（只有新的那几条要翻译）
- 某个 skill 改了，重新生成它的说明和中文译文
- 强制把所有说明和翻译重写一遍
- 检查索引是否和 skills 目录一致
- 我手工改了 SKILLS.json 里的中文，重新生成页面
- 给 yashi-skills 仓库重新生成 README.md
-->

# yashi-build-skills-index — skill 索引构建（JSON 数据库 + SKILLS.md/html + 仓库 README.md）

## 核心规则（先读）

1. **先建数据库，再出产物**：一次运行分两步——先写 JSON 数据库（`--db`，默认与 Markdown 索引同名，
   即 `~/.agents/SKILLS.json`），再渲染 Markdown 与 HTML。数据库是**唯一长期载体**，每个 skill 一条记录：
   文件清单（路径、大小、UTC 修改时间）、上游 description 及哈希、**中英文各一份**用途与适用场景、
   提示词示例快照、每个语言槽的校对状态与哈希。产物只是它的投影，`--check` 校验的就是
   「数据库与产物是否都和 skills 目录一致」。
2. **增量，不重写没变的东西**：每次运行都把每个 skill 与数据库记录比对，结果打印在 `sync:` 行：
   - `unchanged`：文件清单与 description 都没变 → 说明与译文原样复用，不重写；
   - `new`：新 skill → 源文取上一版 Markdown 索引的措辞（没有该措辞、或旧页面上对应的 description
     哈希已经和当前不一致，就按 front matter 启发式生成），其他语言标为未翻译；
   - `src-changed`：SKILL.md 的 description 变了 → 源文重新生成，译文保留但标为待校对；
   - `source-changed`：SKILL.md 变了而 description 没变 → 文案保留，译文标为待校对；
   - `files-changed`：只有 skill 目录里其他文件变了（资源、脚本、示例）→ 只报告，文案与译文不动；
   - `rebuild`：传了 `--rebuild` → 全部源文重写、全部译文标为待校对。
   译文只要被编辑过（改数据库，或改 `--emit-draft` 出的草稿），或跑过 `--mark-reviewed`，就重新变回已校对。
3. **数据不可丢**：`SKILLS.json` 是全部中英文文案的唯一长期载体，**绝对不能删**。它损坏时脚本直接报错
   退出，不会静默重建。确要重建（例如文件被删），删掉它再跑一次即可：脚本会从上一版 `SKILLS.md`
   （源语言措辞）与 `SKILLS.html`（其他语言译文）把内容恢复进新数据库，不丢人工润色。
   **数据库还不存在的首次迁移前不要删 `SKILLS.html`**，那正是这次恢复译文的来源；数据库一旦建立，
   `SKILLS.md` / `SKILLS.html` 就只是可覆盖、可删除的产物，不再是文案来源。
   恢复时每条的校对状态一并保留：已翻译仍是已翻译，待校对仍是待校对，未翻译不会被页面上的英文兜底
   文本冒充成译文；分片库借用完整索引的译文时同理。
4. **三种产物，受众和语言都不同**：
   - `SKILLS.md`（`--md-style ai`，默认）：面向 AI，纯轻量目录，每个 skill 一段（一句话 Purpose、
     若干 Use when、Location），**不复制** SKILL.md 的流程细节，不记录临时状态、项目进度或个人
     任务。默认 **英文**。
   - `SKILLS.html`：面向人类，自包含页面，按类别分组，带侧边导航、关键词搜索、锚点和“打开目录”
     链接。默认 **简体中文**。
   - 人类可读 Markdown（`--md-style human`）：面向人类，内容规则与 HTML 版一致——分类分组、
     目录、一句话用途、适用场景、提示词示例、每条的「打开目录」链接。每个 skill 仍是 `## <名称>`
     一节，标签行独占一行，提示词示例排在“位置”之后并包在 ` ```text ` 代码块里（forge 会给代码块
     一键复制）。列表前统一留一个空行。**目标文件名是 `README.md` 时自动采用本模式 + 简体中文**
     （`--md-style` / `--md-lang` 显式给出时以显式为准），可直接给 skill 仓库当首页索引；
     仓库型索引**不写模板引言**（见规则 8）。
   - 用户明确要求其他语言时，改传 `--md-lang` / `--html-lang`（内置 `en`、`zh-Hans`，另接受 `zh`、
     `zh-cn`、`english` 等别名）。需要第三种语言时，在脚本 `STRINGS` 表里补一条（标签、界面文案、
     分类名、草稿说明），并同步本节。
5. **数据库里每个语言槽各自成文，不互相翻译**：英文文案由 front matter 启发式生成（`src-changed` /
   `--rebuild` 时重写），中文文案来自人工翻译（草稿或直接编辑数据库）。两个方向的产物都只从数据库取字，
   HTML 的中文不来自 SKILLS.md。
6. **无 emoji**。
7. **不修改任何现有 SKILL.md**；不修改 AGENTS.md，除非用户明确要求。
8. **问题不猜测**：校验问题（缺 SKILL.md、front matter 缺 name 或 description、name 与目录名不一致、
   重名）一律列入报告并询问用户，不要编造用途或命名。
9. **产物可全量覆盖，人工润色不作为保护理由**：`SKILLS.md`、`SKILLS.html` 都是可再生的派生产物，
   **现有文件一律直接覆盖**，不论它来自上一次脚本运行还是人工逐条润色。覆盖前不要备份、不要手工合并、
   不要先问用户「要不要保留旧版」，也不要为了避开覆盖只跑 `--html-only`。翻译内容由数据库承载，
   覆盖产物不会丢译文。唯一需要先停下的是**校验问题**（规则 8），覆盖与否不是问点。
10. **要改文案，改数据库**：`--md-style human` 的 Markdown 不再是「可编辑文档」，它和 HTML 一样从
    数据库渲染。手工改 `SKILLS.md` / `README.md` 的文字会在下次运行时被数据库内容覆盖回去，这是预期行为。
11. **位置行会自适应**，判据只看索引相对 skills 目录的位置：
    - 索引就写在 skills 目录**里面**（仓库根目录既放 skill 又放 README.md）→ 相对路径 `yashi-x/`；
    - skills 在索引**旁边的子目录**（如 `yashi-skills/README.md` + `yashi-skills/skills/yashi-x/`）
      → `skills/yashi-x/`；
    - 其余情况（包括扫描 `~/.agents/skills` 的全局索引，即使输出到别的路径）→ 沿用
      `~/.agents/skills/<dir>/` + `file://` 链接与通用标题。
    前两种情况下标题会带上仓库名（`# 可用 Skill 索引 · yashi-skills`，取自 skills 目录的父目录），
    且**不再写模板引言**；全局索引与写到树外的索引仍带引言。标题、目录、分类标题、引言、列表前空行、
    提示词代码块都属「重排的框架」，手工改会被下次运行改回；用途 / 适用场景 / 提示词示例的文字属于内容，
    它们在数据库里。
    树内/树旁的索引用**相对链接**（`skills/yashi-x/`），所以本地与 CI 检出渲染出的文件逐字节相同；
    树外索引（例如全局 `~/.agents/SKILLS.html`）的「打开目录」是 `file:///` 本机绝对链接，换机器或换
    系统渲染就会不同——这是本机索引的固有性质，别拿它做跨机器逐字节比较。
12. **改过渲染或数据库结构后必须验证收敛**：连跑两次，第二次必须报 `already up to date` 且 `--check` 返回 0。
    数据库写回本身是幂等的（内容不变时逐字节不变），风险在渲染与回读：`parse_html_index` 仍用于首次迁移时
    从旧 `SKILLS.html` 恢复译文，新增卡片内元素时要特别检查它只从 `use-when` 列表里抓 `<li>`——卡片里其他
    `<ul>/<li>`（例如提示词示例）绝不能被当成 bullets 收进去，否则迁移会把示例并进「适用场景」。

## 推荐流程

1. 预览并校验（只读，不写任何文件）：

   `python scripts/build_skills_index.py --dry-run`

   脚本仅依赖标准库；Windows 上若 `python` 不在 PATH，改用 `py` 启动器或本机 Python 的绝对路径。
   stderr 会给出 `sync:`（增量判定）、`examples:`（示例覆盖）、`translation:`（翻译进度）三类统计。

2. 检查 stderr 的校验结果。有问题先停下，向用户报告，不写入（缺 SKILL.md、name/description 缺失、
   name 与目录名不一致、重名）。

3. 正式生成：`python scripts/build_skills_index.py`。**已存在的数据库、md、html 直接覆盖，不需要先征求
   同意**（规则 9）。首次运行时 stderr 会有一行 `seed: N source and M localized ...`，表示旧版
   `SKILLS.md` 的措辞与 `SKILLS.html` 的译文已迁进新数据库；这正是老索引升级到数据库格式的过程。
   若仓库装了本 skill 的 Action（见下文「装进 GitHub 仓库」），这一步只更新数据库并打印
   `renders this index`，产物由 CI 负责；要本地连产物一起生成就加 `--ignore-ci`。
4. 看 `sync:` 行决定下一步：
   - 全是 `unchanged` → 没有任何说明或译文需要动，收工；
   - 有 `new` → 新 skill 需要翻译（见第 5 步）；
   - 有 `src-changed` / `source-changed` / `files-changed` → 去读该 skill 的 SKILL.md，判断
     `--emit-draft` 草稿里对应的中文是否仍然准确，需要改就改（改完自动变回已校对）；
   - 有 `rebuild`（只有 `--rebuild` 才会出现）→ 全部条目都要重新过一遍。
5. **翻译或校对中文**（两种做法，任选）：
   - **改草稿**：`python scripts/build_skills_index.py --emit-draft` 生成临时草稿（默认
     `<系统临时目录>/yashi-skills-index/SKILLS.zh.md`），编辑其中的中文后重跑一次主流程，草稿会自动
     合并进数据库并删除。已译条目会带出旧译文，只有标了 `<!-- status: ... -->` 的才需要处理。
   - **改数据库**：直接编辑 `SKILLS.json` 里该 skill 的 `locales.zh-Hans`（`purpose` / `use_when`）。
     脚本发现文本与记录的哈希不一致，就认为有人校对过，自动把该槽置回 `ok`。
6. **只想确认「译文不用改」**：跑 `python scripts/build_skills_index.py --mark-reviewed`，把所有待校对的
   条目确认掉，不改动任何文字。
7. **要强制重写全部说明与翻译**：跑 `python scripts/build_skills_index.py --rebuild`
   （全部源文按 front matter 重新生成，全部译文标为待校对）。
8. 核对：条目数与 skill 目录数一致；`translation:` 行里 `need review` 和 `untranslated` 均为 0，
   `examples:` 行应为 `all N skills have example prompts`（若列出缺示例的 skill，去
   `scripts/assets/skill-examples.json` 补齐；扫描全局目录时列出的过期键要清理；扫描仓库或子集时提示的
   「属于本索引之外的 skill」是共用示例文件的正常现象）。最后跑一次 `--check` 收尾。
9. **改过渲染结构后必须验证收敛**：连跑两次，第二次必须报 `already up to date`，`--check` 返回 0。

## 给 skill 仓库生成 README.md（人类可读索引）

仓库把 skill 放在自己的 `skills/` 子目录（例如 `yashi-skills/skills/yashi-*/`）时，一条命令生成首页索引：

```bash
python scripts/build_skills_index.py --skills-dir /path/to/repo/skills \
    --include 'yashi-*' --output /path/to/repo/README.md --no-html
```

- `--skills-dir` 指到**放 skill 的那个目录**（本仓库是 `skills/`），`--output` 指到仓库根目录的
  `README.md`；两者一起决定条目里的「位置」写成 `skills/yashi-x/`（见核心规则 11）；
- `--output` 同时决定**文件名和位置**，指到 `README.md` 时自动启用 `--md-style human` +
  `--md-lang zh-Hans`（stderr 会打印 `note: README.md is a human-facing index; ...`）；
- 只给 `--output` 而不给 `--html` 时，HTML 写到**同目录同名的 `.html`**，不会再落到全局
  `~/.agents/SKILLS.html`；只要 md 就加 `--no-html`；数据库写到同目录同名的 `.json`（即
  `README.json`），它是这个仓库索引自己的记录，与全局 `SKILLS.json` 无关；
- `--include 'yashi-*'` 让索引只收这一批 skill；即使不加，skills 目录里的 `README.md`、`.gitignore`、
  `.git/` 也不会被当成 skill（`discover_skills` 跳过点开头目录）；
- 提示词示例来自脚本自带的 `scripts/assets/skill-examples.json`（与全局索引共用同一份），在数据库里
  只作快照，**维护请改那个 JSON 文件**；示例只出现在人类可读 md 与 HTML 里，不进 AI md（md 侧渲染成
  ` ```text ` 代码块，HTML 侧是点击即复制的按钮）；
- 仓库型索引**不写模板引言**，标题直接带仓库名；列表前留一个空行；
- 手工润色数据库里的 Purpose / 适用场景后重跑仍收敛（见核心规则 10、2）；但不要改结构：标题、引言、
  目录、分类标题、列表前空行、提示词代码块都由脚本重排。

## 装进 GitHub 仓库（Action + AGENTS.md）

仓库里放 skill 时（例如 yashi-skills 的 `skills/` 子目录），把「生成」这件事交给 CI，本地就只维护数据库：

1. 复制 `templates/skills-index.yml` 到仓库的 `.github/workflows/skills-index.yml`；
2. 复制 `templates/repo-AGENTS.md` 到仓库根，**改名为 `AGENTS.md`**；
3. 建库一次（在仓库里跑普通流程并加 `--ignore-ci`，同时用 `--collection` 定下集合名）：

   ```bash
   python skills/yashi-build-skills-index/scripts/build_skills_index.py \
       --skills-dir skills --collection <仓库名> --ignore-ci \
       --output SKILLS.md --html SKILLS.html --db SKILLS.json
   python skills/yashi-build-skills-index/scripts/build_skills_index.py \
       --render-only --skills-dir skills --db SKILLS.json \
       --output README.md --no-html
   ```

4. 把 `SKILLS.json`（以及首次生成的 `SKILLS.md` / `SKILLS.html` / `README.md`）提交进仓库——
   `SKILLS.json` 是 CI 唯一的输入，也是唯一由 AI 维护的文件。

之后的分工是：

- **AI 只改数据库**：用本 skill 更新 `SKILLS.json` 里的字段（说明、翻译、示例快照、文件清单）。
  脚本扫描到仓库里有那个 workflow 时，普通运行会打印 `note: ... renders this index`，
  然后**只写数据库**，不再碰 `SKILLS.md` / `SKILLS.html` / `README.md`；显式传 `--db-only` 等效，
  `--ignore-ci` 则强制连产物一起生成（本地预览用）。
- **CI 只渲染产物**：workflow 用 `--render-only` 从数据库组装
  `SKILLS.md`（英文、AI 索引）、`SKILLS.html`（简体中文、人类页面）与 `README.md`（简体中文、人类索引），
  有变化就提交回去。`--render-only` 不扫描 skill 目录、不写数据库，数据库不存在时直接报错。
- 所以三个产物**永远不要手工编辑**：它们每次都会被脚本整份覆盖，改文案要改数据库（核心规则 10）。

## 脚本说明

- 默认扫描 `~/.agents/skills`；默认产物为 `~/.agents/SKILLS.json`（数据库）、`~/.agents/SKILLS.md`、
  `~/.agents/SKILLS.html`；可用 `--skills-dir`、`--db`、`--output`、`--html` 覆盖（都是「文件名 + 路径」）。
  `--db` 默认跟随 `--output`：`SKILLS.md` → `SKILLS.json`，`README.md` → `README.json`；
  `--render-only` 且未给 `--skills-dir` 时，沿用数据库里记录的目录。
- **默认语言与定位**：`SKILLS.md` 用英文、`--md-style ai`，只放 AI 需要的骨架（名称、一句话用途、
  适用场景、位置），不放示例；`SKILLS.html` 用简体中文，含用途、适用场景、提示词示例、位置链接与搜索，
  是给人看的完整页面。两者都从数据库渲染，改文案改数据库。
- `--md-style ai|human`：Markdown 风格，默认 `ai`；目标文件名是 `README.md` 时默认 `human`（规则 4、11）。
- `--dry-run`：渲染 Markdown 到标准输出，不写任何文件（含数据库）。
- `--html-only`：跳过 Markdown 产物，只更新数据库与 HTML（用于改完文案后刷新页面）。与 `--no-html`
  同时给出会直接报错。
- `--no-html`：不生成 HTML。
- `--md-lang` / `--html-lang`：输出语言，默认 `en` / `zh-Hans`（目标文件名是 `README.md` 时 md 侧默认
  `zh-Hans`）；不支持的语言直接报错退出。两个产物各自从数据库里对应语言的槽取字。
- `--emit-draft`：只写翻译草稿并退出，不碰数据库与产物。
- `--draft-path`：草稿路径（默认 `<系统临时目录>/yashi-skills-index/<索引名>.<语言>.md`）；
  `--keep-draft`：合并进数据库后保留草稿。**草稿是「待应用的翻译」**：只要它存在，任何一次非
  `--emit-draft` 的运行都会把它并进数据库并删除它，不会先问；不想应用就先删掉它。默认路径只由索引
  文件名和语言派生，所以同名索引（例如全局 `SKILLS` 与另一个也写到 `SKILLS.*` 的索引）会共用同一个
  草稿位置，需要隔离时显式传 `--draft-path`。
- `--rebuild`：忽略增量比对，重写全部源语言说明并把全部译文标为待校对。
- `--mark-reviewed`：把当前所有待校对条目确认为已校对（不改文字）。
- `--collection NAME`：人类可读索引标题里的集合名（通常就是仓库名）。默认先用数据库里记录的值，
  其次用 skills 目录的父目录名。**建库时定一次**，这样本地目录名（例如 submodule 路径 `.agents`）
  与 CI 检出目录名（例如 `yashi-skills`）不同，也能渲染出逐字节相同的文件。
- `--db-only`：只更新数据库、不生成产物。仓库装了 Action 时，普通运行已自动是这个行为。
- `--render-only`：不扫描 skill 目录、不写数据库，只从现有数据库渲染产物（CI 用）；数据库不存在时
  退出 1，`--skills-dir` 省略时改用数据库里记录的目录，`--include` 照样生效（只渲染匹配的记录）。
  与 `--emit-draft` 互斥。
- `--ignore-ci`：不查找渲染本索引的 workflow，即使仓库装了 Action 也照常写出产物（本地预览用）。
  查找范围是输出目录与 skills 目录各自所在的仓库（向上遇到 `.git` 即停，最多几层），且只认非注释
  内容——只在注释里提到本脚本的 lint workflow 不会误触发。
- `--check`：不写入；校验问题返回码 2，数据库或产物缺失/不一致返回码 3，一致返回 0，可用于定时任务或钩子。
  未翻译条目只在报告中提示，不影响返回码。
- 内置最小 YAML front-matter 解析，支持引号标量与 `>` / `|` 块标量（含中文标点与 CJK 折叠），无需安装
  PyYAML。
- **数据库结构**：顶层是 `schema` / `generated_at` / `skills_dir` / `outputs` / `exclude` / `stats` /
  `skills` / `_doc`；每个 skill 记录含 `dir`、`name`、`category`、`description`、`description_hash`、
  `files`（路径 + 大小 + UTC mtime）、`files_hash`、`source_stamp`（SKILL.md 的 size:mtime）、`prompts`、
  `locales`（`en` 与 `zh-Hans`，各含 `purpose` / `use_when` / `status` / `src_hash` / `source_stamp` /
  `text_hash`）。`_doc` 字段里也写了同样一份说明，方便打开文件时对照。
- **文件清单排除缓存**：`__pycache__`、`.git`、`node_modules`、`.venv` 等目录，以及 `*.pyc`、`*.tmp`、
  `*.log` 等文件不计入 `files_hash`，否则跑一次脚本就会把所有 skill 判成变更。
- **变更判定依据**：每个文件的路径、字节数与 UTC 修改时间（微秒精度），所以同一秒内的两次写入也能区分。
  唯一检不出的情况是同步工具刻意复原了时间戳（`tar -x`、`rsync -a` 之类）且文件大小恰好不变——真遇到这种
  漏检，用 `--rebuild` 强制重写，或删掉 `SKILLS.json` 让它重新迁移。SKILL.md 的 description 另有哈希兜底，
  description 一改必定判成 `src-changed`。
- 页面类别固定为 设计界面 / Dart / Flutter / Go / 思维导图 / 个人工具 六组，由 skill 名前缀判定；新增类别需
  同时改脚本的 `category_of` 与 `STRINGS[...]["categories"]`。
- 脚本在 Windows 下强制以 UTF-8 写 stdout/stderr，避免控制台代码页导致 CJK 或 `↔` 等字符编码失败。
- **页面样式与脚本是独立文件**：`scripts/assets/skills-index.css` 和 `scripts/assets/skills-index.js`。
  脚本在渲染时读取它们并内联进页面，所以产物仍是零外部请求的单文件。**要改外观就改这两个文件**；
  文件缺失会直接报错退出。
- **配色是粉白蓝马卡龙，含浅色/深色两套 token**，侧栏有「自动 / 浅色 / 深色」切换：`<html>` 上写
  `data-theme`（`light`/`dark`），选择记在 `localStorage`，默认「自动」跟随系统。主题脚本内联在 `<head>`
  且同步执行，避免首屏闪色。新增语言时要在 `STRINGS` 里补 `theme_switch_aria`、`theme_auto`、
  `theme_light`、`theme_dark` 四个键。
- **提示词示例**：数据在 `scripts/assets/skill-examples.json`，手工维护——键是 skill 目录名，值是该 skill 的
  中文提示词数组（`_` 开头的键是文档，加载时忽略）。数据库里的 `prompts` 是它的快照。渲染时会为 HTML
  每张卡片、以及人类可读 md 的每个 skill 加一个「提示词示例」区块（HTML 里每条点击即复制）；
  **示例文本同时并入卡片与侧栏的搜索索引**。每次生成都会在 stderr 报告覆盖情况：缺示例的 skill；扫描的
  正是 `~/.agents/skills` 时，还会报出匹配不到任何 skill 的过期键（需要清理）。扫描仓库目录或子集时，
  未匹配的键属于本索引之外的 skill，只作信息性提示。
- 示例不进 AI `SKILLS.md`——后者是给 AI 读的轻量目录，加示例会让它变重。
- 人类可读 md **不要**手工改标签行（用途 / 适用场景 / 位置）或把提示词示例插到“位置”之前：结构由脚本重排，
  文案请改数据库（规则 10）。
- **`--include <GLOB>` 只索引一部分 skill**（按目录名匹配 glob；可重复，也接受逗号分隔）：
  `--include "yashi-*"` 或 `--include "dart-*,flutter-*"`。默认全部。
  - **分片运行写自己的文件**，不会碰完整索引：`SKILLS.<tag>.md` / `SKILLS.<tag>.html` /
    `SKILLS.<tag>.json`，tag 由模式生成（`yashi-*` → `yashi`）。只有显式把 `--output` / `--html` /
    `--db` 指到完整索引路径时才会覆盖它；此时若那个索引本来就列着本次没扫到的 skill，会打印警告说明
    将丢掉多少条（数据库侧不会丢：本次没扫到的记录原样保留）。
  - 分片数据库首次生成时会从完整索引的数据库或页面**借用译文**（stderr 里的 `seed:` 行），所以第一次
    生成就是中文，不需要重新翻译；借来的译文带着它自己的校对状态（待校对仍是待校对，未翻译不会被
    英文兜底文本冒充）。借用只在分片数据库为空时发生，分片库建立之后新匹配进来的 skill 不会自动补译文，
    它会显示为待翻译——用 `--emit-draft` 翻译，或从完整索引复制那一条。
  - 空匹配直接报错退出 1。对分片输出跑 `--check` 时要带上同样的 `--include`。
- 改配色必须复核对比度：深浅两套主题里，正文/次要文字/主色/链接/计数/告警这些“墨色” token 在各自底色上
  都已按 WCAG AA 的 4.5:1 校验过。可访问的设计做法是让**底色**保持马卡龙淡彩、让**墨色**足够深。

## 与其他 skill 的配合

- 在 `yashi-global-skills-sync` 同步（增删改）skill 之后运行本 skill 重建索引。同步通常会改动上游
  description，此时 `sync:` 行会把受影响的条目标为 `src-changed`，只需校对这些条目的中文，其余条目自动复用。
- yashi-skills 仓库（`kagurazakayashi/yashi-skills`，也是 `yashi-*` skill 的更新源）用它生成仓库首页
  `README.md`：skill 放在该仓库的 `skills/` 子目录，命令见上文「给 skill 仓库生成 README.md」。
