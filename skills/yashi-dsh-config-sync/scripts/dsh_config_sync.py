#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dsh_config_sync.py — DeepSeek Harness（dsh）配置一句话导入 / 导出。

设计目标
--------
* **导出**：把 ``$DSH_HOME`` 下所有「配置类」文件打包成一个 **7z** 压缩包
  （``7z a -t7z -mx9``，最高压缩率）：
  主设置 ``settings.yaml``、用户全局指令 ``AGENTS.md``、自定义 agent 预设
  ``.agent-presets/``、各 profile 的 ``package.json`` / ``cordis.patch.yml`` /
  ``pnpm-workspace.yaml`` / ``.npmrc``，以及**插件自己的配置文件**
  （顶层 ``*.json`` / ``*.yaml`` / 插件配置子目录等，运行时自动发现）。
  导入侧为向后兼容仍可读取旧版 zip 包（只读，不再产出）。
* **不导出**：
  - 环境变量（API key 等）——由 ``yashi-dsh-env-sync`` 用 ini 单独导入导出；
  - 敏感凭证（``.credentials.yaml`` / ``credentials.json`` / ``*.key`` / ``*.pem``
    / 含 ``_authToken`` 的 ``.npmrc``）——导入后重新登录或重填；
  - **所有导入后能自动下载/重建的东西**（``node_modules``、
    ``.dsh-module-fallback``、``pnpm-lock.yaml``、``cordis.yml``）；
  - 运行时数据（``sessions`` / ``storages`` / ``attachments`` / ``llm-deepseek``
    / 日志 / 回收站 / usage 账本）。
* **导入**：还原文件 → 逐 profile 自动补全依赖（``pnpm install`` +
  ``pnpm approve-builds --all``）→ 实测 ``dsh web`` 启动/停止 → 输出诊断，
  失败时给出可执行的排障建议（联动 ``yashi-dsh-update``）。

命令
----
::

    python dsh_config_sync.py export  [-o OUT.7z] [--home DIR] [--print]
    python dsh_config_sync.py inspect -i IN.7z
    python dsh_config_sync.py import  -i IN.7z [--home DIR] [--dry-run]
    python dsh_config_sync.py test    [--home DIR] [--port N]

