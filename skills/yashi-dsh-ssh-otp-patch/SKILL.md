---
name: yashi-dsh-ssh-otp-patch
description: 给 @linxin666/dsh-ssh 插件补上 keyboard-interactive 2FA 支持（TOTP / 短信验证码，典型场景是服务器启用了 libpam-google-authenticator）：把 onKeyboardInteractive 沿 exec 链透传进 connectClient，并给 ssh_exec 增加可选 otp 参数，让 dsh 的 agent 工具能连上要求二次验证的主机；首次验证成功后连接池复用，同一 alias 后续调用不再需要验证码。补丁幂等、带备份、可一键回滚；插件升级、重装或 dsh 更新覆盖 lib/ 之后重跑一次即可恢复。适用于「dsh ssh 连不上 2FA 主机」「ssh_exec 报 Authentication failed (keyboard-interactive): Verification code」「插件升级后 otp 参数消失」「重新打 2FA 补丁」「ssh 主机要谷歌验证码」等场景。
---

<!--
触发示例：
- dsh 的 ssh_exec 连 2FA 主机报 keyboard-interactive 验证码错误
- 给 dsh-ssh 重新打 otp / 2FA 补丁
- 插件升级后 ssh_exec 的 otp 参数又没了
- 服务器用 libpam-google-authenticator，dsh 工具连不上
- 回滚 dsh-ssh 的补丁
-->

# yashi-dsh-ssh-otp-patch — 给 dsh-ssh 补 keyboard-interactive 2FA 支持

## 核心规则（先读）

1. **只改编译产物**：补丁落在各 profile 的
   `profiles/<name>/node_modules/@linxin666/dsh-ssh/lib/index.js`，
   不动 `src/`（改 src 需要重新构建，且更新即丢）。脚本会自动发现所有 profile。
2. **幂等**：已打补丁的 profile 直接跳过；重复运行安全。
3. **升级后必须重跑**：`yashi-dsh-update` 更新插件、`pnpm install` 重装依赖、
   或 dsh 自身更新覆盖 `lib/` 之后，补丁即失效，重跑本脚本即可。
4. **打补丁后必须重启 dsh web**：host 侧模块已加载在进程里，不重启不生效。
   重启会中断当前会话，刷新页面后恢复对话。
5. **补丁不改安全模型**：otp 只在工具调用参数里出现，不落盘、不写进
   `dsh-ssh.json`；连接池复用只发生在同一 dsh 进程生命周期内。

## 补丁做什么

未打补丁时，`ssh_exec` 的调用链是：
`engine.exec → execCommand → withClient → acquire → doAcquire → connectChain → connectClient`。
`connectClient` 本身接受 `onKeyboardInteractive`，但中间几层没有往下传，
所以遇到 2FA 主机只能报
`Authentication failed (keyboard-interactive): Verification code:`。

补丁共 6 处：

1. `acquire(engine, alias)` 增加 `onKeyboardInteractive` 参数，并传给 `doAcquire`；
2. `doAcquire` 再传给 `connectChain`；
3. `withClient` 增加参数，并在调用 `acquire` 时传入；
4. `execCommand` 增加参数，并作为第 4~6 实参传给 `withClient`；
5. `engine.exec` 增加参数，透传给 `execCommand`；
6. `ssh_exec` 增加可选参数 `otp`，`execute` 里据此构造
   `keyboard-interactive` 处理器（挑战中的每个 prompt 都回答成该 otp）。

## 用法

```bash
# 状态检查
node ~/.agents/skills/yashi-dsh-ssh-otp-patch/scripts/patch-dsh-ssh-otp.mjs --check

# 预览（不写文件）
node ~/.agents/skills/yashi-dsh-ssh-otp-patch/scripts/patch-dsh-ssh-otp.mjs --dry-run

# 应用（默认所有 profile，可加 --profile web 限定）
node ~/.agents/skills/yashi-dsh-ssh-otp-patch/scripts/patch-dsh-ssh-otp.mjs

# 回滚（从首次补丁前的备份恢复）
node ~/.agents/skills/yashi-dsh-ssh-otp-patch/scripts/patch-dsh-ssh-otp.mjs --revert
```

重启 dsh web 后生效。

## 使用方式

补丁后 `ssh_exec` 多了一个可选参数 `otp`：

- 2FA 主机首次连接：`ssh_exec(alias="jp", command="...", otp="123456")`；
- 成功后连接池保留该连接，同一 alias 后续调用可省略 `otp`；
- 连接断开、被池清理或重启 dsh 后，需要重新提供一次验证码。

TOTP 通常 30 秒失效：拿到验证码后应立即调用，不要在对话里来回确认。
`libpam-google-authenticator` 的 `INVALID_HASH`/窗宽可用服务器的
`/etc/pam.d/sshd` 或 `~/.google_authenticator` 配置放宽（属服务器侧调优）。

## 验证与排障

- `--check` 应显示所有 profile「已打补丁」。
- 补丁脚本写入前会用 `node --check` 校验语法，语法错误不会落盘。
- 报 `expected exactly 1 match, found N`：插件版本变了、目标片段已不存在。
  先 `--revert`，再把 `lib/index.js` 与 `src/` 对照，手动确认新的插入点后
  更新本脚本里的匹配字符串。
- 备份文件名：`lib/index.js.yashi-otp-orig`（每 profile 各一份，首次补丁时创建）。
- 若补丁后仍报 keyboard-interactive：确认调用时确实传了 `otp`，且该验证码
  对应的是当前时间窗口。

## 前置条件

- 该主机在 `~/.dsh/dsh-ssh.json` 中已完成密钥/agent 认证配置；
- 若密钥是带 passphrase 的加密私钥，先按 `yashi-` 系列 ssh agent 方案把私钥
  加载进 Windows OpenSSH agent（避免在配置里存明文口令）。
