---
name: yashi-build-skills-index
description: 生成或更新 skill 索引，产出三份产物：面向 AI 读取的 ~/.agents/SKILLS.md（默认英文的轻量 Markdown 目录）、面向人类阅读的 ~/.agents/SKILLS.html（默认简体中文的自包含页面，分类分组、侧边导航、关键词搜索、锚点、目录链接），以及**面向人类阅读的 Markdown**（--md-style human：分类分组、目录、一句话用途、适用场景、提示词示例、每条「打开目录」链接，内容规则与 HTML 版一致）；把 --output 指到名为 README.md 的文件时会自动切到人类可读模式 + 简体中文，可直接给 skill 仓库生成首页索引。遍历 skills 目录下所有 skill，解析各自 SKILL.md 的 YAML front matter（name、description），按固定模板渲染，并校验缺失 SKILL.md、name/description 缺失或用途无法判断、name 与目录名不一致、skill 重名等问题；发现问题不猜测，列出并询问用户。附跨平台、仅标准库的 Python 脚本 scripts/build_skills_index.py，支持 --dry-run、--check、--force、--md-style、--html-only、--no-html、--output、--html、--skills-dir、--include、--md-lang、--html-lang、--emit-draft、--draft-path、--keep-draft；中文译文通过临时草稿提交（HTML 侧），已译条目在重建时自动复用，只有新增或上游变更的 skill 需要重新翻译。适用于「创建 skill 索引」「更新 SKILLS.md / SKILLS.html」「给 skill 仓库生成 README.md」「新增、删除或同步 skill 后重建索引」「检查索引是否最新」等场景。
---

<!--
触发示例：
- 帮我重建 ~/.agents/SKILLS.md 和 SKILLS.html
- 新增了几个 skill，更新一下全局索引
- 我手工润色了 SKILLS.md，重新生成 SKILLS.html
- 检查索引是否和 skills 目录一致
- 直接覆盖重建，不用管旧的 SKILLS.md / SKILLS.html
- 给 yashi-skills 仓库重新生成 README.md
-->

# yashi-build-skills-index — skill 索引构建（全局 SKILLS.md/html + 仓库 README.md）

## 核心规则（先读）

