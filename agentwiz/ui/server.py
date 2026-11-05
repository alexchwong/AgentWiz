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
                runner, runs, tool_forms, tutorial_topics, workshop)
from .. import workspace as wsmod
from .. import workspace_admin
from ..errors import ShuttingDown, report_internal  # noqa: F401  (re-exported; defined in errors.py since 0.1.4)
from ..mcp_client import MCPError
from ..model_call import SettingsError, describe_error
from ..task_settings import TaskSettingError, parse_task
from ..tutorial import TutorialBusy
from ..state import (SHUTDOWN_GRACE_S, BlockUnusable, DefaultConflict, NoSuchBlock, NoSuchProfile, NoSuchServer, ProcessState, ReadOnly,
                     RoleProfileMissing, StaleRevision, WriteFailed)
from ..workspace import NAME_RE, Workspace

INDEX = Path(__file__).with_name("index.html")
JSON = "application/json"
UPLOAD_LIMIT = 52_428_800          # 50 MiB; the JSON cap is insertion.REQUEST_LIMIT. Tests may lower both.
UPLOAD_ROUTE = "/api/attachments/extract"
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

    def active_readers(self) -> int:
        with self._condition:
            return self._readers

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


class UIServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int = 0, *, workspace: Workspace, profiles_dir: Path, page_timeout: float = 30.0,
                 request_limit: int = 8, mcp_servers_dir: Path | None = None, servers=None, config_path=None):
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
        super().__init__(("127.0.0.1", port), Handler)

    def server_bind(self) -> None:
        # HTTPServer.server_bind resolves the hostname, which can stall even for loopback.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/?t={self.token}"

    def handle_error(self, request, client_address) -> None:  # last resort; never prints messages
        exc = sys.exc_info()[1]
        if exc is not None:
            report_internal("connection", exc)

    def _new_state(self, workspace: Workspace) -> ProcessState:
        return ProcessState(workspace, self.request_limit, profiles_dir=self.profiles_dir,
                            mcp_servers_dir=self.mcp_servers_dir, servers=self.servers,
                            config_path=self.config_path)

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
                ("script", state.scripts.in_flight)):
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
                old_state, old_ws = self.state, self.workspace
                old_state.shutdown(grace_s=self.grace_s, wait=True)
                old_ws.close()
                self.state, self.workspace = new_state, target_ws
                new_state = None
                swapped = True
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


