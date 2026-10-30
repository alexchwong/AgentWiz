# Derived from modelSpell ui/server.py at commit 1759c6e3c28a637731434a73c46088048583e358
# (MIT, Copyright (c) 2026 Alex Wong). Restructured per the AgentWiz 0.1.0 techspec, Section 7.6.
"""AgentWiz HTTP server: the only module that knows HTTP.

Binds to 127.0.0.1 only. Every request must carry the per-launch session token (the page URL's
`t` parameter, or the X-Session-Token header), so other web pages in the browser cannot drive the
server or spend API credit. Settings are shared by all tabs and checked per request by revision;
API keys are write-only and held in memory. Profiles are workspace-local copies of shared defaults;
the server never opens or closes a workspace itself. Error text reaching the page or terminal
is limited to text that cannot contain a credential (techspec 7.6, 7.7).
"""
from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import secrets
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import modelspell
from modelspell import Cancelled, ModelSpellError

from .. import __version__, attachments, insertion, jsonio, mcp_ops, model_call, tool_forms
from ..mcp_client import MCPError
from ..model_call import SettingsError, build_messages, classify_outcome, describe_error, result_to_dict
from ..task_settings import TaskSettingError, parse_task
from ..state import (DefaultConflict, NoSuchProfile, NoSuchServer, ProcessState, ReadOnly, StaleRevision,
                     WriteFailed)
from ..workspace import Workspace

INDEX = Path(__file__).with_name("index.html")
JSON = "application/json"
UPLOAD_LIMIT = 52_428_800          # 50 MiB; the JSON cap is insertion.REQUEST_LIMIT. Tests may lower both.
UPLOAD_ROUTE = "/api/attachments/extract"
_HEX = set("0123456789abcdefABCDEF")


class BodyError(ValueError):
    """A malformed request body or field. Messages are fixed or name fields only."""


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


def report_internal(route: str, exc: BaseException) -> None:
    """Terminal report for an unexpected failure: class and stack locations only, never messages."""
    lines = [f"[AgentWiz] internal error in {route}: {type(exc).__name__}"]
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if current is not exc:
            lines.append(f"  caused by {type(current).__name__}")
        for frame in traceback.extract_tb(current.__traceback__):
            lines.append(f"    {frame.filename}, line {frame.lineno}, in {frame.name}")
        current = current.__cause__ or current.__context__
    print("\n".join(lines), file=sys.stderr, flush=True)


class UIServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int = 0, *, workspace: Workspace, profiles_dir: Path, page_timeout: float = 30.0,
                 request_limit: int = 8, mcp_servers_dir: Path | None = None):
        self.token = secrets.token_urlsafe(24)
        self.workspace = workspace  # opened and closed by the caller
        self.state = ProcessState(workspace, request_limit, profiles_dir=profiles_dir,
                                  mcp_servers_dir=mcp_servers_dir if mcp_servers_dir is not None
                                  else Path("./mcp_servers"))
        self.page_timeout = page_timeout
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
        except mcp_ops.Busy as exc:
            self._reply_safely(503, {"error": str(exc), "code": "busy"})
        except NoSuchServer:
            self._reply_safely(404, {"error": "no such MCP server", "code": "no_such_server"})
        except mcp_ops.NoSuchOperation:
            self._reply_safely(404, {"error": "no such tool operation", "code": "no_such_operation"})
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

    # --- routes ------------------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        self._dispatch(path, lambda: self._get(path))

    def _get(self, path: str) -> None:
        if not self._authorised():
            return self._send(403, {"error": "missing or invalid session token"})
        if path == "/":
            return self._send(200, self.server.index, "text/html; charset=utf-8")
        if path == "/api/state":
            return self._send(200, self._state_body(self.server.state.view()))
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        self._dispatch(path, lambda: self._post(path))

    def _post(self, path: str) -> None:
        if not self._authorised():
            return self._send(403, {"error": "missing or invalid session token"})
        routes = {"/api/config": self._config, "/api/models": self._models,
                  "/api/providers": self._providers, "/api/complete": self._complete,
                  "/api/profiles/save": self._profile_save, "/api/profiles/select": self._profile_select,
                  "/api/profiles/delete": self._profile_delete,
                  "/api/profiles/update": self._profile_update,
                  "/api/task_settings/save": self._task_save,
                  "/api/mcp/servers": self._mcp_servers, "/api/mcp/token": self._mcp_token,
                  "/api/mcp/tools": self._mcp_tools, "/api/mcp/call": self._mcp_call,
                  "/api/mcp/cancel": self._mcp_cancel}
        if path == UPLOAD_ROUTE:
            return self._upload()
        body = self._body(" Insert a smaller part of the file." if path == "/api/complete" else "")
        handler = routes.get(path)
        if handler is None:
            return self._send(404, {"error": "not found"})
        handler(body)

    @staticmethod
    def _state_body(view: dict) -> dict:
        return {**view, "agentwiz_version": __version__, "modelspell_version": modelspell.__version__,
                "limits": {"request_bytes": insertion.REQUEST_LIMIT, "upload_bytes": UPLOAD_LIMIT}}

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

    def _complete(self, body: dict) -> None:
        # Section 7.2: every refusal is a real HTTP status, before the backend or the budget is touched.
        system, prompt = _optional_str(body, "system"), _optional_str(body, "prompt")
        revision = _revision(body)
        snapshot = self.server.state.snapshot(revision)          # 409 on a stale revision
        if snapshot.model.strip() == "":
            raise SettingsError("choose a model in Model settings")
        task = parse_task(body.get("task"))
        arguments = model_call.task_arguments(task, snapshot.kind, snapshot.provider)
        budget = self.server.state.budget
        if not budget.try_acquire():
            return self._send(503, {"error": f"{budget.limit} model requests are still running or stopping. "
                                             "Wait for one to finish.", "code": "busy"})
        try:
            extra: dict = {}
            model_info = None
            if snapshot.kind == "lmstudio" and task.reasoning == "off":
                model_info, extra["model_lookup"] = model_call.lmstudio_model_info(snapshot.endpoint, snapshot.model)
            self._stream(snapshot, build_messages(system, prompt), arguments, model_info, task, extra)
        finally:
            budget.release()

    def _stream(self, snapshot, messages: list[dict], arguments: dict, model_info, task, extra: dict) -> None:
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
        except OSError:
            return

        def emit(event: dict) -> None:
            data = jsonio.encode_line(event)
            try:
                self.wfile.write(data)
                self.wfile.flush()
            except OSError:  # includes TimeoutError from the page timeout
                raise Cancelled("page disconnected") from None  # makes modelSpell close upstream

        def emit_error(text: str, **fields) -> None:
            try:
                emit({"type": "error", "error": text, **extra, **fields})
            except Cancelled:
                pass

        base_url = snapshot.endpoint.base_url
        try:
            result = model_call.call_model(snapshot, messages, arguments, model_info=model_info,
                                           on_text=lambda t: emit({"type": "text", "text": t}),
                                           on_reasoning=lambda t: emit({"type": "reasoning", "text": t}))
        except Cancelled:
            return  # the page has gone; partial output is discarded in 0.1.0
        except ModelSpellError as exc:
            text = describe_error(exc, base_url)
            status = getattr(exc, "status", None)
            if snapshot.kind == "openrouter" and isinstance(exc, modelspell.BackendError) and status == 404:
                hint = model_call.OPENROUTER_HINT
                if (arguments.get("provider") or {}).get("require_parameters"):
                    hint += ", and whether any provider supports every task setting sent"
                return emit_error(f"{text} {hint}.", hint="openrouter_no_provider")
            return emit_error(text)
        except Exception as exc:  # noqa: BLE001 - messages may carry keys (techspec 7.6)
            report_internal("/api/complete", exc)
            return emit_error(f"InternalError: {type(exc).__name__}")

        # Outside the model call's error handling (R0-13): a failed write here only loses the event.
        try:
            line = jsonio.encode_line({"type": "result", "result": result_to_dict(result),
                                       "outcome": classify_outcome(result), "task": task.request_shape(),
                                       **extra})
        except Exception as exc:  # noqa: BLE001
            report_internal("/api/complete", exc)
            return emit_error(f"InternalError: {type(exc).__name__}")
        try:
            self.wfile.write(line)
            self.wfile.flush()
        except OSError:
            pass
