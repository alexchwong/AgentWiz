# Adapted from modelSpell scripts/smoke_installed.py at commit 1759c6e3c28a637731434a73c46088048583e358
# (MIT, Copyright (c) 2026 Alex Wong), per AgentWiz techspec 11.2 (0.1.0), 11.1 (0.1.1) and 13.1 (0.1.2).
"""Smoke check against the installed AgentWiz wheel (steps S1-S10). Run with the environment's
`python -I` from a scratch directory. Every step checks behaviour; none reads a file's contents.
The launch's default workspace (./workspaces/default) is validated by AgentWiz itself and checked
through /api/state."""
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def check(condition, message):
    if not condition:
        sys.exit(f"smoke FAILED: {message}")


# S1: clear proxies; both packages import from this environment
for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
    os.environ.pop(name, None)
    os.environ.pop(name.lower(), None)
os.environ.pop("OPENROUTER_API_KEY", None)

import agentwiz  # noqa: E402
import modelspell  # noqa: E402

prefix = Path(sys.prefix).resolve()
for mod in (agentwiz, modelspell):
    check(prefix in Path(mod.__file__).resolve().parents, f"{mod.__name__} imported from {mod.__file__}")

# S2: --version from the console script and from python -m; modelSpell is the pinned 0.2.1
check(modelspell.__version__ == "0.2.1", f"modelSpell {modelspell.__version__} installed, expected 0.2.1")
expected = f"AgentWiz {agentwiz.__version__} (modelSpell {modelspell.__version__})"
bindir = Path(sys.executable).parent
exe = bindir / ("agentwiz.exe" if os.name == "nt" else "agentwiz")
for cmd in ([str(exe), "--version"], [sys.executable, "-m", "agentwiz", "--version"]):
    run = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    check(run.returncode == 0 and run.stdout.strip() == expected, f"{cmd}: {run.returncode} {run.stdout!r}")


# S3: inline OpenAI-compatible stub streaming "pong" in two deltas
def chunk(content=None, finish=None):
    d = {} if content is None else {"content": content}
    return b"data: " + json.dumps({"choices": [{"delta": d, "finish_reason": finish}]}).encode() + b"\n\n"


received = []


class Stub(BaseHTTPRequestHandler):
    def do_POST(self):
        received.append(json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}"))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for c in (chunk("po"), chunk("ng"), chunk(finish="stop"), b"data: [DONE]\n\n"):
            self.wfile.write(c)
            self.wfile.flush()

    def log_message(self, *a):
        pass


stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
threading.Thread(target=stub.serve_forever, daemon=True).start()
stub_url = f"http://127.0.0.1:{stub.server_address[1]}/v1"

