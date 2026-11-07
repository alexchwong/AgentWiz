"""Workspace administration: listing, cloning, resetting and deleting workspace folders.

This module is deliberately headless. It knows the workspace filesystem, the workspace lock and the
workspace layout, but nothing about HTTP or ``ProcessState``. The UI server owns the lifecycle of the
currently open workspace; this module only performs one filesystem operation at a time.

Every destructive operation is bounded to a name directly under the configured workspaces root.
Symbolic links are never followed. Clones are staged in a hidden sibling folder and renamed into
place, so a failed clone never leaves a partly populated workspace under the requested name.
"""
from __future__ import annotations

import os
import shlex
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import profiles
from . import workspace as wsmod

LIST_LIMIT = 200
CLONE_FILE_LIMIT = 20_000
CLONE_BYTE_LIMIT = 1 << 30
SKIP_NAMES = {wsmod.LOCK_FILE, ".profiles.lock"}
ALWAYS_PATHS = ("workspace.json", "task_settings.json", "roles.json", "model-profiles",
                "contracts", "blocks", "workflows", "workshop/examples")
EVIDENCE_PATHS = ("runs", "workshop/comparisons")


class WorkspaceAdminError(ValueError):
    """A refused workspace operation. The message is AgentWiz's fixed text, never a file's content."""

    def __init__(self, code: str, message: str, status: int = 409, *, path: str | None = None,
                 holder: dict | None = None, active: list | None = None, limit: str | None = None,
                 cleanup: str | None = None):
        super().__init__(message)
        self.code, self.status = code, status
        self.path, self.holder, self.active, self.limit, self.cleanup = path, holder, active, limit, cleanup


def _error(code: str, message: str, status: int = 409, **fields) -> WorkspaceAdminError:
    return WorkspaceAdminError(code, message, status, **fields)


def _strerror(exc: OSError) -> str:
    return exc.strerror or type(exc).__name__


def _root(value: Path) -> Path:
    return Path(value).resolve()


def _path(root: Path, name: object) -> Path:
    if not wsmod.valid_name(name):
        raise _error("invalid_workspace_name", f"the workspace name must use {wsmod.NAME_RULE}", 400)
    path = _root(root) / name  # type: ignore[operator]
    if path.parent != _root(root):
        raise _error("invalid_workspace_name", "the workspace name must not leave the workspaces folder", 400)
    return path


def _lstat(path: Path) -> os.stat_result:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        raise _error("no_such_workspace", "the workspace does not exist", 404) from None
    except OSError as exc:
        raise _error("workspace_io_error", f"the workspace cannot be read ({_strerror(exc)})", 500,
                     path=str(path)) from None


def validate_existing(root: Path, name: object) -> Path:
    path = _path(root, name)
    st = _lstat(path)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise _error("no_such_workspace", "the workspace is not a plain folder", 404, path=str(path))
    return path


def validate_new(root: Path, name: object) -> Path:
    path = _path(root, name)
    if os.path.lexists(path):
        raise _error("workspace_exists", "a workspace with that name already exists", 409, path=str(path))
    return path


def _check_protected(path: Path, protected: list[Path] | tuple[Path, ...] | None) -> None:
    """Refuse reset/delete when a shared defaults or MCP directory lives inside the workspace."""
    target = Path(path).resolve()
    for item in protected or ():
        try:
            shared = Path(item).resolve()
        except OSError:
            continue
        if shared == target or target in shared.parents:
            raise _error("shared_data_inside_workspace",
                         "shared defaults or the MCP server library is inside this workspace; move it first",
                         409, path=str(target))


def check_protected(path: Path, protected) -> None:
    """Public preflight for a destructive operation; raises the same error before state is stopped."""
    _check_protected(path, protected)

def list_workspaces(root: Path, current: str, limit: int = LIST_LIMIT) -> dict:
    """Bounded listing of plain workspace folders. No lock is taken and no workspace is opened."""
    root = _root(root)
    rows, total = [], 0
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if not wsmod.valid_name(entry.name):
                    continue
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                total += 1
                if len(rows) < limit:
                    rows.append({"name": entry.name, "path": str(root / entry.name),
                                 "current": entry.name == current})
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise _error("workspace_io_error", f"the workspaces folder cannot be read ({_strerror(exc)})", 500,
                     path=str(root)) from None
    rows.sort(key=lambda row: row["name"])
    return {"root": str(root), "current": current, "workspaces": rows,
            "total": total, "truncated": total > len(rows), "limit": limit}