纯标准库；Python 之外唯一的外部依赖是 **7z 命令行**（7-Zip / p7zip），
压缩与解压配置包都要用它（可用环境变量 ``DSH_SYNC_7Z`` 指定其全路径）。
"""

from __future__ import annotations

import argparse
import contextlib
import fnmatch
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

TOOL = "yashi-dsh-config-sync"
FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"   #: 包内清单文件名（导出/导入两侧共用）
HOME_DIR = "dsh-home"             #: 包内对应 $DSH_HOME 的目录名
AGENTS_DIR = "agents-home"        #: 包内对应 ~/.agents 的目录名
TUI_DIR = "tui-home"              #: 包内对应 ~/.dsh-tui 的目录名
SCOPE_FILE = "scope.json"         #: 包内记录本次导出范围的清单
ENV_INVENTORY_FILE = "env-inventory.txt"   #: 包内「环境变量清单」（只有名字）
HOME_PREFIX = HOME_DIR + "/"
AGENTS_PREFIX = AGENTS_DIR + "/"
TUI_PREFIX = TUI_DIR + "/"
EXPORT_SUFFIX = ".7z"             #: 压缩包格式（7z -mx9）

# Windows 控制台默认不是 UTF-8，强制 UTF-8 输出避免中文乱码。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

# --------------------------------------------------------------------------
# 分类规则（改这里就能调整导出范围）
# --------------------------------------------------------------------------

#: 可自动重建/下载：任何位置出现这些目录名都跳过。
REBUILDABLE_DIRS = {
    "node_modules",
    ".dsh-module-fallback",
    ".pnpm-store",
    ".pnpm",
    "__pycache__",
    ".cache",
    "cache",
    ".dsh-market",        # 插件市场缓存（discovery 索引等）
    "compile-cache",      # dsh-tui 的编译缓存（本机实测 26MB）
}

#: 可自动重建/生成的单个文件。
REBUILDABLE_FILES = {
    "pnpm-lock.yaml",
    "package-lock.json",
    "yarn.lock",
    "cordis.yml",          # 运行时由 bundle 层合成的产物
    ".DS_Store",
    "Thumbs.db",
    ".eslintcache",
}

#: 可自动重建的文件后缀。
REBUILDABLE_SUFFIXES = (".log", ".tmp", ".temp", ".pid", ".lock")

#: 敏感凭证文件（勾选 keys 类别才会包含，默认绝不导出）。
SENSITIVE_FILES = {
    ".credentials.yaml",
    "credentials.yaml",
    "credentials.json",
    ".env",
    ".env.local",
    ".env.production",
    ".netrc",
    "_netrc",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
}

#: 敏感文件后缀。
SENSITIVE_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".jks", ".keystore", ".ppk")

#: 文件名含这些片段即视为凭证（大小写不敏感）。
SENSITIVE_HINTS = ("secret", "password", "passwd", "api_key", "apikey", "access_token", "private_key")

#: profile 目录里的配置文件白名单（其余文件按「插件/未知」处理）。
PROFILE_CONFIG_FILES = {"package.json", "cordis.patch.yml", "pnpm-workspace.yaml", ".npmrc"}

PROFILES_DIR = "profiles"

#: 环境变量引用扫描：settings.yaml / cordis.patch.yml 里常见的几种写法。
#: 每个模式只捕获「变量名」本身（键名一律用非捕获组，避免把 apiKeyEnv 当成变量）。
ENV_REF_PATTERNS = (
    re.compile(r"apiKeyEnv\s*:\s*([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"(?:[A-Za-z_][A-Za-z0-9_]*Env)\s*:\s*([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"process\.env\.([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"process\.env\[\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']\s*\]"),
    re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}"),
    re.compile(r"env\.([A-Z][A-Z0-9_]{2,})"),
)

#: 明显不是 dsh 相关、也不是环境变量的通用变量名，扫描时忽略。
ENV_NAME_IGNORE = {"PATH", "HOME", "TMP", "TEMP", "SHELL", "PWD", "USER", "OS", "LANG"}


# --------------------------------------------------------------------------
# 范围类别（scope）：用户 / AI 可自由勾选的导入导出范围
# --------------------------------------------------------------------------
#
# 设计：把「导什么」拆成一组带 id 的类别，导出、导入、sync、GUI 选择器共用同一份
# 目录（SCOPE_CATALOG）。新增一类内容只需在这里加一条 + 在 category_of() 里给出
# 归属规则，其余链路（清单、报告、GUI、--scope 解析、导入过滤）自动跟上。
#
# 字段：
#   id      命令行 / scope.json 里用的标识，稳定不变
#   group   分组（见 SCOPE_GROUPS），GUI 按组折叠显示
#   label   GUI 与报告里的中文名
#   desc    一句话说明「里面到底是什么」，同时给 AI 与用户看
#   default 导出时默认是否勾选（导入侧默认全选包内已有的类别）
#   danger  True 表示敏感或易踩坑，GUI 里会额外确认
#   restore 导入时是否真的写回磁盘；False 表示只随包携带（信息类）
#   globs   代表性路径（给 AI 与 --print 报告用，不参与匹配）

SCOPE_GROUPS = {
    "config": "配置（默认带）",
    "assets": "素材包（用户资源，体积大）",
    "data": "运行数据（会话 / 缓存 / 回收站）",
    "keys": "凭证 / KEY（敏感）",
    "other": "其它（外部目录 / 可重建）",
}

#: 范围类别目录（顺序即 GUI 显示顺序）。
SCOPE_CATALOG: list[dict] = [
    # ---------------- 配置 ----------------
    {"id": "settings", "group": "config", "label": "主设置", "default": True, "restore": True,
     "desc": "settings.yaml（0.2.0 起 dsh 多已把它导入并改名为 settings.yaml.imported）",
     "globs": ["settings.yaml", "settings.yaml.imported"]},
    {"id": "instructions", "group": "config", "label": "用户全局指令", "default": True, "restore": True,
     "desc": "AGENTS.md / CLAUDE.md（含 .local.md 变体）——全局行为规则",
     "globs": ["AGENTS.md", "CLAUDE.md"]},
    {"id": "presets", "group": "config", "label": "自定义 agent 预设", "default": True, "restore": True,
     "desc": ".agent-presets/ 里用户自建的 agent 预设（递归）",
     "globs": [".agent-presets/**"]},
    {"id": "identity", "group": "config", "label": "身份标识", "default": True, "restore": True,
     "desc": ".anonymous-user-id（匿名统计标识，纯本机身份，不含隐私）",
     "globs": [".anonymous-user-id"]},
    {"id": "profiles", "group": "config", "label": "profile 配置", "default": True, "restore": True,
     "desc": "各 profile 的 package.json、cordis.patch.yml（0.2.0 起真正的设置源）、"
             "pnpm-workspace.yaml、.npmrc、pnpm.cmd",
     "globs": ["profiles/*/package.json", "profiles/*/cordis.patch.yml"]},
    {"id": "plugin-configs", "group": "config", "label": "插件配置文件", "default": True, "restore": True,
     "desc": "插件自己的配置与状态：根目录顶层 *.json/*.yaml（pet.json、dsh-ssh.json、"
             "dsh-cloud-sync/ 等）以及插件配置子目录，运行时自动推断，报告里用 * 标注",
     "globs": ["*.json", "*.yaml", "dsh-*/**"]},

    # ---------------- 素材包 ----------------
    {"id": "themes", "group": "assets", "label": "主题包 / 皮肤", "default": False, "restore": True,
     "desc": "skins/ 下的皮肤包、skin-center/ 壁纸、skin-center-active.json（当前选中）"
             "——本机实测 57 个包约 85MB",
     "globs": ["skins/**", "skin-center/**", "skin-center-active.json"]},
    {"id": "pets", "group": "assets", "label": "宠物包", "default": False, "restore": True,
     "desc": "pets/ 下的宠物包与 pet.json（当前宠物配置）——本机实测约 380MB，"
             "是配置包体积的主要来源",
     "globs": ["pets/**", "pet.json"]},
    {"id": "memes", "group": "assets", "label": "表情包", "default": False, "restore": True,
     "desc": "meme-packs/ 表情图库、dsh-expression.json、表情目录缓存——本机实测约 7MB",
     "globs": ["meme-packs/**", "dsh-expression.json", "dsh-meme-catalog-cache.json"]},

    # ---------------- 运行数据 ----------------
    {"id": "sessions", "group": "data", "label": "会话正文", "default": False, "restore": True,
     "desc": "sessions/<工作目录>/<会话 id>/session.jsonl.zstd 全部聊天记录——本机实测约 57MB",
     "globs": ["sessions/**"]},
    {"id": "session-list", "group": "data", "label": "会话列表（元数据）", "default": False,
     "restore": True,
     "desc": "storages/session_projcache.json：会话 id、标题、工作目录、创建时间、统计。"
             "只想要「会话列表」不想要几百 MB 聊天正文时勾这个",
     "globs": ["storages/session_projcache.json"]},
    {"id": "storages", "group": "data", "label": "其它 KV 持久化", "default": False, "restore": True,
     "desc": "storages/ 里除会话列表外的存储单元（workspace.json、cost-meter/、whale.json 等）",
     "globs": ["storages/**"]},
    {"id": "attachments", "group": "data", "label": "附件字节", "default": False, "restore": True,
     "desc": "attachments/ 里图片等附件实体——本机实测约 21MB",
     "globs": ["attachments/**"]},
    {"id": "data-extra", "group": "data", "label": "其它运行数据", "default": False, "restore": True,
     "desc": "task-board/（任务板）、dsh-usage/（用量账本）、dsh-session-archive/（归档会话）、"
             "trash/（回收站）、llm-deepseek/（上传缓存）、logs/、sentinel.lease",
     "globs": ["task-board/**", "dsh-usage/**", "dsh-session-archive/**", "trash/**"]},

    # ---------------- 凭证 / KEY ----------------
    {"id": "keys", "group": "keys", "label": "凭证与 KEY（危险）", "default": False, "danger": True,
     "restore": True,
     "desc": ".credentials.yaml、.env*、*.key/*.pem/*.p12、含 _authToken 的 .npmrc。"
             "导出后压缩包内即为明文凭据，拷贝/上传都要当作机密处理",
     "globs": [".credentials.yaml", "*.pem", ".npmrc"]},
    {"id": "env-inventory", "group": "keys", "label": "环境变量清单（只名字）", "default": True,
     "danger": False, "restore": False,
     "desc": "扫描配置里引用了哪些环境变量（apiKeyEnv / ${VAR} / process.env.X），"
             "只导变量名与出处、不含任何值，换机器时按清单去 yashi-dsh-env-sync 补",
     "globs": ["env-inventory.txt"]},

    # ---------------- 其它 ----------------
    {"id": "skills", "group": "other", "label": "skills（~/.agents/skills）",
     "default": False, "restore": True,
     "desc": "用户全局 skill 目录，dsh 与 opencode 等共用——本机实测 95 个 skill、约 19MB",
     "globs": ["agents-home/skills/**"]},
    {"id": "agents-db", "group": "other", "label": "~/.agents 规则与索引库", "default": False,
     "restore": True,
     "desc": "~/.agents 下的 AGENTS.json（规则数据库）、SKILLS.json / SKILLS.md / SKILLS.html"
             "（skill 索引）——想让新机器索引一致时带上",
     "globs": ["agents-home/AGENTS.json", "agents-home/SKILLS.json"]},
    {"id": "local-plugins", "group": "other", "label": "本地插件目录", "default": False,
     "restore": True,
     "desc": "plugins/ 下的本地（多为软链 / file: 依赖）插件；导出软链清单与真实文件，"
             "新机器按清单自行重建链接",
     "globs": ["plugins/**"]},
    {"id": "npm-plugins", "group": "other", "label": "npm 插件包本体（node_modules）",
     "default": False, "restore": True,
     "desc": "profiles/*/node_modules 里真正装好的插件包——本机实测 web 475MB + desktop 266MB。"
             "默认不带（pnpm install 可重建）；只有「离线装机、新机器不能联网」才需要",
     "globs": ["profiles/*/node_modules/**"]},
    {"id": "lockfiles", "group": "other", "label": "锁文件", "default": False, "restore": True,
     "desc": "pnpm-lock.yaml 等——带上后 pnpm install 可直接复用本机 store，实测零下载；"
             "不带则由 package.json 重新解析（版本可能漂移）",
     "globs": ["profiles/*/pnpm-lock.yaml"]},
    {"id": "backups", "group": "other", "label": "历史备份文件", "default": False, "restore": True,
     "desc": "*.bak* / .backup-* 这类同步或迁移留下的旧副本（cordis.patch.yml.bak-…、"
             "AGENTS.md.bak-…），一般不必带",
     "globs": ["**/*.bak*", "profiles/*/.backup-*"]},
    {"id": "tui-state", "group": "other", "label": "TUI 状态（~/.dsh-tui）", "default": False,
     "restore": True,
     "desc": "dsh-tui 自己的状态与历史目录（不在 $DSH_HOME 里）——本机实测约 26MB，"
             "其中 compile-cache 会自动跳过",
     "globs": ["tui-home/**"]},
    {"id": "misc", "group": "other", "label": "其它杂项文件（图标 / 安装包等）",
     "default": False, "restore": True,
     "desc": "根目录下的非配置类杂项：应用图标（gensheek.icns/ico/png）、"
             "插件安装包（dsh-cloud-sync/releases/*.tgz）等二进制资源",
     "globs": ["gensheek.*", "*.tgz", "*.zip"]},
]

#: id → 类别定义。
SCOPE_BY_ID: dict[str, dict] = {c["id"]: c for c in SCOPE_CATALOG}

#: 导出时的默认类别（= 只要配置，不碰素材 / 数据 / 凭证）。
DEFAULT_SCOPE: list[str] = [c["id"] for c in SCOPE_CATALOG if c.get("default")]

#: 预设：AI 或用户可以用 --scope preset:<名字> 一次勾好一组。
#: 说明：preset 只是「勾选项的快捷方式」，不会替你确认危险项，keys 只在 full 里。
SCOPE_PRESETS: dict[str, list[str]] = {
    # 只搬配置，换机器后靠 pnpm install 重建依赖与素材：最小、最常用
    "config": DEFAULT_SCOPE,
    # 换机迁移：配置 + skills + 素材包 + 本地插件清单 + 锁文件（不含会话正文、不含 node_modules、不含密钥）
    "migration": DEFAULT_SCOPE + ["skills", "agents-db", "lockfiles", "local-plugins",
                                  "themes", "pets", "memes"],
    # 离线装机：连 node_modules 一起带走，新机器无需联网即可启动
    "offline": DEFAULT_SCOPE + ["skills", "agents-db", "lockfiles", "local-plugins",
                                "themes", "pets", "memes", "npm-plugins"],
    # 全量（不含凭证）：再加会话与运行数据
    "everything": DEFAULT_SCOPE + ["skills", "agents-db", "lockfiles", "local-plugins",
                                   "themes", "pets", "memes", "npm-plugins",
                                   "sessions", "session-list", "storages", "attachments",
                                   "data-extra", "tui-state"],
    # 全量 + 凭证：仅用于「把整台机器搬走」，包内为明文密钥，务必自行加密保管
    "full": DEFAULT_SCOPE + ["skills", "agents-db", "lockfiles", "local-plugins",
                             "themes", "pets", "memes", "npm-plugins",
                             "sessions", "session-list", "storages", "attachments",
                             "data-extra", "tui-state", "keys"],
}

#: 锁文件名（单独成类，便于「带 / 不带锁文件」）。
LOCKFILE_NAMES = {"pnpm-lock.yaml", "package-lock.json", "yarn.lock"}

#: 明显不是「配置」的二进制 / 素材后缀：根目录里遇到这类文件归 misc（默认不带）。
#: 反过来，未知后缀一律按「可能是插件配置」留在 plugin-configs，宁可多带不乱丢配置。
NON_CONFIG_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".icns", ".svg", ".bmp", ".tif",
    ".tgz", ".tar", ".gz", ".zip", ".7z", ".rar", ".exe", ".msi", ".dll", ".node",
    ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".wav", ".ogg", ".mp4", ".webm",
    ".pdf", ".wasm", ".db", ".sqlite", ".sqlite3",
)

#: 根目录「已知文件」→ 类别。
ROOT_FILE_CATEGORY = {
    "settings.yaml": "settings",
    "settings.yml": "settings",
    # settings.yaml.imported 是 0.2.0 导入后的残留快照，可能含旧版内联密钥，
    # 归入 backups（默认不带）；真要它用 --scope backups。
    "settings.yaml.imported": "backups",
    "AGENTS.md": "instructions",
    "CLAUDE.md": "instructions",
    "AGENTS.local.md": "instructions",
    "CLAUDE.local.md": "instructions",
    ".anonymous-user-id": "identity",
    "pet.json": "pets",
    "skin-center-active.json": "themes",
    "dsh-expression.json": "memes",
    "dsh-meme-catalog-cache.json": "memes",
    "sentinel.lease": "data-extra",
    ".credentials.yaml": "keys",
    "credentials.yaml": "keys",
    "credentials.json": "keys",
    ".env": "keys",
    ".env.local": "keys",
    ".env.production": "keys",
}

#: 根目录「已知目录」→ 类别；未列出的根目录一律归 plugin-configs（插件自建目录）。
ROOT_DIR_CATEGORY = {
    ".agent-presets": "presets",
    PROFILES_DIR: "profiles",
    "pets": "pets",
    "skins": "themes",
    "skin-center": "themes",
    "meme-packs": "memes",
    "sessions": "sessions",
    "storages": "storages",
    "attachments": "attachments",
    "task-board": "data-extra",
    "dsh-usage": "data-extra",
    "dsh-session-archive": "data-extra",
    "llm-deepseek": "data-extra",
    "trash": "data-extra",
    "logs": "data-extra",
    "log": "data-extra",
    "tmp": "data-extra",
    "plugins": "local-plugins",
    # 远端 Web UI 注册表里存的是实例 id/secret，属于凭证而不是普通插件配置
    "remote-web-ui-registry": "keys",
    # 插件更新留下的临时目录（不是配置）
    ".plugin-update-tmp": "backups",
    ".dsh-update-backup": "backups",
}

#: --with-data 等价于这些类别（兼容老参数）。
DATA_SCOPE = ["sessions", "session-list", "storages", "attachments", "data-extra", "tui-state"]

#: --with-assets 等价于这些类别。
ASSET_SCOPE = ["themes", "pets", "memes"]

#: 「容器目录」：目录自身的类别只是默认值，它的子孙可能属于别的类别。
#: 例如 storages/ 里既有「会话列表」（session-list）又有普通 KV（storages），
#: profiles/ 里既有配置也有 node_modules（npm-plugins）——只勾了子类别时也必须下去看，
#: 不能因为「目录自身类别没勾」就整棵树跳过（否则选了 session-list 会一个文件都拿不到）。
CONTAINER_CATEGORIES = {
    "storages": {"storages", "session-list"},
    "profiles": {"profiles", "plugin-configs", "npm-plugins", "lockfiles", "backups"},
    "plugin-configs": {"plugin-configs", "misc", "backups"},
}


def scope_label(cat_id: str) -> str:
    c = SCOPE_BY_ID.get(cat_id)
    return c["label"] if c else cat_id


def normalize_scope(ids) -> list[str]:
    """把类别 id 列表按目录顺序去重排序，并校验未知 id。"""
    seen: set[str] = set()
    unknown: list[str] = []
    for raw in ids or []:
        cid = str(raw).strip()
        if not cid:
            continue
        if cid in SCOPE_BY_ID:
            seen.add(cid)
        elif cid.lower() in SCOPE_PRESETS:      # 允许直接在列表里写预设名
            seen.update(SCOPE_PRESETS[cid.lower()])
        elif cid.lower() in ("all", "*", "全部"):
            seen.update(SCOPE_BY_ID)
        else:
            unknown.append(cid)
    if unknown:
        die("未知的范围类别: " + ", ".join(unknown) + "\n"
            "  可用类别: " + ", ".join(c["id"] for c in SCOPE_CATALOG) + "\n"
            "  预设: " + ", ".join(f"preset:{k}" for k in SCOPE_PRESETS) + "\n"
            "  看完整说明: python dsh_config_sync.py pick --list")
    return [c["id"] for c in SCOPE_CATALOG if c["id"] in seen]


def expand_preset(name: str) -> list[str]:
    key = name.strip().lower()
    key = key[len("preset:"):] if key.startswith("preset:") else key
    if key not in SCOPE_PRESETS:
        die(f"未知预设: {name}\n  可用: " + ", ".join(SCOPE_PRESETS))
    return normalize_scope(SCOPE_PRESETS[key])


def category_of(rel: str, is_dir: bool = False) -> str:
    """判断一个相对 $DSH_HOME 的路径属于哪个范围类别。

    导出（遍历本地文件）与导入（判断包内条目）共用本函数，保证两侧对同一路径
    的分类完全一致 —— 否则「导出的类别」与「导入能筛的类别」会对不上。
    纯字符串运算，不碰文件系统。
    """
    parts = [p for p in str(rel).replace("\\", "/").split("/") if p and p != "."]
    if not parts:
        return "plugin-configs"
    top = parts[0]
    name = parts[-1]
    low = name.lower()

    # 1) 历史备份：路径里任何一段像备份（*.bak* / *backup*）就归 backups。
    #    这类是同步、升级、迁移留下的旧副本，默认不带。
    for part in parts:
        plow = part.lower()
        if ".bak" in plow or "backup" in plow:
            return "backups"

    # 2) 锁文件（pnp-lock 之类，单独成类以便「带 / 不带」）
    if not is_dir and low in LOCKFILE_NAMES:
        return "lockfiles"

    # 3) profiles/ 内部：node_modules 是 npm 插件包本体，配置白名单算 profiles，
    #    其余（compatibility.json、.plugin-manager/、.dsh-market/…）算插件配置。
    if top == PROFILES_DIR:
        if len(parts) >= 3 and parts[2] == "node_modules":
            return "npm-plugins"
        if len(parts) <= 2:
            return "profiles"
        if parts[2] in PROFILE_CONFIG_FILES or parts[2] == "pnpm.cmd":
            return "profiles"
        return "plugin-configs"

    # 4) storages/ 内部：会话列表（session_projcache + workspace 分组）单独成类，
    #    其余是普通 KV。会话列表是「小索引」，可以只带它不带几十 MB 的会话正文。
    if top == "storages":
        if len(parts) >= 2 and (parts[1].startswith("session_projcache")
                                or parts[1] == "workspace.json"):
            return "session-list"
        return "storages"

    # 5) 根目录直接子项：查表；未列出且形似凭证的归 keys，二进制素材归 misc。
    if len(parts) == 1:
        if top in ROOT_FILE_CATEGORY:
            return ROOT_FILE_CATEGORY[top]
        if top in ROOT_DIR_CATEGORY:
            return ROOT_DIR_CATEGORY[top]
        if is_sensitive_file(name):
            return "keys"
        if not is_dir and low.endswith(NON_CONFIG_SUFFIXES):
            return "misc"
        return "plugin-configs"

    # 6) 更深的路径：继承其根目录条目的类别。
    if top in ROOT_DIR_CATEGORY:
        return ROOT_DIR_CATEGORY[top]
    if top in ROOT_FILE_CATEGORY:
        return ROOT_FILE_CATEGORY[top]
    # 未知插件目录里明显不是配置的文件（安装包、图片、sqlite 索引）归 misc
    if not is_dir and low.endswith(NON_CONFIG_SUFFIXES):
        return "misc"
    return "plugin-configs"


def scope_doc(categories, profiles=None, preset=None, mode=None, source=None) -> dict:
    """生成可序列化的范围描述（写进 scope.json，也可直接用 --scope 传回）。"""
    doc = {
        "tool": TOOL,
        "kind": "scope",
        "version": 1,
        "categories": normalize_scope(categories),
        "profiles": sorted(profiles) if profiles else None,
    }
    if preset:
        doc["preset"] = preset
    if mode:
        doc["mode"] = mode
    if source:
        doc["source"] = source
    return doc


def load_scope_doc(value) -> dict | None:
    """解析 --scope 取值：scope.json 路径 / 内联 JSON / 逗号类别 / preset:名字。

    返回 ``{"categories": [...], "profiles": [...] | None}``；None 表示未指定。
    """
    if not value:
        return None
    raw = value if isinstance(value, str) else str(value)
    text = raw.strip()
    if not text:
        return None

    # 预设
    if text.lower().startswith("preset:") or text.lower() in SCOPE_PRESETS:
        name = text.split(":", 1)[1] if ":" in text else text
        return {"categories": expand_preset(name), "profiles": None, "preset": name.strip().lower()}

    # 文件
    if not text.startswith("{"):
        p = Path(text).expanduser()
        if p.is_file():
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                die(f"--scope 文件无法解析: {p}: {e}")
            return load_scope_doc(json.dumps(doc, ensure_ascii=False))
        # 逗号分隔的类别列表
        return {"categories": normalize_scope(parse_list(value)), "profiles": None}

    # 内联 JSON
    try:
        doc = json.loads(text)
    except ValueError as e:
        die(f"--scope JSON 无法解析: {e}")
    # 允许直接传类别数组
    if isinstance(doc, list):
        return {"categories": normalize_scope(doc), "profiles": None}
    if not isinstance(doc, dict):
        die("--scope JSON 必须是对象或类别数组")
    cats = doc.get("categories") or doc.get("scope") or []
    if isinstance(cats, str):
        cats = parse_list(cats)
    return {"categories": normalize_scope(cats), "profiles": parse_profiles(doc.get("profiles")),
            "preset": doc.get("preset"), "archive": doc.get("archive")}


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------

def log(*parts) -> None:
    print(*parts, flush=True)


def warn(msg: str) -> None:
    print(f"warn: {msg}", file=sys.stderr, flush=True)


def parse_profiles(value) -> set[str] | None:
    """解析 --profiles 取值（逗号分隔，可重复给出）。

    返回 None 表示「未限定范围」＝所有 profile；空字符串同样按 None 处理。
    """
    if not value:
        return None
    names: set[str] = set()
    chunks = value if isinstance(value, (list, tuple)) else [value]
    for chunk in chunks:
        for name in str(chunk).split(","):
            name = name.strip()
            if name:
                names.add(name)
    return names or None


def parse_list(value) -> list[str]:
    """解析逗号分隔的字符串列表（可重复给出），按出现顺序去重。"""
    if not value:
        return []
    chunks = value if isinstance(value, (list, tuple)) else [value]
    out: list[str] = []
    for chunk in chunks:
        for item in str(chunk).split(","):
            item = item.strip()
            if item and item not in out:
                out.append(item)
    return out


def profile_selected(name: str, wanted: set[str] | None) -> bool:
    """wanted=None 表示全部选中。"""
    return wanted is None or name in wanted


#: 从包内条目名里取出 profile 名（形如 ``dsh-home/profiles/web/package.json``）。
_PROFILE_IN_ARCHIVE_RE = re.compile(r"(?:^|/)profiles/([^/]+)/")


def profile_of_archive_entry(entry: str) -> str | None:
    m = _PROFILE_IN_ARCHIVE_RE.search(entry)
    return m.group(1) if m else None


def profile_file_selected(entry: str, wanted: set[str] | None) -> bool:
    """包内条目是否落在选中的 profile 里；非 profiles/ 下的条目一律保留。"""
    if wanted is None:
        return True
    name = profile_of_archive_entry(entry)
    return True if name is None else profile_selected(name, wanted)


def die(msg: str, code: int = 1):
    print(f"error: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def resolve_dsh_home(cli_value: str | None = None) -> Path:
    """显式参数 > $DSH_HOME（非空白）> ~/.dsh，与 dsh-home-paths 的优先级一致。"""
    if cli_value:
        return Path(cli_value).expanduser().resolve()
    env = os.environ.get("DSH_HOME")
    if env and env.strip():
        return Path(env.strip()).expanduser().resolve()
    return (Path.home() / ".dsh").resolve()


def resolve_agents_home() -> Path:
    env = os.environ.get("DSH_AGENTS_HOME")
    if env and env.strip():
        return Path(env.strip()).expanduser().resolve()
    return (Path.home() / ".agents").resolve()


def resolve_tui_home() -> Path:
    """dsh-tui 自己的状态目录（不在 $DSH_HOME 里，默认 ~/.dsh-tui）。"""
    env = os.environ.get("DSH_TUI_HOME")
    if env and env.strip():
        return Path(env.strip()).expanduser().resolve()
    return (Path.home() / ".dsh-tui").resolve()


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def dsh_version() -> str:
    exe = which_dsh()
    if not exe:
        return "unknown"
    try:
        out = subprocess.run(_cmd_wrap(exe, ["--version"]), capture_output=True, text=True, timeout=60)
        return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr).strip() else "unknown"
    except Exception:
        return "unknown"


def which_dsh() -> str | None:
    return shutil.which("dsh") or shutil.which("dsh.cmd") or shutil.which("dsh.exe")


def which_pnpm() -> str | None:
    return shutil.which("pnpm") or shutil.which("pnpm.cmd") or shutil.which("pnpm.exe")


def _cmd_wrap(exe: str, args: list[str]) -> list[str]:
    """Windows 上 .cmd/.bat 需要通过 cmd.exe 启动。"""
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/c", exe, *args]
    return [exe, *args]


def run(exe: str, args: list[str], cwd: Path | None = None, env: dict | None = None,
        timeout: int = 900) -> tuple[int, str]:
    full = _cmd_wrap(exe, args)
    try:
        # stdin=DEVNULL：pnpm 的 approve-builds 等子命令在缺少 TTY 时会退化成交互式
        # 提问，若继承父进程 stdin 就会无限期等待输入（表现为「脚本卡死」）。关掉
        # stdin 让它直接失败，再由调用方按退出码处理。
        p = subprocess.run(full, cwd=str(cwd) if cwd else None, env=env,
                           stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s: {' '.join(full)}"
    except FileNotFoundError as e:
        return 127, f"not found: {e}"


# --------------------------------------------------------------------------
# 分类
# --------------------------------------------------------------------------

def is_sensitive_file(name: str) -> bool:
    low = name.lower()
    if low in SENSITIVE_FILES:
        return True
    if low.endswith(SENSITIVE_SUFFIXES):
        return True
    return any(h in low for h in SENSITIVE_HINTS)


def npmrc_has_credentials(path: Path) -> bool:
    """profile 的 .npmrc 可能带 _authToken / _password，那种也算凭证。"""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    low = text.lower()
    return "_authtoken" in low or "_password" in low or "_auth " in low or ":_auth=" in low


def is_rebuildable_file(name: str, opts) -> bool:
    low = name.lower()
    if low in REBUILDABLE_FILES:
        return not opts.with_lockfiles
    return low.endswith(REBUILDABLE_SUFFIXES)


class ExportOptions:
    """导出范围与开关集合（scope 是核心，其余布尔参数只为兼容旧用法）。

    ``scope`` 是类别 id 集合；``--with-agents`` / ``--with-data`` / ``--with-assets``
    / ``--with-secrets`` / ``--with-lockfiles`` 都会被翻译成对应的类别，两种写法可以混用。
    """

    def __init__(self, scope=None, with_lockfiles=False, with_secrets=False, with_data=False,
                 with_agents=False, with_assets=False, strict=False, extra_exclude=None,
                 profiles=None):
        cats = list(DEFAULT_SCOPE) if scope is None else list(scope)
        if with_agents:
            cats += ["skills"]
        if with_data:
            cats += DATA_SCOPE
        if with_assets:
            cats += ASSET_SCOPE
        if with_secrets:
            cats += ["keys"]
        if with_lockfiles:
            cats += ["lockfiles"]
        self.scope = {c for c in normalize_scope(cats) if c}
        if not self.scope:
            die("没有选中任何类别：请用 --scope 至少勾一项，"
                "或看 python dsh_config_sync.py pick --list")
        self.with_lockfiles = with_lockfiles or "lockfiles" in self.scope
        self.with_secrets = with_secrets or "keys" in self.scope
        self.with_data = with_data or bool(set(DATA_SCOPE) & self.scope)
        self.with_agents = with_agents or bool({"skills", "agents-db"} & self.scope)
        self.with_assets = with_assets or bool(set(ASSET_SCOPE) & self.scope)
        self.strict = strict
        self.extra_exclude = extra_exclude or []
        # None = 所有 profile；否则只导出集合内的 profile
        self.profiles = profiles

    def includes(self, cat: str) -> bool:
        """该类别是否被选中。"""
        return cat in self.scope

    def categories(self) -> list[str]:
        """按目录顺序返回选中的类别 id。"""
        return [c["id"] for c in SCOPE_CATALOG if c["id"] in self.scope]


def _excluded_by_user(rel: str, opts: ExportOptions) -> bool:
    return any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch("/" + rel, pat)
               for pat in opts.extra_exclude)


def known_at(rel: str) -> bool:
    """rel 是否落在脚本有明确归类规则的内容里；False = 自动推断的插件配置。

    只有「推断」类才在报告里打 ``*``，提醒用户这些是脚本猜的、可能想排除。
    """
    return category_of(rel) != "plugin-configs"


def collect(home: Path, opts: ExportOptions):
    """遍历 $DSH_HOME，返回 ``(entries, skipped, notSelected, links)``。

    entries: ``[{"rel","size","sha256","cat","known"}]``；
    skipped: ``{原因: [rel, ...]}``（可重建 / 凭证 / 用户排除 / profile / 备份）；
    notSelected: ``{类别: [rel, ...]}``（这次没勾选的类别里有什么，只留样本）；
    links: 符号链接清单 ``[{"rel","target"}]``（plugins/ 下的本地插件软链）。
    """
    entries: list[dict] = []
    skipped: dict[str, list[str]] = {"rebuildable": [], "sensitive": [], "data": [],
                                     "user-excluded": [], "profiles": [], "backup": []}
    not_selected: dict[str, list[str]] = {}
    links: list[dict] = []
    #: 每个类别只保留少量样本路径，避免报告过长
    sample_cap = 40

    def skip_cat(cat: str, rel: str, is_dir: bool = False) -> None:
        bucket = not_selected.setdefault(cat, [])
        if len(bucket) < sample_cap:
            bucket.append(rel + ("/" if is_dir else ""))

    def add_file(f: Path, rel: str, cat: str) -> None:
        name = f.name
        if _excluded_by_user(rel, opts):
            skipped["user-excluded"].append(rel)
            return
        # 凭证只在显式勾选 keys 时才收；.npmrc 含 _authToken 同样按凭证对待。
        if not opts.includes("keys") and (is_sensitive_file(name)
                                          or (name.lower() == ".npmrc" and npmrc_has_credentials(f))):
            skipped["sensitive"].append(rel)
            return
        if is_rebuildable_file(name, opts) and cat != "lockfiles":
            skipped["rebuildable"].append(rel)
            return
        try:
            size = f.stat().st_size
        except OSError:
            return
        entries.append({"rel": rel, "size": size, "sha256": sha256_of(f),
                        "cat": cat, "known": known_at(rel)})

    def walk(d: Path, prefix: str, depth: int) -> None:
        try:
            children = sorted(d.iterdir(), key=lambda p: p.name)
        except OSError as e:
            warn(f"无法读取 {d}: {e}")
            return
        for child in children:
            rel = f"{prefix}/{child.name}" if prefix else child.name
            is_dir = child.is_dir()
            cat = category_of(rel, is_dir=is_dir)

            # 符号链接：目录软链不跟随（可能指向任意位置的仓库），只把「指向哪里」
            # 记进清单；文件软链照常按内容打包（$DSH_HOME/AGENTS.md 常是指向
            # ~/.agents/AGENTS.md 的链接，跳过它会静默丢配置）。
            if child.is_symlink():
                try:
                    target = os.readlink(child)
                except OSError:
                    target = "(无法读取链接目标)"
                if child.is_dir():
                    if not opts.includes(cat):
                        skip_cat(cat, rel, True)
                    else:
                        links.append({"rel": rel, "target": str(target), "kind": "dir"})
                    continue
                if cat not in opts.scope:
                    skip_cat(cat, rel)
                    continue
                add_file(child, rel, cat)
                links.append({"rel": rel, "target": str(target), "kind": "file"})
                continue

            if is_dir:
                if _excluded_by_user(rel, opts):
                    skipped["user-excluded"].append(rel + "/")
                    continue
                if child.name in REBUILDABLE_DIRS and cat != "npm-plugins":
                    skipped["rebuildable"].append(rel + "/")
                    continue
                # 素材包的「安装残留」目录（如 pets/doro.install-<随机>）不是有效包，
                # 实测能占一半体积（本机 380MB 里 161MB 是残留），直接跳过。
                if ".install-" in child.name:
                    skipped["rebuildable"].append(rel + "/ (安装残留)")
                    continue
                if cat not in opts.scope and \
                        not (CONTAINER_CATEGORIES.get(cat, set()) & opts.scope):
                    skip_cat(cat, rel, True)
                    continue
                if opts.strict and cat == "plugin-configs" and depth == 0:
                    skipped["user-excluded"].append(rel + "/ (strict)")
                    continue
                # .backup-* 目录是派生物；只有显式选了 backups 类别才进去。
                if child.name.startswith(".backup-") and not opts.includes("backups"):
                    skipped["backup"].append(rel + "/")
                    continue
                if prefix == PROFILES_DIR and not profile_selected(child.name, opts.profiles):
                    skipped["profiles"].append(rel + "/")
                    continue
                walk(child, rel, depth + 1)
            elif child.is_file():
                if cat not in opts.scope:
                    skip_cat(cat, rel)
                    continue
                add_file(child, rel, cat)

    walk(home, "", 0)
    entries.sort(key=lambda e: e["rel"])
    links.sort(key=lambda x: x["rel"])
    return entries, skipped, not_selected, links


def scan_env_refs(files: list[tuple[Path, str]]) -> list[str]:
    """从配置文件文本里扫出被引用的环境变量名。"""
    names: set[str] = set()
    for path, _rel in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pat in ENV_REF_PATTERNS:
            for m in pat.finditer(text):
                for g in m.groups():
                    if not g or not g[0].isalpha() or len(g) < 3:
                        continue
                    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", g):
                        continue
                    if g.upper() in ENV_NAME_IGNORE or g.endswith("Env"):
                        continue
                    names.add(g)
        if path.name == "package.json":
            try:
                doc = json.loads(text)
            except Exception:
                continue
            for key in ("dsh",):
                _collect_json_env(doc.get(key), names)
    return sorted(names)


def _collect_json_env(node, names: set) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and ("apiKeyEnv" in k or k.endswith("Env")):
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v):
                    names.add(v)
            else:
                _collect_json_env(v, names)
    elif isinstance(node, list):
        for it in node:
            _collect_json_env(it, names)


def profile_summary(home: Path, profiles: set[str] | None = None) -> list[dict]:
    out = []
    pdir = home / PROFILES_DIR
    if not pdir.is_dir():
        return out
    for d in sorted(pdir.iterdir()):
        if not d.is_dir() or d.name in ("node_modules",):
            continue
        if not profile_selected(d.name, profiles):
            continue
        pkg = d / "package.json"
        info = {"name": d.name, "manifest": pkg.exists(), "bundles": [], "dependencies": {},
                "hasPatch": (d / "cordis.patch.yml").exists(),
                "hasWorkspace": (d / "pnpm-workspace.yaml").exists(),
                "nodeModules": (d / "node_modules").is_dir()}
        if pkg.exists():
            try:
                doc = json.loads(pkg.read_text(encoding="utf-8"))
                prof = (doc.get("dsh") or {}).get("profile") or {}
                info["bundles"] = prof.get("bundles") or []
                info["dependencies"] = sorted((doc.get("dependencies") or {}).keys())
            except Exception as e:
                info["manifestError"] = str(e)
        out.append(info)
    return out


# --------------------------------------------------------------------------
# 7z 与压缩包读写
# --------------------------------------------------------------------------

#: 手工指定 7z 可执行文件的路径（自动探测不到时使用）。
SEVENZIP_ENV = "DSH_SYNC_7Z"

#: zip 本地文件头魔数：旧版（zip 时代）配置包的标识，用于只读兼容。
_ZIP_MAGIC = b"PK\x03\x04"


def _sevenzip_candidates() -> list[str]:
    """7z 可执行文件的查找顺序：环境变量 > PATH > 各平台常见安装位置。"""
    cands: list[str] = []
    env = os.environ.get(SEVENZIP_ENV)
    if env and env.strip():
        cands.append(env.strip())
    cands += ["7z", "7za", "7zr"]                       # PATH 里的常规名字
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles"),
                     os.environ.get("ProgramW6432"),
                     os.environ.get("ProgramFiles(x86)"),
                     r"C:\ProgramData\chocolatey\bin"):  # choco/scoop 的 shim 目录
            if base:
                cands.append(str(Path(base) / "7-Zip" / "7z.exe"))
                cands.append(str(Path(base) / "7z.exe"))
    else:
        cands += ["/usr/bin/7z", "/usr/local/bin/7z", "/opt/homebrew/bin/7z",
                  "/usr/lib/p7zip/7z", "/usr/bin/7za"]
    return cands


def find_sevenzip() -> str | None:
    """定位 7z 可执行文件；返回 None 表示本机没有可用的 7z。"""
    seen: set[str] = set()
    for cand in _sevenzip_candidates():
        if not cand or cand in seen:
            continue
        seen.add(cand)
        if os.sep in cand or "/" in cand:
            if Path(cand).is_file():
                return cand
            continue
        found = shutil.which(cand)
        if found:
            return found
    return None


def require_sevenzip() -> str:
    """取得 7z 可执行文件路径，本机没有就带安装指引退出。"""
    exe = find_sevenzip()
    if exe:
        return exe
    die("找不到 7z（7-Zip / p7zip）。配置包自本版起使用 `7z -mx9` 压缩，"
        "导出与导入都需要它。\n"
        "  Windows: 安装 7-Zip（https://7-zip.org/）并把安装目录加入 PATH\n"
        "  macOS:   brew install p7zip\n"
        "  Linux:   apt install p7zip-full / dnf install p7zip\n"
        f"  也可用环境变量 {SEVENZIP_ENV} 指定 7z 可执行文件的全路径。")
    return ""  # 上一行的 die() 不会返回，仅为类型完整性保留


#: 手工指定 7zG（7-Zip 的图形模块，压缩时能显示进度窗口）路径。
SEVENZIP_GUI_ENV = "DSH_SYNC_7ZG"

#: 设成任意非空值即禁用 7zG 图形进度，一律用控制台 7z。
NO_GUI_PROGRESS_ENV = "DSH_SYNC_NO_7ZG_PROGRESS"


def find_7zg() -> str | None:
    """定位 7zG（图形进度版）；没有就返回 None，调用方回退到控制台 7z。

    7zG 与 7z 用法一致（同样的命令与开关），区别只是会弹一个带进度条的小窗，
    压缩几百 MB 素材时用户能看见「正在干活」。它需要图形会话，所以只在
    Windows 桌面会话里尝试。
    """
    if os.name != "nt":
        return None
    cands: list[str] = []
    env = os.environ.get(SEVENZIP_GUI_ENV)
    if env and env.strip():
        cands.append(env.strip())
    # 与 7z 同目录的 7zG：先看用户指定的 7z 在哪
    base = find_sevenzip()
    if base:
        sibling = Path(base).with_name("7zG.exe")
        cands.append(str(sibling))
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        root = os.environ.get(var)
        if root:
            cands.append(str(Path(root) / "7-Zip" / "7zG.exe"))
    cands.append(r"C:\ProgramData\chocolatey\bin\7zG.exe")
    seen: set[str] = set()
    for cand in cands:
        if cand and cand not in seen and Path(cand).is_file():
            seen.add(cand)
            return cand
    found = shutil.which("7zG") or shutil.which("7zg")
    return found


def archive_needs_password(path: Path) -> bool:
    """判断包是否加密（含「只加密数据」与「连文件名一起加密」两种）。

    - 连文件名加密（7z ``-mhe=on``）：不带密码连列表都打不开 → exit≠0 且提示要密码；
    - 只加密数据：能列目录，压缩方法里会出现 ``7zAES``。
    判断不出来（例如文件损坏）时返回 False，交给后续解压报错说话。
    """
    exe = find_sevenzip()
    if not exe or archive_is_legacy_zip(path):
        return False
    code, out = run(exe, ["l", "-slt", str(path.resolve())], timeout=180)
    if code == 0:
        return "7zAES" in out
    low = out.lower()
    return ("cannot open encrypted archive" in low or "enter password" in low
            or "wrong password" in low)


def read_password_file(path: str | None) -> str | None:
    """读取密码文件（首行即密码，保留其余空白原样不裁剪）。"""
    if not path:
        return None
    p = Path(path).expanduser()
    if not p.is_file():
        die(f"密码文件不存在: {p}")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        die(f"无法读取密码文件 {p}: {e}")
    pwd = text.splitlines()[0] if text else ""
    return pwd or None


def resolve_password(args) -> str | None:
    """按 --password / --password-file 取密码；两者都没给返回 None。

    注意：密码会以 ``-p<密码>`` 形式出现在 7z 的命令行里（7-Zip 不支持从 stdin
    读密码），同机同用户的进程能短暂看到。更安全的做法是用 ``--choose`` 在 GUI
    里输入（密码只留在内存），或用 ``--password-file``。
    """
    pwd = getattr(args, "password", None)
    if pwd:
        return pwd
    return read_password_file(getattr(args, "password_file", None))


def ask_password_gui(prompt: str = "请输入压缩包密码") -> str | None:
    """弹一个小输入框要密码；没有图形会话就返回 None，交给调用方回退。

    密码输入框是 ``show="*"``，且只在内存里用，不写日志、不写 scope.json。
    """
    ok, why = gui_available()
    if not ok:
        print(f"warn: 无法弹密码框（{why}）", file=sys.stderr, flush=True)
        return None
    try:
        import tkinter as tk
        from tkinter import simpledialog
    except Exception:
        return None
    root = tk.Tk()
    root.withdraw()
    try:
        pwd = simpledialog.askstring("需要密码", prompt, show="*", parent=root)
    finally:
        root.destroy()
    return pwd or None


def archive_is_legacy_zip(path: Path) -> bool:
    """按文件头判断是否为旧版 zip 包（当前导出格式是 7z，仅保留读取兼容）。"""
    try:
        with path.open("rb") as f:
            return f.read(4) == _ZIP_MAGIC
    except OSError:
        return False


def _safe_member_path(root: Path, name: str) -> Path | None:
    """把包内条目名解析成 root 下的路径；越界（``..`` / 绝对路径）时返回 None。

    交给 7z 解压时它自己会剥掉绝对路径与前导 ``..``，这里主要给旧版 zip 的
    标准库解压路径做路径穿越防护。
    """
    rel = name.replace("\\", "/").lstrip("/")
    if not rel or rel.endswith("/"):
        return None
    target = (root / rel).resolve()
    if target == root or root not in target.parents:
        return None
    return target


def extract_archive(archive: Path, dest: Path, password: str | None = None) -> int:
    """把配置包整包解压到 dest，返回解出的文件数。

    7z 包交给 7z 自己解（跨平台路径分隔符由它归一化）；旧版 zip 包用标准库解，
    逐条目做路径穿越校验（旧 zip 包不支持加密，password 对它无意义）。
    password 非 None 时以 ``-p<密码>`` 传给 7z；密码错误时 7z 以 exit=2 退出并
    报 ``Wrong password?``，这里翻译成更明确的提示。
    """
    dest.mkdir(parents=True, exist_ok=True)
    if archive_is_legacy_zip(archive):
        count = 0
        try:
            with zipfile.ZipFile(archive) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    target = _safe_member_path(dest, info.filename)
                    if target is None:
                        warn(f"跳过可疑路径: {info.filename}")
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    count += 1
        except zipfile.BadZipFile as e:
            die(f"不是有效的 zip 包: {e}")
        return count

    exe = require_sevenzip()
    args = ["x", str(archive.resolve()), f"-o{dest.resolve()}", "-aoa", "-bd", "-y"]
    if password:
        args.append(f"-p{password}")
    code, out = run(exe, args, timeout=1800)
    if code != 0:
        low = out.lower()
        if "wrong password" in low or "cannot open encrypted archive" in low:
            who = "密码不对" if password else "包已加密但没有提供密码"
            die(f"无法解压配置包：{who}。\n"
                "  用 --password <密码> 或 --password-file <文件> 重新执行；"
                "有图形会话时也会自动弹密码框。")
        die(f"7z 解压失败（exit={code}）:\n{out.strip()}")
    return sum(1 for p in dest.rglob("*") if p.is_file())


@contextlib.contextmanager
def opened_archive(path: Path, password: str | None = None):
    """解包到临时目录，产出 ``(临时根目录, manifest, 包内条目名列表)``。

    7z 命令行没有可靠的「只取单个条目到管道」方式（``-so`` 走文本管道会破坏
    非 ASCII 字节），因此统一先整包解压再读取；配置包很小，这点开销可以接受。
    条目名统一用 POSIX 风格（如 ``dsh-home/profiles/web/package.json``）。
    password 用于加密包（7z ``-p`` / ``-mhe=on``）。
    """
    if not path.is_file():
        die(f"找不到导入包: {path}")
    with tempfile.TemporaryDirectory(prefix="dsh-config-read-") as td:
        root = Path(td)
        extract_archive(path, root, password=password)
        mf = root / MANIFEST_NAME
        if not mf.is_file():
            die(f"包内缺少 {MANIFEST_NAME}（不是 yashi-dsh-config-sync 导出的包？）")
        try:
            manifest = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            die(f"包内 {MANIFEST_NAME} 无法解析: {e}")
        names = sorted(p.relative_to(root).as_posix()
                       for p in root.rglob("*") if p.is_file())
        yield root, manifest, names


def copy_package_tree(src_root: Path, prefix: str, dest: Path,
                      only: set[str] | None = None) -> list[str]:
    """把已解包的 ``src_root/prefix`` 还原到 dest，防路径穿越。

    only 非 None 时只还原该集合内的条目名（配合 --profiles 限定还原范围）。
    """
    dest = dest.resolve()
    src = src_root / prefix.rstrip("/")
    written: list[str] = []
    if not src.is_dir():
        return written
    for f in sorted(src.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(src).as_posix()
        entry = prefix + rel
        if only is not None and entry not in only:
            continue
        raw = dest / rel
        # 目标本身是软链（典型：$DSH_HOME/AGENTS.md → ~/.agents/AGENTS.md）：
        # 写回链接目标、保留软链，否则会把软链替换成独立文件，以后两份配置各改各的。
        if raw.is_symlink():
            real = Path(os.path.realpath(raw))
            if real.is_file():
                real.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, real)
                written.append(rel + f" → {real}")
                continue
        target = raw.resolve()
        if target == dest or dest not in target.parents:
            warn(f"跳过可疑路径: {entry}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
        written.append(rel)
    return written


def _link_or_copy(src: Path, dst: Path) -> None:
    """把导出源文件放进暂存目录：优先硬链接（同卷零拷贝），失败则退回复制。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def build_archive(out: Path, groups: list[tuple[str, Path, list[dict]]],
                  manifest: dict, extra_files: dict[str, str] | None = None,
                  mx: int = 9, password: str | None = None,
                  encrypt_headers: bool = True, gui_progress: bool = True) -> dict:
    """用 7-Zip 打出配置包，返回 ``{"bytes", "mx", "encrypted", "headerEncrypted", "tool"}``。

    包内结构：``manifest.json`` + 各分组目录（``dsh-home/``、``agents-home/``、
    ``tui-home/``）+ 可选的包根文件（``scope.json``、``env-inventory.txt``）。
    groups 是 ``[(包内目录名, 本机根目录, 条目列表), ...]``，只写出现在有选中文件的组。

    压缩参数：
    * ``mx`` 0-9（7z ``-mx``；0 仅存储、9 极限）。素材包多为已压缩格式，
      高压缩级别收益有限但更慢，GUI 里可选。
    * ``password`` 非空则用 AES-256 加密；``encrypt_headers=True`` 时加
      ``-mhe=on``，连文件名一起加密（否则包内路径肉眼可见，会泄露「导出了什么」）。
    * ``gui_progress=True`` 且本机有 7zG 与图形会话时，用 7zG 压缩——它会弹一个
      带进度条的小窗；否则回退到控制台 7z（输出靠返回值，不刷屏）。

    密码**只经内存传给 7z 的命令行**（7-Zip 不支持从 stdin 读密码），不写输出、
    不写 manifest、不写 scope.json。
    """
    out = out.resolve()
    out_tool = "7z"
    exe: str | None = None
    if gui_progress and not os.environ.get(NO_GUI_PROGRESS_ENV) and os.name == "nt":
        g = find_7zg()
        if g:
            ok, _why = gui_available()
            if ok:
                exe, out_tool = g, "7zG"
    if exe is None:
        exe = require_sevenzip()

    with tempfile.TemporaryDirectory(prefix="dsh-config-stage-") as td:
        stage = Path(td)
        (stage / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        members = [MANIFEST_NAME]
        for name, text in (extra_files or {}).items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            members.append(name)
        for prefix, root, entries in groups:
            if not entries:
                continue
            for e in entries:
                _link_or_copy(root / e["rel"], stage / prefix / e["rel"])
            members.append(prefix)

        out.parent.mkdir(parents=True, exist_ok=True)
        # 7z 的 a 是「追加/更新」语义：同名旧包必须先删掉，否则会残留上次的条目。
        try:
            if out.exists():
                out.unlink()
        except OSError as e:
            die(f"无法覆盖已有压缩包 {out}: {e}")

        mx = max(0, min(9, int(mx)))
        args = ["a", "-t7z", f"-mx{mx}", "-bd", "-y"]
        if password:
            args.append(f"-p{password}")
            if encrypt_headers:
                args.append("-mhe=on")
        args += [str(out), *members]
        log(f"   压缩中: {out_tool} -mx{mx}"
            + ("（AES-256 加密）" if password else "（未加密）")
            + f" → {out}（大包可能要几分钟，7zG 会显示进度窗）")
        # 7zG 的进度窗在压缩大包时才有意义；给它同样的超时上限，避免出错时挂住。
        code, text = run(exe, args, cwd=stage, timeout=1800)
        if code != 0 or not out.is_file():
            hint = ""
            if out_tool == "7zG":
                hint = ("\n  提示：7zG 出错时可能只弹了一个关掉的窗口、没有输出；"
                        "可加 --no-gui-progress 用控制台 7z 重试，看真实报错。")
            die(f"7z 压缩失败（exit={code}，{out_tool}）:\n{text.strip()}{hint}")
    return {"bytes": out.stat().st_size, "mx": mx, "encrypted": bool(password),
            "headerEncrypted": bool(password and encrypt_headers), "tool": out_tool}


# --------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------

def human_bytes(n: int) -> str:
    """把字节数压缩成便于阅读的形式（报告里用）。"""
    step = 1024.0
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < step or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= step
    return f"{n} B"


def collect_agents(opts: ExportOptions, skipped: dict, not_selected: dict):
    """收集 ~/.agents 下被选中的内容（skills / agents-db），返回 (entries, links)。"""
    ah = resolve_agents_home()
    entries: list[dict] = []
    links: list[dict] = []
    if not ah.is_dir():
        return entries, links

    def add_file(f: Path, cat: str, rel: str) -> None:
        if not opts.includes(cat):
            bucket = not_selected.setdefault(cat, [])
            if len(bucket) < 40:
                bucket.append("agents-home/" + rel)
            return
        if _excluded_by_user(rel, opts) or is_sensitive_file(f.name):
            skipped["sensitive"].append("agents-home/" + rel)
            return
        try:
            entries.append({"rel": rel, "size": f.stat().st_size, "sha256": sha256_of(f),
                            "cat": cat, "known": True})
        except OSError:
            pass

    skills = ah / "skills"
    if skills.is_dir():
        for f in sorted(skills.rglob("*")):
            if f.is_symlink():
                if opts.includes("skills"):
                    try:
                        links.append({"rel": f.relative_to(ah).as_posix(),
                                      "target": os.readlink(f),
                                      "kind": "dir" if f.is_dir() else "file"})
                    except OSError:
                        pass
                continue
            if f.is_file() and "node_modules" not in f.parts:
                add_file(f, "skills", f.relative_to(ah).as_posix())

    # ~/.agents 的规则与索引数据库（小文件，想让新机器索引一致时带上）
    for name in ("AGENTS.json", "SKILLS.json", "SKILLS.md", "SKILLS.html", "AGENTS.md"):
        f = ah / name
        if f.is_file():
            add_file(f, "agents-db", name)
    return entries, links


def collect_tui(opts: ExportOptions, skipped: dict, not_selected: dict):
    """收集 ~/.dsh-tui（dsh-tui 自己的状态目录），返回 (entries, links)。"""
    entries: list[dict] = []
    links: list[dict] = []
    if not opts.includes("tui-state"):
        return entries, links
    root = resolve_tui_home()
    if not root.is_dir():
        return entries, links
    for f in sorted(root.rglob("*")):
        if any(part in REBUILDABLE_DIRS for part in f.relative_to(root).parts[:-1]):
            continue
        if f.is_symlink():
            try:
                links.append({"rel": f.relative_to(root).as_posix(),
                              "target": os.readlink(f),
                              "kind": "dir" if f.is_dir() else "file"})
            except OSError:
                pass
            continue
        if not f.is_file() or "node_modules" in f.parts or is_sensitive_file(f.name):
            continue
        try:
            entries.append({"rel": f.relative_to(root).as_posix(),
                            "size": f.stat().st_size, "sha256": sha256_of(f),
                            "cat": "tui-state", "known": True})
        except OSError:
            pass
    return entries, links


def render_env_inventory(env_refs: list[str]) -> str:
    """生成「环境变量清单」文件内容：只有变量名，绝不含值。"""
    lines = [
        "# dsh 环境变量清单（由 yashi-dsh-config-sync 生成）",
        "# 这里只有变量名，不含任何密钥值。新机器上请用 yashi-dsh-env-sync 的 ini 恢复。",
        "",
    ]
    if env_refs:
        lines += [f"{name}" for name in env_refs]
    else:
        lines.append("#（未扫描到环境变量引用）")
    lines += ["", "# 来源：settings.yaml / cordis.patch.yml / package.json 里的",
              "#       apiKeyEnv、${VAR}、process.env.X 等写法。"]
    return "\n".join(lines) + "\n"


def _print_scope_plan(entries_by_cat: dict[str, list[dict]], not_selected: dict,
                      links: list[dict], verbose: bool = True) -> None:
    """按类别打印「将带什么 / 没带什么」，导出与 import 预演共用。"""
    for cat in SCOPE_CATALOG:
        items = entries_by_cat.get(cat["id"]) or []
        if not items:
            continue
        size = sum(e["size"] for e in items)
        danger = "  ⚠️ 敏感" if cat.get("danger") else ""
        log(f"  · {cat['label']:<18} {len(items):>6} 个文件 {human_bytes(size):>10}"
            f"   [{cat['id']}]{danger}")
        if verbose:
            for e in items[:12]:
                flag = "" if e.get("known") else " *推断"
                log(f"      {e['rel']}{flag}")
            if len(items) > 12:
                log(f"      …… 还有 {len(items) - 12} 个")
    if links:
        file_links = [x for x in links if x.get("kind") != "dir"]
        dir_links = [x for x in links if x.get("kind") == "dir"]
        log(f"  · 符号链接 {len(links)} 个（文件类 {len(file_links)} 个已按内容打包，"
            f"目录类 {len(dir_links)} 个只记录指向不复制）")
        for item in links[:10]:
            kind = "目录" if item.get("kind") == "dir" else "文件"
            log(f"      {kind} {item.get('root', HOME_DIR)}/{item['rel']} → {item['target']}")
    missing = [(c["id"], c["label"], v) for c in SCOPE_CATALOG
               for v in [not_selected.get(c["id"])] if v]
    if missing:
        log("")
        log("未选中（如需要请用 --scope 加上）:")
        for cid, label, samples in missing:
            log(f"  × {label} [{cid}]：{len(samples)} 项样本，例如 {samples[0]}")


def cmd_export(args) -> int:
    home = resolve_dsh_home(args.home)
    if not home.is_dir():
        die(f"DSH_HOME 不存在: {home}")

    scope = resolve_scope_for(args, mode="export",
                              initial=parse_list(getattr(args, "initial", None)) or None)
    profs = parse_profiles(args.profiles) or scope.get("profiles")
    # 压缩参数：命令行显式给出 > GUI 里填的 > 默认（-mx9、不加密）
    arch = scope.get("archive") or {}
    mx_cli = getattr(args, "mx", None)
    if mx_cli is not None:
        mx = max(0, min(9, int(mx_cli)))
    elif arch.get("mx") is not None:
        mx = max(0, min(9, int(arch["mx"])))
    else:
        mx = 9
    password = resolve_password(args) or scope.get("password")
    encrypt_headers = bool(arch.get("headerEncrypt", True)) if password else True
    if getattr(args, "no_encrypt_headers", False):
        encrypt_headers = False
    gui_progress = not getattr(args, "no_gui_progress", False)

    opts = ExportOptions(scope=scope["categories"], with_lockfiles=args.with_lockfiles,
                         with_secrets=args.with_secrets, with_data=args.with_data,
                         with_agents=args.with_agents,
                         with_assets=getattr(args, "with_assets", False),
                         strict=args.strict,
                         extra_exclude=args.exclude or [],
                         profiles=profs)
    entries, skipped, not_selected, links = collect(home, opts)
    agents_entries, agents_links = collect_agents(opts, skipped, not_selected)
    tui_entries, tui_links = collect_tui(opts, skipped, not_selected)
    links = [{"root": HOME_DIR, **item} for item in links]
    links += [{"root": AGENTS_DIR, **item} for item in agents_links]
    links += [{"root": TUI_DIR, **item} for item in tui_links]
    all_entries = entries + agents_entries + tui_entries

    # 按类别汇总
    by_cat: dict[str, list[dict]] = {}
    for e in all_entries:
        by_cat.setdefault(e["cat"], []).append(e)
    cat_stats = {cid: {"files": len(items), "bytes": sum(e["size"] for e in items)}
                 for cid, items in by_cat.items()}

    # 收集待扫描环境变量引用的文件（本地路径）。只扫「配置类」文件：
    # npm-plugins 里成千上万个第三方 package.json 会把 NODE_ENV / npm_package_name
    # 这类构建脚本变量全算成 dsh 的环境变量引用，报告会被噪声淹没。
    scan_cats = {"settings", "instructions", "profiles", "plugin-configs",
                 "presets", "agents-db", "misc"}
    scan_targets: list[tuple[Path, str]] = []
    for e in entries:
        if e.get("cat") in scan_cats and e["rel"].endswith((".yaml", ".yml", ".json", ".toml")):
            p = home / e["rel"]
            if p.is_file():
                scan_targets.append((p, e["rel"]))
    if len(scan_targets) > 400:
        warn(f"待扫描的配置文件过多（{len(scan_targets)} 个），只扫前 400 个")
        scan_targets = scan_targets[:400]
    env_refs = scan_env_refs(scan_targets)

    profiles = profile_summary(home, opts.profiles)
    scope_json = scope_doc(opts.categories(), opts.profiles, scope.get("preset"), "export")
    if password:
        # 只记录「加密了」，绝不把密码本身写进任何文件
        scope_json["archive"] = {"format": "7z", "mx": mx, "encrypted": True,
                                 "headerEncrypted": encrypt_headers}
    else:
        scope_json["archive"] = {"format": "7z", "mx": mx, "encrypted": False}
    manifest = {
        "tool": TOOL,
        "formatVersion": FORMAT_VERSION,
        "createdAt": iso_now(),
        "host": {
            "os": platform.platform(),
            "python": platform.python_version(),
            "dshHome": str(home),
            "agentsHome": str(resolve_agents_home()) if agents_entries else None,
            "tuiHome": str(resolve_tui_home()) if tui_entries else None,
        },
        "dsh": {"version": dsh_version(), "cli": which_dsh()},
        "scope": scope_json,
        "categories": cat_stats,
        "profiles": profiles,
        "envVarsReferenced": env_refs,
        "fileCount": len(all_entries),
        "totalBytes": sum(e["size"] for e in all_entries),
        "files": entries,
        "agentsFiles": agents_entries,
        "tuiFiles": tui_entries,
        "links": links,
        "archive": scope_json["archive"],
        "excluded": {k: v for k, v in skipped.items() if v},
        "notSelected": {k: v for k, v in not_selected.items() if v},
        "inferredFiles": [e["rel"] for e in all_entries if not e.get("known")],
        "notes": [
            "导入后必须执行 pnpm install 补全依赖（除非导出时选了 npm-plugins 类别）。",
            "环境变量不在本包内（除非选了 keys 类别），请用 yashi-dsh-env-sync 的 ini 恢复。",
            "凭证（.credentials.yaml / *_authToken）默认不在本包内，导入后重新登录。",
            "本包范围见 scope.json；导入侧可用 --scope 只还原其中一部分。",
        ],
    }
    extras = {"scope.json": json.dumps(scope_json, ensure_ascii=False, indent=2) + "\n"}
    if opts.includes("env-inventory"):
        extras["env-inventory.txt"] = render_env_inventory(env_refs)

    if args.print or args.dry_run:
        log(f"DSH_HOME : {home}")
        log(f"dsh      : {manifest['dsh']['version']}")
        log(f"profiles : {', '.join(p['name'] for p in profiles) or '(none)'}")
        log(f"范围     : {len(opts.categories())} 个类别"
            + (f"（预设 {scope['preset']}）" if scope.get("preset") else ""))
        log(f"类别     : {', '.join(opts.categories())}")
        log(f"环境变量引用: {', '.join(env_refs) or '(none)'}")
        log(f"压缩     : -mx{mx}" + ("，AES-256 加密（含文件名）" if password and encrypt_headers
                                     else "，AES-256 加密（文件名可见）" if password else "，不加密"))
        if password:
            log("（预演不落盘；加密只在正式导出时执行）")
        log("")
        log(f"将导出 {len(all_entries)} 个文件（{human_bytes(manifest['totalBytes'])}）：")
        _print_scope_plan(by_cat, not_selected, links)
        log("")
        for k, v in manifest["excluded"].items():
            log(f"排除[{k}]: {len(v)} 项")
        if manifest["inferredFiles"]:
            log(f"* = 自动推断的插件/未知配置（{len(manifest['inferredFiles'])} 个），"
                f"如不需要可用 --exclude 排除")
        if args.print or args.dry_run:
            return 0

    out = Path(args.output).expanduser() if args.output else \
        Path.home() / f"dsh-config-export-{now_stamp()}{EXPORT_SUFFIX}"

    groups: list[tuple[str, Path, list[dict]]] = [(HOME_DIR, home, entries)]
    if agents_entries:
        groups.append((AGENTS_DIR, resolve_agents_home(), agents_entries))
    if tui_entries:
        groups.append((TUI_DIR, resolve_tui_home(), tui_entries))
    pack = build_archive(out, groups, manifest, extras, mx=mx, password=password,
                         encrypt_headers=encrypt_headers, gui_progress=gui_progress)

    log(f"✅ 导出完成: {out}")
    log(f"   文件 {len(all_entries)} 个（{human_bytes(manifest['totalBytes'])}），"
        f"压缩包 {human_bytes(pack['bytes'])}（{pack['tool']} -mx{pack['mx']}）")
    log(f"   dsh {manifest['dsh']['version']} | profiles: "
        f"{', '.join(p['name'] for p in profiles) or '(none)'}")
    log(f"   范围: {', '.join(opts.categories())}")
    if password:
        log("   🔒 包已加密：导入/查看时需要密码（--password），密码请另行保管，丢了无法恢复")
        if encrypt_headers:
            log("      文件名也已加密（-mhe=on），不带密码连包里有什么都看不到")
    if "keys" in opts.scope:
        log("   ⚠️ 已包含凭证/KEY 类别：包内是明文凭据"
            + ("（已加密，但密码泄露即等于凭据泄露）" if password else "，请自行加密保管、别上传到公共位置"))
    if not opts.includes("skills"):
        log("   提示: 未包含 ~/.agents/skills（如需一并迁移请加 --scope skills）")
    if not opts.includes("npm-plugins"):
        log("   提示: 未包含 node_modules（新机器会自动 pnpm install 重建）")
    if env_refs:
        log(f"   引用的环境变量（未导出值，需 yashi-dsh-env-sync 恢复）: {', '.join(env_refs)}")
    return 0


# --------------------------------------------------------------------------
# inspect
# --------------------------------------------------------------------------

def _resolve_archive_password(src: Path, args) -> str | None:
    """加密包要密码：优先命令行，其次弹框（有图形会话时），否则明确报错。

    只对 7z 包做探测；旧版 zip 不支持加密，直接返回 None。
    """
    if not archive_needs_password(src):
        return resolve_password(args)
    pwd = resolve_password(args)
    if pwd:
        return pwd
    pwd = ask_password_gui(f"“{src.name}” 已加密，请输入密码")
    if pwd:
        return pwd
    die(f"这个包已加密，需要密码才能打开: {src}\n"
        "  用 --password <密码> 或 --password-file <文件> 重新执行；"
        "有图形会话时会自动弹密码框。\n"
        "  （没有图形会话时，请让用户把密码告诉你，不要尝试猜测或暴力破解。）")
    return None


def cmd_inspect(args) -> int:
    src = Path(args.input).expanduser()
    pwd = _resolve_archive_password(src, args)
    with opened_archive(src, password=pwd) as (_root, m, names):
        log(f"包      : {src}")
        log(f"容器    : {'zip（旧版）' if archive_is_legacy_zip(src) else '7z'}"
            f" | {human_bytes(src.stat().st_size)}")
        arch = m.get("archive") or {}
        enc = arch.get("encrypted")
        if enc is None:
            enc = archive_needs_password(src)
        log(f"加密    : {'是' + ('（含文件名 -mhe=on）' if arch.get('headerEncrypted') else '（文件名可见）') if enc else '否'}")
        if arch.get("mx") is not None:
            log(f"压缩级别: -mx{arch['mx']}")
        log(f"工具    : {m.get('tool')} (format v{m.get('formatVersion')})")
        log(f"导出时间: {m.get('createdAt')}")
        log(f"来源机器: {m.get('host', {}).get('os')} | DSH_HOME {m.get('host', {}).get('dshHome')}")
        log(f"dsh 版本: {m.get('dsh', {}).get('version')}")
        scope = m.get("scope") or {}
        if scope.get("categories"):
            log(f"导出范围: {', '.join(scope['categories'])}")
            if scope.get("preset"):
                log(f"对应预设: {scope['preset']}")
            if scope.get("profiles"):
                log(f"profile  : {', '.join(scope['profiles'])}")
        log("")
        # 按类别汇总（老包没有 categories 就按条目名反推）
        stats = m.get("categories") or {}
        if not stats:
            inferred: dict[str, dict] = {}
            for n in names:
                if n in (MANIFEST_NAME, SCOPE_FILE, ENV_INVENTORY_FILE):
                    continue
                c = cat_of_archive_entry(n)
                s = inferred.setdefault(c, {"files": 0, "bytes": 0})
                s["files"] += 1
            stats = inferred
        log(f"包内类别 {len(stats)} 个：")
        for c in SCOPE_CATALOG:
            s = stats.get(c["id"])
            if not s:
                continue
            danger = "  ⚠️ 敏感" if c.get("danger") else ""
            log(f"  · {c['label']:<18} {s['files']:>5} 个文件"
                f"   [{c['id']}]{danger}")
        for cid, s in stats.items():
            if cid not in SCOPE_BY_ID:
                log(f"  · {cid:<18} {s.get('files', 0):>5} 个文件")
        log("")
        log("profiles:")
        for p in m.get("profiles", []):
            deps = p.get("dependencies") or []
            log(f"  - {p['name']}: bundles={p.get('bundles')} deps={len(deps)}")
            for d in deps:
                log(f"      · {d}")
        log("")
        # 老包没有 fileCount / totalBytes 字段，按实际条目兜底，避免显示 None
        fcount = m.get("fileCount")
        if fcount is None:
            fcount = len([n for n in names if n != MANIFEST_NAME])
        tbytes = m.get("totalBytes")
        if tbytes is None:
            tbytes = sum(int(e.get("size") or 0) for e in (m.get("files") or []))
        log(f"文件 {fcount} 个（{human_bytes(tbytes)}）:")
        for e in m.get("files", []):
            flag = " " if e.get("known") else "*"
            log(f"  {flag} {e['rel']} ({e['size']}B)")
        if m.get("agentsFiles"):
            log(f"~/.agents 文件 {len(m['agentsFiles'])} 个")
        if m.get("tuiFiles"):
            log(f"~/.dsh-tui 文件 {len(m['tuiFiles'])} 个")
        if m.get("links"):
            log(f"本地软链 {len(m['links'])} 个（只记录指向）")
            for item in m["links"][:10]:
                log(f"  {item.get('root', HOME_DIR)}/{item['rel']} → {item['target']}")
        if m.get("envVarsReferenced"):
            log("")
            log(f"引用的环境变量（包内不含值）: {', '.join(m['envVarsReferenced'])}")
        if m.get("excluded"):
            log("")
            log("导出时排除:")
            for k, v in m["excluded"].items():
                log(f"  {k}: {len(v)} 项")
        if m.get("notSelected"):
            log("")
            log("本次没带的类别（包内没有）:")
            for k, v in m["notSelected"].items():
                log(f"  × {scope_label(k)} [{k}]: {len(v)} 项样本")
        log("")
        log("包内实际条目:", len(names))
    return 0


# --------------------------------------------------------------------------
# import
# --------------------------------------------------------------------------

#: pnpm 生成 allowBuilds 段时留下的占位文本（要求用户手工改成布尔值）。
ALLOWBUILDS_PLACEHOLDER = "set this to true or false"

#: allowBuilds 段落内唯一合法的两种取值。
BOOL_LITERALS = {"true", "false"}


def repair_allow_builds(profile_dir: Path) -> list[str]:
    """修正 pnpm-workspace.yaml 中 allowBuilds 的非法值，返回被修正的依赖名。

    背景：pnpm 首次生成 ``allowBuilds:`` 段时会给每个待批准依赖写入占位文本
    ``set this to true or false``。用户没手工改成布尔值时，该值既不是 true 也不是
    false，pnpm 11 会判定构建脚本「未经批准」，令 ``pnpm install`` 直接以
    ``ERR_PNPM_IGNORED_BUILDS`` 退出（exit=1）。导出包会把这份「半成品」配置原样
    带走，于是每次导入都会踩到同一个坑。

    这里把非法值一律视为「同意构建」（true），与同段落中其它已批准的依赖保持一致；
    修正只写回 pnpm-workspace.yaml，不改动 package.json。返回被改写的依赖名列表，
    空列表表示无需修正（文件不存在、或取值已合法）。
    """
    ws = profile_dir / "pnpm-workspace.yaml"
    if not ws.is_file():
        return []
    try:
        # keepends=True 保留原换行与缩进，尽量做「最小改写」而不是重排 YAML。
        lines = ws.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return []

    fixed: list[str] = []
    in_block = False
    block_indent = 0
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        # 缩进回退到 allowBuilds 同级或更浅 → 段落结束。
        if in_block and indent <= block_indent:
            in_block = False
        if stripped.startswith("allowBuilds:"):
            in_block = True
            block_indent = indent
            continue
        if not in_block or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        if value.strip().strip("'\"").lower() in BOOL_LITERALS:
            continue
        head, sep, _tail = raw.partition(":")
        eol = "\n" if raw.endswith("\n") else ""
        lines[i] = head + sep + " true" + eol
        fixed.append(key.strip().strip("'\""))

    if fixed:
        try:
            ws.write_text("".join(lines), encoding="utf-8")
        except OSError:
            return []
    return fixed


#: profile 里的 pnpm.cmd 是导出时留下的包装器（形如 ``call "<npm 根>\pnpm.cmd" %*``，
#: npm 根应由 `npm config get prefix` 取得、勿写死盘符）。
#: cmd.exe 会优先命中当前目录的同名文件，所以如果该绝对路径在换机器 / pnpm 搬迁后
#: 失效，dsh 在 profile 目录里调 pnpm 就会直接「找不到命令」。
PNPM_WRAPPER_RE = re.compile(r'([A-Za-z]:[\\/][^"\r\n]*?pnpm\.cmd)', re.IGNORECASE)


def check_pnpm_wrappers(home: Path) -> list[dict]:
    """检查各 profile 的 pnpm.cmd 指向是否存在，返回坏掉的包装器信息。

    每项形如 ``{"profile", "wrapper", "target", "pnpm"}``：target 是包装器里写死的
    pnpm 路径，pnpm 是本机实际可用的 pnpm（用于给出修复建议）。指向有效的包装器
    不会出现在结果里。
    """
    bad: list[dict] = []
    pdir = home / PROFILES_DIR
    if not pdir.is_dir():
        return bad
    for d in sorted(pdir.iterdir()):
        wrapper = d / "pnpm.cmd"
        if not d.is_dir() or not wrapper.is_file():
            continue
        try:
            text = wrapper.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        m = PNPM_WRAPPER_RE.search(text)
        if not m or Path(m.group(1)).is_file():
            continue
        bad.append({"profile": d.name, "wrapper": str(wrapper),
                    "target": m.group(1), "pnpm": which_pnpm()})
    return bad


def wrapper_fix_tips(bad: list[dict]) -> list[str]:
    """把 check_pnpm_wrappers 的结果翻译成可直接执行的修复命令。"""
    tips: list[str] = []
    for item in bad:
        line = f"profiles/{item['profile']}/pnpm.cmd 指向不存在的 {item['target']}"
        real = item.get("pnpm")
        if real:
            line += f"（本机实际 pnpm: {real}）"
        tips.append(line)
        missing_dir = str(Path(item["target"]).parent)
        real_dir = str(Path(real).parent) if real else None
        if real_dir and real_dir != missing_dir:
            tips.append(f'修复：cmd.exe 执行 mklink /J "{missing_dir}" "{real_dir}"'
                        f'（junction 不需要管理员权限），或把 pnpm.cmd 里的路径改成 {real}')
        else:
            tips.append("修复：把 pnpm.cmd 里的路径改成本机真实的 pnpm 路径。")
    return tips


def install_profiles(home: Path, proxy: str | None = None, timeout: int = 1800,
                     profiles: set[str] | None = None) -> list[dict]:
    """逐 profile 补全依赖：pnpm install → approve-builds --all →（必要时）rebuild。

    profiles 非 None 时只处理集合内的 profile（导入/同步时的范围限定）。
    """
    pdir = home / PROFILES_DIR
    results: list[dict] = []
    pnpm = which_pnpm()
    env = os.environ.copy()
    if proxy:
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                  "http_proxy", "https_proxy", "all_proxy"):
            env[k] = proxy
        env["NO_PROXY"] = env.get("NO_PROXY", "127.0.0.1,localhost")
    if not pdir.is_dir():
        return results
    for d in sorted(pdir.iterdir()):
        if not d.is_dir() or d.name == "node_modules":
            continue
        if not (d / "package.json").exists():
            continue
        if not profile_selected(d.name, profiles):
            continue
        rec = {"profile": d.name, "dir": str(d), "install": None, "approve": None, "rebuild": None}
        if not pnpm:
            rec["install"] = {"exit": 127, "output": "pnpm 未找到；可改用 dsh plugin --profile <name> install"}
            results.append(rec)
            continue
        log(f"  → pnpm install @ {d}")
        fixed = repair_allow_builds(d)
        if fixed:
            rec["allowBuildsRepaired"] = fixed
            log(f"     ⚠️ 已自动修正 pnpm-workspace.yaml 的 allowBuilds 非法值: "
                f"{', '.join(fixed)} → true")
        code, out = run(pnpm, ["install"], cwd=d, env=env, timeout=timeout)
        # 兜底：pnpm 11 遇到「未批准的构建脚本」会以 ERR_PNPM_IGNORED_BUILDS 退出。
        # repair_allow_builds 已覆盖常见情形，这里再补跑一次 approve-builds 重装，
        # 应对 allowBuilds 段落缺失、或依赖自带 issue 等其它情况。
        if code != 0 and "IGNORED_BUILDS" in out:
            log(f"     ⚠️ {d.name}: 构建脚本未获批准，尝试 approve-builds --all 后重装…")
            run(pnpm, ["approve-builds", "--all"], cwd=d, env=env, timeout=600)
            code, out = run(pnpm, ["install"], cwd=d, env=env, timeout=timeout)
        rec["install"] = {"exit": code, "output": out}
        if code == 0:
            code2, out2 = run(pnpm, ["approve-builds", "--all"], cwd=d, env=env, timeout=600)
            rec["approve"] = {"exit": code2, "output": out2}
            if code2 == 0 and "no packages awaiting approval" not in out2.lower():
                log(f"  → 已批准原生模块构建，重跑 pnpm rebuild @ {d.name}")
                code3, out3 = run(pnpm, ["rebuild"], cwd=d, env=env, timeout=timeout)
                rec["rebuild"] = {"exit": code3, "output": out3}
        results.append(rec)
    return results


