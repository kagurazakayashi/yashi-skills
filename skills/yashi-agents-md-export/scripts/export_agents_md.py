#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""從規則資料庫 AGENTS.json 匯出 AGENTS.md。

用途
----
`~/.agents/AGENTS.json` 是 yashi 全域 AGENTS 規則的唯一權威來源；本腳本依
「平台 + 語言」篩選規則，輸出對應語言的 `AGENTS.md`（Markdown 無序清單）。
未指定平台／語言時，自動偵測目前作業系統並輸出英文版。

行為要點
--------
1. 平台篩選：規則的 ``platforms`` 含 ``all`` 或目標平台才會匯出。
2. 語言篩選：``en`` -> ``en`` 欄位；``zh-CN`` -> ``zh`` 欄位。
3. 環境偵測：逐條檢查規則 ``commands`` 中的命令是否真的存在（字串＝平台無關，
   ``{"cmd","os"}`` 物件＝只在該平台取用），含無效命令的規則整條剔除並回報
   （--check auto|on|off）。僅在「目標平台 == 目前平台」時啟用檢查，因為本機的
   探測結果不能代表其他作業系統。
4. 輸出：``-o`` 以 ``.md`` 結尾視為檔案，否則視為目錄（目錄內固定寫
   ``AGENTS.md``）；覆蓋既有檔案前先備份為 ``AGENTS.md.<時間戳>.bak``。

