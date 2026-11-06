"""Saved contracts: one flat, append-only revision container per stable name (overhaul spec 9, 11).

    <workspace>/contracts/<name>.json   {"format_version": 2, "name", "family", "latest", "revisions": [...]}

The file name is the contract's immutable identity. `family` is mutable grouping metadata. Each revision holds
one user contract definition (recognised by `checks.parse_contract`) and its canonical digest
(`contract_schema.contract_digest`). Revisions are contiguous, oldest first, and never rewritten through
AgentWiz: a save appends, a save equal to the latest digest is a no-op, and Restore appends a copy.

Reading: the folder is re-scanned on use; inode, modification time and size only decide whether to reparse a
listed file. Explicit get/save/delete/resolve-for-dispatch read and hash the current bytes. A file that fails
any rule is listed with its problem and never written over; it can be viewed and deleted (after the same
reference scan). Every stored revision digest is recomputed on load; a mismatch makes the file unusable.

Writing: the caller (state.py) holds a registered writer and the logical contract-registry reservation; this
module takes no lock and never holds one across I/O. Writes go through a same-directory temporary file, file
fsync, atomic replace and parent-directory fsync. Symbolic links and non-regular files are refused. The
`file_token` (SHA-256 of the complete file bytes) is the stale-edit token. Knows nothing of HTTP.
"""
from __future__ import annotations

import datetime
import hashlib
import os
import re
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import checks, contract_schema, jsonio
from .workspace import NAME_RE, Problem, valid_name

DIRECTORY = "contracts"
SUFFIX = ".json"
FORMAT_VERSION = 2
CONTAINER_LIMIT = 32 << 20
LABEL_LIMIT = 120
FAMILY_LIMIT = 64
DEFAULT_FAMILY = "General"
ORIGINS = ("manual", "synthesized", "derived_from_synthesis")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CREATED_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_TOP_KEYS = {"format_version", "name", "family", "latest", "revisions"}
_REVISION_KEYS = {"revision", "label", "created_at", "origin", "digest", "contract"}
_BASIS_REQUIRED = ("prompt_id", "prompt_version", "role", "provider_kind", "model")
_BASIS_OPTIONAL = ("provider", "note")


def before_contract_replace(name: str) -> None:
    """Does nothing in shipped code; tests make it block or fail just before the atomic replace."""


def after_contract_replace(name: str) -> None:
    """Does nothing in shipped code; tests make it fail just after the atomic replace (durability)."""


def before_contract_delete(name: str) -> None:
    """Does nothing in shipped code; tests make it block after the reference scan, before the unlink."""


class StoreError(Exception):
    """A refused contract operation: `code` and HTTP `status` from overhaul spec 13, a plain message,
    optional bounded `issues`, and extra machine-readable fields (current file_token, blockers...)."""

    def __init__(self, code: str, status: int, message: str, issues=None, total=None, **extra):
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message
        self.issues = list(issues or [])[:contract_schema.ISSUES_SHOWN]
        self.total = len(issues or []) if total is None else total
        self.extra = extra

    def body(self) -> dict:
        out = {"code": self.code, "message": self.message, **self.extra}
        if self.issues:
            out["issues"] = self.issues
            out["issue_count"] = self.total
        return out


