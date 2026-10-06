/**
 * 校验 dsh profile 迁移结果（只读，不修改任何文件）。
 *
 * 检查项：
 *   1. package.json 是合法 JSON，dsh.profile.bundles 每项都能在 node_modules 解析到
 *      （核心包 @deepseek-ai/* 内嵌于 app.asar，缺失属正常）
 *   2. cordis.patch.yml 是合法 YAML、顶层为数组、条目 id 不重复
 *   3. patch 里的条目 id 能否在「已安装 bundle 提供的 id」中找到
 *      （bundle patch 常以 insert 嵌套结构声明条目，故递归收集 id）
 *   4. 与源 profile 的依赖清单差异
 *
 * 用法: node verify-profile-migration.cjs [目标profile=desktop] [源profile=web]
 * 退出码: 0 无错误；1 存在 errors
 */
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");

const targetName = process.argv[2] || "desktop";
const sourceName = process.argv[3] || "web";
const HOME = process.env.DSH_HOME || path.join(process.env.USERPROFILE || process.env.HOME || ".", ".dsh");
const targetDir = path.join(HOME, "profiles", targetName);
const sourceDir = path.join(HOME, "profiles", sourceName);

const report = { target: targetName, source: sourceName, errors: [], warnings: [], info: {} };

function resolveYaml() {
  for (const dir of [targetDir, sourceDir, process.cwd()]) {
    try {
      return createRequire(path.join(dir, "__resolve__.cjs"))("yaml");
    } catch {}
  }
  return null;
}

const YAML = resolveYaml();
if (!YAML) report.warnings.push("未找到 yaml 解析包，跳过 YAML 语义校验（只做文本检查）");

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (error) {
    report.errors.push(`${file} 不是合法 JSON: ${error.message}`);
    return null;
  }
}

function readYaml(file) {
  if (!YAML) return null;
  try {
    // dsh 的 patch 允许 !!js 自定义标签；静音解析告警，避免污染报告输出
    return YAML.parse(fs.readFileSync(file, "utf8"), { logLevel: "silent" });
  } catch (error) {
    report.errors.push(`${file} 不是合法 YAML: ${error.message}`);
    return null;
  }
}

/** 取包声明的入口文件（main / exports["."]），用于确认「包装了但入口缺失」。 */
function entryFile(pkgDir) {
  try {
    const pkg = JSON.parse(fs.readFileSync(path.join(pkgDir, "package.json"), "utf8"));
    let main = pkg.main;
    const exportsField = pkg.exports;
    if (!main && exportsField) {
      const dot = typeof exportsField === "object" && exportsField !== null ? exportsField["."] ?? exportsField : exportsField;
      if (typeof dot === "string") main = dot;
      else if (dot && typeof dot === "object") main = dot.import ?? dot.require ?? dot.default;
    }
    return typeof main === "string" ? path.resolve(pkgDir, main) : null;
  } catch {
    return null;
  }
}

/** 递归收集任意层级的条目 id：bundle patch 里条目多以 `- insert: [ { id: ... } ]` 嵌套出现。 */
function collectIds(node, out) {
  if (Array.isArray(node)) {
    for (const item of node) collectIds(item, out);
    return;
  }
  if (node && typeof node === "object") {
    if (typeof node.id === "string") out.add(node.id);
    for (const value of Object.values(node)) collectIds(value, out);
  }
}

/** 取包声明的 bundle patch 文件：优先 package.json 的 dsh.bundle.patch，回退常见文件名。 */
function bundlePatchFiles(pkgDir) {
  const files = [];
  const pkgPath = path.join(pkgDir, "package.json");
  if (fs.existsSync(pkgPath)) {
    try {
      const pkg = JSON.parse(fs.readFileSync(pkgPath, "utf8"));
      const declared = pkg && pkg.dsh && pkg.dsh.bundle && pkg.dsh.bundle.patch;
      if (typeof declared === "string") files.push(path.resolve(pkgDir, declared));
      else if (Array.isArray(declared)) for (const one of declared) if (typeof one === "string") files.push(path.resolve(pkgDir, one));
    } catch {}
  }
  for (const name of ["cordis.patch.yml", "cordis.patch.yaml", "cordis.yml"]) files.push(path.join(pkgDir, name));
  return [...new Set(files)].filter((file) => fs.existsSync(file));
}

// ---- 1. package.json ----
const pkgPath = path.join(targetDir, "package.json");
const pkg = fs.existsSync(pkgPath) ? readJson(pkgPath) : null;
if (!pkg) report.errors.push(`缺少 ${pkgPath}`);

