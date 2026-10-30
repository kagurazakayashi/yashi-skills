#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_skills_index.py - Generate or update the global skill index.

Scans every skill directory under ~/.agents/skills/ (or --skills-dir), parses
the YAML front matter of each SKILL.md (only `name` and `description` are
needed), validates the entries, and renders outputs with different audiences
and, by default, different languages:

  SKILLS.md   (AI-oriented)    Lightweight template (--md-style ai), one
                               section per skill:
                                   ## <name>
                                   Purpose: <one sentence>
                                   Use when: - <scenario> ...
                                   Location: `~/.agents/skills/<dir>/`
                               Rendered in --md-lang (default: en / English).

  README.md   (human-oriented) Same content rules as the HTML page
                               (--md-style human): category grouping, a table of
                               contents, example prompts taken from
                               skill-examples.json (fenced, so a forge offers a
                               copy button), and a folder link per skill. Every
                               list is preceded by a blank line. A repository
                               index names its collection in the title and drops
                               the boilerplate intro.
                               A Markdown target named README.md picks this
                               style on its own, together with --md-lang
                               zh-Hans, because a README is read by people; pass
                               --md-style / --md-lang to override either one.

  SKILLS.html (human-oriented) Self-contained page with category grouping,
                               sticky navigation, client-side search, anchors,
                               and folder links. Rendered in --html-lang
                               (default: zh-Hans / Simplified Chinese).
                               Its stylesheet and script are maintained as real
                               files -- scripts/assets/skills-index.css and
                               skills-index.js -- and inlined at render time, so
                               the page itself makes no external requests. The
                               theme is a light/dark macaron palette with an
                               Auto / Light / Dark switch in the sidebar.

The output file names are both selectable: --output names the Markdown index,
--html names the page, and when only --output is given the page is written next
to it with an .html suffix. A repository that hosts its own skills therefore
builds its entry point with one flag:

  python3 scripts/build_skills_index.py --skills-dir <repo>/skills \
      --include 'yashi-*' --output <repo>/README.md --no-html

Both outputs can be limited to a subset of skills with --include (glob patterns
matched against the skill directory name, e.g. --include 'yashi-*'). A filtered
run writes to its own files -- SKILLS.<tag>.md / SKILLS.<tag>.html /
SKILLS.<tag>.json -- so building a subset can never overwrite the complete index,
and it seeds its prose from the full database (or page) so the subset comes out
translated on the very first run.

Three run modes split the work between the agent and CI:

  (default)       scan the skills, update the database and render everything. In
                  a repository that installs the workflow below the scan updates
                  the database only, because CI owns the rendered files;
  --db-only       update the database, render nothing;
  --render-only   render the stored database only -- no scan, no database write.
                  This is what the action runs;
  --ignore-ci     do not look for the workflow, render locally in any case.

The action template (templates/skills-index.yml) renders SKILLS.md (English, the
AI-facing catalogue), SKILLS.html and README.md (Simplified Chinese, for people)
from SKILLS.json and commits them, so the database is the only index file that is
edited by hand.

A JSON database drives everything (--db; by default the Markdown index path
with a .json suffix, i.e. ~/.agents/SKILLS.json). It is written first and is the
single long-lived carrier of the prose: per skill it holds the file list with
sizes and modification times, the upstream description and its hash, an English
and a Simplified-Chinese copy of the purpose and the use-when list, and the
example prompts. The Markdown index and the HTML page are then rendered from it,
so the two can no longer drift apart, and neither is read back for its wording.

Rebuilding is incremental. Every skill is compared with its database entry by
the file list and modification times (plus the description hash):

  unchanged       nothing to do; the stored prose and translation are reused;
  new             a new skill: the prose is seeded from the previous Markdown
                  index (or from the front-matter heuristic), and the other
                  languages start out untranslated;
  src-changed     SKILL.md's description changed: the source prose is
                  regenerated, and every other language is marked for review;
  source-changed  SKILL.md changed without touching the description: the prose
                  is kept and marked for review;
  files-changed   only other files in the skill folder changed: reported, but
                  the prose is kept as it is;
  rebuild         --rebuild was passed: every skill is rewritten and every
                  translation is marked for review.

A translation counts as reviewed again as soon as its text is edited -- in the
database itself or in the draft written by --emit-draft -- or when
--mark-reviewed is passed.

The database is never silently recreated: a corrupt one stops the run, because
it carries the only long-lived copy of the translations. When it does not exist
yet, the prose is seeded from the previous SKILLS.md (source language) and
SKILLS.html (the other languages), so an existing index migrates without losing
translation work.

The translation draft (--emit-draft, --draft-path) is still a scratch file: it
defaults to the system temp directory, it is written in the HTML language, and
it is removed once the database has been updated with it (--keep-draft keeps
it). Entries without localized prose fall back to the source text and are
reported as untranslated, so partial translation stays safe.

Validation issues (missing SKILL.md, missing/empty name or description, name
not matching the directory, duplicate names) are reported instead of guessed;
nothing is written while issues exist unless --force is passed.

Examples:
  python3 scripts/build_skills_index.py               # build db + md + html
  python3 scripts/build_skills_index.py --emit-draft  # write the zh draft
  python3 scripts/build_skills_index.py --html-only   # render html only
  python3 scripts/build_skills_index.py --dry-run     # preview, no writes
  python3 scripts/build_skills_index.py --check       # fail if outputs stale
  python3 scripts/build_skills_index.py --rebuild     # rewrite all prose
  python3 scripts/build_skills_index.py --html-lang en
  python3 scripts/build_skills_index.py --output README.md   # human md, zh-Hans
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import html as html_mod
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Minimal YAML front-matter parsing (no third-party dependencies)
# ---------------------------------------------------------------------------

FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


def parse_front_matter(text: str) -> dict[str, str]:
    """Parse the top-level scalar fields of a YAML front-matter block.

    Supports plain, single/double-quoted, and block (`>` / `|`) scalars.
    Nested mappings (e.g. metadata:) are ignored. Only string values are
    returned.
    """
    m = FRONT_MATTER_RE.match(text)
    if not m:
        return {}

    lines = m.group(1).split("\n")
    result: dict[str, str] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.startswith(" ") or line.startswith("\t"):
            i += 1
            continue

        key_match = re.match(r"([A-Za-z0-9_.\-]+)\s*:\s*(.*)$", line)
        if not key_match:
            i += 1
            continue

        key = key_match.group(1)
        rest = key_match.group(2).strip()

        # Quoted scalar, possibly spanning several lines.
        if rest[:1] in ("'", '"'):
            quote = rest[0]
            if rest.count(quote) >= 2 and rest.endswith(quote) and len(rest) >= 2:
                value = rest[1:-1]
                i += 1
            else:
                buf = [rest[1:]]
                i += 1
                while i < len(lines):
                    if quote in lines[i]:
                        before, _sep, _after = lines[i].partition(quote)
                        buf.append(before)
                        i += 1
                        break
                    buf.append(lines[i])
                    i += 1
                value = "\n".join(buf)
            result[key] = value
            continue

        # Block scalar (> folded, | literal), possibly with chomping indicator.
        block_match = re.match(r"([>|])([+\-]?)\s*$", rest)
        if block_match:
            style, chomp = block_match.group(1), block_match.group(2)
            block_lines: list[str] = []
            i += 1
            while i < len(lines):
                bl = lines[i]
                if bl.strip() == "":
                    block_lines.append("")
                    i += 1
                    continue
                if bl[0] in (" ", "\t"):
                    block_lines.append(bl)
                    i += 1
                    continue
                break
            result[key] = _render_block_scalar(block_lines, style, chomp)
            continue

        # Plain scalar (single line) or a nested mapping that we skip.
        result[key] = rest
        i += 1

    return result


def _render_block_scalar(block_lines: list[str], style: str, chomp: str) -> str:
    indents = [len(l) - len(l.lstrip(" ")) for l in block_lines if l.strip()]
    indent = min(indents) if indents else 1
    stripped = [l[indent:] if l.strip() else "" for l in block_lines]

    if style == "|":
        joined = "\n".join(stripped)
    else:  # folded: single newlines become spaces, blank lines stay newlines
        parts: list[str] = []
        run: list[str] = []
        for l in stripped:
            if l == "":
                if run:
                    parts.append(" ".join(run))
                    run = []
                parts.append("")
            else:
                run.append(l.strip())
        if run:
            parts.append(" ".join(run))
        joined = "\n".join(parts)
        # Do not insert spaces between adjacent CJK characters/punctuation.
        cjk = r"\u4e00-\u9fff\u3000-\u303f\uff00-\uffef"
        joined = re.sub(f"(?<=[{cjk}])[ \t]+(?=[{cjk}])", "", joined)

    joined = joined.rstrip("\n")
    if chomp == "+":
        return joined + "\n"
    if chomp == "-":
        return joined
    return joined + "\n"


# ---------------------------------------------------------------------------
# Description -> Purpose / Use-when extraction
# ---------------------------------------------------------------------------

# A sentence may open with an uppercase Latin letter (English prose) or with a
# CJK character (Chinese prose, plus its full-width openers). Without the CJK
# branch a Chinese description never splits: the lookahead fails on the next
# sentence's CJK start, so the entire description became the "purpose".
SENTENCE_RE = re.compile(
    r"\S.+?(?<=[.!?。！？])"
    r"(?=\s+(?:[A-Z\"'(\u201c\u300c\u300e\uff08]|[\u3400-\u4dbf\u4e00-\u9fff]|\Z)|\Z)",
    re.S,
)
CUE_RE = re.compile(
    r"\b(?:also\s+)?(?:activate[sd]?|use[sd]?|apply|trigger(?:ed|s)?)"
    r"(?:\s+(?:this(?:\s+skill)?|the\s+skill|it))?\s+(?:when|for)\b",
    re.I,
)
NEGATIVE_RE = re.compile(r"^(?:not\b|don'?t\b|do\s+not\b)", re.I)
LEADING_CONNECTOR_RE = re.compile(r"^(?:or|and|,\s*)+\s*", re.I)
CROSS_REF_RE = re.compile(r"(?:→\s*)?\bSee\b[^\n]*skill", re.I)


def normalize(text: str) -> str:
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def split_sentences(text: str) -> list[str]:
    t = normalize(text)
    # Put a space after CJK sentence punctuation so the lookahead below works
    # even when the next sentence starts immediately with another CJK char.
    t = re.sub(r"([。！？])(?=\S)", r"\1 ", t)
    if t and t[-1] not in ".!?。！？":
        t += "."
    return [normalize(m.group(0)) for m in SENTENCE_RE.finditer(t)]


def split_clauses(text: str) -> list[str]:
    """Split a scenario clause on commas, merging unbalanced/short fragments."""
    raw = [p.strip() for p in re.split(r",\s*", text) if p.strip()]

    merged: list[str] = []
    for p in raw:
        if merged and _unbalanced(merged[-1]):
            merged[-1] = merged[-1] + ", " + p
        else:
            merged.append(p)

    # Short single-word lead-ins ("Writing", "MariaDB") share their object
    # with the following fragment, so chain them forward onto it.
    result: list[str] = []
    i = 0
    while i < len(merged):
        cur = merged[i]
        if _is_lead(cur) and i + 1 < len(merged):
            chain = [cur]
            j = i + 1
            while j < len(merged) and _is_lead(merged[j]):
                chain.append(merged[j])
                j += 1
            if j < len(merged):
                merged[j] = ", ".join(chain) + ", " + merged[j]
                i = j
                continue
        result.append(cur)
        i += 1

    # Fragments that merely continue the previous one ("including pages",
    # "such as charts") are merged backward.
    back_merged: list[str] = []
    for p in result:
        if back_merged and re.match(r"^(?:including|such\s+as)\b", p, re.I):
            back_merged[-1] = back_merged[-1] + ", " + p
        else:
            back_merged.append(p)
    result = back_merged

    # If most remaining fragments are still tiny, keep the clause intact.
    if len(result) > 1 and sum(1 for x in result if len(x) < 15) > len(result) / 2:
        result = [text.strip()]
    return result


def _is_lead(s: str) -> bool:
    return " " not in s and len(s) < 15


def _unbalanced(s: str) -> bool:
    return s.count("(") > s.count(")") or s.count("[") > s.count("]")


def as_bullet(text: str) -> str:
    text = normalize(text).rstrip(".")
    text = LEADING_CONNECTOR_RE.sub("", text)
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return text


def _is_cross_reference(s: str) -> bool:
    return bool(CROSS_REF_RE.search(s))


def extract_zh_scenarios(description: str) -> list[str]:
    """Extract quoted scenarios after Chinese '适用于' / '適用於'."""
    m = re.search(r"(?:适用于|適用於)(.*?)(?:等场景|等場景|$)", description, re.S)
    if not m:
        return []
    return re.findall(r"[「『](.*?)[」』]", m.group(1))


