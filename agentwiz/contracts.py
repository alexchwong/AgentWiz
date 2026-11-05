"""Saved contracts: files in `<workspace>/contracts/` (techspec 0.1.3, 7.8; R4-7, R4-25, R4-34).

A contract is `<workspace>/contracts/<name>.json`; its name is the file name without `.json` and follows
the profile name rule. Each file is read whole with the exact reader and checked with
`checks.parse_contract` (plus schema rules when the required validator is available). A file that fails is
listed with its problem: it can be opened and deleted, but never selected for a request, and AgentWiz
never writes over it.

The folder is re-scanned on every use, so an edit made with any editor is noticed: each file's inode,
modification time and size are recorded, and a new, changed or removed file is reloaded or dropped, and
a changed contract gets a new revision. Revisions come from one process-wide counter, so a contract that
is deleted and created again never repeats an earlier revision.

Mutating methods take no lock of their own: `state.py` calls them with the state lock held. Knows nothing
of HTTP.
"""
from __future__ import annotations

import itertools
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from . import checks, jsonio
from .workspace import NAME_RE, NAME_RULE, Problem, valid_name, write_atomic_bytes

DIRECTORY = "contracts"
SUFFIX = ".json"

_revisions = itertools.count(1)
_revisions_lock = threading.Lock()


def _next_revision() -> int:
    with _revisions_lock:
        return next(_revisions)


class NoSuchContract(Exception):
    pass


class ContractExists(Exception):
    pass


class StaleContract(Exception):
    def __init__(self, current: int):
        super().__init__("the contract was changed elsewhere")
        self.current = current


class WorkspaceReadOnly(Exception):
    pass


class ContractUnusable(Exception):
    """The existing contract of that name has a problem; it is never written over through the API."""

    def __init__(self, problem: Problem):
        super().__init__(f"{problem.file}: {problem.message}")
        self.problem = problem


class ContractInvalid(Exception):
    """The submitted contract text is not a valid contract. `issues`: [{"location", "message"}]."""

    def __init__(self, issues: list[dict], total: int | None = None):
        super().__init__("the contract is not valid")
        self.issues = issues
        self.total = len(issues) if total is None else total


class BadName(ValueError):
    pass


@dataclass(frozen=True)
class Entry:
    name: str
    revision: int
    fingerprint: tuple
    text: str                          # the file's content as stored (decoded with replacement)
    contract: checks.Contract | None   # None when the file has a problem
    problem: Problem | None

    def problem_view(self) -> dict | None:
        if self.problem is None:
            return None
        return {"file": self.problem.file, "code": self.problem.code, "message": self.problem.message}

    def list_view(self) -> dict:
        return {"name": self.name, "revision": self.revision,
                "format": self.contract.format if self.contract else None,
                "description": self.contract.description if self.contract else "",
                "problem": self.problem_view()}

    def full_view(self) -> dict:
        return {"name": self.name, "revision": self.revision, "text": self.text, "problem": self.problem_view()}


def _strerror(exc: OSError) -> str:
    return exc.strerror or type(exc).__name__