class Handler(BaseHTTPRequestHandler):
    server: UIServer
    protocol_version = "HTTP/1.0"  # each response closes its connection, which delimits the stream

    def setup(self) -> None:
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

    def _authorised(self) -> bool:
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
        except workshop.WorkshopError as exc:
            self._reply_safely(exc.status, {"error": "Workshop request refused", "code": exc.code, **({"issues": exc.issues} if exc.issues else {}), **({"plan": exc.plan} if hasattr(exc, "plan") else {})})
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
        except attachments.AttachmentError as exc:  # the reader's own text, never file content
            self._reply_safely(400, {"error": str(exc), "code": exc.code})
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
        except (mcp_ops.Busy, ShuttingDown, TutorialBusy) as exc:
            self._reply_safely(503, {"error": str(exc), "code": "busy"})
        except runner.Busy as exc:  # names the budget (15.1)
            self._reply_safely(503, {"error": f"{exc.limit} {exc.budget} are still running or stopping. "
                                              "Wait for one to finish.", "code": "busy", "budget": exc.budget})
        except BlockUnusable as exc:  # the problem names files, locations and rules, never values
            self._reply_safely(409, {"error": str(exc), "code": "block_unusable", "problem": exc.problem.view()})
        except runs.NoSuchRunInMemory:
            self._reply_safely(404, {"error": "no such run", "code": "no_such_run"})
        except RoleProfileMissing as exc:  # names the role, never the profile
            self._reply_safely(400, {"error": str(exc), "code": "no_such_profile", "role": exc.role})
        except NoSuchBlock:
            self._reply_safely(404, {"error": "no such block", "code": "no_such_block"})
        except NoSuchServer:
            self._reply_safely(404, {"error": "no such MCP server", "code": "no_such_server"})
        except mcp_ops.NoSuchOperation:
            self._reply_safely(404, {"error": "no such tool operation", "code": "no_such_operation"})
        except contracts.NoSuchContract:  # contract names are not echoed
            self._reply_safely(404, {"error": "no such contract", "code": "no_such_contract"})
        except contracts.ContractExists:
            self._reply_safely(409, {"error": "a contract with that name already exists", "code": "exists"})
        except contracts.ContractUnusable as exc:  # names a file, a code and a rule; never a value
            self._reply_safely(409, {"error": f"the existing contract has a problem and cannot be replaced: {exc}",
                                     "code": "contract_unusable", "problem": dataclasses.asdict(exc.problem)})
        except contracts.ContractInvalid as exc:  # locations and rules, never values
            first = exc.issues[0] if exc.issues else {"location": "contract", "message": "is not valid"}
            self._reply_safely(400, {"error": f"ContractError: {first['location']}: {first['message']}",
                                     "code": "contract_error", "issues": exc.issues, "issue_count": exc.total})
        except contract_schema.DefinitionError as exc:
            code = exc.issues[0].get('code', 'contract_invalid') if exc.issues else 'contract_invalid'
            self._reply_safely(400, {'code': code, 'message': 'the contract definition or edit is invalid',
                                     'issues': exc.issues, 'issue_count': exc.total})
        except (contract_service.ValidationUnavailable, checks.ValidationUnavailable):
            self._reply_safely(503, {'code': 'validation_unavailable',
                                     'message': 'repair the AgentWiz validator installation'})
        except runs.RepairRefused as exc:
            self._reply_safely(exc.status, {"error": exc.message, "code": exc.code})
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
        if not self._authorised():
            return self._send(403, {"error": "missing or invalid session token"})
        if path == "/":
            return self._send(200, self.server.index, "text/html; charset=utf-8")
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
        if path.startswith("/api/contracts/"):
            return self._send(200, self.server.state.get_contract(path[len("/api/contracts/"):]))
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
        if not self._authorised():
            return self._send(403, {"error": "missing or invalid session token"})
        if path.startswith(WORKSPACES_ROUTE + "/"):
            return self._workspaces_post(path)
        if path.startswith("/api/workshop/") and path != "/api/workshop/tickets":
            return self._workshop_post(path[len("/api/workshop/"):], self._body())
        routes = {"/api/workshop/tickets": self._workshop_ticket, "/api/tutorial/start": self._tutorial_start,
                  "/api/tutorial/step": self._tutorial_step,
                  "/api/tutorial/exit": self._tutorial_exit,
                  "/api/config": self._config, "/api/openrouter-key": self._openrouter_key, "/api/models": self._models,
                  "/api/providers": self._providers, "/api/complete": self._complete,
                  "/api/complete/stop": self._complete_stop, "/api/repair/preview": self._repair_preview,
                  "/api/repair": self._repair, "/api/history/switch": self._history_switch,
                  "/api/runs/delete": self._runs_delete,
                  "/api/profiles/save": self._profile_save, "/api/profiles/select": self._profile_select,
                  "/api/profiles/delete": self._profile_delete,
                  "/api/profiles/update": self._profile_update,
                  "/api/task_settings/save": self._task_save, "/api/roles/save": self._roles_save, "/api/blocks/run": self._block_run,
                  "/api/blocks/stop": self._block_stop,
                  "/api/contracts/save": self._contract_save, "/api/contracts/delete": self._contract_delete,
                  "/api/contracts/check": self._contract_check,
                  "/api/contracts/project": self._contract_project, "/api/contracts/edit": self._contract_edit,
                  "/api/mcp/servers": self._mcp_servers, "/api/mcp/token": self._mcp_token,
                  "/api/mcp/tools": self._mcp_tools, "/api/mcp/call": self._mcp_call,
                  "/api/mcp/cancel": self._mcp_cancel}
        if path == UPLOAD_ROUTE:
            return self._upload()
        body = self._body(" Copy a smaller part of the file." if path == "/api/complete" else "")
        handler = routes.get(path)
        if handler is None:
            return self._send(404, {"error": "not found"})
        handler(body)

    def _state_body(self, view: dict) -> dict:
        out = {**view, "agentwiz_version": __version__, "modelspell_version": modelspell.__version__,
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
        if path == 'starters':
            from ..workshop_editor import STARTERS, examples
            result = {'format_version': 1, 'starters': [{'id': k, 'version': 1, 'examples': examples(k)} for k in STARTERS]}
        elif len(parts) == 3 and parts[0] == 'blocks' and parts[2] == 'revisions':
            result = service.assets.revisions(parts[1])
        elif len(parts) == 4 and parts[0] == 'blocks' and parts[2] == 'revisions':
            result = service.preview({'key': parts[1], 'revision': parts[3]})
        elif len(parts) == 3 and parts[0] == 'blocks' and parts[2] == 'examples':
            result = service.collection(parts[1])
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
        if path == 'input-value':
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
        elif path == 'contract-form':
            from ..workshop_editor import contract_view
            result = contract_view(body['source'])
            result['blank_schema'] = jsonio.decode_exact(body['source']).get('schema') is None
            if 'field_edit' in body:
                from ..workshop_editor import edit
                definition = {'inputs':{'value':{'contract':jsonio.decode_exact(body['source'])}}}
                draft = {'definition_text':jsonio.encode_exact(definition)}
                changed = edit(draft, {**body['field_edit'],'kind':'property_form','side':'inputs','port':'value'})
                raw = jsonio.decode_exact(changed['definition_text'])['inputs']['value']['contract']
                result = contract_view(jsonio.encode_exact(raw))
            elif 'description' in body:
                if result['source_only']:
                    raise workshop.WorkshopError('source_only', 409)
                obj = jsonio.decode_exact(body['source'])
                node = obj['schema']
                for segment in body['path']:
                    node = node['items'] if segment == '[]' else node['properties'][segment]
                node['description'] = body['description']
                result = contract_view(jsonio.encode_exact(obj))
        elif path == 'preview':
            result = service.preview(body)
        elif path == 'plans':
            result = service.plan(body.get('action'), body.get('request', {}))
        elif path == 'save':
            result = service.save(body)
        elif path in ('test', 'batches'):
            result = service.start(path, body)
        elif path == 'assist':
            result = service.assist(body)
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

    def _upload(self) -> None:
        """POST /api/attachments/extract (Section 8.4): the file's bytes; nothing is stored."""
        length = self._length(UPLOAD_LIMIT)
        if length is None:
            raise LengthRequired()
        filename = decode_filename(self.headers.get("X-Filename"))
        try:
            data = self.rfile.read(length) if length else b""
        except OSError:
            raise _ReadTimeout() from None
        if len(data) < length:
            raise _ReadTimeout()
        try:
            document = attachments.extract(data, filename=filename)
            attachments.check_document(document)
            rendered = attachments.render(document)
        except attachments.AttachmentError:
            raise
        except Exception as exc:  # noqa: BLE001 - any other reader failure is extraction_failed (interface 6)
            raise attachments.AttachmentError("extraction_failed",
                                              f"The reader failed ({type(exc).__name__}).") from None
        self._send(200, {"filename": filename, "format": document["format"], "page_count": document["page_count"],
                         "warnings": document["warnings"], "source_bytes": len(data),
                         "source_sha256": hashlib.sha256(data).hexdigest(),
                         "notice": rendered["notice"],
                         "insertion": insertion.preview(insertion.file_provenance(filename), rendered["text"])})

    def _config(self, body: dict) -> None:
        revision = _revision(body)
        self._send(200, self._state_body(self.server.state.apply_config(body, revision)))

    def _openrouter_key(self, body: dict) -> None:
        self._send(200, self._state_body(self.server.state.set_openrouter_key(body.get("api_key"), _revision(body))))

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

    # --- contracts (techspec 0.1.3, 7.8, 13.3) ----------------------------------------------------
    @staticmethod
    def _contract_draft_body(body, fields):
        if (set(body) != {'format_version', *fields} or type(body.get('format_version')) is not int
                or body['format_version'] != 2):
            raise contract_schema.DefinitionError([{'location': 'request', 'code': 'invalid',
                                                    'message': 'requires the version 2 request fields'}])

    def _contract_project(self, body):
        self._contract_draft_body(body, {'definition_text'})
        self._send(200, {'format_version': 2, **contract_service.project(body['definition_text'])})

    def _contract_edit(self, body):
        self._contract_draft_body(body, {'definition_text', 'edit'})
        self._send(200, {'format_version': 2, **contract_service.apply_edit(body['definition_text'], body['edit'])})

    @staticmethod
    def _contract_name(body: dict) -> str:
        name = body.get("name")
        if not isinstance(name, str):
            raise BodyError("name must be a string")
        return name

    def _contract_save(self, body: dict) -> None:
        name = self._contract_name(body)
        text = body.get("text")
        if not isinstance(text, str):
            raise BodyError("text must be a string")
        revision = body.get("revision")
        if revision is not None and (not isinstance(revision, int) or isinstance(revision, bool)):
            raise BodyError("revision must be an integer, or null to create a contract")
        view, contract, notes = self.server.state.save_contract(name, text, revision)
        self._send(200, {**self._state_body(view), "contract": contract, "notes": notes})

    def _contract_delete(self, body: dict) -> None:
        name = self._contract_name(body)
        revision = _revision(body)
        self._send(200, self._state_body(self.server.state.delete_contract(name, revision)))

    def _contract_check(self, body: dict) -> None:
        """Check pasted text against contract text, saved or not. No revision: nothing shared is used,
        and no model is called (R4-19)."""
        if 'format_version' in body:
            self._contract_draft_body(body, {'definition_text', 'value_text'})
            return self._send(200, {'format_version': 2,
                                    **contract_service.check(body['definition_text'], body['value_text'])})
        contract_text, text = body.get("contract_text"), body.get("text")
        if not isinstance(contract_text, str):
            raise BodyError("contract_text must be a string")
        if not isinstance(text, str):
            raise BodyError("text must be a string")
        try:
            contract = checks.contract_from_text(contract_text)
        except checks.ContractError as exc:
            raise contracts.ContractInvalid(exc.issues, exc.total) from None
        self._send(200, {"check": checks.check_answer(contract, text)})

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
        if source not in ("chat", "checked_request"):
            raise BodyError('source must be "chat" or "checked_request"')
        revision = _revision(body)
        contract_ref = None
        if source == "chat":
            if "contract" in body:
                raise BodyError("a Chat request takes no contract")
        else:
            ref = body.get("contract")
            if (not isinstance(ref, dict) or not wsmod.valid_name(ref.get("name"))
                    or not isinstance(ref.get("revision"), int) or isinstance(ref.get("revision"), bool)):
                raise BodyError("a Checked request needs contract: {name, revision}")
            contract_ref = (ref["name"], ref["revision"])
        snapshot = state.snapshot(revision)                     # 409 on a stale revision
        if snapshot.model.strip() == "":
            raise SettingsError("choose a model in Model settings")
        task = parse_task(body.get("task"))
        arguments = model_call.task_arguments(task, snapshot.kind, snapshot.provider)
        copy_ = None
        if contract_ref is not None:
            entry = state.contract_for_request(*contract_ref)   # 404, 409 stale_revision, 409 contract_unusable
            copy_ = model_step.ContractCopy.of(entry.name, entry.revision, entry.contract)
            checks.require_validation([copy_.parsed])
        writer = state.register_writer(f"{source} attempt")      # 503 once shutdown has begun (10.10)
        try:
            budget = state.budget
            if not budget.try_acquire():
                return self._busy()
            try:
                prepared = model_step.prepare_first(state.store, snapshot=snapshot, system=system, prompt=prompt,
                                                    task=task, arguments=arguments, source=source,
                                                    save_runs=state.save_runs_now(), contract=copy_)
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

    def _repair_preview(self, body: dict) -> None:
        _run, record, number = self.server.state.store.admit_repair(self._attempt_id(body), reserve=False)
        self.server.state.key_for(record["endpoint"]["kind"], record["endpoint"]["base_url"])
        plan = model_step.repair_plan(record)
        contract = plan["contract"]
        self._send(200, {"parent_id": record["id"], "attempt": number, "messages": plan["messages"],
                         "settings": plan["settings"],
                         "contract": {"name": contract.name, "revision": contract.revision},
                         "repair_message": plan["repair_message"], "repair_reason": plan["repair_reason"]})

    def _repair(self, body: dict) -> None:
        state = self.server.state
        attempt_id = self._attempt_id(body)
        writer = state.register_writer("repair attempt")         # 503 once shutdown has begun (10.10)
        try:
            run, parent, number = state.store.admit_repair(attempt_id)
            budget = state.budget
            if not budget.try_acquire():
                state.store.abandon_run(run, number)
                return self._busy()
            try:
                try:
                    prepared = model_step.prepare_repair(run, number, parent, state.key_for)
                except BaseException:
                    state.store.abandon_run(run, number)
                    raise
                self._run_stream(prepared)
            finally:
                budget.release()
        finally:
            writer.release()

    def _attempt_poll(self, attempt_id: str) -> None:
        if not runs.valid_attempt_id(attempt_id):
            raise BodyError("attempt_id must be an attempt ID")
        self._send(200, self.server.state.store.envelope(attempt_id))

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