ZH_TEXT_RE = re.compile(r"[\u4e00-\u9fff]")
# Chinese descriptions carry capability and approach sentences alongside the
# trigger sentences ("支持自动检测编码", "做法是…"). Only the trigger-shaped
# ones belong in a "use when" list.
ZH_SCENARIO_RE = re.compile(r"適用於|适用于|用于|用來|用来|需要|时调用|時調用")
ZH_WHEN_RE = re.compile(r"^[当當].{1,80}?[时時]")


def _is_zh_sentence(text: str) -> bool:
    return bool(ZH_TEXT_RE.search(text))


def _looks_like_zh_scenario(text: str) -> bool:
    """Whether a Chinese sentence reads as a trigger rather than a description."""
    return bool(ZH_SCENARIO_RE.search(text) or ZH_WHEN_RE.match(text))


# Emitted when a description carries no scenario at all; the body fallback and
# the renderer both key off this exact string.
USE_WHEN_PLACEHOLDER = "[Add use-when scenarios from SKILL.md]"
PURPOSE_PLACEHOLDER = "[Add one-sentence purpose from SKILL.md]"


def extract_blocks(description: str) -> tuple[str, list[str]]:
    """Return (purpose, use-when bullets) from a front-matter description."""
    sentences = split_sentences(description)
    if not sentences:
        return "", [USE_WHEN_PLACEHOLDER]

    first = sentences[0]
    rest = sentences[1:]
    purpose = first

    # "Statement. Use when a, b, or c." -> statement is the purpose, the
    # clause after the cue becomes scenario bullets.
    cue_in_first = CUE_RE.search(first)
    opening_clause = ""
    if cue_in_first:
        before = first[: cue_in_first.start()].strip().rstrip(" .")
        after = first[cue_in_first.end():].strip()
        if before:
            purpose = before
            opening_clause = after
        # If the description starts directly with "Use when ..." there is no
        # purpose statement; keep the whole sentence as purpose and let the
        # agent polish it.

    bullets: list[str] = []
    negatives: list[str] = []
    plain: list[str] = []

    if opening_clause:
        bullets.extend(split_clauses(opening_clause))

    for s in rest:
        if NEGATIVE_RE.match(s):
            negatives.append(s)
            continue
        cue = CUE_RE.search(s)
        if cue:
            clause = s[cue.end():].strip()
            bullets.extend(split_clauses(clause))
        elif _is_cross_reference(s):
            continue  # pointers to other skills are not use-when scenarios
        else:
            plain.append(s)

    bullets = [as_bullet(b) for b in bullets if b.strip()]
    bullets = list(dict.fromkeys(bullets))  # dedupe, preserve order

    if not bullets:
        bullets = [as_bullet(s) for s in extract_zh_scenarios(description)]

    if len(bullets) < 2:
        for s in plain:
            if len(bullets) >= 3:
                break
            # English prose reaches this point only through "Use when …" cues,
            # which are already scenario-phrased. Chinese descriptions need the
            # extra filter: they also carry capability and approach sentences
            # ("支持自动检测编码", "做法是…"), and one that merely restates an
            # already collected quoted scenario is redundant.
            if _is_zh_sentence(s):
                if not _looks_like_zh_scenario(s):
                    continue
                if any(b and b in s for b in bullets):
                    continue
            bullets.append(as_bullet(s))

    if not bullets:
        bullets = [USE_WHEN_PLACEHOLDER]

    bullets = bullets[:5]
    for s in negatives[:2]:
        bullets.append(as_bullet(s))

    return purpose, bullets


# A description is not the only place scenarios live: plenty of skills state
# them under a dedicated heading in the body instead.
USE_WHEN_HEADING_RE = re.compile(
    r"when\s+to\s+(?:use|apply)|use\s+when|trigger|"
    r"何时(?:用|使用)|使用时机|什么时候(?:用|使用)|适用|触发",
    re.I,
)
USE_WHEN_HEADING_EXCLUDE_RE = re.compile(r"^\s*(?:not\b|don'?t\b|不|否)", re.I)
# A scenario list ends where an option/flag list begins: flags describe how to
# call the skill, not why to reach for it.
OPTION_LABEL_RE = re.compile(
    r"^\s*(?:\*\*)?(?:flags?|options?|arguments?|参数|选项|参数说明|命令行参数)(?:\*\*)?\s*[:：]",
    re.I,
)
MD_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
MD_HEADING_RE = re.compile(r"^(#{2,4})\s+(.*?)\s*$")
# A horizontal rule closes the section just as a heading does; without this it
# would be glued onto the previous bullet as a wrapped line.
MD_RULE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")


def extract_body_scenarios(text: str, limit: int = 4) -> list[str]:
    """Collect use-when bullets from a SKILL.md body section.

    Used only when the front-matter description yielded no scenario at all, so
    entries that already have bullets are never affected. Wrapped bullet lines
    are joined, and the walk stops at the section end or at an option list.
    """
    lines = text.splitlines()

    start = None
    heading_level = 0
    for i, line in enumerate(lines):
        m = MD_HEADING_RE.match(line)
        if not m or not USE_WHEN_HEADING_RE.search(m.group(2)):
            continue
        # "不适用" / "Not for" heads the opposite list.
        if USE_WHEN_HEADING_EXCLUDE_RE.match(m.group(2)):
            continue
        start, heading_level = i, len(m.group(1))
        break
    if start is None:
        return []

    bullets: list[str] = []
    for line in lines[start + 1 :]:
        m = MD_HEADING_RE.match(line)
        if m and len(m.group(1)) <= heading_level:
            break
        if OPTION_LABEL_RE.match(line):
            break
        if MD_RULE_RE.match(line):
            break
        b = MD_BULLET_RE.match(line)
        if b:
            bullets.append(b.group(1).strip())
        elif line.strip() and bullets and not line.lstrip().startswith(("|", "#")):
            bullets[-1] += " " + line.strip()  # wrapped continuation
    return [as_bullet(b) for b in bullets[:limit] if b.strip()]


# ---------------------------------------------------------------------------
# Discovery, validation
# ---------------------------------------------------------------------------

class SkillEntry:
    def __init__(self, directory: str, path: Path | None, fields: dict[str, str]):
        self.directory = directory
        self.path = path
        self.name = fields.get("name", "").strip()
        self.description = fields.get("description", "").strip()
        # File inventory, the basis of an incremental rebuild: path, size and
        # mtime of every file in the skill folder (caches excluded).
        self.files: list[dict] = []
        self.files_hash = ""
        # Stamp of SKILL.md alone, so a body edit is told apart from other
        # files of the skill (assets, scripts) changing.
        self.skill_md_stamp = ""


# Directories and files that stay out of change detection: caches and editor
# leftovers change on their own schedule and say nothing about the prose.
EXCLUDE_DIR_NAMES = {
    "__pycache__", ".git", ".hg", ".svn", "node_modules", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea", ".vscode",
}
EXCLUDE_FILE_PATTERNS = ("*.pyc", "*.pyo", "*.pyd", "*.tmp", "*.swp", "*.log",
                         ".DS_Store", "Thumbs.db")


def is_excluded(rel_path: str) -> bool:
    """Whether a skill-relative path is cache/plumbing rather than content."""
    parts = rel_path.split("/")
    if any(part in EXCLUDE_DIR_NAMES for part in parts[:-1]):
        return True
    name = parts[-1]
    if name in EXCLUDE_DIR_NAMES:
        return True
    return any(fnmatch.fnmatchcase(name, pat) for pat in EXCLUDE_FILE_PATTERNS)


def iso_utc(timestamp: float) -> str:
    """Microsecond-resolution UTC timestamp.

    Seconds alone are not enough: two writes to the same file inside one second
    that happen to keep the byte count would look identical, and the incremental
    comparison would miss the change. Microseconds are readable, stable across
    runs, and precise enough for every file system this runs on.
    """
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f") + "Z"


def collect_files(skill_dir: Path) -> list[dict]:
    """Inventory a skill folder: relative path, byte size and mtime per file."""
    files: list[dict] = []
    for path in sorted(skill_dir.rglob("*"), key=lambda p: p.as_posix()):
        if not path.is_file():
            continue
        relative = path.relative_to(skill_dir).as_posix()
        if is_excluded(relative):
            continue
        try:
            st = path.stat()
        except OSError:  # pragma: no cover - unreadable file
            continue
        files.append({"path": relative, "size": st.st_size,
                      "mtime": iso_utc(st.st_mtime)})
    return files


def files_fingerprint(files: list[dict]) -> str:
    """Hash of the file list: any add, remove, resize or touch changes it."""
    blob = "\n".join(f"{f['path']}\t{f['size']}\t{f['mtime']}" for f in files)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:HASH_LEN]


def file_stamp(files: list[dict], relative: str) -> str:
    """size+mtime stamp of one known file, or '' when it is not in the list."""
    for f in files:
        if f["path"] == relative:
            return f"{f['size']}:{f['mtime']}"
    return ""


def discover_skills(skills_dir: Path) -> list[SkillEntry]:
    entries: list[SkillEntry] = []
    for child in sorted(skills_dir.iterdir(), key=lambda p: p.name.lower()):
        # Dot-directories are plumbing, never skills: a repository used as a
        # skills directory also holds .git next to the skill folders, and it
        # must not show up as an entry without a SKILL.md.
        if not child.is_dir() or child.name.startswith("."):
            continue
        skill_md = child / "SKILL.md"
        if skill_md.is_file():
            text = skill_md.read_text(encoding="utf-8", errors="replace")
            fields = parse_front_matter(text)
            entry = SkillEntry(child.name, skill_md, fields)
        else:
            entry = SkillEntry(child.name, None, {})
        entry.files = collect_files(child)
        entry.files_hash = files_fingerprint(entry.files)
        entry.skill_md_stamp = file_stamp(entry.files, "SKILL.md")
        entries.append(entry)
    return entries


def validate(entries: list[SkillEntry]) -> list[str]:
    issues: list[str] = []
    seen: dict[str, str] = {}
    for e in entries:
        if e.path is None:
            issues.append(f"{e.directory}: SKILL.md not found")
            continue
        if not e.name:
            issues.append(f"{e.directory}: missing 'name' in SKILL.md front matter")
        elif e.name != e.directory:
            issues.append(
                f"{e.directory}: front-matter name '{e.name}' does not match "
                f"the directory name"
            )
        if not e.description:
            issues.append(
                f"{e.directory}: missing or empty 'description' in SKILL.md "
                f"front matter; purpose cannot be determined"
            )
        if e.name:
            if e.name in seen:
                issues.append(
                    f"naming conflict: '{e.name}' is used by both "
                    f"{seen[e.name]} and {e.directory}"
                )
            else:
                seen[e.name] = e.directory
    return issues


def prepare(entries: list[SkillEntry]) -> list[dict]:
    """Build structured blocks (dir/name/purpose/bullets) from valid entries."""
    prepared: list[dict] = []
    for e in entries:
        if not e.name or not e.description:
            continue
        purpose, bullets = extract_blocks(e.description)
        if bullets == [USE_WHEN_PLACEHOLDER] and e.path is not None:
            from_body = extract_body_scenarios(
                e.path.read_text(encoding="utf-8", errors="replace")
            )
            if from_body:
                bullets = from_body
        prepared.append(
            {"dir": e.directory, "name": e.name,
             "purpose": purpose or PURPOSE_PLACEHOLDER,
             "bullets": bullets}
        )
    return prepared


# ---------------------------------------------------------------------------
# Localization
# ---------------------------------------------------------------------------

DEFAULT_MD_LANG = "en"
DEFAULT_HTML_LANG = "zh-Hans"

# Markdown flavours: "ai" is the lightweight directory an agent reads, "human" is
# the same content the HTML page carries (categories, example prompts, links),
# written for people. --md-style selects one; a README.md target defaults to the
# human one.
MD_STYLES = ("ai", "human")
DEFAULT_MD_STYLE = "ai"

# A Markdown target named README.md is a human-facing document, so it flips both
# Markdown defaults -- style and language -- unless the flags say otherwise.
README_STEM = "readme"
README_MD_LANG = "zh-Hans"

# Where the unfiltered index lives. A filtered run (--include) derives its own
# output names from these so that it can never silently overwrite the full index,
# and it seeds translations from the full page (see main()).
DEFAULT_MD_PATH = "~/.agents/SKILLS.md"
DEFAULT_HTML_PATH = "~/.agents/SKILLS.html"

# The database sits next to the Markdown index and takes its name:
# ~/.agents/SKILLS.md -> ~/.agents/SKILLS.json. It is the long-lived carrier of
# the prose (both languages) and of the file inventory used for change
# detection; the Markdown and HTML outputs are rendered from it.
DB_SUFFIX = ".json"
DB_SCHEMA = "yashi-build-skills-index/1"

# The language the prose is authored in. Every other language is a translation
# of it, and is the only kind of text that needs a human to confirm a review.
SOURCE_LANG = "en"