URL_RE = re.compile(r"http://[0-9a-zA-Z\.\-]+:(\d+)/\?token=([A-Za-z0-9_\-]+)")


def http_status(url: str, timeout: int = 10) -> int:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(urllib.request.Request(url, method="GET"), timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def kill_tree(pid: int) -> None:
    """强杀一棵进程树（Windows: taskkill /T；POSIX: killpg）。"""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, text=True)
    else:
        try:
            os.killpg(os.getpgid(pid), 9)
        except Exception:
            try:
                os.kill(pid, 9)
            except Exception:
                pass


def kill_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, text=True)
    else:
        try:
            os.kill(pid, 9)
        except Exception:
            pass


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    if not port:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0


def listener_pids(port: int) -> list[int]:
    """返回正在 LISTEN 该端口的 PID（Windows 用 netstat，其他用 lsof）。"""
    pids: list[int] = []
    try:
        if os.name == "nt":
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                                 text=True, timeout=60).stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[3].upper() == "LISTENING" and parts[1].endswith(f":{port}"):
                    try:
                        pids.append(int(parts[4]))
                    except ValueError:
                        pass
        else:
            out = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                                 capture_output=True, text=True, timeout=60).stdout
            pids = [int(x) for x in out.split() if x.strip().isdigit()]
    except Exception:
        pass
    return sorted(set(pids))


