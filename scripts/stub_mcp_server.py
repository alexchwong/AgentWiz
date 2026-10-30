#!/usr/bin/env python3
"""A scripted MCP server for trying AgentWiz's Tools panel without installing a real one (techspec 0.1.2,
Section 13.3). Standard library only; not shipped.

    python scripts/stub_mcp_server.py [--port 8098] [--era dual|modern|legacy] [--token TOKEN]

The first line printed is always `stub MCP server at http://127.0.0.1:<port>/mcp`. --port 0 picks a free
port. --era dual (default) answers MCP 2026-07-28 and the handshake revisions on one endpoint, as current
SDKs do; modern refuses `initialize`; legacy answers `server/discover` with HTTP 400 -32600 and issues
session IDs. --token requires `Authorization: Bearer TOKEN`.

Tools: echo, slow, fail, numbers, hang, header_demo. Every request, client disconnect, cancellation
notice and DELETE is logged on one line; tools/call logs its arguments as the exact JSON text received.
"""
from __future__ import annotations

import argparse
import itertools
import json
import select
import socket
import sys
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODERN = "2026-07-28"
LEGACY = ("2025-11-25", "2025-06-18", "2025-03-26")

TOOLS = [
    {"name": "echo", "description": "Returns the text repeated.",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string", "description": "Text to echo"},
         "times": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None}}, "required": ["text"]}},
    {"name": "slow", "description": "Sends a progress notification every second, then 'done'.",
     "inputSchema": {"type": "object", "properties": {"seconds": {"type": "integer", "default": 5}},
                     "required": ["seconds"]}},
    {"name": "fail", "description": "Returns a tool error.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "numbers", "description": "Echoes id and ratio exactly, as structured content and text.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "integer", "default": 9007199254740993},
         "ratio": {"type": "number", "default": Decimal("0.10000000000000000001")}}}},
    {"name": "hang", "description": "Never answers, until the client disconnects.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "header_demo", "description": "Its region argument is mirrored into an Mcp-Param-Region header.",
     "inputSchema": {"type": "object", "properties": {
         "region": {"type": "string", "x-mcp-header": "Region"}}, "required": ["region"]}},
]