@dataclass
class _Budget:
    files: int = 0
    bytes: int = 0

    def add_file(self, size: int) -> None:
        self.files += 1
        self.bytes += size
        if self.files > CLONE_FILE_LIMIT:
            raise _error("workspace_too_large", "the workspace clone has too many files", 409,
                         limit="clone_files")
        if self.bytes > CLONE_BYTE_LIMIT:
            raise _error("workspace_too_large", "the workspace clone is too large", 409,
                         limit="clone_bytes")


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _source_path(source: Path, rel: str) -> Path | None:
    """Resolve a known relative path only when every component is a plain non-link directory.

    Returns None when the path is absent. A symbolic link anywhere on the way is an unsafe entry.
    """
    parts = [part for part in rel.split("/") if part]
    current = source
    for index, part in enumerate(parts[:-1]):
        current = current / part
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise _error("workspace_io_error", f"the workspace cannot be read ({_strerror(exc)})", 500,
                         path=str(current)) from None
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise _error("unsafe_entry", "a symbolic link or non-folder blocks the clone", 409,
                         path="/".join(parts[:index + 1]))
    final = source.joinpath(*parts)
    try:
        os.lstat(final)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _error("workspace_io_error", f"the workspace cannot be read ({_strerror(exc)})", 500,
                     path=str(final)) from None
    return final


def _copy_file(source: Path, target: Path, budget: _Budget) -> None:
    try:
        st = os.lstat(source)
    except OSError as exc:
        raise _error("workspace_io_error", f"the workspace file cannot be read ({_strerror(exc)})", 500,
                     path=str(source)) from None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise _error("unsafe_entry", "a symbolic link or special file blocks the clone", 409,
                     path=source.name)
    budget.add_file(st.st_size)
    _ensure_dir(target.parent)
    src_fd = dst_fd = None
    try:
        src_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        dst_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        while True:
            chunk = os.read(src_fd, 1 << 20)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                view = view[os.write(dst_fd, view):]
        os.fsync(dst_fd)
    except OSError as exc:
        try:
            os.unlink(target)
        except OSError:
            pass
        raise _error("workspace_io_error", f"the workspace file cannot be copied ({_strerror(exc)})", 500,
                     path=str(source)) from None
    finally:
        if src_fd is not None:
            os.close(src_fd)
        if dst_fd is not None:
            os.close(dst_fd)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass


def _copy_dir(source: Path, target: Path, budget: _Budget) -> None:
    try:
        st = os.lstat(source)
    except OSError as exc:
        raise _error("workspace_io_error", f"the workspace folder cannot be read ({_strerror(exc)})", 500,
                     path=str(source)) from None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise _error("unsafe_entry", "a symbolic link or special file blocks the clone", 409,
                     path=source.name)
    _ensure_dir(target)
    try:
        entries = list(os.scandir(source))
    except OSError as exc:
        raise _error("workspace_io_error", f"the workspace folder cannot be read ({_strerror(exc)})", 500,
                     path=str(source)) from None
    for entry in entries:
        if entry.name in SKIP_NAMES:
            continue
        child = target / entry.name
        try:
            if entry.is_symlink():
                raise _error("unsafe_entry", "a symbolic link blocks the clone", 409, path=entry.name)
            if entry.is_dir(follow_symlinks=False):
                _copy_dir(Path(entry.path), child, budget)
            elif entry.is_file(follow_symlinks=False):
                _copy_file(Path(entry.path), child, budget)
            else:
                raise _error("unsafe_entry", "a special file blocks the clone", 409, path=entry.name)
        except OSError as exc:
            raise _error("workspace_io_error", f"the workspace cannot be copied ({_strerror(exc)})", 500,
                         path=str(entry.path)) from None


def _copy_known(source: Path, rel: str, target: Path, budget: _Budget) -> None:
    found = _source_path(source, rel)
    if found is None:
        return
    try:
        st = os.lstat(found)
    except OSError as exc:
        raise _error("workspace_io_error", f"the workspace cannot be read ({_strerror(exc)})", 500,
                     path=str(found)) from None
    if stat.S_ISLNK(st.st_mode):
        raise _error("unsafe_entry", "a symbolic link blocks the clone", 409, path=rel)
    if stat.S_ISDIR(st.st_mode):
        _copy_dir(found, target, budget)
    elif stat.S_ISREG(st.st_mode):
        _copy_file(found, target, budget)
    else:
        raise _error("unsafe_entry", "a special file blocks the clone", 409, path=rel)


def clone_workspace(root: Path, source: str, target: str, include_evidence: bool = False) -> dict:
    """Copy one workspace's AgentWiz data into a new, independent workspace folder."""
    root = _root(root)
    source_path = validate_existing(root, source)
    target_path = validate_new(root, target)
    if source_path == target_path:
        raise _error("workspace_exists", "the clone target must differ from its source", 409)
    staging = Path(tempfile.mkdtemp(prefix=".agentwiz-clone-", dir=root))
    budget = _Budget()
    try:
        for rel in ALWAYS_PATHS + (EVIDENCE_PATHS if include_evidence else ()):
            _copy_known(source_path, rel, staging / rel, budget)
        if os.path.lexists(target_path):
            raise _error("workspace_exists", "a workspace with that name appeared during the clone", 409)
        os.rename(staging, target_path)
    except BaseException:
        _remove_tree(staging, missing_ok=True)
        raise
    skipped = 0
    known = {"workspace.json", "task_settings.json", "roles.json", "model-profiles",
             "contracts", "blocks", "workflows", "workshop", "runs", *SKIP_NAMES}
    try:
        with os.scandir(source_path) as entries:
            for entry in entries:
                if entry.name not in known:
                    skipped += 1
    except OSError:
        pass
    return {"name": target, "path": str(target_path), "files": budget.files, "bytes": budget.bytes,
            "skipped": skipped, "evidence": bool(include_evidence)}