def stop_web(proc: subprocess.Popen, port: int) -> dict:
    """停止测试服务并确认端口释放。

    关键点：Windows 上 ``dsh`` 是 ``dsh.cmd``，node 是它的**孙进程**；
    只 terminate 包装器会留下孤儿 node 继续占用端口。所以先整棵树强杀，
    再用端口反查兜底。
    """
    info = {"stopped": False, "orphanPids": [], "portReleased": None}
    if proc.poll() is None:
        kill_tree(proc.pid)          # 趁 cmd.exe 还活着，一次带走整棵树
    try:
        proc.wait(timeout=15)
    except Exception:
        pass
    if port:
        deadline = time.time() + 10
        while time.time() < deadline and port_open(port):
            time.sleep(0.5)
        if port_open(port):
            for pid in listener_pids(port):
                info["orphanPids"].append(pid)
                kill_pid(pid)
            time.sleep(1)
        info["portReleased"] = not port_open(port)
    info["stopped"] = proc.poll() is not None and (info["portReleased"] is not False)
    return info


def start_and_test_web(home: Path, port: int, wait: int = 60,
                       proxy: str | None = None) -> dict:
    """启动 dsh web，验证端口与 HTTP，然后确保停止。返回诊断 dict。"""
    exe = which_dsh()
    if not exe:
        return {"ok": False, "stage": "locate", "error": "找不到 dsh 可执行文件（PATH 中没有 dsh）"}

    probe = port
    if probe:
        # 端口被占用则让 OS 选
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", probe))
            except OSError:
                warn(f"端口 {probe} 被占用，改用系统分配端口")
                probe = 0

    logfile = Path(os.environ.get("TEMP", "/tmp")) / f"dsh-config-sync-web-{now_stamp()}.log"
    env = os.environ.copy()
    env["DSH_HOME"] = str(home)
    home.mkdir(parents=True, exist_ok=True)
    if proxy:
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                  "http_proxy", "https_proxy", "all_proxy"):
            env[k] = proxy

    log(f"  → 启动 dsh web (DSH_HOME={home}, port={probe or 'auto'})")
    creationflags = 0
    kwargs = {}
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    with open(logfile, "w+", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.Popen(_cmd_wrap(exe, ["web", "--port", str(probe), "--no-open"]),
                                cwd=str(home), env=env, stdout=fh, stderr=subprocess.STDOUT,
                                creationflags=creationflags, **kwargs)
        result = {"ok": False, "pid": proc.pid, "port": probe, "log": str(logfile)}
        deadline = time.time() + wait
        url = token = None
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            fh.flush()
            try:
                text = logfile.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            m = URL_RE.search(text)
            if m:
                url, token = m.group(0), m.group(2)
                result["port"] = int(m.group(1))
                break
            time.sleep(0.5)
        fh.flush()
        text = logfile.read_text(encoding="utf-8", errors="replace") if logfile.exists() else ""
        result["output"] = text.strip()

        if not url:
            result["stage"] = "startup"
            result["error"] = "启动超时或进程提前退出，未打印带 token 的 URL"
        else:
            base = f"http://127.0.0.1:{result['port']}/"
            code_no = http_status(base)
            code_yes = http_status(f"{base}?token={token}")
            result.update({"url": url, "httpNoToken": code_no, "httpWithToken": code_yes,
                           "stage": "http"})
            result["ok"] = code_no in (401, 403) and code_yes in (200, 302, 303)

        # 停止：整棵树强杀 + 端口兜底（见 stop_web 的说明）
        result.update(stop_web(proc, result.get("port") or 0))
    try:
        logfile.unlink()
    except OSError:
        pass
    return result


def diagnose(result: dict) -> list[str]:
    """把启动失败翻译成可执行的排障建议。"""
    out = (result.get("output") or "")
    tips = []
    if "does not provide an export named" in out or "SyntaxError" in out:
        m = re.search(r"Cannot find package '([^']+)'|from '([^']+)'|does not provide an export named '([^']+)'", out)
        tips.append("侦测到 ESM 导出/语法错误 → 多半是某个插件与新版 core 不兼容。"
                    "按 yashi-dsh-update 的「问题插件」流程在 profiles/<name>/cordis.patch.yml 里 "
                    "`- id: <plugin>` + `disabled: true` 临时禁用后重测。")
    if "Cannot find module" in out or "ERR_MODULE_NOT_FOUND" in out:
        tips.append("侦测到缺模块 → 依赖没装全：在对应 profile 目录重跑 `pnpm install`，"
                    "并确认 `pnpm approve-builds --all`（原生模块需要编译）。")
    if "EADDRINUSE" in out:
        tips.append("端口被占用 → 换端口或先结束占用进程。")
    if "no such file" in out.lower() or "ENOENT" in out:
        tips.append("缺文件 → 检查 settings.yaml / cordis.patch.yml 是否被完整还原。")
    if not tips:
        tips.append("查看上方日志；常见根因见 SKILL.md 的「常见坑」表。")
    return tips


def cat_of_archive_entry(entry: str) -> str:
    """包内条目名 → 范围类别（与导出侧 category_of 对齐）。"""
    if entry.startswith(HOME_PREFIX):
        return category_of(entry[len(HOME_PREFIX):])
    if entry.startswith(AGENTS_PREFIX):
        rel = entry[len(AGENTS_PREFIX):]
        return "skills" if rel.startswith("skills/") else "agents-db"
    if entry.startswith(TUI_PREFIX):
        return "tui-state"
    return "package"


def cmd_import(args) -> int:
    src = Path(args.input).expanduser()
    target = resolve_dsh_home(args.home)

    # 加密包：先备好密码（命令行 / 密码文件 / GUI 弹框），否则连包内清单都读不到。
    pwd = _resolve_archive_password(src, args)

    # 解包临时目录只在这里存活：文件全部还原后立刻释放，不再参与后续安装/测试。
    with opened_archive(src, password=pwd) as (root, manifest, names):
        cat_of = {n: cat_of_archive_entry(n) for n in names}
        # 包内实际包含哪些类别：优先信 manifest，老包没有就按条目名反推。
        # 过滤掉本脚本不认识的类别（更新版本导出的包不该让老脚本直接失败）。
        raw_declared = list((manifest.get("categories") or {}).keys())
        unknown_declared = [c for c in raw_declared if c not in SCOPE_BY_ID]
        if unknown_declared:
            warn("包内有本脚本不认识的类别，已忽略: " + ", ".join(unknown_declared))
        declared = [c for c in raw_declared if c in SCOPE_BY_ID]
        present = normalize_scope(declared) if declared else normalize_scope(
            [c for c in {cat_of[n] for n in names} if c in SCOPE_BY_ID])

        # 范围：显式 --scope / --choose 才限制；否则「包内有什么就还原什么」
        if getattr(args, "scope", None) or getattr(args, "choose", False):
            chosen = resolve_scope_for(
                args, "import", present=present, initial=present,
                profiles=[p["name"] for p in manifest.get("profiles", [])])
        else:
            chosen = {"categories": present, "profiles": None}
        scope_set = set(chosen["categories"])
        wanted = parse_profiles(args.profiles) or parse_profiles(chosen.get("profiles"))

        files = [n for n in names
                 if n.startswith(HOME_PREFIX) and cat_of[n] in scope_set
                 and profile_file_selected(n, wanted)]
        agents = [n for n in names if n.startswith(AGENTS_PREFIX) and cat_of[n] in scope_set]
        tui = [n for n in names if n.startswith(TUI_PREFIX) and cat_of[n] in scope_set]
        skipped_cats: dict[str, int] = {}
        for n in names:
            c = cat_of[n]
            if c not in scope_set and c != "package":
                skipped_cats[c] = skipped_cats.get(c, 0) + 1

        log(f"导入源  : {src}")
        log(f"来源 dsh: {manifest.get('dsh', {}).get('version')}  ({manifest.get('createdAt')})")
        log(f"目标    : {target}")
        arch = manifest.get("archive") or {}
        if arch.get("encrypted") or pwd:
            log("加密    : 是" + ("（含文件名）" if arch.get("headerEncrypted") else ""))
        log(f"包内容  : dsh-home {len(files)} 个文件"
            + (f"，agents-home {len(agents)} 个" if agents else "")
            + (f"，tui-home {len(tui)} 个" if tui else ""))
        log(f"范围    : {', '.join(scope_set) if scope_set else '(无)'}")
        log("")
        log("profiles:")
        for p in manifest.get("profiles", []):
            if p["name"] == "node_modules":
                continue
            mark = "  " if profile_selected(p["name"], wanted) else "× "
            log(f"  {mark}{p['name']}: bundles={p.get('bundles')} deps={len(p.get('dependencies') or [])}")
        if wanted is not None:
            log(f"  范围限定: 只还原 {', '.join(sorted(wanted))}（× 标记的 profile 跳过）")
        if skipped_cats:
            log("")
            log("未选中的类别（包内有、这次不还原）: " + ", ".join(
                f"{scope_label(c)}({n} 个)" for c, n in sorted(skipped_cats.items())))
        if manifest.get("envVarsReferenced"):
            log("")
            log(f"⚠️  包内不含环境变量值，导入后请用 yashi-dsh-env-sync 恢复: "
                f"{', '.join(manifest['envVarsReferenced'])}")
        inv = root / ENV_INVENTORY_FILE
        if inv.is_file() and "env-inventory" in scope_set:
            log(f"   环境变量清单见包内 {ENV_INVENTORY_FILE}")
        links = manifest.get("links") or []
        if links and (scope_set & {"local-plugins", "skills", "tui-state", "settings",
                                   "instructions", "plugin-configs"}):
            dir_links = [x for x in links if x.get("kind") == "dir"]
            log("")
            log(f"⚠️  包内软链 {len(links)} 个：文件类内容已随包携带（链接关系需按需重建），"
                f"目录类 {len(dir_links)} 个只记录了指向:")
            for item in links[:10]:
                kind = "目录" if item.get("kind") == "dir" else "文件"
                log(f"      [{kind}] {item.get('root', HOME_DIR)}/{item['rel']} → {item['target']}")
            if len(links) > 10:
                log(f"      …… 还有 {len(links) - 10} 个")
        if scope_set & {"sessions", "storages", "data-extra", "session-list", "attachments"}:
            log("")
            log("⚠️  本次会还原会话/运行数据：请先退出正在运行的 dsh，避免与内存里的状态打架。")

        if args.dry_run:
            log("")
            log("（--dry-run：以下为将写入的文件，未做任何修改）")
            for n in files + agents + tui:
                log(f"  {n}")
            return 0

        target.mkdir(parents=True, exist_ok=True)

        # 1) 备份将被覆盖的文件（dsh-home / agents-home / tui-home 三处都备份）
        backup = Path.home() / f"dsh-config-backup-{now_stamp()}"
        backed = 0
        for prefix, dest, group in ((HOME_PREFIX, target, files),
                                    (AGENTS_PREFIX, resolve_agents_home(), agents),
                                    (TUI_PREFIX, resolve_tui_home(), tui)):
            for n in group:
                rel = n[len(prefix):]
                existing = dest / rel
                if existing.is_file():
                    b = backup / prefix / rel
                    b.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(existing, b)
                    backed += 1
        if backed:
            log(f"① 已备份 {backed} 个将被覆盖的文件 → {backup}")

        # 2) 还原（只还原选中的类别与 profile）
        written = copy_package_tree(root, HOME_PREFIX, target, only=set(files))
        log(f"② 已还原 {len(written)} 个文件 → {target}")
        if agents:
            ah = resolve_agents_home()
            aw = copy_package_tree(root, AGENTS_PREFIX, ah, only=set(agents))
            log(f"   已还原 {len(aw)} 个 ~/.agents 文件 → {ah}")
        if tui:
            th = resolve_tui_home()
            tw = copy_package_tree(root, TUI_PREFIX, th, only=set(tui))
            log(f"   已还原 {len(tw)} 个 ~/.dsh-tui 文件 → {th}")
        if inv.is_file() and "env-inventory" in scope_set:
            try:
                names_inv = [ln.strip() for ln in inv.read_text(encoding="utf-8").splitlines()
                             if ln.strip() and not ln.startswith("#")]
            except OSError:
                names_inv = []
            log(f"   包内环境变量清单: {', '.join(names_inv) or '(空)'}")

    report: dict = {"manifest": manifest, "restored": written, "backup": str(backup) if backed else None}

    # 2.5) profile 的 pnpm.cmd 包装器指向检查（导出包会带上原机器的绝对路径）
    bad_wrappers = check_pnpm_wrappers(target)
    if bad_wrappers:
        report["pnpmWrappers"] = bad_wrappers
        log("   ⚠️ 发现失效的 pnpm.cmd 包装器：")
        for tip in wrapper_fix_tips(bad_wrappers):
            log(f"      {tip}")

    # 3) 补全依赖
    if "profiles" not in scope_set:
        log("③ 跳过依赖安装（本次没有还原 profile 配置）")
    elif args.no_install:
        log("③ 跳过依赖安装（--no-install）")
    else:
        log("③ 补全各 profile 依赖 (pnpm install + approve-builds)…")
        installs = install_profiles(target, proxy=args.proxy, profiles=wanted)
        report["installs"] = installs
        for rec in installs:
            st = (rec.get("install") or {}).get("exit")
            log(f"   {rec['profile']}: pnpm install exit={st}")
            if st not in (0, None):
                tail = "\n".join(((rec.get("install") or {}).get("output") or "").strip().splitlines()[-8:])
                log(f"      {tail}")
        bad = [r["profile"] for r in installs if (r.get("install") or {}).get("exit") not in (0,)]
        if bad:
            log(f"   ⚠️ 安装失败: {', '.join(bad)} → 检查网络/代理，或 git 依赖改用 HTTPS：")
            log('      git config --global url."https://github.com/".insteadOf "git@github.com:"')

    # 4) 实测启动
    if args.no_test:
        log("④ 跳过启动测试（--no-test）")
        return 0

    log("④ 实测 dsh web 启动/停止…")
    res = start_and_test_web(target, args.port, wait=args.wait, proxy=args.proxy)
    report["webTest"] = res
    if res.get("ok"):
        log(f"   ✅ 启动成功 http://127.0.0.1:{res['port']}/")
        log(f"      无 token → {res.get('httpNoToken')}，带 token → {res.get('httpWithToken')}")
    else:
        log(f"   ❌ 启动失败（stage={res.get('stage')}）")
        if res.get("output"):
            log("   ---- 日志尾部 ----")
            for line in res["output"].splitlines()[-25:]:
                log(f"   {line}")
            log("   ------------------")
        for t in diagnose(res):
            log(f"   💡 {t}")
    stop_note = f"   测试进程已停止: {res.get('stopped')}"
    if res.get("orphanPids"):
        stop_note += f"（另清理孤儿进程 {res['orphanPids']}）"
    if res.get("portReleased") is not None:
        stop_note += f"，端口已释放: {res.get('portReleased')}"
    log(stop_note)
    return 0 if res.get("ok") else 1


# --------------------------------------------------------------------------
# sync：把一个 profile 的插件与配置同步到另一个 profile
# --------------------------------------------------------------------------

#: 参与同步的 profile 级文件（依赖与 bundle 在 package.json 里单独合并）。
SYNC_FILES = (".npmrc", "pnpm-workspace.yaml", "pnpm.cmd", "pnpm-lock.yaml")

#: 默认不搬运的 patch 条目：web 启动器专属（桌面版等由宿主托管窗口的 profile 不适用）。
SYNC_DEFAULT_SKIP = ("web-runtime",)

#: 由 Electron 应用独占、CLI 无法启动的 profile（无法做启动测试）。
SYNC_NO_CLI_PROFILES = ("desktop",)


def split_patch_entries(text: str) -> tuple[str, list[dict]]:
    """按顶层 ``- `` 切分 cordis.patch.yml，保留注释与排版（不做 YAML 序列化）。

    返回 ``(文件头注释, 条目列表)``；条目形如 ``{"id": ..., "body": ...}``，
    body 是该条目的完整文本（含其后的注释行），可直接拼回文件。
    """
    header: list[str] = []
    blocks: list[list[str]] = []
    cur: list[str] | None = None
    for line in text.splitlines(keepends=True):
        if re.match(r"^- ", line):
            if cur:
                blocks.append(cur)
            cur = [line]
        elif cur is not None:
            cur.append(line)
        else:
            header.append(line)
    if cur:
        blocks.append(cur)
    out: list[dict] = []
    for lines in blocks:
        body = "".join(lines).rstrip()
        m = re.match(r"^- id:\s*([^\s#]+)", body)
        out.append({"id": m.group(1) if m else None, "body": body})
    return "".join(header).rstrip(), out


def render_patch(header: str, entries: list[dict], added: list[dict],
                 banner: str | None = None) -> str:
    """把条目列表渲染回 patch 文本：保留头部注释，块之间空一行。"""
    chunks = [e["body"] for e in entries]
    if added:
        block: list[str] = [banner] if banner else []
        block.extend(e["body"] for e in added)
        chunks.append("\n".join(block))
    body = "\n\n".join(chunks)
    parts = [p for p in (header, body) if p]
    return "\n\n".join(parts).rstrip() + "\n"


def merge_name_lists(target: list, source: list) -> list:
    """并集：保持 target 原顺序，source 独有项追加在后。"""
    out: list[str] = []
    for name in list(target or []) + list(source or []):
        if name not in out:
            out.append(name)
    return out


def merge_package_json(target_doc: dict, source_doc: dict) -> dict:
    """把源 profile 的依赖与 bundle 并入目标 profile 的 package.json（源侧取值优先）。"""
    doc = json.loads(json.dumps(target_doc))  # 深拷贝，避免就地改动
    deps = dict(target_doc.get("dependencies") or {})
    deps.update(source_doc.get("dependencies") or {})
    if deps:
        doc["dependencies"] = {k: deps[k] for k in sorted(deps)}
    src_profile = ((source_doc.get("dsh") or {}).get("profile") or {})
    dst_profile = dict(((doc.get("dsh") or {}).get("profile") or {}))
    dst_profile["bundles"] = merge_name_lists(dst_profile.get("bundles") or [],
                                              src_profile.get("bundles") or [])
    dsh = dict(doc.get("dsh") or {})
    dsh["profile"] = dst_profile
    doc["dsh"] = dsh
    return doc


def _write_package_json(path: Path, doc: dict) -> None:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cmd_sync(args) -> int:
    home = resolve_dsh_home(args.home)
    src_name, dst_name = args.frm, args.to
    if src_name == dst_name:
        die("源 profile 与目标 profile 不能相同")
    src = home / PROFILES_DIR / src_name
    dst = home / PROFILES_DIR / dst_name
    if not (src / "package.json").is_file():
        die(f"源 profile 不存在或缺少 package.json: {src}")
    if not dst.is_dir():
        die(f"目标 profile 不存在: {dst}（该 profile 至少要被 dsh 启动过一次）")

    skip_entries = set(SYNC_DEFAULT_SKIP) | set(parse_list(args.skip_entry))
    dry = args.dry_run

    log(f"DSH_HOME: {home}")
    log(f"同步源  : {src}")
    log(f"同步目标: {dst}")
    log(f"模式    : {'预演（不写文件、不安装）' if dry else '正式同步'}")
    log("")

    # 1) 备份目标 profile 将被改动的文件
    backup = Path.home() / f"dsh-config-backup-{now_stamp()}" / f"sync-{src_name}-to-{dst_name}"
    backed: list[str] = []
    for name in ("package.json", "cordis.patch.yml", *SYNC_FILES):
        f = dst / name
        if not f.is_file():
            continue
        backed.append(name)
        if not dry:
            backup.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, backup / name)
    if backed:
        log(f"① 备份 {len(backed)} 个文件 → {backup}"
            + ("（预演，未写入）" if dry else ""))
    elif not dry:
        log("① 目标 profile 没有需要备份的同名文件（首次同步）")

    # 2) 复制 pnpm 配置（锁文件一起带过来，可让 install 直接复用 store）
    copied: list[str] = []
    for name in SYNC_FILES:
        f = src / name
        if not f.is_file():
            continue
        copied.append(name)
        if not dry:
            shutil.copy2(f, dst / name)
    log(f"② pnpm 配置: {', '.join(copied) or '(源 profile 没有)'}")

    # 3) 合并 package.json：先写「不含新 bundle」的版本，避免热重载时依赖未就绪
    try:
        src_doc = json.loads((src / "package.json").read_text(encoding="utf-8"))
        dst_doc = json.loads((dst / "package.json").read_text(encoding="utf-8"))
    except Exception as e:
        die(f"读取 package.json 失败: {e}")
    merged = merge_package_json(dst_doc, src_doc)
    deps = merged.get("dependencies") or {}
    bundles = ((merged.get("dsh") or {}).get("profile") or {}).get("bundles") or []
    log(f"③ 依赖并集: {len(deps)} 个；bundle 并集: {len(bundles)} 个")

    staged = json.loads(json.dumps(merged))
    staged_profile = dict(((staged.get("dsh") or {}).get("profile") or {}))
    staged_profile["bundles"] = ((dst_doc.get("dsh") or {}).get("profile") or {}).get("bundles") or []
    staged_dsh = dict(staged.get("dsh") or {})
    staged_dsh["profile"] = staged_profile
    staged["dsh"] = staged_dsh
    if not dry:
        _write_package_json(dst / "package.json", staged)

    # 4) 合并 cordis.patch.yml（按 id 差集追加，保留双方注释与排版）
    added: list[dict] = []
    patch_skipped: list[str] = []
    replaced: list[str] = []
    if args.no_patch:
        log("④ 跳过 cordis.patch.yml 合并（--no-patch）")
    else:
        src_patch = src / "cordis.patch.yml"
        dst_patch = dst / "cordis.patch.yml"
        if not src_patch.is_file():
            log("④ 源 profile 没有 cordis.patch.yml，仅同步插件")
        else:
            dst_text = dst_patch.read_text(encoding="utf-8") if dst_patch.is_file() else ""
            dst_header, dst_entries = split_patch_entries(dst_text)
            src_header, src_entries = split_patch_entries(src_patch.read_text(encoding="utf-8"))
            src_ids = {e["id"] for e in src_entries if e["id"]}
            dst_ids = {e["id"] for e in dst_entries if e["id"]}

            # --overwrite-entries：用源侧版本替换目标同 id 条目。
            # 只对「源里确实存在的 id」生效，避免写错名字把目标条目删掉。
            overwrite = set(parse_list(args.overwrite_entries))
            if overwrite:
                unknown = overwrite - src_ids
                if unknown:
                    patch_skipped.append(f"{', '.join(sorted(unknown))}(源 profile 无此条目，忽略)")
                overwrite &= src_ids
                kept: list[dict] = []
                for e in dst_entries:
                    if e["id"] and e["id"] in overwrite:
                        replaced.append(e["id"])
                    else:
                        kept.append(e)
                dst_entries = kept

            for entry in src_entries:
                if not entry["id"]:
                    continue
                if entry["id"] in overwrite:
                    added.append(entry)
                elif entry["id"] in dst_ids:
                    patch_skipped.append(f"{entry['id']}(目标已有)")
                elif entry["id"] in skip_entries:
                    patch_skipped.append(f"{entry['id']}(--skip-entry)")
                else:
                    added.append(entry)

            if (added or replaced) and not dry:
                banner = (f"# ── 以下条目由 dsh_config_sync sync 从 {src_name} profile 同步"
                          f"（{now_stamp()}）──")
                text = render_patch(dst_header or src_header, dst_entries, added, banner)
                dst_patch.write_text(text, encoding="utf-8")

            added_new = [e["id"] for e in added if e["id"] not in overwrite]
            log(f"④ patch: 新增 {len(added_new)} 条"
                + (f"（{', '.join(added_new)}）" if added_new else "")
                + (f"；覆盖 {len(replaced)} 条（{', '.join(replaced)}）" if replaced else "")
                + (f"；跳过 {len(patch_skipped)} 条: {', '.join(patch_skipped)}" if patch_skipped else ""))

    if dry:
        log("")
        log("（--dry-run：以上为计划，未写入任何文件）")
        return 0

    # 5) 补全目标 profile 依赖
    if args.no_install:
        log("⑤ 跳过依赖安装（--no-install）")
    else:
        log(f"⑤ 补全 {dst_name} 依赖 (pnpm install)…")
        installs = install_profiles(home, proxy=args.proxy, profiles={dst_name})
        for rec in installs:
            st = (rec.get("install") or {}).get("exit")
            log(f"   {rec['profile']}: pnpm install exit={st}")
            if st not in (0, None):
                tail = "\n".join(((rec.get("install") or {}).get("output") or "").strip().splitlines()[-8:])
                log(f"      {tail}")

    # 依赖就绪后才注册 bundle
    _write_package_json(dst / "package.json", merged)
    log(f"   已注册 bundle: {len(bundles)} 个")

    # 6) 启动测试：CLI 无法启动 desktop（由 Electron 独占），如实说明而不是假测
    if args.no_test:
        log("⑥ 跳过启动测试（--no-test）")
    elif dst_name in SYNC_NO_CLI_PROFILES:
        log(f"⑥ 跳过启动测试：profile \"{dst_name}\" 由 Electron 应用独占，"
            f"CLI 无法启动它；请重开桌面版验证")
    else:
        log("⑥ 实测 dsh web 启动/停止…")
        res = start_and_test_web(home, args.port, wait=args.wait, proxy=args.proxy)
        if res.get("ok"):
            log(f"   ✅ 启动成功 http://127.0.0.1:{res['port']}/")
        else:
            log(f"   ❌ 启动失败（stage={res.get('stage')}）")
            if res.get("output"):
                for line in res["output"].splitlines()[-25:]:
                    log(f"   {line}")
            for t in diagnose(res):
                log(f"   💡 {t}")

    log("")
    log(f"✅ 同步完成: {src_name} → {dst_name}")
    if backed:
        log(f"   回滚: 把 {backup} 里的文件拷回 {dst}")
    return 0


