#!/usr/bin/env node
/**
 * yashi-dsh-ssh-otp-patch
 *
 * 给 @linxin666/dsh-ssh 的 host 侧编译产物打一个幂等补丁，让 `ssh_exec`
 * 支持带 keyboard-interactive 2FA（TOTP / 短信验证码）的主机：
 *
 * 现状（未打补丁）：ssh_exec -> engine.exec -> execCommand -> withClient ->
 * acquire -> doAcquire -> connectChain 这条链没有把 onKeyboardInteractive
 * 往下传，而 connectClient 本身是支持的，所以遇到 2FA 主机直接报
 * "Authentication failed (keyboard-interactive): Verification code:"。
 *
 * 补丁做的事：
 *   1. 把 onKeyboardInteractive 参数沿 exec 链逐层透传到 connectChain；
 *   2. 给 ssh_exec 增加可选 `otp` 参数；
 *   3. 有 otp 时构造 keyboard-interactive 处理器，把挑战里的每个 prompt
 *      都回答成该 otp。
 *
 * 连接建立后 dsh-ssh 的连接池会复用该连接，所以同一 alias 后续调用
 * 不再需要 otp，直到连接断开或被池清理。
 *
 * 用法：
 *   node patch-dsh-ssh-otp.mjs              # 给所有 profile 打补丁
 *   node patch-dsh-ssh-otp.mjs --dry-run    # 只检查，不写文件
 *   node patch-dsh-ssh-otp.mjs --profile web
 *   node patch-dsh-ssh-otp.mjs --revert     # 从首次备份恢复
 *   node patch-dsh-ssh-otp.mjs --check      # 只报告每个 profile 的补丁状态
 *
 * 退出码：0 成功（含已打过），1 失败。
 */

import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'

const PLUGIN = '@linxin666/dsh-ssh'
const ORIG_SUFFIX = '.yashi-otp-orig'

const argv = process.argv.slice(2)
const dryRun = argv.includes('--dry-run')
const revert = argv.includes('--revert')
const checkOnly = argv.includes('--check')
const profileIdx = argv.indexOf('--profile')
const onlyProfile = profileIdx >= 0 ? argv[profileIdx + 1] : undefined

function dshHome() {
  const raw = process.env.DSH_HOME
  if (raw !== undefined && raw.trim() !== '') return path.resolve(raw.trim())
  return path.join(os.homedir(), '.dsh')
}

function findTargets() {
  const profilesDir = path.join(dshHome(), 'profiles')
  const out = []
  if (!fs.existsSync(profilesDir)) return out
  for (const profile of fs.readdirSync(profilesDir).sort()) {
    if (onlyProfile !== undefined && profile !== onlyProfile) continue
    const file = path.join(profilesDir, profile, 'node_modules', ...PLUGIN.split('/'), 'lib', 'index.js')
    if (fs.existsSync(file)) out.push({ profile, file })
  }
  return out
}

/** 已打补丁的判定标志：execCommand 的签名里带上了新参数。 */
const PATCHED_SIG = 'async function execCommand(engine, alias, command, timeoutMs, signal, onKeyboardInteractive) {'

function isPatched(code) {
  return code.includes(PATCHED_SIG)
}

function must(code, label, from, to) {
  const count = code.split(from).length - 1
  if (count !== 1) throw new Error(label + ': expected exactly 1 match, found ' + count)
  return code.replace(from, to)
}

function mustWithin(code, startMarker, endMarker, label) {
  const start = code.indexOf(startMarker)
  const end = start < 0 ? -1 : code.indexOf(endMarker, start)
  if (start < 0 || end < 0) throw new Error(label + ': boundaries not found')
  return { start, end, text: code.slice(start, end) }
}

const OTP_PARAM = [
  '\t\t\totp: {',
  '\t\t\t\ttype: "string",',
  '\t\t\t\tdescription: "One-time code (TOTP or SMS) for hosts that require keyboard-interactive 2FA. Every prompt of the challenge is answered with this value; the pooled connection is reused afterwards, so later calls need no code."',
  '\t\t\t}',
].join('\n')

