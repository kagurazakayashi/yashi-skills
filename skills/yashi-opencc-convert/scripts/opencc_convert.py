#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
opencc_convert.py — 中文简繁转换工具 (基于 OpenCC)

使用 OpenCC 进行中文简繁转换, 支持简体↔繁体、以及台湾/香港/日本汉字变体。
默认「自动检测方向」: 文本含繁体字 → 转为简体; 全为简体 → 转为繁体。

用法 (核心: 管道输入/输出 + -w 就地改写, 对 AI 调用最友好):
    # 1) 就地改写文件中的文本 (AI 最常用, 无需处理 stdout)
    python opencc_convert.py -w notes.md

    # 2) heredoc 管道粘贴文本 → 转换结果直接输出到 stdout
    python opencc_convert.py <<'EOF'
    中文简体测试
    EOF

    # 3) 管道: 从 stdin 读入, 输出到 stdout (cat / echo 等均可)
    cat text.txt | python opencc_convert.py

    # 4) 读取文件, 结果输出到 stdout
    python opencc_convert.py notes.md

    # 5) 输出到指定文件
    python opencc_convert.py notes.md -o notes_trad.md

    # 6) 显式指定转换集 / 目标地区
    python opencc_convert.py -f s2t   notes.md   # 简体 → 繁体 (标准)
    python opencc_convert.py -f t2s   notes.md   # 繁体 → 简体
    python opencc_convert.py -f s2twp notes.md   # 简体 → 台湾繁体 (含短语)
    python opencc_convert.py --to tw  notes.md   # 快捷: 转台湾繁体
    python opencc_convert.py --to cn  notes.md   # 快捷: 转简体

    # 7) 在 Python 中作为模块直接调用
    import opencc_convert
    text = opencc_convert.convert_text(原文)     # 返回转换后的文本

    # 8) 环境自检 (不确定 OpenCC 是否可用时先跑这一步)
    python opencc_convert.py --check

转换集说明 (OpenCC):
    s2t    简体 → 繁体 (标准)     t2s    繁体 → 简体
    s2tw   简体 → 台湾繁体       tw2s   台湾繁体 → 简体
    s2hk   简体 → 香港繁体       hk2s   香港繁体 → 简体
    s2twp  简体 → 台湾繁体(含短语) tw2sp  台湾繁体 → 简体(含短语)
    t2tw   繁体 → 台湾繁体       t2hk   繁体 → 香港繁体
    t2jp   繁体 → 日本新字体     jp2t   日本新字体 → 繁体
    auto   自动检测方向 (默认)

依赖: opencc-python-reimplemented (Python 包, 不是系统里的 opencc.exe 命令行)
    python -m pip install opencc-python-reimplemented

OpenCC 不可用时 (未安装 / import 失败 / 词典加载失败):
    脚本会打印原因并以退出码 2 结束。此时绝不能改用逐字替换、记忆映射或
    其它近似做法替代; 请立即把原因告知用户并询问如何处理, 等待用户答复。

退出码: 0 成功; 1 参数错误或其它运行错误; 2 OpenCC 不可用
"""

import argparse
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 依赖探测
# ---------------------------------------------------------------------------
# 刻意不在模块顶层 sys.exit: 这样 --check 也能报告「opencc 不可用」,
# 并且调用方 (AI) 能拿到明确的退出码 2, 而不是一句 ImportError traceback。
OPENCC_INSTALL_CMD = "python -m pip install opencc-python-reimplemented"

try:
    import opencc
    OPENCC_IMPORT_ERROR = None
except Exception as exc:                # 未安装 / 包损坏 / 依赖缺失
    opencc = None
    OPENCC_IMPORT_ERROR = exc

UNAVAILABLE_NOTICE = """\
错误: OpenCC 不可用, 无法进行可靠的简繁转换。

AI 代理请注意: 不要改用逐字替换、记忆映射或其它近似做法代替本工具!
请立即把下面的原因告知用户, 并询问如何处理 (安装依赖 / 改用其它环境 / 是否授权替代方案);
在用户答复前不要继续转换。

原因: {reason}

排查:
  1) 确认 Python 可用: python --version
  2) 安装依赖: {install}
  3) 重新自检: python opencc_convert.py --check
"""

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

VALID_CONFIGS = (
    "s2t", "t2s", "s2tw", "tw2s", "s2hk", "hk2s",
    "s2twp", "tw2sp", "t2tw", "t2hk", "t2jp", "jp2t", "auto",
)

# --to 快捷地区映射: 目标地区 → 默认转换集
REGION_MAP = {
    "cn": "t2s",     # 转简体
    "tw": "s2twp",   # 转台湾繁体 (含短语)
    "hk": "s2hk",    # 转香港繁体
    "jp": "t2jp",    # 转日本新字体 (输入须为繁体)
}

_CACHE = {}


# ---------------------------------------------------------------------------
# 可用性检查
# ---------------------------------------------------------------------------

def _setup_stdout() -> None:
    """Windows 控制台默认 GBK, 统一切到 UTF-8, 避免中文输出乱码。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


def _unavailable(reason: str) -> None:
    """打印「OpenCC 不可用」提示并终止, 退出码固定为 2。

    退出码 2 是给调用方 (AI) 的明确信号: 不可用, 必须告知用户并询问,
    不得自行改用其它翻译/替换方式。
    """
    print(UNAVAILABLE_NOTICE.format(reason=reason, install=OPENCC_INSTALL_CMD),
          file=sys.stderr)
    raise SystemExit(2)


def _require_opencc() -> None:
    """确认 opencc 包可导入, 否则以退出码 2 结束。"""
    if opencc is None:
        _unavailable(f"import opencc 失败: {OPENCC_IMPORT_ERROR}")


