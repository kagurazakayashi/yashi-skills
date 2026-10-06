#!/usr/bin/env node
/**
 * 探测主机的 TLS 信任状况，用于定位「杀毒软件 / 企业代理对 HTTPS 做中间人替换」这类问题。
 *
 * 原理：裸 TLS 握手会暴露对端证书的真实签发者。若签发者不是该站的公开 CA，
 * 而是某个本地软件（杀毒软件、代理）的根证书，就说明存在中间人替换。
 * `--fetch` 则走 Node 内置 fetch（含 undici 全局 dispatcher 与环境代理变量），
 * 复现应用真实的请求路径。
 *
 * 之所以要跑多次：拦截常是间歇性的，同一域名的签发者会在真实 CA 与拦截方之间随机切换，
 * 单次成功不代表没有问题。
 *
 * 用法：
 *   node tls_probe.mjs api.deepseek.com
 *   node tls_probe.mjs api.deepseek.com --runs 12 --fetch -v
 *   node tls_probe.mjs a.example.com b.example.com --json
 *
 * 退出码：0 = 全部成功；1 = 存在失败；2 = 参数错误。
 */

import tls from 'node:tls';

const USAGE = `用法: node tls_probe.mjs <主机> [<主机>...] [选项]

选项:
  --runs N     每个主机探测的次数，默认 8；定位间歇性拦截建议 >= 12
  --fetch      额外用真实 fetch 请求一次 https://<主机><路径>
  --path P     --fetch 时请求的路径，默认 /
  -v, --verbose 逐次输出明细
  --json       以 JSON 输出
  -h, --help   显示本帮助

退出码: 0 全部成功; 1 存在失败; 2 参数错误`;

function parseArgs(argv) {
  const hosts = [];
  const opts = { runs: 8, fetch: false, path: '/', verbose: false, json: false };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--runs') {
      const n = Number(argv[i + 1]);
      if (!Number.isInteger(n) || n < 1) {
        console.error('--runs 需要一个正整数');
        process.exit(2);
      }
      opts.runs = n;
      i += 1;
    } else if (a === '--fetch') opts.fetch = true;
    else if (a === '--path') { opts.path = argv[i + 1]; i += 1; }
    else if (a === '-v' || a === '--verbose') opts.verbose = true;
    else if (a === '--json') opts.json = true;
    else if (a === '-h' || a === '--help') { console.log(USAGE); process.exit(0); }
    else if (a.startsWith('-')) { console.error(`未知选项: ${a}\n\n${USAGE}`); process.exit(2); }
    else hosts.push(a);
  }
  return { hosts, opts };
}

/** 一次裸 TLS 握手，返回授权结果与对端证书签发者。 */
function probeTls(host, port = 443, timeoutMs = 12000) {
  return new Promise((resolve) => {
    let settled = false;
    const done = (r) => { if (!settled) { settled = true; resolve(r); } };
    const socket = tls.connect({ host, port, servername: host, timeout: timeoutMs }, () => {
      const cert = socket.getPeerCertificate();
      const result = {
        authorized: socket.authorized,
        authError: socket.authorizationError ?? null,
        issuerCN: cert?.issuer?.CN ?? null,
        issuerOrg: cert?.issuer?.O ?? null,
        subjectCN: cert?.subject?.CN ?? null,
      };
      socket.end();
      done(result);
    });
    socket.on('timeout', () => { socket.destroy(); done({ authorized: false, error: 'TIMEOUT' }); });
    socket.on('error', (err) => done({ authorized: false, error: err.code ?? err.message }));
  });
}

/** 一次真实 fetch，只关心是否拿到 HTTP 响应（任何状态码都算 TLS 成功）。 */
async function probeFetch(host, path) {
  try {
    const res = await fetch(`https://${host}${path}`, { method: 'GET', redirect: 'manual' });
    return { ok: true, status: res.status };
  } catch (err) {
    return { ok: false, error: err?.cause?.code ?? err?.cause?.message ?? err?.message ?? 'unknown' };
  }
}

const { hosts, opts } = parseArgs(process.argv.slice(2));
if (hosts.length === 0) {
  console.error(USAGE);
  process.exit(2);
}

const report = [];
let anyFailure = false;

for (const host of hosts) {
  const runs = [];
  for (let i = 0; i < opts.runs; i += 1) runs.push(await probeTls(host));

  const issuers = new Map();
  let tlsOk = 0;
  const errors = new Map();
  for (const r of runs) {
    if (r.authorized) {
      tlsOk += 1;
      const key = r.issuerCN ?? '(未知)';
      issuers.set(key, (issuers.get(key) ?? 0) + 1);
    } else {
      const key = r.error ?? r.authError ?? 'unknown';
      errors.set(key, (errors.get(key) ?? 0) + 1);
    }
  }

  let fetchResult = null;
  if (opts.fetch) fetchResult = await probeFetch(host, opts.path);

  const failed = runs.length - tlsOk;
  if (failed > 0 || (fetchResult && !fetchResult.ok)) anyFailure = true;

  report.push({
    host,
    runs: runs.length,
    tlsAuthorized: tlsOk,
    tlsFailed: failed,
    issuers: [...issuers.entries()].map(([issuer, count]) => ({ issuer, count })),
    errors: [...errors.entries()].map(([error, count]) => ({ error, count })),
    fetch: fetchResult,
    detail: opts.verbose ? runs : undefined,
  });

  if (!opts.json) {
    console.log(`\n=== ${host} ===`);
    console.log(`  裸 TLS 授权成功: ${tlsOk}/${runs.length}`);
    for (const [issuer, count] of issuers) console.log(`    签发者 ${issuer}  x${count}`);
    for (const [error, count] of errors) console.log(`    失败   ${error}  x${count}`);
    if (fetchResult) {
      console.log(fetchResult.ok
        ? `  真实 fetch: HTTP ${fetchResult.status}（TLS 成功）`
        : `  真实 fetch: 失败 ${fetchResult.error}`);
    }
    if (opts.verbose && opts.detail) {
      for (const [i, r] of report[report.length - 1].detail.entries()) {
        console.log(`    #${i + 1} authorized=${r.authorized} issuer=${r.issuerCN ?? r.error}`);
      }
    }
    // 同一主机出现多个签发者，是间歇性中间人的直接证据。
    if (issuers.size > 1) {
      console.log('  提示: 出现多个签发者，说明拦截是间歇性的（真实 CA 与拦截方交替）。');
    }
  }
}

if (opts.json) {
  console.log(JSON.stringify(report, null, 2));
} else {
  console.log(`\n总计: ${anyFailure ? '存在失败' : '全部成功'}`);
}

process.exit(anyFailure ? 1 : 0);
