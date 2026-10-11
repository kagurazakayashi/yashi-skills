#!/usr/bin/env python
"""Minimal Dart VM Service client for a running Flutter app.

All channels go over the same VM Service WebSocket that DevTools uses:

  tree  [--depth N]      -> Widget Tree via ext.flutter.debugDumpApp
  logs  --seconds N      -> app stdout/stderr via streamListen(Stdout/Stderr/Logging)
  isolate                -> isolate id + debug name + pause state
  rpc   <method> [json]  -> call any VM Service RPC (escape hatch)

Usage:
  python devtools_probe.py --ws ws://127.0.0.1:PORT/TOKEN=/ws tree
  python devtools_probe.py --ws ws://127.0.0.1:PORT/TOKEN=/ws logs --seconds 8

Requires: pip install websocket-client
"""
import argparse
import base64
import json
import sys
import time

import websocket  # pip install websocket-client


class VMService:
    def __init__(self, ws_uri, timeout=15):
        self.ws = websocket.create_connection(ws_uri, timeout=timeout)
        self._id = 0

    def call(self, method, params=None):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps(
            {"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method} -> {msg['error']}")
                return msg.get("result",)

    def isolate_id(self):
        isolates = self.call("getVM").get("isolates", [])
        if not isolates:
            raise RuntimeError("no isolate found (is the app running?)")
        return isolates[0]["id"]


def cmd_tree(vm, args):
    iso = vm.isolate_id()
    res = vm.call("ext.flutter.debugDumpApp", {"isolateId": iso})
    text = res.get("data", "")
    if args.depth:
        out = []
        for line in text.splitlines():
            indent = len(line) - len(line.lstrip(" \u2502\u2514\u251c\u2500"))
            if indent // 2 <= args.depth:
                out.append(line)
        text = "\n".join(out)
    print(text)


def cmd_logs(vm, args):
    for sid in ("Stdout", "Stderr", "Logging"):
        try:
            vm.call("streamListen", {"streamId": sid})
        except RuntimeError:
            pass
    vm.ws.settimeout(args.seconds)
    start = time.time()
    while time.time() - start < args.seconds:
        try:
            msg = json.loads(vm.ws.recv())
        except Exception:
            break
        if msg.get("method") != "streamNotify":
            continue
        ev = msg["params"]["event"]
        stream = msg["params"]["streamId"]
        if ev.get("kind") == "WriteEvent":
            raw = ev.get("bytes", "")
            try:
                raw = base64.b64decode(raw).decode("utf-8", "replace")
            except Exception:
                pass
            sys.stdout.write(raw)
            sys.stdout.flush()
        elif stream == "Logging":
            print(f"[Logging] {ev.get('logRecord', ev)}")


def cmd_isolate(vm, args):
    iso = vm.isolate_id()
    info = vm.call("getIsolate", {"isolateId": iso})
    print(f"isolateId : {iso}")
    print(f"name      : {info.get('name')}")
    print(f"pause     : {info.get('pauseEvent',).get('kind')}")


def cmd_rpc(vm, args):
    params = json.loads(args.json) if args.json else {}
    iso = params.pop("isolateId", None) or vm.isolate_id()
    params["isolateId"] = iso
    print(json.dumps(vm.call(args.method, params), indent=2, ensure_ascii=False)[:4000])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", required=True, help="VM Service ws URI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("tree"); p.add_argument("--depth", type=int, default=0)
    p.set_defaults(func=cmd_tree)

    p = sub.add_parser("logs"); p.add_argument("--seconds", type=float, default=8.0)
    p.set_defaults(func=cmd_logs)

    p = sub.add_parser("isolate"); p.set_defaults(func=cmd_isolate)

    p = sub.add_parser("rpc")
    p.add_argument("method"); p.add_argument("json", nargs="?", default="")
    p.set_defaults(func=cmd_rpc)

    args = ap.parse_args()
    vm = VMService(args.ws)
    try:
        args.func(vm, args)
    finally:
        try:
            vm.ws.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