# S4: launch the installed command; page served with the token, refused without
proc = subprocess.Popen([str(exe), "--no-browser"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
try:
    lines = []
    reader = threading.Thread(target=lambda: lines.append(proc.stdout.readline()), daemon=True)
    reader.start()
    reader.join(15)
    check(lines and lines[0].startswith("[AgentWiz] http://127.0.0.1:"), f"no address printed: {lines}")
    address = lines[0].split(" ", 1)[1].strip()
    root, token = address.split("/?t=", 1)
    page = urllib.request.urlopen(address, timeout=10)
    check(page.status == 200 and page.headers["Content-Type"].startswith("text/html"), "page not served")
    try:
        urllib.request.urlopen(root + "/", timeout=10)
        check(False, "page served without the token")
    except urllib.error.HTTPError as e:
        check(e.code == 403, f"tokenless GET returned {e.code}")

    def api(path, body=None):
        req = urllib.request.Request(root + path, method="GET" if body is None else "POST",
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-Session-Token": token, "Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=30)

    # S4 (0.1.1): the default workspace was created here, and AgentWiz found its files valid
    initial = json.loads(api("/api/state").read())
    workspace = initial["workspace"]
    expected_path = str((Path.cwd() / "workspaces" / "default").resolve())
    check(workspace["path"] == expected_path, f"workspace at {workspace['path']}, expected {expected_path}")
    check(workspace["read_only"] is False and workspace["problems"] == [], f"workspace not usable: {workspace}")
    check(Path(expected_path).is_dir(), "default workspace folder missing")
    defaults = Path.cwd() / "model_profiles"
    check(defaults.is_dir(), "default profile folder missing")
    check(initial["profiles"]["defaults_path"] == str(defaults.resolve() / "profiles.json"),
          "unexpected defaults path")
    check(initial["profiles"]["names"] == [] and initial["profiles"]["active"] is None,
          "fresh workspace has unexpected profiles")
    # S4 (0.1.2): the MCP library and task settings are usable; the retired settings are gone
    check(initial["mcp"]["read_only"] is False and initial["mcp"]["servers"] == {}, f"MCP library: {initial['mcp']}")
    check(initial["task"]["read_only"] is False, f"task settings: {initial['task']}")
    check("reasoning" not in initial and "max_tokens" not in initial, "retired settings still in /api/state")

    # S5: configure the stub with the current revision
    rev = json.loads(api("/api/state").read())["revision"]
    state = json.loads(api("/api/config", {"revision": rev, "kind": "openai_compat", "base_url": stub_url,
                                           "model": "m"}).read())
    check(state["revision"] == rev + 1, f"revision did not advance: {state}")

    # S6: one completion
    events = [json.loads(line) for line in api("/api/complete", {
        "prompt": "ping", "revision": state["revision"], "task": {"temperature": 0.5, "max_tokens": 64}})
              .read().splitlines() if line]
    text = "".join(e["text"] for e in events if e["type"] == "text")
    end = events[-1]
    check(text == "pong" and end["type"] == "result" and end["result"]["text"] == "pong"
          and end["outcome"] == "complete", f"unexpected events: {events}")
    check(received and received[-1].get("temperature") == 0.5 and received[-1].get("max_tokens") == 64,
          f"task settings did not arrive under Chat Completions' names: {received[-1:]}")

    # S9 (0.1.2): an attachment with a non-ASCII name is read and previewed with its provenance line
    from urllib.parse import quote
    upload = urllib.request.Request(root + "/api/attachments/extract", method="POST", data=b"# Notes\nhello\n",
                                    headers={"X-Session-Token": token, "Content-Type": "application/octet-stream",
                                             "X-Filename": quote("résumé.md", safe="")})
    extracted = json.loads(urllib.request.urlopen(upload, timeout=30).read())
    check(extracted["insertion"]["text"].startswith('[file "résumé.md"]\n# Notes'), f"upload: {extracted}")

    # S10 (0.1.2): an inline MCP server speaking 2026-07-28: save, Load tools, call echo
    class FakeMCP(BaseHTTPRequestHandler):
        def do_POST(self):
            msg = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            if self.headers.get("MCP-Protocol-Version") != "2026-07-28":
                result, status = {"jsonrpc": "2.0", "id": msg.get("id"),
                                  "error": {"code": -32600, "message": "unsupported"}}, 400
            elif msg["method"] == "server/discover":
                result, status = {"jsonrpc": "2.0", "id": msg["id"], "result": {"supportedVersions": ["2026-07-28"]}}, 200
            elif msg["method"] == "tools/list":
                result, status = {"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [{
                    "name": "echo", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]}}, 200
            else:
                result, status = {"jsonrpc": "2.0", "id": msg["id"], "result": {
                    "content": [{"type": "text", "text": msg["params"]["arguments"].get("text", "")}]}}, 200
            data = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    mcp = ThreadingHTTPServer(("127.0.0.1", 0), FakeMCP)
    threading.Thread(target=mcp.serve_forever, daemon=True).start()
    try:
        mcp_rev = json.loads(api("/api/state").read())["mcp"]["revision"]
        saved = json.loads(api("/api/mcp/servers", {"revision": mcp_rev, "servers": {
            "smoke": {"url": f"http://127.0.0.1:{mcp.server_address[1]}/mcp"}}}).read())
        mcp_rev = saved["mcp"]["revision"]
        tools = json.loads(api("/api/mcp/tools", {"server": "smoke", "revision": mcp_rev}).read())
        check(tools["era"] == "modern" and [t["name"] for t in tools["tools"]] == ["echo"], f"tools: {tools}")
        lines = [json.loads(line) for line in api("/api/mcp/call", {
            "server": "smoke", "tool": "echo", "arguments_text": '{"text":"pong"}', "revision": mcp_rev})
                 .read().splitlines() if line]
        final = lines[-1]
        check(final["type"] == "final" and final["outcome"] == "ok" and final["era"] == "modern"
              and final["result"]["text"] == "pong", f"tool call: {lines}")
    finally:
        mcp.shutdown()

    # S8: a second AgentWiz on the same workspace is refused with exit status 3
    second = subprocess.run([str(exe), "--no-browser"], capture_output=True, text=True, timeout=15)
    check(second.returncode == 3, f"second launch on the same workspace exited {second.returncode}")

    # S7: Ctrl-C exits cleanly
    proc.send_signal(signal.SIGINT)
    deadline = time.monotonic() + 5
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    check(proc.returncode == 0, f"exit status after SIGINT: {proc.returncode}")
finally:
    if proc.poll() is None:
        proc.kill()
    stub.shutdown()

print(f"smoke ok: {expected} from {agentwiz.__file__}")
