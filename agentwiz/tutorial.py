"""Isolated tutorial instances. No source checkout or external services required."""
from __future__ import annotations

import itertools
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

from .workspace import open_workspace

CONTRACT = '''{
  "format_version": 1, "format": "json", "strict": false,
  "description": "Tutorial exact-number fields", "schema": null,
  "checks": [{"type": "json_equals", "value": {
    "id": 9007199254740993, "ratio": 0.10000000000000000001
  }}]
}'''


class Tutorial:
    """One child session per parent; expiry bounds abandoned browser sessions."""

    def __init__(self, parent):
        self.parent = parent
        self.lock = threading.RLock()
        self.resources = []
        self.child = None
        self.folder = None
        self.workspace = None
        self.last_seen = time.monotonic()
        self.closed = threading.Event()
        self.step = 0

    def _serve(self, server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.resources.append((server, thread))
        return server

    def start(self):
        from .ui.server import UIServer
        from .tutorial_support.backend import Handler
        from .tutorial_support.mcp import Stub, StubServer

        with self.lock:
            self.last_seen = time.monotonic()
            if self.child:
                return {"url": self.child.url}
            try:
                self.folder = tempfile.TemporaryDirectory(prefix="agentwiz-tutorial-")
                root = Path(self.folder.name)
                backend = self._serve(ThreadingHTTPServer(("127.0.0.1", 0), Handler))
                self.backend_url = f"http://127.0.0.1:{backend.server_port}/v1"
                handler = type("TutorialStub", (Stub,), {"era_mode": "modern", "token": None,
                                                       "sessions": set(), "counter": itertools.count(1)})
                stub = self._serve(StubServer(("127.0.0.1", 0), handler))
                servers = {"tutorial": {"url": f"http://127.0.0.1:{stub.server_port}/mcp"}}
                self.workspace = open_workspace(root / "workspaces", "tutorial")
                self.child = self._serve(UIServer(workspace=self.workspace, profiles_dir=root / "profiles",
                                                  mcp_servers_dir=root / "mcp"))
                self.child.tutorial_session = self
                state = self.child.state
                state.save_mcp_servers(servers, state.view()["mcp"]["revision"])
                state.apply_config({"kind": "openai_compat", "base_url": self.backend_url,
                                    "model": "complete"}, state.view()["revision"])
                state.save_contract("fields", CONTRACT, None)
                self.closed.clear()
                threading.Thread(target=self._expire, args=(self.closed,), daemon=True).start()
                return {"url": self.child.url}
            except Exception:
                self.close()
                raise

    def _expire(self, closed):
        while not closed.wait(30):
            with self.lock:
                if closed.is_set():
                    return
                if time.monotonic() - self.last_seen > 1800:
                    self.close()
                    return

    def info(self):
        with self.lock:
            self.last_seen = time.monotonic()
            return {"active": True, "return_url": self.parent.url, "step": self.step,
                    "contract": CONTRACT}

    def prepare(self, body):
        with self.lock:
            step = body.get("step")
            if type(step) is not int or not 0 <= step < 5:
                raise ValueError("invalid tutorial step")
            if self.child.state.store.running_count() or self.child.state.mcp_ops.active_count():
                return {"busy": True}
            self.step = step
            self.last_seen = time.monotonic()
            model = "json-repair" if step == 4 else "complete"
            state = self.child.state
            state.apply_config({"kind": "openai_compat", "base_url": self.backend_url,
                                "model": model}, state.view()["revision"])
            if step == 4 and not any(c["name"] == "fields" for c in state.view()["contracts"]["items"]):
                state.save_contract("fields", CONTRACT, None)
            return self.info()

    def close(self):
        with self.lock:
            self.closed.set()
            if self.child:
                self.child.state.store.stop_all()
                self.child.state.mcp_ops.cancel_all()
            for server, thread in reversed(self.resources):
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
            self.resources.clear()
            self.child = None
            if self.workspace:
                self.workspace.close()
                self.workspace = None
            if self.folder:
                self.folder.cleanup()
                self.folder = None
            self.step = 0
            self.closed = threading.Event()
