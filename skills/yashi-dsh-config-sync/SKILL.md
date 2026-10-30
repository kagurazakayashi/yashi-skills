---
name: yashi-dsh-config-sync
description: 一句话导出/导入 DeepSeek Harness（dsh）的配置（压缩包为 7z，**压缩级别 -mx0-9 与 AES-256 密码可选、默认用 -mhe=on 连文件名一起加密**；有图形会话时装了 7-Zip 就用 **7zG 弹图形进度窗口**，否则自动回退控制台 7z；导入侧兼容旧版 zip 并能自动/按参数处理加密包），**核心是「先让用户选范围」**：脚本内置 24 个可选范围类别（插件配置文件、npm 插件包本体、skills、profiles、会话列表、会话正文、主题/皮肤包、宠物包、表情包、凭证与 KEY、环境变量清单、锁文件、TUI 状态、本地插件、历史备份、其它杂项……），既可用 `pick` 弹出原生 GUI 勾选框让用户点选（AI 可用 `--only` 决定只显示哪些选项），也可在 GUI 不可用时由 AI 用 `pick --list` 拿到结构化目录后自行提问，再把结果用 `--scope` 传回导出/导入；还支持 `--profiles` 限定 profile 与 `sync --from web --to desktop` 在 profile 间直接同步插件与配置（依赖并集、bundle 注册、cordis.patch.yml 按 id 差集合并，Electron 独占的 profile 如实跳过启动测试）。导入时自动补全依赖（pnpm install + approve-builds）、自动修正 pnpm-workspace.yaml 的 allowBuilds 半成品、检查 pnpm.cmd 包装器指向、实测 dsh web 启停并给出排障建议，可联动 yashi-dsh-update / yashi-dsh-env-sync / yashi-dsh-desktop-migrate。默认只带配置（不含素材包、运行数据、凭证），需要什么由用户显式勾选。适用于「导出 dsh 配置」「备份 dsh 配置」「迁移 dsh 到新机器」「导入 dsh 配置」「恢复 dsh 配置」「只导出某些东西（插件/技能/主题/宠物/表情包/会话列表）」「导出时弹个框让我选」「把 web 的插件和配置同步到桌面版」等场景。
---

<!--
触发示例：
- 导出 dsh 的配置（settings、插件、插件配置）
- 把 dsh 配置备份一份 / 迁移到新电脑
- 导入之前导出的 dsh 配置
- 恢复 dsh 配置
- 弹个框让我选导出范围 / 让我勾选要带哪些东西
- 只导出插件配置，别把主题宠物表情包一起打进去
- 只导出会话列表（不要聊天正文）
- 把 dsh 的 API key / 凭证一起导出吗？
- 只导出一个 profile / 把 web 的插件和配置同步到桌面版
-->

# yashi-dsh-config-sync — DeepSeek Harness 配置导入 / 导出

## 核心规则（先读）

1. **第一步永远是「选范围」，不要默认全带。** 有 24 个范围类别（见下表），按用户的
   真实意图勾。三条路，优先第一条：
   - **A. 弹 GUI 勾选框**（能弹就用这个，用户点鼠标最省事）：
     `python $S pick --mode export` → 用户勾完点确定 → 结果写成 `scope.json`；
     或直接在导出/导入命令上加 `--choose`，一步到位。
   - **B. GUI 弹不出来**（`pick` 退出码 2，无图形会话 / 没装 tkinter）：AI 用
     `python $S pick --list` 拿结构化类别目录，**用自己的提问能力**（如 DSH 的
     `ask_user_question` / GenUI 表单）照着问用户，再把答案拼成 `--scope` 传回。
   - **C. 用户已经说清要什么**：直接 `--scope`，不用弹框。
2. **不会让用户做二十道选择题**：用**预设**先给一个合理起点（`preset:config` 只配置、
   `preset:migration` 换机迁移、`preset:offline` 离线装机、`preset:everything` 全量、
   `preset:full` 连凭证），只在用户关心的地方逐个确认。`pick` 的 GUI 里也有预设下拉框。
3. **默认只带配置**：`settings`/`instructions`/`presets`/`identity`/`profiles`/
   `plugin-configs`/`env-inventory` 七类。本机实测默认包 **49 个文件 / 240KB**；
   要是把素材包一起勾上就是 **470MB 级**（`pets` 380MB + `skins` 85MB + `memes` 7MB）——
   这正是必须先选范围的原因。运行数据与密钥同样默认不带。
4. **一句话跑脚本**：导出/导入都由 `scripts/dsh_config_sync.py` 完成，不要手工 `cp` 文件。
   脚本路径（相对本 SKILL.md）：`scripts/dsh_config_sync.py`；
   完整路径：`~/.agents/skills/yashi-dsh-config-sync/scripts/dsh_config_sync.py`。
5. **环境变量的值不进包**：API key 等由 `yashi-dsh-env-sync` 用 ini 单独恢复。选
   `env-inventory` 只导**变量名清单**（`env-inventory.txt`，不含任何值）；选 `keys` 才会
   带上 `.credentials.yaml` 等明文凭据（危险，GUI 里会二次确认）。
6. **「能自动下载/重建的都不带」**：`node_modules`、`.pnpm-store`、`.dsh-module-fallback`、
   `.dsh-market`、`compile-cache`、`cordis.yml`、`*.log/*.tmp`、素材包的 `*.install-*`
   安装残留一律跳过。真要离线装机、新机器不能联网时，才勾 `npm-plugins` 把
   `profiles/*/node_modules` 整套带走（本机实测 web 475MB + desktop 266MB，且要逐文件算
   哈希，导入导出都慢）。
7. **导入后必须自动补全 + 实测**：只要这次还原了 `profiles`，脚本默认会跑
   `pnpm install` → `pnpm approve-builds --all` →（有原生模块时）`pnpm rebuild`
   → 启动 `dsh web` 实测 → 停止并确认端口释放。只还原配置以外的东西时可 `--no-install`
   跳过安装。
