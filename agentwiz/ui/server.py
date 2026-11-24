# Derived from modelSpell ui/server.py at commit 1759c6e3c28a637731434a73c46088048583e358
# (MIT, Copyright (c) 2026 Alex Wong). Restructured per the AgentWiz 0.1.0 techspec, Section 7.6.
"""AgentWiz HTTP server: the only module that knows HTTP.

Binds to 127.0.0.1 only. Every request must carry the per-launch session token (the page URL's
`t` parameter, or the X-Session-Token header), so other web pages in the browser cannot drive the
server or spend API credit. Settings are shared by all tabs and checked per request by revision;
API keys are write-only and held in memory. Profiles are workspace-local copies of shared defaults;
the server prepares and adopts a destination workspace before switching, and never serves two workspace
states at once. Error text reaching the page or terminal is limited to text that cannot contain a
credential (techspec 7.6, 7.7).
"""
from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import re
import secrets
import socket
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

import modelspell
from modelspell import ModelSpellError

from .. import (__version__, attachments, checks, contract_schema, contract_service, contracts, history, insertion, jsonio, mcp_ops, model_call, model_step,
                output_protocol, runner, runs, tool_forms, tutorial_topics, workshop)
from .. import workspace as wsmod
from .. import workspace_admin
from .. import agent_documents, agent_loop, library_install, outbound_policy, pdf_reader
from ..errors import SettingsHeld, ShuttingDown, report_internal  # noqa: F401  (re-exported; defined in errors.py since 0.1.4)
from ..mcp_client import MCPError
from ..model_call import SettingsError, describe_error
from ..task_settings import REASONING_LEVELS, TaskSettingError, parse_task
from ..tutorial import TutorialBusy
from ..state import (SHUTDOWN_GRACE_S, BlockUnusable, DefaultConflict, NoSuchBlock, NoSuchProfile, NoSuchServer, ProcessState, ReadOnly,
                     RoleProfileMissing, RoleUnresolved, StaleRevision, WriteFailed)
from ..workspace import NAME_RE, Workspace

INDEX = Path(__file__).with_name("index.html")
# The one static script the page loads besides itself (overhaul spec 15.4): an explicit allowlist entry, never
# path-based file serving. It is read once, like the page, and needs the session token like every route.
ASSETS = {f"/assets/{name}.js": (Path(__file__).with_name(f"{name}.js"), "text/javascript; charset=utf-8")
          for name in ("contracts", "workflows")}
JSON = "application/json"
UPLOAD_LIMIT = 52_428_800          # 50 MiB; the JSON cap is insertion.REQUEST_LIMIT. Tests may lower both.
BODY_CHUNK = 64 << 10              # an upload body is read in chunks of at most this (addendum 4.2)
BODY_DEADLINE_S = 60.0             # the whole body of an upload, for every format. Tests may lower it.
UPLOAD_ROUTE = "/api/attachments/extract"
AGENT_UPLOAD_ROUTE = "/api/agent/documents"     # the second raw-body route (0.2.2, 4.9)
WORKSPACES_ROUTE = "/api/workspaces"
_HEX = set("0123456789abcdefABCDEF")


def _is_workspace_admin_path(path: str) -> bool:
    return path == WORKSPACES_ROUTE or path.startswith(WORKSPACES_ROUTE + "/")


class BodyError(ValueError):
    """A malformed request body or field. Messages are fixed or name fields only."""


class BlockBodyError(BodyError):
    """A block run request whose body does not fit the block's declarations (400 `block_body`)."""


class _ReadTimeout(Exception):
    """The request body could not be read within the page timeout: close without a response."""


class TooLarge(Exception):
    """Content-Length is over the route's limit; answered 413 before anything is read (R3-3)."""

    def __init__(self, size: int, limit: int, remedy: str = ""):
        super().__init__(f"The request is {size} bytes; the limit is {limit} bytes.{remedy}")
        self.size, self.limit = size, limit


class LengthRequired(Exception):
    pass


class BadArguments(ValueError):
    """Tool arguments that cannot be read exactly; the message names the rule, never the text."""


class FilenameError(ValueError):
    """X-Filename broke a rule; the message names the rule, never the name."""


def decode_filename(header: str | None) -> str:
    """Strict percent-decoding of X-Filename (Section 8.4); urllib's unquote is too lenient."""
    if header is None:
        raise FilenameError("X-Filename is required")
    if not header.isascii() or len(header) > 1024:
        raise FilenameError("X-Filename must be ASCII and at most 1,024 bytes")
    out = bytearray()
    i = 0
    while i < len(header):
        c = header[i]
        if c == "%":
            pair = header[i + 1:i + 3]
            if len(pair) != 2 or not set(pair) <= _HEX:
                raise FilenameError("every % in X-Filename must be followed by two hexadecimal digits")
            out.append(int(pair, 16))
            i += 3
        else:
            out.append(ord(c))
            i += 1
    try:
        name = out.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise FilenameError("X-Filename must decode to valid UTF-8") from None
    if name == "" or any(ord(ch) < 0x20 or 0x7F <= ord(ch) < 0xA0 for ch in name):
        raise FilenameError("the file name must not be empty or contain control characters")
    return name