1. **三种产物，受众和语言都不同**：
   - `SKILLS.md`（`--md-style ai`，默认）：面向 AI，纯轻量目录，每个 skill 一段（一句话 Purpose、
     若干 Use when、Location），**不复制** SKILL.md 的流程细节，不记录临时状态、项目进度或个人
     任务。默认 **英文**。
   - `SKILLS.html`：面向人类，自包含页面，按类别分组，带侧边导航、关键词搜索、锚点和“打开目录”
     链接。默认 **简体中文**。
   - 人类可读 Markdown（`--md-style human`）：面向人类，内容规则与 HTML 版一致——分类分组、
     目录、一句话用途、适用场景、提示词示例、每条的「打开目录」链接。每个 skill 仍是 `## <名称>`
     一节，标签行独占一行（这是回读格式，别改成行内），提示词示例排在“位置”之后，并包在
     ` ```text ` 代码块里（forge 会给代码块一键复制，最接近页面卡片的点击即复制）。
     列表前统一留一个空行（Markdown 格式化器的预期写法，也让手工格式化过的文件仍能收敛）。
     **目标文件名是 `README.md` 时自动采用本模式 + 简体中文**（`--md-style` / `--md-lang`
     显式给出时以显式为准），可直接给 skill 仓库当首页索引；仓库型索引**不写模板引言**（见规则 7）。
   - 用户明确要求其他语言时，改传 `--md-lang` / `--html-lang`（内置 `en`、`zh-Hans`，另接受 `zh`、`zh-cn`、`english` 等别名）。需要第三种语言时，在脚本 `STRINGS` 表里补一条（标签、界面文案、分类名、草稿说明），并同步本节。
2. **两侧正文各自成文，不互相翻译**：HTML 的中文不来自 SKILLS.md，而来自「临时翻译草稿 + 上一版 SKILLS.html 里已有的中文」。每条译文是否可复用，由 `data-src`（该 skill 的 SKILL.md description 哈希）决定：上游 description 未变就复用，变了就标为待校对。
3. **无 emoji**。
4. **不修改任何现有 SKILL.md**；不修改 AGENTS.md，除非用户明确要求。
5. **问题不猜测**：校验问题一律列入报告并询问用户，不要编造用途或命名。
6. **产物可全量覆盖，人工润色不作为保护理由**：`SKILLS.md` 与 `SKILLS.html` 都是可再生的派生产物，**现有文件一律直接覆盖**——不论它是上一次脚本生成的，还是人工逐条润色过的。覆盖前不要备份、不要手工把新条目并进旧文件、不要先问用户「要不要保留旧版」、也不要为了避开覆盖而只跑 `--html-only`。按本 skill 的规则直接跑完整流程即可。
   - 覆盖 `SKILLS.md` 不会连带丢掉 HTML 侧的中文：译文复用由 `data-src`（该 skill 的 SKILL.md description 哈希）决定，与 SKILLS.md 的措辞无关，所以整跑一遍仍然只把上游表述变过的条目标为 `stale`。
   - 第 4 步的润色是「生成之后再润色」的可选后续动作，不属于必须保住的状态；下次重建会覆盖它，这是预期行为。
   - **更新前可以直接删除 `SKILLS.md`，并推荐这么做**：它的内容只由 skills 目录决定，是纯派生物，删掉后本次运行即收敛——实测只留 html、md 不存在时跑一次，产出的页面与收敛态逐字节相同。若不删而直接覆盖一份润色过的 md，则要跑两遍：html 的源措辞取自 md，第一次跑出来的页面仍是旧 md 的措辞，`--check` 会报 **html** 过期（不是 md），再跑一次才返回 0。这个中间态是预期行为，不是失败，也不要回头去恢复润色版。
   - **`SKILLS.html` 绝对不能删**：它是全部中文译文的唯一长期载体。删掉后所有条目都会退回未翻译状态（实测：html 不存在时 `--emit-draft` 报告 `0 localized, 88 untranslated`），等于丢掉全部翻译工作。它只能被覆盖，不能被删除。翻译草稿（`<系统临时目录>/yashi-skills-index/SKILLS.zh.md`）是临时文件，可留可删，渲染后会自动删除。
   - 唯一需要先停下的是**校验问题**（见规则 5）：只有在缺 SKILL.md、name/description 缺失或重名这类情况下才向用户报告并等待，覆盖与否不是问点。
7. **人类可读 md 是「可编辑的文档」，AI md 是「纯派生物」**：
   - `--md-style human` 的正文取自**上一版该文件自己的措辞**（回读 `parse_markdown_index`，文件还
     不存在或某条缺失时回落到 front matter），所以人工润色过的 Purpose / 适用场景在重建后**会保留**，
     连跑两次即收敛、`--check` 返回 0；只有结构（标题、目录、分类标题、示例区块）由脚本重排。
   - `--md-style ai` 一律按 front matter 启发式重渲染（核心规则 6 的“纯派生物”），润色会被覆盖，
     这是预期行为。
   - 人类可读 md 的“位置”行会自适应，判据只看索引相对 skills 目录的位置：
     - 索引就写在 skills 目录**里面**（仓库根目录既放 skill 又放 README.md）→ 相对路径 `yashi-x/`；
     - skills 在索引**旁边的子目录**（如 `yashi-skills/README.md` + `yashi-skills/skills/yashi-x/`）
       → `skills/yashi-x/`；
     - 其余情况（包括扫描 `~/.agents/skills` 的全局索引，即使输出到别的路径）→ 沿用
       `~/.agents/skills/<dir>/` + `file://` 链接与通用标题。
     前两种情况下标题会带上仓库名（`# 可用 Skill 索引 · yashi-skills`，取自 skills 目录的父目录），
     且**不再写模板引言**——标题已经点明这是哪个集合，仓库首页不需要「本文件是……索引」这类样板；
     全局索引与写到树外的索引仍带引言。标题、目录、分类标题、引言、列表前空行、提示词代码块都属
     「重排的框架」，手工改会被下次运行改回；只有用途 / 适用场景 / 提示词示例文字是内容。

## 推荐流程

