---
name: yashi-dsh-desktop-migrate
description: 把某个 dsh profile（通常是 web）的插件与配置整体迁移到桌面版 profile（desktop），让桌面版拥有与 web 版一致的插件集与设置——包括依赖清单（package.json 的 dependencies）、bundle 注册（dsh.profile.bundles）、pnpm 配置（pnpm-workspace.yaml / .npmrc / pnpm.cmd / pnpm-lock.yaml）与设置条目（cordis.patch.yml 的合并）。核心背景：桌面版 profile 被 Electron 独占，`dsh plugin --profile desktop` 会被直接拒绝，只能用文件级迁移 + pnpm 安装；0.2.0 起 `$DSH_HOME/settings.yaml` 不再是配置源（设置持久化在各 profile 的 cordis.patch.yml，且该 yaml 一旦出现就会被启动流程一次性导入并改名成 settings.yaml.imported）。附带迁移后校验脚本（依赖差异、bundle 可解析、JSON/YAML 合法性、patch 条目 id 是否有提供方）。适用于「把 dsh web 的插件和配置导入桌面版」「桌面版没有宠物/皮肤/侧边栏/通知器这些 web 插件」「让 desktop profile 和 web profile 的插件集一致」「桌面版插件迁移」「dsh 桌面版功能比 web 版少」等场景。
---

<!--
触发示例：
- 把 dsh web 的所有插件和配置导入到 dsh 桌面版
- 桌面版没有 web 版那些插件（宠物、皮肤、任务看板、通知器）
- 让 desktop profile 和 web profile 的插件集保持一致
- 桌面版能不能用 web 版装的插件
- dsh 桌面版迁移 profile
-->

# yashi-dsh-desktop-migrate — dsh profile 插件与配置迁移（web → 桌面版）

## 核心规则（先读）

1. **桌面版 profile 不能由 CLI 管理**：`dsh plugin --profile desktop ...` 会立刻报
   `error: profile "desktop" is managed exclusively by the Electron application`（连 `--help` 都被拒）。
   迁移只能走「直接改 profile 文件 + pnpm 安装」，不要浪费时间找 `dsh plugin add` 这条路。
2. **0.2.0 之后 `$DSH_HOME/settings.yaml` 不再是配置源**：设置持久化在
   `$DSH_HOME/profiles/<profile>/cordis.patch.yml`。若该 yaml 意外出现，dsh 启动时（Settings 插件、
   Loader 就绪后）会执行一次 `importLegacyDocument()`：**先改名成 `settings.yaml.imported`，
   再把各 section 写进当前 profile 的同名条目**，被当前组合拒绝的 section 只留在改名后的文件里。
   结论：**不要把配置还原成 `settings.yaml` 放回去**，任何 profile 下次启动都会吞掉它。
3. **迁移三件套 + 正确顺序**：
   - 依赖清单：`package.json` 的 `dependencies`
   - 插件注册：`package.json` 的 `dsh.profile.bundles`
   - 设置：`cordis.patch.yml`；另需 `pnpm-workspace.yaml`（含 `allowBuilds` / `onlyBuiltDependencies`）、
     `.npmrc`、`pnpm.cmd`、`pnpm-lock.yaml`
   - 顺序必须是 **先写 dependencies → `pnpm install` → 最后写 bundles 和 patch**。
     desktop profile 有热重载：bundles 先生效而 `node_modules` 未就绪就会加载失败。
4. **锁文件是提速关键**：把源 profile 的 `pnpm-lock.yaml` 一起复制，`pnpm install` 会输出
   `Lockfile is up to date, resolution step is skipped`，包全部从 store 硬链接复用
   （实测 259 包 0 下载、约 2.5 分钟，耗时大头是 cpu-features / node-pty / ssh2 的原生编译）。
5. **pnpm 12 不再读 package.json 的 `pnpm` 字段**：`onlyBuiltDependencies`、`allowBuilds` 必须写在
   `pnpm-workspace.yaml`，否则会看到 `The "pnpm" field in package.json is no longer read` 并在
   需要原生构建时报 `ERR_PNPM_IGNORED_BUILDS`。直接复制源 profile 的同名文件最省事。
6. **不要迁移 `web-runtime` 这条 override**：web 版为「不自动开系统浏览器」把该条目整行替换，
   且 `trustedHosts: !!js ctx.webStartup.trustedHosts` 依赖 web 启动上下文；桌面版由 Electron 托管窗口，
   既不需要也可能解析不了。默认跳过该 id。