8. **配置半成品要自动修正**：`pnpm-workspace.yaml` 的 `allowBuilds:` 里若残留 pnpm 占位文本
   `set this to true or false`，`pnpm install` 必然以 `ERR_PNPM_IGNORED_BUILDS` 秒退。
   脚本安装前会自动把非法值改成 `true` 并告警；遇到该报错先看这条。
9. **pnpm.cmd 指向要检查**：`profiles/*/pnpm.cmd` 写死了原机器的 pnpm 绝对路径，cmd.exe 会
   优先命中当前目录同名文件；失效时 dsh 在 profile 目录里调不到 pnpm。导入与 `test` 都会检查，
   并给出 `mklink /J` 修复建议（junction 不需要管理员权限）。
10. **代理必须先问用户**：任何网络操作（pnpm install / git 拉依赖）前先确认是否用代理、用哪个
    （本机候选 `socks5://192.168.255.1:23334` / `http://192.168.255.1:23334`，以用户指定为准），
    确认后 `--proxy <url>` 传给脚本。
11. **压缩与加密也在 GUI 里选**：勾选框底部有「压缩与加密」区——`-mx` 级别（0-9）、
    是否设密码（AES-256）、是否连文件名一起加密（默认 `-mhe=on`）。密码**绝不写进
    `scope.json` / `manifest.json`**，只在内存里交给 7z；忘记密码无法恢复，必须提醒用户另存。
12. **压缩时优先用 7zG 显示图形进度**：Windows 图形会话里装了 7-Zip 就调 `7zG.exe`（同样的
    开关，多一个进度窗口，几百 MB 素材时用户能看见在干活）；无图形会话 / 没装 7zG /
    设了 `DSH_SYNC_NO_7ZG_PROGRESS` 时自动回退控制台 `7z`（结果与退出码一致）。
13. **联动**：插件不兼容/装不上 → `yashi-dsh-update`；环境变量 → `yashi-dsh-env-sync`；
    桌面版迁移 → `yashi-dsh-desktop-migrate`。

## 范围类别总表（AI 提问与 `--scope` 的唯一依据）

「默认」= 导出时不加 `--scope` 是否包含；导入侧默认还原「包里有的全部」，用 `--scope` 才收窄。

### 配置（默认带）

| id               | 类别              | 默认 | 里面到底是什么                                                                                                                                                                        |
| ---------------- | ----------------- | ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `settings`       | 主设置            | ✓    | `settings.yaml`（0.2.0 起 dsh 多已导入并改名 `settings.yaml.imported`，那个残留在 `backups`）                                                                                         |
| `instructions`   | 用户全局指令      | ✓    | `AGENTS.md` / `CLAUDE.md`（含 `.local.md`）。`$DSH_HOME/AGENTS.md` 常是软链，导出按内容打包并记录指向                                                                                 |
| `presets`        | 自定义 agent 预设 | ✓    | `.agent-presets/`（递归）                                                                                                                                                             |
| `identity`       | 身份标识          | ✓    | `.anonymous-user-id`                                                                                                                                                                  |
| `profiles`       | profile 配置      | ✓    | `profiles/*` 的 `package.json`、`cordis.patch.yml`（0.2.0 起真正的设置源）、`pnpm-workspace.yaml`、`.npmrc`、`pnpm.cmd`                                                               |
| `plugin-configs` | 插件配置文件      | ✓    | 插件自己的配置与状态：顶层 `*.json/*.yaml`（`pet.json`、`dsh-ssh.json`、`dsh-get-balance.json`…）与插件配置子目录（`dsh-cloud-sync/`、`@wingsky-1/`…），自动推断，报告里用 `*` 标注 |

### 素材包（用户资源，体积大，默认不带）

| id       | 类别          | 默认 | 里面到底是什么                                                                             |
| -------- | ------------- | ---- | ------------------------------------------------------------------------------------------ |
| `themes` | 主题包 / 皮肤 |      | `skins/`（本机 57 套 85MB）、`skin-center/` 壁纸、`skin-center-active.json`（当前选中）    |
| `pets`   | 宠物包        |      | `pets/`（本机 380MB，其中 161MB 是 `.install-*` 残留，会自动跳过）、`pet.json`（当前宠物） |
| `memes`  | 表情包        |      | `meme-packs/`（本机 2 包 7.1MB）、`dsh-expression.json`、表情目录缓存                      |

### 运行数据（默认不带）