/** 把未打补丁的 lib/index.js 源码转成打过补丁的版本。 */
export function applyPatch(input) {
  let code = input

  code = must(code, 'acquire signature',
    'async function acquire(engine, alias) {',
    'async function acquire(engine, alias, onKeyboardInteractive) {')
  code = must(code, 'acquire -> doAcquire',
    'const task = doAcquire(engine, alias);',
    'const task = doAcquire(engine, alias, onKeyboardInteractive);')
  code = must(code, 'doAcquire signature',
    'async function doAcquire(engine, alias) {',
    'async function doAcquire(engine, alias, onKeyboardInteractive) {')
  code = must(code, 'doAcquire -> connectChain',
    'const { client, hops } = await connectChain(engine, entry);',
    'const { client, hops } = await connectChain(engine, entry, onKeyboardInteractive);')
  code = must(code, 'withClient signature',
    'async function withClient(engine, alias, fn, attempts = 3, signal) {',
    'async function withClient(engine, alias, fn, attempts = 3, signal, onKeyboardInteractive) {')
  code = must(code, 'withClient -> acquire',
    'record = await acquire(engine, alias);',
    'record = await acquire(engine, alias, onKeyboardInteractive);')
  code = must(code, 'execCommand signature',
    'async function execCommand(engine, alias, command, timeoutMs, signal) {',
    'async function execCommand(engine, alias, command, timeoutMs, signal, onKeyboardInteractive) {')

  // execCommand 内以 withClient(...) 收尾：补上 attempts / signal / handler 三个实参
  {
    const { start, end, text } = mustWithin(code, PATCHED_SIG, '\n//#endregion', 'execCommand body')
    const tail = '\t});\n}'
    const idx = text.lastIndexOf(tail)
    if (idx < 0) throw new Error('execCommand -> withClient tail not found')
    const patched = text.slice(0, idx)
      + '\t}, 3, signal, onKeyboardInteractive);\n}'
      + text.slice(idx + tail.length)
    code = code.slice(0, start) + patched + code.slice(end)
  }

  code = must(code, 'engine.exec signature',
    'async exec(alias, command, timeoutMs, signal) {',
    'async exec(alias, command, timeoutMs, signal, onKeyboardInteractive) {')
  code = must(code, 'engine.exec -> execCommand',
    'return execCommand(this, alias, command, timeoutMs, signal);',
    'return execCommand(this, alias, command, timeoutMs, signal, onKeyboardInteractive);')

  // ssh_exec 的参数表加 otp
  {
    const { start, end, text } = mustWithin(code, 'function sshExecTool(engine) {', '/** The upload tool. */', 'sshExecTool body')
    const timeoutBlock = [
      '\t\t\ttimeoutMs: {',
      '\t\t\t\ttype: "integer",',
      '\t\t\t\tdescription: "Timeout in milliseconds (default 60000)."',
      '\t\t\t}',
    ].join('\n')
    if (!text.includes(timeoutBlock)) throw new Error('ssh_exec timeoutMs block not found')
    const patched = text.replace(timeoutBlock, timeoutBlock + ',\n' + OTP_PARAM)
    code = code.slice(0, start) + patched + code.slice(end)
  }

  // ssh_exec 的 execute：有 otp 时构造 keyboard-interactive 处理器
  {
    const oldCall = '\t\t\t\treturn await engine.exec(args.alias, args.command, args.timeoutMs, exec.signal);'
    const newCall = [
      '\t\t\t\tconst onKeyboardInteractive = args.otp === void 0 ? void 0 : (name, instructions, lang, prompts, finish) => {',
      '\t\t\t\t\tfinish(prompts.map(() => String(args.otp)));',
      '\t\t\t\t};',
      '\t\t\t\treturn await engine.exec(args.alias, args.command, args.timeoutMs, exec.signal, onKeyboardInteractive);',
    ].join('\n')
    code = must(code, 'ssh_exec execute -> engine.exec', oldCall, newCall)
  }

  return code
}

/** 用 node --check 校验语法（写临时文件，避免把坏产物留在原地）。 */
function syntaxCheck(code) {
  const tmp = path.join(os.tmpdir(), 'dsh-ssh-otp-check-' + process.pid + '.mjs')
  fs.writeFileSync(tmp, code, 'utf8')
  try {
    execFileSync(process.execPath, ['--check', tmp], { stdio: 'pipe' })
  } finally {
    try { fs.unlinkSync(tmp) } catch { /* best effort */ }
  }
}

function main() {
  const targets = findTargets()
  if (targets.length === 0) {
    console.error('[otp-patch] 没找到 ' + PLUGIN + ' 的 lib/index.js，检查 DSH_HOME 或 --profile')
    process.exit(1)
  }
  let failed = 0
  for (const { profile, file } of targets) {
    const code = fs.readFileSync(file, 'utf8')
    const orig = file + ORIG_SUFFIX

    if (checkOnly) {
      console.log('[otp-patch] ' + profile + ': ' + (isPatched(code) ? '已打补丁' : '未打补丁'))
      continue
    }

    if (revert) {
      if (!fs.existsSync(orig)) {
        console.log('[otp-patch] ' + profile + ': 没有备份，跳过')
        continue
      }
      fs.copyFileSync(orig, file)
      console.log('[otp-patch] ' + profile + ': 已从备份恢复')
      continue
    }

    if (isPatched(code)) {
      console.log('[otp-patch] ' + profile + ': 已是补丁版本，跳过')
      continue
    }

    let patched
    try {
      patched = applyPatch(code)
      syntaxCheck(patched)
    } catch (error) {
      failed += 1
      console.error('[otp-patch] ' + profile + ': 失败 — ' + (error instanceof Error ? error.message : String(error)))
      continue
    }

    if (dryRun) {
      console.log('[otp-patch] ' + profile + ': 可打补丁（dry-run，未写入）')
      continue
    }

    // 首次补丁前保留原始产物，供 --revert 使用
    if (!fs.existsSync(orig)) fs.copyFileSync(file, orig)
    fs.writeFileSync(file, patched, 'utf8')
    console.log('[otp-patch] ' + profile + ': 补丁已写入 ' + file)
  }
  if (failed > 0) process.exitCode = 1
  if (!checkOnly && !dryRun && !revert) {
    console.log('[otp-patch] 完成。需要重启 dsh web 才会加载新的 host 产物。')
  }
}

main()
