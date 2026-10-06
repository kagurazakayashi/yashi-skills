#!/usr/bin/env node
/**
 * 把一个 dsh profile 的插件与配置迁移到另一个 profile（默认 web -> desktop）。
 *
 * 做四件事（顺序很重要，见 SKILL.md「核心规则」第 3 条）：
 *   1. 备份目标 profile 的 package.json / cordis.patch.yml / pnpm 配置
 *   2. 复制源 profile 的 pnpm 配置（.npmrc / pnpm-workspace.yaml / pnpm.cmd / pnpm-lock.yaml）
 *   3. 写 dependencies -> pnpm install -> 写 dsh.profile.bundles
 *   4. 把源 profile 的 cordis.patch.yml 顶层条目按 id 差集追加到目标（文本级合并，保留双方注释）
 *
 * 用法:
 *   node migrate-profile.mjs --from web --to desktop --dry-run
 *   node migrate-profile.mjs --from web --to desktop
 *
 * 兼容 Windows / macOS / Linux，Node >= 18，无第三方依赖。
 */
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

const argv = process.argv.slice(2);
const has = (name) => argv.includes(name);
function opt(name, fallback) {
  const index = argv.indexOf(name);
  if (index === -1) return fallback;
  const next = argv[index + 1];
  return next && !next.startsWith("--") ? next : true;
}
function optAll(name) {
  const out = [];
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] !== name) continue;
    const next = argv[index + 1];
    if (next && !next.startsWith("--")) out.push(next);
  }
  return out;
}

const from = String(opt("--from", "web"));
const to = String(opt("--to", "desktop"));
const dryRun = has("--dry-run");
const noInstall = has("--no-install") || dryRun;
const noPatch = has("--no-patch");
const skipEntries = new Set(["web-runtime", ...optAll("--skip-entry")]);
const pnpmOverride = typeof opt("--pnpm") === "string" ? opt("--pnpm") : null;
const PNPM_FILES = [".npmrc", "pnpm-workspace.yaml", "pnpm.cmd", "pnpm-lock.yaml"];

const HOME = process.env.DSH_HOME || path.join(process.env.USERPROFILE || process.env.HOME || ".", ".dsh");
const fromDir = path.join(HOME, "profiles", from);
const toDir = path.join(HOME, "profiles", to);

const steps = [];
const warnings = [];

function log(line = "") {
  console.log(line);
}
function fail(message) {
  console.error(`错误: ${message}`);
  process.exit(1);
}
function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}
function stamp() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
}