1. 预览并校验（只读）：

   `python scripts/build_skills_index.py --dry-run`

   脚本仅依赖标准库；Windows 上若 `python` 不在 PATH，改用 `py` 启动器或本机 Python 的绝对路径。stderr 会给出 `translation: N localized, M need review, K untranslated` 统计。

2. 检查 stderr 的校验结果。有问题先停下，向用户报告，不写入：
   - skill 目录缺少 SKILL.md
   - front matter 缺少 name 或 description，或 description 无法判断用途
   - front-matter name 与目录名不一致
   - 多个 skill 使用相同的 name
3. （可选，推荐）先删掉 `SKILLS.md`：它是纯派生物，删掉后本次运行即收敛，免去"再跑一遍"的过渡态。**不要删 `SKILLS.html`**（理由见核心规则 6）。然后正式生成 md + html：`python scripts/build_skills_index.py`。**已存在的 md/html 直接覆盖，即使是人工润色过的版本也照覆盖，不需要先征求同意**（见核心规则 6），不要备份、也不要手工把新条目并进旧文件。问题存在但确需保留部分索引时，加 `--force`。首次运行 HTML 里全是英文回退文案，属正常，按下面第 5 步翻译。
4. **逐段润色 SKILLS.md 草稿**（英文，启发式生成，需要人工过一遍）：
   - Purpose：一句话，动词开头，说清“做什么”。
   - Use when：保留 2–4 条最能触发该 skill 的具体场景；“→ See ... skill”这类跨 skill 引用不作为条目；“Not for ...”否定说明最多保留 1–2 条。
   - 删除 `[Add ... from SKILL.md]` 占位符；信息不足时打开对应 SKILL.md 正文补全。
5. **翻译 HTML 侧的中文草稿**：
   - 运行 `python scripts/build_skills_index.py --emit-draft` 生成临时草稿（默认 `<系统临时目录>/yashi-skills-index/SKILLS.zh.md`）。已译条目会自动带出旧译文，只有标了 `<!-- status: ... -->` 的条目需要处理：`stale` = 上游 SKILL.md 已变更，需校对译文；`untranslated` = 尚未翻译。
   - 直接编辑该草稿：把源文改成简体中文，改完删掉该条目的 status 注释。条数多时**可以分批翻译**：已译部分在下次 `--emit-draft` 时仍会带出，未译部分继续保留英文源文，不会丢。
   - 草稿是临时文件，渲染 HTML 后自动删除（`--keep-draft` 可保留）；译文的长期载体是 SKILLS.html 本身。
6. 渲染中文 HTML：`python scripts/build_skills_index.py --html-only`。不改动 SKILLS.md，合并「当前 md 措辞 + 草稿译文 + 上一版页面的中文」，然后删除草稿。只有在**第 4、5 步确有改动**（润色了 md，或翻译了草稿）时才需要单独跑它；没有这两类改动就直接走第 3 步的完整流程，不必先跑完整流程再补一次 `--html-only`。
7. 核对：条目数与 skill 目录数一致；stderr 报告里 `need review` 和 `untranslated` 均为 0，`examples` 行应为 `all N skills have example prompts`（若列出缺示例的 skill，去 `skill-examples.json` 补齐；在全局目录下跑出「没有对应 skill」的过期键要清理；扫描仓库或子集时提示的「属于本索引之外的 skill」是共用示例文件的正常现象）。最后跑一次 `--check` 收尾；如果在第 3 步覆盖掉的是人工润色过的 md，页面可能要再跑一遍才一致（见核心规则 6），按提示重跑即可。
8. **改过渲染结构后必须验证收敛**：连跑两次，第二次必须报 `already up to date` 且 `--check` 返回 0。渲染时会回读上一版 HTML（`parse_html_index`）来复用译文，所以只要新加的标记和回读规则打架，页面就会**每跑一次胖一圈**、`--check` 永远不过——而且全程不报错，只会静默膨胀（曾把 159 KB 撑到 1.08 MB）。新增卡片内元素时特别检查：`parse_html_index` 只应从 `use-when` 列表里抓 `<li>`，卡片里其他 `<ul>/<li>`（例如提示词示例）绝不能被当成 bullets 收进去。人类可读 md 走的是同一条回读链路（`parse_markdown_index`），同样要连跑两次验证：第二次必须 `already up to date`、`--check` 返回 0。