def dumps(value) -> str:
    """Exact JSON text: Decimal written with its own digits (standard library only)."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k) + ":" + dumps(v) for k, v in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(dumps(v) for v in value) + "]"
    return json.dumps(value)


def log(line: str) -> None:
    print(line, flush=True)


class Stub(BaseHTTPRequestHandler):
    era_mode = "dual"
    token: str | None = None
    sessions: set = set()
    counter = itertools.count(1)

    def log_message(self, *a):
        pass

    # --- plumbing ---------------------------------------------------------------------------------
    def _json(self, payload, status=200, headers=None):
        data = dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _empty(self, status):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _error(self, rid, code, message, status=200, data=None):
        err = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        self._json({"jsonrpc": "2.0", "id": rid, "error": err}, status)

    def _event(self, payload):
        self.wfile.write(b"data: " + dumps(payload).encode() + b"\n\n")
        self.wfile.flush()

    def _peer_closed(self, wait=0.05) -> bool:
        try:
            if select.select([self.connection], [], [], wait)[0]:
                return not self.connection.recv(1, socket.MSG_PEEK)
        except OSError:
            return True
        return False

    def _era(self):
        if self.era_mode == "modern":
            return "modern"
        if self.era_mode == "legacy":
            return "legacy"
        return "modern" if self.headers.get("MCP-Protocol-Version") == MODERN else "legacy"

    # --- requests --------------------------------------------------------------------------------
    def do_DELETE(self):
        log(f"DELETE session {self.headers.get('Mcp-Session-Id')}")
        Stub.sessions.discard(self.headers.get("Mcp-Session-Id"))
        self._empty(200)

    def do_GET(self):
        self._empty(405)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.token is not None and self.headers.get("Authorization") != f"Bearer {self.token}":
            log("refused: missing or wrong token")
            return self._json({"error": "unauthorized"}, 401)
        try:
            body = json.loads(raw, parse_float=Decimal)
        except ValueError:
            return self._error(None, -32700, "Parse error", 400)
        method, rid = body.get("method"), body.get("id")
        era = self._era()
        if method is None:
            log(f"[{era}] answer to server request {rid}")
            return self._empty(202)
        if method == "notifications/cancelled":
            log(f"[{era}] cancellation notice for request {body.get('params', {}).get('requestId')}")
            return self._empty(202)
        if era == "modern":
            return self._modern(body, method, rid, raw)
        return self._legacy(body, method, rid, raw)

    def _modern(self, body, method, rid, raw):
        if self.headers.get("MCP-Protocol-Version") != MODERN:
            return self._error(rid, -32022, "Unsupported protocol version", 400, {"supported": [MODERN]})
        if self.headers.get("Mcp-Method") != method:
            return self._error(rid, -32020, "Mcp-Method does not match the body", 400)
        log(f"[modern {MODERN}] {method}" + self._call_note(body, raw))
        if method == "server/discover":
            return self._json({"jsonrpc": "2.0", "id": rid, "result": {
                "supportedVersions": [MODERN],
                "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "stub_mcp_server", "version": "0.1.2"}}}})
        if method == "tools/list":
            return self._json({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        if method == "tools/call":
            return self._tool(body, rid)
        return self._error(rid, -32601, "Method not found", 404)

    def _legacy(self, body, method, rid, raw):
        if self.headers.get("MCP-Protocol-Version") == MODERN or method == "server/discover":
            log(f"[legacy] refused {method}: unsupported protocol version")
            return self._error(rid, -32600, f"Unsupported protocol version: {MODERN}", 400)
        if method == "initialize":
            if self.era_mode == "modern":
                return self._error(rid, -32601, "Method not found", 404)
            offered = body.get("params", {}).get("protocolVersion")
            version = offered if offered in LEGACY else LEGACY[0]
            sid = f"stub-session-{next(Stub.counter)}"
            Stub.sessions.add(sid)
            log(f"[legacy {version}] initialize -> session {sid}")
            return self._json({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": version, "capabilities": {"tools": {}},
                "serverInfo": {"name": "stub_mcp_server", "version": "0.1.2"}}}, headers={"Mcp-Session-Id": sid})
        sid = self.headers.get("Mcp-Session-Id")
        if sid not in Stub.sessions:
            log(f"[legacy] {method}: unknown session")
            return self._json({"error": "Session not found"}, 404)
        version = self.headers.get("MCP-Protocol-Version")
        log(f"[legacy {version}] {method}" + self._call_note(body, raw))
        if method == "notifications/initialized":
            return self._empty(202)
        if method == "ping":
            return self._json({"jsonrpc": "2.0", "id": rid, "result": {}})
        if method == "tools/list":
            return self._json({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        if method == "tools/call":
            return self._tool(body, rid)
        return self._error(rid, -32601, "Method not found")

    def _call_note(self, body, raw) -> str:
        if body.get("method") != "tools/call":
            return ""
        text = raw.decode()
        # the arguments exactly as received: re-serialised with Decimal, which keeps every digit
        args = dumps(body.get("params", {}).get("arguments"))
        header = {k: v for k, v in self.headers.items() if k.lower().startswith("mcp-param-")}
        return f" {body['params'].get('name')} arguments={args}" + (f" headers={header}" if header else "") \
            + ("" if len(text) < 100000 else " (large)")

    # --- tools -----------------------------------------------------------------------------------
    def _tool(self, body, rid):
        params = body.get("params", {})
        name, args = params.get("name"), params.get("arguments") or {}
        token = params.get("_meta", {}).get("progressToken")
        result = None
        if name == "echo":
            times = args.get("times") or 1
            result = {"content": [{"type": "text", "text": str(args.get("text", "")) * int(times)}], "isError": False}
        elif name == "fail":
            result = {"content": [{"type": "text", "text": "the stub's fail tool always fails"}], "isError": True}
        elif name == "numbers":
            values = {"id": args.get("id", 9007199254740993), "ratio": args.get("ratio", Decimal("0.10000000000000000001"))}
            result = {"content": [{"type": "text", "text": dumps(values)}], "structuredContent": values,
                      "isError": False}
        elif name == "header_demo":
            result = {"content": [{"type": "text", "text": f"Mcp-Param-Region: {self.headers.get('Mcp-Param-Region')}"}],
                      "isError": False}
        if result is not None:
            return self._json({"jsonrpc": "2.0", "id": rid, "result": result})
        if name not in ("slow", "hang"):
            return self._json({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": f"unknown tool {name}"}], "isError": True}})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            if name == "slow":
                seconds = int(args.get("seconds", 5))
                for i in range(1, seconds + 1):
                    if self._peer_closed(1.0):
                        log(f"client disconnected during slow ({i - 1}/{seconds})")
                        return
                    self._event({"jsonrpc": "2.0", "method": "notifications/progress",
                                 "params": {"progressToken": token, "progress": i, "total": seconds,
                                            "message": f"step {i} of {seconds}"}})
                self._event({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": "done"}], "isError": False}})
                return
            while not self._peer_closed(0.2):
                pass
            log("client disconnected during hang")
        except OSError:
            log(f"client disconnected during {name}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8098)
    parser.add_argument("--era", choices=("dual", "modern", "legacy"), default="dual")
    parser.add_argument("--token")
    args = parser.parse_args(argv)
    Stub.era_mode, Stub.token = args.era, args.token
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Stub)
    httpd.daemon_threads = True
    print(f"stub MCP server at http://127.0.0.1:{httpd.server_address[1]}/mcp", flush=True)
    print(f"era {args.era}" + (", token required" if args.token else "") + "; Ctrl-C to stop", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    sys.exit(main())