class WorkspaceGate:
    """Refuse ordinary requests while a workspace is being switched, reset or copied.

    Normal requests hold a shared slot. A workspace operation takes the exclusive slot after existing
    requests have finished; new ordinary requests receive 503 `busy` rather than waiting indefinitely.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._readers = 0
        self._admin = False
        self._closed = False

    @contextmanager
    def shared(self):
        with self._condition:
            if self._closed or self._admin:
                raise workspace_admin.WorkspaceAdminError("busy", "the workspace is being changed", 503)
            self._readers += 1
        try:
            yield
        finally:
            with self._condition:
                self._readers -= 1
                self._condition.notify_all()

    @contextmanager
    def admin(self, timeout: float = 2.0):
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._readers or self._admin:
                if self._closed:
                    raise workspace_admin.WorkspaceAdminError("workspace_busy", "AgentWiz is shutting down", 503)
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._condition.wait(remaining):
                    raise workspace_admin.WorkspaceAdminError(
                        "workspace_busy", "a request is still running; stop it before changing workspaces", 409)
            self._admin = True
        try:
            yield
        finally:
            with self._condition:
                self._admin = False
                self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


from . import surfaces


class UIServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int = 0, *, workspace: Workspace, profiles_dir: Path, page_timeout: float = 30.0,
                 request_limit: int = 8, mcp_servers_dir: Path | None = None, servers=None, config_path=None,
                 production: bool = False):
        self.authority = surfaces.Authority("workshop") if production else None
        self.chat_listener = None
        self.generation_changed = lambda: None
        self.token = secrets.token_urlsafe(24)
        from ..tutorial import Tutorial
        self.tutorial = Tutorial(self)
        self.tutorial_session = None
        self.workspace = workspace  # the current workspace; close_workspace() closes it
        self.profiles_dir = Path(profiles_dir).resolve()
        self.mcp_servers_dir = (Path(mcp_servers_dir) if mcp_servers_dir is not None
                                else Path("./mcp_servers")).resolve()
        self.workspace_root = Path(workspace.path).parent.resolve()
        self.request_limit = request_limit
        if servers is None:
            from .. import server_config
            config_path = Path(config_path or "./config/model-servers.ini").absolute()
            server_config.initialize(config_path)
            servers = server_config.load(config_path)
        self.servers = servers
        self.config_path = config_path
        self.workspace_gate = WorkspaceGate()
        self._admin_lock = threading.Lock()
        self._workspace_close_lock = threading.Lock()
        self.state = self._new_state(workspace)
        self.page_timeout = page_timeout
        self.grace_s = SHUTDOWN_GRACE_S               # tests lower it; the tutorial's Exit uses it for its bounded waits
        self.index = INDEX.read_bytes()
        if self.authority:
            self.index = surfaces.workshop_page(self.index)
        self.assets = {route: (path.read_bytes(), ctype) for route, (path, ctype) in ASSETS.items()}
        super().__init__(("127.0.0.1", port), Handler)

    def server_bind(self) -> None:
        # HTTPServer.server_bind resolves the hostname, which can stall even for loopback.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    @property
    def url(self) -> str:
        if self.authority:
            return surfaces.origin(self)
        return f"http://127.0.0.1:{self.server_address[1]}/?t={self.token}"

    def handle_error(self, request, client_address) -> None:  # last resort; never prints messages
        exc = sys.exc_info()[1]
        if exc is not None:
            report_internal("connection", exc)

    def _new_state(self, workspace: Workspace) -> ProcessState:
        state = ProcessState(workspace, self.request_limit, profiles_dir=self.profiles_dir,
                             mcp_servers_dir=self.mcp_servers_dir, servers=self.servers,
                             config_path=self.config_path)
        state.chat_surfaces = self.authority is not None
        if self.authority is not None:
            try:
                state.starter_install_failures = state.block_library.install_chat_starters()
            except BaseException:
                state.shutdown()
                raise
        return state

    @property
    def workspace_admin_enabled(self) -> bool:
        """Tutorial child servers must never switch or reset their isolated workspace."""
        return self.tutorial_session is None

    def _workspace_view(self) -> dict:
        return {"name": self.workspace.name, "path": str(self.workspace.path)}

    def _protected_paths(self) -> tuple[Path, Path]:
        return self.profiles_dir, self.mcp_servers_dir

    def _active_work(self) -> list[dict]:
        state = self.state
        active = []
        for kind, count in (
                ("page_attempt", getattr(state.budget, "in_flight", 0)),
                ("block_attempt", state.store.running_count()),
                ("block_run", len(state.store.active_block_runs())),
                ("mcp_operation", state.mcp_ops.active_count()),
                ("python", state.python.in_flight),
                ("agent_run", 1 if state.agent.active is not None else 0)):
            if count:
                active.append({"kind": kind, "count": count})
        outstanding = state.writers.outstanding()
        if outstanding:
            active.append({"kind": "writer", "count": len(outstanding)})
        if state.workshop.active:
            active.append({"kind": "workshop_batch", "count": 1})
        return active

    def _require_admin(self) -> None:
        if not self.workspace_admin_enabled:
            raise workspace_admin.WorkspaceAdminError("workspace_admin_disabled",
                                                      "workspace administration is unavailable in the tutorial", 409)

    def _require_tutorial_idle(self) -> None:
        if self.tutorial.active():
            raise workspace_admin.WorkspaceAdminError("tutorial_active", "exit the tutorial before changing workspaces", 409)

    def _require_idle(self) -> None:
        active = self._active_work()
        if active:
            raise workspace_admin.WorkspaceAdminError(
                "workspace_busy", "stop running requests and Workshop batches before changing workspaces", 409,
                active=active)

    def _require_no_private_chat(self) -> None:
        agent = self.state.agent
        with agent.lock:
            if agent.conversation is not None and agent.conversation.private:
                raise workspace_admin.WorkspaceAdminError(
                    "private_close_confirmation",
                    "Download any drafts you want to keep, then close the private chat in AgentWiz Chat "
                    "before switching or resetting the workspace.",
                    409)

    def _discard_state(self, state: ProcessState | None, workspace: Workspace) -> None:
        if state is not None:
            try:
                state.shutdown(grace_s=self.grace_s, wait=True)
            except Exception as exc:  # noqa: BLE001 - the new state was never served
                report_internal("workspace", exc)
        workspace.close()

    def close_workspace(self) -> None:
        """Close the currently open workspace's lock. Idempotent; callers use it after a drained shutdown."""
        with self._workspace_close_lock:
            workspace, self.workspace = self.workspace, None
        if workspace is not None:
            workspace.close()


    def switch_workspace(self, name: str, *, create: bool = False) -> dict:
        """Open and adopt another workspace in the same root, or create and adopt a new one."""
        self._require_admin()
        self._require_tutorial_idle()
        if not create and name == self.workspace.name:
            return self._workspace_view()
        if create:
            workspace_admin.validate_new(self.workspace_root, name)
        else:
            workspace_admin.validate_existing(self.workspace_root, name)
        self._require_idle()
        self._require_no_private_chat()
        try:
            target_ws = wsmod.open_workspace(self.workspace_root, name)
        except wsmod.WorkspaceInUse as exc:
            raise workspace_admin.WorkspaceAdminError(
                "workspace_in_use", "the workspace is in use by another AgentWiz process", 409,
                holder=exc.holder) from None
        except wsmod.WorkspaceError:
            raise workspace_admin.WorkspaceAdminError(
                "workspace_io_error", "the workspace could not be opened", 500) from None
        if create and not target_ws.created:
            target_ws.close()
            raise workspace_admin.WorkspaceAdminError("workspace_exists", "a workspace with that name appeared", 409)
        new_state = None
        swapped = False
        try:
            try:
                new_state = self._new_state(target_ws)
            except Exception:
                raise workspace_admin.WorkspaceAdminError(
                    "workspace_unavailable", "the destination workspace could not be opened", 503) from None
            with self.workspace_gate.admin():
                if self._active_work():
                    self._require_idle()
                # A Chat request may have made the conversation private while the target was opening.
                self._require_no_private_chat()
                old_state, old_ws = self.state, self.workspace
                old_state.shutdown(grace_s=self.grace_s, wait=True)
                old_ws.close()
                self.state, self.workspace = new_state, target_ws
                new_state = None
                swapped = True
                self.generation_changed()
            return self._workspace_view()
        finally:
            if not swapped:
                self._discard_state(new_state, target_ws)
                if create and target_ws.created:
                    try:
                        workspace_admin.delete_workspace(self.workspace_root, name, protected=self._protected_paths())
                    except Exception:  # noqa: BLE001 - best effort for a workspace this call just created
                        pass

    def reset_workspace_by_name(self, name: str) -> dict:
        """Reset a workspace; resetting the current one rebuilds its ProcessState in place."""
        self._require_admin()
        self._require_tutorial_idle()
        path = workspace_admin.validate_existing(self.workspace_root, name)
        protected = self._protected_paths()
        workspace_admin.check_protected(path, protected)
        if name != self.workspace.name:
            try:
                with wsmod.acquire_existing_lock(self.workspace_root, name):
                    workspace_admin.reset_contents(path, self.profiles_dir, protected)
            except wsmod.WorkspaceInUse as exc:
                raise workspace_admin.WorkspaceAdminError(
                    "workspace_in_use", "the workspace is in use by another AgentWiz process", 409,
                    path=str(path), holder=exc.holder) from None
            except wsmod.WorkspaceError:
                raise workspace_admin.WorkspaceAdminError(
                    "workspace_io_error", "the workspace could not be locked for reset", 500,
                    path=str(path)) from None
            return {"name": name, "path": str(path), "reset": True}

        self._require_idle()
        with self.workspace_gate.admin():
            if self._active_work():
                self._require_idle()
            self._require_no_private_chat()
            old_state = self.state
            old_state.shutdown(grace_s=self.grace_s, wait=True)
            try:
                workspace_admin.reset_contents(path, self.profiles_dir, protected)
                self.workspace.problems = []
                self.workspace.read_only = False
                new_state = self._new_state(self.workspace)
            except workspace_admin.WorkspaceAdminError:
                raise
            except Exception:
                raise workspace_admin.WorkspaceAdminError(
                    "workspace_unavailable", "the workspace was reset but could not be reopened; restart AgentWiz",
                    503, path=str(path)) from None
            self.state = new_state
            self.generation_changed()
        return {"name": name, "path": str(path), "reset": True}

    def clone_workspace_from(self, source: str, target: str, include_evidence: bool = False) -> dict:
        self._require_admin()
        self._require_tutorial_idle()
        workspace_admin.validate_existing(self.workspace_root, source)
        workspace_admin.validate_new(self.workspace_root, target)
        if source != self.workspace.name:
            try:
                with wsmod.acquire_existing_lock(self.workspace_root, source):
                    return workspace_admin.clone_workspace(self.workspace_root, source, target, include_evidence)
            except wsmod.WorkspaceInUse as exc:
                raise workspace_admin.WorkspaceAdminError(
                    "workspace_in_use", "the source workspace is in use by another AgentWiz process", 409,
                    holder=exc.holder) from None
            except wsmod.WorkspaceError:
                raise workspace_admin.WorkspaceAdminError(
                    "workspace_io_error", "the source workspace could not be locked", 500) from None
        self._require_idle()
        with self.workspace_gate.admin():
            if self._active_work():
                self._require_idle()
            return workspace_admin.clone_workspace(self.workspace_root, source, target, include_evidence)

    def delete_workspace_by_name(self, name: str) -> dict:
        self._require_admin()
        self._require_tutorial_idle()
        return workspace_admin.delete_workspace(self.workspace_root, name, current=self.workspace.name,
                                                protected=self._protected_paths())

    def server_close(self, *, wait: bool = True) -> list[str]:
        """Close the listening socket and shut the process state down (10.10): the tutorial's session first
        (bounded), then admission, cancellation and the drain. Returns the labels still outstanding (empty once
        drained; non-empty only with `wait=False`). Idempotent: a drained state answers at once.

        The workspace lock is not closed here. The owner calls close_workspace() after a drained shutdown;
        a workspace switch closes the old lock itself only after its state has drained.
        """
        with self._admin_lock:
            self.workspace_gate.close()
            pending = self.tutorial.begin_shutdown(take_pending=wait)
            super().server_close()
            left = self.state.shutdown(grace_s=self.grace_s, wait=wait)
        self.tutorial.finish(pending)             # a pending tutorial session, only after this state has drained
        return left


def _optional_str(body: dict, name: str) -> str:
    value = body.get(name)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise BodyError(f"{name} must be a string")
    return value


def _profile_name(body: dict, allow_null: bool = False) -> str | None:
    value = body.get("name")
    if isinstance(value, str) or (value is None and allow_null and "name" in body):
        return value
    raise BodyError("name must be a string" + (" or null" if allow_null else ""))


def _revision(body: dict) -> int:
    value = body.get("revision")
    if not isinstance(value, int) or isinstance(value, bool):
        raise BodyError("revision is required and must be an integer")
    return value


class _Delivery:
    """One page connection's event delivery, which shutdown can interrupt (10.10, step 2): further writes are
    dropped and a write blocked on a page that stopped reading ends because the socket is shut down."""

    def __init__(self, handler: "Handler"):
        self.handler = handler
        self._lock = threading.Lock()
        self.gone = False

    def interrupt(self) -> None:
        with self._lock:
            self.gone = True
        try:
            socket.socket.shutdown(self.handler.connection, socket.SHUT_RDWR)
        except OSError:
            pass


class BlockDelivery(_Delivery, runner.Observer):
    """The HTTP observer of a block run (10.9). Every write is best effort and bounded by the page timeout; after
    the first failure it marks itself gone and drops every later event without touching the socket. It never
    raises into the runner and never cancels anything."""

    def __init__(self, handler: "Handler"):
        super().__init__(handler)
        self.head_sent = False
        self.run_id: str | None = None

    def _write(self, event: dict) -> None:
        with self._lock:
            if self.gone:
                return
        try:
            if not self.head_sent:
                self.head_sent = True
                self.handler.send_response(200)
                self.handler.send_header("Content-Type", "application/x-ndjson")
                self.handler.send_header("Cache-Control", "no-store")
                self.handler.end_headers()
            self.handler.write_event(event)
        except OSError:                                      # includes TimeoutError
            with self._lock:
                self.gone = True

    def started(self, event: dict) -> None:
        self.run_id = event.get("run_id")
        # Registered only now: a request refused before it had a run must still be able to send its 503. If
        # shutdown has already begun, this interrupts the connection at once, which is what a started run needs.
        self.handler.server.state.add_delivery(self)
        self._write(event)

    def attempt_committed(self, record: dict) -> None:
        check = record.get("check")
        self._write({"type": "attempt", "run_id": record["run_id"], "attempt_id": record["id"],
                     "attempt": record["attempt"], "outcome": record["outcome"],
                     "check": check.get("overall") if isinstance(check, dict) else None,
                     "repair_reason": record.get("repair_reason")})

    def operation_started(self, kind: str) -> None:
        self._write({"type": "operation", "run_id": self.run_id, "kind": kind, "state": "running"})

    def final(self, event: dict) -> None:
        self._write(event)

    def saved(self, event: dict) -> None:
        self._write(event)


class AgentDelivery(_Delivery, agent_loop.Observer):
    """The HTTP observer of an agent run: best effort, bounded by the page timeout. After the first failure it
    drops every later event; the run continues, and its state stays readable by run ID (4.8)."""

    def __init__(self, handler: "Handler"):
        super().__init__(handler)
        self.head_sent = False

    def event(self, event: dict) -> None:
        with self._lock:
            if self.gone:
                return
        try:
            if not self.head_sent:
                self.head_sent = True
                self.handler.send_response(200)
                self.handler.send_header("Content-Type", "application/x-ndjson")
                self.handler.send_header("Cache-Control", "no-store")
                self.handler.end_headers()
            self.handler.write_event(event)
        except OSError:
            with self._lock:
                self.gone = True