退出碼
------
0 成功；1 參數或資料庫錯誤；2 有規則因命令缺失被剔除（輸出仍會產生）。
"""

from __future__ import annotations

import argparse
import json
import os
import platform as platform_mod
import shutil
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCHEMA = "yashi-agents-rules/2"
PLATFORMS = ("all", "windows", "linux", "macos")
LANGUAGES = ("en", "zh-CN")
RULE_FIELDS = ("id", "zh", "en", "platforms", "commands",
               "category", "scope", "updated_at")
OUTPUT_NAME = "AGENTS.md"
DEFAULT_TZ_OFFSET_HOURS = 8  # Asia/Shanghai

# 規則腳本自身位置推導專案根：<root>/skills/yashi-agents-md-export/scripts/export_agents_md.py
SCRIPT_PATH = Path(__file__).resolve()
SKILL_DIR = SCRIPT_PATH.parent.parent
DEFAULT_ROOT = SKILL_DIR.parent.parent if SKILL_DIR.parent.name == "skills" else SKILL_DIR
DEFAULT_DB = DEFAULT_ROOT / "AGENTS.json"

# PowerShell 指令（Get-Command 這類）需以 Get-Command 判定；其餘走 shutil.which。
POWERSHELL_HOSTS = ("pwsh", "powershell")

# 僅存在於 POSIX shell 的內建指令：找得到 msys2 bash 即視為可用。
POSIX_BUILTINS = frozenset({
    "read", "cd", "export", "set", "unset", "source", ".", "local", "alias",
    "jobs", "type", "umask", "pwd", "exec", "eval", "getopts", "shift",
    "trap", "wait", "return", "exit", "times", "ulimit", "test", "[", ":",
    "true", "false", "printf", "echo", "kill", "command", "hash", "fc",
    "bg", "fg", "suspend", "logout", "break", "continue", "declare",
})

# 已知工具的自帶目錄：這些程式按規則就是以絕對路徑直接調用的，不進 PATH，
# 所以 shutil.which 找不到並不能證明規則失效。順序即查找順序。
HOME = Path.home()
TOOL_HOMES: dict[str, tuple[str, ...]] = {
    # evernight-capture skill 內自帶的截圖程式（規則中給的就是絕對路徑）
    "ecapture": (
        str(HOME / ".agents" / "skills" / "yashi-evernight-capture" / "ECAPTURE.EXE"),
        r"C:\Program Files\EvernightCapture\ECAPTURE.EXE",
    ),
    "rtk": (
        str(HOME / "Dropbox" / "path" / "rtk.exe"),
    ),
    "msys2": (
        r"C:\tools\msys64\usr\bin\bash.exe",
        r"C:\msys64\usr\bin\bash.exe",
    ),
}


# --------------------------------------------------------------------------- #
# 基礎工具
# --------------------------------------------------------------------------- #
def eprint(*parts: object) -> None:
    """輸出到 stderr（診斷訊息），永不污染 stdout 的報告。"""
    print(*parts, file=sys.stderr)


def detect_platform() -> str:
    """回傳目前作業系統對應的資料庫平台代號。"""
    if os.name == "nt" or sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "all"


def now_tz(tz_offset_hours: int = DEFAULT_TZ_OFFSET_HOURS) -> str:
    """回傳指定時區的 ISO 8601 時間字串（秒級）。"""
    return datetime.now(timezone(timedelta(hours=tz_offset_hours))).isoformat(timespec="seconds")


def strip_exe(name: str) -> str:
    """去掉結尾的 .exe / .cmd / .bat / .ps1，供 shutil.which 查找。"""
    base = name.strip().strip('`')
    lower = base.lower()
    for ext in (".exe", ".cmd", ".bat", ".ps1", ".com"):
        if lower.endswith(ext):
            return base[: -len(ext)]
    return base


def which_any(*names: str) -> str | None:
    """依序以 shutil.which 查找多個候選名，回傳第一個命中的完整路徑。"""
    for name in names:
        hit = shutil.which(name)
        if hit:
            return hit
    return None


def is_wsl_bash(path: str | None) -> bool:
    """判斷 bash 是不是 WSL 那份（本機不可用，規則明確要求避開）。"""
    if not path:
        return False
    lowered = path.replace("/", "\\").lower()
    return lowered.endswith("\\windows\\system32\\bash.exe") or "/windows/system32/bash.exe" in lowered


def find_in_tool_homes(name: str) -> str | None:
    """在已知工具自帶目錄中查找（涵蓋不進 PATH 的程式，如 ECAPTURE.EXE）。"""
    key = strip_exe(name).lower()
    for candidate in TOOL_HOMES.get(key, ()):
        if Path(candidate).is_file():
            return candidate
    return None


# --------------------------------------------------------------------------- #
# 環境偵測
# --------------------------------------------------------------------------- #
class EnvProbe:
    """快取環境探測結果，避免同一命令被重複查詢。"""

    def __init__(self) -> None:
        self._which_cache: dict[str, str | None] = {}
        self._ps_cache: dict[str, bool] = {}
        self._bash_cache: str | None | bool = False  # False = 尚未解析
        self._ps_host: str | None | bool = False

    # -- 個別解析器 ------------------------------------------------------- #
    def pwsh(self) -> str | None:
        """目前系統可用的 PowerShell 主機（優先 pwsh）。"""
        if self._ps_host is False:
            self._ps_host = which_any(*POWERSHELL_HOSTS)
        return self._ps_host or None

    def msys2_bash(self) -> str | None:
        """定位 msys2 的 bash.exe：先看 PATH 上的 sh，再排除 WSL 的 bash。"""
        if self._bash_cache is False:
            found = None
            sh_path = shutil.which("sh")
            if sh_path:
                candidate = Path(sh_path).with_name("bash.exe")
                if candidate.is_file():
                    found = str(candidate)
            if not found:
                found = find_in_tool_homes("msys2")
            if not found:
                hit = which_any("bash", "bash.exe")
                found = hit if hit and not is_wsl_bash(hit) else None
            self._bash_cache = found
        return self._bash_cache or None

    def powershell_has(self, name: str) -> bool:
        """用 Get-Command 判斷 PowerShell 指令／別名／函式是否存在。"""
        if name in self._ps_cache:
            return self._ps_cache[name]
        host = self.pwsh()
        ok = False
        if host:
            try:
                proc = subprocess.run(
                    [host, "-NoProfile", "-NonInteractive", "-Command",
                     f"if (Get-Command -Name '{name}' -ErrorAction SilentlyContinue) {{ 'yes' }}"],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=60,
                )
                ok = proc.returncode == 0 and "yes" in proc.stdout
            except (OSError, subprocess.SubprocessError):
                ok = False
        self._ps_cache[name] = ok
        return ok

    # -- 對外查詢 --------------------------------------------------------- #
    def lookup(self, command: str) -> tuple[bool, str]:
        """回傳 (是否可用, 判定依據說明)。"""
        name = strip_exe(command)
        low = name.lower()

        if not name:
            return False, "空命令名"

        if low in ("msys2", "bash"):
            hit = self.msys2_bash()
            return (bool(hit), f"msys2 bash: {hit}" if hit
                    else "找不到 msys2 的 bash.exe（PATH 上的 bash 是 WSL 的）")

        if low == "sh":
            hit = which_any("sh", "sh.exe")
            return (bool(hit), f"which: {hit}")

        if low == "cmd":
            hit = which_any("cmd", "cmd.exe")
            return (bool(hit), f"which: {hit}")

        if low in ("powershell", "powershell.exe"):
            hit = which_any("powershell", "powershell.exe")
            return (bool(hit), f"which: {hit}")

        if low == "pwsh":
            hit = which_any("pwsh", "pwsh.exe")
            return (bool(hit), f"which: {hit}")

        if low == "rtk":
            hit = which_any("rtk", "rtk.exe")
            if hit and is_wsl_bash(hit):
                hit = None
            if not hit:
                hit = find_in_tool_homes("rtk")
            return (bool(hit), f"which/home: {hit}" if hit else "PATH 與已知目錄都沒有 rtk")

        if "-" in name:
            ok = self.powershell_has(name)
            return (ok, f"Get-Command（主機 {self.pwsh() or '無'}）: {'找到' if ok else '找不到'}")

        if low in POSIX_BUILTINS:
            hit = self.msys2_bash()
            return (bool(hit), f"POSIX 內建，msys2 bash: {hit}" if hit else "POSIX 內建，但找不到 msys2 bash")

        hit = find_in_tool_homes(name)
        how = f"工具自帶目錄: {hit}" if hit else None
        if not hit:
            if name not in self._which_cache:
                self._which_cache[name] = which_any(name, name + ".exe")
            hit = self._which_cache[name]
            how = f"which: {hit}"
        return (bool(hit), how)


# --------------------------------------------------------------------------- #
# 資料庫載入與驗證
# --------------------------------------------------------------------------- #
class DbError(Exception):
    """資料庫不符合預期格式。"""


def schema_ok(actual: object) -> bool:
    """接受同一家族的任何版本號，僅在家族不符時視為錯誤。"""
    return isinstance(actual, str) and actual.split("/")[0] == SCHEMA.split("/")[0]


def load_db(path: Path) -> dict:
    """讀取並基本驗證資料庫。"""
    if not path.is_file():
        raise DbError(f"找不到資料庫：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DbError(f"資料庫不是合法 JSON：{path}（{exc}）") from exc
    if not schema_ok(data.get("schema")):
        raise DbError(f"schema 不符：期望 {SCHEMA}，實際 {data.get('schema')!r}")
    rules = data.get("rules")
    if not isinstance(rules, list) or not rules:
        raise DbError("rules 必須是非空陣列")
    return data


def validate_db(data: dict) -> list[str]:
    """檢查規則欄位完整性、id 唯一性、列舉值合法性；回傳問題清單。"""
    problems: list[str] = []
    seen: set[str] = set()
    for idx, rule in enumerate(data["rules"]):
        where = f"rules[{idx}]"
        rid = rule.get("id", "?")
        for field in RULE_FIELDS:
            if field not in rule:
                problems.append(f"{where}（{rid}）缺少欄位 {field}")
        if rid in seen:
            problems.append(f"{where} id 重複：{rid}")
        seen.add(rid)
        for field in ("zh", "en"):
            text = rule.get(field, "")
            if not isinstance(text, str) or not text.strip():
                problems.append(f"{where}（{rid}）{field} 為空或非字串")
        plats = rule.get("platforms")
        if not isinstance(plats, list) or not plats:
            problems.append(f"{where}（{rid}）platforms 必須是非空陣列")
        else:
            for plat in plats:
                if plat not in PLATFORMS:
                    problems.append(f"{where}（{rid}）platforms 含非法值：{plat}")
        cmds = rule.get("commands")
        if not isinstance(cmds, list):
            problems.append(f"{where}（{rid}）commands 必須是陣列（字串或 {{\"cmd\", \"os\"}} 物件）")
        else:
            for index, item in enumerate(cmds):
                spot = f"{where}（{rid}）commands[{index}]"
                if isinstance(item, str):
                    continue
                if not isinstance(item, dict):
                    problems.append(f"{spot} 必須是字串或物件")
                    continue
                unknown = set(item) - {"cmd", "os"}
                if unknown:
                    problems.append(f"{spot} 含未知鍵：{sorted(unknown)}")
                if not item.get("cmd"):
                    problems.append(f"{spot} 缺少 cmd")
                if item.get("os") is not None and item["os"] not in PLATFORMS:
                    problems.append(f"{spot} 的 os 非法：{item.get('os')!r}")
        scope = rule.get("scope")
        if scope not in ("global", "project"):
            problems.append(f"{where}（{rid}）scope 非法：{scope!r}")
        upd = rule.get("updated_at", "")
        if not isinstance(upd, str) or len(upd) != 10:
            problems.append(f"{where}（{rid}）updated_at 格式應為 YYYY-MM-DD")
    return problems


# --------------------------------------------------------------------------- #
# 篩選
# --------------------------------------------------------------------------- #
def rule_platforms_ok(rule: dict, target: str) -> bool:
    """target 為 'all' 時不過濾；否則要求規則含 'all' 或 target。"""
    if target == "all":
        return True
    plats = rule.get("platforms") or []
    return "all" in plats or target in plats


def rule_scope_ok(rule: dict, scope: str) -> bool:
    """scope 為 'all' 時不過濾；否則要求相等。"""
    return scope == "all" or rule.get("scope") == scope


def command_entries(rule: dict) -> list[tuple[str, str | None]]:
    """把規則的 commands 正規化成 (命令名, 限定平台或 None) 清單。

    兩種寫法（規則內可混用）：
      "git"                          平台無關，於任何平台都可校驗
      {"cmd": "ECAPTURE.EXE", "os": "windows"}   只在該平台有意義
    """
    entries: list[tuple[str, str | None]] = []
    for item in rule.get("commands") or []:
        if isinstance(item, str):
            if item:
                entries.append((item, None))
        elif isinstance(item, dict):
            cmd = item.get("cmd")
            if cmd:
                entries.append((str(cmd), item.get("os")))
    return entries


def commands_for_platform(rule: dict, platform: str) -> list[str]:
    """挑出在指定平台上需要校驗的命令（限定平台的條目只在該平台取用）。"""
    return [cmd for cmd, os_name in command_entries(rule)
            if platform == "all" or os_name in (None, platform)]


def check_commands(rules: list[dict], target: str, mode: str, probe: EnvProbe):
    """依檢查模式回傳 (保留規則, 剔除清單, 命令可用性表, 生效模式, 說明)。

    只在「目標平台 == 目前平台」時才真的探測，避免用本機結果誤刪其他平台的
    條目；--check on 也只能在目標平台等於目前平台時生效。
    另外若目標平台正是目前平台、卻找不到任何一條命令，代表資料庫格式與本
    腳本不匹配（例如 commands 欄位改名），此時一律不剔除，避免整份規則被誤刪。
    """
    current = detect_platform()
    effective = mode
    note = ""
    if mode == "auto":
        effective = "on" if target == current else "off"
        if target != current:
            note = (f"目標平台 {target} 與目前平台 {current} 不同，"
                    f"自動略過命令檢查（本機探測結果不代表 {target}）")
    elif mode == "on" and target != current:
        effective = "off"
        note = ("警告：--check on 但目標平台非目前平台，"
                "為避免誤刪已自動略過命令檢查")

    applicable = sum(1 for rule in rules if commands_for_platform(rule, target))
    if effective != "off" and applicable == 0:
        effective = "off"
        note = ("警告：資料庫在本平台找不到任何可校驗的命令，已停用命令檢查"
                "（請確認 commands 欄位格式與本腳本相符）")

    kept: list[dict] = []
    dropped: list[dict] = []
    availability: dict[str, dict] = {}

    for rule in rules:
        if effective == "off":
            kept.append(rule)
            continue
        missing = []
        for cmd in commands_for_platform(rule, target):
            if cmd not in availability:
                ok, how = probe.lookup(cmd)
                availability[cmd] = {"ok": ok, "how": how}
            if not availability[cmd]["ok"]:
                missing.append(f"{cmd}（{availability[cmd]['how']}）")
        if missing:
            dropped.append({"id": rule["id"], "category": rule.get("category"),
                            "platforms": rule.get("platforms"), "missing": missing,
                            "text": rule.get("en") or rule.get("zh")})
        else:
            kept.append(rule)
    return kept, dropped, availability, effective, note


# --------------------------------------------------------------------------- #
# 輸出
# --------------------------------------------------------------------------- #
def render(kept: list[dict], lang: str, target: str, db_path: Path,
           generated_at: str, dropped: list[dict], checked: bool,
           newline: str = "\n", metadata: dict | None = None) -> str:
    """渲染 Markdown 內容：可選的來源說明註解 + 規則清單。

    輸出中的換行一律先保持 \\n，最終換行符由 write_output 統一換算。
    """

    def one_line(value: object) -> str:
        """把可能含換行的文字壓成單行（規則與說明都必須單行）。"""
        return str(value).replace("\r\n", " ").replace("\r", " ").replace("\n", " ").strip()

    field = "zh" if lang == "zh-CN" else "en"
    lines: list[str] = []

    meta = (metadata or {}).get(lang) or {}
    # 說明文字：主鍵 note_zh / note_en，後續分段用 note_zh_2、note_zh_3……按序拼接。
    primary = "note_zh" if lang == "zh-CN" else "note_en"
    notes = [one_line(meta[primary])] if meta.get(primary) else []
    index = 2
    while meta.get(f"{primary}_{index}"):
        notes.append(one_line(meta[f"{primary}_{index}"]))
        index += 1
    if notes:
        lines.append("<!--")
        lines.extend(notes)
        lines.append("-->")
        lines.append("")

    for rule in kept:
        text = one_line(rule[field])
        if text:
            lines.append("- " + text)

    if not lines:
        return ""
    return newline.join(lines) + newline


def newline_for(kind: str) -> str:
    """解析 --newline：auto 於 Windows 用 CRLF，其餘平台用 LF。"""
    if kind == "lf":
        return "\n"
    if kind == "crlf":
        return "\r\n"
    return "\r\n" if detect_platform() == "windows" else "\n"


def bilingual_name(base: Path, lang: str) -> Path:
    """雙語輸出時推導語言對應檔名（英文 AGENTS.md，簡中 AGENTS.zh-CN.md）。"""
    if lang == "en":
        return base
    name = base.name
    stem = name[:-3] if name.lower().endswith(".md") else name
    return base.with_name(f"{stem}.zh-CN.md")


def resolve_output(output: str | None) -> Path:
    """把 -o 解析成最終要寫入的檔案路徑。"""
    if not output:
        return Path.cwd() / OUTPUT_NAME
    path = Path(output).expanduser()
    if path.suffix.lower() == ".md":
        return path
    return path / OUTPUT_NAME


def write_output(path: Path, content: str, backup: bool, newline: str = "\n") -> str | None:
    """寫入檔案，必要時先備份；回傳備份檔路徑。

    寫入前先把所有換行（\\r\\n、\\r、\\n）統一成 \\n 再翻成目標換行符，避免
    規則文字中殘留的 CR 造成 \\r\\r\\n；newline='' 則關閉平台翻譯。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if path.is_file() and backup:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = path.with_name(f"{path.name}.{stamp}.bak")
        shutil.copy2(path, backup_path)
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(normalized.translate({0x0A: newline}))
    return str(backup_path) if backup_path else None


