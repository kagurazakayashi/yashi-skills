#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync-skills.py - Cross-platform sync for the user's global skills directory.

Mirrors skills from six fixed GitHub repositories into ~/.agents/skills/:

  dart    : dart-*    (source: flutter/agent-plugins, overrides dart-lang/skills)
  flutter : flutter-* (source: flutter/agent-plugins)
  golang  : golang-*  (source: samber/cc-skills-golang, excludes golang-samber-*)
  mindmap : mindmap*  (source: galiacheng/mindmap-skills)
  yashi   : yashi-*   (source: kagurazakayashi/yashi-skills, repository root)

ui-ux-pro-max is installed via the official npm CLI (ui-ux-pro-max-cli) and is
NOT handled here; the commands are printed at the end.

Rules per prefix: online-only -> add; local-only -> delete; both -> mirror to the
online version. File comparison is content based (size then SHA-256), so fresh
checkouts do not trigger spurious updates. A group whose source directory holds
no matching skill is never pruned: an empty or not-yet-pushed repository would
otherwise wipe the local copies (including this skill).

Examples:
  python3 scripts/sync-skills.py
  python3 scripts/sync-skills.py --include flutter golang
  python3 scripts/sync-skills.py --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# group, clone dir, url, skills subpath inside the repo, prefix regex
# The yashi group reads kagurazakayashi/yashi-skills, whose skill folders sit in
# the repository's skills/ subdirectory (same convention as the other sources);
# the repository root also holds README.md, .gitignore and .git, which the
# yashi-* prefix keeps out of the selection.
REPOS = [
    {"group": "flutter", "dir": "flutter",
     "url": "https://github.com/flutter/agent-plugins.git",
     "sub": "skills", "match": r"^flutter-"},
    {"group": "dart", "dir": "flutter",
     "url": "https://github.com/flutter/agent-plugins.git",
     "sub": "skills", "match": r"^dart-"},
    {"group": "golang", "dir": "golang",
     "url": "https://github.com/samber/cc-skills-golang.git",
     "sub": "skills", "match": r"^golang-"},
    {"group": "mindmap", "dir": "mindmap",
     "url": "https://github.com/galiacheng/mindmap-skills.git",
     "sub": "skills", "match": r"^mindmap"},
    {"group": "yashi", "dir": "yashi-skills",
     "url": "https://github.com/kagurazakayashi/yashi-skills.git",
     "sub": "skills", "match": r"^yashi-"},
]

# Go CLI dependencies referenced by the golang skills (best-effort, idempotent).
GO_BINS = [
    ("gopls", "golang.org/x/tools/gopls@latest"),
    ("dlv", "github.com/go-delve/delve/cmd/dlv@latest"),
    ("swag", "github.com/swaggo/swag/cmd/swag@latest"),
    ("gotests", "github.com/cweill/gotests/gotests@latest"),
    ("govulncheck", "golang.org/x/vuln/cmd/govulncheck@latest"),
]

