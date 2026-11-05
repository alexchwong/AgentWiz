"""Isolated tutorial instances. No source checkout or external services required.

0.1.4 (Section 14): topics come from `tutorial_topics`; the child's temporary workspace is seeded with a profile, a
role map and the four example blocks; ending a session closes the child through `UIServer.server_close(wait=False)`,
and a session whose cleanup is still outstanding becomes the manager's one pending session (14.5)."""
from __future__ import annotations

import itertools
import shutil
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

from . import tutorial_topics
from .tutorial_topics import (CONTRACT, CONTRACT_FAMILY, CONTRACT_LABEL, CONTRACT_NAME, ROLE, ROLE_MODEL,
                              ROLE_PROFILE, TOPICS)
from .workspace import open_workspace

BLOCKS_SOURCE = Path(__file__).with_name("tutorial_support") / "blocks"


def ensure_contract(state) -> None:
    """Idempotent: reuse `fields` when its revision 1 is the packaged definition; create it when missing. Runs only
    against the tutorial's own temporary workspace; an existing different file is left for the learner to see."""
    from .contract_schema import contract_digest
    from .contracts import StoreError
    from .jsonio import decode_exact
    try:
        detail = state.contract_detail(CONTRACT_NAME, 1)
    except StoreError as exc:
        if exc.code != "no_such_contract":
            return
        state.save_contract(CONTRACT_NAME, CONTRACT_FAMILY, None, CONTRACT, CONTRACT_LABEL)
        return
    if detail.get("digest") != contract_digest(decode_exact(CONTRACT)):
        return


class TutorialBusy(Exception):
    """A tutorial cannot start now: the previous session is still cleaning up, or AgentWiz is shutting down. The
    HTTP layer answers 503 `busy` with this text."""


class _Pending:
    """A closed session that something still writes to. It keeps, together and with their last owning references,
    the child's process state, its workspace handle and lock, the `TemporaryDirectory` (so its finaliser cannot run
    early) and, through the state, every outstanding cleanup owner (14.5)."""

    def __init__(self, state, workspace, folder, labels):
        self.state, self.workspace, self.folder, self.labels = state, workspace, folder, list(labels)

    def finish(self) -> None:
        """Only after a completed drain: the workspace is closed, then the folder is removed."""
        try:
            self.workspace.close()
        finally:
            self.folder.cleanup()