def _remove_entry(path: Path) -> None:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise _error("workspace_io_error", f"the workspace cannot be removed ({_strerror(exc)})", 500,
                     path=str(path)) from None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        try:
            os.unlink(path)
        except OSError as exc:
            raise _error("workspace_io_error", f"the workspace cannot be removed ({_strerror(exc)})", 500,
                         path=str(path)) from None
        return
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                _remove_entry(Path(entry.path))
        os.rmdir(path)
    except OSError as exc:
        raise _error("workspace_io_error", f"the workspace cannot be removed ({_strerror(exc)})", 500,
                     path=str(path)) from None


def _remove_tree(path: Path, *, missing_ok: bool = False) -> None:
    try:
        _remove_entry(path)
    except WorkspaceAdminError:
        if missing_ok and not os.path.lexists(path):
            return
        raise


def reset_contents(workspace_path: Path, profiles_dir: Path, protected=None) -> dict:
    """Clear a workspace and make it fresh, preserving its folder and lock file inode."""
    path = Path(workspace_path)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise _error("no_such_workspace", "the workspace does not exist", 404) from None
    except OSError as exc:
        raise _error("workspace_io_error", f"the workspace cannot be read ({_strerror(exc)})", 500,
                     path=str(path)) from None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise _error("no_such_workspace", "the workspace is not a plain folder", 404, path=str(path))
    _check_protected(path, protected)
    lock = path / wsmod.LOCK_FILE
    try:
        if os.path.lexists(lock):
            lock_st = os.lstat(lock)
            if stat.S_ISLNK(lock_st.st_mode) or not stat.S_ISREG(lock_st.st_mode):
                raise _error("unsafe_entry", "the workspace lock is not a plain file", 409, path=wsmod.LOCK_FILE)
    except OSError as exc:
        raise _error("workspace_io_error", f"the workspace lock cannot be read ({_strerror(exc)})", 500,
                     path=str(lock)) from None
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.name != wsmod.LOCK_FILE:
                    _remove_entry(Path(entry.path))
        wsmod.write_atomic(path, wsmod.WORKSPACE_FILE, wsmod.new_workspace_document())
    except OSError as exc:
        raise _error("workspace_io_error", f"the workspace cannot be reset ({_strerror(exc)})", 500,
                     path=str(path)) from None
    try:
        profiles.seed_workspace(path / "model-profiles", profiles.Library(profiles_dir))
    except OSError:
        try:
            folder = path / "model-profiles"
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            profiles.write_profiles(folder, profiles.empty_profiles_raw())
        except OSError as exc:
            raise _error("workspace_io_error", f"the workspace profiles cannot be created ({_strerror(exc)})",
                         500, path=str(path / "model-profiles")) from None
    return {"name": path.name, "path": str(path), "reset": True}


def delete_workspace(root: Path, name: str, current: str | None = None, protected=None) -> dict:
    """Delete a non-current workspace by locking it, renaming it to a tombstone, then removing it."""
    root = _root(root)
    target = validate_existing(root, name)
    if current is not None and name == current:
        raise _error("workspace_is_current", "switch to another workspace before deleting this one", 409)
    _check_protected(target, protected)
    tombstone = root / (".agentwiz-delete-" + uuid.uuid4().hex)
    try:
        with wsmod.acquire_existing_lock(root, name):
            os.rename(target, tombstone)
    except wsmod.WorkspaceInUse as exc:
        raise _error("workspace_in_use", "the workspace is in use by another AgentWiz process", 409,
                     path=str(target), holder=exc.holder) from None
    except wsmod.WorkspaceError:
        raise _error("workspace_io_error", "the workspace could not be locked for deletion", 500,
                     path=str(target)) from None
    except OSError as exc:
        raise _error("workspace_io_error", f"the workspace could not be deleted ({_strerror(exc)})", 500,
                     path=str(target)) from None
    try:
        _remove_tree(tombstone)
    except WorkspaceAdminError:
        return {"deleted": name, "cleanup": "incomplete", "path": str(tombstone)}
    return {"deleted": name, "cleanup": "complete", "path": str(target)}


def launch_command(root: Path, name: str, profiles_dir: Path, mcp_dir: Path, port: int | None = None) -> str:
    path = _path(root, name)
    args = ["agentwiz", "--workspaces-dir", str(_root(root)), "--profiles-dir", str(Path(profiles_dir).resolve()),
            "--mcp-servers-dir", str(Path(mcp_dir).resolve()), "--workspace", path.name]
    if port is not None:
        if not isinstance(port, int) or not 0 <= port <= 65535:
            raise _error("invalid_workspace_name", "port must be an integer from 0 to 65535", 400)
        args += ["--port", str(port)]
    return shlex.join(args)

