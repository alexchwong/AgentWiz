"""Workspace folders, the workspace lock and AgentWiz's own files (techspec 7.5; R1-1, R1-2, R1-3, R1-6).

A workspace is a folder `<workspaces_dir>/<name>/` holding `workspace.json`, `profiles.json` and
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from modelspell import ModelSpellError

from . import __version__
from .jsonio import decode_file, encode_file
from .model_call import _FIELDS, KINDS, REASONING_LEVELS, ModelSettings, SettingsError, settings_from_body

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
FORMAT_VERSION = 1
NAME_RULE = "1 to 64 letters, digits, '.', '_' or '-', starting with a letter or digit"

WORKSPACE_FILE = "workspace.json"
PROFILES_FILE = "profiles.json"
LOCK_FILE = ".agentwiz.lock"
PROFILE_FIELDS = _FIELDS  # the six ModelSettings fields, in their usual order


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
    file: str      # "workspace.json" or "profiles.json"
    code: str      # "unreadable" | "invalid_json" | "invalid_field" | "newer_format"
    message: str   # AgentWiz's own text: JSON location and rule; never a value or exception text


class _Invalid(Exception):
    """A field rule was broken; the message is the location and the rule."""


def _strerror(exc: OSError) -> str:
    return exc.strerror or type(exc).__name__


# --- reading and validating -------------------------------------------------------------------
def _read(path: Path, file: str) -> tuple[dict | None, Problem | None]:
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


def _format_version(obj: dict) -> int:
    """Returns the format version; raises _Invalid if it is missing or not an integer of at least 1."""
    if "format_version" not in obj:
        raise _Invalid("format_version is missing")
    value = obj["format_version"]
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise _Invalid("format_version must be an integer of at least 1")
    return value


_NEWER = f"written by a newer AgentWiz (format_version greater than {FORMAT_VERSION})"


def _check_workspace(obj: dict) -> Problem | None:
    try:
        if _format_version(obj) > FORMAT_VERSION:
            return Problem(WORKSPACE_FILE, "newer_format", _NEWER)
        if not isinstance(obj.get("created_by"), str):
            raise _Invalid("created_by must be a string")
        if not isinstance(obj.get("save_runs"), bool):
            raise _Invalid("save_runs must be true or false")
    except _Invalid as exc:
        return Problem(WORKSPACE_FILE, "invalid_field", str(exc))
    return None


def _check_profile(name: str, obj: Any) -> ModelSettings:
    where = f"profiles.{name}"
    if not isinstance(obj, dict):
        raise _Invalid(f"{where} must be an object")
    for f in PROFILE_FIELDS:
        if f not in obj:
            raise _Invalid(f"{where}.{f} is missing")
    if obj["kind"] not in KINDS:
        raise _Invalid(f"{where}.kind must be one of {', '.join(KINDS)}")
    for f in ("base_url", "model", "provider"):
        if not isinstance(obj[f], str):
            raise _Invalid(f"{where}.{f} must be a string")
    if obj["reasoning"] not in REASONING_LEVELS:
        raise _Invalid(f"{where}.reasoning must be one of: \"\", off, low, medium, high")
    mt = obj["max_tokens"]
    if mt is not None and not (isinstance(mt, int) and not isinstance(mt, bool) and mt >= 1):
        raise _Invalid(f"{where}.max_tokens must be null or an integer of at least 1")
    try:
        return settings_from_body(ModelSettings(), {f: obj[f] for f in PROFILE_FIELDS})
    except SettingsError as exc:  # AgentWiz's own messages; they name fields, never values
        raise _Invalid(f"{where}: {exc}") from None
    except ModelSpellError:
        raise _Invalid(f"{where}.base_url must start with http:// or https:// "
                       "(and is required for openai_compat)") from None


@dataclass(frozen=True)
class ProfilesFile:
    """The result of reading profiles.json."""
    problem: Problem | None
    exists: bool = False
    profiles: dict[str, ModelSettings] = field(default_factory=dict)
    active: str | None = None
    raw: dict = field(default_factory=dict)   # the file's object, for carrying unknown fields over


def empty_profiles_raw() -> dict:
    return {"format_version": FORMAT_VERSION, "active": None, "profiles": {}}


def read_profiles(ws: "Workspace") -> ProfilesFile:
    """Read and validate the whole of profiles.json (techspec 7.5.3)."""
    raw, problem = _read(ws.path / PROFILES_FILE, PROFILES_FILE)
    if problem is not None:
        return ProfilesFile(problem)
    if raw is None:
        return ProfilesFile(None, exists=False, raw=empty_profiles_raw())
    try:
        if _format_version(raw) > FORMAT_VERSION:
            return ProfilesFile(Problem(PROFILES_FILE, "newer_format", _NEWER))
        if "profiles" not in raw or not isinstance(raw["profiles"], dict):
            raise _Invalid("profiles must be an object")
        profiles: dict[str, ModelSettings] = {}
        for name, obj in raw["profiles"].items():
            if not valid_name(name):
                raise _Invalid(f"profiles has a profile name that breaks the name rule ({NAME_RULE})")
            profiles[name] = _check_profile(name, obj)
        if "active" not in raw:
            raise _Invalid("active is missing")
        active = raw["active"]
        if active is not None and not (isinstance(active, str) and active in profiles):
            raise _Invalid("active must be null or the name of a profile in profiles")
    except _Invalid as exc:
        return ProfilesFile(Problem(PROFILES_FILE, "invalid_field", str(exc)))
    return ProfilesFile(None, exists=True, profiles=profiles, active=active, raw=raw)


def profile_object(settings: ModelSettings, previous: Mapping | None = None) -> dict:
    """A profile's object: the previous object's unknown fields kept, the six fields replaced.

    Only the six ModelSettings fields are written; ModelSettings holds no key (P6).
    """
    out = dict(previous) if isinstance(previous, Mapping) else {}
    for f in PROFILE_FIELDS:
        out[f] = getattr(settings, f)
    return out


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


def write_profiles(ws: "Workspace", raw: Mapping[str, Any]) -> None:
    write_atomic(ws.path, PROFILES_FILE, raw)


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
        obj, problem = _read(path / WORKSPACE_FILE, WORKSPACE_FILE)
        if problem is None and obj is None:
            try:
                write_atomic(path, WORKSPACE_FILE, {"format_version": FORMAT_VERSION,
                                                    "created_by": "AgentWiz " + __version__,
                                                    "save_runs": False})
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


__all__ = ["FORMAT_VERSION", "NAME_RE", "Problem", "ProfilesFile", "Workspace", "WorkspaceError",
           "WorkspaceInUse", "open_workspace", "read_profiles", "valid_name", "write_profiles"]