# The skill directory the shared example-prompt file is written for. Only a scan
# of this directory can call an unmatched example key stale; a repository index or
# a subset run sees keys that simply belong to skills outside this index.
DEFAULT_SKILLS_DIR = "~/.agents/skills"

# Languages with a built-in string table. Supporting another language means
# adding one entry here (labels, UI chrome, category names, draft banner).
SUPPORTED_LANGS = ("en", "zh-Hans")

LANG_ALIASES = {
    "en": "en", "en-us": "en", "en-gb": "en", "english": "en",
    "zh": "zh-Hans", "zh-hans": "zh-Hans", "zh-cn": "zh-Hans",
    "zh-sg": "zh-Hans", "chinese": "zh-Hans", "simplified-chinese": "zh-Hans",
}

# Short tag used in the temporary draft file name (SKILLS.<tag>.md).
LANG_FILE_TAG = {"en": "en", "zh-Hans": "zh"}

# Internal category keys. Ordering and grouping are stable across languages;
# only the display names come from the string table.
CATEGORY_ORDER = ["Design & UI", "Dart", "Flutter", "Go", "Mindmap", "Personal"]

STRINGS = {
    "en": {
        "html_lang": "en",
        "page_title": "Available Skills",
        "md_title": "# Available Skills",
        "md_intro": [
            "This file provides a lightweight index of available AI Agent skills.",
            "For detailed instructions, always read the corresponding `SKILL.md`.",
        ],
        "md_human_title": "# Available Skills",
        "md_human_intro": [
            "Human-readable index of the available AI Agent skills, grouped by "
            "category.",
            "Each entry links to the skill folder; read its `SKILL.md` for the "
            "full instructions.",
        ],
        "toc": "Contents",
        "category_heading": "**{name}** ({n})",
        "labels": {"purpose": "Purpose:", "use_when": "Use when:",
                   "location": "Location:"},
        "sidebar_title": "Skills Index",
        "skill_count": "{n} skills",
        "filter_placeholder": "Filter skills...",
        "filter_aria": "Filter skills",
        "theme_switch_aria": "Colour theme",
        "theme_auto": "Auto",
        "theme_light": "Light",
        "theme_dark": "Dark",
        "use_when": "Use when",
        "location": "Location",
        "open_folder": "open folder",
        "examples_label": "Example prompts",
        "copy_action": "copy",
        "copied_action": "copied",
        "note_stale": "Source changed; translation needs review",
        "note_untranslated": "Not translated yet",
        "categories": {c: c for c in CATEGORY_ORDER},
        "draft_banner": [
            "Temporary translation draft, generated by yashi-build-skills-index.",
            "It is deleted once the HTML has been rendered with it.",
            "Only entries marked below need work; leave the others untouched.",
            "status: stale        -> the upstream SKILL.md changed; review the text "
            "and drop the marker.",
            "status: untranslated -> translate the source text below and drop the marker.",
        ],
    },
    "zh-Hans": {
        "html_lang": "zh-Hans",
        "page_title": "Skill 索引",
        "md_title": "# 可用 Skill 索引",
        "md_intro": [
            "本文件是可供 AI Agent 使用的 skill 轻量索引。",
            "需要详细说明时，请始终阅读对应的 `SKILL.md`。",
        ],
        "md_human_title": "# 可用 Skill 索引",
        "md_human_intro": [
            "本文件是面向人类阅读的 skill 索引，按类别分组。",
            "每个条目都给出用途、适用场景、提示词示例与所在目录；"
            "完整说明请阅读该 skill 自己的 `SKILL.md`。",
        ],
        "toc": "目录",
        "category_heading": "**{name}**（{n} 个）",
        "labels": {"purpose": "用途:", "use_when": "适用场景:", "location": "位置:"},
        "sidebar_title": "Skill 索引",
        "skill_count": "共 {n} 个 skill",
        "filter_placeholder": "筛选 skill…",
        "filter_aria": "筛选 skill",
        "theme_switch_aria": "配色主题",
        "theme_auto": "自动",
        "theme_light": "浅色",
        "theme_dark": "深色",
        "use_when": "适用场景",
        "location": "位置",
        "open_folder": "打开目录",
        "examples_label": "提示词示例",
        "copy_action": "复制",
        "copied_action": "已复制",
        "note_stale": "原文已更新，译文待校对",
        "note_untranslated": "尚未翻译",
        "categories": {
            "Design & UI": "设计与界面",
            "Dart": "Dart",
            "Flutter": "Flutter",
            "Go": "Go",
            "Mindmap": "思维导图",
            "Personal": "个人工具",
        },
        "draft_banner": [
            "临时翻译草稿，由 yashi-build-skills-index 生成；渲染 HTML 后会自动删除。",
            "只需处理下面标了 status 的条目，其余条目保持原样即可。",
            "status: stale        -> 上游 SKILL.md 已变更，校对译文后删除该标记。",
            "status: untranslated -> 把下方源文译成简体中文后删除该标记。",
        ],
    },
}

STATUS_OK = "ok"
STATUS_STALE = "stale"
STATUS_UNTRANSLATED = "untranslated"


def normalize_lang(value: str) -> str:
    """Map a user-supplied language tag to a supported language key ('' if none)."""
    return LANG_ALIASES.get(value.strip().lower(), "")


def category_of(name: str) -> str:
    if name.startswith("golang-"):
        return "Go"
    if name.startswith("dart-"):
        return "Dart"
    if name.startswith("flutter-"):
        return "Flutter"
    if name.startswith("yashi-"):
        return "Personal"
    if name.startswith("mindmap"):
        return "Mindmap"
    return "Design & UI"


# ---------------------------------------------------------------------------
# Markdown rendering (AI-oriented)
# ---------------------------------------------------------------------------

def render_markdown(prepared: list[dict], lang: str, skills_dir: Path,
                    md_path: Path) -> str:
    """Render the AI-oriented index.

    The location line follows the same rule as the human outputs (see
    location_display): an index inside its own tree points at the folder
    relatively, so a repository catalogue does not claim the skills live under
    ~/.agents/skills/.
    """
    s = STRINGS[lang]
    labels = s["labels"]
    lines = [s["md_title"], ""] + list(s["md_intro"])
    for b in prepared:
        shown, _target = location_display(skills_dir, md_path, b["dir"])
        lines += [
            "",
            f"## {b['name']}",
            "",
            labels["purpose"],
            b["purpose"],
            "",
            labels["use_when"],
        ]
        lines += [f"- {bullet}" for bullet in b["bullets"]]
        lines += [
            "",
            labels["location"],
            f"`{shown}`",
        ]
    return "\n".join(lines) + "\n"


def index_scope(skills_dir: Path, index_path: Path | None) -> tuple[str | None, str]:
    """Return (relative prefix for each skill, collection name) of an index.

    The canonical global index -- DEFAULT_MD_PATH over DEFAULT_SKILLS_DIR -- is
    the single case that keeps the absolute `~/.agents/skills/<dir>/` form,
    because it is read from anywhere on the machine. Every other index belongs
    to the tree it is written into and shows paths relative to itself:

      * `""`          skills and index in the same directory (a repository that
                      keeps the skills at its root);
      * `"skills/"`   skills in a subdirectory next to the index
                      (`<repo>/README.md` over `<repo>/skills/`).

    The collection name is what prose calls that tree ("yashi-skills"), and is
    empty for the global index and for an index written outside the tree.
    """
    if index_path is None:
        return None, ""
    try:
        if skills_dir.resolve() == Path(DEFAULT_SKILLS_DIR).expanduser().resolve():
            return None, ""
        index_dir = index_path.parent.resolve()
        skills_root = skills_dir.resolve()
    except OSError:  # pragma: no cover - unreadable path
        return None, ""
    if index_dir == skills_root:
        return "", skills_root.name
    if index_dir == skills_root.parent:
        return f"{skills_root.name}/", skills_root.parent.name
    return None, ""


def location_display(skills_dir: Path, index_path: Path | None,
                     directory: str) -> tuple[str, str]:
    """Return (path shown to the reader, link target) for one skill folder.

    `index_path` is the index being written; None, the global index, or an index
    outside the skills tree keeps the absolute ~/.agents/skills/ form.
    """
    prefix, _collection = index_scope(skills_dir, index_path)
    if prefix is None:
        shown = f"~/.agents/skills/{directory}/"
        try:
            target = (skills_dir / directory).resolve().as_uri() + "/"
        except (OSError, ValueError):  # pragma: no cover - unusable path
            target = shown
        return shown, target
    relative = f"{prefix}{directory}/"
    return relative, relative


def render_markdown_human(prepared: list[dict], lang: str, skills_dir: Path,
                          md_path: Path, collection: str | None = None) -> str:
    """Render the human-oriented Markdown index (--md-style human).

    It follows the same content rules as the HTML page -- category grouping, a
    table of contents, example prompts and a folder link per skill -- while
    staying parseable by parse_markdown_index: the label lines keep the
    label-on-its-own-line shape, and the example prompts sit after the location
    line, so a re-render of a parsed index is a fixed point (--check stays
    green). Trimming that shape would make every run fold prose into the wrong
    field.

    A repository index (see index_scope) names its collection in the title and
    drops the boilerplate intro, and every list is preceded by a blank line,
    which is both what Markdown formatters expect and what keeps a hand-formatted
    file convergent. Example prompts are fenced, so a forge offers a copy button
    for them -- the closest Markdown gets to the page's click-to-copy.
    """
    strings = STRINGS[lang]
    categories = strings["categories"]
    labels = strings["labels"]

    grouped: dict[str, list[dict]] = {c: [] for c in CATEGORY_ORDER}
    for b in prepared:
        grouped[category_of(b["name"])].append(b)

    lines: list[str] = [strings["md_human_title"], ""]
    _prefix, scope_collection = index_scope(skills_dir, md_path)
    if collection is None:
        collection = scope_collection
    if collection:
        # A repository index says which collection it belongs to; the title is
        # part of the regenerated frame, so it is not hand-editable either.
        lines[0] = f"{strings['md_human_title']} · {collection}"
    else:
        lines += list(strings["md_human_intro"])
        lines.append("")
    lines += [strings["skill_count"].format(n=len(prepared)), "",
              f"## {strings['toc']}", ""]
    for cat in CATEGORY_ORDER:
        items = grouped[cat]
        if not items:
            continue
        lines += [strings["category_heading"].format(
            name=categories[cat], n=len(items)), ""]
        for b in items:
            lines.append(f"- [{b['name']}](#{slug(b['name'])})")
        lines.append("")

    for cat in CATEGORY_ORDER:
        items = grouped[cat]
        if not items:
            continue
        lines += ["---", "",
                  strings["category_heading"].format(
                      name=categories[cat], n=len(items)), ""]
        for b in items:
            shown, target = location_display(skills_dir, md_path, b["dir"])
            lines += [f"## {b['name']}", "", labels["purpose"], b["purpose"], "",
                      labels["use_when"], ""]
            lines += [f"- {bullet}" for bullet in b["bullets"]]
            lines += ["", labels["location"],
                      f"`{shown}` · [{strings['open_folder']}]({target})"]
            prompts = list(b.get("prompts") or [])
            if prompts:
                lines += ["", f"{strings['examples_label']}:", "", "```text"]
                lines += list(prompts)
                lines += ["```"]
            lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _label_alternation(key: str) -> str:
    """Regex alternation matching one template label in any supported language.

    Accepting every language keeps the parser usable both for the source index
    and for a translation draft written in another language.
    """
    alts = [re.escape(STRINGS[l]["labels"][key]) for l in SUPPORTED_LANGS]
    return "(?:" + "|".join(dict.fromkeys(alts)) + ")"


STATUS_RE = re.compile(r"<!--\s*status:\s*([a-z\-]+)\s*-->")
COMMENT_RE = re.compile(r"(?s)<!--.*?-->")


def parse_markdown_index(md: str) -> list[dict]:
    """Parse a rendered index (any supported language) into structured blocks.

    Per-entry status markers carried by the translation draft
    (`<!-- status: stale -->`, `<!-- status: untranslated -->`) are
    read before the comments are stripped, so the returned text never contains
    them.
    """
    purpose_l = _label_alternation("purpose")
    use_l = _label_alternation("use_when")
    loc_l = _label_alternation("location")

    out: list[dict] = []
    for sec in re.split(r"(?m)^## ", md)[1:]:
        sec_lines = sec.split("\n")
        name = sec_lines[0].strip()
        body = "\n".join(sec_lines[1:])
        status_match = STATUS_RE.search(body)
        status = status_match.group(1) if status_match else STATUS_OK
        body = COMMENT_RE.sub("", body)
        pm = re.search(rf"(?s){purpose_l}\s*\n(.*?)\n{use_l}", body)
        uw = re.search(rf"(?s){use_l}\s*\n(.*?)\n{loc_l}", body)
        loc = re.search(rf"(?s){loc_l}\s*\n`([^`]+)`", body)
        bullets = (
            [normalize(l[2:]) for l in uw.group(1).split("\n") if l.startswith("- ")]
            if uw else []
        )
        directory = ""
        if loc:
            mm = re.search(r"skills/([^/]+)/", loc.group(1))
            directory = mm.group(1) if mm else ""
        out.append({
            "dir": directory or name,
            "name": name,
            "purpose": normalize(pm.group(1)) if pm else "",
            "bullets": [b for b in bullets if b],
            "status": status,
        })
    return out


