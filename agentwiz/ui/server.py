# Derived from modelSpell ui/server.py at commit 1759c6e3c28a637731434a73c46088048583e358
# (MIT, Copyright (c) 2026 Alex Wong). Restructured per the AgentWiz 0.1.0 techspec, Section 7.6.
"""AgentWiz HTTP server: the only module that knows HTTP.

Binds to 127.0.0.1 only. Every request must carry the per-launch session token (the page URL's
`t` parameter, or the X-Session-Token header), so other web pages in the browser cannot drive the
server or spend API credit. Settings are shared by all tabs and checked per request by revision;
API keys are write-only and held in memory. Profiles are saved in the workspace the server was
given; the server never opens or closes a workspace itself. Error text reaching the page or terminal
is limited to text that cannot contain a credential (techspec 7.6, 7.7).
"""
from __future__ import annotations

import dataclasses
import hmac
import json
import secrets
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import modelspell
from modelspell import Cancelled, ModelSpellError

from .. import __version__, jsonio, model_call
from ..model_call import SettingsError, build_messages, classify_outcome, describe_error, result_to_dict
from ..state import NoSuchProfile, ProcessState, ReadOnly, StaleRevision, WriteFailed
from ..workspace import Workspace

INDEX = Path(__file__).with_name("index.html")
JSON = "application/json"


class BodyError(ValueError):
    """A malformed request body or field. Messages are fixed or name fields only."""


class _ReadTimeout(Exception):
    """The request body could not be read within the page timeout: close without a response."""


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

    def __init__(self, port: int = 0, *, workspace: Workspace, page_timeout: float = 30.0,
                 request_limit: int = 8):
        self.token = secrets.token_urlsafe(24)
        self.workspace = workspace  # opened and closed by the caller
        self.state = ProcessState(workspace, request_limit)
        self.page_timeout = page_timeout
        self.index = INDEX.read_bytes()
        super().__init__(("127.0.0.1", port), Handler)

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

    def _body(self) -> dict:
        length = self.headers.get("Content-Length")
        if length is None:
            return {}
        if not (length.isascii() and length.isdigit()):
            raise BodyError("Content-Length must be a non-negative integer")
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
        except (BodyError, SettingsError) as exc:
            self._reply_safely(400, {"error": f"{type(exc).__name__}: {exc}"})
        except StaleRevision:
            self._reply_safely(409, {"error": "settings were changed elsewhere", "code": "stale_revision"})
        except ReadOnly as exc:  # names a file, a JSON location and a rule; never a value
            self._reply_safely(409, {"error": str(exc), "code": "read_only"})
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
                  "/api/profiles/delete": self._profile_delete}
        handler = routes.get(path)
        if handler is None:
            return self._send(404, {"error": "not found"})
        handler(self._body())

    @staticmethod
    def _state_body(view: dict) -> dict:
        return {**view, "agentwiz_version": __version__, "modelspell_version": modelspell.__version__}

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
        system, prompt = _optional_str(body, "system"), _optional_str(body, "prompt")
        revision = _revision(body)
        snapshot = self.server.state.snapshot(revision)          # 409 on a stale revision
        budget = self.server.state.budget
        if not budget.try_acquire():
            return self._send(503, {"error": f"{budget.limit} model requests are still running or stopping. "
                                             "Wait for one to finish.", "code": "busy"})
        try:
            self._stream(snapshot, build_messages(system, prompt))
        finally:
            budget.release()

    def _stream(self, snapshot, messages: list[dict]) -> None:
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

        def emit_error(text: str) -> None:
            try:
                emit({"type": "error", "error": text})
            except Cancelled:
                pass

        base_url = snapshot.endpoint.base_url
        try:
            result = model_call.call_model(snapshot, messages,
                                           on_text=lambda t: emit({"type": "text", "text": t}),
                                           on_reasoning=lambda t: emit({"type": "reasoning", "text": t}))
        except Cancelled:
            return  # the page has gone; partial output is discarded in 0.1.0
        except ModelSpellError as exc:
            return emit_error(describe_error(exc, base_url))
        except Exception as exc:  # noqa: BLE001 - messages may carry keys (techspec 7.6)
            report_internal("/api/complete", exc)
            return emit_error(f"InternalError: {type(exc).__name__}")

        # Outside the model call's error handling (R0-13): a failed write here only loses the event.
        try:
            line = jsonio.encode_line({"type": "result", "result": result_to_dict(result),
                                       "outcome": classify_outcome(result)})
        except Exception as exc:  # noqa: BLE001
            report_internal("/api/complete", exc)
            return emit_error(f"InternalError: {type(exc).__name__}")
        try:
            self.wfile.write(line)
            self.wfile.flush()
        except OSError:
            pass

