"""The shared MCP server library: `<mcp-servers-dir>/servers.json` (techspec 0.1.2, Section 9.1; R2-15, D39).

One library shared by every workspace and every AgentWiz process; no workspace copies. Like the shared
profile defaults: validated whole on every read, written whole and atomically (mode 0600), never
written when it is damaged or newer, unknown fields kept. Changes by other processes are noticed by
inode, modification time and size. The permanent `.servers.lock` is held with a blocking `flock` only
around each read-change-replace, after the state lock, and is never replaced or deleted.

Call mutations from state.py under its lock. Holds no tokens: only environment-variable names.
Messages name files, JSON locations and rules, never values. Standard library only; no HTTP.
"""
from __future__ import annotations

import copy
import fcntl
import os
import re
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .workspace import (NAME_RE, NAME_RULE, InvalidDocument, Problem, check_format_version, read_document,
                        write_atomic)

FILE = "servers.json"
LOCK_FILE = ".servers.lock"
FORMAT_VERSION = 1
TOKEN_ENV_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
LIMITS = {"idle_timeout_s": (1, 600, 30), "call_deadline_s": (1, 3600, 120), "discovery_deadline_s": (1, 600, 20)}
FIELDS = ("url", *LIMITS, "token_env")


@dataclass(frozen=True)
class ServerConfig:
    url: str
    idle_timeout_s: int = 30
    call_deadline_s: int = 120
    discovery_deadline_s: int = 20
    token_env: str | None = None

    def view(self) -> dict:
        return {f: getattr(self, f) for f in FIELDS}


def url_problem(url: object) -> str | None:
    """The URL rule (R1-7), or None when the URL is acceptable."""
    if not isinstance(url, str):
        return "must be a string"
    if url == "" or any(not 0x21 <= ord(c) <= 0x7E for c in url):
        return "must be visible ASCII with no whitespace"
    lower = url.lower()
    if not (lower.startswith("http://") or lower.startswith("https://")):
        return "must start with http:// or https://"
    rest = url.split("://", 1)[1]
    host = rest.split("/", 1)[0]
    if host == "" or host.startswith(":"):
        return "must have a host"
    if any(c in url for c in "@?#"):
        return "must not contain a user name, password, query or fragment (use the token for credentials)"
    return None


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def check_server(name: object, obj: object, where: str = "servers") -> ServerConfig:
    """Validate one entry; raises InvalidDocument naming the server and field, never a value."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise InvalidDocument(f"{where} has a server name that breaks the name rule ({NAME_RULE})")
    at = f"{where}.{name}"
    if not isinstance(obj, Mapping):
        raise InvalidDocument(f"{at} must be an object")
    if "url" not in obj:
        raise InvalidDocument(f"{at}.url is missing")
    problem = url_problem(obj["url"])
    if problem:
        raise InvalidDocument(f"{at}.url {problem}")
    values: dict[str, Any] = {"url": obj["url"]}
    for key, (low, high, default) in LIMITS.items():
        value = obj.get(key, default)
        if not _is_int(value) or not low <= value <= high:
            raise InvalidDocument(f"{at}.{key} must be an integer from {low} to {high:,}")
        values[key] = value
    token_env = obj.get("token_env")
    if token_env is not None and (not isinstance(token_env, str) or not TOKEN_ENV_RE.match(token_env)):
        raise InvalidDocument(f"{at}.token_env must be null or an environment-variable name")
    values["token_env"] = token_env
    return ServerConfig(**values)


@dataclass(frozen=True)
class LibraryFile:
    problem: Problem | None
    exists: bool = False
    servers: dict[str, ServerConfig] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


def empty_raw() -> dict:
    return {"format_version": FORMAT_VERSION, "servers": {}}


class Library:
    def __init__(self, folder: Path):
        self.folder = Path(folder).resolve()
        self.path = self.folder / FILE

    def ensure(self) -> None:
        """Create the folder and the permanent lock file if missing."""
        self.folder.mkdir(parents=True, exist_ok=True)
        os.close(os.open(self.folder / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600))

    @contextmanager
    def locked(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.folder / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield self
        finally:
            os.close(fd)

    def fingerprint(self) -> tuple | None:
        try:
            st = self.path.stat()
            return st.st_ino, st.st_mtime_ns, st.st_size
        except FileNotFoundError:
            return None
        except OSError as exc:
            return ("unreadable", exc.errno)

    def read(self) -> LibraryFile:
        raw, problem = read_document(self.path, str(self.path))
        if problem is not None:
            return LibraryFile(problem)
        if raw is None:
            return LibraryFile(None, exists=False, raw=empty_raw())
        try:
            if check_format_version(raw) > FORMAT_VERSION:
                return LibraryFile(Problem(str(self.path), "newer_format",
                                           f"written by a newer AgentWiz (format_version greater than {FORMAT_VERSION})"))
            if not isinstance(raw.get("servers"), dict):
                raise InvalidDocument("servers must be an object")
            servers = {name: check_server(name, obj) for name, obj in raw["servers"].items()}
        except InvalidDocument as exc:
            return LibraryFile(Problem(str(self.path), "invalid_field", str(exc)))
        return LibraryFile(None, exists=True, servers=servers, raw=raw)

    def write(self, raw: Mapping[str, Any]) -> None:
        write_atomic(self.folder, FILE, raw)


def parse_servers(body: object) -> dict[str, ServerConfig]:
    """The dialog's whole list from a save request; raises InvalidDocument naming the server and field."""
    if not isinstance(body, Mapping):
        raise InvalidDocument("servers must be an object")
    return {name: check_server(name, obj) for name, obj in body.items()}


def build_raw(previous: Mapping, servers: Mapping[str, ServerConfig]) -> dict:
    """The new file, keeping unknown top-level fields and unknown fields of servers that keep their name."""
    raw = copy.deepcopy(dict(previous))
    raw["format_version"] = FORMAT_VERSION
    old = previous.get("servers") if isinstance(previous.get("servers"), Mapping) else {}
    out = {}
    for name, config in servers.items():
        entry = {k: v for k, v in (old.get(name) or {}).items() if k not in FIELDS} \
            if isinstance(old.get(name), Mapping) else {}
        entry.update(config.view())
        out[name] = entry
    raw["servers"] = out
    return raw
