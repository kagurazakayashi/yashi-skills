#!/usr/bin/env python
"""Minimal Dart VM Service client for a running Flutter app.

All channels go over the same VM Service WebSocket that DevTools uses:

  tree  [--depth N]      -> Widget Tree via ext.flutter.debugDumpApp
  logs  --seconds N      -> app stdout/stderr via streamListen(Stdout/Stderr/Logging)
  isolate                -> isolate id + debug name + pause state
  rpc   <method> [json] [--isolate]
                         -> call any VM Service RPC (escape hatch).
                            isolateId is attached automatically ONLY when the
                            method needs it (see cmd_rpc); --isolate forces it.

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
    """Call any VM Service RPC.

    isolateId must NOT be attached unconditionally: VM-level RPCs such as
    getVM / getVersion take no isolateId and reject an unexpected one with
    -32602 Invalid params. So the caller's params are sent as-is first, and an
    isolateId is attached only when the service reports it is required.

    An explicit "isolateId" inside the JSON always wins, and --isolate forces
    the injection without the probe round-trip.
    """
    params = json.loads(args.json) if args.json else {}
    explicit = params.pop("isolateId", None)
    if explicit or args.isolate:
        params["isolateId"] = explicit or vm.isolate_id()
        _dump(vm.call(args.method, params))
        return
    try:
        result = vm.call(args.method, params)
    except RuntimeError as exc:
        # isolate-scoped RPC called without isolateId -> retry once with it.
        if "isolateId" not in str(exc):
            raise
        params["isolateId"] = vm.isolate_id()
        result = vm.call(args.method, params)
    _dump(result)


def _dump(obj, limit=4000):
    print(json.dumps(obj, indent=2, ensure_ascii=False)[:limit])


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
    p.add_argument("--isolate", action="store_true",
                   help="force-attach isolateId (otherwise auto-probed)")
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