# ---------------------------------------------------------------------------
# Translation reuse: source hashes, rendered-HTML recovery, draft, merge
# ---------------------------------------------------------------------------

HASH_LEN = 12
DRAFT_DIR_NAME = "yashi-skills-index"


def source_hash(description: str) -> str:
    """Hash of one skill's upstream SKILL.md front-matter description.

    It is embedded in the page as `data-src` and decides whether an existing
    translation may be reused: it changes when the skill itself is updated
    upstream (e.g. by a skill sync), and stays stable when only the wording of
    the index is re-polished.
    """
    return hashlib.sha1(normalize(description).encode("utf-8")).hexdigest()[:HASH_LEN]


def default_draft_path(html_path: Path, html_lang: str) -> Path:
    """Scratch path of the translation draft: system temp dir, not a deliverable."""
    tag = LANG_FILE_TAG.get(html_lang, html_lang)
    return Path(tempfile.gettempdir()) / DRAFT_DIR_NAME / f"{html_path.stem}.{tag}.md"


# ---------------------------------------------------------------------------
# Database: the long-lived carrier of the prose and of the file inventory
# ---------------------------------------------------------------------------

# Per-skill sync states, reported on stderr. They are derived on every run and
# deliberately not stored: a stored copy would change the database on every run
# and break the "no change means byte-identical" property.
STATE_NEW = "new"
STATE_UNCHANGED = "unchanged"
STATE_SRC_CHANGED = "src-changed"
STATE_SOURCE_CHANGED = "source-changed"
STATE_FILES_CHANGED = "files-changed"
STATE_REBUILD = "rebuild"

# States in which the upstream text moved and a translation needs a look.
UPSTREAM_STATES = (STATE_NEW, STATE_REBUILD, STATE_SRC_CHANGED,
                   STATE_SOURCE_CHANGED)


def db_path_for(md_path: Path) -> Path:
    """Database that accompanies a Markdown index: path.md -> path.json."""
    return md_path.with_suffix(DB_SUFFIX)


def normalize_db(db: dict) -> dict[str, dict]:
    """Map skill directory -> stored record, dropping malformed entries."""
    out: dict[str, dict] = {}
    for rec in db.get("skills") or []:
        if isinstance(rec, dict) and isinstance(rec.get("dir"), str) and rec["dir"]:
            out[rec["dir"]] = rec
    return out


def load_db(path: Path) -> dict:
    """Read the database; a missing file is empty, a corrupt one is fatal.

    The database carries the only long-lived copy of the localized prose, so a
    malformed file is never overwritten silently: the run stops and the user
    decides. --rebuild exists to recreate it from the rendered outputs.
    """
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"error: cannot read the database {path}: {exc}")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise SystemExit(
            f"error: cannot parse the database {path}: {exc}\n"
            "It carries the only copy of the translations, so it is not "
            "overwritten automatically. Fix it by hand, or delete it to rebuild "
            "the database from the current outputs."
        )
    if not isinstance(data, dict) or not isinstance(data.get("skills"), list):
        raise SystemExit(
            f"error: {path} is not a skills-index database "
            f"(no 'skills' list at the top level)"
        )
    return data


def dump_db(db: dict) -> str:
    """Serialise the database: UTF-8 text, two-space indent, trailing newline."""
    return json.dumps(db, ensure_ascii=False, indent=2) + "\n"


def now_local_iso() -> str:
    """Local timestamp with an offset: readable, unambiguous, sortable."""
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def locale_text_hash(purpose: str, bullets: list[str]) -> str:
    """Hash of one language slot's text, used to notice hand edits.

    The database stores the hash of the text it wrote. When the two disagree,
    somebody edited the text, and the slot counts as reviewed.
    """
    blob = "\n".join([purpose or "", *[str(b) for b in (bullets or [])]])
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:HASH_LEN]


def _record_core(rec: dict) -> str:
    """Stable JSON of a record without its timestamps, for change detection."""
    core = {k: v for k, v in rec.items() if k not in ("added_at", "updated_at")}
    return json.dumps(core, sort_keys=True, ensure_ascii=False)


def _old_locale(old: dict, lang: str) -> dict:
    locales = old.get("locales")
    if not isinstance(locales, dict):
        return {}
    loc = locales.get(lang)
    return loc if isinstance(loc, dict) else {}


def _localized_texts(old_loc: dict, seed: dict | None,
                     fallback_src_hash: str,
                     current_stamp: str) -> tuple[str, list[str], str, str]:
    """Text of a translated language slot: stored wording first, then the seed.

    A translation is never regenerated automatically, only flagged for review,
    so once a slot has text that text wins. The seed (recovered from the previous
    SKILLS.html or from another database) is used the first time an entry is
    seen; its own review state travels with it, so a page that showed the source
    text for a missing translation cannot pass it off as a translation, and a
    page that was waiting for review cannot pass its text off as reviewed.
    """
    stored = (old_loc.get("purpose") or "").strip()
    if stored:
        return (stored, list(old_loc.get("use_when") or []),
                old_loc.get("src_hash") or "", old_loc.get("source_stamp") or "")
    if seed and ((seed.get("purpose") or "").strip() or seed.get("bullets")):
        seed_status = seed.get("status") or STATUS_OK
        if seed_status == STATUS_UNTRANSLATED:
            return "", [], "", ""
        src_hash = seed.get("src_hash") or fallback_src_hash
        if seed_status == STATUS_STALE:
            # Borrow the text, but not the claim that it matches the current
            # skill: the review flag has to survive the round trip.
            src_hash = ""
        return ((seed.get("purpose") or "").strip(), list(seed.get("bullets") or []),
                src_hash, current_stamp)
    return "", [], "", ""


def _source_texts(state: str, heuristic: dict, seed: dict | None,
                  old_loc: dict, desc_hash: str, current_stamp: str,
                  seed_is_current: bool = True) -> tuple[str, list[str], str, str]:
    """Text of the source language under the current sync state.

    The source prose is rewritten when the state says the upstream text moved
    (rebuild, changed description), seeded from the previous Markdown index for
    a brand-new entry, and otherwise kept as stored. A seed whose recorded
    description hash no longer matches the skill is dropped: it belongs to an
    older wording of the skill, so the front-matter heuristic is used instead.
    """
    if state in (STATE_REBUILD, STATE_SRC_CHANGED):
        return heuristic["purpose"], list(heuristic["bullets"]), desc_hash, current_stamp
    if state == STATE_NEW:
        if seed_is_current and seed and (seed.get("purpose") or "").strip():
            return seed["purpose"], list(seed.get("bullets") or []), desc_hash, current_stamp
        return heuristic["purpose"], list(heuristic["bullets"]), desc_hash, current_stamp
    stored = (old_loc.get("purpose") or "").strip()
    if stored:
        return stored, list(old_loc.get("use_when") or []), desc_hash, current_stamp
    if seed_is_current and seed and (seed.get("purpose") or "").strip():
        return seed["purpose"], list(seed.get("bullets") or []), desc_hash, current_stamp
    return heuristic["purpose"], list(heuristic["bullets"]), desc_hash, current_stamp


def _build_locale(lang: str, texts: tuple[str, list[str], str, str], old_loc: dict,
                  state: str, desc_hash: str, skill_md_stamp: str,
                  mark_reviewed: bool) -> dict:
    """Assemble one language slot: text, review status and bookkeeping hashes.

    A translation is reviewed when its text still matches the SKILL.md it was
    written against: both the description hash and the SKILL.md stamp have to
    line up. A source-language slot is a derivative of the front matter, so it
    has nothing to review.
    """
    purpose, bullets, src_hash, source_stamp = texts
    purpose = (purpose or "").strip()
    bullets = [str(b).strip() for b in (bullets or []) if str(b).strip()]
    if not purpose:
        status = STATUS_UNTRANSLATED
        src_hash = ""
        source_stamp = ""
    elif lang == SOURCE_LANG:
        status = STATUS_OK
        src_hash = desc_hash
        source_stamp = skill_md_stamp
    else:
        old_hash = old_loc.get("text_hash") or ""
        edited = bool(old_hash) and old_hash != locale_text_hash(
            (old_loc.get("purpose") or "").strip(),
            list(old_loc.get("use_when") or []),
        )
        if edited or mark_reviewed:
            # A hand edit (or an explicit confirmation) means somebody looked at
            # the text against the current skill: it is reviewed as of now.
            status = STATUS_OK
            src_hash = desc_hash
            source_stamp = skill_md_stamp
        elif state == STATE_REBUILD:
            # A forced rebuild revokes the review of every translation: clearing
            # the bookkeeping hashes is what keeps that decision stable across
            # the following runs (a plain run must not silently restore "ok").
            status = STATUS_STALE
            src_hash = ""
            source_stamp = ""
        elif src_hash == desc_hash and source_stamp == skill_md_stamp:
            status = STATUS_OK
        elif state in UPSTREAM_STATES:
            status = STATUS_STALE
        else:
            # Nothing moved: keep a pending review pending, otherwise the stored
            # text is still current.
            status = STATUS_STALE if old_loc.get("status") == STATUS_STALE else STATUS_OK
            if status == STATUS_OK:
                src_hash = desc_hash
                source_stamp = skill_md_stamp
    return {
        "purpose": purpose,
        "use_when": bullets,
        "status": status,
        "src_hash": src_hash if purpose else "",
        "source_stamp": source_stamp if purpose else "",
        "text_hash": locale_text_hash(purpose, bullets) if purpose else "",
    }


def sync_records(entries: list[SkillEntry], prepared: list[dict], db: dict,
                 *, rebuild: bool, mark_reviewed: bool,
                 source_seed: dict[str, dict],
                 localized_seed: dict[str, dict],
                 examples: dict[str, list[str]],
                 now: str) -> tuple[list[dict], dict[str, str]]:
    """Reconcile the scanned skills with the stored database.

    Returns (records, states): the fresh records in scan order, and the sync
    state per directory so main() can report what changed. Prose is kept
    whenever the file inventory says the skill did not move; it is regenerated
    when the upstream description changed, and marked for review when SKILL.md
    itself changed or --rebuild was passed.
    """
    stored = normalize_db(db)
    heuristic = {b["dir"]: b for b in prepared}
    records: list[dict] = []
    states: dict[str, str] = {}

    for entry in entries:
        old = stored.get(entry.directory) or {}
        heur = heuristic.get(entry.directory) or {
            "purpose": PURPOSE_PLACEHOLDER, "bullets": [USE_WHEN_PLACEHOLDER]}
        desc_hash = source_hash(entry.description)

        if rebuild:
            # An explicit rebuild outranks everything, including a brand-new
            # entry: the prose is regenerated from the front matter.
            state = STATE_REBUILD
        elif not old:
            state = STATE_NEW
        elif (old.get("description_hash") or "") != desc_hash:
            state = STATE_SRC_CHANGED
        elif file_stamp(old.get("files") or [], "SKILL.md") != entry.skill_md_stamp:
            state = STATE_SOURCE_CHANGED
        elif (old.get("files_hash") or "") != entry.files_hash:
            state = STATE_FILES_CHANGED
        else:
            state = STATE_UNCHANGED
        states[entry.directory] = state

        name = entry.name or entry.directory
        # A seed recovered from an older page records the description hash it was
        # written against; a mismatch means the skill has moved since, so the
        # seeded wording is stale and must not be copied forward.
        seed_hash = ((localized_seed.get(name) or {}).get("src_hash") or "")
        seed_is_current = (not seed_hash) or seed_hash == desc_hash
        locales: dict[str, dict] = {}
        for lang in SUPPORTED_LANGS:
            old_loc = _old_locale(old, lang)
            if lang == SOURCE_LANG:
                texts = _source_texts(state, heur, source_seed.get(entry.directory),
                                      old_loc, desc_hash, entry.skill_md_stamp,
                                      seed_is_current)
            else:
                texts = _localized_texts(old_loc, localized_seed.get(name), desc_hash,
                                         entry.skill_md_stamp)
            locales[lang] = _build_locale(lang, texts, old_loc, state, desc_hash,
                                          entry.skill_md_stamp, mark_reviewed)

        # The prompt examples are authored in scripts/assets/skill-examples.json
        # and mirrored here, so that one JSON file describes every skill fully.
        prompts = [str(p).strip() for p in (examples.get(name) or []) if str(p).strip()]

        record = {
            "dir": entry.directory,
            "name": name,
            "category": category_of(name),
            "description": entry.description,
            "description_hash": desc_hash,
            "files": entry.files,
            "files_hash": entry.files_hash,
            "source_stamp": entry.skill_md_stamp,
            "prompts": prompts,
            "locales": locales,
            "added_at": (old.get("added_at") or now) if old else now,
            "updated_at": now,
        }
        if old and _record_core(old) == _record_core(record):
            # Nothing but the timestamp would change: keep the stored one, so an
            # unchanged run leaves the database byte-identical.
            record["added_at"] = old.get("added_at") or now
            record["updated_at"] = old.get("updated_at") or now
        records.append(record)

    return records, states