const bundles = (pkg && pkg.dsh && pkg.dsh.profile && pkg.dsh.profile.bundles) || [];
report.info.bundles = bundles;

const resolvedBundles = [];
const missingBundles = [];
for (const name of bundles) {
  if (fs.existsSync(path.join(targetDir, "node_modules", name, "package.json"))) resolvedBundles.push(name);
  else missingBundles.push(name);
}
report.info.resolvedBundles = resolvedBundles;
report.info.missingBundles = missingBundles;
for (const name of missingBundles) {
  if (name.startsWith("@deepseek-ai/")) report.warnings.push(`bundle ${name} 未在 node_modules 中（核心包内嵌于 app.asar，属正常）`);
  else report.errors.push(`bundle ${name} 未安装到 node_modules，插件不会加载`);
}

// 已解析的第三方 bundle：确认包声明的入口文件真实存在（防「装了但没装全」）
const brokenEntries = [];
for (const name of resolvedBundles) {
  const pkgDir = path.join(targetDir, "node_modules", name);
  const entry = entryFile(pkgDir);
  if (entry && !fs.existsSync(entry)) brokenEntries.push(`${name} -> ${path.relative(pkgDir, entry)}`);
}
report.info.brokenBundleEntries = brokenEntries;
for (const broken of brokenEntries) report.errors.push(`bundle 入口文件缺失: ${broken}`);

// ---- 2. cordis.patch.yml ----
const patchPath = path.join(targetDir, "cordis.patch.yml");
const patch = fs.existsSync(patchPath) ? readYaml(patchPath) : null;
if (patch && !Array.isArray(patch)) report.errors.push(`${patchPath} 顶层不是 YAML 数组`);

const patchIds = [];
if (Array.isArray(patch)) {
  for (const row of patch) {
    if (!row || typeof row !== "object") {
      report.warnings.push("patch 中存在非对象条目");
      continue;
    }
    if (row.id) patchIds.push(String(row.id));
  }
}
report.info.patchEntryIds = patchIds;
const duplicated = [...new Set(patchIds.filter((id, index) => patchIds.indexOf(id) !== index))];
if (duplicated.length) report.errors.push(`patch 条目 id 重复: ${duplicated.join(", ")}`);

// ---- 3. 条目 id 的提供方 ----
const provided = new Set();
const providedBy = {};
for (const name of resolvedBundles) {
  const pkgDir = path.join(targetDir, "node_modules", name);
  for (const file of bundlePatchFiles(pkgDir)) {
    const doc = readYaml(file);
    const ids = new Set();
    collectIds(doc, ids);
    for (const id of ids) {
      provided.add(id);
      (providedBy[id] = providedBy[id] || []).push(name);
    }
  }
}
// 核心 bundle 的条目内嵌在 app.asar 中，无法从 node_modules 读到，单独标注
const CORE_PREFIXES = ["ui-", "agent-", "llm-", "locale", "session-", "web-runtime", "pwsh-", "bash-", "host-", "tool-", "skill-"];
report.info.entryIdsProvidedByInstalledPlugins = [...provided].sort();
report.info.unmatchedPatchEntryIds = patchIds.filter((id) => !provided.has(id));
report.info.unmatchedLikelyCore = report.info.unmatchedPatchEntryIds.filter((id) => CORE_PREFIXES.some((p) => id.startsWith(p)));

// ---- 4. 依赖差异 ----
const sourcePkgPath = path.join(sourceDir, "package.json");
if (fs.existsSync(sourcePkgPath)) {
  const sourcePkg = readJson(sourcePkgPath);
  if (sourcePkg) {
    const src = Object.keys(sourcePkg.dependencies || {});
    const dst = Object.keys((pkg && pkg.dependencies) || {});
    report.info.dependencyDiff = {
      missingInTarget: src.filter((d) => !dst.includes(d)),
      extraInTarget: dst.filter((d) => !src.includes(d)),
    };
    const srcBundles = (sourcePkg.dsh && sourcePkg.dsh.profile && sourcePkg.dsh.profile.bundles) || [];
    report.info.bundleDiff = {
      missingInTarget: srcBundles.filter((b) => !bundles.includes(b)),
      extraInTarget: bundles.filter((b) => !srcBundles.includes(b)),
    };
  }
}

console.log(JSON.stringify(report, null, 2));
process.exitCode = report.errors.length ? 1 : 0;
