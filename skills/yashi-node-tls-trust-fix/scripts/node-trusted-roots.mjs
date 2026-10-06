#!/usr/bin/env node
/**
 * 打印 Node **内置**（随发行版打包）根证书的 SHA-256 指纹，每行一个十六进制串。
 *
 * 用途：配合 export-intercept-ca.ps1，回答「Node 是否已经认识这个根证书」。
 * 只有答案为「否」时，那个根才是需要加入信任列表的拦截方根证书。
 *
 * 为什么用 tls.rootCertificates 而不是 tls.getCACertificates('default')：
 *   - tls.rootCertificates 固定为内置打包的根（实测 145 个，且不受 NODE_EXTRA_CA_CERTS 影响）
 *   - getCACertificates('default') 会把 NODE_EXTRA_CA_CERTS 引入的根也算进去
 *   - getCACertificates('system') 返回的是系统存储里的根，它与「Node 当前是否使用系统存储」无关，
 *     直接用它会把「操作系统信任」误判成「Node 信任」
 * 因此判断「Node 认识吗」只能用 tls.rootCertificates。
 *
 * 注意：本脚本刻意不含任何需要转义的引号逻辑之外的东西，也不依赖命令行 -e 传参，
 * 因为 Windows PowerShell 5.1 在向原生程序传含引号的参数时会吞掉引号。
 *
 * 用法：
 *   node node-trusted-roots.mjs                    仅打印指纹，每行一个
 *   node node-trusted-roots.mjs --info             额外打印数量与 Node 版本到 stderr
 *   node node-trusted-roots.mjs --fingerprint <pem>  打印指定 PEM 中每张证书的 SHA-256
 */

import tls from 'node:tls';
import { X509Certificate, createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

const fingerprint = (cert) => createHash('sha256').update(cert.raw).digest('hex');

const argv = process.argv.slice(2);
const fpIndex = argv.indexOf('--fingerprint');
if (fpIndex !== -1) {
  const path = argv[fpIndex + 1];
  if (!path) {
    process.stderr.write('--fingerprint 需要一个 PEM 文件路径\n');
    process.exit(2);
  }
  // 一个 PEM 里可能有多张证书，逐张输出；用 Node 解析以避开
  // Windows PowerShell 5.1 对含引号参数的破坏。
  const text = readFileSync(path, 'utf8');
  const blocks = text.match(/-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----/g) ?? [];
  if (blocks.length === 0) {
    process.stderr.write(`未在 ${path} 中找到任何证书\n`);
    process.exit(2);
  }
  for (const block of blocks) process.stdout.write(`${fingerprint(new X509Certificate(block))}\n`);
  process.exit(0);
}

if (argv.includes('--info')) {
  process.stderr.write(`node ${process.version}, bundled roots: ${tls.rootCertificates.length}\n`);
}

for (const pem of tls.rootCertificates) {
  process.stdout.write(`${fingerprint(new X509Certificate(pem))}\n`);
}
