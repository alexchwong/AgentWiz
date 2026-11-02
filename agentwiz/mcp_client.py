"""A standard-library MCP client for Streamable HTTP, speaking both protocol eras (techspec 0.1.2,
Sections 9.2-9.6; D18, D46, R2-23, R2-24, R2-30, R2-34, R2-36).

- **Modern** (2026-07-28): no handshake or session; every request carries its version, capabilities and
  client identity; closing the response stream is the cancellation.
- **Legacy** (2025-03-26 to 2025-11-25): `initialize` handshake, optional `Mcp-Session-Id`, `ping`, and
  `notifications/cancelled` (sent as maintenance work by `mcp_ops`).

A server's era is found with a `server/discover` probe and cached per URL. Modern servers' discovery
entries (each tool's `x-mcp-header` paths) and legacy sessions belong to identities (URL plus token).

Every outbound message passes a gate (the operation context's `gate`) before connecting and again
after connecting, immediately before writing. Each socket is registered on the operation before the
post-connect gate and withdrawn before it is closed, so abandonment can shut it down from another
thread. Nothing is resent, except a legacy request answered "session expired", once.

Messages are read with `jsonio.decode_exact` and written with `jsonio.encode_exact`, so numbers stay
exact. Connections are direct (`http.client` ignores proxy variables); HTTPS verifies against the
system store; redirects are never followed. Knows nothing about AgentWiz's own HTTP server.
"""
from __future__ import annotations

import base64
import http.client
import itertools
import socket
import ssl
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from . import __version__
from .jsonio import decode_exact, encode_exact

MODERN = "2026-07-28"
LEGACY = ("2025-11-25", "2025-06-18", "2025-03-26")
OFFERED_LEGACY = "2025-11-25"
MESSAGE_LIMIT = 33_554_432      # one message (32 MiB); tests may lower it
DISCOVERY_LIMIT = 33_554_432    # all messages of one discovery together
TOOL_LIMIT = 1000
SERVER_TEXT_LIMIT = 2000
SAFE_INTEGER = 2**53 - 1
REJECTING_CODES = frozenset({-32700, -32600, -32601, -32602, -32020, -32021, -32022})
MODERN_ERRORS = frozenset({-32020, -32021, -32022})
_TOKEN_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#$%&'*+-.^_`|~")
_COMBINATORS = ("items", "anyOf", "oneOf", "allOf", "not", "if", "then", "else", "$ref", "prefixItems",
                "additionalProperties", "patternProperties", "$defs", "definitions", "dependentSchemas",
                "contains", "propertyNames", "unevaluatedProperties", "unevaluatedItems")

_ids = itertools.count(1)          # JSON-RPC request IDs and progress tokens: process-wide, unique
_ids_lock = threading.Lock()


def next_id() -> int:
    with _ids_lock:
        return next(_ids)


def client_info() -> dict:
    return {"name": "AgentWiz", "version": __version__}


# --- hooks tests may replace -----------------------------------------------------------------------
def connect_socket(host: str, port: int, timeout: float) -> socket.socket:
    """Open the TCP connection (name resolution included). Tests replace it to simulate a stuck resolver."""
    return socket.create_connection((host, port), timeout=timeout)


def make_ssl_context() -> ssl.SSLContext:
    """The system's certificate store, verification on. Tests replace it with their test certificate."""
    return ssl.create_default_context()


# --- outcomes ------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Classified:
    outcome: str            # discovery: "ok" | "error"; tools/call: "ok" | "tool_error" | "rejected" | "unknown"
    reason: str | None


def classify(stage: str, *, gate_passed: bool = False, abandoned: str | None = None,
             transport: str | None = None, status: int | None = None, jsonrpc_code: int | None = None,
             reading: str | None = None) -> Classified:
    """Section 9.6's table as one pure function, for both eras and for discovery.

    stage: "discovery" or "call". gate_passed: the tools/call gate was passed. abandoned: "stopped",
    "deadline" or "page_closed" (the operation was abandoned). transport: "name_resolution",
    "connection_refused", "tls", "idle_timeout" or "lost". reading: "result", "is_error", "malformed",
    "too_large", "stream_ended", "input_required" or "accepted" (HTTP 202 to a request).
    """
    def end(call_outcome: str, reason: str) -> Classified:
        if stage == "discovery":
            return Classified("ok" if call_outcome == "ok" else "error", None if call_outcome == "ok" else reason)
        return Classified(call_outcome, None if call_outcome in ("ok", "tool_error") else reason)

    if abandoned is not None:
        if stage == "discovery":
            return end("error", abandoned)
        if not gate_passed:
            reason = {"stopped": "not_sent_stopped", "deadline": "not_sent_deadline"}.get(abandoned, abandoned)
            return end("rejected", reason)
        return end("unknown", abandoned)
    if transport is not None:
        if not gate_passed:
            return end("rejected", transport if transport != "lost" else "connection_refused")
        return end("unknown", "stream_ended" if transport == "lost" else transport)
    if status is not None and status != 200:
        if status in (401, 403):
            return end("rejected", "credentials")
        if 300 <= status < 400:
            return end("rejected", "redirect")
        if 400 <= status < 500:
            return end("rejected", "http_status")
        if status >= 500:
            return end("unknown", "http_status")
        if status == 202:
            return end("unknown", "malformed")
        return end("unknown", "malformed")
    if jsonrpc_code is not None:
        return end("rejected" if jsonrpc_code in REJECTING_CODES else "unknown", "jsonrpc_error")
    if reading == "result":
        return end("ok", "")
    if reading == "is_error":
        return end("tool_error", "")
    if reading == "accepted":
        return end("unknown", "malformed")
    return end("unknown", reading or "malformed")


class MCPError(Exception):
    """A discovery error or a call that did not end with a result. Carries Section 9.8's fields."""

    def __init__(self, outcome: str, reason: str, description: str, *, http_status: int | None = None,
                 jsonrpc_code: int | None = None, server_text: str | None = None):
        super().__init__(description)
        self.outcome, self.reason, self.description = outcome, reason, description
        self.http_status, self.jsonrpc_code, self.server_text = http_status, jsonrpc_code, server_text