def cmd_test(args) -> int:
    home = resolve_dsh_home(args.home)
    log(f"DSH_HOME: {home}")
    # 先提示失效的 pnpm.cmd 包装器：它会让 dsh 在 profile 目录里调不到 pnpm，
    # 表现为插件装不上 / 启动报「找不到命令」，容易被误判成别的问题。
    for tip in wrapper_fix_tips(check_pnpm_wrappers(home)):
        log(f"⚠️ {tip}")
    res = start_and_test_web(home, args.port, wait=args.wait, proxy=args.proxy)
    if res.get("ok"):
        log(f"✅ dsh web 启动/停止正常 http://127.0.0.1:{res['port']}/"
            f" (no-token {res.get('httpNoToken')}, token {res.get('httpWithToken')})")
        return 0
    log(f"❌ 启动失败: stage={res.get('stage')} {res.get('error') or ''}")
    if res.get("output"):
        for line in res["output"].splitlines()[-25:]:
            log(f"   {line}")
    for t in diagnose(res):
        log(f"💡 {t}")
    return 1


# --------------------------------------------------------------------------
# pick：范围选择器（原生 GUI 优先，不可用时让 AI 自己提问）
# --------------------------------------------------------------------------
#
# 为什么要有它：导入/导出范围类别有二十来个，纯命令行让用户逐个背 --scope 不现实。
# 这里用标准库 tkinter 弹一个勾选窗口（零依赖、跨平台），用户勾完点确定，
# 脚本把结果以 JSON 回给调用方（AI），AI 再原样传给 export / import 的 --scope。
#
# 退出码约定（AI 据此决定下一步）：
#   0   用户在 GUI 里确认了范围，stdout 里有 SCOPE_JSON: {...}
#   130 用户点了取消
#   2   GUI 不可用（无图形会话 / 没有 tkinter）→ **AI 应改用自带提问能力**
#       （例如 DSH 的 ask_user_question / GenUI 表单），再用 pick --list 拿到的
#       类别目录拼出 scope JSON，传给 export/import 的 --scope
#   超时未操作按「取消」处理（130），不会卡住调用方。

