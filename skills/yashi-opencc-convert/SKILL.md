---
name: yashi-opencc-convert
display_name: 中文简繁转换
description: >-
  使用 OpenCC 进行中文简繁转换，支持简体↔繁体、台湾/香港繁体变体、日本新字体。
  默认自动检测方向（含繁体字则转简体，全简体则转繁体），也可显式指定转换集。
  适用于 .md 文件、代码注释、对话内容中的简繁转换需求。当用户说"简繁转换"
  "简体转繁体""繁体转简体""转成台湾繁体""转成香港繁体""把这段转成繁体/简体"
  或需要统一文本简繁风格时，调用本技能。
---

# 中文简繁转换

使用 OpenCC 对中文文本做简繁转换，默认自动检测方向，支持台湾/香港变体与日本新字体。

## 工具位置

- 脚本（随本技能分发，Git 版本管理）：`scripts/opencc_convert.py`，与本 SKILL.md 同级；
  请按「本 SKILL.md 所在目录」拼路径，**不要假设当前工作目录**。
- 两处副本内容一致，任选其一：`~/.agents/skills/yashi-opencc-convert/` 或本仓库 `.agents/skills/yashi-opencc-convert/`。
- Python：用 PATH 里的 `python`。
- 依赖：`opencc-python-reimplemented`（脚本 `import opencc`），缺了先
  `python -m pip install opencc-python-reimplemented`。
  > 脚本用的是 **python 包**，不是系统里的 `opencc.exe` 命令行。
- 环境自检：`python <本技能目录>/scripts/opencc_convert.py --check`
  依次检查 Python、`opencc` 包、关键词典与示例转换；不确定环境时先跑这一步，
  不要等转换到一半才发现问题。

## 调用方式

以下命令在**仓库根目录或用户主目录**下运行（两处都有 `.agents/skills/`，同样可用）；
也可 cd 进 `scripts/` 后用脚本名直接调用。

```bash
# 就地改写文件（最常用）
python .agents/skills/yashi-opencc-convert/scripts/opencc_convert.py -w 文件路径

# heredoc 管道粘贴文本 → 结果输出到 stdout（结束标记 EOF 须顶格）
python .agents/skills/yashi-opencc-convert/scripts/opencc_convert.py <<'EOF'
中文简体测试
EOF

# 文件内容输出到 stdout
python .agents/skills/yashi-opencc-convert/scripts/opencc_convert.py 文件路径

# 环境自检（不确定 OpenCC 是否可用时先跑）
python .agents/skills/yashi-opencc-convert/scripts/opencc_convert.py --check
```

显式指定转换方向（默认 auto 自动检测）：

```bash
python opencc_convert.py -f s2t  文件   # 简体 → 繁体（标准）
python opencc_convert.py -f t2s  文件   # 繁体 → 简体
python opencc_convert.py -f s2twp 文件  # 简体 → 台湾繁体（含短语）
python opencc_convert.py -f s2hk 文件   # 简体 → 香港繁体
python opencc_convert.py --to cn 文件   # 快捷：转简体
python opencc_convert.py --to tw 文件   # 快捷：转台湾繁体
python opencc_convert.py --to hk 文件   # 快捷：转香港繁体
```

其它参数（详见脚本头部 docstring）：
- `-o 输出文件`：结果写入指定文件
- `--check`：只做环境自检，不转换
- 完整转换集：s2t/t2s/s2tw/tw2s/s2hk/hk2s/s2twp/tw2sp/t2tw/t2hk/t2jp/jp2t/auto
- 退出码：`0` 成功；`1` 参数错误或其它运行错误；`2` **OpenCC 不可用**（按下一节处理，不得当作成功）

## 环境检查：OpenCC 不可用时立即告知并询问（强制）

OpenCC 是否可用，直接决定转换结果是否可信。**一旦发现不可用，先停下告知用户并询问，
不得由模型自行替代。**

### 判定「不可用」

任一命中即视为不可用，不得继续转换：

- `python` 命令不存在，或脚本无法运行；
- `import opencc` 失败（未安装 `opencc-python-reimplemented`）；
- 加载词典或执行转换时抛异常（词典缺失、包损坏、转换集无效等）；
- 本技能目录下的 `scripts/opencc_convert.py` 不存在或读不到；
- 自检 `--check` 未通过（退出码非 0）。

脚本以退出码 `2` 表示「OpenCC 不可用」，`1` 表示参数错误或其它运行错误；两者都不能当作成功。

### 处理流程

1. **立即停止**：不再继续转换，不输出任何所谓「转换结果」。
2. **如实告知**：说明哪一项检查失败、原始报错的关键行、已运行的命令与退出码。
3. **给出选项并询问**（附推荐）：
   - 安装依赖后重试：`python -m pip install opencc-python-reimplemented`
     （网络不通时，经用户同意后可走代理 `http://127.0.0.1:23333`）；
   - 改用本机其它已安装 OpenCC 的 Python 环境，或另一台机器；
   - 由用户指定替代方案（并说明该结果不是 OpenCC 产出）。
4. **等待答复**：用户答复前不继续推进；安装或重试仍失败，回到第 2 步再次告知并询问。

### 禁止做法

- 按记忆、内置对照表或模型知识逐字/逐词替换简繁（即“自己翻”）；
- 手写或临时生成简繁映射表，冒充 OpenCC 输出；
- 静默降级：不告知用户就直接给出近似结果；
- 把非 OpenCC 产出的文本说成、标成 OpenCC 转换结果。

> 脚本自身在不可用时也会打印同样的提示（退出码 `2`），照此流程处理即可。

## 规则

1. 涉及简繁转换（含代码注释中的文本）时，**必须**调用本工具，不要靠记忆或手工替换
2. 转换后如为就地改写（-w），向用户说明改动；默认输出到 stdout 不落盘
3. 目标地区不明确时，用默认 auto（简体→繁体标准 / 繁体→简体）；
   涉及台湾/香港用词差异（如"软件/軟體、鼠标/滑鼠、服务器/伺服器"）时，按目标地区选 s2twp/s2hk
4. 若脚本不存在、Python 或 OpenCC 不可用、或调用失败：按「环境检查」一节立即告知用户
   并询问处理方式，**不得自行逐字翻译或改用其它近似办法**代替
