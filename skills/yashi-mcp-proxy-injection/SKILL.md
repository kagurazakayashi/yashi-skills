---
name: yashi-mcp-proxy-injection
description: 为任意读取网址或通过非中国搜索引擎（Google、Bing 国际版等）搜索的本地 MCP 服务器注入代理，解决其联网失败(fetch failed、chromium ERR_TIMED_OUT / ERR_CONNECTION_TIMED_OUT、无法访问被墙的 API 或搜索引擎)。通用方法论：适用于任意此类 MCP 服务器、任意 MCP host（opencode / Claude Code / Claude Desktop / Cline 等）与任意本地代理服务器（HTTP CONNECT 或 SOCKS5），代理地址通过环境变量可配置，不绑定特定插件。安装此类 MCP 服务器时，先询问用户是否需要代理补丁、以及代理到哪个地址，确认后再动手。适用于安装、修复或排查无法联网的 MCP 服务器、MCP host 的 env 代理设置无效、以及 npm update -g 覆盖补丁后需要重新注入的场景。
---

# MCP 代理注入

为自身不遵循代理设置的 MCP 服务器注入代理，解决其网络请求失败的问题。

## 何时使用

- 联网型 MCP 请求失败：`fetch failed`、chromium 报 `ERR_TIMED_OUT` /
  `ERR_CONNECTION_TIMED_OUT`，或访问 Google / 被地域封锁的 API 超时。
- 新安装需要代理的浏览器型或 fetch 型 MCP。
- `npm update -g` 覆盖补丁后需要重新注入。

## 先询问：安装时不要擅自打补丁

安装/部署任何**读取网址或通过非中国搜索引擎搜索**的本地 MCP 服务器（fetch、browser、search 类）时，**先问用户，再动手**：

1. **是否需要代理补丁？**（此类 MCP 通常要访问境外站点，默认建议需要，但仍要确认）
   - 不需要（如仅访问国内站点、或 MCP host 已能传递 `env` 代理）：跳过补丁，正常配置即可。
2. **代理到哪里？**（若需要）
   - 给出本机默认 `http://127.0.0.1:23333`，同时接受用户指定的任意 HTTP CONNECT / SOCKS5 地址（如 `socks5://127.0.0.1:23332`、`http://127.0.0.1:7890`）。
   - 确认后把地址写入环境变量 `MCP_PROXY_SERVER`（或作为补丁 fallback 的默认值），不要硬编码到多个位置。
3. 完成后在答复中说明：**补丁位置、代理地址、生效方式**（环境变量 vs 补丁 fallback），并提示重启 MCP host 后验证。

> 本机历史约定：已部署的 brave-search / g-search / fetcher 使用变量名 `MYMCP_PROXY_SERVER`，与 `MCP_PROXY_SERVER` 等效；新安装统一用 `MCP_PROXY_SERVER`。

## 代理的解析方式（可配置，不硬编码）

下面所有模板都按优先级从环境变量解析代理：

```js
process.env.MCP_PROXY_SERVER || process.env.HTTPS_PROXY || process.env.HTTP_PROXY || "http://127.0.0.1:23333"
```

- 设置 `MCP_PROXY_SERVER`（专用，推荐）或标准的 `HTTPS_PROXY`/`HTTP_PROXY` 指向**你自己的**本地代理。
- 本机历史部署（brave-search / g-search / fetcher）使用变量名 `MYMCP_PROXY_SERVER`，与本技能的 `MCP_PROXY_SERVER` 等效；两者都是受支持的专用变量，不必改已有补丁。
- HTTP CONNECT 代理（如 Clash 的 `127.0.0.1:23333`）对 chromium 和 fetch 都有效；SOCKS5（`socks5://127.0.0.1:23332`）对 chromium 有效，但按我们的经验对 undici 的 `fetch` 不可靠——拿不准时优先用 HTTP。
- 回退值 `http://127.0.0.1:23333` 是本机约定；要改就改这一处（环境变量），不要逐个去改补丁。
- 如果国内站点**经代理**反而失败，说明代理客户端需要直连规则（如 Clash 的 `GEOIP,CN,DIRECT`）——这是代理客户端的配置问题，不是 MCP 的。