ALL_GROUPS = ["dart", "flutter", "golang", "mindmap", "yashi"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def run_quiet(cmd, env=None, cwd=None):
    """Run a command, tolerating stderr output (git/go write progress there)."""
    subprocess.run(cmd, cwd=cwd, env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   check=False)


def default_branch_ref(target: Path) -> str:
    """Return e.g. 'refs/remotes/origin/main' via origin/HEAD, else a guess."""
    try:
        out = subprocess.run(
            ["git", "-C", str(target), "symbolic-ref", "refs/remotes/origin/HEAD"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
            text=True).stdout.strip()
        if out:
            return out
    except Exception:
        pass
    return "origin/HEAD"


def ensure_repo(entry, work: Path, proxy: str):
    """Clone, repair or refresh a repository so its working tree is current."""
    target = work / entry["dir"]
    git_dir = target / ".git"

    if not git_dir.exists():
        print(f"[clone] {entry['url']}")
        target.parent.mkdir(parents=True, exist_ok=True)
        run_quiet(["git", "-c", f"http.proxy={proxy}",
                   "-c", f"https.proxy={proxy}",
                   "clone", "--depth=1", entry["url"], str(target)])
        if not git_dir.exists():
            raise RuntimeError(f"clone failed: {entry['url']}")
        return

    # Partial/aborted checkout: only .git at the root -> re-clone.
    tree_items = [p for p in target.iterdir() if p.name != ".git"]
    if not tree_items:
        print(f"[repair partial checkout] {entry['url']}")
        shutil.rmtree(target, ignore_errors=True)
        run_quiet(["git", "-c", f"http.proxy={proxy}",
                   "-c", f"https.proxy={proxy}",
                   "clone", "--depth=1", entry["url"], str(target)])
        if not git_dir.exists():
            raise RuntimeError(f"re-clone failed: {entry['url']}")
        return

    # Refresh existing clone; git only rewrites changed files.
    print(f"[fetch] {entry['url']}")
    run_quiet(["git", "-C", str(target), "fetch", "--depth=1", "origin"])
    ref = default_branch_ref(target)
    run_quiet(["git", "-C", str(target), "reset", "--hard", ref])


def mirror_tree(src: Path, dst: Path, dry_run: bool):
    """Mirror src onto dst (content based). Returns list of (action, relpath)."""
    actions = []
    src_files: set[Path] = set()

    for root, _dirs, files in os.walk(src):
        rel_root = Path(root).relative_to(src)
        for name in files:
            rel = rel_root / name
            src_files.add(rel)
            s, d = src / rel, dst / rel
            needs = (not d.exists()
                     or s.stat().st_size != d.stat().st_size
                     or file_sha256(s) != (file_sha256(d) if d.exists() else None))
            if needs:
                actions.append(("copy", str(rel)))
                if not dry_run:
                    d.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(s, d)

    if dst.exists():
        # Remove files not present in source.
        for root, _dirs, files in os.walk(dst):
            rel_root = Path(root).relative_to(dst)
            for name in files:
                rel = rel_root / name
                if rel not in src_files:
                    actions.append(("remove", str(rel)))
                    if not dry_run:
                        (dst / rel).unlink()
        # Remove directories left empty (bottom-up), i.e. pruned from source.
        for root, dirs, _files in os.walk(dst, topdown=False):
            for d in dirs:
                p = Path(root) / d
                try:
                    if not any(p.iterdir()):
                        if not dry_run:
                            p.rmdir()
                except OSError:
                    pass
    return actions


# ---------------------------------------------------------------------------
# Main sync
# ---------------------------------------------------------------------------

def sync(include, dry_run, proxy):
    skills_root = Path.home() / ".agents" / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.gettempdir()) / "skill-sync"
    work.mkdir(parents=True, exist_ok=True)

    changes = []  # (kind, skill, detail)

    # 1. Clone / refresh only repos needed by the selected groups.
    needed_dirs = sorted({r["dir"] for r in REPOS if r["group"] in include})
    for d in needed_dirs:
        entry = next(r for r in REPOS if r["dir"] == d)
        ensure_repo(entry, work, proxy)

    # 2. Mirror each selected group.
    for entry in REPOS:
        if entry["group"] not in include:
            continue
        src_root = work / entry["dir"] / entry["sub"]
        if not src_root.is_dir():
            print(f"WARNING: missing source: {src_root}", file=sys.stderr)
            continue

        online = []
        prefix = re.compile(entry["match"])
        for skill_dir in sorted(src_root.iterdir()):
            if not skill_dir.is_dir() or not prefix.match(skill_dir.name):
                continue
            if entry["group"] == "golang" and skill_dir.name.startswith("golang-samber-"):
                continue  # skip samber's own libraries
            online.append(skill_dir.name)

            dst = skills_root / skill_dir.name
            if not dst.exists():
                changes.append(("ADDED", skill_dir.name, ""))
                if not dry_run:
                    shutil.copytree(skill_dir, dst)
            else:
                actions = mirror_tree(skill_dir, dst, dry_run)
                if actions:
                    kind = "WOULD-UPDATE" if dry_run else "UPDATED"
                    changes.append((kind, skill_dir.name, f"{len(actions)} files"))
                else:
                    changes.append(("UNCHANGED", skill_dir.name, ""))

        # An empty selection means the repository is new, empty or not pushed
        # yet. Pruning then would delete every local skill of this group -- the
        # yashi group included this very skill -- so deletions are skipped and
        # the situation is reported instead.
        if not online:
            print(f"WARNING: no {entry['group']} skill found in {src_root}; "
                  f"skipping deletions for this group", file=sys.stderr)
            continue

        # 3. Delete local skills under the prefix that are absent online.
        for local_dir in sorted(skills_root.iterdir()):
            if not local_dir.is_dir() or not prefix.match(local_dir.name):
                continue
            stale = False
            if entry["group"] == "golang" and local_dir.name.startswith("golang-samber-"):
                stale = True
            elif local_dir.name not in online:
                stale = True
            if stale:
                kind = "WOULD-DELETE" if dry_run else "DELETED"
                changes.append((kind, local_dir.name, ""))
                if not dry_run:
                    shutil.rmtree(local_dir, ignore_errors=True)

    # 4. Install Go CLI dependencies (idempotent).
    if "golang" in include and not dry_run and shutil.which("go"):
        env = dict(os.environ)
        env["HTTPS_PROXY"] = proxy
        env["HTTP_PROXY"] = proxy
        env["GOPROXY"] = "https://goproxy.cn,direct"
        for exe, pkg in GO_BINS:
            if not shutil.which(exe):
                print(f"[go install] {pkg}")
                run_quiet(["go", "install", pkg], env=env)
                changes.append(("GO-INSTALL", exe, pkg))

    # 5. Report.
    print("\n===== Change summary =====")
    width = max((len(s) for _k, s, _d in changes), default=10)
    for kind, skill, detail in sorted(changes, key=lambda c: (c[0], c[1])):
        print(f"{kind:<12} {skill:<{width}} {detail}")

    totals = {}
    for kind, _s, _d in changes:
        totals[kind] = totals.get(kind, 0) + 1
    print("\nTotals: " + "  ".join(f"{k}={v}" for k, v in sorted(totals.items())))

    print("""
Next (manual, official installer):
  npm install -g ui-ux-pro-max-cli
  rm -rf ~/.agents/skills/ui-ux-pro-max        # Windows: Remove-Item -Recurse -Force
  uipro init --ai universal --global

Then verify global AGENTS.md references:
  ~/.dsh/AGENTS.md , ~/.config/opencode/AGENTS.md , ~/.qoder-cn/AGENTS.md
""")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--include", nargs="+", choices=ALL_GROUPS, default=ALL_GROUPS,
                    help="groups to sync (default: all)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report changes without writing or deleting")
    ap.add_argument("--proxy", default="http://127.0.0.1:23333",
                    help="proxy URL (default %(default)s)")
    args = ap.parse_args(argv)
    sync(args.include, args.dry_run, args.proxy)


if __name__ == "__main__":
    main()