def file_token(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _controls(text: str) -> bool:
    return any(ord(c) < 32 or 0x7F <= ord(c) <= 0x9F for c in text)


def _surrogates(text: str) -> bool:
    return any(0xD800 <= ord(c) <= 0xDFFF for c in text)


def text_issue(value, limit: int, *, blank_ok=False, controls_ok=False) -> str | None:
    """The rule a short metadata string breaks, or None."""
    if not isinstance(value, str):
        return "must be a string"
    if _surrogates(value):
        return "must not contain unpaired Unicode surrogates"
    if len(value) > limit:
        return f"must have at most {limit} code points"
    if not blank_ok and not value.strip():
        return "must not be blank"
    if not controls_ok and _controls(value):
        return "must not contain control characters"
    return None


def family_issue(value) -> str | None:
    if isinstance(value, str) and value != value.strip():
        return "must be trimmed"
    return text_issue(value, FAMILY_LIMIT)


def basis_issues(basis) -> list[dict]:
    """Minimal server-captured provenance (spec 9.2). Never task text, records or credentials."""
    if not isinstance(basis, dict):
        return [{"location": "basis", "message": "must be an object"}]
    issues = []
    for key in basis:
        if key not in _BASIS_REQUIRED + _BASIS_OPTIONAL:
            issues.append({"location": f"basis.{key}", "message": "is not a basis member"})
    for key in _BASIS_REQUIRED:
        if key not in basis:
            issues.append({"location": f"basis.{key}", "message": "is required"})
    limits = {"prompt_id": 64, "provider_kind": 32, "model": 2048, "provider": 256, "note": 500}
    for key, limit in limits.items():
        if key in basis:
            problem = text_issue(basis[key], limit, blank_ok=key == "note")
            if problem:
                issues.append({"location": f"basis.{key}", "message": problem})
    if "prompt_version" in basis and (type(basis["prompt_version"]) is not int or basis["prompt_version"] < 1):
        issues.append({"location": "basis.prompt_version", "message": "must be a positive integer"})
    if "role" in basis and not valid_name(basis["role"]):
        issues.append({"location": "basis.role", "message": "must follow the name rule"})
    return issues


def _created_issue(value) -> str | None:
    if not isinstance(value, str) or _CREATED_RE.match(value) is None:
        return "must be UTC RFC3339 with whole seconds and Z"
    try:
        datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return "is not a real calendar time"
    return None


@dataclass(frozen=True)
class Revision:
    revision: int
    label: str
    created_at: str
    origin: str
    digest: str
    contract: checks.Contract
    basis: dict | None = None

    def definition_text(self) -> str:
        return jsonio.encode_exact(dict(self.contract.raw), indent=2)

    def summary(self) -> dict:
        out = {"revision": self.revision, "label": self.label, "created_at": self.created_at,
               "origin": self.origin, "digest": self.digest, "format": self.contract.format,
               "description": self.contract.description}
        if self.basis is not None:
            out["basis"] = dict(self.basis)
        return out

    def stored(self) -> dict:
        out = {"revision": self.revision, "label": self.label, "created_at": self.created_at,
               "origin": self.origin, "digest": self.digest, "contract": self.contract.raw}
        if self.basis is not None:
            out["basis"] = self.basis
        return out


@dataclass(frozen=True)
class Entry:
    name: str
    fingerprint: tuple
    token: str | None                 # None when the bytes could not be read
    family: str | None
    revisions: tuple[Revision, ...]
    problem: Problem | None
    size: int = 0

    @property
    def latest(self) -> int | None:
        return self.revisions[-1].revision if self.revisions else None

    def revision(self, number: int) -> Revision | None:
        if type(number) is int and 1 <= number <= len(self.revisions):
            return self.revisions[number - 1]
        return None

    def problem_view(self) -> dict | None:
        if self.problem is None:
            return None
        return {"file": self.problem.file, "code": self.problem.code, "message": self.problem.message}

    def list_view(self) -> dict:
        last = self.revisions[-1] if self.revisions and self.problem is None else None
        return {"name": self.name, "family": self.family if self.problem is None else None,
                "latest": last.revision if last else None, "latest_digest": last.digest if last else None,
                "format": last.contract.format if last else None,
                "kind": (None if last is None else "text" if last.contract.format == "text" else
                         "json" if last.contract.schema is not None else "any_json"),
                "description": last.contract.description if last else "",
                "problem": self.problem_view()}


# --- parsing a container ------------------------------------------------------------------------------
def parse_container(name: str, data: bytes, where: str) -> tuple[str | None, tuple[Revision, ...], Problem | None]:
    """(family, revisions, problem). Size is checked before decoding; every rule is located."""
    def bad(code, message):
        return None, (), Problem(where, code, message)

    if len(data) > CONTAINER_LIMIT:
        return bad("too_large", f"the file is larger than {CONTAINER_LIMIT:,} bytes")
    try:
        obj = jsonio.decode_file_exact(data)
    except jsonio.JSONSyntax as exc:
        return bad("invalid", f"line {exc.line}, column {exc.column}: the file is not valid JSON")
    except ValueError as exc:
        return bad("invalid", str(exc))
    version = obj.get("format_version")
    if "format_version" not in obj:
        return bad("invalid", "format_version: is missing")
    if type(version) is not int or version < 1:
        return bad("invalid", "format_version: must be an integer of at least 1")
    if version > FORMAT_VERSION:
        return bad("newer_format", f"written by a newer AgentWiz (format_version greater than {FORMAT_VERSION})")
    if version < FORMAT_VERSION:
        return bad("migration_required", "an old single-definition contract file: create a new version 2 "
                                         "contract and delete this file; it is never converted automatically")
    for key in obj:
        if key not in _TOP_KEYS:
            return bad("invalid", f"{key}: is not a member of a contract container")
    for key in _TOP_KEYS:
        if key not in obj:
            return bad("invalid", f"{key}: is required")
    if obj["name"] != name:
        return bad("invalid", "name: must equal the file name")
    problem = family_issue(obj["family"])
    if problem:
        return bad("invalid", f"family: {problem}")
    revisions = obj["revisions"]
    if not isinstance(revisions, list) or not revisions:
        return bad("invalid", "revisions: must be a nonempty array")
    if type(obj["latest"]) is not int or obj["latest"] != len(revisions):
        return bad("invalid", "latest: must equal the final revision number")
    out = []
    for index, item in enumerate(revisions):
        at = f"revisions[{index}]"
        if not isinstance(item, dict):
            return bad("invalid", f"{at}: must be an object")
        for key in item:
            if key not in _REVISION_KEYS and key != "basis":
                return bad("invalid", f"{at}.{key}: is not a revision member")
        for key in _REVISION_KEYS:
            if key not in item:
                return bad("invalid", f"{at}.{key}: is required")
        if type(item["revision"]) is not int or item["revision"] != index + 1:
            return bad("invalid", f"{at}.revision: revisions must be numbered 1..latest without gaps")
        problem = text_issue(item["label"], LABEL_LIMIT, blank_ok=True)
        if problem:
            return bad("invalid", f"{at}.label: {problem}")
        problem = _created_issue(item["created_at"])
        if problem:
            return bad("invalid", f"{at}.created_at: {problem}")
        if item["origin"] not in ORIGINS:
            return bad("invalid", f"{at}.origin: must be one of {', '.join(ORIGINS)}")
        if not isinstance(item["digest"], str) or DIGEST_RE.match(item["digest"]) is None:
            return bad("invalid", f"{at}.digest: must be sha256: and 64 lowercase hexadecimal characters")
        basis = item.get("basis")
        if "basis" in item:
            issues = basis_issues(basis)
            if issues:
                return bad("invalid", f"{at}.{issues[0]['location']}: {issues[0]['message']}")
        try:
            contract = checks.parse_contract(item["contract"])
        except checks.ContractError as exc:
            first = exc.issues[0] if exc.issues else {"location": "contract", "message": "is invalid"}
            return bad("invalid", f"{at}.contract.{first['location']}: {first['message']}")
        if contract_schema.contract_digest(item["contract"]) != item["digest"]:
            return bad("digest_mismatch", f"{at}.digest: does not match the stored definition; restore the "
                                          "original definition or append a new revision")
        out.append(Revision(item["revision"], item["label"], item["created_at"], item["origin"],
                            item["digest"], contract, basis))
    return obj["family"], tuple(out), None


def encode_container(name: str, family: str, revisions) -> bytes:
    return jsonio.encode_file_exact({"format_version": FORMAT_VERSION, "name": name, "family": family,
                                     "latest": revisions[-1].revision,
                                     "revisions": [r.stored() for r in revisions]})


# --- the store ------------------------------------------------------------------------------------------
@dataclass
class Snapshot:
    """A coherent copy of the parsed entries, for resolving every port of one block."""
    entries: dict = field(default_factory=dict)

    def get(self, name: str) -> Entry | None:
        return self.entries.get(name)

    def pin_state(self, name: str, revision: int, digest: str) -> tuple:
        return pin_state(self.entries.get(name), revision, digest)

    def resolve(self, name: str, revision: int, digest: str):
        return resolve_pin(self.entries.get(name), name, revision, digest)


def pin_state(entry: Entry | None, revision: int, digest: str) -> tuple:
    """What a block's effective revision depends on for one pin: never the container's latest or its
    file token, so appending a revision or changing the family leaves a pin's state unchanged."""
    if entry is None:
        return ("missing",)
    if entry.problem is not None:
        return ("unusable", entry.problem.code)
    found = entry.revision(revision)
    if found is None:
        return ("no_revision",)
    if found.digest != digest:
        return ("digest_mismatch",)
    return ("ok", found.digest)


def resolve_pin(entry: Entry | None, name: str, revision: int, digest: str):
    """(Revision, None) or (None, (code, message)) for one exact pin."""
    state = pin_state(entry, revision, digest)
    if state[0] == "ok":
        return entry.revision(revision), None
    messages = {"missing": ("no_such_contract", "the saved contract does not exist"),
                "unusable": ("contract_unusable", "the saved contract file has a problem"),
                "no_revision": ("no_such_revision", "the pinned revision does not exist"),
                "digest_mismatch": ("digest_mismatch", "the pinned revision's digest does not match the reference")}
    return None, messages[state[0]]


class ContractStore:
    def __init__(self, workspace_path: Path):
        self.path = Path(workspace_path) / DIRECTORY
        self._entries: dict[str, Entry] = {}

    # --- reading ---------------------------------------------------------------------------------
    def _folder_ok(self) -> bool:
        try:
            st = os.lstat(self.path)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode)

    def _listing(self) -> dict[str, tuple] | None:
        """name -> fingerprint of every contract-named entry; None when the folder cannot be listed safely."""
        if not self._folder_ok():
            return None
        found: dict[str, tuple] = {}
        try:
            with os.scandir(self.path) as folder:
                for item in folder:
                    stem = item.name[:-len(SUFFIX)]
                    if not item.name.endswith(SUFFIX) or NAME_RE.match(stem) is None:
                        continue
                    try:
                        st = item.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    except OSError as exc:
                        found[stem] = ("unreadable", exc.errno)
                        continue
                    if stat.S_ISLNK(st.st_mode):
                        found[stem] = ("link",)
                    elif not stat.S_ISREG(st.st_mode):
                        found[stem] = ("not_file",)
                    else:
                        found[stem] = (st.st_ino, st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            return {}
        except OSError:
            return None
        return found

    def _read_bytes(self, name: str) -> tuple[bytes | None, tuple, Problem | None]:
        """The current bytes of one file, never following a link. (data, fingerprint, problem)."""
        file = self.path / (name + SUFFIX)
        where = str(file)
        if not self._folder_ok():
            return None, ("folder",), Problem(where, "unusable", "the contracts folder is not a real folder")
        try:
            st = os.lstat(file)
        except FileNotFoundError:
            return None, ("missing",), None
        except OSError as exc:
            return None, ("unreadable", exc.errno), Problem(where, "unreadable", "the file cannot be read")
        if stat.S_ISLNK(st.st_mode):
            return None, ("link",), Problem(where, "unusable", "a symbolic link is not followed")
        if not stat.S_ISREG(st.st_mode):
            return None, ("not_file",), Problem(where, "unusable", "the path is not a regular file")
        fingerprint = (st.st_ino, st.st_mtime_ns, st.st_size)
        if st.st_size > CONTAINER_LIMIT:
            return None, fingerprint, Problem(where, "too_large", f"the file is larger than {CONTAINER_LIMIT:,} bytes")
        try:
            fd = os.open(file, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError:
            return None, fingerprint, Problem(where, "unreadable", "the file cannot be read")
        try:
            chunks, remaining = [], CONTAINER_LIMIT + 1
            while remaining > 0:
                chunk = os.read(fd, min(remaining, 1 << 20))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        except OSError:
            return None, fingerprint, Problem(where, "unreadable", "the file cannot be read")
        finally:
            os.close(fd)
        data = b"".join(chunks)
        if len(data) > CONTAINER_LIMIT:
            return None, fingerprint, Problem(where, "too_large", f"the file is larger than {CONTAINER_LIMIT:,} bytes")
        return data, fingerprint, None

    def _entry(self, name: str, data: bytes | None, fingerprint: tuple, problem: Problem | None) -> Entry:
        if data is None:
            return Entry(name, fingerprint, None, None, (), problem or
                         Problem(str(self.path / (name + SUFFIX)), "unreadable", "the file cannot be read"))
        family, revisions, problem = parse_container(name, data, str(self.path / (name + SUFFIX)))
        return Entry(name, fingerprint, file_token(data), family, revisions, problem, len(data))

    def read_current(self, name: str) -> Entry | None:
        """Read and hash the current bytes (never the stat cache). None when no such file exists."""
        data, fingerprint, problem = self._read_bytes(name)
        if data is None and problem is None:
            self._entries.pop(name, None)
            return None
        entry = self._entry(name, data, fingerprint, problem)
        self._entries[name] = entry
        return entry

    def rescan(self) -> None:
        """Notice changes on disk: reparse new and changed files, drop removed ones."""
        found = self._listing()
        if found is None:
            return
        for name in [n for n in self._entries if n not in found]:
            del self._entries[name]
        for name, fingerprint in found.items():
            entry = self._entries.get(name)
            if entry is None or entry.fingerprint != fingerprint:
                data, read_fp, problem = self._read_bytes(name)
                if data is None and problem is None:
                    self._entries.pop(name, None)
                    continue
                self._entries[name] = self._entry(name, data, read_fp, problem)

    def get(self, name: str) -> Entry | None:
        return self._entries.get(name)

    def entries(self) -> list[Entry]:
        return [self._entries[name] for name in sorted(self._entries)]

    def snapshot(self) -> Snapshot:
        return Snapshot(dict(self._entries))

    def pin_state(self, name: str, revision: int, digest: str) -> tuple:
        return pin_state(self._entries.get(name), revision, digest)

    def resolve(self, name: str, revision: int, digest: str):
        return resolve_pin(self._entries.get(name), name, revision, digest)

    def families(self) -> list[str]:
        found = {e.family for e in self._entries.values() if e.problem is None and e.family}
        return sorted(found | {DEFAULT_FAMILY}, key=lambda f: (f.casefold(), f))

    # --- writing ---------------------------------------------------------------------------------
    def _collision(self, name: str) -> bool:
        """Another file whose name differs only by case (Linux/macOS consistency)."""
        try:
            with os.scandir(self.path) as folder:
                for item in folder:
                    if not item.name.endswith(SUFFIX):
                        continue
                    stem = item.name[:-len(SUFFIX)]
                    if stem != name and stem.casefold() == name.casefold():
                        return True
        except FileNotFoundError:
            return False
        return False

    def _ensure_folder(self) -> None:
        try:
            st = os.lstat(self.path)
        except FileNotFoundError:
            os.mkdir(self.path, 0o700)
            os.chmod(self.path, 0o700)
            _sync_directory(self.path.parent)
            return
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise StoreError("contract_unusable", 409, "the contracts folder is not a real folder")

    def _publish(self, name: str, data: bytes, previous_token: str | None) -> str:
        """Atomically replace (or create) one container. Returns "committed" or "durability_uncertain".
        Raises StoreError(stale_contract) when the file changed since it was read under the reservation."""
        self._ensure_folder()
        target = self.path / (name + SUFFIX)
        fd, tmp = tempfile.mkstemp(dir=self.path, prefix="." + name + ".", suffix=".tmp")
        replaced = False
        try:
            try:
                os.fchmod(fd, 0o600)
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view):]
                os.fsync(fd)
            finally:
                os.close(fd)
            # Recheck the observed file immediately before replacement (spec 11).
            current, _fp, problem = self._read_bytes(name)
            if problem is not None and problem.code == "unusable":
                raise StoreError("contract_unusable", 409, problem.message)
            if (file_token(current) if current is not None else None) != previous_token:
                raise StoreError("stale_contract", 409, "the contract file changed since it was read",
                                 file_token=file_token(current) if current is not None else None)
            before_contract_replace(name)
            try:
                os.replace(tmp, target)
                replaced = True
            except OSError:
                # Ambiguous: settle by rereading, never by blind retry.
                after, _fp, _problem = self._read_bytes(name)
                if after == data:
                    replaced = True
                    return "durability_uncertain"
                raise
        finally:
            if not replaced:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        try:
            after_contract_replace(name)
            _sync_directory(self.path)
        except OSError:
            return "durability_uncertain"
        return "committed"

    def _locked_current(self, name: str, expected_token: str | None, *, must_exist: bool) -> Entry | None:
        if not valid_name(name):
            raise StoreError("invalid", 400, "the contract name must follow the name rule")
        entry = self.read_current(name)
        if entry is not None and entry.problem is not None and entry.problem.code == "unusable":
            raise StoreError("contract_unusable", 409, entry.problem.message, problem=entry.problem_view())
        if expected_token is None:
            if entry is not None:
                raise StoreError("contract_exists", 409, "a contract with that name already exists",
                                 file_token=entry.token)
            if self._collision(name):
                raise StoreError("contract_exists", 409, "a contract whose name differs only by case exists")
            if must_exist:
                raise StoreError("no_such_contract", 404, "no such contract")
            return None
        if entry is None:
            raise StoreError("no_such_contract", 404, "no such contract")
        if entry.token != expected_token:
            raise StoreError("stale_contract", 409, "the contract was changed elsewhere; refresh and review",
                             file_token=entry.token)
        return entry

    @staticmethod
    def _usable(entry: Entry) -> None:
        if entry.problem is not None:
            raise StoreError("contract_unusable", 409, "the existing contract file has a problem and is never "
                             "written over", problem=entry.problem_view())

    def _append(self, entry: Entry, contract: checks.Contract, digest: str, label: str, origin: str,
                basis: dict | None) -> dict:
        new = Revision(entry.latest + 1, label, utc_now(), origin, digest, contract, basis)
        revisions = (*entry.revisions, new)
        data = encode_container(entry.name, entry.family, revisions)
        if len(data) > CONTAINER_LIMIT:
            raise StoreError("too_large", 400, "appending would exceed the contract file limit; create a new "
                                               "named contract instead (revisions are never pruned)")
        publication = self._publish(entry.name, data, entry.token)
        self.read_current(entry.name)
        return {"name": entry.name, "family": entry.family, "revision": new.revision, "digest": digest,
                "file_token": file_token(data), "unchanged": False, "publication": publication,
                "latest": new.revision}

    def save(self, name: str, family: str, expected_token: str | None, definition_text: str, label: str, *,
             origin: str = "manual", basis: dict | None = None, read_only: bool = False) -> dict:
        """Create (expected token null) or append. Raises StoreError."""
        if read_only:
            raise StoreError("read_only", 409, "the workspace is read-only")
        problem = text_issue(label, LABEL_LIMIT, blank_ok=True)
        if problem:
            raise StoreError("invalid", 400, f"label {problem}")
        problem = family_issue(family)
        if problem:
            raise StoreError("invalid", 400, f"family {problem}")
        if origin not in ORIGINS or (basis is not None and basis_issues(basis)):
            raise StoreError("invalid", 400, "invalid provenance")
        contract, digest = parse_definition(definition_text)
        entry = self._locked_current(name, expected_token, must_exist=False)
        if entry is None:
            first = Revision(1, label, utc_now(), origin, digest, contract, basis)
            data = encode_container(name, family, (first,))
            if len(data) > CONTAINER_LIMIT:
                raise StoreError("too_large", 400, "the contract file would be too large")
            publication = self._publish(name, data, None)
            self.read_current(name)
            return {"name": name, "family": family, "revision": 1, "digest": digest,
                    "file_token": file_token(data), "unchanged": False, "publication": publication,
                    "latest": 1, "created": True}
        self._usable(entry)
        if family != entry.family:
            raise StoreError("invalid", 400, "a family change is a separate grouping save")
        latest = entry.revisions[-1]
        if latest.digest == digest:
            return {"name": name, "family": entry.family, "revision": latest.revision, "digest": digest,
                    "file_token": entry.token, "unchanged": True, "publication": "unchanged",
                    "latest": latest.revision}
        return self._append(entry, contract, digest, label, origin, basis)

    def set_family(self, name: str, expected_token: str, family: str, *, read_only: bool = False) -> dict:
        if read_only:
            raise StoreError("read_only", 409, "the workspace is read-only")
        problem = family_issue(family)
        if problem:
            raise StoreError("invalid", 400, f"family {problem}")
        entry = self._locked_current(name, expected_token, must_exist=True)
        self._usable(entry)
        identities = [{"revision": r.revision, "digest": r.digest} for r in entry.revisions]
        if family == entry.family:
            return {"name": name, "family": family, "file_token": entry.token, "unchanged": True,
                    "publication": "unchanged", "revisions": identities}
        data = encode_container(name, family, entry.revisions)
        publication = self._publish(name, data, entry.token)
        self.read_current(name)
        return {"name": name, "family": family, "file_token": file_token(data), "unchanged": False,
                "publication": publication, "revisions": identities}

    def restore(self, name: str, expected_token: str, revision: int, label: str, *, read_only=False) -> dict:
        if read_only:
            raise StoreError("read_only", 409, "the workspace is read-only")
        problem = text_issue(label, LABEL_LIMIT, blank_ok=True)
        if problem:
            raise StoreError("invalid", 400, f"label {problem}")
        entry = self._locked_current(name, expected_token, must_exist=True)
        self._usable(entry)
        old = entry.revision(revision)
        if old is None:
            raise StoreError("no_such_revision", 404, "no such revision")
        latest = entry.revisions[-1]
        if old.digest == latest.digest:
            return {"name": name, "family": entry.family, "revision": latest.revision, "digest": latest.digest,
                    "file_token": entry.token, "unchanged": True, "publication": "unchanged",
                    "latest": latest.revision}
        return self._append(entry, old.contract, old.digest, label, "manual", None)

    def delete(self, name: str, expected_token: str, scan, *, read_only=False) -> dict:
        """`scan(name)` -> (blockers, unreadable paths). A malformed file can be deleted, never overwritten."""
        if read_only:
            raise StoreError("read_only", 409, "the workspace is read-only")
        if not valid_name(name):
            raise StoreError("invalid", 400, "the contract name must follow the name rule")
        entry = self.read_current(name)
        if entry is None:
            raise StoreError("no_such_contract", 404, "no such contract")
        if entry.token is None or entry.token != expected_token:
            raise StoreError("stale_contract", 409, "the contract was changed elsewhere; refresh and review",
                             file_token=entry.token)
        blockers, unreadable = scan(name)
        if unreadable:
            raise StoreError("reference_scan_incomplete", 409, "some block definitions could not be inspected, "
                             "so absence of use was not established", unreadable=sorted(unreadable))
        if blockers:
            raise StoreError("contract_in_use", 409, "block definitions reference this contract",
                             blockers=blockers)
        target = self.path / (name + SUFFIX)
        before_contract_delete(name)
        current, _fp, _problem = self._read_bytes(name)
        if current is None or file_token(current) != expected_token:
            raise StoreError("stale_contract", 409, "the contract was changed elsewhere; refresh and review")
        try:
            os.unlink(target)
        except FileNotFoundError:
            pass
        self._entries.pop(name, None)
        try:
            _sync_directory(self.path)
        except OSError:
            return {"deleted": True, "publication": "durability_uncertain"}
        return {"deleted": True, "publication": "committed"}


def parse_definition(definition_text: str) -> tuple[checks.Contract, str]:
    """A user definition from exact source text, with its digest. Raises StoreError."""
    try:
        contract = checks.contract_from_text(definition_text)
    except checks.ContractError as exc:
        code = exc.issues[0].get("code", "contract_invalid") if exc.issues else "contract_invalid"
        if code not in ("contract_invalid", "unsupported_keyword", "too_large"):
            code = "contract_invalid"
        raise StoreError(code, 400, "the contract definition is invalid", exc.issues, exc.total) from None
    if contract.schema is not None and not checks.schema_support()[0]:
        raise StoreError("validation_unavailable", 503, "repair the AgentWiz validator installation")
    return contract, contract_schema.contract_digest(contract.raw)


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