## 先诊断

1. 先确认代理本身可用（Linux/macOS 上把 `-o NUL` 换成 `-o /dev/null`）：
   `curl -x $MCP_PROXY_SERVER -s -o NUL -w "%{http_code}" https://<target>/`
2. 对基于 chromium 的 MCP，直接用其内置浏览器测试，分别带与不带显式代理：
   `"<...>\chrome-headless-shell.exe" --headless --no-sandbox --proxy-server="$MCP_PROXY_SERVER" --dump-dom "https://www.google.com/" > out.html`
   带 `--proxy-server` 成功、不带则失败，即证明该 MCP 没有把代理交给 chromium——套用下面的方法 A。

## 注入方法

### A. Playwright / chromium 型 MCP（基于浏览器）

在调用 `chromium.launch` 的那个文件顶部打补丁，包一层以强制带上 `proxy`。一个包装即可覆盖文件里所有启动点：

```js
import { chromium } from "playwright";
const __PROXY = process.env.MCP_PROXY_SERVER || process.env.HTTPS_PROXY || process.env.HTTP_PROXY || "http://127.0.0.1:23333";
const __origLaunch = chromium.launch.bind(chromium);
chromium.launch = (o = {}) => __origLaunch({ ...o, proxy: { server: __PROXY } });
```

### B. Node 内置 `fetch` 型 MCP（基于 API）

Node 内置的 `fetch`（undici）走全局 dispatcher。需要 `undici` 包（缺失时 `npm i -g undici`；它会从全局 `node_modules` 根解析）。给入口文件打补丁：

```js
import { ProxyAgent, setGlobalDispatcher } from "undici";
setGlobalDispatcher(new ProxyAgent(process.env.MCP_PROXY_SERVER || process.env.HTTPS_PROXY || process.env.HTTP_PROXY || "http://127.0.0.1:23333"));
```

注意：Node 的 `NODE_USE_ENV_PROXY=1` 配合 `HTTP(S)_PROXY` 对直接启动的 node 进程也有效，但在这里不可靠——很多 MCP host（如 opencode）不会把 MCP 的 `env` 透传下去，所以宁可打补丁，不要依赖它。

如果该服务器用的是 `playwright` 的 `browserType.launch`、`puppeteer.launch` 或 chromium 的 `--proxy-server` 参数而不是内置 `fetch`，思路相同：包装启动调用（方法 A），或往启动参数里前置 `--proxy-server`（下面的方法 C）。

### C. Chromium 启动参数型 MCP（基于参数列表）

当 MCP 用显式参数列表启动 chromium（如 puppeteer 或自定义启动器）时，注入参数而不是包装调用：

```js
const __PROXY = process.env.MCP_PROXY_SERVER || process.env.HTTPS_PROXY || process.env.HTTP_PROXY || "http://127.0.0.1:23333";
// 在启动参数数组中加入：
// '--proxy-server=' + __PROXY
```

## 打完补丁之后

1. 把 MCP host 的配置（`opencode.json` 的 `mcp.<name>.command`、Claude Desktop 的 `claude_desktop_config.json` 等）指向 `node <入口>`——绕开 `npx`，它会拉起未打补丁的新副本。允许覆盖 env 的 host，直接在那里设 `MCP_PROXY_SERVER`，不必改补丁。
2. 语法检查：`node --check <打过补丁的文件>`。
3. 重启 MCP host，再调用该 MCP 工具确认。

本机当前部署的 MCP（brave-search / g-search / fetcher）的具体补丁位置、入口与版本等台账信息见回收站中的 `yashi-mcp-setup`（2026-09-15 删除，可从回收站恢复）；本技能保持通用方法论，不绑定具体部署。
