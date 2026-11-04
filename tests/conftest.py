# Reply, TestServer, PROXY_VARS, _clean_env, make_server, server and KEY are copied from modelSpell
# tests/conftest.py at commit 1759c6e3c28a637731434a73c46088048583e358 (MIT, Copyright (c) 2026
# Alex Wong), with the additions of AgentWiz techspec 13.2: Reply.after_release,
# TestServer.wait_holding, and close() ending post-release waits. The rest is AgentWiz's.
"""Fake model backend, AgentWiz server fixtures and HTTP helpers. All fixtures are inline.

Every AgentWiz server gets a workspace under the test's tmp_path; tests never write outside it."""
from __future__ import annotations

import itertools
import json
import os
import select
import socket
import threading
import time
import urllib.error
import urllib.request
from types import MappingProxyType
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agentwiz.ui.server import UIServer
from agentwiz.workspace import open_workspace
from agentwiz.server_config import ServerSettings

def pytest_collection_modifyitems(config, items):
    """Tests marked needs_checks skip, with a reason, only where the checks extra is really absent: CI's
    Python 3.10 jobs (techspec 0.1.3, 18.1). Everywhere else they run; the no-extra path is tested by
    making checks.schema_support() report the extra as missing."""
    from agentwiz import checks
    if checks.schema_support()[0]:
        return
    skip = pytest.mark.skip(reason="the checks extra is not installed")
    for item in items:
        if "needs_checks" in item.keywords:
            item.add_marker(skip)