def build_db(records: list[dict], *, skills_dir: Path, md_path: Path,
             html_path: Path, old_db: dict, now: str,
             collection: str = "") -> dict:
    """Wrap the records into the database document, keeping timestamps stable."""
    statuses = {lang: {STATUS_OK: 0, STATUS_STALE: 0, STATUS_UNTRANSLATED: 0}
                for lang in SUPPORTED_LANGS}
    for rec in records:
        locales = rec.get("locales") or {}
        for lang in SUPPORTED_LANGS:
            status = (locales.get(lang) or {}).get("status") or STATUS_UNTRANSLATED
            statuses[lang][status] = statuses[lang].get(status, 0) + 1

    db = {
        "schema": DB_SCHEMA,
        "generated_at": now,
        "skills_dir": str(skills_dir),
        "collection": collection,
        "outputs": {"markdown": str(md_path), "html": str(html_path),
                    "database": str(db_path_for(md_path))},
        "exclude": sorted(EXCLUDE_DIR_NAMES) + list(EXCLUDE_FILE_PATTERNS),
        "stats": {
            "skills": len(records),
            "with_prompts": sum(1 for r in records if r.get("prompts")),
            "locale_status": statuses,
        },
        "skills": records,
        "_doc": DOC,
    }
    # The document is a fixed point: the timestamp only moves when the payload
    # does, so an unchanged run rewrites the very same bytes.
    old_skills = json.dumps(old_db.get("skills") or [], sort_keys=True,
                            ensure_ascii=False)
    new_skills = json.dumps(records, sort_keys=True, ensure_ascii=False)
    if old_db and old_skills == new_skills and old_db.get("generated_at"):
        db["generated_at"] = old_db["generated_at"]
    return db


DOC = {
    "purpose": (
        "Database of the skill index: one record per skill, carrying the file "
        "inventory used for incremental rebuilds and the prose of every "
        "language. The Markdown index and the HTML page are rendered from it."
    ),
    "generated_by": "yashi-build-skills-index (scripts/build_skills_index.py)",
    "fields": {
        "collection": ("title of the human-readable index, fixed when the "
                       "database is created so local runs and CI agree"),
        "description": "verbatim front-matter description of the skill's SKILL.md",
        "description_hash": "hash of that description; a change rewrites the source prose",
        "files": "path, size and UTC mtime of every file in the skill folder",
        "files_hash": "hash of that file list; the basis of the incremental rebuild",
        "source_stamp": "size:mtime of SKILL.md, the source the prose is written against",
        "prompts": "example prompts, mirrored from scripts/assets/skill-examples.json",
        "locales": ("purpose + use_when per language; src_hash and source_stamp "
                    "are the description hash and the SKILL.md stamp the text was "
                    "reviewed against, text_hash notices hand edits (an edited "
                    "text counts as reviewed)"),
        "status": "ok | stale (upstream moved, review the text) | untranslated",
        "added_at / updated_at": "first and last time this record changed",
        "generated_at": "last time the payload changed, not the last run",
    },
    "editing": (
        "Edit the prose here (or translate the --emit-draft file), then rerun; "
        "--rebuild rewrites every source-language text and marks all "
        "translations for review, --mark-reviewed confirms pending reviews "
        "without touching the text."
    ),
}


def seed_source_from_markdown(md_path: Path) -> dict[str, dict]:
    """Recover source-language prose from a rendered Markdown index.

    Hand-polished wording lives in that file, so reading it once is how an index
    built by an older version of this script migrates into the database.
    """
    seed: dict[str, dict] = {}
    if not md_path.is_file():
        return seed
    try:
        for b in parse_markdown_index(md_path.read_text(encoding="utf-8")):
            if not (b.get("purpose") or "").strip():
                continue
            seed[b.get("dir") or b["name"]] = {
                "purpose": b["purpose"], "bullets": list(b.get("bullets") or [])}
    except OSError:  # pragma: no cover - unreadable file
        pass
    return seed


def seed_localized_from_html(html_path: Path, lang: str) -> dict[str, dict]:
    """Recover translated prose from a rendered HTML page, keyed by skill name.

    Cards the page itself marked as untranslated are dropped: the renderer shows
    the source-language fallback there, not a translation, and seeding it would
    silently turn "not translated yet" into "translated".
    """
    if not html_path.is_file():
        return {}
    try:
        recovered = parse_html_index(html_path.read_text(encoding="utf-8"), lang)
    except OSError:  # pragma: no cover - unreadable file
        return {}
    return {name: entry for name, entry in recovered.items()
            if entry.get("status") != STATUS_UNTRANSLATED}


def locale_seed_from_db(db: dict, lang: str) -> dict[str, dict]:
    """Recover one language's prose from another database (subset seeding).

    A filtered run keeps its own database file; this is how a fresh one borrows
    the translations of the full index instead of starting untranslated.
    """
    seed: dict[str, dict] = {}
    for rec in normalize_db(db).values():
        loc = (rec.get("locales") or {}).get(lang)
        if not isinstance(loc, dict) or not (loc.get("purpose") or "").strip():
            continue
        seed[rec.get("name") or rec["dir"]] = {
            "purpose": loc["purpose"],
            "bullets": list(loc.get("use_when") or []),
            "src_hash": loc.get("src_hash") or rec.get("description_hash", ""),
            "status": loc.get("status") or STATUS_OK,
        }
    return seed


def merge_seed_tables(primary: dict[str, dict],
                      secondary: dict[str, dict]) -> dict[str, dict]:
    """Overlay two seed tables; entries already present in `primary` win."""
    merged = dict(secondary)
    merged.update(primary)
    return merged


def blocks_for_lang(records: list[dict], lang: str) -> list[dict]:
    """Project records into the render shape (dir/name/purpose/bullets/status).

    A skill without prose in the requested language falls back to the source
    language and is reported as untranslated, so a page never shows a hole.
    """
    blocks: list[dict] = []
    for rec in records:
        locales = rec.get("locales") or {}
        loc = locales.get(lang) if isinstance(locales.get(lang), dict) else {}
        fallback = locales.get(SOURCE_LANG) if isinstance(
            locales.get(SOURCE_LANG), dict) else {}
        loc_purpose = (loc.get("purpose") or "").strip()
        purpose = loc_purpose or (fallback.get("purpose") or "").strip()
        bullets = list(loc.get("use_when") or []) or list(fallback.get("use_when") or [])
        status = loc.get("status") or STATUS_OK
        if not loc_purpose:
            status = STATUS_UNTRANSLATED
        blocks.append({
            "dir": rec["dir"],
            "name": rec["name"],
            "purpose": purpose,
            "bullets": bullets,
            "status": status,
            # The hash this slot's text was checked against -- not the current
            # description hash. Writing the current one would make a stale or
            # missing translation look current as soon as the page is read back.
            "src_hash": (loc.get("src_hash") or "") if loc_purpose else "",
            "prompts": list(rec.get("prompts") or []),
        })
    return blocks


def locale_previous(records: list[dict], lang: str) -> dict[str, dict]:
    """Stored prose of one language, in the shape render_draft expects."""
    out: dict[str, dict] = {}
    for rec in records:
        loc = (rec.get("locales") or {}).get(lang) or {}
        if not (loc.get("purpose") or "").strip():
            continue
        out[rec["name"]] = {
            "name": rec["name"],
            "dir": rec["dir"],
            "purpose": loc.get("purpose", ""),
            "bullets": list(loc.get("use_when") or []),
            "src_hash": loc.get("src_hash") or rec.get("description_hash", ""),
            "status": loc.get("status") or STATUS_OK,
        }
    return out


def merge_draft_records(records: list[dict], draft_blocks: list[dict], lang: str,
                        source_blocks: list[dict]) -> int:
    """Fold a hand-edited translation draft back into the records.

    Only text that really differs from what the database holds counts as an
    edit; entries the draft merely carried along stay untouched and keep their
    review flag. Returns how many slots were updated.
    """
    draft = {b["name"]: b for b in draft_blocks}
    source = {b["name"]: b for b in source_blocks}
    updated = 0
    for rec in records:
        d = draft.get(rec["name"])
        if not d:
            continue
        src = source.get(rec["name"], {"purpose": "", "bullets": []})
        if not _draft_is_translated(d, src):
            continue
        purpose = (d.get("purpose") or "").strip()
        bullets = [str(b).strip() for b in (d.get("bullets") or []) if str(b).strip()]
        if not purpose:
            continue
        locales = rec.setdefault("locales", {})
        loc = locales.get(lang) if isinstance(locales.get(lang), dict) else {}
        if purpose == (loc.get("purpose") or "") and bullets == list(loc.get("use_when") or []):
            continue
        loc.update({
            "purpose": purpose,
            "use_when": bullets,
            "status": STATUS_OK,
            "src_hash": rec.get("description_hash", ""),
            "source_stamp": rec.get("source_stamp", ""),
            "text_hash": locale_text_hash(purpose, bullets),
        })
        locales[lang] = loc
        updated += 1
    return updated



CARD_RE = re.compile(
    r'<article class="skill-card(?P<cls>[^"]*)"(?P<attrs>[^>]*)>(?P<body>.*?)</article>',
    re.S,
)


def _text_from_html(fragment: str) -> str:
    text = re.sub(r"<code>(.*?)</code>", r"`\1`", fragment, flags=re.S)
    return normalize(html_mod.unescape(text))


def parse_html_index(html_text: str, lang: str) -> dict[str, dict]:
    """Recover per-skill localized prose from a previously rendered page.

    Returns an empty mapping when the page was rendered in another language,
    because its prose cannot be reused as a translation of the requested one.
    """
    root = re.search(r'<html lang="([^"]*)"', html_text)
    if root and root.group(1) != STRINGS[lang]["html_lang"]:
        return {}

    out: dict[str, dict] = {}
    for m in CARD_RE.finditer(html_text):
        body = m.group("body")
        attrs = m.group("attrs")
        name_m = re.search(r'<h3 class="skill-name">(.*?)</h3>', body, re.S)
        if not name_m:
            continue
        name = normalize(html_mod.unescape(name_m.group(1)))
        purpose_m = re.search(r'<p class="[^"]*purpose[^"]*">(.*?)</p>', body, re.S)
        # Scope the bullet scrape to the "Use when" list. A card also contains
        # the example-prompt list, whose items are <li> as well; scraping the
        # whole card would fold those prompts into the recovered bullets, and
        # every re-render would then duplicate them, so the page would never
        # reach a fixed point and --check could never pass.
        use_when_m = re.search(r'class="use-when".*?<ul>(.*?)</ul>', body, re.S)
        bullets = (re.findall(r"<li[^>]*>(.*?)</li>", use_when_m.group(1), re.S)
                   if use_when_m else [])
        loc_m = re.search(r"skills/([^/]+)/", body)
        src_m = re.search(r'data-src="([^"]*)"', attrs)
        status_m = re.search(r'data-status="([^"]*)"', attrs)
        cls = m.group("cls") + " " + attrs
        if status_m:
            status = status_m.group(1)
        elif "placeholder" in cls:
            status = STATUS_UNTRANSLATED
        else:
            status = STATUS_OK
        out[name] = {
            "name": name,
            "dir": loc_m.group(1) if loc_m else name,
            "purpose": _text_from_html(purpose_m.group(1)) if purpose_m else "",
            "bullets": [_text_from_html(b) for b in bullets],
            "src_hash": src_m.group(1) if src_m else "",
            "status": status,
        }
    return out


def render_draft(source_blocks: list[dict], previous: dict[str, dict],
                 lang: str) -> str:
    """Render the temporary translation draft in the HTML language.

    Entries whose upstream description is unchanged are pre-filled with the
    prose recovered from the previous page (marked `ok`); changed ones keep the
    old translation and are marked for review; new ones carry the source text
    and are marked as untranslated.
    """
    s = STRINGS[lang]
    labels = s["labels"]
    lines = ["<!--"] + [f"  {line}" for line in s["draft_banner"]] + ["-->"]
    for b in source_blocks:
        purpose, bullets = b["purpose"], list(b["bullets"])
        prev = previous.get(b["name"])
        status = STATUS_OK
        if prev and prev.get("purpose") and prev.get("status") != STATUS_UNTRANSLATED:
            purpose = prev["purpose"]
            bullets = list(prev["bullets"]) or bullets
            if prev.get("status") == STATUS_STALE or (
                prev.get("src_hash") and prev["src_hash"] != b["src_hash"]
            ):
                status = STATUS_STALE
        else:
            status = STATUS_UNTRANSLATED
        lines += ["", f"## {b['name']}", ""]
        if status != STATUS_OK:
            lines.append(f"<!-- status: {status} -->")
            lines.append("")
        lines += [labels["purpose"], purpose, "", labels["use_when"]]
        lines += [f"- {x}" for x in bullets]
        lines += ["", labels["location"], f"`~/.agents/skills/{b['dir']}/`"]
    return "\n".join(lines) + "\n"


