# Adapted from modelSpell scripts/smoke_installed.py at commit 1759c6e3c28a637731434a73c46088048583e358
# (MIT, Copyright (c) 2026 Alex Wong), per AgentWiz techspec 11.2 (0.1.0), 11.1 (0.1.1), 13.1 (0.1.2), 16.1 (0.1.3)
# and 18.1 (0.1.4).
"""Smoke check against the installed AgentWiz wheel (steps S1-S15). Run with the environment's
`python -I` from a scratch directory. Every step checks behaviour; none reads a file's contents.
The launch's default workspace (./workspaces/default) is validated by AgentWiz itself and checked
through /api/state."""
import json
import os
import select
import signal
import socket
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

# S2: --version from the console script and from python -m; modelSpell is the pinned 0.3.0
check(modelspell.__version__ == "0.3.0", f"modelSpell {modelspell.__version__} installed, expected 0.3.0")
expected = f"AgentWiz {agentwiz.__version__} (modelSpell {modelspell.__version__})"
bindir = Path(sys.executable).parent
exe = bindir / ("agentwiz.exe" if os.name == "nt" else "agentwiz")
for cmd in ([str(exe), "--version"], [sys.executable, "-m", "agentwiz", "--version"]):
    run = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    check(run.returncode == 0 and run.stdout.strip() == expected, f"{cmd}: {run.returncode} {run.stdout!r}")


# S3: inline OpenAI-compatible stub streaming "pong" in two deltas. The model "silent" (S12) accepts the
# connection, reads the request and never sends response headers; it ends when the client disconnects.
def chunk(content=None, finish=None):
    d = {} if content is None else {"content": content}
    return b"data: " + json.dumps({"choices": [{"delta": d, "finish_reason": finish}]}).encode() + b"\n\n"


received = []
silent_reached, silent_closed = threading.Event(), threading.Event()