PICK_EXIT_CANCELLED = 130
PICK_EXIT_NO_GUI = 2

#: GUI 标题后缀，按模式变化。
_PICK_TITLES = {
    "export": "导出范围",
    "import": "导入范围（只还原勾选项）",
    "sync": "同步范围",
}


def gui_available() -> tuple[bool, str]:
    """探测能否真的弹出一个 Tk 窗口；返回 (可用, 原因)。"""
    if os.environ.get("DSH_SYNC_NO_GUI"):
        return False, "环境变量 DSH_SYNC_NO_GUI 已设置"
    try:
        import tkinter
    except Exception as e:                                   # pragma: no cover
        return False, f"导入 tkinter 失败: {e}"
    if os.name != "nt" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False, "无图形会话（DISPLAY / WAYLAND_DISPLAY 都没有）"
    try:
        root = tkinter.Tk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
    except Exception as e:                                   # pragma: no cover
        return False, f"无法创建 Tk 窗口: {e}"
    return True, "ok"


def scope_catalog_doc(mode: str = "export", only=None, present=None) -> dict:
    """给 AI / GUI 用的类别目录（JSON 友好）。

    ``only``    限定只显示这些类别（AI 可以决定「这次只问哪几项」）；
    ``present`` 包内实际存在的类别（import 模式用来把「包里没有的」标出来）。
    """
    only_set = set(normalize_scope(parse_list(only))) if only else None
    present_set = set(present) if present else None
    cats = []
    for c in SCOPE_CATALOG:
        if only_set is not None and c["id"] not in only_set:
            continue
        item = {
            "id": c["id"],
            "group": c["group"],
            "groupLabel": SCOPE_GROUPS.get(c["group"], c["group"]),
            "label": c["label"],
            "desc": c["desc"],
            "default": bool(c.get("default")),
            "danger": bool(c.get("danger")),
            "restore": bool(c.get("restore", True)),
            "globs": c.get("globs") or [],
        }
        if present_set is not None:
            item["inPackage"] = c["id"] in present_set
        cats.append(item)
    return {
        "tool": TOOL,
        "kind": "scope-catalog",
        "mode": mode,
        "groups": SCOPE_GROUPS,
        "categories": cats,
        "presets": {k: normalize_scope(v) for k, v in SCOPE_PRESETS.items()},
        "default": list(DEFAULT_SCOPE),
        "usage": {
            "export": "python dsh_config_sync.py export --scope '<本 JSON 或类别列表>'",
            "import": "python dsh_config_sync.py import -i PKG --scope '<类别列表>'",
        },
        "note": ("categories 里勾中的 id 用逗号或数组传给 --scope；"
                 "danger=true 的类别要单独向用户确认，默认不要勾。"),
    }