7. **`dsh-startup-command` 在桌面版会多弹一个窗口**：它在「web 启动成功后」执行自定义命令，web profile
   的配置是用 Chromium `--app={url}` 再开一个同款 GUI 窗口。迁过去后每次启动都会多一个 Chromium；
   不想要就把该条目 `enabled` 改成 `false`。
8. **生效方式**：服务端插件在 bundles 写入后由 dsh 热重载，无需重启——可用插件数据文件 mtime 佐证
   （`$DSH_HOME/pet.json`、`skin-center-active.json`、`storages/cost-meter/ledger.json` 会立刻更新）；
   客户端 UI（宠物、皮肤、侧边栏）需要刷新页面或重启桌面版才可见。
9. **改动前必须备份**：脚本会在目标 profile 下建 `.backup-<from>-import-<ts>/`，桌面版万一起不来，
   把里面的 `package.json`、`cordis.patch.yml`、`pnpm-workspace.yaml` 拷回即可回滚。
10. **迁移后必须校验**：跑 `scripts/verify-profile-migration.cjs`，再看一眼插件数据文件的 mtime。

## 用法

```bash
# 1) 预演（只打印计划，不写任何文件）
node scripts/migrate-profile.mjs --from web --to desktop --dry-run

# 2) 正式迁移（备份 -> 复制 pnpm 配置 -> 写依赖 -> pnpm install -> 写 bundles + 合并 patch）
node scripts/migrate-profile.mjs --from web --to desktop

# 3) 校验（依赖差异、bundle 可解析、JSON/YAML 合法、patch id 提供方）
node scripts/verify-profile-migration.cjs desktop web
```

脚本路径：`scripts/migrate-profile.mjs`、`scripts/verify-profile-migration.cjs`；
完整路径：`~/.agents/skills/yashi-dsh-desktop-migrate/scripts/`。

| 参数 | 说明 |
| --- | --- |
| `--from <profile>` | 源 profile，默认 `web` |
| `--to <profile>` | 目标 profile，默认 `desktop` |
| `--dry-run` | 只打印计划，不落盘、不安装 |
| `--no-install` | 跳过后面的 pnpm install |
| `--skip-entry <id>` | 不合并的 patch 条目 id，可重复；默认已含 `web-runtime` |
| `--no-patch` | 只迁插件（dependencies + bundles），不合并 `cordis.patch.yml` |
| `--pnpm <path>` | 指定 pnpm 可执行文件（否则依次尝试 profile 的 pnpm.cmd、`DSH_PNPM`、dsh 运行时、PATH） |

## 关键路径

| 用途 | 路径 |
| --- | --- |
| profile 根 | `$DSH_HOME/profiles/<name>/`（`DSH_HOME` 默认 `~/.dsh`） |
| 插件依赖 | `profiles/<name>/node_modules/`（`nodeLinker: hoisted`，包平铺在顶层） |
| 设置落点 | `profiles/<name>/cordis.patch.yml` |
| 桌面版程序 | `%LOCALAPPDATA%\Programs\DeepSeek Harness\`（`resources\app.asar` 内嵌 dsh 与核心 bundle） |
| 桌面版 userData | `%APPDATA%\@deepseek-ai\dsh-desktop\`（含 `logs\`，通常为空） |
| 桌面版更新缓存 | `%LOCALAPPDATA%\@deepseek-aidsh-desktop-updater\installer.exe` |
| 工作运行时 | `$DSH_HOME/dsh-runtimes/dsh-primary-runtime/`（node / pnpm / python） |

## 排障

| 现象 | 原因与处理 |
| --- | --- |
| `error: profile "desktop" is managed exclusively by the Electron application` | 设计如此；改用本 skill 的文件级迁移 |
| `ERR_PNPM_IGNORED_BUILDS` | `pnpm-workspace.yaml` 缺 `allowBuilds` / `onlyBuiltDependencies`；从源 profile 复制 |
| `The "pnpm" field in package.json is no longer read` | pnpm 12 行为；把 `onlyBuiltDependencies` 移到 `pnpm-workspace.yaml` |
| 迁移后插件没出现 | 客户端 UI 需刷新/重启；确认 `dsh.profile.bundles` 已注册且 `node_modules` 存在 |
| 启动后多出一个 Chromium 窗口 | `dsh-startup-command` 的效果，改 `enabled: false` |
| 又冒出 `settings.yaml` | 不要放回 `$DSH_HOME`；它会被启动流程改名成 `.imported` 并尝试导入 |
| 依赖装不上（github: 依赖、网络） | 见 `yashi-node-tls-trust-fix`（证书）与 `yashi-dsh-update`（代理与 store 修复） |