class Stub(BaseHTTPRequestHandler):
    def do_POST(self):
        received.append(json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}"))
        if received[-1].get("model") == "silent":
            silent_reached.set()
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:       # until the client goes (or a minute passes)
                if select.select([self.connection], [], [], 0.05)[0]:
                    try:
                        gone = not self.connection.recv(1, socket.MSG_PEEK)
                    except OSError:
                        gone = True
                    if gone:
                        silent_closed.set()
                        return
            return
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
from agentwiz.server_config import initialize
initialize(Path("config/model-servers.ini"))
Path("config/model-servers.ini").write_text(
    f"[lmstudio]\nbase_url = {stub_url}\n[openrouter]\nbase_url = https://openrouter.ai/api/v1\n"
    f"[openai_compat]\nbase_url = {stub_url}\n", encoding="utf-8")

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
    # The packaged contract editor script (overhaul spec 15.4): served from the installed wheel, token required,
    # as JavaScript, exactly as the page loads it.
    script = urllib.request.urlopen(root + "/assets/contracts.js?t=" + token, timeout=10)
    check(script.status == 200 and script.headers["Content-Type"].startswith("text/javascript")
          and b"window.AgentWizContracts" in script.read(), "contracts.js not served from the installed wheel")
    try:
        urllib.request.urlopen(root + "/assets/contracts.js", timeout=10)
        check(False, "contracts.js served without the token")
    except urllib.error.HTTPError as e:
        check(e.code == 403, f"tokenless script GET returned {e.code}")

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
    # S4 (0.1.4): no roles yet, and the script budget is four
    check(initial["roles"]["map"] == {} and initial["scripts"]["limit"] == 4,
          f"roles/scripts: {initial['roles']} {initial['scripts']}")
    # S4 (0.1.3): contracts and history are present, empty and off
    check(initial["contracts"]["items"] == [] and initial["contracts"]["format_version"] == 2
          and initial["contracts"]["validation"]["available"] is True, f"contracts: {initial['contracts']}")
    check(initial["history"]["save_runs"] is False and initial["history"]["read_only"] is False,
          f"history: {initial['history']}")

    # S5: configure the stub with the current revision
    rev = json.loads(api("/api/state").read())["revision"]
    state = json.loads(api("/api/config", {"revision": rev, "kind": "openai_compat",
                                           "model": "m"}).read())
    check(state["revision"] == rev + 1, f"revision did not advance: {state}")

    # S6: one completion; the stream is `started`, then text, then one `final` carrying the record
    events = [json.loads(line) for line in api("/api/complete", {
        "source": "chat", "prompt": "ping", "system": "", "revision": state["revision"],
        "task": {"temperature": 0.5, "max_tokens": 64}}).read().splitlines() if line]
    text = "".join(e["text"] for e in events if e["type"] == "text")
    end = events[-1]
    check(events[0]["type"] == "started" and [e["type"] for e in events[1:-1]] == ["text"] * len(events[1:-1]),
          f"unexpected event order: {[e['type'] for e in events]}")
    check(text == "pong" and end["type"] == "final" and end["outcome"] == "complete"
          and end["record"]["result"]["text"] == "pong" and end["record"]["run_id"] == events[0]["run_id"],
          f"unexpected events: {events}")
    check(received and received[-1].get("temperature") == 0.5 and received[-1].get("max_tokens") == 64,
          f"task settings did not arrive under Chat Completions' names: {received[-1:]}")

    # Draft contract v2 operations must be present in the installed wheel, including
    # its required validator and exact-number path; they must not require a model.
    draft = '{"format":"json","schema":{"type":"number","minimum":9007199254740993}}'
    projected = json.loads(api('/api/contracts/project', {
        'format_version': 2, 'definition_text': draft}).read())
    check(projected['format_version'] == 2 and projected['digest'].startswith('sha256:'),
          f'contract projection: {projected}')
    for sample, outcome in [('9007199254740993', 'pass'), ('9007199254740992', 'fail')]:
        checked = json.loads(api('/api/contracts/check', {
            'format_version': 2, 'definition_text': draft, 'value_text': sample}).read())
        check(checked['check']['overall'] == outcome, f'exact contract check: {checked}')
    edited = json.loads(api('/api/contracts/edit', {
        'format_version': 2, 'definition_text': draft,
        'edit': {'op': 'set_description', 'path': None, 'description': 'Smoke value'}}).read())
    check(edited['description'] == 'Smoke value' and edited['digest'] != projected['digest'],
          f'contract edit: {edited}')

    # S11 (0.1.3): save a text contract, then a Checked request against it passes.
    saved_contract = json.loads(api("/api/contracts/save", {
        "format_version": 2, "name": "smoke", "family": "General", "expected_file_token": None,
        "definition_text": '{"format": "text", "contains": ["pong"]}', "label": "Smoke"}).read())
    check(saved_contract["revision"] == 1 and saved_contract["digest"].startswith("sha256:"),
          f"contract save: {saved_contract}")
    ref = {"name": "smoke", "revision": saved_contract["revision"], "digest": saved_contract["digest"]}
    # The packaged synthesis prompt is read before the (unassigned) write_contract role is resolved, so a missing
    # prompt would be an internal error rather than this setup refusal.
    try:
        api("/api/contracts/synthesis/plan", {"format_version": 2, "task_description": "Classify a message.",
                                              "output_name": "label", "definition_text": None,
                                              "role": "write_contract"})
        check(False, "synthesis plan without an assigned role was accepted")
    except urllib.error.HTTPError as exc:
        refusal = json.loads(exc.read())
        check(exc.code == 409 and refusal.get("code") == "role_unresolved", f"synthesis setup refusal: {refusal}")
    events = [json.loads(line) for line in api("/api/complete", {
        "source": "checked_request", "prompt": "ping", "system": "", "revision": state["revision"],
        "contract": ref}).read().splitlines() if line]
    end = events[-1]
    check(end["type"] == "final" and end["record"]["check"]["overall"] == "pass", f"checked request: {events}")

    # S12 (0.1.3): Stop reaches a request that is still waiting for response headers, from the installed wheel:
    # the handle modelSpell 0.3.0 provides works here. The stub reads the request, so it was sent.
    state = json.loads(api("/api/config", {"revision": state["revision"], "kind": "openai_compat",
                                           "model": "silent"}).read())
    stream = api("/api/complete", {"source": "chat", "prompt": "ping", "system": "", "revision": state["revision"]})
    started = json.loads(stream.readline())
    check(started["type"] == "started", f"first event: {started}")
    check(silent_reached.wait(10), "the stub never saw the request")
    asked = time.monotonic()
    answer = json.loads(api("/api/complete/stop", {"attempt_id": started["attempt_id"]}).read())
    check(answer == {"status": "stopping"}, f"stop answered {answer}")
    rest = [json.loads(line) for line in stream.read().splitlines() if line]
    final = rest[-1]
    check(time.monotonic() - asked < 5, "the stopped request took more than 5 seconds to end")
    check(final["type"] == "final" and final["outcome"] == "stopped" and final["record"]["stop_mode"] == "immediate"
          and final["record"]["sent"] is True, f"stopped request: {final}")
    check(silent_closed.wait(5), "the stub never saw the client disconnect")

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

    # S13 (0.1.4): a script block written into the running workspace is found without a restart and runs from the
    # wheel: started, then final `complete`, the output equal to the input.
    block_dir = Path(expected_path) / "blocks" / "echo_script"
    (block_dir / "scripts").mkdir(parents=True)
    (block_dir / "block.json").write_text(json.dumps({
        "format_version": 3, "name": "echo_script", "inputs": {"a": {"contract": {"format": "text"}}},
        "outputs": {"x": {"contract": {"format": "text"}}, "y": {"contract": {"format": "json"}}},
        "steps": [{"id": "run", "kind": "script", "file": "scripts/go.py", "timeout_s": 20}]}))
    (block_dir / "scripts" / "go.py").write_text(
        "import json, sys\nd = json.load(sys.stdin)\nprint(json.dumps({'x': d['a'], 'y': {'n': 1}}))\n")
    listing = json.loads(api("/api/blocks").read())
    row = next((b for b in listing["blocks"] if b["name"] == "echo_script"), None)
    check(row is not None and row["problem"] is None, f"block listing: {listing}")
    events = [json.loads(line) for line in api("/api/blocks/run", {
        "name": "echo_script", "revision": row["revision"], "inputs": {"a": "pong"}}).read().splitlines() if line]
    check(events[0]["type"] == "started" and events[-1]["type"] == "final"
          and events[-1]["outcome"] == "complete"
          and events[-1]["result"]["outputs"]["x"] == {"format": "text", "text": "pong"}, f"script block: {events}")

    # S14 (0.1.4): the tutorial child ships its example blocks and scripts in the wheel
    tutorial = json.loads(api("/api/tutorial/start", {}).read())
    child_root, child_token = tutorial["url"].split("/?t=", 1)

    def child_api(path, body=None):
        req = urllib.request.Request(child_root + path, method="GET" if body is None else "POST",
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-Session-Token": child_token, "Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=30)

    try:
        child_listing = json.loads(child_api("/api/blocks").read())
        names = sorted(b["name"] for b in child_listing["blocks"])
        check(names == ["echo_numbers", "extract_fields", "wait_for_stop", "word_count"]
              and all(b["problem"] is None for b in child_listing["blocks"]), f"tutorial blocks: {child_listing}")
        counter = next(b for b in child_listing["blocks"] if b["name"] == "word_count")
        events = [json.loads(line) for line in child_api("/api/blocks/run", {
            "name": "word_count", "revision": counter["revision"],
            "inputs": {"text": "The quick brown fox jumps over the lazy dog"}}).read().splitlines() if line]
        check(events[-1]["type"] == "final" and events[-1]["outcome"] == "complete"
              and events[-1]["result"]["outputs"]["words"]["text"] == "9", f"word_count: {events}")
    finally:
        json.loads(child_api("/api/tutorial/exit", {}).read())

    # S15: installed Workshop creates and runs a captured starter, including packaged runtime/license.
    def workshop(path, body=None):
        payload = None if body is None else {'format_version': 1, **body}
        return json.loads(api('/api/workshop/' + path, payload).read())

    starters = workshop('starters')
    check(len(starters['starters']) == 9, 'installed Workshop starter inventory')
    draft = workshop('preview', {'starter': 'count_text', 'key': 'installed_counter'})['draft']
    save = {'draft': draft, 'expected_identity': None}
    plan = workshop('plans', {'action': 'save', 'request': save})
    saved = workshop('save', {**save, 'expected_plan': plan['expected_plan'],
        'confirmations': [{'kind': c['kind'], 'digest': c['digest']} for c in plan['required_confirmations']]})
    check(bool(saved['durable_id']), 'installed Workshop publication')
    start = {'key': 'installed_counter', 'inputs': {'text': 'one two three'},
             'ticket': workshop('tickets', {})['ticket']}
    plan = workshop('plans', {'action': 'test', 'request': start})
    start.update(expected_plan=plan['expected_plan'], confirmations=[])
    operation = workshop('test', start)
    deadline = time.monotonic() + 10
    batch = workshop('batches/' + operation['batch_id'])
    while batch['state'] == 'running' and time.monotonic() < deadline:
        time.sleep(.02)
        batch = workshop('batches/' + operation['batch_id'])
    check(batch['state'] == 'complete' and batch['rows'][0]['outputs']['out']['text'] == '3',
          'installed Workshop frozen script trial')
    duplicate = workshop('test', start)
    check(duplicate['operation_id'] == operation['operation_id'], 'installed Workshop duplicate Start')

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
