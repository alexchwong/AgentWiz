"""Workspace folders, the workspace lock and AgentWiz's own files (techspec 7.5; R1-1, R1-2, R1-3, R1-6).

A workspace is a folder `<workspaces_dir>/<name>/` holding `workspace.json`, `model-profiles/` and
`.agentwiz.lock`. One process holds a workspace at a time: `.agentwiz.lock` is opened once and held
with `flock` for the life of the process, so the kernel releases it however the process ends. The
lock file is never replaced, renamed or deleted, because a new file at that path would have a new
inode that another process could lock.

Files are validated whole on every read, written whole through a temporary file in the same folder
and renamed over the target, and never written at all when they are damaged or newer. Unknown
fields are carried over. Problem messages name files, JSON locations and rules, never values.

Mutating functions take no lock of their own: `state.py` calls them with the state lock held.
Knows nothing about HTTP. POSIX only (`fcntl`).
"""
from __future__ import annotations

import datetime
import fcntl
import os
import re
import socket
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .jsonio import decode_file, encode_file

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
FORMAT_VERSION = 1
NAME_RULE = "1 to 64 letters, digits, '.', '_' or '-', starting with a letter or digit"

WORKSPACE_FILE = "workspace.json"
LOCK_FILE = ".agentwiz.lock"


def valid_name(value: object) -> bool:
    """True for a str matching NAME_RE. A predicate, not an argparse type."""
    return isinstance(value, str) and NAME_RE.match(value) is not None


class WorkspaceError(Exception):
    """The workspace cannot be opened. The message is AgentWiz's text plus an OS strerror."""


class WorkspaceInUse(WorkspaceError):
    def __init__(self, holder: dict):
        super().__init__("the workspace is in use by another AgentWiz process")
        self.holder = holder  # subset of {"pid": int, "host": str, "started": str}; may be empty


@dataclass(frozen=True)
class Problem:
    file: str      # workspace.json or an absolute profile-library path
    code: str      # "unreadable" | "invalid_json" | "invalid_field" | "newer_format"
    message: str   # AgentWiz's own text: JSON location and rule; never a value or exception text


class InvalidDocument(Exception):
    """A field rule was broken; the message is the location and the rule."""


def _strerror(exc: OSError) -> str:
    return exc.strerror or type(exc).__name__