# --------------------------------------------------------------------------- #
# 自我測試
# --------------------------------------------------------------------------- #
def self_test(db_path: Path, data: dict) -> int:
    """結構驗證 + 與同目錄既有的 AGENTS.md / AGENTS.zh-CN.md 逐條比對。"""
    problems = validate_db(data)
    rules = data["rules"]
    eprint(f"[self-test] 資料庫：{db_path}")
    eprint(f"[self-test] 規則數：{len(rules)}；結構問題：{len(problems)}")
    for item in problems:
        eprint(f"  - {item}")

    root = db_path.parent
    for lang, filename in (("en", "AGENTS.md"), ("zh-CN", "AGENTS.zh-CN.md")):
        source = root / filename
        if not source.is_file():
            eprint(f"[self-test] {filename} 不存在，略過比對")
            continue
        field = "zh" if lang == "zh-CN" else "en"
        src_lines = [ln.strip()[2:] for ln in source.read_text(encoding="utf-8").splitlines()
                     if ln.strip().startswith("- ")]
        db_lines = [str(r[field]) for r in rules]
        only_src = [t for t in src_lines if t not in set(db_lines)]
        only_db = [t for t in db_lines if t not in set(src_lines)]
        if not only_src and not only_db:
            eprint(f"[self-test] {filename}：{len(src_lines)} 條，與資料庫逐條一致")
        else:
            problems.append(f"{filename} 與資料庫不一致")
            eprint(f"[self-test] {filename} 不一致：僅檔案有 {len(only_src)} 條，"
                   f"僅資料庫有 {len(only_db)} 條")
            for text in only_src[:5]:
                eprint(f"    僅檔案：{text[:80]}")
            for text in only_db[:5]:
                eprint(f"    僅資料庫：{text[:80]}")

    eprint("[self-test] 結果：" + ("通過" if not problems else f"失敗（{len(problems)} 個問題）"))
    return 0 if not problems else 1


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="export_agents_md.py",
        description="從 AGENTS.json 依平台與語言匯出 AGENTS.md",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "範例：\n"
            "  python export_agents_md.py                        # 自動偵測系統，英文，寫到當前目錄\n"
            "  python export_agents_md.py --lang zh-CN -o out/   # 簡中版寫到 out/AGENTS.md\n"
            "  python export_agents_md.py --lang both -o out/    # 一次匯出兩種語言\n"
            "  python export_agents_md.py --dry-run --verbose    # 只看結果不寫檔\n"
            "  python export_agents_md.py --self-test            # 檢查資料庫與既有檔案一致性\n"
        ),
    )
    parser.add_argument("-d", "--db", default=None,
                        help=f"資料庫路徑（預設自動推導，通常為 {DEFAULT_DB}）")
    parser.add_argument("-p", "--platform", default="auto", choices=("auto",) + PLATFORMS,
                        help="目標平台；auto=當前系統（預設）")
    parser.add_argument("-l", "--lang", default="auto", choices=("auto",) + LANGUAGES + ("both",),
                        help="輸出語言；auto=英文（預設）")
    parser.add_argument("-o", "--output", default=None,
                        help="輸出檔案（以 .md 結尾）或目錄；預設為當前目錄下 AGENTS.md")
    parser.add_argument("--scope", default="all", choices=("all", "global", "project"),
                        help="依規則 scope 過濾（預設 all）")
    parser.add_argument("--check", default="auto", choices=("auto", "on", "off"),
                        help="是否校驗 commands 裡的命令（預設 auto：僅目標平台為目前平台時開啟）")
    parser.add_argument("--newline", default="auto", choices=("auto", "lf", "crlf"),
                        help="換行符；auto=Windows 用 CRLF，其餘平台用 LF（預設 auto）")
    parser.add_argument("--dry-run", action="store_true", help="只回報，不寫入任何檔案")
    parser.add_argument("--no-backup", action="store_true", help="覆蓋既有檔案時不備份")
    parser.add_argument("--verbose", action="store_true", help="輸出每條被採用／剔除的規則")
    parser.add_argument("--report-json", default=None, help="把執行報告寫成 JSON")
    parser.add_argument("--self-test", action="store_true",
                        help="驗證資料庫結構，並與既有 AGENTS.md / AGENTS.zh-CN.md 比對")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    args = build_parser().parse_args(argv)
    db_path = Path(args.db).expanduser().resolve() if args.db else DEFAULT_DB
    try:
        data = load_db(db_path)
    except DbError as exc:
        eprint(f"錯誤：{exc}")
        return 1

    if args.self_test:
        return self_test(db_path, data)

    target = detect_platform() if args.platform == "auto" else args.platform
    lang = "en" if args.lang == "auto" else args.lang

    problems = validate_db(data)
    if problems:
        eprint("錯誤：資料庫結構有問題，已中止：")
        for item in problems:
            eprint(f"  - {item}")
        return 1

    all_rules = data["rules"]
    selected_idx = [i for i, r in enumerate(all_rules)
                    if rule_platforms_ok(r, target) and rule_scope_ok(r, args.scope)]
    scoped = [all_rules[i] for i in selected_idx]
    platform_skipped = [r for i, r in enumerate(all_rules)
                        if i not in set(selected_idx) and not rule_platforms_ok(r, target)]
    scope_skipped = [r for i, r in enumerate(all_rules)
                     if i not in set(selected_idx) and rule_platforms_ok(r, target)]

    probe = EnvProbe()
    kept, dropped, availability, effective_check, check_note = check_commands(
        scoped, target, args.check, probe)

    generated_at = now_tz()
    eprint(f"[export] 資料庫：{db_path}")
    eprint(f"[export] 目標平台：{target}（目前平台 {detect_platform()}）｜"
           f"語言：{lang}｜scope：{args.scope}")
    eprint(f"[export] 規則總數 {len(data['rules'])}｜平台篩除 {len(platform_skipped)}｜"
           f"scope 篩除 {len(scope_skipped)}｜命令檢查：{effective_check}")
    if check_note:
        eprint(f"[export] {check_note}")

    if dropped:
        eprint(f"[export] 因命令缺失剔除 {len(dropped)} 條規則：")
        for item in dropped:
            eprint(f"  - {item['id']} [{item['category']}] 缺少 {', '.join(item['missing'])}")
            eprint(f"      {str(item['text'])[:100]}")
    if availability:
        eprint("[export] 命令可用性：")
        for name, info in sorted(availability.items()):
            eprint(f"  {'OK  ' if info['ok'] else 'MISS'} {name:<16} {info['how']}")
    if args.verbose and platform_skipped:
        eprint("[export] 因平台被排除：")
        for rule in platform_skipped:
            eprint(f"  - {rule['id']} platforms={rule.get('platforms')}")
    if args.verbose:
        eprint(f"[export] 採用 {len(kept)} 條：" +
               ", ".join(r["id"] for r in kept))

    langs = ["en", "zh-CN"] if lang == "both" else [lang]
    nl = newline_for(args.newline)
    metadata = data.get("source_metadata") or {}
    written: list[dict] = []
    for one in langs:
        content = render(kept, one, target, db_path, generated_at, dropped,
                         effective_check == "on", newline=nl, metadata=metadata)
        out_path = resolve_output(args.output)
        if lang == "both":
            out_path = bilingual_name(out_path, one)
        if args.dry_run:
            eprint(f"[export] （dry-run）{one} -> {out_path}｜{len(kept)} 條｜{len(content)} 字元")
            written.append({"lang": one, "path": str(out_path), "rules": len(kept),
                            "chars": len(content), "written": False})
        else:
            backup = write_output(out_path, content, backup=not args.no_backup, newline=nl)
            eprint(f"[export] 已寫入 {out_path}｜{len(kept)} 條｜{len(content)} 字元"
                   + (f"（備份 {backup}）" if backup else ""))
            written.append({"lang": one, "path": str(out_path), "rules": len(kept),
                            "chars": len(content), "written": True, "backup": backup})

    report = {
        "generated_at": generated_at,
        "database": str(db_path),
        "current_platform": detect_platform(),
        "target_platform": target,
        "languages": langs,
        "scope": args.scope,
        "command_check": effective_check,
        "command_check_note": check_note,
        "total_rules": len(data["rules"]),
        "platform_skipped": [{"id": r["id"], "platforms": r.get("platforms")}
                             for r in platform_skipped],
        "scope_skipped": [{"id": r["id"], "scope": r.get("scope")} for r in scope_skipped],
        "kept": [r["id"] for r in kept],
        "dropped": dropped,
        "command_availability": availability,
        "outputs": written,
        "dry_run": bool(args.dry_run),
    }
    if args.report_json:
        Path(args.report_json).expanduser().write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        eprint(f"[export] 報告已寫入 {args.report_json}")

    return 2 if dropped else 0


if __name__ == "__main__":
    raise SystemExit(main())