def _get_converter(config: str):
    """获取 (并缓存) OpenCC 转换器, 避免重复加载词典。

    不可用或转换器初始化失败 (词典缺失 / 包损坏等) 时以退出码 2 结束,
    而不是抛出 traceback 让调用方误以为可以继续。
    """
    _require_opencc()
    if config not in _CACHE:
        try:
            _CACHE[config] = opencc.OpenCC(config)
        except Exception as exc:
            _unavailable(f"初始化 OpenCC 转换器 ({config}) 失败: {exc!r}")
    return _CACHE[config]


# ---------------------------------------------------------------------------
# 核心转换
# ---------------------------------------------------------------------------

def convert_text(text: str, config: str = "auto") -> str:
    """将文本按指定转换集转换; config='auto' 时自动检测方向。

    自动检测规则: 用 t2s 试转, 若结果与原文不同说明含繁体字符 → 转简体;
    否则视为简体文本 → 转繁体。空文本原样返回。
    """
    if not text:
        return text
    if config == "auto":
        t2s = _get_converter("t2s")
        if t2s.convert(text) != text:
            return t2s.convert(text)
        return _get_converter("s2t").convert(text)
    return _get_converter(config).convert(text)


# ---------------------------------------------------------------------------
# 环境自检
# ---------------------------------------------------------------------------

def _run_check() -> int:
    """环境自检: Python / opencc 包 / 词典加载 / 示例转换。

    返回 0 表示可用; 不可用时打印原因并以退出码 2 结束。
    """
    print("OpenCC 环境自检")
    print(f"  Python : {sys.version.split()[0]} ({sys.executable})")

    problems = []

    if opencc is None:
        print(f"  opencc : 不可用 (import opencc 失败: {OPENCC_IMPORT_ERROR})")
        problems.append(f"无法导入 opencc 包: {OPENCC_IMPORT_ERROR}")
    else:
        version = getattr(opencc, "__version__", "未知")
        location = getattr(opencc, "__file__", "未知")
        print(f"  opencc : 已安装, 版本 {version}, 位置 {location}")
        # t2s / s2t 覆盖默认 auto 的两个方向; 能加载且结果有变化才算通过
        for config, sample in (("t2s", "漢字轉換測試"), ("s2t", "汉字转换测试")):
            try:
                converted = opencc.OpenCC(config).convert(sample)
            except Exception as exc:
                print(f"  {config:<5}: 失败 ({exc!r})")
                problems.append(f"转换集 {config} 初始化/转换失败: {exc!r}")
                continue
            if converted == sample:
                print(f"  {config:<5}: 结果无变化 ({sample} -> {converted})")
                problems.append(f"转换集 {config} 未产生变化, 词典可能缺失")
            else:
                print(f"  {config:<5}: 通过 ({sample} -> {converted})")

    if problems:
        print(file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        _unavailable("; ".join(problems))

    print("\n结果: OpenCC 可用。")
    return 0


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

class _ArgumentParser(argparse.ArgumentParser):
    """参数错误时改用退出码 1。

    argparse 默认用 2 表示「用法错误」, 与本脚本「2 = OpenCC 不可用」冲突,
    这里统一改成 1, 保证退出码语义唯一。
    """

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        print(f"参数错误: {message}", file=sys.stderr)
        raise SystemExit(1)


def main() -> None:
    _setup_stdout()
    ap = _ArgumentParser(
        description="中文简繁转换: 基于 OpenCC, 默认自动检测方向。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("file", nargs="?", help="输入文本文件 (缺省从 stdin 读取)")
    ap.add_argument("-o", "--output", help="输出文件 (缺省输出到 stdout)")
    ap.add_argument("-w", "--write", action="store_true",
                    help="就地改写输入文件")
    ap.add_argument("-f", "--from", dest="from_config",
                    choices=VALID_CONFIGS, default="auto",
                    help="OpenCC 转换集 (默认 auto 自动检测方向)")
    ap.add_argument("--to", dest="to_region", choices=tuple(REGION_MAP),
                    help="快捷指定目标地区: cn/tw/hk/jp")
    ap.add_argument("--check", action="store_true",
                    help="仅做环境自检 (Python / opencc 包 / 词典 / 示例转换), 不转换")
    args = ap.parse_args()

    # 自检: 需要先确认 OpenCC 可用, 再决定是否转换
    if args.check:
        raise SystemExit(_run_check())

    # 转换前先确认依赖存在; 不可用则立即报错退出 (退出码 2)
    _require_opencc()

    if args.from_config != "auto" and args.to_region:
        ap.error("-f/--from 与 --to 不能同时使用")

    config = REGION_MAP[args.to_region] if args.to_region else args.from_config

    if args.file:
        try:
            text = Path(args.file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"错误: 无法读取文件 {args.file}: {exc}", file=sys.stderr)
            raise SystemExit(1)
    else:
        text = sys.stdin.read()

    try:
        result = convert_text(text, config)
    except SystemExit:
        raise
    except Exception as exc:            # 转换阶段异常: 不做任何替代转换
        print(f"错误: 转换失败: {exc!r}", file=sys.stderr)
        print("若该错误与 OpenCC 词典/模块有关, 请把原因告知用户并询问, "
              "不要改用逐字翻译。", file=sys.stderr)
        raise SystemExit(1)

    if args.write and args.file:
        Path(args.file).write_text(result, encoding="utf-8")
        print(f"已转换并就地改写: {args.file}")
    elif args.output:
        Path(args.output).write_text(result, encoding="utf-8")
        print(f"已写入: {args.output}")
    else:
        sys.stdout.write(result)


if __name__ == "__main__":
    main()