PROXY_VARS = [v for n in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY") for v in (n, n.lower())]
KEY = "sk-SENTINEL-1234567890"


# --- fake backend (copied from modelSpell, with techspec 13.2 additions) --------------------------
@dataclass
class Reply:
    status: int = 200
    body: bytes | list[bytes] = b""   # list => sent chunk by chunk with flush(), no Content-Length
    headers: dict = field(default_factory=dict)
    delay: float = 0.0                # before the first byte
    chunk_delay: float = 0.0
    hold: bool = False                # keep the body open after the chunks until release is set
    after_release: list[bytes] = field(default_factory=list)  # sent once release is set; then wait for the client
    hold_headers: bool = False        # read the request, then send nothing (not even the status line) until
                                      # release is set or the client disconnects (techspec 0.1.3, 18.2)


class TestServer:
    __test__ = False  # not a pytest test class

    def __init__(self):
        self.requests: list[dict] = []
        self.replies: list[Reply] = []
        self.release = threading.Event()
        self.reached = threading.Event()   # set once a request has been read (hold_headers waits after it)
        self.client_closed = threading.Event()
        self._closing = threading.Event()
        self._holding = 0
        self._lock = threading.Lock()
        srv = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _handle(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw) if raw else None
                except ValueError:
                    body = raw
                srv.requests.append({"method": self.command, "path": self.path, "body": body,
                                     "headers": {k.lower(): v for k, v in self.headers.items()}})
                r = srv.replies.pop(0) if len(srv.replies) > 1 else (srv.replies[0] if srv.replies else Reply())
                srv.reached.set()
                if r.hold_headers:
                    # Ported in spirit from modelSpell 0.3.0's tests/conftest.py (hold before the status line).
                    with srv._lock:
                        srv._holding += 1
                    try:
                        while not srv.release.is_set():
                            if self._peer_closed():
                                srv.client_closed.set()
                                return
                    finally:
                        with srv._lock:
                            srv._holding -= 1
                time.sleep(r.delay)
                try:
                    self.send_response(r.status)
                    for k, v in r.headers.items():
                        self.send_header(k, v)
                    chunks = r.body if isinstance(r.body, list) else [r.body]
                    if not isinstance(r.body, list) and not r.hold:
                        self.send_header("Content-Length", str(len(r.body)))
                    self.end_headers()
                    for c in chunks:
                        self.wfile.write(c)
                        self.wfile.flush()
                        time.sleep(r.chunk_delay)
                    if r.hold:
                        self._hold(r)
                except OSError:
                    srv.client_closed.set()

            def _peer_closed(self) -> bool:
                sock = self.connection
                if select.select([sock], [], [], 0.05)[0]:
                    try:
                        data = sock.recv(1, socket.MSG_PEEK)
                    except OSError:
                        data = b""
                    return not data
                return False

            def _hold(self, r: Reply):
                with srv._lock:
                    srv._holding += 1
                try:
                    while not srv.release.is_set():
                        if self._peer_closed():
                            srv.client_closed.set()
                            return
                finally:
                    with srv._lock:
                        srv._holding -= 1
                if not r.after_release:
                    return
                for c in r.after_release:
                    self.wfile.write(c)
                    self.wfile.flush()
                    time.sleep(r.chunk_delay)
                while not srv._closing.is_set():
                    if self._peer_closed():
                        srv.client_closed.set()
                        return

            do_GET = do_POST = _handle

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, args=(0.02,), daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def reply(self, *replies: Reply) -> "TestServer":
        self.replies = list(replies)
        return self

    def wait_holding(self, n: int, timeout: float = 10.0) -> bool:
        return wait_until(lambda: self._holding >= n, timeout)

    def close(self):
        self.release.set()
        self._closing.set()
        self.httpd.shutdown()
        self.httpd.server_close()



@pytest.fixture(autouse=True)
def _clean_env(request, monkeypatch):
    if request.node.get_closest_marker("live"):
        return
    for var in PROXY_VARS + ["OPENROUTER_API_KEY"]:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def make_server():
    servers = []

    def make() -> TestServer:
        servers.append(TestServer())
        return servers[-1]

    yield make
    for s in servers:
        s.close()


@pytest.fixture
def server(make_server) -> TestServer:
    return make_server()


# --- inline SSE builders ----------------------------------------------------------------------
DONE = b"data: [DONE]\n\n"


def sse_event(json_text: str) -> bytes:
    """One SSE data event from literal JSON text (so tests can embed NaN and 1e400)."""
    return b"data: " + json_text.encode("utf-8") + b"\n\n"


def delta(content=None, reasoning=None, finish=None, usage=None) -> bytes:
    """One Chat Completions streaming chunk."""
    d = {}
    if content is not None:
        d["content"] = content
    if reasoning is not None:
        d["reasoning_content"] = reasoning
    chunk = {"choices": [{"index": 0, "delta": d, "finish_reason": finish}]}
    if usage is not None:
        chunk["usage"] = usage
    return sse_event(json.dumps(chunk))


def answer(text: str = "ok", **kw) -> Reply:
    """A streamed answer: one content delta, finish 'stop', [DONE]."""
    return Reply(body=[delta(text), delta(finish="stop"), DONE], headers={"Content-Type": "text/event-stream"}, **kw)
def block_answer(**values) -> Reply:
    """A model-block reply: one named JSON envelope. `block_answer(out="OK")` answers the output `out`."""
    from agentwiz import jsonio as _exact
    return answer(_exact.encode_exact(values if values else {"out": "ok"}))





# --- LM Studio replies (techspec 0.1.2, Section 15.2) -------------------------------------------
# Shapes adapted from modelSpell's tests/fixtures (LM Studio native chat, Responses and /api/v1/models,
# recorded against LM Studio 0.3.x) at release v0.2.1 (MIT, Copyright (c) 2026 Alex Wong).
def lmstudio_models(model_id: str, reasoning=None) -> Reply:
    """GET /api/v1/models with one LLM; `reasoning` is allowed_options (None: no reasoning capability)."""
    caps = {"trained_for_tool_use": False}
    if reasoning is not None:
        caps["reasoning"] = {"allowed_options": list(reasoning), "default": reasoning[0]}
    row = {"type": "llm", "key": model_id, "display_name": model_id, "max_context_length": 4096,
           "loaded_instances": [], "capabilities": caps}
    return Reply(body=json.dumps({"models": [row]}).encode(), headers={"Content-Type": "application/json"})


def native_answer(text: str = "ok") -> Reply:
    """POST /api/v1/chat streamed: one message delta, then chat.end."""
    end = {"type": "chat.end", "result": {"output": [{"type": "message", "content": text}],
                                          "stats": {"input_tokens": 1, "total_output_tokens": 1}}}
    return Reply(body=[sse_event(json.dumps({"type": "message.delta", "content": text})),
                       sse_event(json.dumps(end))], headers={"Content-Type": "text/event-stream"})


def responses_answer(text: str = "ok") -> Reply:
    """POST /v1/responses streamed: one output_text delta, then response.completed."""
    done = {"type": "response.completed", "response": {
        "id": "resp_1", "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}}
    return Reply(body=[sse_event(json.dumps({"type": "response.output_text.delta", "delta": text})),
                       sse_event(json.dumps(done))], headers={"Content-Type": "text/event-stream"})


# --- AgentWiz server fixtures ---------------------------------------------------------------
@pytest.fixture
def make_ui(tmp_path):
    """make_ui(workspace=None, **kwargs): a running UIServer. Without a workspace, one is opened
    under tmp_path with a unique name; teardown shuts every server down, then closes the workspaces
    this fixture opened."""
    servers, opened, names = [], [], itertools.count(1)

    def make(workspace=None, **kwargs) -> UIServer:
        if workspace is None:
            workspace = open_workspace(tmp_path / "ws", f"w{next(names)}")
            opened.append(workspace)
        kwargs.setdefault("profiles_dir", tmp_path / "model_profiles")
        kwargs.setdefault("mcp_servers_dir", kwargs.pop("mcp_dir", tmp_path / "mcp_servers"))
        kwargs.setdefault("servers", {
            "lmstudio": ServerSettings("http://localhost:1234/v1"),
            "openrouter": ServerSettings("https://openrouter.ai/api/v1"),
            "openai_compat": ServerSettings("http://127.0.0.1:9/v1"),
        })
        srv = UIServer(0, workspace=workspace, **kwargs)
        srv.transcript = []  # every response body the helpers received, for "never echoed" checks
        threading.Thread(target=srv.serve_forever, args=(0.02,), daemon=True).start()
        servers.append(srv)
        return srv

    make.opened = opened
    yield make
    for srv in servers:
        srv.shutdown()
        srv.server_close()
        srv.close_workspace()
    for ws in opened:
        ws.close()


@pytest.fixture
def ui(make_ui) -> UIServer:
    return make_ui()


@pytest.fixture
def restart(make_ui):
    """restart(ui) -> UIServer: shut `ui` down, close its workspace, reopen the same folder and name,
    and return a new server on it (closed at teardown)."""
    def again(old: UIServer) -> UIServer:
        old.shutdown()
        old.server_close()
        ws = old.workspace
        old.close_workspace()
        reopened = open_workspace(ws.path.parent, ws.name)
        make_ui.opened.append(reopened)
        return make_ui(workspace=reopened, profiles_dir=old.state.library.path,
                       mcp_servers_dir=old.state.mcp_library.folder, servers=old.state.servers)

    return again


def write_ws_file(path: Path, text: str) -> None:
    """Write fixture text into a workspace before it is opened (test input, not an assertion)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def file_stat(path: Path) -> tuple:
    """What "left untouched" compares: inode, size and modification time."""
    st = os.stat(path)
    return st.st_ino, st.st_size, st.st_mtime_ns


# --- HTTP helpers -------------------------------------------------------------------------------
def _reject_constant(name):
    raise AssertionError(f"AgentWiz emitted non-standard JSON constant {name}")


def strict_loads(text):
    return json.loads(text, parse_constant=_reject_constant)


def base(ui) -> str:
    return f"http://127.0.0.1:{ui.server_address[1]}"


class HTTPFailure(Exception):
    def __init__(self, status: int, body: dict):
        super().__init__(f"HTTP {status}: {body}")
        self.status, self.body = status, body


def request(ui, path, body=None, token=True, raw=False, timeout=10.0):
    """One request to AgentWiz. `timeout` bounds each socket read, so a stream may take longer overall."""
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Session-Token"] = ui.token
    req = urllib.request.Request(base(ui) + path, data=None if body is None else json.dumps(body).encode(),
                                 headers=headers, method="GET" if body is None else "POST")
    try:
        resp = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        data = e.read()
        getattr(ui, "transcript", []).append(data)
        raise HTTPFailure(e.code, strict_loads(data)) from None
    if raw:
        return resp
    data = resp.read()
    getattr(ui, "transcript", []).append(data)
    return strict_loads(data)


def status_of(fn) -> int:
    try:
        fn()
    except HTTPFailure as e:
        return e.status
    except urllib.error.HTTPError as e:
        return e.code
    return 200


def failure_of(fn) -> HTTPFailure:
    try:
        fn()
    except HTTPFailure as e:
        return e
    raise AssertionError("request unexpectedly succeeded")


def get_state(ui) -> dict:
    return request(ui, "/api/state")


def save_profile(ui, name, revision=None, **settings) -> dict:
    """POST /api/profiles/save with the current configuration revision unless one is given."""
    configure_server(ui, settings)
    rev = get_state(ui)["revision"] if revision is None else revision
    return request(ui, "/api/profiles/save", {"name": name, "revision": rev, **settings})


def save_roles(ui, roles, revision=None) -> dict:
    """POST /api/roles/save with the current roles revision unless one is given."""
    rev = get_state(ui)["roles"]["revision"] if revision is None else revision
    return request(ui, "/api/roles/save", {"roles": roles, "revision": rev})


def write_block(ui_or_path, name, block, files=None) -> Path:
    """Write `blocks/<name>/block.json` (exact encoding, so numbers survive) and the given files
    ({relative path: text}) into a workspace. `ui_or_path` is a running server or a workspace folder.
    `block` is a dict, or text written as it is (to test damaged files)."""
    from agentwiz import jsonio
    root = getattr(getattr(ui_or_path, "workspace", None), "path", ui_or_path)
    folder = Path(root) / "blocks" / name
    folder.mkdir(parents=True, exist_ok=True)
    text = block if isinstance(block, str) else jsonio.encode_exact(block, indent=2)
    (folder / "block.json").write_bytes(text.encode("utf-8"))
    for rel, content in (files or {}).items():
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    return folder


def configure_server(ui, fields):
    """Test-only machine configuration setup, never a browser/API override."""
    if "base_url" not in fields and "api_key" not in fields:
        return
    kind = fields.get("kind", get_state(ui)["kind"])
    url = fields.pop("base_url", ui.state.servers[kind].base_url) or {
        "lmstudio": "http://localhost:1234/v1", "openrouter": "https://openrouter.ai/api/v1",
    }.get(kind, ui.state.servers[kind].base_url)
    key = fields.pop("api_key", None)
    servers = dict(ui.state.servers)
    previous = servers[kind]
    if key is None:
        key = previous.api_key if previous.base_url == url.rstrip("/") else None
    servers[kind] = ServerSettings(url.rstrip("/"), key or None)
    ui.servers = MappingProxyType(servers)
    ui.state.servers = ui.servers
    if key:
        ui.state._credentials.add(key)


def configure(ui, backend, kind="openai_compat", **fields) -> dict:
    body = {"kind": kind, "base_url": backend.url + "/v1", "model": "m", **fields}
    key = body.pop("api_key", None) if kind == "openrouter" else None
    configure_server(ui, body)
    if key is not None:
        request(ui, "/api/openrouter-key", {"revision": get_state(ui)["revision"], "api_key": key})
    body.setdefault("revision", get_state(ui)["revision"])
    return request(ui, "/api/config", body)


def complete(ui, prompt, system="", revision=None, raw=False, task=None, source="chat", contract=None):
    """POST /api/complete. `contract` is (name, revision) and goes with source="checked_request"."""
    rev = get_state(ui)["revision"] if revision is None else revision
    body = {"source": source, "prompt": prompt, "system": system, "revision": rev}
    if task is not None:
        body["task"] = task
    if contract is not None:
        body["contract"] = {"name": contract[0], "revision": contract[1]}
    resp = request(ui, "/api/complete", body, raw=True)
    return resp if raw else events(resp, ui)


def final_of(evs: list[dict]) -> dict:
    """The stream's `final` event; the stream must end with it, or with it followed by a `save` event."""
    finals = [e for e in evs if e["type"] == "final"]
    assert len(finals) == 1, evs
    return finals[0]


def record_of(evs: list[dict]) -> dict:
    return final_of(evs)["record"]


def iter_events(resp):
    """Events as they arrive, one per line (for tests that act while the stream is open)."""
    for line in iter(resp.readline, b""):
        if line.strip():
            yield strict_loads(line)


def read_until(it, kind: str) -> list[dict]:
    """Consume `it` up to and including the first event of type `kind`."""
    seen = []
    for ev in it:
        seen.append(ev)
        if ev["type"] == kind:
            return seen
    raise AssertionError(f"the stream ended before a {kind!r} event: {seen}")


def stop_attempt(ui, attempt_id):
    return request(ui, "/api/complete/stop", {"attempt_id": attempt_id})


def poll_attempt(ui, attempt_id):
    return request(ui, f"/api/attempts/{attempt_id}")


def repair_attempt(ui, attempt_id, raw=False):
    resp = request(ui, "/api/repair", {"attempt_id": attempt_id}, raw=True)
    return resp if raw else events(resp, ui)


def json_answer(text: str, fenced: bool = False, **kw) -> Reply:
    """A fake backend answer carrying JSON text, bare or in a fenced code block."""
    return answer("```json\n" + text + "\n```" if fenced else text, **kw)


def history_dir(ui) -> Path:
    return ui.workspace.path / "runs"


def run_folders(ui) -> list[Path]:
    root = history_dir(ui)
    return sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


def read_run_files(ui, run_id: str) -> dict:
    """What the server wrote for a run, read with the exact reader (X1): {"run": ..., "attempts": {n: ...}}."""
    from agentwiz import jsonio
    folder = next(p for p in run_folders(ui) if p.name.endswith("-" + run_id))
    out = {"folder": folder, "run": jsonio.decode_file_exact((folder / "run.json").read_bytes()), "attempts": {}}
    for f in sorted(folder.glob("request-*.json")):
        out["attempts"][int(f.stem.split("-")[1])] = jsonio.decode_file_exact(f.read_bytes())
    return out


def switch_history(ui, on: bool):
    return request(ui, "/api/history/switch", {"save_runs": on, "revision": get_state(ui)["history"]["revision"]})


def events(resp, ui=None) -> list[dict]:
    data = resp.read()
    if ui is not None:
        getattr(ui, "transcript", []).append(data)
    assert data == b"" or data.endswith(b"\n"), "stream must end with a complete line"
    return [strict_loads(line) for line in data.splitlines() if line]


def wait_until(predicate, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def upload(ui, data: bytes, filename: str | None, encoded: str | None = None, length: str | None = "auto",
           timeout: float = 10.0):
    """POST /api/attachments/extract with raw bytes. `encoded` overrides X-Filename's text as sent.
    Returns (status, parsed body)."""
    import http.client
    import urllib.parse
    conn = http.client.HTTPConnection("127.0.0.1", ui.server_address[1], timeout=timeout)
    try:
        conn.putrequest("POST", "/api/attachments/extract")
        conn.putheader("X-Session-Token", ui.token)
        conn.putheader("Content-Type", "application/octet-stream")
        if encoded is not None or filename is not None:
            conn.putheader("X-Filename", encoded if encoded is not None else urllib.parse.quote(filename, safe=""))
        if length == "auto":
            conn.putheader("Content-Length", str(len(data)))
        elif length is not None:
            conn.putheader("Content-Length", length)
        conn.endheaders(data if length is not None else None)
        resp = conn.getresponse()
        body = resp.read()
        getattr(ui, "transcript", []).append(body)
        return resp.status, strict_loads(body)
    finally:
        conn.close()


def headers_only(ui, path: str, length: int, extra: dict | None = None, timeout: float = 10.0):
    """Send a request's headers with a declared Content-Length and no body; returns (status, body)."""
    sock = socket.create_connection(ui.server_address, timeout=timeout)
    try:
        head = (f"POST {path} HTTP/1.0\r\nX-Session-Token: {ui.token}\r\nContent-Length: {length}\r\n"
                + "".join(f"{k}: {v}\r\n" for k, v in (extra or {}).items()) + "\r\n")
        sock.sendall(head.encode("ascii"))
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    finally:
        sock.close()
    status = int(data.split(b" ", 2)[1])
    return status, strict_loads(data.split(b"\r\n\r\n", 1)[1])


# --- FakeMCP: a scripted Streamable HTTP MCP server, both eras (techspec 0.1.2, Section 15.2) -----
MODERN_VERSION = "2026-07-28"


class FakeCall:
    """One request at FakeMCP, with the means to reply. Tool behaviours and overrides receive it."""

    def __init__(self, fake, handler, record):
        self.fake, self.h, self.record = fake, handler, record
        self.body = record["body"]
        self.headers_sent = False

    # replies
    def json(self, payload, status=200, headers=None):
        from agentwiz.jsonio import encode_exact
        data = encode_exact(payload).encode() if not isinstance(payload, bytes) else payload
        self.h.send_response(status)
        self.h.send_header("Content-Type", "application/json")
        self.h.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.h.send_header(k, v)
        self.h.end_headers()
        self.h.wfile.write(data)

    def result(self, result, status=200, headers=None):
        self.json({"jsonrpc": "2.0", "id": self.body.get("id"), "result": result}, status, headers)

    def error(self, code, message="error", status=200, data=None):
        err = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        self.json({"jsonrpc": "2.0", "id": self.body.get("id") if isinstance(self.body, dict) else None,
                   "error": err}, status)

    def raw(self, status, data=b"", ctype="text/plain", headers=None):
        self.h.send_response(status)
        self.h.send_header("Content-Type", ctype)
        self.h.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.h.send_header(k, v)
        self.h.end_headers()
        self.h.wfile.write(data)

    def start_sse(self, headers=None):
        self.h.send_response(200)
        self.h.send_header("Content-Type", "text/event-stream")
        for k, v in (headers or {}).items():
            self.h.send_header(k, v)
        self.h.end_headers()
        self.headers_sent = True

    def event(self, payload):
        from agentwiz.jsonio import encode_exact
        data = payload if isinstance(payload, bytes) else b"data: " + encode_exact(payload).encode() + b"\n\n"
        self.h.wfile.write(data)
        self.h.wfile.flush()

    def progress(self, progress, total=None, message=None, token="mine"):
        tok = self.body["params"]["_meta"]["progressToken"] if token == "mine" else token
        params = {"progressToken": tok, "progress": progress}
        if total is not None:
            params["total"] = total
        if message is not None:
            params["message"] = message
        self.event({"jsonrpc": "2.0", "method": "notifications/progress", "params": params})

    def final(self, result):
        self.event({"jsonrpc": "2.0", "id": self.body["id"], "result": result})

    def hold(self, until=None, timeout=30.0):
        """Wait until `until` (an Event; default the fake's release) is set or the client disconnects.
        Returns True if released, False on disconnect."""
        until = until or self.fake.release
        end = time.monotonic() + timeout
        with self.fake.lock:
            self.fake.holding += 1
        try:
            while time.monotonic() < end:
                if until.is_set():
                    return True
                if self._peer_closed():
                    self.fake.note_disconnect(self.record)
                    return False
            return False
        finally:
            with self.fake.lock:
                self.fake.holding -= 1

    def _peer_closed(self):
        sock = self.h.connection
        try:
            if select.select([sock], [], [], 0.02)[0]:
                return not socket.socket.recv(sock, 1, socket.MSG_PEEK)   # raw bytes, also under TLS
        except OSError:
            return True
        return False


def text_result(text, **extra):
    return {"content": [{"type": "text", "text": text}], "isError": False, **extra}


def simple_tool(name, schema=None, behave=None):
    return {"name": name, "inputSchema": schema or {"type": "object", "properties": {}},
            "behave": behave or (lambda call: call.result(text_result(name)))}


class FakeMCP:
    """Modes: modern, legacy-stateless, legacy-session, dual. Records every request (parsed exactly)."""
    __test__ = False

    def __init__(self, mode="dual", tools=None, token=None, tls=None):
        from agentwiz.jsonio import decode_exact
        self.mode, self.token = mode, token
        self.tools = {t["name"]: t for t in (tools or [simple_tool("echo")])}
        self.tools_for_token = None              # callable(token) -> list of tool dicts (schemas only)
        self.overrides: dict[str, list] = {}     # JSON-RPC method -> [callable(call) -> bool handled]
        self.page_size = None
        self.requests: list[dict] = []
        self.disconnects: list[dict] = []
        self.notices: list[dict] = []
        self.deletes: list[dict] = []
        self.answers: list[dict] = []
        self.sessions: set[str] = set()
        self.session_counter = itertools.count(1)
        self.delete_status = 200
        self.notice_hold = threading.Event()
        self.notice_hold.set()                   # cleared by tests to hold the notice's acknowledgement
        self.answer_hold = threading.Event()
        self.answer_hold.set()
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.holding = 0
        self.closed = threading.Event()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                fake._handle(self, "POST")

            def do_DELETE(self):
                fake._handle(self, "DELETE")

            def do_GET(self):
                fake._handle(self, "GET")

        self._decode = decode_exact
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        if tls is not None:
            import ssl
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(*tls)
            self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.scheme = "https" if tls else "http"
        threading.Thread(target=self.httpd.serve_forever, args=(0.02,), daemon=True).start()

    @property
    def url(self):
        return f"{self.scheme}://127.0.0.1:{self.httpd.server_address[1]}/mcp"

    def methods(self, name=None):
        return [r for r in self.requests if name is None or r["method"] == name]

    def note_disconnect(self, record):
        with self.lock:
            self.disconnects.append({"method": record["method"], "time": time.monotonic(), "record": record})

    def wait_holding(self, n=1, timeout=10.0):
        return wait_until(lambda: self.holding >= n, timeout)

    def override(self, method, fn, times=1):
        self.overrides.setdefault(method, []).extend([fn] * times)

    def close(self):
        self.release.set()
        self.notice_hold.set()
        self.answer_hold.set()
        self.httpd.shutdown()
        self.httpd.server_close()

    # --- dispatch ---------------------------------------------------------------------------------
    def _era_of(self, headers):
        modern_header = headers.get("mcp-protocol-version") == MODERN_VERSION
        if self.mode == "modern":
            return "modern"
        if self.mode == "dual":
            return "modern" if modern_header else "legacy-session"
        return self.mode

    def _handle(self, h, http_method):
        n = int(h.headers.get("Content-Length") or 0)
        raw = h.rfile.read(n) if n else b""
        headers = {k.lower(): v for k, v in h.headers.items()}
        try:
            body = self._decode(raw.decode()) if raw else None
        except ValueError:
            body = raw
        method = body.get("method") if isinstance(body, dict) else None
        if http_method == "DELETE":
            method = "DELETE"
        elif isinstance(body, dict) and method is None:
            method = "answer"
        record = {"method": method, "http": http_method, "headers": headers, "body": body,
                  "text": raw.decode(errors="replace"), "time": time.monotonic(), "era": self._era_of(headers)}
        with self.lock:
            self.requests.append(record)
        call = FakeCall(self, h, record)
        try:
            self._route(call, record, headers, method)
        except OSError:
            self.note_disconnect(record)

    def _route(self, call, record, headers, method):
        if self.token is not None and headers.get("authorization") != f"Bearer {self.token}":
            return call.raw(401, b'{"error": "unauthorized"}', "application/json")
        for fn in list(self.overrides.get(method, [])):
            self.overrides[method].remove(fn)
            if fn(call) is not False:
                return
            break
        era = record["era"]
        if method == "DELETE":
            with self.lock:
                self.deletes.append(record)
            self.sessions.discard(headers.get("mcp-session-id"))
            return call.raw(self.delete_status)
        if method == "notifications/cancelled":
            with self.lock:
                self.notices.append(record)
            if call.hold(self.notice_hold) or self.notice_hold.is_set():
                return call.raw(202)
            return None
        if method == "answer":
            with self.lock:
                self.answers.append(record)
            if call.hold(self.answer_hold) or self.answer_hold.is_set():
                return call.raw(202)
            return None
        if era == "modern":
            return self._modern(call, headers, method)
        return self._legacy(call, headers, method, era)

    def _modern(self, call, headers, method):
        if headers.get("mcp-protocol-version") != MODERN_VERSION:
            return call.error(-32022, "Unsupported protocol version", status=400, data={"supported": [MODERN_VERSION]})
        if headers.get("mcp-method") != method:
            return call.error(-32020, "Header mismatch", status=400)
        if method == "server/discover":
            return call.result({"supportedVersions": [MODERN_VERSION],
                                "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "fake", "version": "1"}}})
        if method == "tools/list":
            return self._list(call, headers)
        if method == "tools/call":
            if headers.get("mcp-name") is None:
                return call.error(-32020, "Header mismatch", status=400)
            return self._call(call)
        return call.error(-32601, "Method not found", status=404)

    def _legacy(self, call, headers, method, era):
        if headers.get("mcp-protocol-version") == MODERN_VERSION or method == "server/discover":
            if era == "legacy-session" and method != "initialize" and not headers.get("mcp-session-id"):
                return call.error(-32600, "Bad Request: Missing session ID", status=400)
            return call.error(-32600, "Unsupported protocol version: 2026-07-28", status=400)
        if method == "initialize":
            offered = call.body["params"]["protocolVersion"]
            version = offered if offered in ("2025-11-25", "2025-06-18", "2025-03-26") else "2025-11-25"
            extra = {}
            if era == "legacy-session":
                sid = f"session-{next(self.session_counter)}"
                self.sessions.add(sid)
                extra["Mcp-Session-Id"] = sid
            return call.result({"protocolVersion": version, "capabilities": {"tools": {}},
                                "serverInfo": {"name": "fake-legacy", "version": "1"}}, headers=extra)
        if era == "legacy-session":
            sid = headers.get("mcp-session-id")
            if not sid:
                return call.error(-32600, "Bad Request: Missing session ID", status=400)
            if sid not in self.sessions:
                return call.raw(404, b'{"error": "Session not found"}', "application/json")
        if method == "notifications/initialized":
            return call.raw(202)
        if method == "ping":
            return call.result({})
        if method == "tools/list":
            return self._list(call, headers)
        if method == "tools/call":
            return self._call(call)
        return call.error(-32601, "Method not found")

    def _list(self, call, headers):
        token = (headers.get("authorization") or "")[len("Bearer "):] or None
        tools = self.tools_for_token(token) if self.tools_for_token else list(self.tools.values())
        listed = [{k: v for k, v in t.items() if k != "behave"} for t in tools]
        params = call.body.get("params") or {}
        start = int(params.get("cursor") or 0)
        if self.page_size is None:
            return call.result({"tools": listed})
        page = listed[start:start + self.page_size]
        result = {"tools": page}
        if start + self.page_size < len(listed):
            result["nextCursor"] = str(start + self.page_size)
        return call.result(result)

    def _call(self, call):
        name = call.body["params"]["name"]
        tool = self.tools.get(name)
        if tool is None:
            return call.result({"content": [{"type": "text", "text": "unknown tool"}], "isError": True})
        tool["behave"](call)


@pytest.fixture
def make_fake_mcp():
    fakes = []

    def make(*args, **kwargs) -> FakeMCP:
        fakes.append(FakeMCP(*args, **kwargs))
        return fakes[-1]

    yield make
    for f in fakes:
        f.close()


TLS_DIR = Path(__file__).parent / "fixtures" / "tls"


@pytest.fixture
def test_tls(monkeypatch):
    """The committed test certificate: the fake server's pair, and mcp_client trusting only it."""
    import ssl

    from agentwiz import mcp_client
    cert, key = str(TLS_DIR / "test_cert.pem"), str(TLS_DIR / "test_key.pem")
    monkeypatch.setattr(mcp_client, "make_ssl_context", lambda: ssl.create_default_context(cafile=cert))
    return cert, key


# --- MCP route helpers ---------------------------------------------------------------------------
def mcp_state(ui):
    return get_state(ui)["mcp"]


def save_servers(ui, servers: dict, revision=None):
    rev = mcp_state(ui)["revision"] if revision is None else revision
    return request(ui, "/api/mcp/servers", {"servers": servers, "revision": rev})


def add_server(ui, name, url, **fields):
    current = {n: {k: s[k] for k in ("url", "idle_timeout_s", "call_deadline_s", "discovery_deadline_s", "token_env")}
               for n, s in mcp_state(ui)["servers"].items()}
    current[name] = {"url": url, **fields}
    return save_servers(ui, current)


def set_token(ui, server, token):
    return request(ui, "/api/mcp/token", {"server": server, "token": token, "revision": mcp_state(ui)["revision"]})


def load_tools(ui, server, revision=None):
    rev = mcp_state(ui)["revision"] if revision is None else revision
    return request(ui, "/api/mcp/tools", {"server": server, "revision": rev})


def mcp_call(ui, server, tool, arguments_text="{}", raw=False, revision=None, timeout=30.0):
    rev = mcp_state(ui)["revision"] if revision is None else revision
    resp = request(ui, "/api/mcp/call", {"server": server, "tool": tool, "arguments_text": arguments_text,
                                         "revision": rev}, raw=True, timeout=timeout)
    return resp if raw else events(resp, ui)


def start_call(ui, server, tool, arguments_text="{}"):
    """Start a call; returns (response, operation_id) after reading the started event."""
    resp = mcp_call(ui, server, tool, arguments_text, raw=True)
    started = strict_loads(resp.readline())
    assert started["type"] == "started"
    return resp, started["operation_id"]


def rest(resp) -> list[dict]:
    data = resp.read()
    return [strict_loads(line) for line in data.splitlines() if line]


def cancel(ui, op_id):
    return request(ui, "/api/mcp/cancel", {"operation_id": op_id})


# --- contract helpers (techspec 0.1.3, 18.2) ----------------------------------------------------
# Contract texts are written as literal text, never built from Python floats, so that exact numbers
# reach AgentWiz digit for digit.
TEXT_CONTRACT = '{"format": "text", "checks": [{"type": "contains", "value": "pong"}]}'
JSON_CONTRACT = """{
  "format": "json",
  "description": "Fields pulled from a report",
  "schema": {"type": "object", "required": ["id", "ratio"],
             "properties": {"id": {"type": "integer"}, "ratio": {"type": "number", "maximum": 1}}},
  "checks": [{"type": "contains", "value": "\\"id\\""},
             {"type": "json_equals", "value": {"id": 9007199254740993, "ratio": 0.10000000000000000001}}]
}"""
JSON_CONTRACT_NO_SCHEMA = """{
  "format": "json",
  "checks": [{"type": "json_equals", "value": {"id": 9007199254740993, "ratio": 0.10000000000000000001}}]
}"""


def save_contract(ui, name, text, revision=None):
    """POST /api/contracts/save; returns the response body."""
    return request(ui, "/api/contracts/save", {"name": name, "text": text, "revision": revision})


def contract_files(ui) -> Path:
    return ui.workspace.path / "contracts"


def contract_item(ui, name) -> dict:
    """The named contract's entry in /api/state, or None."""
    return next((c for c in get_state(ui)["contracts"]["items"] if c["name"] == name), None)


# --- block runs (techspec 0.1.4, 20.1) ------------------------------------------------------------------------------
def block_revision(ui, name) -> int:
    rows = {r["name"]: r for r in request(ui, "/api/blocks")["blocks"]}
    return rows[name]["revision"]


def run_block(ui, name, inputs, revision=None, raw=False, timeout=30.0):
    """POST /api/blocks/run at the block's current revision unless one is given. Returns the stream's events (or
    the open response with raw=True, for tests that act while it runs)."""
    rev = block_revision(ui, name) if revision is None else revision
    resp = request(ui, "/api/blocks/run", {"name": name, "revision": rev, "inputs": inputs}, raw=True,
                   timeout=timeout)
    return resp if raw else events(resp, ui)


def result_of(evs: list[dict]) -> dict:
    """The block run's committed result from its `final` event."""
    return final_of(evs)["result"]


def stop_block(ui, run_id):
    return request(ui, "/api/blocks/stop", {"run_id": run_id})


def poll_block(ui, run_id):
    return request(ui, f"/api/block_runs/{run_id}")


class FakeTimer:
    def __init__(self, seconds, callback):
        self.seconds, self.callback = seconds, callback
        self.cancelled = False
        self.fired = False

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.fired = True
        self.callback()


class TimerHub:
    def __init__(self):
        self.timers: list[FakeTimer] = []
        self.lock = threading.Lock()

    def make(self, seconds, callback):
        timer = FakeTimer(seconds, callback)
        with self.lock:
            self.timers.append(timer)
        return timer

    def live(self) -> list[FakeTimer]:
        with self.lock:
            return [t for t in self.timers if not t.cancelled and not t.fired]

    def fire(self, seconds=None, index=0):
        """Fire the `index`-th live timer, optionally only among those made with `seconds`."""
        assert wait_until(lambda: [t for t in self.live() if seconds is None or t.seconds == seconds]), \
            "no such timer was made"
        timers = [t for t in self.live() if seconds is None or t.seconds == seconds]
        timers[index].fire()


@pytest.fixture
def fire_timers(monkeypatch):
    """Replace runner.make_timer so tests fire timers on demand instead of waiting for time (20.1)."""
    from agentwiz import runner
    hub = TimerHub()
    monkeypatch.setattr(runner, "make_timer", hub.make)
    return hub


@pytest.fixture
def spawned(monkeypatch):
    """The process groups of every script started, learnt through script_step.after_spawn (11.4)."""
    from agentwiz import script_step
    groups: list[int] = []
    original = script_step.after_spawn

    def record(pgid):
        groups.append(pgid)
        original(pgid)

    monkeypatch.setattr(script_step, "after_spawn", record)
    return groups


def group_gone(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False