class Abandoned(Exception):
    """The operation was abandoned or passed its deadline; the worker stops without sending."""


# --- the operation context ---------------------------------------------------------------------------
class OpContext:
    """What a worker needs from its operation: the gate, the socket set, progress and borrows.

    `mcp_ops.Operation` builds one per operation and decides abandonment under `lock`.
    """

    def __init__(self, deadline: float, on_progress: Callable[[str, str | None, str | None], None] | None = None):
        self.lock = threading.Lock()
        self.deadline = deadline
        self.abandoned: str | None = None          # "stopped" | "deadline" | "page_closed"
        self.call_gate_passed = False
        self.sockets: set[socket.socket] = set()
        self.on_progress = on_progress or (lambda progress, total, message: None)
        self.borrowed: list[Session] = []           # sessions this worker took (released at thread exit)
        self.call_session: Session | None = None    # legacy: the session tools/call was sent in
        self.call_request_id: int | None = None
        self.snapshot: Snapshot | None = None

    def _check_locked(self) -> None:
        if self.abandoned is not None:
            raise Abandoned(self.abandoned)
        if time.monotonic() >= self.deadline:
            raise Abandoned("deadline")    # the operation's owner abandons it (mcp_ops), so commit stays single

    def gate(self, *, call: bool = False, session: Session | None = None, request_id: int | None = None,
             post: bool = False) -> None:
        """Check the abandoned flag and the deadline; `post` marks the gate after connecting. Passing the
        tools/call gate is recorded in the same step, so abandonment and sending never overlap."""
        with self.lock:
            self._check_locked()
            if call:
                self.call_gate_passed = True
                self.call_session = session
                self.call_request_id = request_id

    def register(self, sock: socket.socket) -> None:
        with self.lock:
            if self.abandoned is not None:
                raise Abandoned(self.abandoned)
            self.sockets.add(sock)

    def withdraw(self, sock: socket.socket) -> None:
        with self.lock:
            self.sockets.discard(sock)

    def shutdown_sockets_locked(self) -> None:
        for sock in list(self.sockets):
            try:
                socket.socket.shutdown(sock, socket.SHUT_RDWR)
            except OSError:
                pass

    def abandon(self, reason: str) -> bool:
        """Simple abandonment for direct users of the client (mcp_ops has its own, with commits)."""
        with self.lock:
            if self.abandoned is not None:
                return False
            self.abandoned = reason
            self.shutdown_sockets_locked()
            return True


@dataclass(frozen=True)
class Snapshot:
    """A server's configuration and token value, resolved when the operation started (R2-11)."""
    server: str
    url: str
    idle_timeout_s: float = 30
    call_deadline_s: float = 120
    discovery_deadline_s: float = 20
    token: str | None = None

    @property
    def identity(self) -> tuple[str, str | None]:
        return (self.url, self.token)


# --- sessions (legacy) -------------------------------------------------------------------------------
@dataclass(eq=False)
class Session:
    identity: tuple
    session_id: str | None
    version: str
    generation: int
    state: str = "open"             # "open" | "ending"
    borrowers: int = 0
    superseded: bool = False
    delete_scheduled: bool = False
    delete_sent: bool = False


@dataclass
class Entry:
    """A modern identity's last complete discovery (R2-34)."""
    version: str
    offered: dict[str, list[tuple[tuple[str, ...], str, str]]]   # tool -> [(path, header name, type)]
    not_offered: dict[str, str]                                   # tool -> reason


@dataclass
class DiscoveryResult:
    era: str
    version: str
    server_info: dict | None
    tools: list[dict]                      # as parsed by decode_exact
    not_offered: list[dict] = field(default_factory=list)
    header_params: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class CallResult:
    outcome: str                           # "ok" | "tool_error"
    era: str
    version: str
    result: dict


# --- header values -----------------------------------------------------------------------------------
def header_value(value: str) -> str:
    """Section 9.4's header form: the string itself when plain, else the Base64 sentinel form."""
    plain = (value != "" and all(0x20 <= ord(c) <= 0x7E for c in value) and value == value.strip(" ")
             and not (value.startswith("=?base64?") and value.endswith("?=")))
    if plain:
        return value
    return "=?base64?" + base64.b64encode(value.encode("utf-8")).decode("ascii") + "?="


def _header_params(schema: Any) -> tuple[list[tuple[tuple[str, ...], str, str]], str | None]:
    """The x-mcp-header annotations of an input schema, or a reason the tool is not offered."""
    found: list[tuple[tuple[str, ...], str, str]] = []
    problems: list[str] = []

    def walk(node: Any, path: tuple[str, ...], plain: bool) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, path, False)
            return
        if not isinstance(node, Mapping):
            return
        if "x-mcp-header" in node:
            name = node["x-mcp-header"]
            where = ".".join(path) or "(root)"
            if not isinstance(name, str) or name == "" or any(c not in _TOKEN_CHARS for c in name):
                problems.append(f"x-mcp-header at {where} is not a valid header name")
            elif node.get("type") not in ("string", "integer", "boolean"):
                problems.append(f"x-mcp-header at {where} is on a property that is not a string, integer or boolean")
            elif not plain or not path:
                problems.append(f"x-mcp-header at {where} is not reachable through properties only")
            else:
                found.append((path, name, node["type"]))
        for key, value in node.items():
            if key == "properties" and isinstance(value, Mapping):
                for prop, sub in value.items():
                    walk(sub, path + (str(prop),), plain)
            elif key in _COMBINATORS or isinstance(value, (Mapping, list)):
                walk(value, path, False)

    walk(schema, (), True)
    names = [name.lower() for _, name, _ in found]
    if len(set(names)) != len(names):
        problems.append("x-mcp-header values are not unique")
    return found, ("; ".join(problems) if problems else None)