# --- reading and validating -------------------------------------------------------------------
def read_document(path: Path, file: str) -> tuple[dict | None, Problem | None]:
    """(top-level object, None), (None, None) if missing, or (None, problem)."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, Problem(file, "unreadable", f"the file cannot be read ({_strerror(exc)})")
    try:
        return decode_file(data), None
    except ValueError as exc:  # decode_file's messages are AgentWiz's own text
        return None, Problem(file, "invalid_json", str(exc))


def check_format_version(obj: dict) -> int:
    """Return the version; raise InvalidDocument if missing or not an integer of at least 1."""
    if "format_version" not in obj:
        raise InvalidDocument("format_version is missing")
    value = obj["format_version"]
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise InvalidDocument("format_version must be an integer of at least 1")
    return value


_NEWER = f"written by a newer AgentWiz (format_version greater than {FORMAT_VERSION})"


def _check_workspace(obj: dict) -> Problem | None:
    try:
        if check_format_version(obj) > FORMAT_VERSION:
            return Problem(WORKSPACE_FILE, "newer_format", _NEWER)
        if not isinstance(obj.get("created_by"), str):
            raise InvalidDocument("created_by must be a string")
        active = obj.get("active_profile")
        if active is not None and not valid_name(active):
            raise InvalidDocument("active_profile must be null or a valid profile name")
        if not isinstance(obj.get("save_runs"), bool):
            raise InvalidDocument("save_runs must be true or false")
    except InvalidDocument as exc:
        return Problem(WORKSPACE_FILE, "invalid_field", str(exc))
    return None


# --- writing ----------------------------------------------------------------------------------
def write_atomic(folder: Path, file: str, payload: Mapping[str, Any]) -> None:
    """Write `payload` to folder/file through a temporary file, fsync and os.replace (7.5.4).

    On OSError the temporary file is removed (best effort), the target is unchanged, and the error
    propagates.
    """
    data = encode_file(payload)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix="." + file + ".", suffix=".tmp")
    try:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, folder / file)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --- the workspace ----------------------------------------------------------------------------
class Workspace:
    def __init__(self, name: str, path: Path, fd: int, created: bool, problems: list[Problem]):
        self.name = name
        self.path = path              # absolute
        self.created = created        # workspace.json was created by this open
        self.problems = problems      # workspace-level problems
        self.read_only = bool(problems)
        self._fd: int | None = fd

    def close(self) -> None:
        """Close the lock descriptor, releasing the lock. Idempotent. The lock file stays."""
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)

    def __repr__(self) -> str:
        return f"Workspace(name={self.name!r}, path={str(self.path)!r}, read_only={self.read_only})"


def _holder(fd: int) -> dict:
    try:
        details = decode_file(os.pread(fd, 4096, 0))
    except (OSError, ValueError):
        return {}
    holder: dict = {}
    pid = details.get("pid")
    if isinstance(pid, int) and not isinstance(pid, bool):
        holder["pid"] = pid
    for key in ("host", "started"):
        if isinstance(details.get(key), str):
            holder[key] = details[key]
    return holder


def open_workspace(workspaces_dir: Path, name: str) -> Workspace:
    """Create the workspace if needed, take its lock, and read workspace.json (techspec 7.5.2)."""
    if not valid_name(name):
        raise WorkspaceError(f"invalid workspace name; use {NAME_RULE}")
    path = Path(workspaces_dir).resolve() / name
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkspaceError(f"cannot create the folder ({_strerror(exc)})") from None
    try:
        fd = os.open(path / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)  # non-inheritable (PEP 446)
    except OSError as exc:
        raise WorkspaceError(f"cannot open {LOCK_FILE} ({_strerror(exc)})") from None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        holder = _holder(fd)
        os.close(fd)
        raise WorkspaceInUse(holder) from None
    except OSError as exc:
        os.close(fd)
        raise WorkspaceError(f"the folder's filesystem does not support the workspace lock "
                             f"({_strerror(exc)})") from None

    # In place on the held descriptor: the lock file is never replaced.
    details = {"pid": os.getpid(), "host": socket.gethostname(),
               "started": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
    try:
        os.ftruncate(fd, 0)
        os.pwrite(fd, encode_file(details), 0)
    except OSError:
        pass  # the details only serve the in-use message

    created = False
    problems: list[Problem] = []
    try:
        obj, problem = read_document(path / WORKSPACE_FILE, WORKSPACE_FILE)
        if problem is None and obj is None:
            try:
                write_atomic(path, WORKSPACE_FILE, {"format_version": FORMAT_VERSION,
                                                    "created_by": "AgentWiz " + __version__,
                                                    "save_runs": False, "active_profile": None})
            except OSError as exc:
                raise WorkspaceError(f"cannot create {WORKSPACE_FILE} ({_strerror(exc)})") from None
            created = True
        elif problem is None:
            problem = _check_workspace(obj)
        if problem is not None:
            problems.append(problem)
    except BaseException:
        os.close(fd)
        raise
    return Workspace(name, path, fd, created, problems)


def read_workspace(ws: Workspace) -> tuple[dict | None, Problem | None]:
    raw, problem = read_document(ws.path / WORKSPACE_FILE, WORKSPACE_FILE)
    if raw is not None and problem is None:
        problem = _check_workspace(raw)
    if raw is None and problem is None:
        problem = Problem(WORKSPACE_FILE, "unreadable", "workspace.json is missing")
    return raw, problem


def write_workspace(ws: Workspace, raw: Mapping[str, Any]) -> None:
    write_atomic(ws.path, WORKSPACE_FILE, raw)