class Handler(BaseHTTPRequestHandler):
    server: UIServer
    protocol_version = "HTTP/1.0"  # each response closes its connection, which delimits the stream

    def setup(self) -> None:
        self._nonce = secrets.token_urlsafe(24)
        self._base_url = ""  # endpoint named in discovery error descriptions
        self.timeout = self.server.page_timeout  # R0-13: bounds every read and write on this connection
        super().setup()

    def log_message(self, *args: Any) -> None:
        pass

    # --- plumbing ----------------------------------------------------------------------------
    def _send(self, status: int, payload: Any, ctype: str = JSON) -> None:
        data = payload if isinstance(payload, bytes) else jsonio.encode(payload)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def parse_request(self):
        if not super().parse_request():
            return False
        authority = self.server.authority
        if authority is None:
            return True
        expected = surfaces.origin(self.server)
        host = expected.removeprefix("http://")
        origins = self.headers.get_all("Origin", [])
        if (self.headers.get_all("Host", []) != [host] or (origins and origins != [expected])
                or not self.path.startswith("/") or self.path.startswith("//")):
            self._send(403, {"code": "origin_refused"})
            return False
        path = urlparse(self.path).path
        if self.command == "GET" and (path == "/" or path == "/assets/session.js" or path in self.server.assets):
            return True
        if self.command == "POST" and path == "/api/session/bootstrap" and origins == [expected]:
            return True
        if surfaces.permitted(authority.surface, self.command, path) and self._authorised():
            return True
        self._send(403, {"code": "surface_refused"})
        return False

    def end_headers(self):
        if self.server.authority is not None:
            self.send_header("Content-Security-Policy",
                             f"default-src 'none'; script-src 'self' 'nonce-{self._nonce}'; "
                             "style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; "
                             "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.send_header("X-Frame-Options", "DENY")
            if self.server.authority.surface == "chat":
                self.send_header("X-DNS-Prefetch-Control", "off")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def _authorised(self) -> bool:
        authority = self.server.authority
        if authority is not None:
            return authority.allows(self.headers.get("X-Session-Token", ""))
        supplied = self.headers.get("X-Session-Token")
        if supplied is None:
            supplied = parse_qs(urlparse(self.path).query).get("t", [""])[0]
        return hmac.compare_digest(supplied.encode("utf-8", "surrogateescape"), self.server.token.encode("ascii"))

    def _length(self, limit: int, remedy: str = "") -> int | None:
        length = self.headers.get("Content-Length")
        if length is None:
            return None
        if not (length.isascii() and length.isdigit()):
            raise BodyError("Content-Length must be a non-negative integer")
        if int(length) > limit:
            raise TooLarge(int(length), limit, remedy)
        return int(length)

    def _body(self, remedy: str = "") -> dict:
        length = self._length(insertion.REQUEST_LIMIT, remedy)
        if length is None:
            return {}
        length = str(length)
        try:
            raw = self.rfile.read(int(length))
        except OSError:
            raise _ReadTimeout() from None
        if len(raw) < int(length):
            raise _ReadTimeout()
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            raise BodyError("request body must be a JSON object") from None
        if not isinstance(body, dict):
            raise BodyError("request body must be a JSON object")
        return body

    def _dispatch(self, route: str, fn: Callable[[], None]) -> None:
        """Run a route, mapping every failure to a reply that cannot contain a credential."""
        try:
            fn()
        except agent_loop.AgentError as exc:     # fixed codes and AgentWiz's own fields, never a value
            self._reply_safely(exc.status, {"error": "agentic Chat request refused", "code": exc.code, **exc.extra})
        except workshop.WorkshopError as exc:
            self._reply_safely(exc.status, {"error": "Workshop request refused", "code": exc.code, **({"issues": exc.issues} if exc.issues else {}), **({"plan": exc.plan} if hasattr(exc, "plan") else {})})
        except library_install.LibraryError as exc:
            self._reply_safely(exc.status, {"error": str(exc), "code": exc.code})
        except workspace_admin.WorkspaceAdminError as exc:
            payload = {"error": str(exc), "code": exc.code}
            for key in ("path", "holder", "active", "limit", "cleanup"):
                value = getattr(exc, key, None)
                if value is not None:
                    payload[key] = value
            self._reply_safely(exc.status, payload)
        except _ReadTimeout:
            self.close_connection = True
        except TooLarge as exc:
            self.close_connection = True
            self._reply_safely(413, {"error": str(exc), "code": "too_large", "size": exc.size, "limit": exc.limit})
        except LengthRequired:
            self.close_connection = True
            self._reply_safely(411, {"error": "Content-Length is required", "code": "length_required"})
        except FilenameError as exc:
            self._reply_safely(400, {"error": f"{exc}", "code": "bad_filename"})
        except (attachments.AttachmentError, pdf_reader.AttachmentError) as exc:  # reader text, never file content
            self._reply_safely(getattr(exc, "status", 400), {"error": str(exc), "code": exc.code})
        except TaskSettingError as exc:  # AgentWiz's text or a modelSpell pre-send message
            self._reply_safely(400, {"error": f"TaskSettingError: {exc}", "code": "task_setting"})
        except BlockBodyError as exc:
            self._reply_safely(400, {"error": f"BlockBodyError: {exc}", "code": "block_body"})
        except (BodyError, SettingsError) as exc:
            self._reply_safely(400, {"error": f"{type(exc).__name__}: {exc}"})
        except StaleRevision:
            self._reply_safely(409, {"error": "settings were changed elsewhere", "code": "stale_revision"})
        except DefaultConflict:
            self._reply_safely(409, {"error": "confirm replacing the default profile", "code": "default_conflict"})
        except ReadOnly as exc:  # names a file, a JSON location and a rule; never a value
            self._reply_safely(409, {"error": str(exc), "code": "read_only"})
        except BadArguments as exc:
            self._reply_safely(400, {"error": f"BadArguments: {exc}", "code": "bad_arguments"})
        except (mcp_ops.Busy, ShuttingDown, TutorialBusy, SettingsHeld) as exc:
            self._reply_safely(503, {"error": str(exc), "code": "busy"})
        except runner.Busy as exc:  # names the budget (15.1)
            self._reply_safely(503, {"error": f"{exc.limit} {exc.budget} are still running or stopping. "
                                              "Wait for one to finish.", "code": "busy", "budget": exc.budget})
        except BlockUnusable as exc:  # the problem names files, locations and rules, never values
            self._reply_safely(409, {"error": str(exc), "code": "block_unusable", "problem": exc.problem.view()})
        except runs.NoSuchRunInMemory:
            self._reply_safely(404, {"error": "no such run", "code": "no_such_run"})
        except RoleUnresolved:
            self._reply_safely(409, {"error": "Select a usable role binding for this mode.", "code": "role_unavailable"})
        except RoleProfileMissing as exc:  # names the role, never the profile
            self._reply_safely(400, {"error": str(exc), "code": "no_such_profile", "role": exc.role})
        except NoSuchBlock:
            self._reply_safely(404, {"error": "no such block", "code": "no_such_block"})
        except NoSuchServer:
            self._reply_safely(404, {"error": "no such MCP server", "code": "no_such_server"})
        except mcp_ops.NoSuchOperation:
            self._reply_safely(404, {"error": "no such tool operation", "code": "no_such_operation"})
        except contracts.StoreError as exc:  # codes, locations and rules; contract names/values are not echoed
            self._reply_safely(exc.status, {"format_version": 2, "error": exc.message, **exc.body()})
        except model_step.SnapshotUnavailable as exc:
            self._reply_safely(409, {"error": "the captured contract snapshot cannot be restored for execution",
                                     "code": exc.code})
        except contract_schema.DefinitionError as exc:
            code = exc.issues[0].get('code', 'contract_invalid') if exc.issues else 'contract_invalid'
            self._reply_safely(400, {'code': code, 'message': 'the contract definition or edit is invalid',
                                     'issues': exc.issues, 'issue_count': exc.total})
        except (contract_service.ValidationUnavailable, checks.ValidationUnavailable):
            self._reply_safely(503, {'code': 'validation_unavailable',
                                     'message': 'repair the AgentWiz validator installation'})
        except runs.NoSuchAttempt:
            self._reply_safely(404, {"error": "no such attempt", "code": "no_such_attempt"})
        except runs.NotHeld:
            self._reply_safely(410, {"error": "This outcome is no longer held.", "code": "not_held"})
        except runs.RunActive:
            self._reply_safely(409, {"error": "This run is still active; try again when it has finished.",
                                     "code": "run_active"})
        except history.NoSuchRun:
            self._reply_safely(404, {"error": "no such run", "code": "no_such_run"})
        except history.NotDeletable:
            self._reply_safely(409, {"error": "that run's folder cannot be deleted from here", "code": "not_deletable"})
        except history.HistoryReadOnly:
            self._reply_safely(409, {"error": "the workspace is read-only", "code": "read_only"})
        except history.DeleteFailed as exc:  # AgentWiz's text plus the system's error text
            self._reply_safely(500, {"error": str(exc), "code": "delete_failed"})
        except NoSuchProfile:  # profile names are not echoed
            self._reply_safely(404, {"error": "no such profile", "code": "no_such_profile"})
        except WriteFailed as exc:  # AgentWiz's text plus an OS strerror
            self._reply_safely(500, {"error": str(exc), "code": "write_failed"})
        except ModelSpellError as exc:
            self._reply_safely(400, {"error": describe_error(exc, self._base_url)})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as exc:  # noqa: BLE001 - deliberately broad: messages may carry keys
            report_internal(route, exc)
            self._reply_safely(500, {"error": f"InternalError: {type(exc).__name__}", "code": "internal"})

    def _reply_safely(self, status: int, payload: dict) -> None:
        try:
            self._send(status, payload)
        except OSError:
            self.close_connection = True

    def _reply_workspace_error(self, exc: workspace_admin.WorkspaceAdminError) -> None:
        payload = {"error": str(exc), "code": exc.code}
        for key in ("path", "holder", "active", "limit", "cleanup"):
            value = getattr(exc, key, None)
            if value is not None:
                payload[key] = value
        self._reply_safely(exc.status, payload)

    def _dispatch_gated(self, path: str, fn: Callable[[], None]) -> None:
        try:
            with self.server.workspace_gate.shared():
                self._dispatch(path, fn)
        except workspace_admin.WorkspaceAdminError as exc:
            self._reply_workspace_error(exc)

    # --- routes ------------------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if _is_workspace_admin_path(path):
            self._dispatch(path, lambda: self._get(path))
        else:
            self._dispatch_gated(path, lambda: self._get(path))

    def _get(self, path: str) -> None:
        if self.server.authority is not None:
            if path == "/":
                html = self.server.index.replace(b"<script>", f'<script nonce="{self._nonce}">'.encode())
                return self._send(200, html, "text/html; charset=utf-8")
            if path == "/assets/session.js":
                return self._send(200, (surfaces.HERE / "session.js").read_bytes(), "text/javascript; charset=utf-8")
            if path in self.server.assets:
                return self._send(200, *self.server.assets[path])
        if not self._authorised():
            return self._send(403, {"error": "missing or invalid session token"})
        if path == "/":
            return self._send(200, self.server.index, "text/html; charset=utf-8")
        if path in self.server.assets:
            return self._send(200, *self.server.assets[path])
        if path == "/api/chat/settings":
            return self._send(200, self._chat_settings())
        if path == "/api/chat/history":
            return self._send(200, {"chats": self.server.state.agent.chat_history.listing()})
        if path == WORKSPACES_ROUTE:
            return self._workspaces_get()
        if path == WORKSPACES_ROUTE + "/launch":
            return self._workspaces_launch()
        if path.startswith("/api/workshop/") and not path.startswith("/api/workshop/tickets/"):
            return self._workshop_get(path[len("/api/workshop/"):])
        if path.startswith("/api/workshop/tickets/"):
            return self._send(200, self.server.state.workshop_ticket_status(path[len("/api/workshop/tickets/"):]))
        if path == "/api/state":
            return self._send(200, self._state_body(self.server.state.view()))
        if path == "/api/tutorial":
            session = self.server.tutorial_session
            return self._send(200, session.info() if session else {"active": False})
        if path == "/api/contracts":
            return self._send(200, self.server.state.contracts_listing())
        if path.startswith("/api/contracts/"):
            query = parse_qs(urlparse(self.path).query)
            revision = None
            if set(query) - {"revision"}:
                raise contracts.StoreError("invalid", 400, "unknown query parameter")
            if "revision" in query:
                text = query["revision"]
                if len(text) != 1 or not re.fullmatch(r"[1-9][0-9]{0,8}", text[0]):
                    raise contracts.StoreError("invalid", 400, "revision must be a positive integer")
                revision = int(text[0])
            return self._send(200, self.server.state.contract_detail(unquote(path[len("/api/contracts/"):]), revision))
        if path == "/api/blocks":
            query = parse_qs(urlparse(self.path).query)
            listing = self.server.state.block_listing(reload=query.get("reload") == ["1"])
            return self._send(200, {**listing, "active_runs": self.server.state.store.active_block_runs()})
        if path.startswith("/api/blocks/"):
            return self._send(200, self.server.state.block_detail(self._block_name(path[len("/api/blocks/"):])))
        if path.startswith("/api/attempts/"):
            return self._attempt_poll(path[len("/api/attempts/"):])
        if path.startswith("/api/block_runs/"):
            return self._block_run_poll(path[len("/api/block_runs/"):])
        if path.startswith("/api/agent/"):
            return self._agent_get(path[len("/api/agent/"):])
        if path == "/api/library":
            return self._send(200, self.server.state.block_library.listing())
        if path == "/api/runs":
            return self._send(200, self.server.state.history.list_runs())
        if path.startswith("/api/runs/"):
            return self._send(200, self.server.state.history.read_run(self._run_id(path[len("/api/runs/"):])))
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if _is_workspace_admin_path(path):
            self._dispatch(path, lambda: self._post(path))
        else:
            self._dispatch_gated(path, lambda: self._post(path))

    def _post(self, path: str) -> None:
        if path == "/api/session/bootstrap" and self.server.authority is not None:
            body = self._body()
            result = self.server.authority.exchange(body.get("bootstrap"))
            return self._send(200 if result else 403, {"session": result} if result else {"code": "bootstrap_expired"})
        if not self._authorised():
            return self._send(403, {"error": "missing or invalid session token"})
        if path == "/api/chat/bindings":
            body = self._body()
            self.server.state.save_mode_bindings(body.get("bindings"), _revision(body))
            return self._send(200, self._chat_settings())
        if path == "/api/chat/tools":
            return self._send(200, self.server.state.save_chat_preferences(self._body()))
        if path == "/api/chat/webspell":
            return self._webspell_discover(self._body())
        if path == "/api/chat/models":
            return self._chat_models(self._body())
        if path.startswith("/api/chat/history/"):
            return self._chat_history_post(path, self._body())
        if path.startswith(WORKSPACES_ROUTE + "/"):
            return self._workspaces_post(path)
        if path.startswith("/api/workshop/") and path != "/api/workshop/tickets":
            return self._workshop_post(path[len("/api/workshop/"):], self._body())
        if path in ("/api/library/plan", "/api/library/add"):
            body = self._body()
            if body.get("format_version") != 1 or set(body) != {"format_version", "entries"}:
                raise BodyError("format_version must be 1 and the only other member is entries")
            installer = self.server.state.block_library
            return self._send(200, installer.plan(body) if path.endswith("/plan") else installer.add(body))
        routes = {"/api/workshop/tickets": self._workshop_ticket, "/api/tutorial/start": self._tutorial_start,
                  "/api/tutorial/step": self._tutorial_step,
                  "/api/tutorial/exit": self._tutorial_exit,
                  "/api/config": self._config, "/api/openrouter-key": self._openrouter_key, "/api/models": self._models,
                  "/api/providers": self._providers, "/api/complete": self._complete,
                  "/api/complete/stop": self._complete_stop,
                  "/api/history/switch": self._history_switch,
                  "/api/runs/delete": self._runs_delete,
                  "/api/profiles/save": self._profile_save, "/api/profiles/select": self._profile_select,
                  "/api/profiles/delete": self._profile_delete,
                  "/api/profiles/update": self._profile_update,
                  "/api/task_settings/save": self._task_save, "/api/roles/save": self._roles_save, "/api/blocks/run": self._block_run,
                  "/api/blocks/stop": self._block_stop,
                  "/api/contracts/save": self._contract_save, "/api/contracts/delete": self._contract_delete,
                  "/api/contracts/check": self._contract_check, "/api/contracts/family": self._contract_family,
                  "/api/contracts/restore": self._contract_restore,
                  "/api/contracts/project": self._contract_project, "/api/contracts/edit": self._contract_edit,
                  "/api/contracts/synthesis/plan": self._synthesis_plan,
                  "/api/contracts/synthesis/start": self._synthesis_start,
                  "/api/contracts/synthesis/apply": self._synthesis_apply,
                  "/api/mcp/servers": self._mcp_servers, "/api/mcp/token": self._mcp_token,
                  "/api/mcp/tools": self._mcp_tools, "/api/mcp/call": self._mcp_call,
                  "/api/mcp/cancel": self._mcp_cancel}
        if path == AGENT_UPLOAD_ROUTE:
            return self._agent_upload()
        if path.startswith("/api/agent/"):
            return self._agent_post(path[len("/api/agent/"):], self._body())
        if path == UPLOAD_ROUTE:
            return self._upload()
        body = self._body(" Copy a smaller part of the file." if path == "/api/complete" else "")
        handler = routes.get(path)
        if handler is None:
            return self._send(404, {"error": "not found"})
        handler(body)

    def _chat_settings(self) -> dict:
        from .. import chat_tasks, roles, webspell
        state = self.server.state
        view = state.view()
        bindings = {}
        for role, (name, _) in roles.CHAT_MODELS.items():
            slots = {}
            for mode in ("cloud", "local"):
                try:
                    snapshot = state.chat_snapshot(view["revision"], role, mode)
                    trusted = agent_loop._trusted(state, snapshot, view["revision"], mode, role)
                    slots[mode] = {"model": snapshot.model, "profile": snapshot.profile_name,
                                   "kind": snapshot.kind, "base_url": snapshot.endpoint.base_url,
                                   "routing_preset": snapshot.routing_preset,
                                   "trusted": bool(trusted), "provider": snapshot.provider,
                                   "routing_mode": snapshot.routing_mode, "zdr": snapshot.routing_zdr,
                                   "data_collection": snapshot.routing_data_collection}
                except (RoleUnresolved, StaleRevision):
                    slots[mode] = None
            bindings[role] = {"name": name, **slots}
        try:
            snap = state.mcp_snapshot_for_block(webspell.SERVER)
            web_status = "ready" if webspell.compatible(state.mcp_client, snap) else "verify_connection"
        except NoSuchServer:
            web_status = "configure_in_workshop"
        profile_details = view["profiles"].get("details", {})
        return {"revision": view["revision"], "binding_revision": view["roles"]["revision"], "roles": bindings,
                "agentwiz_version": __version__, "modelspell_version": modelspell.__version__,
                "bindings": view["roles"]["mode_bindings"], "tools": state.chat_preferences(),
                "chat_tasks": [{"id": key, "label": name} for key, (name, _) in chat_tasks.TASKS.items()],
                "profiles": {name: details.get("kind") for name, details in profile_details.items()},
                "reasoning_levels": {kind: [level for level in REASONING_LEVELS[1:]
                                             if self._reasoning_supported(kind, level)]
                                     for kind in model_call.KINDS},
                "key_source": view["openrouter_key_source"], "webspell": web_status,
                "starter_install_failures": state.starter_install_failures,
                "workshop_url": self.server.owner.url}

    def _chat_models(self, body):
        kind, mode = body.get("kind"), body.get("mode")
        if (set(body) != {"kind", "mode"} or kind not in model_call.KINDS
                or mode != ("cloud" if kind == "openrouter" else "local")):
            raise BodyError("choose a model source for the active mode")
        state = self.server.state
        with state.agent.idle_reservation(cloud_discovery=mode == "cloud", discovery=True):
            endpoint = state.discovery_endpoint(kind, None, None)
            self._base_url = endpoint.base_url
            grants = []
            if mode == "local":
                try:
                    url = outbound_policy.canonical_url(endpoint.base_url)
                    # Discovery itself can be pointed only at an unproxied, literal loopback endpoint.
                    target = {"kind": kind, "url": url}
                    outbound_policy.decide(target, [target])
                except outbound_policy.Refused:
                    raise agent_loop.AgentError("destination_refused", 409) from None
                try:
                    _, grants = outbound_policy.read(state.workspace.path)
                except outbound_policy.Refused:
                    pass
            entries = []
            for info in modelspell.list_models(endpoint, timeout=5):
                if not info.id.isprintable() or len(info.id) > 4096:
                    continue
                levels = [] if info.supports_reasoning is False else [
                    level for level in REASONING_LEVELS[1:]
                    if (info.reasoning_options is None or level in info.reasoning_options)
                    and self._reasoning_supported(kind, level)]
                trusted = (mode == "local" and
                           {"kind": kind, "url": url, "model": info.id, "provider": ""} in grants)
                entries.append({"id": info.id, "name": info.name, "trusted": trusted, "reasoning": levels})
        self._send(200, {"models": entries})

    @staticmethod
    def _reasoning_supported(kind, level):
        try:
            modelspell.check_settings(kind, reasoning=level)
        except ModelSpellError:
            return False
        return True

    def _chat_history_post(self, path, body):
        manager = self.server.state.agent
        if path == "/api/chat/history/save" and set(body) == {"conversation", "enabled"}:
            return self._send(200, manager.change_chat_save(body["conversation"], body["enabled"]))
        if path == "/api/chat/history/open" and set(body) == {"id", "close_private"} and isinstance(body["id"], str):
            return self._send(200, manager.start_conversation(require_confirmation=True, saved_id=body["id"],
                                                             close_private=body["close_private"]))
        if path == "/api/chat/history/rename" and set(body) == {"id", "title"}:
            manager.rename_saved_chat(body["id"], body["title"])
            return self._send(200, {"renamed": True})
        if path == "/api/chat/history/delete" and set(body) == {"id"}:
            manager.delete_saved_chat(body["id"])
            return self._send(200, {"deleted": True})
        raise BodyError("invalid Chat history action")

    def _webspell_discover(self, body):
        from .. import webspell
        state = self.server.state
        conversation = state.agent.view()["conversation"]
        if (body != {"mode": "cloud"} or (conversation and conversation["private"])
                or not state.chat_preferences()["webspell"]):
            return self._send(409, {"code": "destination_refused"})
        snap = state.mcp_snapshot_for_block(webspell.SERVER)
        client = state.mcp_client
        with state.agent.public_discovery():
            op = state.mcp_ops.start("discovery", snap, lambda ctx: client.discover(ctx, snap))
            committed = self._wait_committed(op)
        if not committed or "error" in committed or not webspell.compatible(client, snap):
            return self._send(409, {"code": "webspell_unavailable"})
        return self._send(200, {"status": "ready"})

    def _state_body(self, view: dict) -> dict:
        from .. import python_step
        out = {**view, "agentwiz_version": __version__, "modelspell_version": modelspell.__version__,
               "scriptspell_version": python_step.version(),
               "limits": {"request_bytes": insertion.REQUEST_LIMIT, "upload_bytes": UPLOAD_LIMIT}}
        out["workspace"] = {**view["workspace"], "admin": self.server.workspace_admin_enabled}
        return out

    def _workspaces_get(self) -> None:
        info = workspace_admin.list_workspaces(self.server.workspace_root, self.server.workspace.name)
        info.update({"format_version": 1, "admin": self.server.workspace_admin_enabled,
                     "tutorial_active": self.server.tutorial.active()})
        self._send(200, info)

    def _workspaces_launch(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        name = query.get("name", [self.server.workspace.name])[0]
        port = None
        if "port" in query:
            try:
                port = int(query["port"][0])
            except (TypeError, ValueError):
                raise workspace_admin.WorkspaceAdminError("invalid_workspace_name",
                                                          "port must be an integer from 0 to 65535", 400) from None
        command = workspace_admin.launch_command(self.server.workspace_root, name, self.server.profiles_dir,
                                                 self.server.mcp_servers_dir, port)
        self._send(200, {"format_version": 1, "command": command})

    def _workspaces_post(self, path: str) -> None:
        body = self._body()
        if type(body.get("format_version")) is not int or body.get("format_version") != 1:
            raise workspace_admin.WorkspaceAdminError("invalid_workspace_name", "format_version must be 1", 400)
        action = path[len(WORKSPACES_ROUTE) + 1:]
        name = body.get("name")
        if action in ("switch", "create", "reset", "delete"):
            if not isinstance(name, str):
                raise workspace_admin.WorkspaceAdminError("invalid_workspace_name", "name must be a string", 400)
        if action in ("reset", "delete") and body.get("confirm") != name:
            raise workspace_admin.WorkspaceAdminError("confirmation_required",
                                                      "type the workspace name exactly to confirm", 400)
        with self.server._admin_lock:
            if not self._authorised():
                return self._send(403, {"code": "surface_refused"})
            if action == "switch":
                result = self.server.switch_workspace(name)
            elif action == "create":
                result = self.server.switch_workspace(name, create=True)
            elif action == "clone":
                source, target = body.get("source"), body.get("target")
                evidence = body.get("include_evidence", False)
                if not isinstance(source, str) or not isinstance(target, str) or type(evidence) is not bool:
                    raise workspace_admin.WorkspaceAdminError("invalid_workspace_name",
                                                              "source and target must be names", 400)
                result = self.server.clone_workspace_from(source, target, evidence)
            elif action == "reset":
                result = self.server.reset_workspace_by_name(name)
            elif action == "delete":
                result = self.server.delete_workspace_by_name(name)
            else:
                return self._send(404, {"error": "not found", "code": "missing"})
        self._send(200, {"format_version": 1, "workspace": self.server._workspace_view(), **result})

    def _workshop_get(self, path):
        service = self.server.state.workshop
        parts = path.split('/')
        query = parse_qs(urlparse(self.path).query)
        if path == 'workflow-active':
            result = service.workflows.active_view()
        elif path == 'workflows':
            result = service.workflows.listing()
        elif len(parts) == 2 and parts[0] == 'workflows':
            result = service.workflows.read(parts[1])
        elif len(parts) == 2 and parts[0] == 'workflow-source-options':
            result = service.workflows.source_options(parts[1], query.get('revision', [None])[0])
        elif len(parts) == 2 and parts[0] == 'workflow-sources':
            result = service.workflows.source_fingerprint(parts[1])
        elif len(parts) == 2 and parts[0] == 'workflow-jobs':
            job = service.workflows.job(parts[1])
            result = job.detail(query['node'][0]) if query.get('node') else job.view()
        elif path == 'workflow-evidence':
            result = service.workflows.evidence()
        elif len(parts) == 2 and parts[0] == 'workflow-evidence':
            result = service.workflows.evidence(parts[1], query.get("record", [None])[0])
        elif path == 'workflow-mcp-confirmation':
            from ..workflow_settings import read
            result = read(self.server.state)
        elif path == 'destination-trust':
            from ..outbound_policy import view
            result = view(self.server.state)
        elif path == 'messages':
            from ..messages import catalogue
            result = catalogue()
        elif path == 'destination-trust-candidates':
            from ..outbound_policy import candidates
            result = candidates(self.server.state)
        elif path == 'starters':
            from ..workshop_editor import GROUPS, STARTERS, TRANSFORM_IDS, examples
            from ..agent_tools import REGISTRY
            result = {'format_version': 1, 'starters': [{'id': k, 'version': 2 if k in TRANSFORM_IDS else 1, 'examples': examples(k)} for k in STARTERS],
                      'groups': [{'id': ident, 'label': label, 'entries': list(entries)} for ident, label, entries in GROUPS],
                      'builtins': [{'id': u.name, 'title': u.name.replace('_', ' '), 'description': u.description}
                                   for u in REGISTRY.values()]}
        elif len(parts) == 3 and parts[0] == 'blocks' and parts[2] == 'revisions':
            result = service.assets.revisions(parts[1])
        elif len(parts) == 4 and parts[0] == 'blocks' and parts[2] == 'revisions':
            result = service.preview({'key': parts[1], 'revision': parts[3]})
        elif len(parts) == 3 and parts[0] == 'blocks' and parts[2] == 'examples':
            result = service.collection(parts[1])
        elif len(parts) == 3 and parts[0] == 'blocks' and parts[2] == 'retained-labels':
            result = service.retained_label_review(parts[1])
        elif len(parts) == 3 and parts[0] == 'blocks' and parts[2] == 'support':
            result = service.support(parts[1])
        elif len(parts) == 2 and parts[0] in ('batches', 'comparisons'):
            result = service.batch(parts[1])
        elif path == 'batches':
            result = {'active': service.active, 'latest': service.latest}
        elif path == 'comparisons':
            result = service.comparisons()
        else:
            return self._send(404, {'code': 'missing'})
        self._send(200, result)

    def _workshop_post(self, path, body):
        try:
            return self._workshop_post_checked(path, body)
        except (KeyError, TypeError, ValueError, checks.ContractError):
            raise workshop.WorkshopError('invalid', 400) from None

    def _workshop_post_checked(self, path, body):
        service = self.server.state.workshop
        if body.get('format_version') != 1 or type(body.get('format_version')) is not int:
            raise workshop.WorkshopError('invalid', 400)
        body = {k: v for k, v in body.items() if k != 'format_version'}
        parts = path.split('/')
        if path == 'workflow-definition':
            if set(body) != {'editor'}:
                raise workshop.WorkshopError('invalid', 400)
            result = {'definition_text': jsonio.encode_exact(service.workflows.editor_definition(body['editor']), indent=2)}
        elif path == 'workflow-validate':
            result = service.workflows.validate(body)
        elif path == 'destination-trust-preview':
            from ..outbound_policy import preview
            result = preview(self.server.state, body)
        elif path == 'workflow-mcp-confirmation':
            from ..workflow_settings import update
            result = update(self.server.state, body)
        elif len(parts) == 3 and parts[0] == 'workflow-evidence' and parts[2] == 'delete':
            if body:
                raise workshop.WorkshopError('invalid', 400)
            result = service.workflows.delete_evidence(parts[1])
        elif path == 'destination-trust':
            from ..outbound_policy import update
            result = update(self.server.state, body)
        elif path == 'publish-inline':
            result = service.publish_inline(body)
        elif path == 'input-value':
            from ..workshop_editor import input_value
            result = {'text':jsonio.encode_exact(input_value(body['value']))}
        elif path == 'render-value':
            from ..workshop_editor import typed
            result = typed(jsonio.decode_exact(body['text']))
        elif path == 'answer-view':
            from ..workshop_editor import typed
            _, value = checks.check_answer_value(checks.contract_from_text(body['contract_text']), body['text'])
            result = {'available': value is not checks.NO_VALUE}
            if result['available']:
                result.update(value=typed(value), text=jsonio.encode_exact(value))
        elif path == 'drafts/import':
            result = service.import_draft(body)
        elif path == 'preview':
            result = service.preview(body)
        elif path == 'plans':
            action = body.get('action')
            if isinstance(action, str) and action.startswith('workflow_'):
                result = service.workflows.plan(action, body.get('request', {}))
            else:
                result = service.plan(action, body.get('request', {}))
        elif len(parts) == 3 and parts[0] == 'workflows' and parts[2] in ('save', 'rebind', 'delete', 'run'):
            request = body.get('request')
            if not isinstance(request, dict) or request.get('name') != parts[1]:
                raise workshop.WorkshopError('invalid', 400)
            if parts[2] == 'run':
                result = service.workflows.start(body)
            else:
                result = service.workflows.edit('workflow_' + parts[2], request, body)
        elif len(parts) == 3 and parts[0] == 'workflow-jobs' and parts[2] == 'release':
            result = service.workflows.job(parts[1]).decide_release(body)
        elif len(parts) == 3 and parts[0] == 'workflow-jobs' and parts[2] == 'review':
            result = service.workflows.review(parts[1], body)
        elif len(parts) == 3 and parts[0] == 'workflow-jobs' and parts[2] == 'stop':
            result = service.workflows.stop(parts[1])
        elif path == 'save':
            result = service.save(body)
        elif path in ('test', 'batches'):
            result = service.start(path, body)
        elif path == 'assist':
            result = service.assist(body)
        elif len(parts) == 2 and parts[0] == 'python':
            # Python authoring (0.2.2, 10.7): Describe it, Generate examples, Edit as YAML, Generate and check, Apply.
            flows = service.python
            if parts[1] == 'start':
                result = flows.start(body)
            elif parts[1] in ('examples', 'generate', 'apply', 'stop') and set(body) == {'flow'}:
                result = {'examples': flows.generate_examples, 'generate': flows.generate_and_check,
                          'apply': flows.apply, 'stop': flows.stop}[parts[1]](body['flow'])
            elif parts[1] == 'accept' and set(body) == {'flow', 'text'}:
                result = flows.accept_examples(body['flow'], body['text'])
            else:
                return self._send(404, {'code': 'missing'})
        elif path == 'proposals/apply':
            result = service.apply(body)
        elif len(parts) == 3 and parts[0] == 'batches' and parts[2] == 'stop':
            result = service.stop(parts[1])
        elif len(parts) == 4 and parts[0] == 'blocks' and parts[2:] == ['support', 'preview']:
            result = service.support_preview(parts[1], body)
        else:
            return self._send(404, {'code': 'missing'})
        self._send(200, result)

    def do_PUT(self):
        path = urlparse(self.path).path
        def put():
            if not self._authorised():
                return self._send(403, {'error': 'missing or invalid session token'})
            parts = path.split('/')
            body = self._body()
            if body.pop('format_version', None) != 1:
                raise workshop.WorkshopError('invalid', 400)
            if len(parts) == 6 and parts[1:3] == ['api', 'workshop'] and parts[3] == 'blocks' and parts[5] == 'examples':
                return self._send(200, self.server.state.workshop.save_examples(parts[4], body))
            if len(parts) == 6 and parts[1:3] == ['api', 'workshop'] and parts[3] == 'blocks' and parts[5] == 'retained-labels':
                return self._send(200, self.server.state.workshop.save_retained_label_review(parts[4], body))
            if len(parts) == 8 and parts[1:4] == ['api', 'workshop', 'blocks'] and parts[5] == 'revisions' \
                    and parts[7] == 'description':
                return self._send(200, self.server.state.workshop.save_description(parts[4], parts[6], body))
            if len(parts) == 5 and parts[1:4] == ['api', 'workshop', 'reviews']:
                return self._send(200, self.server.state.workshop.review(parts[4], body))
            self._send(404, {'code': 'missing'})
        self._dispatch_gated(path, put)

    def do_DELETE(self):
        path = urlparse(self.path).path
        def delete():
            if not self._authorised():
                return self._send(403, {'error': 'missing or invalid session token'})
            parts = path.split('/')
            if len(parts) == 5 and parts[1:4] == ['api', 'workshop', 'comparisons']:
                return self._send(200, self.server.state.workshop.delete_comparison(parts[4]))
            self._send(404, {'code': 'missing'})
        self._dispatch_gated(path, delete)

    def _workshop_ticket(self, body: dict) -> None:
        if body != {"format_version": 1} or type(body.get("format_version")) is not int:
            raise workshop.WorkshopError("invalid", 400)
        self._send(200, self.server.state.workshop_ticket())

    def _tutorial_start(self, body: dict) -> None:
        session = self.server.tutorial_session
        self._send(200, {"url": self.server.url} if session else self.server.tutorial.start())

    def _tutorial_step(self, body: dict) -> None:
        session = self.server.tutorial_session
        if not session:
            return self._send(409, {"error": "not a tutorial session"})
        try:
            tutorial_topics.by_id(body.get("topic"))
        except KeyError:
            raise BodyError("unknown tutorial topic") from None
        self._send(200, session.prepare(body))

    def _tutorial_exit(self, body: dict) -> None:
        session = self.server.tutorial_session
        if not session:
            raise BodyError("not a tutorial session")
        self._send(200, {"url": session.parent.url})
        threading.Thread(target=session.close, daemon=True).start()

    # --- uploads (addendum 4.2) ------------------------------------------------------------------------------
    def _read_upload(self, length: int, slot=None) -> bytes:
        """The body in chunks of at most BODY_CHUNK under one BODY_DEADLINE_S deadline for the whole body. The
        socket timeout bounds each read, not the whole, so a slow sender cannot keep a read going. For a PDF the
        slot's cancelled flag is checked between chunks, and the connection is stored in the slot so that
        shutdown can interrupt a read blocked waiting for bytes."""
        if slot is not None:
            slot.attach_socket(self.connection)
        deadline = time.monotonic() + BODY_DEADLINE_S
        chunks, received = [], 0
        try:
            while received < length:
                if slot is not None:
                    slot.check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _ReadTimeout()
                self.connection.settimeout(max(0.01, min(self.timeout or remaining, remaining)))
                try:
                    chunk = self.rfile.read1(min(BODY_CHUNK, length - received))
                except OSError:
                    if slot is not None:
                        slot.check()
                    raise _ReadTimeout() from None
                if not chunk:
                    if slot is not None:
                        slot.check()
                    raise _ReadTimeout()
                chunks.append(chunk)
                received += len(chunk)
        finally:
            try:
                self.connection.settimeout(self.timeout)
            except OSError:
                pass
        if slot is not None:
            slot.check()                                   # after the body, before the worker starts
        return b"".join(chunks)

    def _admitted_upload(self, route_limit: int):
        """(filename, length, slot): X-Filename and Content-Length before anything is read. A `.pdf` is held to
        16 MiB on both routes and admitted to a PDF slot before its body is read, so a waiting request holds no
        file bytes. The caller releases the slot in its `finally`."""
        filename = decode_filename(self.headers.get("X-Filename"))
        pdf = attachments.is_pdf(filename)
        length = self._length(pdf_reader.SOURCE_LIMIT if pdf else route_limit)
        if length is None:
            raise LengthRequired()
        slot = self.server.state.pdf.acquire() if pdf else None
        return filename, length, slot

    @staticmethod
    def _read_document(data: bytes, filename: str, slot=None) -> tuple[dict, dict]:
        try:
            document = attachments.extract(data, filename=filename, slot=slot)
            attachments.check_document(document)
            rendered = attachments.render(document)
        except (attachments.AttachmentError, pdf_reader.AttachmentError):
            raise
        except Exception as exc:  # noqa: BLE001 - any other reader failure is extraction_failed (interface 6)
            raise attachments.AttachmentError("extraction_failed",
                                              f"The reader failed ({type(exc).__name__}).") from None
        attachments.require_text(document)
        if slot is not None:
            slot.check()                                   # immediately before the result is returned or staged
        return document, rendered

    def _upload(self) -> None:
        """POST /api/attachments/extract (Section 8.4): the file's bytes; nothing is stored."""
        filename, length, slot = self._admitted_upload(UPLOAD_LIMIT)
        try:
            data = self._read_upload(length, slot) if length else b""
            document, rendered = self._read_document(data, filename, slot)
        finally:
            if slot is not None:
                slot.release()
        self._send(200, {"filename": filename, "format": document["format"], "page_count": document["page_count"],
                         "warnings": document["warnings"], "source_bytes": len(data),
                         "source_sha256": hashlib.sha256(data).hexdigest(),
                         "notice": rendered["notice"], "coverage": attachments.coverage(document),
                         "insertion": insertion.preview(insertion.file_provenance(filename), rendered["text"])})

    def _config(self, body: dict) -> None:
        revision = _revision(body)
        self._send(200, self._state_body(self.server.state.apply_config(body, revision)))

    def _openrouter_key(self, body: dict) -> None:
        view = self.server.state.set_openrouter_key(body.get("api_key"), _revision(body))
        if self.server.authority is not None and self.server.authority.surface == "chat":
            return self._send(200, self._chat_settings())
        self._send(200, self._state_body(view))

    def _profile_save(self, body: dict) -> None:
        _profile_name(body)
        revision = _revision(body)
        self._send(200, self._state_body(self.server.state.save_profile(body, revision)))

    def _profile_select(self, body: dict) -> None:
        name = _profile_name(body, allow_null=True)
        revision = _revision(body)
        self._send(200, self._state_body(self.server.state.select_profile(name, revision)))

    def _profile_delete(self, body: dict) -> None:
        name = _profile_name(body)
        revision = _revision(body)
        self._send(200, self._state_body(self.server.state.delete_profile(name, revision)))

    def _task_save(self, body: dict) -> None:
        revision = _revision(body)
        self._send(200, self._state_body(self.server.state.save_task_settings(body.get("task"), revision)))

    def _roles_save(self, body: dict) -> None:
        revision = _revision(body)
        self._send(200, self._state_body(self.server.state.save_roles(body.get("roles"), revision, body.get("definitions"))))

    # --- block runs (techspec 0.1.4, 10.9, 15.3) --------------------------------------------------------
    def _block_run(self, body: dict) -> None:
        """Order (5.2): body and size; the block at its effective revision, and its problem; the `inputs` shape;
        then the runner (input checks, admission). Everything before the stream is a real HTTP status."""
        state = self.server.state
        name = body.get("name")
        if not isinstance(name, str) or NAME_RE.match(name) is None:
            raise BodyError("the block name must follow the name rule")
        revision = _revision(body)
        loaded = state.block_for_run(name, revision)             # 404, 409 stale_revision, 409 block_unusable
        declared = [d.name for d in loaded.block.inputs]
        inputs = body.get("inputs")
        if not isinstance(inputs, dict):
            raise BlockBodyError("inputs must be an object with one text per declared input")
        for key in inputs:
            if key not in declared:
                raise BlockBodyError("inputs holds a name that the block does not declare")
        for key in declared:
            if key not in inputs:
                raise BlockBodyError(f"inputs is missing the input {key}")
            if not isinstance(inputs[key], str):
                raise BlockBodyError(f"the input {key} must be a string")
        delivery = BlockDelivery(self)          # registers itself with the state once the run exists (`started`)
        try:
            runner.run_block(state, loaded, inputs, delivery)
        finally:
            state.remove_delivery(delivery)

    def _block_stop(self, body: dict) -> None:
        run_id = self._run_id(body.get("run_id"))
        self._send(200, {"status": self.server.state.store.stop_run(run_id, runs.USER_STOP)})

    def _block_run_poll(self, raw: str) -> None:
        self._send(200, self.server.state.store.run_envelope(self._run_id(raw)))

    @staticmethod
    def _block_name(raw: str) -> str:
        name = unquote(raw)
        if NAME_RE.match(name) is None:
            raise BodyError("the block name must follow the name rule")
        return name

    # --- contracts (overhaul spec 13) -------------------------------------------------------------
    @staticmethod
    def _v2(body, required, optional=()):
        """A version 2 contract request: exactly the listed members, no boolean integer substitutes."""
        if (not isinstance(body, dict) or type(body.get('format_version')) is not int or body['format_version'] != 2
                or not set(required) <= set(body) or set(body) - {'format_version', *required, *optional}):
            raise contracts.StoreError('invalid', 400, 'requires exactly the version 2 request members')
        return body

    @staticmethod
    def _name_field(body):
        if not wsmod.valid_name(body.get('name')):
            raise contracts.StoreError('invalid', 400, 'name must follow the name rule')
        return body['name']

    @staticmethod
    def _token_field(body, nullable=False):
        token = body.get('expected_file_token')
        if token is None and nullable:
            return None
        if not isinstance(token, str) or contracts.DIGEST_RE.match(token) is None:
            raise contracts.StoreError('invalid', 400, 'expected_file_token must be a sha256 file token'
                                       + (' or null' if nullable else ''))
        return token

    @staticmethod
    def _text_field(body, key):
        if not isinstance(body.get(key), str):
            raise contracts.StoreError('invalid', 400, f'{key} must be text')
        return body[key]

    def _contract_project(self, body):
        self._v2(body, {'definition_text'})
        self._send(200, {'format_version': 2, **contract_service.project(body['definition_text'])})

    def _contract_edit(self, body):
        self._v2(body, {'definition_text', 'edit'})
        self._send(200, {'format_version': 2, **contract_service.apply_edit(body['definition_text'], body['edit'])})

    def _contract_save(self, body: dict) -> None:
        self._v2(body, {'name', 'family', 'expected_file_token', 'definition_text', 'label'}, {'proposal_id'})
        name = self._name_field(body)
        token = self._token_field(body, nullable=True)
        family, label = self._text_field(body, 'family'), self._text_field(body, 'label')
        text = self._text_field(body, 'definition_text')
        origin, basis = 'manual', None
        if 'proposal_id' in body:
            contracts.parse_definition(text)               # located issues before the proposal is consulted
            origin, basis = self.server.state.workshop.synthesis_origin(body['proposal_id'], text)
        result = self.server.state.save_contract(name, family, token, text, label, origin=origin, basis=basis)
        self._send(200, {'format_version': 2, **result})

    def _contract_family(self, body: dict) -> None:
        self._v2(body, {'name', 'expected_file_token', 'family'})
        result = self.server.state.set_contract_family(self._name_field(body), self._token_field(body),
                                                       self._text_field(body, 'family'))
        self._send(200, {'format_version': 2, **result})

    def _contract_restore(self, body: dict) -> None:
        self._v2(body, {'name', 'expected_file_token', 'revision', 'label'})
        revision = body['revision']
        if type(revision) is not int or revision < 1:
            raise contracts.StoreError('invalid', 400, 'revision must be a positive integer')
        result = self.server.state.restore_contract(self._name_field(body), self._token_field(body), revision,
                                                    self._text_field(body, 'label'))
        self._send(200, {'format_version': 2, **result})

    def _contract_delete(self, body: dict) -> None:
        self._v2(body, {'name', 'expected_file_token'})
        result = self.server.state.delete_contract(self._name_field(body), self._token_field(body))
        self._send(200, {'format_version': 2, **result})

    def _contract_check(self, body: dict) -> None:
        """A Contract check of pasted text: no model call, save or acceptance (spec 13)."""
        self._v2(body, {'definition_text', 'value_text'})
        self._send(200, {'format_version': 2, 'label': 'Contract check',
                         **contract_service.check(body['definition_text'], body['value_text'])})

    # --- contract synthesis (overhaul spec 14.3) ------------------------------------------------------
    _SYNTHESIS = ('task_description', 'output_name', 'definition_text', 'role')
    _SYNTHESIS_OPTIONAL = ('settings_text', 'max_tokens', 'context_local_only', 'references')

    def _synthesis_body(self, body, extra=()):
        self._v2(body, self._SYNTHESIS + tuple(extra), self._SYNTHESIS_OPTIONAL)
        return {k: v for k, v in body.items() if k != 'format_version'}

    def _synthesis_plan(self, body):
        result = self.server.state.workshop.synthesis_plan(self._synthesis_body(body))
        self._send(200, {'format_version': 2, **result})

    def _synthesis_start(self, body):
        self._v2(body, self._SYNTHESIS + ('expected_plan', 'ticket'), self._SYNTHESIS_OPTIONAL + ('confirmations',))
        clean = {k: v for k, v in body.items() if k != 'format_version'}
        if 'confirmations' in body:
            clean['confirmations'] = body['confirmations']
        result = self.server.state.workshop.synthesis_start(clean)
        self._send(200, {'format_version': 2, **result})

    def _synthesis_apply(self, body):
        self._v2(body, {'proposal_id', 'definition_text'})
        text = body['definition_text']
        if text is not None and not isinstance(text, str):
            raise contracts.StoreError('invalid', 400, 'definition_text must be text or null')
        result = self.server.state.workshop.synthesis_apply(body['proposal_id'], text)
        self._send(200, {'format_version': 2, **result})

    # --- MCP (Sections 9, 10.7) -------------------------------------------------------------
    def _mcp_servers(self, body: dict) -> None:
        revision = _revision(body)
        self._send(200, self._state_body(self.server.state.save_mcp_servers(body.get("servers"), revision)))

    def _mcp_token(self, body: dict) -> None:
        revision = _revision(body)
        self._send(200, self._state_body(self.server.state.set_mcp_token(body.get("server"), body.get("token"),
                                                                         revision)))

    @staticmethod
    def _mcp_error_fields(err: MCPError) -> dict:
        out = {"reason": err.reason, "http_status": err.http_status, "jsonrpc_code": err.jsonrpc_code,
               "description": err.description}
        if err.server_text is not None:
            out["server_text"] = err.server_text
        return out

    def _mcp_tools(self, body: dict) -> None:
        revision = _revision(body)
        state = self.server.state
        snap = state.mcp_snapshot(body.get("server"), revision)
        client = state.mcp_client
        op = state.mcp_ops.start("discovery", snap, lambda ctx: client.discover(ctx, snap))
        committed = self._wait_committed(op)
        if committed is None or "error" in committed:
            err = committed["error"] if committed else MCPError("error", "deadline", "the discovery deadline passed")
            return self._send(400, {"error": err.description, "code": "mcp_error", **self._mcp_error_fields(err)})
        found = committed["value"]
        tools = []
        for tool in found.tools:
            schema = tool.get("inputSchema")
            form, notes = tool_forms.form_spec(schema)
            annotations = tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {}
            title = tool.get("title") if isinstance(tool.get("title"), str) else annotations.get("title")
            tools.append({"name": tool["name"], "title": title if isinstance(title, str) else None,
                          "description": tool.get("description") if isinstance(tool.get("description"), str) else None,
                          "input_schema_text": jsonio.encode_exact(schema), "form": form, "form_notes": notes,
                          "header_params": found.header_params.get(tool["name"], [])})
        self._send(200, {"server": snap.server, "era": found.era, "protocol_version": found.version,
                         "server_info": found.server_info, "tools": tools, "not_offered": found.not_offered})

    @staticmethod
    def _wait_committed(op) -> dict | None:
        """Wait for the operation's commit; past its deadline, abandon it (the commit is then immediate)."""
        while True:
            kind, value = op.wait(op.time_left())
            if kind == "final":
                return value
            if kind == "timeout":
                op.abandon("deadline")
                kind, value = op.wait(30.0)
                while kind == "progress":
                    kind, value = op.wait(30.0)
                return value if kind == "final" else None

    def _mcp_cancel(self, body: dict) -> None:
        self._send(200, self.server.state.mcp_ops.cancel(body.get("operation_id")))

    def _mcp_call(self, body: dict) -> None:
        server, tool, text = body.get("server"), body.get("tool"), body.get("arguments_text")
        if not isinstance(tool, str) or tool == "":
            raise BodyError("tool must be a non-empty string")
        if not isinstance(text, str):
            raise BodyError("arguments_text must be a string")
        revision = _revision(body)
        try:
            arguments = jsonio.decode_exact(text)
        except ValueError as exc:  # decode_exact's messages name the rule, never the text
            raise BadArguments(str(exc)) from None
        if not isinstance(arguments, dict):
            raise BadArguments("the arguments must be a JSON object")
        state = self.server.state
        snap = state.mcp_snapshot(server, revision)
        client = state.mcp_client
        op = state.mcp_ops.start("call", snap, lambda ctx: client.call(ctx, snap, tool, arguments))
        self._stream_call(op, snap, tool, text)

    def _stream_call(self, op, snap, tool: str, arguments_text: str) -> None:
        """Section 9.7, item 2: running writes bounded by the deadline; one final event after the commit."""
        page_timeout = self.server.page_timeout
        failed = False

        def write_running(event: dict) -> bool:
            nonlocal failed
            left = op.time_left()
            if left <= 0:
                op.abandon("deadline")
                return False
            try:
                self.connection.settimeout(min(page_timeout, left))
                self.wfile.write(jsonio.encode_line(event))
                self.wfile.flush()
            except OSError:
                failed = True
                op.abandon("deadline" if op.time_left() <= 0 else "page_closed")
                return False
            if op.time_left() <= 0:
                op.abandon("deadline")
                return False
            return True

        try:
            self.connection.settimeout(min(page_timeout, max(op.time_left(), 0.001)))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            ok = write_running({"type": "started", "operation_id": op.id})
        except OSError:
            failed, ok = True, False
            op.abandon("deadline" if op.time_left() <= 0 else "page_closed")
        while ok:
            kind, value = op.wait(op.time_left())
            if kind == "final":
                break
            if kind == "timeout":
                op.abandon("deadline")
                break
            progress, total, message = value
            ok = write_running({"type": "progress", "progress": progress, "total": total, "message": message})
        committed = None
        while committed is None:
            kind, value = op.wait(page_timeout)
            if kind == "final":
                committed = value
            elif kind == "timeout":
                return
        mcp_ops.before_final_write()
        if failed:
            return
        event = self._final_event(op, snap, tool, arguments_text, committed)
        try:
            self.connection.settimeout(page_timeout)
            self.wfile.write(jsonio.encode_line(event))
            self.wfile.flush()
        except OSError:
            pass  # the committed outcome stands; nothing more is sent

    def _final_event(self, op, snap, tool: str, arguments_text: str, committed: dict) -> dict:
        state = self.server.state
        era_now = state.mcp_client.era_of(snap.url)
        with op.lock:
            cancel_status = op.cancel_status if op.ctx.abandoned is not None else None
        event = {"type": "final", "outcome": committed["outcome"], "server": snap.server, "tool": tool,
                 "arguments_text": arguments_text, "result": None, "insertion": None, "insertion_structured": None,
                 "cancel_status": cancel_status, "reason": committed.get("reason"), "http_status": None,
                 "jsonrpc_code": None, "era": era_now[0] if era_now else None,
                 "protocol_version": era_now[1] if era_now else None}
        if "error" in committed:
            event.update(self._mcp_error_fields(committed["error"]))
            event["reason"] = committed["error"].reason
            return event
        value = committed["value"]
        result = value.result
        event["era"], event["protocol_version"] = value.era, value.version
        content = result.get("content") if isinstance(result.get("content"), list) else []
        texts = [item["text"] for item in content if item.get("type") == "text"]
        others = [{"type": item["type"]} for item in content if item.get("type") != "text"]
        structured = jsonio.encode_exact(result["structuredContent"]) if "structuredContent" in result else None
        is_error = value.outcome == "tool_error"
        event["result"] = {"text": "\n\n".join(texts), "structured_text": structured, "is_error": is_error,
                           "other_items": others}
        provenance = insertion.tool_provenance(snap.server, tool, arguments_text, is_error)
        if texts:
            event["insertion"] = insertion.preview(provenance, "\n\n".join(texts))
            if structured is not None:
                event["insertion_structured"] = insertion.preview(provenance, structured)
        else:
            event["insertion"] = insertion.preview(provenance, structured if structured is not None else "")
        return event

    def _profile_update(self, body: dict) -> None:
        self._send(200, self._state_body(self.server.state.update_profiles(body, _revision(body))))

    def _discovery_endpoint(self, body: dict):
        if "base_url" in body or "api_key" in body:
            raise SettingsError("server connections are configured in the model server configuration file")
        if "kind" not in body:
            raise BodyError("kind is required")
        base_url = body.get("base_url")
        endpoint = self.server.state.discovery_endpoint(body["kind"], "" if base_url is None else base_url,
                                                        body.get("api_key"))
        self._base_url = endpoint.base_url
        return endpoint

    @staticmethod
    def _entries(items: list) -> list[dict]:
        out = []
        for item in items:
            entry = dataclasses.asdict(item)
            entry.pop("raw", None)
            out.append(entry)
        return out

    def _models(self, body: dict) -> None:
        endpoint = self._discovery_endpoint(body)
        self._send(200, {"models": self._entries(modelspell.list_models(endpoint))})

    def _providers(self, body: dict) -> None:
        endpoint = self._discovery_endpoint(body)
        model = body.get("model")
        if model is not None and not isinstance(model, str):
            raise BodyError("model must be a string")
        self._send(200, {"providers": self._entries(modelspell.list_providers(endpoint, model or ""))})


    # --- agentic Chat (0.2.2, 4.9) -----------------------------------------------------------------------------
    def _agent_get(self, path: str) -> None:
        manager = self.server.state.agent
        query = parse_qs(urlparse(self.path).query)
        if path == "conversation":
            return self._send(200, manager.view())
        if path == "inventory":
            body: dict = {}
            try:
                if "revision" in query:
                    body["revision"] = int(query["revision"][0])
                if "task" in query:
                    body["task"] = json.loads(query["task"][0])
                if "capabilities" in query:
                    body["capabilities"] = json.loads(query["capabilities"][0])
                if "limits" in query:
                    body["limits"] = json.loads(query["limits"][0])
            except (ValueError, IndexError):
                raise BodyError("revision, task, capabilities and limits must be JSON") from None
            return self._send(200, manager.inventory(body))
        if path.startswith("runs/"):
            return self._send(200, manager.run_view(path[len("runs/"):], chat=self.server.authority is not None))
        if path.startswith("drafts/"):
            conversation = query.get("conversation", [None])[0]
            name, data = manager.draft_bytes(path[len("drafts/"):], conversation)
            ctype = "text/markdown; charset=utf-8" if name.endswith(".md") else "text/plain; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "capabilities":
            return self._send(200, self._capabilities_view())
        return self._send(404, {"error": "not found"})

    def _capabilities_view(self) -> dict:
        from .. import agent_capabilities
        state = self.server.state
        revision, rows = state.agent.store.read()
        eligible, refused = agent_capabilities.mcp_candidates(state, rows)
        tools = [{"url": c.url, "server": c.server, "tool": c.tool, "schema_digest": c.schema_digest,
                  "name": c.name, "eligible": True,
                  "classification": {k: v for k, v in state.agent.store.row(rows, c.url, c.server, c.tool,
                                                                            c.schema_digest).items()
                                     if k in agent_capabilities.DEFAULTS}} for c in eligible]
        tools += [{**row, "eligible": False} for row in refused]
        return {"format_version": 1, "revision": revision, "rows": rows, "tools": tools}

    def _agent_post(self, path: str, body: dict) -> None:
        if body.get("format_version") != 1 or type(body.get("format_version")) is not int:
            raise BodyError("format_version must be 1")
        body = {k: v for k, v in body.items() if k != "format_version"}
        manager = self.server.state.agent
        if path == "conversation":
            chat = self.server.authority is not None
            if set(body) - ({"close_private"} if chat else set()):
                raise BodyError("invalid new conversation fields")
            return self._send(200, manager.start_conversation(require_confirmation=chat,
                                                              close_private=body.get("close_private")))
        if path == "conversation/close":
            return self._send(200, manager.close_conversation(body.get("conversation"),
                confirm=self.server.authority is None or body.get("confirm") is True))
        if path == "conversation/memory":
            return self._send(200, manager.memory(body))
        if path == "documents/update":
            return self._send(200, manager.update_document(body, chat=self.server.authority is not None))
        if path == "inventory":
            return self._send(200, manager.inventory(body))
        if path == "run":
            if self.server.authority is not None and body.get("mode") not in ("cloud", "local"):
                raise BodyError("an explicit Chat mode is required")
            return self._agent_run(body)
        if path == "stop":
            if set(body) != {"run_id"}:
                raise BodyError("stop takes run_id")
            return self._send(200, manager.stop(body["run_id"]))
        if path == "approve":
            return self._send(200, manager.approve(body))
        if path == "capabilities":
            try:
                result = manager.store.update(self.server.state, body)
            except ValueError:
                raise workshop.WorkshopError("invalid", 400) from None
            return self._send(200, result)
        return self._send(404, {"error": "not found"})

    def _agent_upload(self) -> None:
        """POST /api/agent/documents: the file's bytes, as on /api/attachments/extract (raw-body exception)."""
        conversation = self.headers.get("X-Conversation")
        flag = self.headers.get("X-Local-Only", "true")
        if flag not in ("true", "false"):
            raise BodyError('X-Local-Only must be "true" or "false"')
        filename, length, slot = self._admitted_upload(agent_documents.SOURCE_LIMIT)   # before the body is read
        try:
            data = self._read_upload(length, slot) if length else b""
            try:
                result = self.server.state.agent.stage(
                    conversation, filename, data, flag == "true",
                    extract=lambda raw, name: self._read_document(raw, name, slot))
            except (attachments.AttachmentError, pdf_reader.AttachmentError, agent_loop.AgentError):
                raise
            except Exception as exc:  # noqa: BLE001 - any other reader failure is extraction_failed (interface 6)
                raise attachments.AttachmentError("extraction_failed",
                                                  f"The reader failed ({type(exc).__name__}).") from None
        finally:
            if slot is not None:
                slot.release()
        self._send(200, result)

    def _agent_run(self, body: dict) -> None:
        """Refusals are real HTTP statuses before the run exists (4.9); then an NDJSON stream."""
        state = self.server.state
        delivery = AgentDelivery(self)
        run = state.agent.prepare_run(body, delivery)
        try:
            state.add_delivery(delivery)
            try:
                run.run()
            finally:
                state.remove_delivery(delivery)
        finally:
            state.agent.abandon(run)          # a failure before run() releases the reservation; idempotent

    # --- model requests (techspec 0.1.3, 5.2, 8.6-8.8, 9.4) ----------------------------------------------
    def write_event(self, event: dict) -> None:
        """The one writer of stream events (a test hook, techspec 18.1). Raises OSError (TimeoutError
        included) when the page cannot be written to; every event is bounded by the page timeout."""
        self.wfile.write(jsonio.encode_line(event))
        self.wfile.flush()

    def _complete(self, body: dict) -> None:
        # Every refusal is a real HTTP status, before a run is created or a budget slot is taken (5.2).
        state = self.server.state
        system, prompt = _optional_str(body, "system"), _optional_str(body, "prompt")
        source = body.get("source")
        # 0.2.3: Checked request is retired. Its saved records stay readable in History.
        if source != "chat":
            raise BodyError('source must be "chat"')
        if "contract" in body:
            raise BodyError("a Chat request takes no contract")
        revision = _revision(body)
        snapshot = state.snapshot(revision)                     # 409 on a stale revision
        if snapshot.model.strip() == "":
            raise SettingsError("choose a model in Model settings")
        task = parse_task(body.get("task"))
        arguments = model_call.task_arguments(task, snapshot.kind, snapshot.provider)
        model_call.validate_request(snapshot, arguments)
        writer = state.register_writer(f"{source} attempt")      # 503 once shutdown has begun (10.10)
        try:
            budget = state.budget
            if not budget.try_acquire():
                return self._busy()
            try:
                prepared = model_step.prepare_first(state.store, snapshot=snapshot, system=system, prompt=prompt,
                                                    task=task, arguments=arguments, source=source,
                                                    save_runs=state.save_runs_now())
                if prepared.run.save_runs:
                    try:
                        state.history.start_run(prepared.run)       # run.json, before the first attempt starts
                    except BaseException:
                        state.store.abandon_run(prepared.run, prepared.number)
                        raise
                self._run_stream(prepared)
            finally:
                budget.release()
        finally:
            writer.release()

    def _busy(self) -> None:
        budget = self.server.state.budget
        self._send(503, {"error": f"{budget.limit} model requests are still running or stopping. "
                                  "Wait for one to finish.", "code": "busy"})

    def _run_stream(self, prepared: model_step.PreparedAttempt) -> None:
        """Stream one attempt (Section 8.7). The stream is the handler's; the outcome is run_attempt's."""
        state = self.server.state
        head_sent = False

        def emit(event: dict) -> None:
            try:
                self.write_event(event)
            except OSError:                                      # includes TimeoutError
                raise model_step.PageGone() from None

        def started(info: dict) -> None:
            nonlocal head_sent
            if not head_sent:
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                except OSError:
                    raise model_step.PageGone() from None
                head_sent = True
            emit({"type": "started", **info})

        def committed(record: dict, status: str) -> None:
            emit({"type": "final", "attempt_id": record["id"], "outcome": record["outcome"],
                  "record": record, "save_status": status})

        callbacks = model_step.Callbacks(
            started=started, text=lambda t: emit({"type": "text", "text": t}),
            reasoning=lambda t: emit({"type": "reasoning", "text": t}), committed=committed)
        delivery = _Delivery(self)
        state.add_delivery(delivery)         # shutdown shuts this connection down, so no write waits (10.10)
        try:
            record = model_step.run_attempt(state.store, prepared, callbacks,
                                            state.history if prepared.run.save_runs else None)
        finally:
            state.remove_delivery(delivery)
        if prepared.run.save_runs and head_sent:
            status, detail = state.store.save_detail(record["id"])
            if status in ("saved", "failed"):
                try:
                    self.write_event({"type": "save", "attempt_id": record["id"], "save_status": status, **detail})
                except OSError:
                    pass

    @staticmethod
    def _attempt_id(body: dict) -> str:
        value = body.get("attempt_id")
        if not runs.valid_attempt_id(value):
            raise BodyError("attempt_id must be an attempt ID")
        return value

    def _complete_stop(self, body: dict) -> None:
        self._send(200, {"status": self.server.state.store.stop(self._attempt_id(body))})

    def _attempt_poll(self, attempt_id: str) -> None:
        """The polling envelope; with `?view=compact` only, the five-field compact view (0.2.3, T-1). Without
        `view`, other query parameters are ignored as before."""
        if not runs.valid_attempt_id(attempt_id):
            raise BodyError("attempt_id must be an attempt ID")
        view = parse_qs(urlparse(self.path).query, keep_blank_values=True).get("view")
        if view is None:
            return self._send(200, self.server.state.store.envelope(attempt_id))
        if view != ["compact"]:
            raise BodyError("view must be compact")
        self._send(200, self.server.state.store.compact(attempt_id))

    # --- run history (techspec 10) ----------------------------------------------------------------------
    def _history_switch(self, body: dict) -> None:
        value = body.get("save_runs")
        if not isinstance(value, bool):
            raise BodyError("save_runs must be true or false")
        self._send(200, self._state_body(self.server.state.set_history_switch(value, _revision(body))))

    @staticmethod
    def _run_id(value: object) -> str:
        if not runs.valid_run_id(value):
            raise BodyError("run_id must be a run ID")
        return value

    def _runs_delete(self, body: dict) -> None:
        run_id = self._run_id(body.get("run_id"))
        self.server.state.delete_run(run_id, read_only=self.server.workspace.read_only)
        self._send(200, {"deleted": run_id})