/** 按顶层 `- ` 切分 patch 文本，返回 { id, body }；保留注释与排版（不做 YAML 序列化）。 */
function splitEntries(text) {
  const blocks = [];
  let current = null;
  for (const line of text.split(/\r?\n/)) {
    if (/^- /.test(line)) {
      if (current) blocks.push(current);
      current = [line];
    } else if (current) {
      current.push(line);
    }
  }
  if (current) blocks.push(current);
  return blocks.map((lines) => {
    const body = lines.join("\n").replace(/\s+$/, "");
    const matched = body.match(/^- id:\s*([^\s#]+)/m);
    return { id: matched ? matched[1] : null, body };
  });
}

// ---- 前置检查 ----
const fromPkgPath = path.join(fromDir, "package.json");
if (!fs.existsSync(fromPkgPath)) fail(`源 profile 不存在或缺少 package.json: ${fromDir}`);
if (!fs.existsSync(toDir)) fail(`目标 profile 不存在: ${toDir}（桌面版需至少启动过一次以初始化 profile）`);

const fromPkg = readJson(fromPkgPath);
const toPkgPath = path.join(toDir, "package.json");
const toPkg = fs.existsSync(toPkgPath) ? readJson(toPkgPath) : { name: `dsh-profile-${to}`, private: true };
const toBundles = (toPkg.dsh && toPkg.dsh.profile && toPkg.dsh.profile.bundles) || [];
const fromBundles = (fromPkg.dsh && fromPkg.dsh.profile && fromPkg.dsh.profile.bundles) || [];
const mergedDeps = { ...(toPkg.dependencies || {}), ...(fromPkg.dependencies || {}) };
const mergedBundles = [...new Set([...toBundles, ...fromBundles])];

log(`源 profile : ${fromDir}`);
log(`目标 profile: ${toDir}`);
log(`模式       : ${dryRun ? "预演（不写文件、不安装）" : "正式迁移"}`);
log("");

// ---- 1. 备份 ----
const backupDir = path.join(toDir, `.backup-${from}-import-${stamp()}`);
const backupFiles = ["package.json", "cordis.patch.yml", "cordis.yml", ...PNPM_FILES];
if (!dryRun) fs.mkdirSync(backupDir, { recursive: true });
const backedUp = [];
for (const name of backupFiles) {
  const src = path.join(toDir, name);
  if (!fs.existsSync(src)) continue;
  if (!dryRun) fs.copyFileSync(src, path.join(backupDir, name));
  backedUp.push(name);
}
steps.push(`备份 ${backedUp.length} 个文件 -> ${path.relative(toDir, backupDir)}`);
steps.push(`备份内容: ${backedUp.join(", ")}`);

// ---- 2. 复制 pnpm 配置 ----
const copied = [];
for (const name of PNPM_FILES) {
  const src = path.join(fromDir, name);
  if (!fs.existsSync(src)) continue;
  if (!dryRun) fs.copyFileSync(src, path.join(toDir, name));
  copied.push(name);
}
steps.push(`复制 pnpm 配置: ${copied.join(", ")}`);
if (!copied.includes("pnpm-workspace.yaml")) {
  warnings.push("源 profile 没有 pnpm-workspace.yaml；若依赖含原生模块，需手工补 allowBuilds / onlyBuiltDependencies，否则会 ERR_PNPM_IGNORED_BUILDS");
}

// ---- 3. package.json（两阶段）----
const stageDeps = {
  ...toPkg,
  dependencies: mergedDeps,
  dsh: { ...(toPkg.dsh || {}), profile: { ...((toPkg.dsh && toPkg.dsh.profile) || {}), bundles: toBundles } },
};
const stageBundles = {
  ...stageDeps,
  dsh: { ...(stageDeps.dsh || {}), profile: { ...((stageDeps.dsh && stageDeps.dsh.profile) || {}), bundles: mergedBundles } },
};
const writePkg = (payload) => {
  if (!dryRun) fs.writeFileSync(toPkgPath, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
};
writePkg(stageDeps);
steps.push(`写入 dependencies（${Object.keys(mergedDeps).length} 个，bundle 暂不新增，避免热重载时依赖未就绪）`);

// ---- 4. pnpm install ----
function findPnpm() {
  const candidates = [];
  if (pnpmOverride) candidates.push({ cmd: pnpmOverride, args: [] });
  if (process.env.DSH_PNPM) candidates.push({ cmd: process.env.DSH_PNPM, args: [] });
  const local = path.join(toDir, process.platform === "win32" ? "pnpm.cmd" : "pnpm");
  if (fs.existsSync(local)) candidates.push({ cmd: local, args: [] });
  const runtimes = path.join(HOME, "dsh-runtimes");
  if (fs.existsSync(runtimes)) {
    for (const name of fs.readdirSync(runtimes)) {
      const entry = path.join(runtimes, name, "dependencies", "pnpm", "bin", "pnpm.cjs");
      if (fs.existsSync(entry)) candidates.push({ cmd: process.execPath, args: [entry] });
    }
  }
  candidates.push({ cmd: process.platform === "win32" ? "pnpm.cmd" : "pnpm", args: [] });
  return candidates;
}

let installOk = null;
if (noInstall) {
  steps.push("跳过 pnpm install（--no-install 或 --dry-run）");
} else {
  const candidates = findPnpm();
  for (const candidate of candidates) {
    log(`[install] ${candidate.cmd} install  (cwd=${toDir})`);
    const result = spawnSync(candidate.cmd, [...candidate.args, "install", "--reporter=append-only"], {
      cwd: toDir,
      stdio: "inherit",
      shell: process.platform === "win32",
    });
    if (result.error && result.error.code === "ENOENT") continue;
    installOk = result.status === 0;
    break;
  }
  if (installOk === null) fail("找不到可用的 pnpm，请用 --pnpm <path> 指定");
  if (!installOk) fail("pnpm install 失败，见上方输出；修正后重跑本脚本（已备份，可回滚）");
  steps.push("pnpm install 完成");
}

// 安装成功后才注册 bundles
if (installOk !== false) {
  writePkg(stageBundles);
  steps.push(`写入 dsh.profile.bundles（共 ${mergedBundles.length} 个）`);
}

// ---- 5. 合并 cordis.patch.yml ----
const fromPatchPath = path.join(fromDir, "cordis.patch.yml");
const toPatchPath = path.join(toDir, "cordis.patch.yml");
let added = [];
let skipped = [];
if (noPatch) {
  steps.push("跳过 cordis.patch.yml 合并（--no-patch）");
} else if (!fs.existsSync(fromPatchPath)) {
  warnings.push(`源 profile 没有 cordis.patch.yml，仅迁移插件`);
} else {
  const fromText = fs.readFileSync(fromPatchPath, "utf8");
  const toText = fs.existsSync(toPatchPath) ? fs.readFileSync(toPatchPath, "utf8") : "";
  const toIds = new Set(splitEntries(toText).map((e) => e.id).filter(Boolean));
  for (const entry of splitEntries(fromText)) {
    if (!entry.id) {
      warnings.push("源 patch 中存在无法解析 id 的顶层条目，已跳过");
      continue;
    }
    if (toIds.has(entry.id)) {
      skipped.push(`${entry.id}（目标已有，保留目标值）`);
      continue;
    }
    if (skipEntries.has(entry.id)) {
      skipped.push(`${entry.id}（--skip-entry）`);
      continue;
    }
    added.push(entry);
  }
  if (added.length) {
    const banner = `\n# ── 以下条目由 yashi-dsh-desktop-migrate 从 ${from} profile 迁入（${new Date().toISOString().slice(0, 10)}）──\n`;
    const merged = `${toText.replace(/\s+$/, "")}\n${banner}\n${added.map((e) => e.body).join("\n\n")}\n`;
    if (!dryRun) fs.writeFileSync(toPatchPath, merged, "utf8");
  }
  steps.push(`合并 patch：新增 ${added.length} 条（${added.map((e) => e.id).join(", ") || "无"}）`);
  if (skipped.length) steps.push(`patch 跳过：${skipped.join("; ")}`);
}

// ---- 报告 ----
log("");
log("迁移计划 / 结果：");
for (const step of steps) log(`  - ${step}`);
if (warnings.length) {
  log("");
  log("警告：");
  for (const warning of warnings) log(`  - ${warning}`);
}
log("");
log("下一步：");
log("  1) 校验：node verify-profile-migration.cjs " + to + " " + from);
log("  2) 刷新 GUI 页面；客户端 UI 插件（宠物/皮肤/侧边栏）需重启桌面版才全部可见");
log("  3) 回滚：把 " + path.relative(toDir, backupDir) + " 里的文件拷回 " + toDir);
process.exitCode = 0;