class ContractStore:
    def __init__(self, workspace_path: Path):
        self.path = Path(workspace_path) / DIRECTORY
        self._entries: dict[str, Entry] = {}

    # --- reading ---------------------------------------------------------------------------------
    def _listing(self) -> dict[str, tuple] | None:
        """name -> fingerprint of every contract-named file; None when the folder cannot be listed."""
        found: dict[str, tuple] = {}
        try:
            with os.scandir(self.path) as folder:
                for item in folder:
                    stem = item.name[:-len(SUFFIX)]
                    if not item.name.endswith(SUFFIX) or NAME_RE.match(stem) is None:
                        continue
                    try:
                        if not item.is_file():
                            continue
                        st = item.stat()
                    except FileNotFoundError:
                        continue
                    except OSError as exc:
                        found[stem] = ("unreadable", exc.errno)
                        continue
                    found[stem] = (st.st_ino, st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            return {}
        except OSError:
            return None
        return found

    def _load(self, name: str, fingerprint: tuple) -> Entry:
        file = self.path / (name + SUFFIX)
        where = str(file)
        try:
            data = file.read_bytes()
        except OSError as exc:
            return Entry(name, _next_revision(), fingerprint, "", None,
                         Problem(where, "unreadable", f"the file cannot be read ({_strerror(exc)})"))
        text = data.decode("utf-8", errors="replace")

        def bad(code: str, message: str) -> Entry:
            return Entry(name, _next_revision(), fingerprint, text, None, Problem(where, code, message))

        try:
            obj = jsonio.decode_file_exact(data)
        except jsonio.JSONSyntax as exc:
            return bad("invalid", f"line {exc.line}, column {exc.column}: the file is not valid JSON")
        except ValueError as exc:
            return bad("invalid", str(exc))
        if "format_version" not in obj:
            return bad("invalid", "format_version: is missing")
        version = obj["format_version"]
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            return bad("invalid", "format_version: must be an integer of at least 1")
        if version > checks.FORMAT_VERSION:
            return bad("newer_format",
                       f"written by a newer AgentWiz (format_version greater than {checks.FORMAT_VERSION})")
        try:
            contract = checks.parse_contract(obj)
        except checks.ContractError as exc:
            return bad("invalid", str(exc))
        return Entry(name, _next_revision(), fingerprint, text, contract, None)

    def rescan(self) -> None:
        """Notice changes on disk (R4-25): reload new and changed files, drop removed ones."""
        found = self._listing()
        if found is None:
            return
        for name in [n for n in self._entries if n not in found]:
            del self._entries[name]
        for name, fingerprint in found.items():
            entry = self._entries.get(name)
            if entry is None or entry.fingerprint != fingerprint:
                self._entries[name] = self._load(name, fingerprint)

    def get(self, name: str) -> Entry | None:
        return self._entries.get(name)

    def entries(self) -> list[Entry]:
        return [self._entries[name] for name in sorted(self._entries)]

    # --- changing --------------------------------------------------------------------------------
    def save(self, name: str, text: str, revision: int | None, read_only: bool) -> tuple[Entry, list[str]]:
        """Section 7.8, Saving. Raises ContractExists, NoSuchContract, StaleContract, WorkspaceReadOnly,
        ContractUnusable, BadName, ContractInvalid; OSError when the write fails."""
        self.rescan()
        existing = self._entries.get(name)
        if revision is None:
            if existing is not None:
                raise ContractExists()
        else:
            if existing is None:
                raise NoSuchContract()
            if existing.revision != revision:
                raise StaleContract(existing.revision)
        if read_only:
            raise WorkspaceReadOnly()
        if existing is not None and existing.problem is not None:
            raise ContractUnusable(existing.problem)
        if not valid_name(name):
            raise BadName(f"the contract name must be {NAME_RULE}")
        try:
            contract = checks.contract_from_text(text)
        except checks.ContractError as exc:
            raise ContractInvalid(exc.issues, exc.total) from None
        if contract.schema is not None:
            checks.require_validation([contract])
        raw = contract.raw
        payload = ({k: (checks.FORMAT_VERSION if k == "format_version" else v) for k, v in raw.items()}
                   if "format_version" in raw else {"format_version": checks.FORMAT_VERSION, **raw})
        data = jsonio.encode_file_exact(payload)
        unknown = [k for k in raw if k not in checks._TOP_LEVEL]
        notes = ["unknown top-level fields kept: " + ", ".join(json.dumps(k) for k in unknown)] if unknown else []
        file = self.path / (name + SUFFIX)
        try:
            current = file.read_bytes() if existing is not None else None
        except OSError:
            current = None
        if existing is not None and current == data:
            return existing, notes
        self.path.mkdir(parents=True, exist_ok=True)
        write_atomic_bytes(self.path, name + SUFFIX, data)
        st = file.stat()
        entry = Entry(name, _next_revision(), (st.st_ino, st.st_mtime_ns, st.st_size),
                      data.decode("ascii"), contract, None)
        self._entries[name] = entry
        return entry, notes

    def delete(self, name: str, revision: int, read_only: bool) -> None:
        """Section 7.8, Deleting. A damaged contract has a revision too, and can be deleted."""
        self.rescan()
        existing = self._entries.get(name)
        if existing is None:
            raise NoSuchContract()
        if existing.revision != revision:
            raise StaleContract(existing.revision)
        if read_only:
            raise WorkspaceReadOnly()
        try:
            os.unlink(self.path / (name + SUFFIX))
        except FileNotFoundError:
            pass
        del self._entries[name]