def _draft_is_translated(draft_entry: dict, source: dict) -> bool:
    """True when a draft entry holds real localized text.

    A marker left behind by a careless edit must not throw a translation away,
    so text that differs from the source text counts as translated regardless
    of its status marker.
    """
    if not draft_entry.get("purpose"):
        return False
    if normalize(draft_entry["purpose"]) != normalize(source["purpose"]):
        return True
    return draft_entry.get("status") != STATUS_UNTRANSLATED


def status_counts(blocks: list[dict]) -> dict[str, int]:
    counts = {STATUS_OK: 0, STATUS_STALE: 0, STATUS_UNTRANSLATED: 0}
    for b in blocks:
        key = b.get("status", STATUS_OK)
        counts[key] = counts.get(key, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# HTML rendering (human-oriented)
# ---------------------------------------------------------------------------

def inline_html(text: str) -> str:
    """Escape text, then convert markdown backtick code to <code>."""
    escaped = html_mod.escape(text, quote=False)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-]+", "-", name)


def search_text(block: dict) -> str:
    """Haystack for the client-side filter: name, prose and example prompts.

    Examples are included on purpose, so that searching for a phrase that only
    appears in one of them still finds the skill.
    """
    parts = [block["name"], block["purpose"], *block["bullets"],
             *(block.get("prompts") or [])]
    return " ".join(parts).lower()


# The page's stylesheet and script are kept as real files, so they can be edited
# with proper highlighting and linting. They are inlined into the rendered page,
# which therefore stays a single self-contained document.
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
CSS_ASSET = ASSETS_DIR / "skills-index.css"
JS_ASSET = ASSETS_DIR / "skills-index.js"
EXAMPLES_ASSET = ASSETS_DIR / "skill-examples.json"


def load_assets() -> tuple[str, str]:
    """Return (css, js) read from the asset files next to this script."""
    try:
        css = CSS_ASSET.read_text(encoding="utf-8")
        js = JS_ASSET.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(
            f"error: cannot read the page assets: {exc}\n"
            f"expected both {CSS_ASSET} and {JS_ASSET}"
        )
    return css, js


def load_examples() -> dict[str, list[str]]:
    """Return {skill name: [example prompt, ...]} from skill-examples.json.

    The file is hand maintained, keyed by skill directory name; keys starting
    with "_" are documentation and ignored. A missing file is not an error --
    the page then simply renders without example prompts -- but a malformed one
    is, because silently dropping the examples would be confusing.
    """
    if not EXAMPLES_ASSET.is_file():
        return {}
    try:
        data = json.loads(EXAMPLES_ASSET.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"error: cannot parse {EXAMPLES_ASSET}: {exc}")
    if not isinstance(data, dict):
        raise SystemExit(f"error: {EXAMPLES_ASSET} must contain a JSON object")
    out: dict[str, list[str]] = {}
    for name, prompts in data.items():
        if name.startswith("_") or not isinstance(prompts, list):
            continue
        cleaned = [str(p).strip() for p in prompts if str(p).strip()]
        if cleaned:
            out[name] = cleaned
    return out


def report_examples(blocks: list[dict], examples: dict[str, list[str]],
                    all_names: list[str] | None = None,
                    prefix: str = "examples", canonical: bool = True) -> None:
    """Warn about skills without prompts and about keys that match no skill.

    `all_names` is the unfiltered skill list: with --include active, keys for
    skills outside the subset are not orphans, they are simply not rendered.
    `canonical` says the scan covers the full set the prompts file was written
    for (the default ~/.agents/skills). Only then can an unmatched key be called
    stale; a repository index or a subset sees keys that belong to skills which
    are not part of this index at all.
    """
    names = [b["name"] for b in blocks]
    own = {b["name"]: (b.get("prompts") or []) for b in blocks}
    missing = [n for n in names if not own.get(n)]
    known = set(all_names) if all_names is not None else set(names)
    unmatched = sorted(set(examples) - known)
    stale = unmatched if canonical else []
    foreign = [] if canonical else unmatched
    if missing:
        shown = ", ".join(missing[:10])
        if len(missing) > 10:
            shown += f", ... (+{len(missing) - 10} more)"
        print(f"{prefix}: {len(missing)} skill(s) have no example prompts: {shown}",
              file=sys.stderr)
    if stale:
        print(f"{prefix}: {len(stale)} key(s) match no skill: "
              f"{', '.join(stale[:10])}", file=sys.stderr)
    if foreign:
        print(f"{prefix}: {len(foreign)} key(s) belong to skills outside this "
              f"index (shared prompts file; informational): "
              f"{', '.join(foreign[:5])}"
              + (", ..." if len(foreign) > 5 else ""), file=sys.stderr)
    if not missing and not stale:
        print(f"{prefix}: all {len(names)} skills have example prompts",
              file=sys.stderr)


def render_html(blocks: list[dict], skills_dir: Path, lang: str,
                html_path: Path | None = None) -> str:
    strings = STRINGS[lang]
    categories = strings["categories"]

    grouped: dict[str, list[dict]] = {c: [] for c in CATEGORY_ORDER}
    for b in blocks:
        grouped[category_of(b["name"])].append(b)

    total = len(blocks)

    # Sidebar
    sidebar: list[str] = [
        '<nav class="sidebar">',
        f'<div class="sidebar-title">{html_mod.escape(strings["sidebar_title"])}</div>',
        f'<div class="sidebar-count">'
        f'{html_mod.escape(strings["skill_count"].format(n=total))}</div>',
        f'<input id="search" type="search" '
        f'placeholder="{html_mod.escape(strings["filter_placeholder"], quote=True)}" '
        f'autocomplete="off" '
        f'aria-label="{html_mod.escape(strings["filter_aria"], quote=True)}">',
        f'<div class="theme-switch" role="group" '
        f'aria-label="{html_mod.escape(strings["theme_switch_aria"], quote=True)}">',
        f'<button type="button" class="theme-btn" data-theme-mode="auto">'
        f'{html_mod.escape(strings["theme_auto"])}</button>',
        f'<button type="button" class="theme-btn" data-theme-mode="light">'
        f'{html_mod.escape(strings["theme_light"])}</button>',
        f'<button type="button" class="theme-btn" data-theme-mode="dark">'
        f'{html_mod.escape(strings["theme_dark"])}</button>',
        '</div>',
    ]
    for cat in CATEGORY_ORDER:
        items = grouped[cat]
        if not items:
            continue
        sidebar.append('<div class="sidebar-group">')
        sidebar.append(
            f'<div class="sidebar-group-title">{html_mod.escape(categories[cat])}'
            f'<span class="sidebar-group-count">{len(items)}</span></div>'
        )
        for b in items:
            s = slug(b["name"])
            hay = search_text(b)
            sidebar.append(
                f'<a class="sidebar-link" href="#{s}" '
                f'data-text="{html_mod.escape(hay, quote=True)}">'
                f'{html_mod.escape(b["name"])}</a>'
            )
        sidebar.append('</div>')
    sidebar.append('</nav>')

    # Main content
    main: list[str] = ['<main class="content">']
    for cat in CATEGORY_ORDER:
        items = grouped[cat]
        if not items:
            continue
        main.append(f'<section class="category-section" id="cat-{slug(cat)}">')
        main.append(
            f'<h2 class="category-title">{html_mod.escape(categories[cat])}'
            f'<span class="category-count">{len(items)}</span></h2>'
        )
        for b in items:
            s = slug(b["name"])
            shown, folder_uri = location_display(skills_dir, html_path, b["dir"])
            hay = search_text(b)
            status = b.get("status", STATUS_OK)
            cls = "skill-card"
            note = ""
            if status == STATUS_STALE:
                cls += " stale"
                note = strings["note_stale"]
            elif status == STATUS_UNTRANSLATED:
                cls += " untranslated"
                note = strings["note_untranslated"]
            main.append(
                f'<article class="{cls}" id="{s}" data-status="{status}" '
                f'data-src="{html_mod.escape(b.get("src_hash", ""), quote=True)}" '
                f'data-text="{html_mod.escape(hay, quote=True)}">'
            )
            main.append(f'<h3 class="skill-name">{html_mod.escape(b["name"])}</h3>')
            if note:
                main.append(f'<p class="trans-note">{html_mod.escape(note)}</p>')
            purpose_cls = "purpose placeholder" if status == STATUS_UNTRANSLATED else "purpose"
            main.append(f'<p class="{purpose_cls}">{inline_html(b["purpose"])}</p>')
            main.append(
                f'<div class="use-when"><div class="label">'
                f'{html_mod.escape(strings["use_when"])}</div><ul>'
            )
            for bullet in b["bullets"]:
                li_cls = ' class="placeholder"' if status == STATUS_UNTRANSLATED else ""
                main.append(f"<li{li_cls}>{inline_html(bullet)}</li>")
            main.append('</ul></div>')
            prompts = list(b.get("prompts") or [])
            if prompts:
                main.append(
                    f'<div class="examples" data-copy-label="'
                    f'{html_mod.escape(strings["copy_action"], quote=True)}" '
                    f'data-copied-label="'
                    f'{html_mod.escape(strings["copied_action"], quote=True)}">'
                )
                main.append(f'<div class="label">'
                            f'{html_mod.escape(strings["examples_label"])}</div>')
                main.append('<ul class="example-list">')
                for prompt in prompts:
                    main.append(
                        f'<li><button type="button" class="example-prompt" '
                        f'data-copy="{html_mod.escape(prompt, quote=True)}">'
                        f'<span class="example-text">{inline_html(prompt)}</span>'
                        f'<span class="example-copy" aria-hidden="true">'
                        f'{html_mod.escape(strings["copy_action"])}</span>'
                        f'</button></li>'
                    )
                main.append('</ul></div>')
            main.append(
                f'<div class="location"><span class="label">'
                f'{html_mod.escape(strings["location"])}</span> '
                f'<code>{html_mod.escape(shown)}</code> '
                f'<a class="folder-link" href="{html_mod.escape(folder_uri, quote=True)}">'
                f'{html_mod.escape(strings["open_folder"])}</a>'
            )
            main.append('</div>')
            main.append('</article>')
        main.append('</section>')
    main.append('</main>')

    css, js = load_assets()

    return f"""<!DOCTYPE html>
<html lang="{strings["html_lang"]}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html_mod.escape(strings["page_title"])}</title>
<style>{css}</style>
<script>{js}</script>
</head>
<body>
<div class="layout">
{''.join(sidebar)}
{''.join(main)}
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def force_utf8_streams() -> None:
    """Write UTF-8 to stdout/stderr regardless of the console code page.

    The index carries CJK text and unicode punctuation (e.g. "↔"); on Windows a
    redirected stream would otherwise fail to encode it.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def report_translation(blocks: list[dict], html_lang: str,
                       draft_path: Path, db_path: Path | None = None,
                       prefix: str = "translation") -> None:
    """Print how much of the page is localized in the HTML language."""
    counts = status_counts(blocks)
    if not counts[STATUS_STALE] and not counts[STATUS_UNTRANSLATED]:
        print(f"{prefix}: all {counts[STATUS_OK]} entries localized "
              f"into {html_lang}", file=sys.stderr)
        return
    pending = [b["name"] for b in blocks if b.get("status") != STATUS_OK]
    shown = ", ".join(pending[:10])
    if len(pending) > 10:
        shown += f", ... (+{len(pending) - 10} more)"
    print(f"{prefix}: {counts[STATUS_OK]} localized, "
          f"{counts[STATUS_STALE]} need review, "
          f"{counts[STATUS_UNTRANSLATED]} untranslated: {shown}", file=sys.stderr)
    where = f"the database ({db_path})" if db_path else "the database"
    print(f"{prefix}: edit {where} -- or the draft ({draft_path}) written by "
          f"--emit-draft -- then rerun to localize them into {html_lang}",
          file=sys.stderr)


def report_sync(states: dict[str, str], prefix: str = "sync") -> None:
    """Print what the incremental comparison decided, one bucket per state."""
    order = (STATE_UNCHANGED, STATE_NEW, STATE_REBUILD, STATE_SRC_CHANGED,
             STATE_SOURCE_CHANGED, STATE_FILES_CHANGED)
    buckets: dict[str, list[str]] = {}
    for directory, state in states.items():
        buckets.setdefault(state, []).append(directory)
    parts: list[str] = []
    for state in order:
        items = sorted(buckets.get(state) or [])
        if not items:
            continue
        if state == STATE_UNCHANGED:
            parts.append(f"{len(items)} unchanged")
            continue
        shown = ", ".join(items[:10])
        if len(items) > 10:
            shown += f", ... (+{len(items) - 10} more)"
        parts.append(f"{len(items)} {state} ({shown})")
    if parts:
        print(f"{prefix}: " + "; ".join(parts), file=sys.stderr)