def _say(line: str) -> None:
    print(f"[AgentWiz] {line}", file=sys.stderr, flush=True)


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
        self.topic = TOPICS[0].id
        self.pending: _Pending | None = None
        self.closing = False

    def _serve(self, server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.resources.append((server, thread))
        return server

    # --- starting ------------------------------------------------------------------------------------
    def _reclaim_pending(self) -> None:
        """Under the lock. A pending session is finished when its drain is now complete, else the start is refused."""
        pending = self.pending
        if pending is None:
            return
        if pending.state.shutdown(wait=False):             # returns at once on a second call
            raise TutorialBusy("the previous tutorial session is still cleaning up")
        self.pending = None
        pending.finish()

    def _seed(self, state, workspace) -> None:
        """The profile, the role map and the example blocks (14.4). Nothing is written outside the session's
        temporary folder."""
        base = {"kind": "openai_compat"}
        state.save_profile({"name": ROLE_PROFILE, **base, "model": ROLE_MODEL}, state.view()["revision"])
        state.select_profile(None, state.view()["revision"])          # detach: the active settings stay the stand-in's
        state.apply_config({**base, "model": "complete"}, state.view()["revision"])
        state.save_roles({ROLE: ROLE_PROFILE}, state.view()["roles"]["revision"],
                         {ROLE: {"description": "Routine extraction", "location": "local",
                                 "task_defaults": {"temperature": 0}}})
        shutil.copytree(BLOCKS_SOURCE, workspace.path / "blocks", copy_function=shutil.copyfile)

    def start(self):
        from .ui.server import UIServer
        from .tutorial_support.backend import Handler
        from .tutorial_support.mcp import Stub, StubServer

        with self.lock:
            if self.closing:
                raise TutorialBusy("AgentWiz is shutting down")
            self.last_seen = time.monotonic()
            if self.child:
                return {"url": self.child.url}
            self._reclaim_pending()
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
                from .server_config import ServerSettings
                model_servers = {kind: ServerSettings(self.backend_url) for kind in
                                 ("lmstudio", "openrouter", "openai_compat")}
                self.child = UIServer(workspace=self.workspace, profiles_dir=root / "profiles",
                                      mcp_servers_dir=root / "mcp", servers=model_servers)
                self._serve(self.child)
                self.child.tutorial_session = self
                state = self.child.state
                state.save_mcp_servers(servers, state.view()["mcp"]["revision"])
                ensure_contract(state)
                self._seed(state, self.workspace)
                self.topic = TOPICS[0].id
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

    # --- the session -----------------------------------------------------------------------------------
    def info(self):
        with self.lock:
            self.last_seen = time.monotonic()
            return {"active": True, "return_url": self.parent.url, "topic": self.topic,
                    "topics": [tutorial_topics.public(t) for t in TOPICS], "contract": CONTRACT}

    def active(self) -> bool:
        """True while a child session or its cleanup still exists. Used before changing workspaces."""
        with self.lock:
            return self.closing or self.child is not None or bool(self.resources) or self.pending is not None

    def prepare(self, body):
        """Prepare a topic by ID. Raises KeyError for an unknown ID (the route answers 400)."""
        with self.lock:
            topic = tutorial_topics.by_id(body.get("topic"))
            state = self.child.state
            if state.store.running_count() or state.store.active_block_runs() or state.mcp_ops.active_count():
                return {"busy": True}
            if topic.id.startswith("assist-"):
                action = topic.id[len("assist-"):]
                state.save_profile({"name":"tutorial-assistant","kind":"openai_compat",
                                    "model":"workshop-" + action}, state.view()["revision"])
                state.select_profile(None, state.view()["revision"])
                state.save_roles({ROLE:ROLE_PROFILE, "assistant":"tutorial-assistant"},state.view()["roles"]["revision"])
            self.topic = topic.id
            self.last_seen = time.monotonic()
            state.apply_config({"kind": "openai_compat",
                                "model": topic.model}, state.view()["revision"])
            if topic.seed_contract:
                ensure_contract(state)
            return self.info()

    # --- ending ------------------------------------------------------------------------------------------
    def close(self):
        """End the active session (14.5): stop the child's serving loop, close it through `server_close(wait=False)`
        exactly once, stop the stand-ins, then either release everything or keep it as the pending session."""
        with self.lock:
            self.closed.set()
            child, workspace, folder = self.child, self.workspace, self.folder
            resources, self.resources = self.resources, []
            self.child = self.workspace = self.folder = None
            self.topic = TOPICS[0].id
            left: list[str] = []
            for server, thread in reversed(resources):
                if server is child:
                    server.shutdown()                       # the serving loop only
                    try:
                        left = server.server_close(wait=False)
                    finally:
                        thread.join(timeout=2)
                else:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)
            if child is not None and not any(s is child for s, _ in resources):
                left = child.server_close(wait=False)       # a child that was never served (a failed start)
            if left and child is not None:
                self.pending = _Pending(child.state, workspace, folder, left)
                _say(f"tutorial cleanup outstanding in {folder.name}: " + "; ".join(left))
            else:
                if workspace:
                    workspace.close()
                if folder:
                    folder.cleanup()
            self.closed = threading.Event()

    def begin_shutdown(self, take_pending: bool = True):
        """The first half of the parent's shutdown (14.5): no later start succeeds, and the active session is closed
        (bounded). Returns the pending session when `take_pending`, for `finish`."""
        with self.lock:
            self.closing = True
            if self.child is not None or self.resources:
                self.close()
            if not take_pending:
                return None
            pending, self.pending = self.pending, None
            return pending

    @staticmethod
    def finish(pending) -> None:
        """The second half, holding no lock: drain the pending child's state, then close its workspace and remove
        its folder (never earlier than a completed drain)."""
        if pending is None:
            return
        pending.state.shutdown(wait=True)
        pending.finish()

    def shutdown_all(self):
        """`Tutorial.shutdown_all()` (14.5): both halves."""
        self.finish(self.begin_shutdown())