## 给 skill 仓库生成 README.md（人类可读索引）

仓库把 skill 放在自己的 `skills/` 子目录（例如 `yashi-skills/skills/yashi-*/`）时，一条命令生成首页索引：

```bash
python scripts/build_skills_index.py --skills-dir /path/to/repo/skills \
    --include 'yashi-*' --output /path/to/repo/README.md --no-html
```

- `--skills-dir` 指到**放 skill 的那个目录**（本仓库是 `skills/`），`--output` 指到仓库根目录的
  `README.md`；两者一起决定条目里的「位置」写成 `skills/yashi-x/`（见核心规则 7）；
- `--output` 同时决定**文件名和位置**，指到 `README.md` 时自动启用 `--md-style human` + `--md-lang
  zh-Hans`（stderr 会打印 `note: README.md is a human-facing index; ...`）；
- 只给 `--output` 而不给 `--html` 时，HTML 写到**同目录同名的 `.html`**，不会再落到全局
  `~/.agents/SKILLS.html`；只要 md 就加 `--no-html`；
- `--include 'yashi-*'` 让索引只收这一批 skill；即使不加，skills 目录里的 `README.md`、`.gitignore`、
  `.git/` 也不会被当成 skill（`discover_skills` 跳过点开头目录）；
- 提示词示例来自脚本自带的 `scripts/assets/skill-examples.json`（与全局索引共用同一份），只出现在
  人类可读 md 与 HTML 里，不进 AI md；md 侧渲染成 ` ```text ` 代码块（forge 提供一键复制），HTML 侧
  是点击即复制的按钮；stderr 里“属于本索引之外的 skill”的键是共用示例文件的正常现象；
- 仓库型索引**不写模板引言**，标题直接带仓库名；列表前留一个空行；
- 手工润色 README.md 的 Purpose / 适用场景后重跑仍收敛（见核心规则 7）；但不要改结构：标题、引言、
  目录、分类标题、列表前空行、提示词代码块都由脚本重排。

## 脚本说明

- 默认扫描 `~/.agents/skills`；输出 `~/.agents/SKILLS.md` 与 `~/.agents/SKILLS.html`；可用 `--skills-dir`、`--output`、`--html` 覆盖（两者都是「文件名 + 路径」）。
- `--md-style ai|human`：Markdown 风格，默认 `ai`；目标文件名是 `README.md` 时默认 `human`（见核心规则 1、7）。
- `--dry-run`：渲染 Markdown 到标准输出，不写文件。
- `--html-only`：不改动 SKILLS.md，仅重建 SKILLS.html（用于润色或翻译之后）。与 `--no-html` 同时给出会直接报错。
- `--no-html`：只生成 Markdown（此时不做 HTML 播种，也不会去读 `~/.agents/SKILLS.html`）。
- `--md-lang` / `--html-lang`：输出语言，默认 `en` / `zh-Hans`（目标文件名是 `README.md` 时 md 侧默认 `zh-Hans`）；不支持的语言直接报错退出。
- `--emit-draft`：只写翻译草稿并退出，不碰任何输出文件。
- `--draft-path`：草稿路径（默认系统临时目录）；`--keep-draft`：渲染后保留草稿。
- `--check`：不写入；校验问题返回码 2，索引缺失或不一致返回码 3，一致返回 0，可用于定时任务或钩子。注意：手工润色过的 md 与脚本启发式渲染必然不同，此时 md 项会报过期——按核心规则 6 直接重跑完整流程覆盖它即可，不要把它当成「需要保住的东西」，也不要为此改用 `--html-only` 绕过；html 项校验的是“是否与当前 md + 草稿 + 既有页面中文一致”。未翻译条目只在报告中提示，不影响返回码。
- 内置最小 YAML front-matter 解析，支持引号标量与 `>` / `|` 块标量（含中文标点与 CJK 折叠），无需安装 PyYAML。
- 页面类别固定为 设计界面 / Dart / Flutter / Go / 思维导图 / 个人工具 六组，由 skill 名前缀判定；新增类别需同时改脚本的 `category_of` 与 `STRINGS[...]["categories"]`。
- 脚本在 Windows 下强制以 UTF-8 写 stdout/stderr，避免控制台代码页导致 CJK 或 `↔` 等字符编码失败。
- **页面样式与脚本是独立文件**：`scripts/assets/skills-index.css` 和 `scripts/assets/skills-index.js`。脚本在渲染时读取它们并内联进页面，所以产物仍是零外部请求的单文件。**要改外观就改这两个文件**，脚本里已经没有内联的 css/js 字符串了；文件缺失会直接报错退出。
- **配色是粉白蓝马卡龙，含浅色/深色两套 token**，侧栏有「自动 / 浅色 / 深色」切换：`<html>` 上写 `data-theme`（`light`/`dark`），选择记在 `localStorage`，默认「自动」跟随系统。主题脚本内联在 `<head>` 且同步执行，避免首屏闪色。新增语言时要在 `STRINGS` 里补 `theme_switch_aria`、`theme_auto`、`theme_light`、`theme_dark` 四个键。
- **提示词示例**：数据在 `scripts/assets/skill-examples.json`，手工维护——键是 skill 目录名，值是该 skill 的中文提示词数组（`_` 开头的键是文档，加载时忽略）。渲染时会为 HTML 每张卡片、以及人类可读 md 的每个 skill 加一个「提示词示例」区块（HTML 里每条点击即复制）；**示例文本同时并入卡片与侧栏的搜索索引**，所以搜只在示例里出现的词也能定位到对应 skill。每次生成都会在 stderr 报告覆盖情况：缺示例的 skill；扫描的正是 `~/.agents/skills` 时，还会报出匹配不到任何 skill 的过期键（需要清理）。扫描的是仓库目录或子集时，未匹配的键属于本索引之外的 skill（共用同一份示例文件），只作信息性提示。
- 示例出现在 HTML 与**人类可读 md**里，不进 AI `SKILLS.md`——后者是给 AI 读的轻量目录，加示例会让它变重。
- 人类可读 md **不要**把标签行（用途 / 适用场景 / 位置）改成行内写法，也不要把提示词示例插到“位置”之前：`parse_markdown_index` 按“标签独占一行 + 段落顺序”回读，改了就每跑一次胖一圈且 `--check` 永远不过（见推荐流程第 8 步）。提示词示例排在最末的 ` ```text ` 代码块里，回读时不参与解析，所以块内文字可以自由改。
- **`--include <GLOB>` 只索引一部分 skill**（按目录名匹配 glob；可重复，也接受逗号分隔）：`--include "yashi-*"` 或 `--include "dart-*,flutter-*"`。默认全部。
  - **分片运行写自己的文件**，不会碰完整索引：`SKILLS.<tag>.md` / `SKILLS.<tag>.html`，tag 由模式生成（`yashi-*` → `yashi`；`dart-*,flutter-*` → `dart-flutter`）。只有显式把 `--output` / `--html` 指到完整索引路径时才会覆盖它，那种情况会打印警告。
  - 分片页面会从完整索引 `SKILLS.html` **借用译文**（stderr 里的 `seed:` 行），所以第一次生成就是中文，不需要重新翻译。
  - 空匹配直接报错退出 1。对分片输出跑 `--check` 时要带上同样的 `--include`。
- 改配色必须复核对比度：深浅两套主题里，正文/次要文字/主色/链接/计数/告警这些"墨色" token 在各自底色上都已按 WCAG AA 的 4.5:1 校验过。可访问的设计做法是让**底色**保持马卡龙淡彩、让**墨色**足够深，不要把淡彩直接当正文色。

## 与其他 skill 的配合

- 在 `yashi-global-skills-sync` 同步（增删改）skill 之后运行本 skill 重建索引。同步通常会改动上游 description，此时 `--emit-draft` 会把受影响的条目标为 `stale`，只需校对这几条译文，其余条目自动复用。
- yashi-skills 仓库（`kagurazakayashi/yashi-skills`，也是 `yashi-*` skill 的更新源）用它生成仓库首页 `README.md`：skill 放在该仓库的 `skills/` 子目录，命令见上文「给 skill 仓库生成 README.md」。