def consume_draft(draft_path: Path, keep: bool) -> None:
    """Remove the scratch draft once the page has been rendered with it."""
    if keep or not draft_path.is_file():
        return
    try:
        draft_path.unlink()
        print(f"{draft_path} consumed (temporary draft removed)", file=sys.stderr)
    except OSError as exc:
        print(f"warning: could not remove the draft {draft_path}: {exc}",
              file=sys.stderr)


# ---------------------------------------------------------------------------
# Continuous-integration hand-off
# ---------------------------------------------------------------------------

# A workflow that mentions the script (or the skill) counts as the index's
# generator: the repository installed the action so the files are rebuilt in CI,
# and a local run only has to maintain the database.
CI_MARKERS = ("build_skills_index.py", "yashi-build-skills-index")
WORKFLOW_SUFFIXES = (".yml", ".yaml")


def first_existing_dir(path: Path) -> Path:
    """Nearest existing directory at or above `path`, for the CI search."""
    current = path
    for _ in range(64):
        if current.is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return path


# How far above the index the workflow search may look. The action lives in the
# repository root, so a couple of levels cover a subdirectory layout; the bound
# also keeps an unrelated workflow far up the tree from hijacking the run.
CI_SEARCH_DEPTH = 4


def _workflow_runs_this_index(path: Path) -> bool:
    """True when a workflow file really calls this script.

    Comments are stripped first: a lint workflow that merely mentions the skill
    in a note must not take over the index.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover - unreadable file
        return False
    for line in text.splitlines():
        code = line.split("#", 1)[0]
        if any(marker in code for marker in CI_MARKERS):
            return True
    return False


def find_ci_workflow(start: Path) -> Path | None:
    """Return the workflow that renders this index, when the tree has one.

    The search walks up from the index location (bounded by CI_SEARCH_DEPTH and
    by the repository root) looking for a `.github/workflows/*.yml` that calls
    this script, so a repository can install the action once and let it own the
    generated files. No file name is assumed, and nothing outside the repository
    is considered.
    """
    base = first_existing_dir(start)
    for depth, directory in enumerate((base, *base.parents)):
        if depth > CI_SEARCH_DEPTH:
            break
        workflows = directory / ".github" / "workflows"
        if workflows.is_dir():
            for candidate in sorted(workflows.iterdir()):
                if (candidate.suffix.lower() in WORKFLOW_SUFFIXES
                        and _workflow_runs_this_index(candidate)):
                    return candidate
        if (directory / ".git").exists():
            break  # repository root: never look further up
    return None


def warn_filtered_overwrite(md_path: Path, entries: list[SkillEntry],
                            full_md: Path, full_html: Path, html_path: Path,
                            db_path: Path) -> None:
    """Warn when a filtered run would shrink an existing index in place.

    The fixed global paths are reported first. Beyond them the file itself is
    read: an index that already lists skills this run does not scan would lose
    those entries, whether it lives in the global directory or in a repository.
    """
    for label, target, full in (("--output", md_path, full_md),
                                ("--html", html_path, full_html),
                                ("--db", db_path, db_path_for(full_md))):
        if target == full:
            print(f"warning: {label} points at the full index ({full}) while "
                  f"--include is active; the complete index will be replaced by "
                  f"this filtered one", file=sys.stderr)
    selected = {e.directory for e in entries}
    if not md_path.is_file():
        return
    try:
        existing = {b.get("dir") or b["name"]
                    for b in parse_markdown_index(md_path.read_text(encoding="utf-8"))}
    except OSError:  # pragma: no cover - unreadable file
        return
    dropped = existing - selected
    if not dropped:
        return
    shown = ", ".join(sorted(dropped)[:5])
    if len(dropped) > 5:
        shown += ", ..."
    print(f"warning: {md_path} lists {len(existing)} skill(s) while this filtered "
          f"run writes {len(selected)}; {len(dropped)} existing entry(ies) would "
          f"be dropped: {shown}", file=sys.stderr)


def parse_include(values: list[str] | None) -> list[str]:
    """Flatten repeated / comma-separated --include values into glob patterns."""
    patterns: list[str] = []
    for value in values or []:
        for part in str(value).split(","):
            part = part.strip()
            if part and part not in patterns:
                patterns.append(part)
    return patterns


def filter_slug(patterns: list[str]) -> str:
    """Filename-safe tag for a filtered run: 'yashi-*' -> 'yashi'."""
    parts = []
    for pattern in patterns:
        cleaned = re.sub(r"[^A-Za-z0-9]+", "-", pattern).strip("-").lower()
        if cleaned:
            parts.append(cleaned)
    return "-".join(parts) or "filtered"


def select_entries(entries: list[SkillEntry], patterns: list[str]) -> list[SkillEntry]:
    """Keep only the skills whose directory name matches one of the globs."""
    if not patterns:
        return entries
    return [e for e in entries
            if any(fnmatch.fnmatchcase(e.directory, p) for p in patterns)]


def resolve_outputs(args: argparse.Namespace,
                    patterns: list[str]) -> tuple[Path, Path]:
    """Resolve --output / --html, giving a filtered run its own default names.

    A filtered index must not land on SKILLS.md / SKILLS.html by default, or
    building a subset would silently wipe the complete index. So 'yashi-*'
    defaults to SKILLS.yashi.md / SKILLS.yashi.html; explicit paths always win.

    An explicitly named Markdown index keeps its page next to it: --output
    README.md writes README.md together with README.html, instead of dropping a
    page into the global index directory.
    """
    md_default = Path(DEFAULT_MD_PATH).expanduser()
    html_default = Path(DEFAULT_HTML_PATH).expanduser()
    slug = filter_slug(patterns) if patterns else ""
    if args.output:
        md_path = Path(args.output).expanduser()
    elif slug:
        md_path = md_default.with_name(f"{md_default.stem}.{slug}{md_default.suffix}")
    else:
        md_path = md_default
    if args.html:
        html_path = Path(args.html).expanduser()
    elif args.output:
        html_path = md_path.with_suffix(".html")
    elif slug:
        html_path = html_default.with_name(
            f"{html_default.stem}.{slug}{html_default.suffix}")
    else:
        html_path = html_default
    return md_path, html_path


def resolve_md_defaults(args: argparse.Namespace,
                        md_path: Path) -> tuple[str, str, bool]:
    """Resolve --md-style and --md-lang, letting a README.md target drive them.

    A README is read by people, so it defaults to the human flavour in
    Simplified Chinese; explicit flags always win. Returns
    (style, raw language, whether the target is a README).
    """
    is_readme = md_path.stem.lower() == README_STEM
    style = args.md_style or ("human" if is_readme else DEFAULT_MD_STYLE)
    lang = args.md_lang or (README_MD_LANG if is_readme else DEFAULT_MD_LANG)
    return style, lang, is_readme


def main() -> int:
    force_utf8_streams()
    ap = argparse.ArgumentParser(
        description="Generate or update the skill index: the JSON database "
                    "(~/.agents/SKILLS.json), the Markdown index "
                    "(~/.agents/SKILLS.md) and the HTML page "
                    "(~/.agents/SKILLS.html), rebuilding only what changed."
    )
    ap.add_argument(
        "--skills-dir",
        default=None,
        help=f"Directory containing skill folders (default: {DEFAULT_SKILLS_DIR}, "
             f"or the directory recorded in the database under --render-only)",
    )
    ap.add_argument(
        "--collection",
        default=None,
        metavar="NAME",
        help="Name of this skill collection, used in the human-readable title "
             "(usually the repository name). Defaults to the value recorded in "
             "the database, then to the skills directory's parent folder. Set it "
             "once when the database is created, so a local run and a CI "
             "checkout -- whose folder names differ -- render the same title",
    )
    ap.add_argument(
        "--output",
        default=None,
        help="Markdown index file (name and location). Default: "
             f"{DEFAULT_MD_PATH}, or {Path(DEFAULT_MD_PATH).stem}.<tag>"
             f"{Path(DEFAULT_MD_PATH).suffix} when --include is used. "
             "A target named README.md defaults to the human flavour in "
             "Simplified Chinese, and gets a sibling .html page.",
    )
    ap.add_argument(
        "--html",
        default=None,
        help="HTML index file (name and location). Default: "
             f"{DEFAULT_HTML_PATH}, or {Path(DEFAULT_HTML_PATH).stem}.<tag>"
             f"{Path(DEFAULT_HTML_PATH).suffix} when --include is used, or the "
             "--output path with an .html suffix when --output is given",
    )
    ap.add_argument(
        "--db",
        default=None,
        help="JSON database file (name and location). Default: the Markdown "
             "index path with a .json suffix, i.e. "
             f"{Path(DEFAULT_MD_PATH).with_suffix(DB_SUFFIX)}. The database is "
             "written first and is the single carrier of the prose and of the "
             "file inventory; --output and --html are rendered from it.",
    )
    ap.add_argument(
        "--db-only",
        action="store_true",
        help="Update the database only and skip the generated files; used in a "
             "repository where the action renders them (see also --ignore-ci)",
    )
    ap.add_argument(
        "--render-only",
        action="store_true",
        help="Render the outputs from the existing database without scanning "
             "the skills directory or touching the database; fails when there "
             "is no database yet. This is what CI runs.",
    )
    ap.add_argument(
        "--ignore-ci",
        action="store_true",
        help="Do not look for a workflow that renders this index; write the "
             "generated files even in a repository that has the action",
    )
    ap.add_argument(
        "--include", action="append", default=None, metavar="GLOB",
        help="Only index the skills whose directory name matches this glob "
             "(repeatable, comma-separated lists also accepted), e.g. "
             "--include 'yashi-*'. Default: every skill. Filtered runs write to "
             "their own files and seed their translations from the full index.",
    )
    ap.add_argument("--no-html", action="store_true",
                    help="Skip HTML output")
    ap.add_argument(
        "--html-only",
        action="store_true",
        help="Skip the Markdown output and only rebuild the database and the "
             "HTML page (used after editing the prose or a translation in the "
             "database)",
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="Preview without writing files")
    ap.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if validation issues exist or outputs are stale",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Write outputs even when validation issues were found",
    )
    ap.add_argument(
        "--rebuild",
        action="store_true",
        help="Ignore the incremental comparison: regenerate every skill's "
             "source-language prose from its front matter and mark every "
             "translation for review",
    )
    ap.add_argument(
        "--mark-reviewed",
        action="store_true",
        help="Confirm the pending translations (status stale) as reviewed "
             "without touching their text, e.g. after checking that an upstream "
             "edit did not affect them",
    )
    ap.add_argument(
        "--md-style", choices=MD_STYLES, default=None, metavar="STYLE",
        help="Markdown flavour: 'ai' (lightweight directory for agents, "
             "default) or 'human' (category grouping, table of contents, "
             "example prompts and folder links -- the same content rules as the "
             "HTML page). Defaults to 'human' when --output names a README.md",
    )
    ap.add_argument(
        "--md-lang", default=None, metavar="LANG",
        help=f"Language of the Markdown index (default: {DEFAULT_MD_LANG}, or "
             f"{README_MD_LANG} for a README.md target); "
             f"supported: {', '.join(SUPPORTED_LANGS)}",
    )
    ap.add_argument(
        "--html-lang", default=None, metavar="LANG",
        help=f"Language of the HTML page (default: {DEFAULT_HTML_LANG}); "
             f"supported: {', '.join(SUPPORTED_LANGS)}",
    )
    ap.add_argument(
        "--draft-path", default=None, metavar="PATH",
        help="Temporary translation draft file (default: "
             f"<temp>/{DRAFT_DIR_NAME}/<html stem>.<lang>.md)",
    )
    ap.add_argument(
        "--emit-draft", action="store_true",
        help="Write the translation draft and exit without touching the "
             "database or the index outputs",
    )
    ap.add_argument(
        "--keep-draft", action="store_true",
        help="Keep the translation draft after it has been merged into the "
             "database",
    )
    args = ap.parse_args()

    if args.html_only and args.no_html:
        print("error: --html-only and --no-html contradict each other",
              file=sys.stderr)
        return 1
    if args.db_only and args.render_only:
        print("error: --db-only and --render-only contradict each other",
              file=sys.stderr)
        return 1
    if args.db_only and args.html_only:
        print("error: --db-only and --html-only contradict each other",
              file=sys.stderr)
        return 1
    if args.render_only and args.emit_draft:
        print("error: --render-only renders the stored database and never "
              "updates it, so it cannot emit a translation draft",
              file=sys.stderr)
        return 1

    include = parse_include(args.include)
    md_path, html_path = resolve_outputs(args, include)
    db_path = Path(args.db).expanduser() if args.db else db_path_for(md_path)
    md_style, md_lang_raw, readme_target = resolve_md_defaults(args, md_path)
    html_lang_raw = args.html_lang or DEFAULT_HTML_LANG
    md_lang = normalize_lang(md_lang_raw)
    html_lang = normalize_lang(html_lang_raw)
    for flag, raw, resolved in (("--md-lang", md_lang_raw, md_lang),
                                ("--html-lang", html_lang_raw, html_lang)):
        if not resolved:
            print(f"error: unsupported language for {flag}: {raw!r} "
                  f"(supported: {', '.join(SUPPORTED_LANGS)})", file=sys.stderr)
            return 1

    if readme_target:
        derived = []
        if not args.md_style:
            derived.append(f"--md-style {md_style}")
        if not args.md_lang:
            derived.append(f"--md-lang {md_lang}")
        if derived:
            print(f"note: {md_path.name} is a human-facing index; using "
                  f"{', '.join(derived)}", file=sys.stderr)

    # --render-only makes the database the only input: the skills directory, the
    # prose of every language and the prompts are all recorded in it.
    stored_db: dict = {}
    if args.render_only:
        stored_db = load_db(db_path)
        if not normalize_db(stored_db):
            print(f"error: --render-only needs an existing database: {db_path}\n"
                  f"Run the full pipeline once (without --render-only) to create "
                  f"it.", file=sys.stderr)
            return 1
    if args.skills_dir:
        skills_dir = Path(args.skills_dir).expanduser()
    elif args.render_only and stored_db.get("skills_dir"):
        skills_dir = Path(stored_db["skills_dir"]).expanduser()
    else:
        skills_dir = Path(DEFAULT_SKILLS_DIR).expanduser()

    # The collection name is part of the rendered title. A database records the
    # name it was created with, so a local run and a CI checkout -- whose folder
    # names differ -- still render the very same file. A blank --collection means
    # "keep what is recorded" rather than "forget it".
    requested_collection = (args.collection or "").strip()
    if requested_collection:
        collection = requested_collection
    elif args.render_only:
        collection = stored_db.get("collection") or ""
    elif db_path.is_file():
        collection = load_db(db_path).get("collection") or ""
    else:
        collection = ""

    # CI hand-off: a repository that installs the action lets it own the
    # generated files, so a plain local run only maintains the database. The
    # workflow is looked for next to the index and next to the skills it indexes;
    # a dry run or a check changes nothing and keeps its own report.
    db_only = args.db_only
    if not args.ignore_ci and not args.db_only and not args.render_only:
        workflow = find_ci_workflow(md_path.parent) or find_ci_workflow(skills_dir)
        if workflow is not None and not args.check and not args.dry_run:
            db_only = True
            print(f"note: {workflow} renders this index; updating the database "
                  f"only (pass --ignore-ci to write the files here as well)",
                  file=sys.stderr)

    full_md = Path(DEFAULT_MD_PATH).expanduser()
    full_html = Path(DEFAULT_HTML_PATH).expanduser()
    full_db = db_path_for(full_md)
    draft_path = (Path(args.draft_path).expanduser() if args.draft_path
                  else default_draft_path(html_path, html_lang))
    # Translations are written into the HTML language; the source language needs
    # no draft, because it is generated from the front matter.
    needs_translation = html_lang != SOURCE_LANG

    entries: list[SkillEntry] = []
    all_names: list[str] = []
    issues: list[str] = []
    if not args.render_only:
        if not skills_dir.is_dir():
            print(f"error: skills directory not found: {skills_dir}",
                  file=sys.stderr)
            return 1

        entries = discover_skills(skills_dir)
        all_names = [e.directory for e in entries]
        if include:
            entries = select_entries(entries, include)
            if not entries:
                print(f"error: --include matched no skill out of {len(all_names)}. "
                      f"Patterns: {', '.join(include)}", file=sys.stderr)
                return 1
            print(f"include: {len(entries)} of {len(all_names)} skill(s) match "
                  f"{', '.join(include)}", file=sys.stderr)
        issues = validate(entries)

        if issues:
            print("Validation issues found (not guessing; resolve before indexing):",
                  file=sys.stderr)
            for issue in issues:
                print(f"  - {issue}", file=sys.stderr)
            print(file=sys.stderr)

        if include:
            warn_filtered_overwrite(md_path, entries, full_md, full_html,
                                    html_path, db_path)

    if args.render_only:
        # The database is both the record and the source: nothing is scanned,
        # nothing is validated, nothing is written back to it.
        records = list(normalize_db(stored_db).values())
        if include:
            records = [rec for rec in records
                       if any(fnmatch.fnmatchcase(rec.get("dir") or rec.get("name", ""), p)
                              for p in include)]
            if not records:
                print(f"error: --include matched no record in {db_path}. "
                      f"Patterns: {', '.join(include)}", file=sys.stderr)
                return 1
            print(f"include: {len(records)} record(s) selected from {db_path}",
                  file=sys.stderr)
        states: dict[str, str] = {}
        all_names = [rec["dir"] for rec in records]
        examples = load_examples()
        draft_blocks: list[dict] = []
        new_db = stored_db
        db_text = ""
    else:
        prepared = prepare(entries)
        examples = load_examples()
        old_db = load_db(db_path)
        stored = normalize_db(old_db)

        # A database that does not exist yet is seeded from the previous
        # outputs, so an index built by an older version of this script
        # migrates without losing the wording somebody may have polished.
        source_seed: dict[str, dict] = {}
        localized_seed: dict[str, dict] = {}
        if not stored:
            if md_path.is_file():
                source_seed = merge_seed_tables(
                    source_seed, seed_source_from_markdown(md_path))
            if html_path.is_file():
                localized_seed = merge_seed_tables(
                    localized_seed, seed_localized_from_html(html_path, html_lang))
            if md_path != full_md and full_md.is_file():
                source_seed = merge_seed_tables(
                    source_seed, seed_source_from_markdown(full_md))
            # The full database is a better source than the full page: it
            # carries the review state of every slot, while a page only carries
            # what it rendered. The target's own outputs stay the most relevant
            # source, so they are merged first and win.
            if full_db != db_path and full_db.is_file():
                shared = load_db(full_db)
                source_seed = merge_seed_tables(
                    source_seed, locale_seed_from_db(shared, SOURCE_LANG))
                localized_seed = merge_seed_tables(
                    localized_seed, locale_seed_from_db(shared, html_lang))
            if full_html.is_file():
                localized_seed = merge_seed_tables(
                    localized_seed, seed_localized_from_html(full_html, html_lang))
            # Only the skills this run scans are worth seeding; a seed for
            # anything else is noise (a foreign index) or a skill that is gone.
            wanted_dirs = {e.directory for e in entries}
            wanted_names = {e.name or e.directory for e in entries}
            source_seed = {k: v for k, v in source_seed.items() if k in wanted_dirs}
            localized_seed = {
                k: v for k, v in localized_seed.items() if k in wanted_names}
            if source_seed or localized_seed:
                print(f"seed: {len(source_seed)} source and "
                      f"{len(localized_seed)} localized entr(ies) recovered from "
                      f"the previous outputs (the database is new)",
                      file=sys.stderr)

        now = now_local_iso()
        records, states = sync_records(
            entries, prepared, old_db, rebuild=args.rebuild,
            mark_reviewed=args.mark_reviewed, source_seed=source_seed,
            localized_seed=localized_seed, examples=examples, now=now)

        # --emit-draft: produce the temporary translation draft, write nothing
        # else.
        if args.emit_draft:
            if not needs_translation:
                print(f"note: --html-lang ({html_lang}) is the source language; "
                      f"no translation draft is needed", file=sys.stderr)
                return 0
            draft_text = render_draft(blocks_for_lang(records, SOURCE_LANG),
                                      locale_previous(records, html_lang), html_lang)
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            unchanged = (draft_path.is_file()
                         and draft_path.read_text(encoding="utf-8") == draft_text)
            draft_path.write_text(draft_text, encoding="utf-8", newline="\n")
            print(f"{draft_path} {'already up to date' if unchanged else 'written'}"
                  f" ({len(records)} entries, language: {html_lang})")
            report_translation(blocks_for_lang(records, html_lang), html_lang,
                               draft_path, db_path)
            return 0

        # A hand-edited translation draft (--emit-draft) is folded back in
        # before anything is rendered.
        draft_blocks = (parse_markdown_index(draft_path.read_text(encoding="utf-8"))
                        if draft_path.is_file() else [])
        if draft_blocks:
            merged = merge_draft_records(records, draft_blocks, html_lang,
                                         blocks_for_lang(records, SOURCE_LANG))
            if merged:
                print(f"draft: {merged} entr(ies) merged from {draft_path}",
                      file=sys.stderr)

        # Records this run did not scan survive a filtered run; on a full scan
        # they are simply gone, and dropping them is the point.
        scanned = {e.directory for e in entries}
        kept = ([rec for d, rec in stored.items() if d not in scanned]
                if include else [])
        gone = (sorted(d for d in stored if d not in scanned)
                if not include else [])
        if kept:
            print(f"db: kept {len(kept)} record(s) outside this filtered scan",
                  file=sys.stderr)
        if gone:
            shown = ", ".join(gone[:10]) + (", ..." if len(gone) > 10 else "")
            print(f"db: dropped {len(gone)} record(s) whose skill is gone: {shown}",
                  file=sys.stderr)

        all_records = records + kept
        new_db = build_db(all_records, skills_dir=skills_dir, md_path=md_path,
                          html_path=html_path, old_db=old_db, now=now,
                          collection=collection)
        db_text = dump_db(new_db)

    md_blocks = blocks_for_lang(records, md_lang)
    html_blocks = blocks_for_lang(records, html_lang)
    report_sync(states)

    expected_md = (render_markdown_human(md_blocks, md_lang, skills_dir, md_path,
                                         collection or None)
                   if md_style == "human"
                   else render_markdown(md_blocks, md_lang, skills_dir, md_path))
    expected_html = (render_html(html_blocks, skills_dir, html_lang, html_path)
                     if not args.no_html else "")

    # Example-prompt coverage is reported whenever examples reach an output:
    # the HTML page, or a human Markdown index.
    if not args.no_html or md_style == "human":
        canonical = False
        try:
            canonical = (skills_dir.resolve()
                         == Path(DEFAULT_SKILLS_DIR).expanduser().resolve())
        except OSError:  # pragma: no cover - unusable path
            canonical = False
        report_examples(md_blocks, examples, all_names, canonical=canonical)

    def current_text(path: Path) -> str:
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    if args.check:
        if issues:
            print("check: validation issues exist", file=sys.stderr)
            return 2
        stale = []
        if not args.render_only and current_text(db_path) != db_text:
            stale.append(str(db_path))
        if not args.html_only and current_text(md_path) != expected_md:
            stale.append(str(md_path))
        if not args.no_html and current_text(html_path) != expected_html:
            stale.append(str(html_path))
        if stale:
            print("check: missing or out of date: " + ", ".join(stale),
                  file=sys.stderr)
            return 3
        if needs_translation and not args.no_html:
            report_translation(html_blocks, html_lang, draft_path, db_path)
        print(f"check: all outputs up to date ({len(records)} skills)")
        return 0

    if args.dry_run:
        sys.stdout.write(expected_md)
        msg = (f"\n[dry-run] {len(records)} skills, {len(issues)} issue(s); "
               f"db -> {db_path}")
        if not args.no_html:
            msg += f"; html -> {html_path} (language: {html_lang})"
        print(msg, file=sys.stderr)
        if needs_translation and not args.no_html:
            report_translation(html_blocks, html_lang, draft_path, db_path)
        return 2 if (issues and not args.force) else 0

    if issues and not args.force:
        print("Outputs not written. Fix the issues above, or rerun with "
              "--force to write partial indexes.", file=sys.stderr)
        return 2

    if not args.render_only:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_unchanged = current_text(db_path) == db_text
        db_path.write_text(db_text, encoding="utf-8", newline="\n")
        print(f"{db_path} {'already up to date' if db_unchanged else 'written'}"
              f" ({len(new_db.get('skills') or [])} records)")

    # --db-only (explicit, or implied by a repository that has the action)
    # maintains the database and leaves the generated files to CI.
    if not db_only:
        if not args.html_only:
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_unchanged = current_text(md_path) == expected_md
            md_path.write_text(expected_md, encoding="utf-8", newline="\n")
            print(f"{md_path} {'already up to date' if md_unchanged else 'written'}"
                  f" ({len(records)} skills, language: {md_lang})")

        if not args.no_html:
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_unchanged = current_text(html_path) == expected_html
            html_path.write_text(expected_html, encoding="utf-8", newline="\n")
            print(f"{html_path} {'already up to date' if html_unchanged else 'written'}"
                  f" ({len(records)} skills, language: {html_lang})")
            if needs_translation:
                report_translation(html_blocks, html_lang, draft_path, db_path)
    elif needs_translation:
        report_translation(html_blocks, html_lang, draft_path, db_path)

    if draft_blocks:
        consume_draft(draft_path, args.keep_draft)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