def _pick_apply_preset(preset: str, visible: list[dict]) -> set[str]:
    """把预设展开成「可见类别里的勾选项」。"""
    ids = set(expand_preset(preset))
    return {c["id"] for c in visible if c["id"] in ids}


def run_scope_picker(mode: str = "export", only=None, present=None, initial=None,
                     timeout: int = 600, check_gui_only: bool = False,
                     profiles=None):
    """弹出范围勾选窗，返回范围 dict；用户取消返回 None。

    profiles 为「可勾选的 profile 名单」（导入时用包里的清单，导出时用本机的）。
    GUI 不可用时抛 SystemExit(2) 并打印说明，让调用方（AI）改用提问能力。
    """
    ok, why = gui_available()
    if check_gui_only:
        log(f"GUI 可用: {ok}" + ("" if ok else f"（原因: {why}）"))
        raise SystemExit(0 if ok else PICK_EXIT_NO_GUI)

    catalog = scope_catalog_doc(mode, only=only, present=present)
    visible = catalog["categories"]
    if not visible:
        die("筛选后没有任何可选项（检查 --only 的类别 id）")

    if not ok:
        # 不能弹窗：交给 AI。把「该问什么」结构化地打出来，AI 直接照着提问。
        print(f"error: 无法弹出 GUI 配置框（{why}）。", file=sys.stderr, flush=True)
        print("      请改用 AI 自己的提问能力向用户确认范围，然后这样调用：",
              file=sys.stderr, flush=True)
        print("        python dsh_config_sync.py export -o OUT.7z --scope '类别A,类别B'",
              file=sys.stderr, flush=True)
        print("      --scope 也接受 scope.json 路径或内联 JSON。类别目录见下方 SCOPE_CATALOG_JSON。",
              file=sys.stderr, flush=True)
        print("SCOPE_CATALOG_JSON: " + json.dumps(catalog, ensure_ascii=False),
              flush=True)
        raise SystemExit(PICK_EXIT_NO_GUI)

    import tkinter as tk
    from tkinter import messagebox, ttk

    # 初始勾选：显式 initial > 包内已有类别（import）> 目录默认值
    initial_ids = set(initial) if initial else None
    if initial_ids is None:
        initial_ids = {c["id"] for c in visible if c.get("default")}
        if present:
            initial_ids &= set(present) or initial_ids

    chosen: dict | None = {"value": None, "preset": None}

    root = tk.Tk()
    root.title(f"DSH {_PICK_TITLES.get(mode, '范围')} — yashi-dsh-config-sync")
    try:  # 高 DPI 下别让窗口糊成一团
        if os.name == "nt":
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root.geometry("760x780")
    root.minsize(640, 520)

    style = ttk.Style(root)
    try:
        style.configure(".", font=("Microsoft YaHei UI", 10) if os.name == "nt" else ("TkDefaultFont",))
    except Exception:
        pass

    top = ttk.Frame(root, padding=(14, 12, 14, 6))
    top.pack(fill="x")
    ttk.Label(top, text=f"选择{_PICK_TITLES.get(mode, '范围')}：勾中什么就只处理什么",
              font=(("Microsoft YaHei UI", 12, "bold") if os.name == "nt" else ("TkDefaultFont", 12, "bold"))
              ).pack(anchor="w")
    ttk.Label(top, text="未勾选的类别完全不会进包 / 不会被还原。带〔敏感〕标记的类别请格外小心。",
              foreground="#666").pack(anchor="w", pady=(2, 0))

    preset_row = ttk.Frame(root, padding=(14, 0, 14, 6))
    preset_row.pack(fill="x")
    ttk.Label(preset_row, text="预设：").pack(side="left")
    preset_names = ["自定义"] + list(SCOPE_PRESETS.keys())
    preset_var = tk.StringVar(value="自定义")
    preset_box = ttk.Combobox(preset_row, textvariable=preset_var, values=preset_names,
                              state="readonly", width=16)
    preset_box.pack(side="left")
    ttk.Label(preset_row, text="（选预设会直接改勾选状态，之后仍可手动微调）",
              foreground="#888").pack(side="left", padx=(6, 0))

    # ---- 可滚动的勾选区 ----
    body = ttk.Frame(root, padding=(14, 0, 14, 0))
    body.pack(fill="both", expand=True)
    canvas = tk.Canvas(body, borderwidth=0, highlightthickness=0)
    vbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
    inner = ttk.Frame(canvas)
    canvas.configure(yscrollcommand=vbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    vbar.pack(side="right", fill="y")
    win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

    def _on_inner_configure(_e=None):
        canvas.configure(scrollregion=canvas.bbox("all"))

    def _on_canvas_configure(e):
        canvas.itemconfigure(win_id, width=e.width)

    inner.bind("<Configure>", _on_inner_configure)
    canvas.bind("<Configure>", _on_canvas_configure)

    def _wheel(event):
        delta = -1 * int(event.delta / 120) if getattr(event, "delta", 0) else 0
        if delta:
            canvas.yview_scroll(delta, "units")

    canvas.bind_all("<MouseWheel>", _wheel)

    vars_: dict[str, tk.BooleanVar] = {}
    current_group = None
    group_frame = None
    for c in visible:
        if c["group"] != current_group:
            current_group = c["group"]
            group_frame = ttk.LabelFrame(inner, text=SCOPE_GROUPS.get(current_group, current_group),
                                         padding=(10, 6))
            group_frame.pack(fill="x", expand=True, pady=(0, 8), padx=2)
        var = tk.BooleanVar(value=c["id"] in initial_ids)
        vars_[c["id"]] = var
        row = ttk.Frame(group_frame)
        row.pack(fill="x", pady=(2, 2))
        title = c["label"] + ("  〔敏感〕" if c.get("danger") else "")
        ttk.Checkbutton(row, text=title, variable=var).pack(anchor="w")
        ttk.Label(row, text=c["desc"], foreground="#666", wraplength=640,
                  justify="left").pack(anchor="w", padx=(22, 0))
        if not c.get("restore", True):
            ttk.Label(row, text="（信息类：只随包携带，导入时不写回磁盘）",
                      foreground="#a06", wraplength=640,
                      justify="left").pack(anchor="w", padx=(22, 0))

    # ---- profile 勾选（仅当有多于 1 个 profile）----
    profile_names = list(profiles) if profiles else _pick_profiles()
    profile_vars: dict[str, tk.BooleanVar] = {}
    if len(profile_names) > 1:
        pf = ttk.LabelFrame(inner, text="profile 范围（取消勾选 = 完全不碰该 profile）",
                            padding=(10, 6))
        pf.pack(fill="x", expand=True, pady=(0, 8), padx=2)
        cols = 4
        for i, name in enumerate(profile_names):
            var = tk.BooleanVar(value=True)
            profile_vars[name] = var
            ttk.Checkbutton(pf, text=name, variable=var).grid(
                row=i // cols, column=i % cols, sticky="w", padx=(0, 18), pady=2)

    def _apply_preset(_e=None):
        name = preset_var.get()
        if name == "自定义":
            return
        try:
            picked = _pick_apply_preset(name, visible)
        except SystemExit:
            return
        for cid, var in vars_.items():
            var.set(cid in picked)
        chosen["preset"] = name

    preset_box.bind("<<ComboboxSelected>>", _apply_preset)

    # ---- 压缩与加密（仅导出模式：压缩包才谈得上 -mx 与密码）----
    arch_vars: dict[str, tk.Variable] = {}
    if mode == "export":
        af = ttk.LabelFrame(inner, text="压缩与加密（不勾密码 = 明文包）", padding=(10, 6))
        af.pack(fill="x", expand=True, pady=(0, 8), padx=2)

        mx_row = ttk.Frame(af)
        mx_row.pack(fill="x", pady=(2, 2))
        ttk.Label(mx_row, text="压缩级别 -mx：").pack(side="left")
        mx_labels = ["0  仅存储（最快）", "1  极速", "3  快速", "5  普通",
                     "7  较大", "9  极限（默认，最慢）"]
        mx_var = tk.StringVar(value=mx_labels[5])
        arch_vars["mx"] = mx_var
        ttk.Combobox(mx_row, textvariable=mx_var, values=mx_labels,
                     state="readonly", width=22).pack(side="left")
        ttk.Label(mx_row, text="素材多为已压缩格式，mx9 更慢但收益有限",
                  foreground="#888").pack(side="left", padx=(8, 0))

        enc_var = tk.BooleanVar(value=False)
        arch_vars["encrypt"] = enc_var
        ttk.Checkbutton(af, text="为压缩包设置密码（AES-256）", variable=enc_var).pack(anchor="w")

        pwd_row = ttk.Frame(af)
        pwd_row.pack(fill="x", padx=(22, 0), pady=(2, 0))
        ttk.Label(pwd_row, text="密码：").pack(side="left")
        pwd_var = tk.StringVar()
        arch_vars["password"] = pwd_var
        pwd_entry = ttk.Entry(pwd_row, textvariable=pwd_var, show="*", width=24)
        pwd_entry.pack(side="left")
        ttk.Label(pwd_row, text="  再次输入：").pack(side="left")
        pwd2_var = tk.StringVar()
        arch_vars["password2"] = pwd2_var
        pwd2_entry = ttk.Entry(pwd_row, textvariable=pwd2_var, show="*", width=24)
        pwd2_entry.pack(side="left")

        hdr_var = tk.BooleanVar(value=True)
        arch_vars["header"] = hdr_var
        hdr_cb = ttk.Checkbutton(
            af, text="同时加密文件名（-mhe=on，否则别人能直接看到包里有 .credentials.yaml）",
            variable=hdr_var)
        hdr_cb.pack(anchor="w", padx=(22, 0))

        def _toggle_pwd(*_a):
            state = "normal" if enc_var.get() else "disabled"
            for w in (pwd_entry, pwd2_entry, hdr_cb):
                try:
                    w.configure(state=state)
                except Exception:
                    pass

        enc_var.trace_add("write", _toggle_pwd)
        _toggle_pwd()

    # ---- 底部按钮 ----
    bottom = ttk.Frame(root, padding=(14, 8, 14, 12))
    bottom.pack(fill="x")

    def _select_all():
        for var in vars_.values():
            var.set(True)
        for var in profile_vars.values():
            var.set(True)
        preset_var.set("自定义")

    def _select_none():
        for var in vars_.values():
            var.set(False)
        preset_var.set("自定义")

    def _select_default():
        for cid, var in vars_.items():
            var.set(cid in set(DEFAULT_SCOPE))
        for var in profile_vars.values():
            var.set(True)
        preset_var.set("自定义")

    ttk.Button(bottom, text="全选", command=_select_all).pack(side="left")
    ttk.Button(bottom, text="全不选", command=_select_none).pack(side="left", padx=(6, 0))
    ttk.Button(bottom, text="恢复推荐（只配置）", command=_select_default).pack(side="left", padx=(6, 0))
    ttk.Button(bottom, text="取消", command=lambda: _finish(None)).pack(side="right")
    ttk.Button(bottom, text="确定", command=lambda: _confirm()).pack(side="right", padx=(0, 8))

    def _finish(value):
        chosen["value"] = value
        try:
            canvas.unbind_all("<MouseWheel>")
        except Exception:
            pass
        root.destroy()

    def _collect_archive_options():
        """读取压缩区；返回 (archive_dict, password) 或 (None, error_msg)。"""
        if not arch_vars:
            return None, None
        mx_label = str(arch_vars["mx"].get() or "9")
        mx = int(mx_label.split()[0]) if mx_label.split()[0].isdigit() else 9
        encrypt = bool(arch_vars["encrypt"].get())
        header = bool(arch_vars["header"].get())
        pwd = str(arch_vars["password"].get() or "")
        pwd2 = str(arch_vars["password2"].get() or "")
        if encrypt:
            if not pwd:
                return None, "勾了「设置密码」但密码是空的：请填写密码，或取消勾选。"
            if pwd != pwd2:
                return None, "两次输入的密码不一致，请重新输入。"
            if len(pwd) < 4:
                return None, "密码太短（至少 4 位）。"
        else:
            pwd = ""
        return {"mx": mx, "encrypt": encrypt, "headerEncrypt": header if encrypt else False}, (pwd or None)

    def _confirm():
        picked = [cid for cid, var in vars_.items() if var.get()]
        if not picked:
            messagebox.showwarning("还没选", "至少勾选一个类别，否则没有内容可处理。")
            return
        checked = [n for n, v in profile_vars.items() if v.get()]
        if profile_vars and not checked:
            messagebox.showwarning("profile 全取消了",
                                   "至少保留一个 profile，否则没有任何 profile 内容可处理。")
            return
        arch, pwd = _collect_archive_options()
        if arch is None:
            messagebox.showwarning("压缩选项有问题", pwd)
            return
        dangerous = [SCOPE_BY_ID[cid]["label"] for cid in picked
                     if SCOPE_BY_ID.get(cid, {}).get("danger")]
        if dangerous or (arch or {}).get("encrypt"):
            lines = []
            if dangerous:
                lines.append("勾选了这些敏感类别：\n  " + "\n  ".join(dangerous))
            if (arch or {}).get("encrypt"):
                lines.append("压缩包将用 AES-256 加密"
                             + ("，并加密文件名（-mhe=on）。" if arch.get("headerEncrypt")
                                else "，但文件名仍然可见。")
                             + "\n密码请自行另存，丢了无法恢复。")
            if not messagebox.askyesno("确认", "\n\n".join(lines) + "\n\n确定继续吗？"):
                return
        result = compose_pick_result(picked, checked, list(profile_vars.keys()))
        result["preset"] = chosen.get("preset")
        if arch:
            result["archive"] = arch
            if pwd:
                result["password"] = pwd      # 只留在内存，不写 scope.json
        _finish(result)

    def _timeout():
        # 超时直接收窗返回，不弹二次确认框（那会把脚本又卡住，AI 侧也会超时）
        if chosen["value"] is None:
            print(f"warn: {timeout} 秒未操作，已取消", file=sys.stderr, flush=True)
        _finish(None)

    root.bind("<Escape>", lambda _e: _finish(None))
    if timeout:
        root.after(int(timeout * 1000), _timeout)
    root.lift()
    try:
        root.attributes("-topmost", True)
        root.after(400, lambda: root.attributes("-topmost", False))
    except Exception:
        pass
    root.mainloop()

    if chosen["value"] is None:
        return None
    return chosen["value"]


def compose_pick_result(picked, profile_checked, profile_all) -> dict:
    """把 GUI 的勾选状态整理成范围 dict（纯函数，便于自测）。

    profile 全选 = 不限定（None）；只勾了一部分才写进 profiles。
    """
    profs = [n for n in profile_all if n in set(profile_checked)]
    if not profs or len(profs) == len(profile_all):
        profs = None
    return {"categories": normalize_scope(picked), "profiles": profs}


def _pick_profiles() -> list[str]:
    """列出这台机器上存在的 profile 名（供选择器显示）。"""
    home = resolve_dsh_home(None)
    names: list[str] = []
    pdir = home / PROFILES_DIR
    if pdir.is_dir():
        try:
            names = sorted(d.name for d in pdir.iterdir()
                           if d.is_dir() and d.name != "node_modules")
        except OSError:
            names = []
    return names


def cmd_pick(args) -> int:
    """pick 子命令：弹窗勾选范围，或（不可用时）输出目录让 AI 自己提问。"""
    mode = args.mode
    only = parse_list(args.only)
    present = parse_list(args.present)

    if args.list:
        doc = scope_catalog_doc(mode, only=only, present=present or None)
        text = json.dumps(doc, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).expanduser().write_text(text + "\n", encoding="utf-8")
            log(f"类别目录已写入 {Path(args.out).expanduser()}")
        else:
            log(text)
        return 0

    if args.check_gui:
        run_scope_picker(mode, only=only, check_gui_only=True)   # 内部会 SystemExit
        return 0

    try:
        picked = run_scope_picker(mode, only=only, present=present or None,
                                  initial=parse_list(getattr(args, "initial", None)) or None,
                                  timeout=args.timeout)
    except SystemExit as e:
        return int(e.code or 0)

    if picked is None:
        print("已取消（未做任何修改）", file=sys.stderr, flush=True)
        return PICK_EXIT_CANCELLED

    doc = scope_doc(picked["categories"], picked.get("profiles"),
                    picked.get("preset"), mode)
    if picked.get("archive"):
        doc["archive"] = picked["archive"]
    # 密码绝不写进 scope.json（那是要跟着包走/会被复用的文件）。
    # 独立使用 pick 时，密码只能落到一个临时文件里交给下一步，并明确告诉调用方。
    pwd = picked.get("password")
    if pwd:
        secret = (Path(tempfile.gettempdir())
                  / f"dsh-archive-password-{now_stamp()}-{os.getpid()}.txt")
        try:
            secret.write_text(pwd, encoding="utf-8")
            try:
                os.chmod(secret, 0o600)
            except OSError:
                pass
            print(f"PASSWORD_FILE: {secret}", flush=True)
            print(f"（密码未写入 scope.json；用完请删除该临时文件，或改用 "
                  f"export --choose 让密码只留在内存里）", file=sys.stderr, flush=True)
        except OSError as e:
            print(f"error: 无法写密码临时文件: {e}", file=sys.stderr, flush=True)
            return 1
    text = json.dumps(doc, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"范围已写入: {out}", file=sys.stderr, flush=True)
    else:
        # 单行 JSON 放在 stdout 最前面，AI 好解析；人看的是下面那行摘要
        print("SCOPE_JSON: " + json.dumps(doc, ensure_ascii=False), flush=True)
    summary = "、".join(scope_label(c) for c in doc["categories"])
    print(f"已选 {len(doc['categories'])} 个类别: {summary}", file=sys.stderr, flush=True)
    if doc.get("profiles"):
        print(f"限定 profile: {', '.join(doc['profiles'])}", file=sys.stderr, flush=True)
    return 0


def resolve_scope_for(args, mode: str, present=None, initial=None, profiles=None) -> dict:
    """把 --scope / --choose 统一解析成范围 dict（export / import 共用）。

    --choose 会在 GUI 里让用户勾选（GUI 不可用时以退出码 2 让 AI 改用提问能力）。
    """
    doc = load_scope_doc(getattr(args, "scope", None))
    if getattr(args, "choose", False):
        picked = run_scope_picker(mode, only=parse_list(getattr(args, "only", None)),
                                  present=present, initial=initial,
                                  profiles=profiles,
                                  timeout=int(getattr(args, "pick_timeout", 600) or 600))
        if picked is None:
            print("已取消（未做任何修改）", file=sys.stderr, flush=True)
            raise SystemExit(PICK_EXIT_CANCELLED)
        doc = picked
    if doc is None:
        return {"categories": list(DEFAULT_SCOPE), "profiles": None}
    return doc




def main() -> int:
    ap = argparse.ArgumentParser(
        prog="dsh_config_sync.py",
        description="DeepSeek Harness（dsh）配置一句话导入/导出")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("export", help="导出 dsh 配置为 7z 压缩包（-mx9）")
    pe.add_argument("-o", "--output",
                    help="输出路径（默认 ~/dsh-config-export-<时间>.7z；扩展名建议 .7z）")
    pe.add_argument("--home", help="覆盖 $DSH_HOME")
    pe.add_argument("-s", "--scope",
                    help="范围：逗号类别 / preset:名字 / scope.json 路径 / 内联 JSON。"
                         "如 --scope config,npm-plugins 或 --scope preset:migration")
    pe.add_argument("--choose", action="store_true",
                    help="弹 GUI 让用户勾选范围（GUI 不可用时退出码 2，由 AI 改用提问能力）")
    pe.add_argument("--only", help="--choose 时只显示这些类别（逗号分隔），AI 可据此精简选项")
    pe.add_argument("--pick-timeout", type=int, default=600,
                    help="--choose 弹窗等待秒数（默认 600，超时按取消处理）")
    pe.add_argument("--initial", help="--choose 时的初始勾选类别（逗号分隔）")
    pe.add_argument("--mx", type=int, choices=range(0, 10), metavar="0-9",
                    help="7z 压缩级别（0 仅存储 … 9 极限，默认 9）；GUI 里也能选")
    pe.add_argument("--password", help="为压缩包设置密码（AES-256）；会出现在命令行里，"
                                      "更安全的是用 --choose 在 GUI 输入或 --password-file")
    pe.add_argument("--password-file", help="从文件首行读密码（比 --password 少暴露）")
    pe.add_argument("--no-encrypt-headers", action="store_true",
                    help="只加密数据、不加密文件名（默认 -mhe=on 连文件名一起加密）")
    pe.add_argument("--no-gui-progress", action="store_true",
                    help="不用 7zG 的图形进度窗，改用控制台 7z")
    pe.add_argument("--print", action="store_true", help="只打印导出计划，不写文件")
    pe.add_argument("--dry-run", action="store_true", help="同 --print")
    pe.add_argument("--with-lockfiles", action="store_true", help="= 加上 lockfiles 类别")
    pe.add_argument("--with-secrets", action="store_true", help="= 加上 keys 类别（危险）")
    pe.add_argument("--with-data", action="store_true", help="= 加上运行数据类别")
    pe.add_argument("--with-agents", action="store_true", help="= 加上 skills 类别")
    pe.add_argument("--with-assets", action="store_true", help="= 加上主题/宠物/表情包类别")
    pe.add_argument("--strict", action="store_true", help="只导出已知配置，不含推断的插件配置")
    pe.add_argument("--exclude", action="append", help="额外排除的 glob（可重复）")
    pe.add_argument("--profiles", action="append",
                    help="只导出这些 profile（逗号分隔，可重复；默认全部）")
    pe.set_defaults(func=cmd_export)

    pi = sub.add_parser("inspect", help="查看导出包内容（7z 或旧版 zip）")
    pi.add_argument("-i", "--input", required=True)
    pi.add_argument("--password", help="加密包的密码（不传且有图形会话时会弹框）")
    pi.add_argument("--password-file", help="从文件首行读密码")
    pi.set_defaults(func=cmd_inspect)

    pm = sub.add_parser("import", help="导入 dsh 配置并自动补依赖、实测启动")
    pm.add_argument("-i", "--input", required=True)
    pm.add_argument("--home", help="覆盖 $DSH_HOME")
    pm.add_argument("-s", "--scope",
                    help="只还原这些范围类别（逗号 / preset / scope.json / JSON）；默认还原包内全部")
    pm.add_argument("--choose", action="store_true",
                    help="弹 GUI 让用户勾选要还原的范围")
    pm.add_argument("--only", help="--choose 时只显示这些类别（逗号分隔）")
    pm.add_argument("--pick-timeout", type=int, default=600,
                    help="--choose 弹窗等待秒数（默认 600，超时按取消处理）")
    pm.add_argument("--initial", help="--choose 时的初始勾选类别（逗号分隔）")
    pm.add_argument("--password", help="加密包的密码（不传且有图形会话时会弹框）")
    pm.add_argument("--password-file", help="从文件首行读密码")
    pm.add_argument("--dry-run", action="store_true", help="只预览不写入")
    pm.add_argument("--no-install", action="store_true", help="跳过 pnpm install")
    pm.add_argument("--no-test", action="store_true", help="跳过 dsh web 启动测试")
    pm.add_argument("--port", type=int, default=3089, help="启动测试端口（默认 3089，0=自动）")
    pm.add_argument("--wait", type=int, default=60, help="启动等待秒数（默认 60）")
    pm.add_argument("--proxy", help="为安装/测试设置 HTTP(S)_PROXY")
    pm.add_argument("--profiles", action="append",
                    help="只导入这些 profile（逗号分隔，可重复；默认全部）")
    pm.set_defaults(func=cmd_import)

    ps = sub.add_parser("sync", help="把一个 profile 的插件与配置同步到另一个 profile")
    ps.add_argument("--from", dest="frm", default="web", help="源 profile（默认 web）")
    ps.add_argument("--to", dest="to", default="desktop", help="目标 profile（默认 desktop）")
    ps.add_argument("--home", help="覆盖 $DSH_HOME")
    ps.add_argument("--dry-run", action="store_true", help="只打印计划，不写文件、不安装")
    ps.add_argument("--no-install", action="store_true", help="跳过 pnpm install")
    ps.add_argument("--no-test", action="store_true", help="跳过启动测试")
    ps.add_argument("--no-patch", action="store_true", help="只同步插件，不合并 cordis.patch.yml")
    ps.add_argument("--skip-entry", action="append",
                    help="不搬运的 patch 条目 id（可重复；默认已含 "
                         + ", ".join(SYNC_DEFAULT_SKIP) + "）")
    ps.add_argument("--overwrite-entries", action="append",
                    help="用源侧版本覆盖目标同 id 的 patch 条目（逗号分隔，可重复；"
                         "默认保留目标值，只补缺失项）")
    ps.add_argument("--port", type=int, default=3089, help="启动测试端口（默认 3089，0=自动）")
    ps.add_argument("--wait", type=int, default=60, help="启动等待秒数（默认 60）")
    ps.add_argument("--proxy", help="为安装/测试设置 HTTP(S)_PROXY")
    ps.set_defaults(func=cmd_sync)

    pt = sub.add_parser("test", help="单独实测 dsh web 启动/停止")
    pt.add_argument("--home", help="覆盖 $DSH_HOME")
    pt.add_argument("--port", type=int, default=3089)
    pt.add_argument("--wait", type=int, default=60)
    pt.add_argument("--proxy")
    pt.set_defaults(func=cmd_test)

    pp = sub.add_parser("pick", help="弹 GUI 勾选导入/导出范围（不可用时输出目录给 AI 提问）")
    pp.add_argument("--mode", choices=["export", "import", "sync"], default="export",
                    help="按哪种语义选择（影响标题与默认勾选，默认 export）")
    pp.add_argument("--list", "--json", dest="list", action="store_true",
                    help="不弹窗，打印类别目录 JSON（AI 用它自己组织提问）")
    pp.add_argument("--only", help="只显示/只输出这些类别（逗号分隔），AI 可据此精简选项")
    pp.add_argument("--present", help="包内已有的类别（逗号分隔），用来标注可选项")
    pp.add_argument("--initial", help="初始勾选（逗号分隔类别 id）")
    pp.add_argument("-o", "--out", help="把选择结果写入 scope.json（不传则打印到 stdout）")
    pp.add_argument("--timeout", type=int, default=600, help="等待用户操作秒数（默认 600）")
    pp.add_argument("--check-gui", action="store_true", help="只探测能否弹窗（可用退出 0，否则 2）")
    pp.set_defaults(func=cmd_pick)

    args = ap.parse_args()
    return args.func(args) or 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        sys.exit(130)