def _param_headers(paths: list[tuple[tuple[str, ...], str, str]], arguments: Any) -> dict[str, str]:
    headers = {}
    for path, name, _type in paths:
        value: Any = arguments
        for step in path:
            value = value.get(step) if isinstance(value, Mapping) else None
            if value is None:
                break
        if value is None:
            continue
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, str):
            text = header_value(value)
        elif isinstance(value, int) and abs(value) <= SAFE_INTEGER:
            text = str(value)
        elif isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value() \
                and abs(value) <= SAFE_INTEGER:
            text = str(int(value))
        else:
            raise MCPError("rejected", "refused_locally",
                           f"argument {'.'.join(path)} must be a string, integer or boolean, because the server "
                           "copies it into an HTTP header")
        headers["Mcp-Param-" + name] = text
    return headers


# --- one exchange --------------------------------------------------------------------------------
class _Conn(http.client.HTTPConnection):
    def __init__(self, scheme: str, host: str, port: int, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self._scheme = scheme

    def connect(self) -> None:
        sock = connect_socket(self.host, self.port, self.timeout)
        if self._scheme == "https":
            try:
                sock = make_ssl_context().wrap_socket(sock, server_hostname=self.host)
            except BaseException:
                sock.close()
                raise
        self.sock = sock


@dataclass
class Reply:
    status: int
    messages: list[dict]                   # JSON-RPC messages read (a stream may give several)
    response: dict | None                  # the message whose id matches, if any
    reading: str | None                    # None (fine), or "malformed" | "too_large" | "stream_ended"
    error_code: int | None = None
    error_text: str | None = None
    error_data: Any = None
    session_id: str | None = None
    content_type: str = ""


class _Budget:
    """Bytes read during one discovery, against DISCOVERY_LIMIT."""

    def __init__(self, limit: int | None):
        self.limit, self.total = limit, 0

    def add(self, n: int) -> None:
        self.total += n
        if self.limit is not None and self.total > self.limit:
            raise _TooLarge()


class _TooLarge(Exception):
    pass


def _transport_reason(exc: BaseException) -> str:
    if isinstance(exc, socket.gaierror):
        return "name_resolution"
    if isinstance(exc, (ssl.SSLError, ssl.CertificateError)):
        return "tls"
    if isinstance(exc, ConnectionRefusedError):
        return "connection_refused"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "idle_timeout"
    return "lost"


class MCPClient:
    """Era cache, discovery entries and legacy sessions for one AgentWiz process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()                 # era cache and discovery entries
        self._eras: dict[str, tuple[str, str]] = {}  # url -> (era, version)
        self._entries: dict[tuple, Entry] = {}
        self.session_lock = threading.Lock()          # Section 9.2's session lock
        self._sessions: dict[tuple, Session] = {}     # identity -> its open session
        self._all_sessions: list[Session] = []
        self._refused: dict[tuple, str] = {}          # identity -> unsupported negotiated version
        self._current: set[tuple] | None = None       # current identities (None: every identity is current)
        self._init_locks: dict[tuple, threading.Lock] = {}
        self._generation = itertools.count(1)
        # Called with a Session that is due its DELETE; mcp_ops replaces it with maintenance work.
        self.schedule_delete: Callable[[Session], None] = lambda s: None

    # --- views ------------------------------------------------------------------------------------
    def era_of(self, url: str) -> tuple[str, str] | None:
        with self._lock:
            return self._eras.get(url)

    def _set_era(self, url: str, era: str | None, version: str | None = None) -> None:
        with self._lock:
            if era is None:
                self._eras.pop(url, None)
            else:
                self._eras[url] = (era, version)
            if era != "modern":
                for identity in [i for i in self._entries if i[0] == url]:
                    del self._entries[identity]

    def entry(self, identity: tuple) -> Entry | None:
        with self._lock:
            return self._entries.get(identity)

    def _store_entry(self, identity: tuple, entry: Entry) -> None:
        with self._lock:
            if self._eras.get(identity[0], (None,))[0] == "modern":
                self._entries[identity] = entry
        self._drop_obsolete_entries()

    def _drop_entry(self, identity: tuple) -> None:
        with self._lock:
            self._entries.pop(identity, None)

    def _drop_obsolete_entries(self) -> None:
        with self.session_lock:
            current = self._current
        if current is None:
            return
        with self._lock:
            for identity in [i for i in self._entries if i not in current]:
                del self._entries[identity]

    # --- identities and sessions (Section 9.2) -----------------------------------------------------
    def set_current_identities(self, identities: set[tuple]) -> None:
        due: list[Session] = []
        with self.session_lock:
            self._current = set(identities)
            for identity in [i for i in self._refused if i not in self._current]:
                del self._refused[identity]
            due = self._due_locked()
        self._drop_obsolete_entries()
        for session in due:
            self.schedule_delete(session)

    def _eligible_locked(self, session: Session) -> bool:
        obsolete = self._current is not None and session.identity not in self._current
        return session.state == "open" and (obsolete or session.superseded)

    def _due_locked(self) -> list[Session]:
        due = []
        for session in self._all_sessions:
            if self._eligible_locked(session) and session.borrowers == 0 and not session.delete_scheduled \
                    and not session.delete_sent:
                session.delete_scheduled = True
                due.append(session)
        return due

    def _current_session(self, identity: tuple) -> Session | None:
        with self.session_lock:
            s = self._sessions.get(identity)
            return s if s is not None and s.state == "open" else None

    def borrow(self, ctx: OpContext, session: Session) -> bool:
        with self.session_lock:
            if session.state != "open":
                return False
            session.borrowers += 1
        ctx.borrowed.append(session)
        return True

    def release(self, session: Session) -> None:
        with self.session_lock:
            session.borrowers -= 1
            due = self._due_locked()
        for s in due:
            self.schedule_delete(s)

    def release_all(self, ctx: OpContext) -> None:
        """Called when the worker thread exits: releases the sessions it borrowed."""
        borrowed, ctx.borrowed = ctx.borrowed, []
        for session in borrowed:
            self.release(session)
        self._drop_obsolete_entries()

    def reserve_notice(self, session: Session) -> None:
        with self.session_lock:
            session.borrowers += 1

    def delete_gate(self, session: Session) -> bool:
        """At the DELETE's gate: one step under the session lock checks and marks the session ending."""
        with self.session_lock:
            session.delete_scheduled = False
            if not self._eligible_locked(session) or session.borrowers != 0 or session.delete_sent:
                return False
            session.state = "ending"
            session.delete_sent = True
            if self._sessions.get(session.identity) is session:
                del self._sessions[session.identity]
            return True

    def forget(self, session: Session) -> None:
        with self.session_lock:
            session.delete_scheduled = False
            if session in self._all_sessions and session.state == "ending":
                self._all_sessions.remove(session)

    def skip_delete(self, session: Session) -> None:
        """No maintenance slot: the session is forgotten, to expire on the server."""
        with self.session_lock:
            session.delete_scheduled = False
            session.state = "ending"
            session.delete_sent = True
            if self._sessions.get(session.identity) is session:
                del self._sessions[session.identity]
            if session in self._all_sessions:
                self._all_sessions.remove(session)

    def sessions_snapshot(self) -> list[Session]:
        with self.session_lock:
            return list(self._all_sessions)

    # --- transport --------------------------------------------------------------------------------
    def _headers(self, snap: Snapshot, *, era: str, method: str, version: str | None = None,
                 session: Session | None = None, extra: Mapping[str, str] | None = None) -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "User-Agent": f"AgentWiz/{__version__}"}
        if era == "modern":
            headers["MCP-Protocol-Version"] = MODERN
            headers["Mcp-Method"] = method
        elif version is not None:
            headers["MCP-Protocol-Version"] = version
            if session is not None and session.session_id is not None:
                headers["Mcp-Session-Id"] = session.session_id
        if snap.token:
            headers["Authorization"] = f"Bearer {snap.token}"
        if extra:
            headers.update(extra)
        return headers

    def _open(self, ctx: OpContext, snap: Snapshot) -> tuple[_Conn, socket.socket]:
        parts = urlsplit(snap.url)
        scheme = parts.scheme.lower()
        port = parts.port or (443 if scheme == "https" else 80)
        conn = _Conn(scheme, parts.hostname or "", port, timeout=float(snap.idle_timeout_s))
        conn.connect()
        sock = conn.sock
        try:
            ctx.register(sock)
        except Abandoned:
            conn.close()
            raise
        return conn, sock

    def _path(self, snap: Snapshot) -> str:
        parts = urlsplit(snap.url)
        return parts.path or "/"

    def exchange(self, ctx: OpContext, snap: Snapshot, method: str, body: Mapping | None, headers: dict, *,
                 request_id: int | None, gate: dict | None = None, budget: _Budget | None = None,
                 http_method: str = "POST", on_message: Callable[[dict], None] | None = None,
                 stage: str = "discovery") -> Reply:
        """One gated message and its reply. Raises Abandoned, or MCPError for a transport failure."""
        gate = gate or {}
        ctx.gate()                                         # before connecting
        try:
            conn, sock = self._open(ctx, snap)
        except Abandoned:
            raise
        except OSError as exc:
            self._raise_if_abandoned(ctx)
            reason = _transport_reason(exc)
            c = classify(stage, gate_passed=False, transport=reason)
            raise MCPError(c.outcome, c.reason, f"{_REASON_TEXT.get(c.reason, c.reason)} ({snap.server})") from None
        try:
            ctx.gate(post=True, **gate)                   # after connecting, immediately before writing
            data = encode_exact(body).encode("ascii") if body is not None else None
            try:
                conn.putrequest(http_method, self._path(snap), skip_accept_encoding=True)
                for k, v in headers.items():
                    conn.putheader(k, v)
                if data is not None:
                    conn.putheader("Content-Length", str(len(data)))
                conn.endheaders(data)
                resp = conn.getresponse()
                return self._read(resp, request_id, budget, on_message)
            except OSError as exc:
                self._raise_if_abandoned(ctx)
                passed = bool(gate.get("call"))
                c = classify(stage, gate_passed=passed, transport=_transport_reason(exc))
                raise MCPError(c.outcome, c.reason, f"{_REASON_TEXT.get(c.reason, c.reason)} ({snap.server})") from None
            except http.client.HTTPException:
                self._raise_if_abandoned(ctx)
                c = classify(stage, gate_passed=bool(gate.get("call")), reading="malformed")
                raise MCPError(c.outcome, c.reason, f"the reply was malformed ({snap.server})") from None
        finally:
            ctx.withdraw(sock)
            conn.close()

    @staticmethod
    def _raise_if_abandoned(ctx: OpContext) -> None:
        with ctx.lock:
            if ctx.abandoned is not None:
                raise Abandoned(ctx.abandoned)

    def _read(self, resp: http.client.HTTPResponse, request_id: int | None, budget: _Budget | None,
              on_message: Callable[[dict], None] | None) -> Reply:
        status = resp.status
        ctype = (resp.getheader("Content-Type") or "").split(";")[0].strip().lower()
        session_id = resp.getheader("Mcp-Session-Id")
        reply = Reply(status=status, messages=[], response=None, reading=None, session_id=session_id,
                      content_type=ctype)
        try:
            if status == 202 or (status == 200 and resp.getheader("Content-Length") == "0"):
                resp.read(0)
                return reply
            if ctype == "text/event-stream" and status == 200:
                self._read_stream(resp, reply, request_id, budget, on_message)
            else:
                raw = self._read_limited(resp, budget)
                if raw is None:          # over the limit
                    if status != 200:
                        return reply     # an error body past the limit: classified by status alone
                    reply.reading = "too_large"
                    return reply
                try:
                    doc = decode_exact(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    if status == 200:
                        reply.reading = "malformed"
                    else:
                        reply.error_text = raw.decode("utf-8", "replace")
                    return reply
                if isinstance(doc, dict):
                    reply.messages.append(doc)
                    self._absorb(reply, doc, request_id, single=True)
                elif status == 200:
                    reply.reading = "malformed"
        except _TooLarge:
            reply.reading = "too_large"
        return reply

    @staticmethod
    def _read_limited(resp, budget: _Budget | None) -> bytes | None:
        chunks, total = [], 0
        while True:
            chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if budget is not None:
                budget.add(len(chunk))
            if total > MESSAGE_LIMIT:
                return None
            chunks.append(chunk)
        return b"".join(chunks)

    def _absorb(self, reply: Reply, doc: dict, request_id: int | None, single: bool) -> bool:
        """Record a message; True when it is the matching response."""
        if "id" in doc and ("result" in doc or "error" in doc) and "method" not in doc:
            if doc.get("id") != request_id:
                if single:
                    reply.reading = "malformed"
                return False
            reply.response = doc
            err = doc.get("error")
            if err is not None:
                if isinstance(err, Mapping) and isinstance(err.get("code"), int) and not isinstance(err.get("code"), bool):
                    reply.error_code = err["code"]
                    reply.error_text = err.get("message") if isinstance(err.get("message"), str) else None
                    reply.error_data = err.get("data")
                else:
                    reply.reading = "malformed"
            return True
        if single:
            reply.reading = "malformed"
        return False

    def _read_stream(self, resp, reply: Reply, request_id: int | None, budget: _Budget | None,
                     on_message: Callable[[dict], None] | None) -> None:
        data_parts: list[bytes] = []
        data_size = 0
        while True:
            line = resp.readline(MESSAGE_LIMIT + 2)
            if budget is not None:
                budget.add(len(line))
            if not line:
                reply.reading = reply.reading or "stream_ended"
                return
            if len(line) > MESSAGE_LIMIT and not line.endswith(b"\n"):
                reply.reading = "too_large"
                return
            stripped = line.rstrip(b"\r\n")
            if stripped == b"":
                if data_parts:
                    raw = b"\n".join(data_parts)
                    data_parts, data_size = [], 0
                    try:
                        doc = decode_exact(raw.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        reply.reading = "malformed"
                        return
                    if not isinstance(doc, dict):
                        reply.reading = "malformed"
                        return
                    reply.messages.append(doc)
                    if self._absorb(reply, doc, request_id, single=False):
                        return
                    if on_message is not None and "method" in doc:
                        on_message(doc)
                continue
            if stripped.startswith(b":"):
                continue
            name, _, value = stripped.partition(b":")
            if name == b"data":
                value = value[1:] if value.startswith(b" ") else value
                data_size += len(value) + 1
                if data_size > MESSAGE_LIMIT:
                    reply.reading = "too_large"
                    return
                data_parts.append(value)

    # --- failures ---------------------------------------------------------------------------------
    def _server_text(self, snap: Snapshot, reply: Reply) -> str | None:
        """Server-supplied text, only when no token is held for the identity (R2-16)."""
        if snap.token or not reply.error_text:
            return None
        text = reply.error_text
        if len(text) > SERVER_TEXT_LIMIT:
            text = text[:SERVER_TEXT_LIMIT] + f" ({len(reply.error_text) - SERVER_TEXT_LIMIT} more characters not shown)"
        return text

    def _fail(self, snap: Snapshot, stage: str, reply: Reply, *, gate_passed: bool, era: str) -> MCPError:
        status = reply.status if reply.status != 200 else None
        if status is None and reply.response is None and reply.reading is None:
            reading = "accepted" if reply.status == 202 else "stream_ended"
        else:
            reading = reply.reading
        code = reply.error_code
        c = classify(stage, gate_passed=gate_passed, status=status, jsonrpc_code=code if status is None else None,
                     reading=reading)
        parts = [_REASON_TEXT.get(c.reason, c.reason)]
        if status is not None:
            parts.append(f"HTTP {status}")
        if code is not None:
            parts.append(f"JSON-RPC {code}")
        description = f"{', '.join(parts)} (server {snap.server}, MCP {era})"
        return MCPError(c.outcome, c.reason, description, http_status=status if status is not None else None,
                        jsonrpc_code=code, server_text=self._server_text(snap, reply))

    # --- requests ---------------------------------------------------------------------------------
    @staticmethod
    def _modern_body(method: str, params: dict, request_id: int | None) -> dict:
        meta = {"io.modelcontextprotocol/protocolVersion": MODERN,
                "io.modelcontextprotocol/clientCapabilities": {},
                "io.modelcontextprotocol/clientInfo": client_info()}
        params = dict(params)
        params["_meta"] = {**meta, **params.get("_meta", {})}
        body = {"jsonrpc": "2.0", "method": method, "params": params}
        if request_id is not None:
            body["id"] = request_id
        return body

    def probe(self, ctx: OpContext, snap: Snapshot, stage: str = "discovery") -> tuple[str, str, dict | None]:
        """`server/discover`; caches and returns (era, version, self-reported server info)."""
        rid = next_id()
        body = self._modern_body("server/discover", {}, rid)
        reply = self.exchange(ctx, snap, "server/discover", body,
                              self._headers(snap, era="modern", method="server/discover"),
                              request_id=rid, stage=stage)
        status, code = reply.status, reply.error_code
        if status in (401, 403):
            raise self._fail(snap, stage, reply, gate_passed=False, era="unknown")
        if 300 <= status < 400:
            raise self._fail(snap, stage, reply, gate_passed=False, era="unknown")
        if status == 200 and reply.response is not None and "result" in reply.response:
            result = reply.response["result"]
            versions = result.get("supportedVersions") if isinstance(result, Mapping) else None
            versions = [v for v in versions if isinstance(v, str)] if isinstance(versions, list) else []
            info = _server_info(result)
            if MODERN in versions:
                self._set_era(snap.url, "modern", MODERN)
                return "modern", MODERN, info
            if any(v in LEGACY for v in versions):
                self._set_era(snap.url, "legacy", None)
                return "legacy", None, info
            raise self._unsupported(snap, versions, stage)
        if status == 400 and code == -32022:
            data = reply.error_data
            supported = data.get("supported") if isinstance(data, Mapping) else None
            supported = [v for v in supported if isinstance(v, str)] if isinstance(supported, list) else []
            if any(v in LEGACY for v in supported):
                self._set_era(snap.url, "legacy", None)
                return "legacy", None, None
            raise self._unsupported(snap, supported, stage)
        if status == 400 and code in (-32020, -32021):
            raise self._fail(snap, stage, reply, gate_passed=False, era="modern")
        self._set_era(snap.url, "legacy", None)
        return "legacy", None, None

    def _unsupported(self, snap: Snapshot, versions: list[str], stage: str) -> MCPError:
        outcome = "error" if stage == "discovery" else "rejected"
        theirs = ", ".join(versions) or "none"
        return MCPError(outcome, "unsupported_protocol",
                        f"the server speaks MCP {theirs}; AgentWiz speaks {MODERN} and {', '.join(LEGACY)} "
                        f"(server {snap.server})")

    def _list_pages(self, ctx: OpContext, snap: Snapshot, era: str, session: Session | None,
                    stage: str) -> list[dict]:
        tools: list[dict] = []
        seen: set[str] = set()
        cursor: str | None = None
        budget = _Budget(DISCOVERY_LIMIT)
        while True:
            rid = next_id()
            params = {} if cursor is None else {"cursor": cursor}
            if era == "modern":
                body = self._modern_body("tools/list", params, rid)
                headers = self._headers(snap, era="modern", method="tools/list")
            else:
                body = {"jsonrpc": "2.0", "id": rid, "method": "tools/list", "params": params}
                headers = self._headers(snap, era="legacy", method="tools/list", version=session.version,
                                        session=session)
            reply = self._legacy_retry(ctx, snap, session, body, headers, rid, budget, stage, "tools/list") \
                if era == "legacy" else self.exchange(ctx, snap, "tools/list", body, headers, request_id=rid,
                                                      budget=budget, stage=stage)
            if reply.reading == "too_large" or budget.total > DISCOVERY_LIMIT:
                raise MCPError("error" if stage == "discovery" else "rejected", "too_large",
                               f"the tool list is larger than AgentWiz reads (server {snap.server})")
            if reply.status != 200 or reply.response is None or reply.error_code is not None or reply.reading:
                if era == "modern" and reply.status == 400 and _non_modern(reply):
                    self._set_era(snap.url, None)
                raise self._fail(snap, stage, reply, gate_passed=False, era=era) if stage == "discovery" else \
                    _as_rejected(self._fail(snap, "discovery", reply, gate_passed=False, era=era))
            result = reply.response.get("result")
            if not isinstance(result, Mapping) or not isinstance(result.get("tools"), list) or \
                    any(not isinstance(t, Mapping) or not isinstance(t.get("name"), str)
                        or not isinstance(t.get("inputSchema"), Mapping) for t in result["tools"]):
                raise MCPError("error" if stage == "discovery" else "rejected", "malformed",
                               f"the tool list was malformed (server {snap.server}, MCP {era})")
            tools.extend(result["tools"])
            if len(tools) > TOOL_LIMIT:
                raise MCPError("error" if stage == "discovery" else "rejected", "too_large",
                               f"the server lists more than {TOOL_LIMIT} tools (server {snap.server})")
            nxt = result.get("nextCursor")
            if nxt is None:
                return tools
            if not isinstance(nxt, str):
                raise MCPError("error" if stage == "discovery" else "rejected", "malformed",
                               f"the tool list's cursor was malformed (server {snap.server})")
            if nxt in seen:
                raise MCPError("error" if stage == "discovery" else "rejected", "malformed",
                               f"the server repeated a tool-list cursor (server {snap.server})")
            seen.add(nxt)
            cursor = nxt

    @staticmethod
    def _entry_from(tools: list[dict], version: str) -> Entry:
        offered, not_offered = {}, {}
        for tool in tools:
            paths, problem = _header_params(tool.get("inputSchema"))
            if problem:
                not_offered[tool["name"]] = problem
            else:
                offered[tool["name"]] = paths
        return Entry(version, offered, not_offered)

    def discover(self, ctx: OpContext, snap: Snapshot) -> DiscoveryResult:
        """Load tools: probe (always), then initialize if legacy, then every tools/list page."""
        era, version, info = self.probe(ctx, snap)
        if era == "modern":
            tools = self._list_pages(ctx, snap, "modern", None, "discovery")
            entry = self._entry_from(tools, MODERN)
            self._store_entry(snap.identity, entry)
            offered = [t for t in tools if t["name"] in entry.offered]
            return DiscoveryResult("modern", MODERN, info, offered,
                                   [{"name": n, "reason": r} for n, r in entry.not_offered.items()],
                                   {n: [".".join(p) for p, _, _ in paths] for n, paths in entry.offered.items()})
        session = self._session(ctx, snap, "discovery")
        tools = self._list_pages(ctx, snap, "legacy", session, "discovery")
        return DiscoveryResult("legacy", session.version, info, tools)

    # --- legacy sessions --------------------------------------------------------------------------
    def _session(self, ctx: OpContext, snap: Snapshot, stage: str, *, replacing: Session | None = None) -> Session:
        identity = snap.identity
        with self.session_lock:
            init_lock = self._init_locks.setdefault(identity, threading.Lock())
        with init_lock:
            current = self._current_session(identity)
            if current is not None and current is not replacing:
                if self.borrow(ctx, current):
                    return current
            with self.session_lock:
                refused = self._refused.get(identity)
            if refused is not None:
                raise self._unsupported(snap, [refused], stage)
            session = self._initialize(ctx, snap, stage)
            with self.session_lock:
                old = self._sessions.get(identity)
                if old is not None and old is not session:
                    old.superseded = True
                self._sessions[identity] = session
                self._all_sessions.append(session)
                session.borrowers += 1
                due = self._due_locked()
            ctx.borrowed.append(session)
        for s in due:
            self.schedule_delete(s)
        return session

    def _initialize(self, ctx: OpContext, snap: Snapshot, stage: str) -> Session:
        rid = next_id()
        body = {"jsonrpc": "2.0", "id": rid, "method": "initialize",
                "params": {"protocolVersion": OFFERED_LEGACY, "capabilities": {}, "clientInfo": client_info()}}
        reply = self.exchange(ctx, snap, "initialize", body, self._headers(snap, era="legacy", method="initialize"),
                              request_id=rid, stage=stage)
        if reply.status != 200 or reply.response is None or reply.error_code is not None or reply.reading:
            err = self._fail(snap, "discovery", reply, gate_passed=False, era="legacy")
            if stage == "call":
                if reply.status not in (401, 403) and (400 <= reply.status < 500 or reply.error_code is not None):
                    self._set_era(snap.url, None)
                    raise MCPError("rejected", "era_changed",
                                   f"the server no longer answers as MCP legacy; press Load tools to detect its "
                                   f"protocol again (server {snap.server})", http_status=err.http_status,
                                   jsonrpc_code=err.jsonrpc_code, server_text=err.server_text)
                raise _as_rejected(err)
            raise err
        result = reply.response.get("result")
        version = result.get("protocolVersion") if isinstance(result, Mapping) else None
        if version not in LEGACY:
            with self.session_lock:
                self._refused[snap.identity] = str(version)
            raise self._unsupported(snap, [str(version)], stage)
        session = Session(snap.identity, reply.session_id, version, next(self._generation))
        self._set_era(snap.url, "legacy", version)
        note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        self.exchange(ctx, snap, "notifications/initialized", note,
                      self._headers(snap, era="legacy", method="notifications/initialized", version=version,
                                    session=session), request_id=None, stage=stage)
        return session

    def _legacy_retry(self, ctx: OpContext, snap: Snapshot, session: Session, body: dict, headers: dict,
                      rid: int, budget: _Budget | None, stage: str, method: str, *,
                      gate: dict | None = None, on_message=None) -> Reply:
        """A legacy request; a 404 to a request with a session ID re-initialises and resends once."""
        reply = self.exchange(ctx, snap, method, body, headers, request_id=rid, budget=budget, stage=stage,
                              gate=gate, on_message=on_message)
        if reply.status == 404 and session.session_id is not None:
            with self.session_lock:
                still_current = self._sessions.get(session.identity) is session
            if still_current:
                with self.session_lock:
                    session.superseded = True
            new = self._session(ctx, snap, stage, replacing=session)
            if gate and gate.get("call"):
                gate = {**gate, "session": new}
            headers = self._headers(snap, era="legacy", method=method, version=new.version, session=new)
            reply = self.exchange(ctx, snap, method, body, headers, request_id=rid, budget=budget, stage=stage,
                                  gate=gate, on_message=on_message)
        return reply

    def _answer(self, ctx: OpContext, snap: Snapshot, session: Session, message: dict) -> None:
        """Answer a legacy server request on a second, gated connection; failures are ignored."""
        if "id" not in message:
            return
        if message.get("method") == "ping":
            body = {"jsonrpc": "2.0", "id": message["id"], "result": {}}
        else:
            body = {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "Method not found"}}
        idle = min(float(snap.idle_timeout_s), max(0.001, ctx.deadline - time.monotonic()))
        answer_snap = Snapshot(snap.server, snap.url, idle, snap.call_deadline_s, snap.discovery_deadline_s, snap.token)
        try:
            self.exchange(ctx, answer_snap, "answer", body,
                          self._headers(snap, era="legacy", method="answer", version=session.version, session=session),
                          request_id=None, stage="discovery")
        except MCPError:
            pass

    # --- calls -------------------------------------------------------------------------------------
    def call(self, ctx: OpContext, snap: Snapshot, tool: str, arguments: Any) -> CallResult:
        """One tools/call. Raises MCPError (rejected or unknown) or Abandoned."""
        ctx.snapshot = snap
        cached = self.era_of(snap.url)
        if cached is None:
            era, version, _ = self.probe(ctx, snap, stage="call")
        else:
            era, version = cached
        if era == "modern":
            return self._modern_call(ctx, snap, tool, arguments)
        return self._legacy_call(ctx, snap, tool, arguments)

    def _modern_call(self, ctx: OpContext, snap: Snapshot, tool: str, arguments: Any) -> CallResult:
        entry = self.entry(snap.identity)
        if entry is None or (tool not in entry.offered and tool not in entry.not_offered):
            tools = self._list_pages(ctx, snap, "modern", None, "call")
            entry = self._entry_from(tools, MODERN)
            self._store_entry(snap.identity, entry)
        if tool in entry.not_offered:
            raise MCPError("rejected", "not_offered", f"tool {tool} is not offered: {entry.not_offered[tool]}")
        if tool not in entry.offered:
            raise MCPError("rejected", "unknown_tool", f"the server does not list tool {tool}; press Load tools")
        extra = _param_headers(entry.offered[tool], arguments)
        extra["Mcp-Name"] = header_value(tool)
        rid, token = next_id(), next_id()
        body = self._modern_body("tools/call", {"name": tool, "arguments": arguments,
                                                "_meta": {"progressToken": token}}, rid)
        headers = self._headers(snap, era="modern", method="tools/call", extra=extra)
        reply = self.exchange(ctx, snap, "tools/call", body, headers, request_id=rid, stage="call",
                              gate={"call": True, "request_id": rid},
                              on_message=lambda m: self._notification(ctx, m, token))
        if reply.status == 400 and (_non_modern(reply) or reply.error_code == -32022):
            self._set_era(snap.url, None)
            raise MCPError("rejected", "era_changed",
                           f"the server no longer answers as MCP modern; press Load tools to detect its protocol "
                           f"again (server {snap.server})", http_status=400, jsonrpc_code=reply.error_code,
                           server_text=self._server_text(snap, reply))
        if reply.error_code == -32020:
            self._drop_entry(snap.identity)
            err = self._fail(snap, "call", reply, gate_passed=True, era="modern")
            err.description += "; press Load tools"
            raise err
        return self._call_result(snap, reply, "modern", MODERN)

    def _legacy_call(self, ctx: OpContext, snap: Snapshot, tool: str, arguments: Any) -> CallResult:
        session = self._session(ctx, snap, "call")
        rid, token = next_id(), next_id()
        body = {"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                "params": {"name": tool, "arguments": arguments, "_meta": {"progressToken": token}}}
        headers = self._headers(snap, era="legacy", method="tools/call", version=session.version, session=session)

        def on_message(message: dict) -> None:
            if "id" in message and "method" in message:
                current = ctx.call_session or session
                self._answer(ctx, snap, current, message)
            else:
                self._notification(ctx, message, token)

        reply = self._legacy_retry(ctx, snap, session, body, headers, rid, None, "call", "tools/call",
                                   gate={"call": True, "session": session, "request_id": rid},
                                   on_message=on_message)
        used = ctx.call_session or session
        return self._call_result(snap, reply, "legacy", used.version)

    def _notification(self, ctx: OpContext, message: dict, token: int) -> None:
        if message.get("method") != "notifications/progress" or "id" in message:
            return
        params = message.get("params")
        if not isinstance(params, Mapping) or params.get("progressToken") != token:
            return
        def text(v):
            return None if v is None else (encode_exact(v) if not isinstance(v, str) else v)
        try:
            progress = text(params.get("progress"))
            total = text(params.get("total"))
        except (TypeError, ValueError):
            return
        message_text = params.get("message") if isinstance(params.get("message"), str) else None
        if progress is not None:
            ctx.on_progress(progress, total, message_text)

    def _call_result(self, snap: Snapshot, reply: Reply, era: str, version: str) -> CallResult:
        if reply.status != 200 or reply.response is None or reply.error_code is not None or reply.reading:
            raise self._fail(snap, "call", reply, gate_passed=True, era=era)
        result = reply.response.get("result")
        if not _valid_result(result):
            raise MCPError("unknown", "malformed", f"the tool result was malformed (server {snap.server}, MCP {era})")
        if result.get("resultType") == "input_required":
            raise MCPError("unknown", "input_required",
                           "the server asked for input AgentWiz cannot provide; the call did not complete")
        return CallResult("tool_error" if result.get("isError") is True else "ok", era, version, result)

    # --- maintenance (sent by mcp_ops) -------------------------------------------------------------
    def send_notice(self, ctx: OpContext, snap: Snapshot, session: Session, request_id: int) -> bool:
        """`notifications/cancelled` for a legacy call; True when the server answered 202 (or 2xx)."""
        body = {"jsonrpc": "2.0", "method": "notifications/cancelled",
                "params": {"requestId": request_id, "reason": "stopped by the user"}}
        reply = self.exchange(ctx, snap, "notifications/cancelled", body,
                              self._headers(snap, era="legacy", method="notifications/cancelled",
                                            version=session.version, session=session), request_id=None)
        return 200 <= reply.status < 300

    def send_delete(self, ctx: OpContext, snap: Snapshot, session: Session) -> None:
        headers = self._headers(snap, era="legacy", method="DELETE", version=session.version, session=session)
        headers.pop("Content-Type", None)
        self.exchange(ctx, snap, "DELETE", None, headers, request_id=None, http_method="DELETE",
                      gate={})


def _as_rejected(err: MCPError) -> MCPError:
    err.outcome = "rejected"
    return err


def _non_modern(reply: Reply) -> bool:
    """A 400 that is not a recognised modern error: no JSON-RPC error, or the handshake era's -32600."""
    return reply.error_code is None or reply.error_code == -32600


def _server_info(result: Any) -> dict | None:
    if not isinstance(result, Mapping):
        return None
    candidates = [result.get("serverInfo")]
    meta = result.get("_meta")
    if isinstance(meta, Mapping):
        candidates += [meta.get("io.modelcontextprotocol/serverInfo"), meta.get("serverInfo")]
    for info in candidates:
        if isinstance(info, Mapping):
            name, version = info.get("name"), info.get("version")
            return {"name": name if isinstance(name, str) else None,
                    "version": version if isinstance(version, str) else None}
    return None


def _valid_result(result: Any) -> bool:
    if not isinstance(result, Mapping):
        return False
    if "isError" in result and not isinstance(result["isError"], bool):
        return False
    if "resultType" in result and result["resultType"] not in ("complete", "input_required"):
        return False
    content = result.get("content")
    if content is not None:
        if not isinstance(content, list):
            return False
        for item in content:
            if not isinstance(item, Mapping) or not isinstance(item.get("type"), str):
                return False
            if item["type"] == "text" and not isinstance(item.get("text"), str):
                return False
    return True


_REASON_TEXT = {
    "not_sent_stopped": "stopped before sending", "not_sent_deadline": "deadline before sending",
    "refused_locally": "refused before sending", "not_offered": "the tool is not offered",
    "unknown_tool": "the server does not list the tool", "name_resolution": "the server's name could not be resolved",
    "connection_refused": "the connection was refused", "tls": "the TLS connection failed",
    "http_status": "the server reported a failure", "jsonrpc_error": "the server returned a JSON-RPC error",
    "redirect": "the server redirected; redirects are not followed",
    "credentials": "the server refused the credentials", "stream_ended": "the connection ended before the response",
    "malformed": "the reply was malformed", "too_large": "the reply was larger than AgentWiz reads",
    "input_required": "the server asked for input AgentWiz cannot provide", "stopped": "stopped",
    "deadline": "the call deadline passed", "idle_timeout": "no data within the idle timeout",
    "page_closed": "the page went away", "unsupported_protocol": "unsupported protocol version",
    "era_changed": "the server's protocol changed"}


def reason_text(reason: str | None) -> str:
    return _REASON_TEXT.get(reason or "", reason or "")