| id             | 类别               | 默认 | 里面到底是什么                                                                                                                                                                                                      |
| -------------- | ------------------ | ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sessions`     | 会话正文           |      | `sessions/<工作目录>/<会话 id>/session*.jsonl.zstd`，本机 153 个会话 57MB。**可能含隐私、命令输出、密钥片段**                                                                                                                          |
| `session-list` | 会话列表（元数据） |      | `storages/session_projcache.json` + `storages/session_projcache/` + `storages/workspace.json`：会话 id、标题、工作目录、创建时间、token 用量。**只想要「会话列表」不想要聊天正文就勾这个**（本机 236 个文件 7.7MB） |
| `storages`     | 其它 KV 持久化     |      | `storages/` 里除会话列表外的存储单元（`whale.json`、`cost-meter/`…）                                                                                                                                               |
| `attachments`  | 附件字节           |      | `attachments/`（内容寻址的图片等附件实体，本机 21MB）                                                                                                                                                               |
| `data-extra`   | 其它运行数据       |      | `task-board/`、`dsh-usage/`、`dsh-session-archive/`、`trash/`、`llm-deepseek/`、`logs/`、`sentinel.lease`                                                                                                           |

### 凭证 / KEY（敏感）

| id              | 类别                   | 默认 | 里面到底是什么                                                                                                                                                                                                                 |
| --------------- | ---------------------- | ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `keys`          | 凭证与 KEY（危险）     |      | `.credentials.yaml`、`.env*`、`*.key/*.pem/*.p12`、含 `_authToken` 的 `.npmrc`、`remote-web-ui-registry/web.json`（含实例 id/secret）。**导出后就是明文密钥**，GUI 里会二次确认，AI 必须先单独征求同意                         |
| `env-inventory` | 环境变量清单（只名字） | ✓    | 扫描配置里引用了哪些环境变量（`apiKeyEnv` / `${VAR}` / `process.env.X`），生成包内 `env-inventory.txt`：**只有变量名，没有值**（只扫配置类文件，不扫 node_modules 里第三方 package.json 的构建变量）；导入时只报告、不写回磁盘 |

### 其它（外部目录 / 可重建）

| id              | 类别                     | 默认 | 里面到底是什么                                                                                                                    |
| --------------- | ------------------------ | ---- | --------------------------------------------------------------------------------------------------------------------------------- |
| `skills`        | `~/.agents/skills`       |      | 用户全局 skill 目录（dsh 与 opencode 共用，本机 95 个 skill 19MB）                                                                |
| `agents-db`     | `~/.agents` 规则与索引库 |      | `AGENTS.json`（规则数据库）、`SKILLS.json/SKILLS.md/SKILLS.html`（skill 索引）                                                    |
| `local-plugins` | 本地插件目录             |      | `plugins/` 下指向本地仓库的软链插件；导出软链清单 + 真实文件，**新机器要自己重建链接**                                            |
| `npm-plugins`   | npm 插件包本体           |      | `profiles/*/node_modules` 整套（本机 560MB）。默认不带，`pnpm install` 可重建；只有离线装机才需要                                 |
| `lockfiles`     | 锁文件                   |      | `pnpm-lock.yaml` 等。带上后 `pnpm install` 可直接复用本机 store（实测零下载）；不带则按 `package.json` 重新解析                   |
| `backups`       | 历史备份文件             |      | `*.bak*`、`.backup-*`、`.dsh-update-backup/`（更新前备份）、`.plugin-update-tmp/`、`settings.yaml.imported`（可能含旧版内联密钥） |
| `tui-state`     | TUI 状态                 |      | `~/.dsh-tui`（不在 `$DSH_HOME`；本机 26MB，其中 `compile-cache` 自动跳过，实际约 0.5MB）                                          |
| `misc`          | 其它杂项文件             |      | 根目录非配置类二进制：`gensheek.icns/ico/png` 图标、`dsh-cloud-sync/releases/*.tgz` 安装包                                        |

> 还有两条「不在类别表里、也改不了」的口径：**恒排除**（`node_modules` 除非勾
> `npm-plugins`、`.pnpm-store`、`.dsh-market`、`compile-cache`、`cache`、`cordis.yml`、
> `*.log/*.tmp/*.pid`、素材包 `*.install-*` 残留）；**用户级细粒度排除**靠
> `--exclude 'glob'`，在勾选范围之上再做减法，例如 `--exclude 'dsh-ssh.json'`。

### 预设（`--scope preset:<名字>`，GUI 里的下拉框）

| 预设         | 组成                                                                   | 适用                                                    |
| ------------ | ---------------------------------------------------------------------- | ------------------------------------------------------- |
| `config`     | 七类默认配置                                                           | 只是备份/分享配置，新机器重新装依赖                     |
| `migration`  | config + `skills` `agents-db` `lockfiles` `local-plugins` + 三个素材包 | 换机迁移（不含会话正文、不含 `node_modules`、不含密钥） |
| `offline`    | migration + `npm-plugins`                                              | 新机器不能联网，整套插件一起搬                          |
| `everything` | offline + 全部运行数据 + `tui-state`                                   | 全量克隆（不含凭证）                                    |
| `full`       | everything + `keys`                                                    | 连明文凭据一起搬，务必自行加密保管                      |

预设只是「勾选清单的快捷方式」，**不会替用户确认危险项**；`keys` 只在 `full` 里出现。

## 选范围 A：弹出 GUI 勾选框（首选）

```powershell
$S = "$HOME/.agents/skills/yashi-dsh-config-sync/scripts/dsh_config_sync.py"

# 1) 先探测能不能弹窗（0=能，2=不能；只读，无副作用）
python $S pick --check-gui

# 2) 弹窗让用户勾选，结果写进 scope.json
python $S pick --mode export -o "$HOME\scope-导出.json"

# 3) 用这份范围导出 / 导入
python $S export -o "$OUT\dsh-config.7z" --scope "$HOME\scope-导出.json"
python $S import -i "$OUT\dsh-config.7z" --scope "$HOME\scope-导出.json"
```

一步到位（勾完直接干活）：

```powershell
python $S export --choose -o "$OUT\dsh-config.7z"      # 弹框勾完 → 直接导出
python $S import -i "$OUT\dsh-config.7z" --choose       # 弹框勾完 → 只还原勾中的
```

GUI 能力（都只影响「显示什么 / 预勾什么」，不改变安全性）：

| 开关                          | 作用                                                             |
| ----------------------------- | ---------------------------------------------------------------- |
| `--mode export\|import\|sync` | 标题与默认值语义（导入时会预勾「包里已有的类别」）               |
| `--only id1,id2`              | **AI 决定这次只显示哪几项**，其余不打扰用户（其余用默认值）      |
| `--initial id1,id2`           | 指定初始勾选                                                     |
| `--present id1,id2`           | 标注「包里实际有的类别」（独立使用 `pick --mode import` 时可选） |
| `-o scope.json`               | 直接写文件；不传则只往 stdout 打一行 `SCOPE_JSON: {...}`         |
| `--timeout 秒`                | 无人操作自动收窗（默认 600，超时按取消处理，不会卡住 AI）        |
| `--list` / `--json`           | **不弹窗**，输出类别目录 JSON（AI 用它自己提问，见下一节）       |

窗口里还有：预设下拉框、全选 / 全不选 / 恢复推荐、按 profile 的勾选区（多 profile 时才有）、
底部「压缩与加密」区（`-mx` 级别 + 密码 + 是否加密文件名，仅导出模式显示）、
勾了危险类别或设了密码会弹二次确认。

![范围勾选框](assets/scope-picker.png)

只显示少数几项时（`--only`）的样子，可以看到敏感项的文字标记与 profile 勾选区：

![敏感类别与 profile 勾选](assets/scope-picker-sensitive.png)

底部就是压缩与加密区（不勾「设置密码」时输入框是灰的）：

![压缩级别与密码](assets/scope-picker-compress.png)

**退出码**（AI 据此分支，别只看输出）：

| 退出码 | 含义                | 下一步                                        |
| ------ | ------------------- | --------------------------------------------- |
| 0      | 用户在 GUI 里确认了 | 读 `scope.json` 或 stdout 的 `SCOPE_JSON:` 行 |
| 130    | 用户取消 / 超时收窗 | 停止，不要自行决定范围                        |
| 2      | GUI 不可用          | **改用选范围 B（AI 自己提问）**，别重试弹窗   |

## 选范围 B：GUI 不可用时由 AI 提问

```powershell
# 1) 拿类别目录（可只用 --only 限定「这次要问的几项」，AI 可自行精简）
python $S pick --list
python $S pick --list --only themes,pets,memes,sessions,session-list,keys

# 2) AI 用自己的提问能力问用户（ask_user_question / GenUI 表单 / 普通提问都行）

# 3a) 直接命令行传
python $S export -o "$OUT\dsh-config.7z" --scope "settings,instructions,profiles,plugin-configs"

# 3b) 或写成 JSON 传（结构就是 pick 的产物，便于复用）
python $S export -o "$OUT\dsh-config.7z" --scope '{"categories":["profiles","skills"],"profiles":["web"]}'
```

`pick --list` 的 JSON 里每条含 `id / label / desc / default / danger / restore / inPackage`，
`presets` 给了五套预设，`default` 是推荐勾选。**提问要点**（照抄即可）：

1. 先给一句人话总结：「这次要带哪些东西？默认只带配置（约 240KB）。」
2. 素材包 / 运行数据 / 凭证三类**逐类问**，带上体积量级和隐私提示；
   `danger=true` 的类别（`keys`）单独确认，默认不勾。
3. 问要不要限定 profile（多 profile 时），给 `--profiles` 的命令写法。
4. 问导入后要不要实测启动（`--no-test` 可跳过）。
5. 用户懒得选就给预设：`--scope preset:config|migration|offline|everything|full`。

## 选范围 C：命令行直接 `--scope`

`--scope` 接受四种写法，可混用逗号：

```powershell
--scope config,skills                      # 类别 id（也接受预设名混在里面）
--scope preset:migration                   # 预设
--scope "$HOME\scope.json"                 # pick 产出的 scope.json
--scope '{"categories":["pets","memes"]}'  # 内联 JSON
```

未知 id 会直接报错并列出全部可用 id + 预设，不会静默忽略。`all` / `*` 表示全选。

## 命令速查

```powershell
$S = "$HOME/.agents/skills/yashi-dsh-config-sync/scripts/dsh_config_sync.py"
$OUT = "<备份目录>"    # 占位：U 盘 / 网络盘 / 任意目录，按需替换

# 选范围
python $S pick --check-gui                    # 能否弹窗
python $S pick --mode export                  # 弹窗勾选（结果在 stdout / -o）
python $S pick --list                         # 类别目录 JSON（AI 提问用）

# 看会导出什么（不写文件）
python $S export --print
python $S export --print --scope preset:migration

# 导出（默认 ~/dsh-config-export-<时间>.7z；默认 -mx9，压缩时用 7zG 显示进度窗）
python $S export
python $S export --choose -o "$OUT\dsh-config.7z"          # 弹框选范围 + -mx + 密码，一步到位
python $S export -o "$OUT\dsh-config.7z" --scope config,skills
python $S export -o "$OUT\dsh-config.7z" --mx 5 --password '密码'      # 命令行指定压缩与密码
python $S export -o "$OUT\dsh-config.7z" --password-file pw.txt        # 密码从文件读（少暴露）
python $S export -o "$OUT\dsh-config.7z" --no-gui-progress            # 不用 7zG，走控制台 7z

# 查看导出包（含每个类别的文件数、软链清单、环境变量引用、是否加密）
python $S inspect -i "$OUT\dsh-config.7z"
python $S inspect -i "$OUT\dsh-config.7z" --password '密码'   # 加密包

# 导入（默认还原包内全部 → pnpm install → 实测 dsh web 启停）
python $S import -i "$OUT\dsh-config.7z"
python $S import -i "$OUT\dsh-config.7z" --dry-run          # 只预览
python $S import -i "$OUT\dsh-config.7z" --choose           # 弹框选只还原哪些
python $S import -i "$OUT\dsh-config.7z" --scope config     # 只还原配置
python $S import -i ... --no-install --no-test               # 只还原文件
python $S import -i ... --proxy socks5://127.0.0.1:23334     # 需要代理时
python $S import -i "$OUT\dsh-config.7z" --password '密码'    # 加密包（不传会弹密码框）

# 只处理指定 profile（导出、导入都支持；默认全部）
python $S export --print --profiles web,desktop
python $S import -i "$OUT\dsh-config.7z" --profiles desktop

# 把一个 profile 的插件与配置同步到另一个 profile（默认 web → desktop）
python $S sync --from web --to desktop --dry-run
python $S sync --from web --to desktop

# 单纯实测当前配置能否启动 dsh web
python $S test --port 3089
```

export / import 通用开关：

| 开关                | 作用                                                             |
| ------------------- | ---------------------------------------------------------------- |
| `-s, --scope <值>`      | 范围：类别逗号列表 / `preset:名字` / scope.json 路径 / 内联 JSON |
| `--choose`          | 弹 GUI 勾选范围（GUI 不可用时退出码 2）                          |
| `--only id1,id2`    | `--choose` 时只显示这些类别（AI 可据此精简选项）                 |
| `--pick-timeout 秒` | `--choose` 弹窗等待秒数（默认 600，超时按取消=退出码 130）       |
| `--exclude 'glob'`  | 额外排除（可重复），在范围之上再做减法                           |
| `--home 目录`       | 覆盖 `$DSH_HOME`（测试/多 home 场景）                            |
| `--profiles 列表`   | 只处理这些 profile（逗号分隔，可重复；默认全部）                 |

导出专用（都只是「往范围里加类别」的快捷方式，等价写法在括号里）：

| 开关               | 等价于                                                                        |
| ------------------ | ----------------------------------------------------------------------------- |
| `--with-agents`    | `--scope ...,skills`                                                          |
| `--with-assets`    | `--scope ...,themes,pets,memes`                                               |
| `--with-data`      | `--scope ...,sessions,session-list,storages,attachments,data-extra,tui-state` |
| `--with-lockfiles` | `--scope ...,lockfiles`                                                       |
| `--with-secrets`   | `--scope ...,keys`（危险）                                                    |
| `--strict`         | 只导已知配置，不含 `plugin-configs` 推断项                                    |

导出专用（压缩与加密，细节见下一节）：

| 开关                   | 作用                                                                                     |
| ---------------------- | ---------------------------------------------------------------------------------------- |
| `--mx 0-9`             | 7z 压缩级别（0 仅存储 … 9 极限，默认 9）；GUI 里也能选                                  |
| `--password <密码>`          | 设置 AES-256 密码；**会出现在命令行/进程列表里**，优先用 `--choose` 或 `--password-file` |
| `--password-file <文件>`     | 从文件首行读密码                                                                         |
| `--no-encrypt-headers` | 只加密数据、不加密文件名（默认 `-mhe=on` 连文件名一起加密）                              |
| `--no-gui-progress`    | 不用 7zG 的图形进度窗，改走控制台 7z                                                     |
| `--initial id1,id2`    | `--choose` 时的初始勾选类别（复现上次选择）                                              |

导入 / 查看专用：`-i/--input`、`--dry-run`、`--no-install`、`--no-test`、`--port`、`--wait`、
`--proxy`、`--password`、`--password-file`。

## 压缩与加密（`-mx` / 密码 / 7zG 进度）

### 压缩级别 `-mx`

| 值        | 含义                | 何时用                                              |
| --------- | ------------------- | --------------------------------------------------- |
| 0         | 仅存储（最快）      | 只求打包速度、内容本身已是压缩格式                  |
| 1 / 3 / 5 | 极速 / 快速 / 普通  | 素材包（图片、webp、zst）占大头时，高压缩比收益很小 |
| 7 / 9     | 较大 / 极限（默认） | 配置为主的小包，`-mx9` 最划算                       |

GUI 里是下拉框；命令行用 `--mx N`。`scope.json` 会记住这次用的 `mx`，下次直接
`export --scope scope.json` 就沿用（命令行 `--mx` 优先）。

### 图形进度窗：优先 7zG，自动回退 7z

- Windows 且有图形会话、装了 7-Zip → 用 `7zG.exe` 压缩：同样的命令与开关，多一个
  **带进度条的小窗**，几百 MB 时能看见在干活；
- 非 Windows / 无图形会话 / 没装 7zG / 设了 `DSH_SYNC_NO_7ZG_PROGRESS` → 自动回退
  控制台 `7z`，行为与退出码完全一致；
- 想强制控制台：`--no-gui-progress`；想指定 7zG 路径：`DSH_SYNC_7ZG=<7zG.exe 全路径>`；
- 7zG 出错时可能只闪一下窗口、没有输出 → 加 `--no-gui-progress` 重跑看真实报错；
- 解压（导入 / inspect）仍走控制台 7z，快且不需要交互。

### 密码（AES-256）

- GUI 勾「为压缩包设置密码」→ 填两遍，密码**只留在内存**直接交给 7z；
- 默认 `-mhe=on` **连文件名一起加密**——否则别人不用密码就能看到包里有
  `.credentials.yaml`、`sessions/`（取消勾选 / `--no-encrypt-headers` 则文件名可见）；
- 密码**绝不写进** `scope.json`、`manifest.json` 或日志；独立跑 `pick` 时密码会落到一个
  临时文件并打印 `PASSWORD_FILE: <路径>`，交给导出/导入读取后**请删除**；
- `--password` 会出现在命令行参数与进程列表里（7-Zip 不支持从 stdin 读密码），
  共享机器上优先用 `--choose` 或 `--password-file`；
- **忘记密码无法恢复**：包里没有后门，只能重新导出。导出后必须提醒用户把密码另存
  （密码管理器）。
- 加密包查看/导入：`inspect -i x.7z --password ...`、`import -i x.7z --password ...`；
  不传且有图形会话时自动弹密码框，没有图形会话就明确报错（让 AI 问用户，不要猜）。
- 旧版 zip 包不支持加密（`-p` 对它无效），只有本版 7z 包能加密。

## 工作流

### 导出

1. **先选范围**（A/B/C 任选；不确定就 `pick --mode export` 弹窗）。
2. `export --print` 看清单：每个类别多少文件、多大；下面还会列「未选中（如需要请用
   `--scope` 加上）」，据此和用户对一次范围。
3. 有不该带的（比如某个插件目录、SSH 配置），加 `--exclude '模式'` 重跑。
4. **压缩与加密**：勾选框底部能直接选 `-mx` 与密码；命令行对应 `--mx` / `--password`
   （或 `--password-file`）。默认 `-mhe=on` 连文件名一起加密。**导出后必须提醒用户另存密码，
   忘记无法恢复**；如果包里有 `keys`，即使加密也要说明「密码泄露等于凭据泄露」。
5. 正式导出，向用户汇报：**包路径、文件数、压缩包大小、用的 `-mx`、是否加密（是否含文件名）、
   范围类别、profiles、引用的环境变量**，并提醒「环境变量值不在包里，
   要 `yashi-dsh-env-sync` 一起导出」。导出过程中若走 7zG，会看到进度窗。
   勾了 `keys` 要说明包内含凭据（未加密 = 明文凭据；加密了也要说「密码泄露即凭据泄露」）。
6. 包内会带 `scope.json`（本次范围）和 `env-inventory.txt`（环境变量名清单），
   换机器的人/下一个 AI 不用猜这次导了什么。

### 导入

1. `inspect` 看来源包：哪个 dsh 版本、**包里有哪些类别**、哪些 profile、引用哪些环境变量、
   是否加密。加密包要 `--password`（不传会弹框；无图形会话则让用户把密码告诉你）。
2. **先问范围**：默认还原包内全部；只想还原一部分就 `--choose` 弹窗，或 `--scope config`。
   要还原会话/运行数据/凭证这类，单独确认风险。
3. **询问是否使用代理**（见核心规则 10），再决定是否传 `--proxy`。
4. 执行导入；脚本会自动：
   - 备份将被覆盖的文件到 `~/dsh-config-backup-<时间>/`（`dsh-home` / `agents-home` /
     `tui-home` 三处都备份）；
   - 还原到 `$DSH_HOME`（以及 `~/.agents`、`~/.dsh-tui`）；
   - 目标路径是软链时**写回链接目标**、保留链接（典型：`$DSH_HOME/AGENTS.md` →
     `~/.agents/AGENTS.md` 不会被替换成独立文件）；
   - 检查 `profiles/*/pnpm.cmd` 指向，失效则打印 `mklink /J` 修复建议；
   - 自动修正 `pnpm-workspace.yaml` 里 `allowBuilds:` 的非法值（占位文本 → `true`）；
   - 逐个 profile 跑 `pnpm install` + `pnpm approve-builds --all`（有原生模块再 `pnpm rebuild`），
     若仍报 `ERR_PNPM_IGNORED_BUILDS` 会自动补跑一次；
   - 在测试端口（默认 3089）启动 `dsh web --no-open`，验证「无 token 401 / 带 token 303」，
     然后强杀**整棵进程树**并确认端口释放。
5. 若启动失败：把日志尾部 + `💡` 建议转述给用户，多数要转 `yashi-dsh-update`。
6. 若包内有 `envVarsReferenced` / `env-inventory.txt`：提醒跑 `yashi-dsh-env-sync` 恢复密钥。
7. 若脚本打印 `⚠️ 发现失效的 pnpm.cmd 包装器`：把 `mklink /J` 那行原样给用户。
8. 若包内有软链清单（`links`）：告诉用户哪些目录软链要自行重建（`plugins/` 下的本地插件）。

## 包结构

```
dsh-config-xxx.7z
├─ manifest.json        导出时间、dsh 版本、范围、每类统计、文件哈希、软链、排除项、
│                       archive{format,mx,encrypted,headerEncrypted}（不含密码）
├─ scope.json           本次导出范围 + archive 压缩参数（可直接 --scope 传回来）
├─ env-inventory.txt    环境变量名清单（只有名字，选了 env-inventory 才有）
├─ dsh-home/            → $DSH_HOME
├─ agents-home/         → ~/.agents（选了 skills / agents-db 才有）
└─ tui-home/            → ~/.dsh-tui（选了 tui-state 才有）
```

加密包（`-p` / `-mhe=on`）的**内容全部加密**；`manifest.json` / `scope.json` 也在包内，
所以连「这次导出了哪些类别、用了什么 mx」都要先输密码才能看。

## 导出范围（脚本内置规则）

**会导出**：由上表勾选的类别决定。默认只有配置七类。

**始终不会导出**（无论勾什么都会跳过，报告里以 `排除[...]` 计数）：

| 类别     | 内容                                                                                                                                                      | 原因                                    |
| -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------- |
| 可重建   | `node_modules`（除非勾 `npm-plugins`）、`.pnpm-store`、`.dsh-module-fallback`、`.dsh-market`、`compile-cache`、`cache`、`cordis.yml`、`*.log/*.tmp/*.pid` | `pnpm install` / dsh 启动时自愈         |
| 安装残留 | 素材包里的 `*.install-*` 目录（本机 pets 里占 161MB）                                                                                                     | 不是有效包                              |
| 凭证     | 未勾 `keys` 时：`.credentials.yaml`、`.env*`、`*.key/*.pem`、含 `_authToken` 的 `.npmrc`、`remote-web-ui-registry/`                                       | 导入后重新登录/重填更安全               |
| 环境变量 | 任何变量的**值**                                                                                                                                          | 由 `yashi-dsh-env-sync` 用 ini 单独处理 |

## profile 范围与 profile 间同步

### 选定 profile 范围（`--profiles`）

默认处理 `profiles/*` 下所有 profile。只想动其中几个时加 `--profiles`：

```powershell
python $S export --print --profiles web,desktop   # 先看范围，报告里有「排除[profiles]: N 项」
python $S export -o "$OUT\dsh-config.7z" --profiles desktop
python $S import -i "$OUT\dsh-config.7z" --profiles desktop
```

- 导出侧：未选中的 profile 目录整体不进包（计入 `排除[profiles]`）。
- 导入侧：包内未选中的 profile **不会还原**，导入日志里以 `×` 标记；依赖安装也只跑选中的。
- profile 名写错不会报错（只是没匹配到任何 profile），所以务必先 `--print` 核对一次。
- GUI 勾选框里也有 profile 区块，取消某个 profile 等于 `--profiles` 排除它。

### 把 A profile 同步到 B profile（`sync`）

当意图是「让 B 拥有 A 的插件与配置」（典型：桌面版对齐 web 版）时用 `sync`，
不要绕道「导出 7z 再导入」：

```powershell
python $S sync --from web --to desktop --dry-run   # 预演：打印计划，不落盘
python $S sync --from web --to desktop             # 正式同步
```

`sync` 的执行顺序（与 dsh 的热重载安全相关，不要手工打乱）：

1. 备份目标 profile 的 `package.json`、`cordis.patch.yml` 与 pnpm 配置 →
   `~/dsh-config-backup-<时间>/sync-<源>-to-<目标>/`（回滚就是把它们拷回去）；
2. 复制 pnpm 配置：`.npmrc`、`pnpm-workspace.yaml`、`pnpm.cmd`、`pnpm-lock.yaml`
   （带锁文件后 `pnpm install` 可直接复用本机 store，实测零下载）；
3. 合并 `package.json`：`dependencies` 取并集（源侧同名键优先），`bundles` 取并集。
   脚本**先写不含新 bundle 的版本**，等依赖装好后才注册 bundle，避免热重载时依赖未就绪；
4. 合并 `cordis.patch.yml`：按 `id` 取差集，把源独有条目**追加**到目标末尾，
   保留双方的注释与排版（文本级合并，不做 YAML 序列化）。默认跳过 `web-runtime`；
5. `pnpm install` 只跑目标 profile，装完后注册 bundle；
6. 启动测试：目标若是 `desktop`，CLI 无法启动它（由 Electron 应用独占），
   脚本如实跳过并提示重开桌面版——不会假报一次「测试通过」。

字段级取舍：默认**目标 profile 已有的条目一律保留**，只用源侧补齐缺失的 id。
需要源侧覆盖目标同 id 的条目时，用 `--overwrite-entries` 点名：

```powershell
python $S sync --from web --to desktop --overwrite-entries ui-theme,locale
```

> 更完整的桌面版迁移（含 Node 版校验脚本）见 `yashi-dsh-desktop-migrate`；
> 本 skill 的 `sync` 与它语义一致，胜在零依赖、与备份/安装链路同一套。

## 常见坑

| 现象                                                                                                   | 原因                                                                                              | 处理                                                                                                     |
| ------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| 导出包特别大（几百 MB）                                                                                | 勾了素材包（`pets` 380MB / `themes` 85MB）或 `npm-plugins`                                        | 用 `--print` 看每类体积，去掉勾选；`pets` 的 `.install-*` 残留会自动跳过                                 |
| 只想备份配置，却把素材也带上了                                                                         | 旧版默认全带素材                                                                                  | 本版默认只带配置七类；明确要素材才勾 `themes/pets/memes`                                                 |
| 以为导出了 `settings.yaml`，包里的却叫 `settings.yaml.imported`                                        | 0.2.0 起设置迁到各 profile 的 `cordis.patch.yml`，旧文件被改名                                    | 设置看 `profiles` 类别；`settings.yaml.imported` 归 `backups`（默认不带，可能含旧版内联密钥）            |
| 导入后 `~/.dsh/AGENTS.md` 变成独立文件、`~/.agents/AGENTS.md` 不再同步                                 | 旧版还原时把软链替换成了普通文件                                                                  | 本版写回链接目标并保留软链；已损坏的重新 `mklink` 或 `New-Item -ItemType SymbolicLink`                   |
| 想只搬「会话列表」不搬聊天正文                                                                         | 会话列表在 `storages/session_projcache*`，不是独立 `sessions.json`                                | `--scope session-list`；会话正文是 `sessions`，两者分开                                                  |
| `pick` 什么都看不到 / 退出码 2                                                                         | 无图形会话、没装 tkinter、或设了 `DSH_SYNC_NO_GUI`                                                | 走「选范围 B」：`pick --list` + AI 自己提问；别重试弹窗                                                  |
| `pick` 一直等到超时                                                                                    | 用户没在看屏幕                                                                                    | 默认 600 秒后按取消收窗（退出码 130），不会卡住；可 `--timeout 60` 缩短                                  |
| AI 问了一长串类别，用户看花眼                                                                          | 一次问二十项                                                                                      | 用 `pick --only` 只显示相关的几项，或先给 `preset:config`/`preset:migration` 让用户微调                  |
| `--allow-scripts` 报 `unexpected argument`                                                             | 新版 pnpm 已移除该参数                                                                            | 改用 `pnpm approve-builds --all`；构建许可写在 `pnpm-workspace.yaml` 的 `allowBuilds:`                   |
| `pnpm install` 秒退 exit=1，报 `ERR_PNPM_IGNORED_BUILDS`                                               | `allowBuilds:` 里残留占位文本 `set this to true or false`                                         | 脚本已自动改成 `true` 并告警；手工场景把该值改成 `true`/`false` 后重跑                                   |
| 导入时脚本长时间无输出、像卡死                                                                         | (1) 调用方把输出缓冲了（别用 `Select-Object -Last`，重定向到文件）(2) `approve-builds` 在等 stdin | 脚本已给所有子进程设 `stdin=DEVNULL`；看日志先重定向到文件                                               |
| `dsh` 在 profile 目录里调 pnpm 报找不到命令                                                            | `profiles/*/pnpm.cmd` 写死失效路径，cmd.exe 优先命中当前目录同名文件                              | 把包装器路径改成 `Join-Path (npm.cmd config get prefix) pnpm.cmd`；或 `mklink /J`（junction 不需管理员） |
| 导入后插件全报找不到模块                                                                               | 忘了装依赖                                                                                        | 脚本已自动 `pnpm install`；手工场景在两个 profile 目录各跑一次                                           |
| `dsh web` 启动即崩，日志有 `does not provide an export named`                                          | 某插件与新版 core 不兼容                                                                          | 按 `yashi-dsh-update`，在 `cordis.patch.yml` 追加 `- id: 插件 id` + `disabled: true`                     |
| pnpm install 报 git 依赖无法访问                                                                       | 锁文件/依赖记录的是 `git@github.com:` SSH 地址                                                    | `git config --global url."https://github.com/".insteadOf "git@github.com:"`                              |
| 启动测试「端口被占用」自动换端口                                                                       | 3089 被别的进程占着                                                                               | 正常降级；也可 `--port 0` 让系统分配                                                                     |
| 测试后仍有 node 进程残留                                                                               | 只 kill 了 `dsh.cmd` 包装器，node 是孙进程                                                        | 脚本已用 `taskkill /F /T` 整树清理 + 端口反查兜底；手工务必 `/T`                                         |
| 云同步/SSH 不可用                                                                                      | 凭据没跟着搬（`keys` 默认不带）                                                                   | 重新登录/重填；或明确勾 `keys`（危险）后导入                                                             |
| `~/.dsh-tui` 没被带上                                                                                  | 它在 `$DSH_HOME` 之外                                                                             | 勾 `tui-state` 类别（`compile-cache` 会自动跳过）                                                        |
| `sync`/导入的目标是 desktop，报 `profile "desktop" is managed exclusively by the Electron application` | desktop profile 由桌面版 Electron 独占                                                            | 属设计行为：迁移用 `sync`，验证靠重开桌面版；脚本已自动跳过 desktop 的启动测试                           |
| `sync` 完成后目标 profile 界面没变化                                                                   | 服务端插件热加载，客户端 UI 需刷新                                                                | Ctrl+R 刷新；桌面版完全退出后重开                                                                        |
| 导出/导入报 `找不到 7z（7-Zip / p7zip）`                                                               | 本机没装 7z 或不在 PATH                                                                           | 装 7-Zip / `p7zip`；或用 `DSH_SYNC_7Z=<7z 全路径>` 指定                                                             |
| 用旧版 `.zip` 备份包导入                                                                               | 导出格式已改为 7z，导入侧保留旧 zip 只读兼容                                                      | 直接 `import -i xxx.zip`；`inspect` 会显示「容器: zip（旧版）」，没有类别信息时按条目名自动还原          |
| 包内出现 `*推断` 标记的文件                                                                            | 属于 `plugin-configs` 的自动推断项（插件自建目录）                                                | 正常；不想要就 `--exclude '模式'` 或 `--strict`                                                          |
| 压缩时弹出一个 7-Zip 进度小窗                                                                          | 正常：有图形会话就优先用 `7zG` 显示进度                                                           | 想关掉：`--no-gui-progress`；没装 7zG / 无图形会话会自动回退控制台 7z                                    |
| 7zG 一闪而过、没有报错细节                                                                             | 7zG 的错误走窗口，控制台拿不到输出                                                                | 加 `--no-gui-progress` 用控制台 7z 重跑，才能看到真实报错                                                |
| `inspect` / `import` 报「这个包已加密，需要密码」                                                      | 包用了 `-p` / `-mhe=on`                                                                           | 传 `--password` 或 `--password-file`；有图形会话时也会自动弹密码框                                       |
| 打开加密包报「密码不对」                                                                               | 密码错，或包不是用当前密码导出的                                                                  | 换对的密码；**忘记密码无法恢复**，只能重新导出                                                           |
| 想让别人看不到包里有 `.credentials.yaml`                                                               | 只加密数据不加密文件名时，包内路径仍可见                                                          | 保持默认 `-mhe=on`（GUI 里「同时加密文件名」不要取消）                                                   |
| 命令行 `--password` 觉得不放心                                                                         | 密码会出现在命令行参数与进程列表里（7-Zip 不支持 stdin 读密码）                                   | 用 `--choose` 在 GUI 里输入（只留内存），或 `--password-file`                                            |
| 压缩比不理想                                                                                           | 素材包（webp/zst/图片）本身已压缩，`-mx9` 收益很小                                                | 这种包用 `--mx 1` 或 `5` 明显更快，体积差不多                                                            |
| 旧版 zip 包为什么不能设密码                                                                            | 本版只有 7z 导出，加密是 7z 的能力；zip 仅只读兼容                                                | 要加密就重新用本版导出成 7z                                                                              |

## 联动

| Skill                       | 何时用                                                  |
| --------------------------- | ------------------------------------------------------- |
| `yashi-dsh-env-sync`        | 导出/导入 API key 等**环境变量值**（ini）；配置包不含值 |
| `yashi-dsh-update`          | 导入后插件装不上、`dsh web` 起不来、插件与 core 不兼容  |
| `yashi-dsh-desktop-migrate` | 专门的「web → 桌面版」迁移，带 Node 版校验脚本         |

**完整迁移配方**：`config-sync 导出 7z（--scope preset:migration）` +
`env-sync 导出 ini` → 新机器 `config-sync 导入 7z` → `env-sync 导入 ini` →
重开终端 → `dsh web`。

## 与 opencode / 其它 agent 的关系

`~/.agents/skills` 是 dsh 与 opencode 等共用的 skill 源，**默认不带**（避免和
`yashi-opencode-config-portability` 的产物重复）。要连 skill 一起迁移就勾 `skills`
类别（或预设 `migration` / `offline`）。
