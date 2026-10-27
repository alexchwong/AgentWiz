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
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agentwiz.ui.server import UIServer
from agentwiz.workspace import open_workspace

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


class TestServer:
    __test__ = False  # not a pytest test class

    def __init__(self):
        self.requests: list[dict] = []
        self.replies: list[Reply] = []
        self.release = threading.Event()
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
        ws.close()
        reopened = open_workspace(ws.path.parent, ws.name)
        make_ui.opened.append(reopened)
        return make_ui(workspace=reopened, profiles_dir=old.state.library.path)

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
        resp = urllib.request.build_opener().open(req, timeout=timeout)
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


def configure(ui, backend, kind="openai_compat", **fields) -> dict:
    body = {"kind": kind, "base_url": backend.url + "/v1", "model": "m", **fields}
    body.setdefault("revision", get_state(ui)["revision"])
    return request(ui, "/api/config", body)


def complete(ui, prompt, system="", revision=None, raw=False):
    rev = get_state(ui)["revision"] if revision is None else revision
    resp = request(ui, "/api/complete", {"prompt": prompt, "system": system, "revision": rev}, raw=True)
    return resp if raw else events(resp, ui)


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
