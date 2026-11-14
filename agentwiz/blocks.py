"""Blocks: folders in the workspace that hold one runnable step (0.1.4, Section 8).

    <workspace>/blocks/<name>/
        block.json
        prompts/<file>.md
        scripts/<file>.py

Workshop publishes versioned assets. This loader reads executable definitions and managed heads. `block.json` is read with the exact reader, so inline contracts keep their numbers digit for
digit. Every key not listed in Section 8.2 is refused with its location, so a mistyped key never silently does
nothing (P7). Messages name locations and rules, never values.

`BlockStore` is called from `state.py` under the state lock, as `contracts.ContractStore` is. It lists the
folders directly under `blocks/`, loads a block on demand, fingerprints exactly the files the last load
referenced, and gives each block an **effective revision** from one process-wide counter: it changes whenever
the block's own referenced files (by content) or the state of any exact contract pin it holds (the pinned
revision's existence, usability and digest) change, so a revision is never repeated (P11). Appending a newer
revision to a referenced contract, or changing its family, does not change a pin's state. Knows nothing of HTTP. Standard library only.
"""
from __future__ import annotations

import dataclasses
import hashlib
import itertools
import os
import re
import stat
import threading
import weakref
from collections import OrderedDict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from . import checks, jsonio, templates, tool_forms, transforms
from .contracts import DIGEST_RE, ContractStore
from .model_step import ContractCopy
from .task_settings import OVERRIDABLE, TaskSettingError, TaskSettings, parse_task
from .workspace import NAME_RE, NAME_RULE, valid_name

DIRECTORY = "blocks"
FILE = "block.json"
FORMAT_VERSION = 3
WORKFLOW_FORMAT_VERSION = 4               # executable definitions; 2 is the Workshop managed head
BLOCK_FILE_LIMIT = 1 << 20      # block.json
FILE_LIMIT = 4 << 20            # each referenced prompt or script file
LIST_LIMIT = 200                # blocks shown in the listing (never limits which blocks can be opened or run)
CACHE_LIMIT = 400               # loaded blocks kept in memory
ISSUE_LIMIT = 100               # issues listed for one block
INPUT_LIMIT = 50
OUTPUT_LIMIT = 50
DEFAULT_MAX_SECONDS = 1800
DEFAULT_MAX_REPAIRS = 2
DEFAULT_TIMEOUT_S = 60
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
TASK_KEYS = ("reasoning", "temperature", "max_tokens", "advanced")

_revisions = itertools.count(1)
_revisions_lock = threading.Lock()


def _next_revision() -> int:
    with _revisions_lock:
        return next(_revisions)


# --- value types ----------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Budget:
    max_seconds: int = DEFAULT_MAX_SECONDS
    max_model_tokens: int | None = None

    def view(self) -> dict:
        return {"max_seconds": self.max_seconds, "max_model_tokens": self.max_model_tokens}


@dataclass(frozen=True)
class Reference:
    """An exact saved-contract pin: never "latest", never a name alone."""
    name: str
    revision: int
    digest: str

    def view(self) -> dict:
        return {"name": self.name, "revision": self.revision, "digest": self.digest}


@dataclass(frozen=True)
class Declared:
    name: str
    description: str
    contract: checks.Contract | None      # an inline contract, parsed
    reference: Reference | None = None    # a saved contract's exact pin
    local_only: bool = False


@dataclass(frozen=True)
class Step:
    id: str
    kind: str                             # "model" | "tool" | "script"
    raw: Mapping                          # the step object, exact-parsed
    output: str | None = None             # tool: the declared output the step fills
    role: str | None = None               # model
    task: TaskSettings | None = None      # model: the block's own explicit settings
    max_repairs: int | None = None        # model
    system: str | None = None             # model: the template text (from the file or inline)
    prompt: str | None = None             # model
    response_mode: str | None = None      # model: "named_json", the only mode
    task_resets: tuple[str, ...] = ()     # model: settings that ask for the backend default
    server: str | None = None             # tool
    tool: str | None = None               # tool
    arguments: object = None              # tool: the arguments template
    timeout_s: int | None = None          # script
    script_file: str | None = None        # script: the reference, relative to the block folder
    script_path: Path | None = None       # script: absolute


@dataclass(frozen=True)
class FileView:
    path: str
    sha256: str | None
    text: str | None
    unavailable: str | None

    def view(self) -> dict:
        return {"path": self.path, "sha256": self.sha256, "text": self.text, "unavailable": self.unavailable}


@dataclass(frozen=True)
class Block:
    name: str
    revision: int
    description: str
    inputs: tuple[Declared, ...]
    outputs: tuple[Declared, ...]
    step: Step
    budget: Budget
    files: Mapping[str, str]              # relative path -> sha256, block.json included
    texts: Mapping[str, str]              # relative path -> text, for block.json and prompt files
    contracts: Mapping[str, Mapping[str, ContractCopy]]   # {"inputs": {...}, "outputs": {...}}, captured
    notes: tuple[str, ...]
    format_version: int = FORMAT_VERSION
    context_local_only: bool = False


@dataclass(frozen=True)
class BlockProblem:
    file: str
    code: str                             # "unreadable" | "invalid" | "newer_format" | "unusable"
    issues: tuple[dict, ...]              # [{"location", "message"}], at most ISSUE_LIMIT
    total: int

    def view(self) -> dict:
        return {"file": self.file, "code": self.code, "issues": [dict(i) for i in self.issues], "total": self.total}


@dataclass(frozen=True)
class Loaded:
    """One load of a block folder: the block (or its problem) at its effective revision."""
    name: str
    revision: int
    block: Block | None
    problem: BlockProblem | None
    description: str
    kind: str | None
    views: tuple[FileView, ...]
    notes: tuple[str, ...]
    # what the last load referenced, for fingerprints and revisions
    paths: tuple[str, ...]
    contract_pins: tuple[Reference, ...]
    sig: tuple
    content: tuple

    workshop: dict = field(default_factory=dict)
    physical_root: Path | None = None

    def list_row(self) -> dict:
        return {"name": self.name, "display_name": self.workshop.get('display_name', self.name),
                "revision": self.revision, "description": self.description, "kind": self.kind,
                "problem": self.problem.view() if self.problem else None}


# --- bounded, link-free file reading (8.3) ---------------------------------------------------------------
@dataclass(frozen=True)
class ReadResult:
    data: bytes | None
    fingerprint: tuple
    reason: str | None


def _strerror(exc: OSError) -> str:
    return exc.strerror or type(exc).__name__


def read_file(folder: Path, rel: str, limit: int) -> ReadResult:
    """Read `folder/rel` only when every component is a real folder, the file is a regular file within
    `limit` bytes. A symbolic link or an oversized file is never read, even to show it. The fingerprint is
    taken before the read (inode, modification time in nanoseconds, size)."""
    path = Path(folder)
    parts = rel.split("/")
    try:
        for part in parts[:-1]:
            path = path / part
            st = os.lstat(path)
            if stat.S_ISLNK(st.st_mode):
                return ReadResult(None, ("link",), "a symbolic link is not followed")
            if not stat.S_ISDIR(st.st_mode):
                return ReadResult(None, ("not_folder",), "a path component is not a folder")
        path = path / parts[-1]
        st = os.lstat(path)
    except FileNotFoundError:
        return ReadResult(None, ("missing",), "the file is missing")
    except OSError as exc:
        return ReadResult(None, ("unreadable", exc.errno), f"the file cannot be read ({_strerror(exc)})")
    fingerprint = (st.st_ino, st.st_mtime_ns, st.st_size)
    if stat.S_ISLNK(st.st_mode):
        return ReadResult(None, ("link",), "a symbolic link is not followed")
    if not stat.S_ISREG(st.st_mode):
        return ReadResult(None, ("not_file",), "the path is not a regular file")
    if st.st_size > limit:
        return ReadResult(None, fingerprint, f"the file is larger than {limit:,} bytes")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        return ReadResult(None, fingerprint, f"the file cannot be read ({_strerror(exc)})")
    try:
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 1 << 20))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    except OSError as exc:
        return ReadResult(None, fingerprint, f"the file cannot be read ({_strerror(exc)})")
    finally:
        os.close(fd)
    if len(data) > limit:
        return ReadResult(None, fingerprint, f"the file is larger than {limit:,} bytes")
    return ReadResult(data, fingerprint, None)


def _fingerprint(folder: Path, rel: str) -> tuple:
    """A cheap fingerprint of one referenced file, to notice changes without reading bytes."""
    try:
        st = os.lstat(Path(folder) / rel)
    except FileNotFoundError:
        return ("missing",)
    except OSError as exc:
        return ("unreadable", exc.errno)
    if stat.S_ISLNK(st.st_mode):
        return ("link", st.st_ino, st.st_mtime_ns)
    return (st.st_ino, st.st_mtime_ns, st.st_size)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- issue collection ------------------------------------------------------------------------------------
class _Issues:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str]] = []     # (kind, location, message)

    def add(self, location: str, message: str, kind: str = "invalid") -> None:
        self.items.append((kind, location, message))

    def __bool__(self) -> bool:
        return bool(self.items)

    def code(self) -> str:
        kinds = {kind for kind, _loc, _msg in self.items}
        for code in ("migration_required", "invalid", "unreadable", "unusable"):
            if code in kinds:
                return code
        return "invalid"

    def problem(self, file: str) -> BlockProblem:
        shown = tuple({"location": loc, "message": msg} for _kind, loc, msg in self.items[:ISSUE_LIMIT])
        return BlockProblem(file, self.code(), shown, len(self.items))


# --- parsing block.json -----------------------------------------------------------------------------------
_TOP_KEYS = ("format_version", "name", "description", "inputs", "outputs", "steps", "budget")
_STEP_KEYS = {
    "model": ("id", "kind", "role", "system", "prompt", "response_mode", "task", "task_resets", "repair"),
    "tool": ("id", "kind", "server", "tool", "arguments", "output"),
    "script": ("id", "kind", "file", "timeout_s"),
    "transform": ("id", "kind", "operation", "operation_version", "parameters", "output"),
}
_DECLARATION_KEYS = ("contract", "description")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _reference_issue(ref: object, prefix: str, suffix: str | None) -> str | None:
    """The rule a file reference breaks, or None."""
    if not isinstance(ref, str) or ref == "":
        return "must be a relative path"
    if "\\" in ref or "\x00" in ref:
        return "must be a relative POSIX path"
    if ref.startswith("/"):
        return "must be a relative path, with no leading slash"
    parts = ref.split("/")
    if any(part == "" for part in parts):
        return "must not hold an empty path segment"
    if any(part in (".", "..") for part in parts):
        return 'must not hold a "." or ".." segment'
    if not ref.startswith(prefix):
        return f'must start with "{prefix}"'
    if suffix is not None and not ref.endswith(suffix):
        return f'must end with "{suffix}"'
    return None


def _task_value(value: Any, where: str, issues: _Issues) -> Any:
    """0.1.2's task settings types from exact-parsed JSON: an integer stays an integer; a decimal is accepted
    only when it equals, exactly, the float whose repr has the same value."""
    if isinstance(value, Decimal):
        try:
            as_float = float(value)
            exact = Decimal(repr(as_float)) == value
        except (OverflowError, ValueError, ArithmeticError):
            exact = False
        if not exact:
            issues.add(where, "a number in task cannot be kept exactly")
            return 0
        return as_float
    if isinstance(value, list):
        return [_task_value(item, f"{where}[{index}]", issues) for index, item in enumerate(value)]
    if isinstance(value, Mapping):
        return {key: _task_value(item, where + (f".{key}" if IDENT.match(key) else "[" + jsonio.encode_exact(key) + "]"),
                                 issues)
                for key, item in value.items()}
    return value


class _Parse:
    """The working state of one load."""

    def __init__(self, folder: Path, name: str, contract_store: ContractStore, *, internal_contracts=False,
                 validators=None):
        self.version = FORMAT_VERSION
        self.folder = folder
        self.name = name
        self.contract_store = contract_store
        self.internal_contracts = internal_contracts
        self.validators = dict(validators or {})
        self.issues = _Issues()
        self.views: list[FileView] = []
        self.files: dict[str, str] = {}
        self.texts: dict[str, str] = {}
        self.paths: list[str] = [FILE]
        self.fingerprints: dict[str, tuple] = {}
        self.contract_pins: list[Reference] = []
        self.notes: list[str] = []
        self.captured: dict[str, dict[str, ContractCopy]] = {"inputs": {}, "outputs": {}}
        self.declared_inputs: set[str] = set()
        self.declared_outputs: list[str] = []     # every output named with an identifier, even one whose contract failed

    # --- files ---
    def referenced(self, rel: str, where: str, keep_text: bool, as_text: bool) -> tuple[str | None, str | None]:
        """Read a referenced file once. Returns (text, sha256), either None when unavailable (an issue is added)."""
        if rel not in self.paths:
            self.paths.append(rel)
        result = read_file(self.folder, rel, FILE_LIMIT)
        self.fingerprints[rel] = result.fingerprint
        if result.data is None:
            self.views.append(FileView(rel, None, None, result.reason))
            self.issues.add(where, result.reason or "the file cannot be read", "unreadable")
            return None, None
        digest = _sha256(result.data)
        self.files[rel] = digest
        text = result.data.decode("utf-8", errors="replace")
        if text.startswith("﻿"):
            text = text[1:]
        self.views.append(FileView(rel, digest, text, None))
        if not as_text:
            return None, digest
        try:
            result.data.decode("utf-8")
        except UnicodeDecodeError:
            self.issues.add(where, "the file is not valid UTF-8")
            return None, digest
        if keep_text:
            self.texts[rel] = text
        return text, digest

    # --- contracts ---
    def contract(self, section: str, name: str, declaration: object, where: str) -> Declared | None:
        if not isinstance(declaration, Mapping):
            self.issues.add(where, "must be an object")
            return None
        local_only = declaration.get("local_only")
        if self.version == WORKFLOW_FORMAT_VERSION and type(local_only) is not bool:
            self.issues.add(f"{where}.local_only", "must be an explicit boolean")
        for key in declaration:
            if key not in _DECLARATION_KEYS and not (key == "local_only" and self.version == WORKFLOW_FORMAT_VERSION):
                self.issues.add(f"{where}.{key}", "is not a key of an input or output declaration")
        description = declaration.get("description", "")
        if "description" in declaration and not isinstance(description, str):
            self.issues.add(f"{where}.description", "must be a string")
            description = ""
        if "contract" not in declaration:
            self.issues.add(f"{where}.contract", "is required: an inline contract or the name of a saved contract")
            return None
        contract = declaration["contract"]
        location = f"{where}.contract"
        if isinstance(contract, Mapping) and ("format" in contract or not
                                              {"name", "revision", "digest"} & set(contract)):
            try:
                if self.internal_contracts:
                    parsed = checks.parse_internal_contract(dict(contract),
                                                            validator=self.validators.get((section, name)))
                else:
                    parsed = checks.parse_contract(dict(contract))
            except checks.ContractError as exc:
                for item in exc.issues:
                    self.issues.add(f"{location}.{item['location']}", item["message"])
                return None
            self.captured[section][name] = ContractCopy.inline(parsed)
            return Declared(name, description, parsed, None, local_only is True)
        if isinstance(contract, Mapping):
            reference = self.reference(contract, location)
            if reference is None:
                return None
            if self.internal_contracts:
                self.issues.add(location, "a server-owned definition holds inline contracts only")
                return None
            if reference not in self.contract_pins:
                self.contract_pins.append(reference)
            revision, refused = self.contract_store.resolve(reference.name, reference.revision, reference.digest)
            if refused is not None:
                self.issues.add(location, f"the pinned saved contract cannot be used ({refused[0]}: {refused[1]})",
                                "unusable")
            else:
                self.captured[section][name] = ContractCopy.reference(reference.name, reference.revision,
                                                                      reference.digest, revision.contract)
            return Declared(name, description, None, reference, local_only is True)
        if isinstance(contract, str):
            self.issues.add(location, "reference_invalid: a saved contract is referenced by an exact "
                            '{"name", "revision", "digest"} pin, not by its name alone')
            return None
        self.issues.add(location, "must be an inline contract or an exact saved-contract reference")
        return None

    def reference(self, value: Mapping, location: str) -> Reference | None:
        """An exact pin: exactly name, revision and digest; no format, family, "latest" or null revision."""
        keys = set(value)
        if keys != {"name", "revision", "digest"}:
            for key in sorted(keys - {"name", "revision", "digest"}):
                self.issues.add(f"{location}.{key}", "reference_invalid: is not a member of a saved-contract reference")
            for key in sorted({"name", "revision", "digest"} - keys):
                self.issues.add(f"{location}.{key}", "reference_invalid: is required in a saved-contract reference")
            return None
        name, revision, digest = value["name"], value["revision"], value["digest"]
        ok = True
        if not valid_name(name):
            self.issues.add(f"{location}.name", f"reference_invalid: must be a saved contract's name ({NAME_RULE})")
            ok = False
        if not _is_int(revision) or revision < 1:
            self.issues.add(f"{location}.revision", "reference_invalid: must be a positive revision number")
            ok = False
        if not isinstance(digest, str) or DIGEST_RE.match(digest) is None:
            self.issues.add(f"{location}.digest", "reference_invalid: must be sha256: and 64 lowercase hexadecimal characters")
            ok = False
        return Reference(name, revision, digest) if ok else None

    def declarations(self, section: str, value: object, minimum: int, limit: int) -> tuple[Declared, ...]:
        if not isinstance(value, Mapping):
            self.issues.add(section, "must be an object")
            return ()
        if len(value) < minimum:
            self.issues.add(section, f"must declare at least {minimum} {section[:-1]}")
        if len(value) > limit:
            self.issues.add(section, f"may declare at most {limit} entries")
        out: list[Declared] = []
        for key, declaration in value.items():
            if not IDENT.match(key):
                self.issues.add(section, "has a name that is not an identifier (letters, digits and _, not starting with a digit)")
                continue
            declared = self.contract(section, key, declaration, f"{section}.{key}")
            if declared is not None:
                out.append(declared)
        return tuple(out)

    # --- templates ---
    def check_placeholders(self, placeholders, where: str) -> None:
        for placeholder in placeholders:
            if placeholder.path[0] not in self.declared_inputs:
                self.issues.add(where, f"{placeholder.text} names an input that is not declared")


def _parse_step(p: _Parse, steps: object, outputs: tuple[Declared, ...]) -> Step | None:
    issues = p.issues
    if not isinstance(steps, list):
        issues.add("steps", "must be a list of exactly one step")
        return None
    if len(steps) == 0:
        issues.add("steps", "must hold exactly one step")
        return None
    if len(steps) > 1:
        # the block is unusable, but the first step is still checked so every issue is listed in one pass
        issues.add("steps", "a block runs exactly one step")
    raw = steps[0]
    where = "steps[0]"
    if not isinstance(raw, Mapping):
        issues.add(where, "must be an object")
        return None
    kind = raw.get("kind")
    sid = raw.get("id")
    if "id" not in raw:
        issues.add(f"{where}.id", "is required")
    elif not isinstance(sid, str) or not IDENT.match(sid):
        issues.add(f"{where}.id", "must be an identifier (letters, digits and _, not starting with a digit)")
        sid = None
    if "kind" not in raw:
        issues.add(f"{where}.kind", 'is required: "model", "tool" or "script"')
        return None
    if kind == "block":
        issues.add(f"{where}.kind", "connect separate blocks in a workflow")
        return None
    if not isinstance(kind, str) or kind not in _STEP_KEYS:
        issues.add(f"{where}.kind", 'must be "model", "tool" or "script"')
        return None
    if (kind == "script" and p.version == WORKFLOW_FORMAT_VERSION
            or kind == "transform" and p.version != WORKFLOW_FORMAT_VERSION):
        issues.add(f"{where}.kind", "host scripts require v3 and transforms require v4")
        return None
    for key in raw:
        if key in _STEP_KEYS[kind]:
            continue
        if kind == "model" and key == "output":
            # 0.1.x blocks named the one output on the step. The envelope returns every declared output,
            # so the rewrite is mechanical: drop this key, add the response mode, keep `outputs` as it is.
            issues.add(f"{where}.output", 'is a 0.1.x key: remove it and add "response_mode": "named_json" '
                       "to this step; the envelope returns every output declared in outputs")
        else:
            issues.add(f"{where}.{key}", f"is not a key of a {kind} step")
    output_names = p.declared_outputs

    def declared_output() -> str | None:
        value = raw.get("output")
        if "output" not in raw:
            issues.add(f"{where}.output", "is required: the declared output the step fills")
            return None
        if not isinstance(value, str) or value not in output_names:
            issues.add(f"{where}.output", "must name a declared output")
            return None
        return value

    step_id = sid if isinstance(sid, str) else "step"
    if kind == "model":
        return _parse_model(p, raw, where, step_id, outputs)
    if kind == "tool":
        output = declared_output()
        if len(p.declared_outputs) != 1:
            issues.add("outputs", "a tool block declares exactly one output, the one its step names")
        server, tool = raw.get("server"), raw.get("tool")
        if "server" not in raw:
            issues.add(f"{where}.server", "is required: a server in the MCP library")
        elif not valid_name(server):
            issues.add(f"{where}.server", f"must be a server name ({NAME_RULE})")
        if "tool" not in raw:
            issues.add(f"{where}.tool", "is required: the tool's name")
        elif not isinstance(tool, str) or tool == "":
            issues.add(f"{where}.tool", "must be a non-empty string")
        arguments = raw.get("arguments", {})
        if not isinstance(arguments, Mapping):
            issues.add(f"{where}.arguments", "must be a JSON object")
            arguments = {}
        try:
            found = templates.scan_arguments(arguments)
        except templates.TemplateError as exc:
            issues.add(f"{where}.{exc.location}", exc.message)
        else:
            for loc, placeholder in found:
                p.check_placeholders([placeholder], f"{where}.{loc}")
        return Step(step_id, "tool", raw, output=output, server=server if isinstance(server, str) else None,
                    tool=tool if isinstance(tool, str) else None, arguments=arguments)
    if kind == "transform":
        output = declared_output()
        if len(output_names) != 1:
            issues.add("outputs", "a transform declares exactly one output")
        if type(raw.get("operation_version")) is not int or raw["operation_version"] != 1:
            issues.add(f"{where}.operation_version", "must be integer 1")
        try:
            transforms.validate(raw.get("operation"), raw.get("parameters"), p.declared_inputs)
        except (templates.TemplateError, ValueError) as exc:
            issues.add(f"{where}.parameters", str(exc))
        return Step(step_id, kind, raw, output=output)
    # script
    ref = raw.get("file")
    path: Path | None = None
    if "file" not in raw:
        issues.add(f"{where}.file", "is required: a path under scripts/ ending in .py")
    else:
        problem = _reference_issue(ref, "scripts/", ".py")
        if problem:
            issues.add(f"{where}.file", problem)
        else:
            _text, _digest = p.referenced(ref, f"{where}.file", keep_text=False, as_text=False)
            path = Path(os.path.abspath(p.folder / ref))
    timeout = raw.get("timeout_s", DEFAULT_TIMEOUT_S)
    if "timeout_s" in raw and (not _is_int(timeout) or not 1 <= timeout <= 3600):
        issues.add(f"{where}.timeout_s", "must be an integer from 1 to 3600")
        timeout = DEFAULT_TIMEOUT_S
    return Step(step_id, "script", raw, timeout_s=timeout, script_file=ref if isinstance(ref, str) else None,
                script_path=path)


def _task_resets(value: object, where: str, issues: _Issues) -> list[str]:
    """The step's explicit backend-default requests, in canonical order. Refuses anything else."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > len(OVERRIDABLE):
        issues.add(where, "must be a list holding temperature and/or reasoning")
        return []
    if any(not isinstance(name, str) or name not in OVERRIDABLE for name in value):
        issues.add(where, "may hold only temperature and reasoning")
        return []
    if len(set(value)) != len(value):
        issues.add(where, "must not repeat a setting")
        return []
    return [name for name in OVERRIDABLE if name in value]


def _parse_model(p: _Parse, raw: Mapping, where: str, step_id: str, outputs: tuple[Declared, ...]) -> Step:
    issues = p.issues
    mode = raw.get("response_mode")
    if "response_mode" not in raw:
        issues.add(f"{where}.response_mode", 'is required: "named_json"')
    elif mode != "named_json":
        issues.add(f"{where}.response_mode", 'must be "named_json": a model step returns one named JSON envelope')
    if len(p.declared_outputs) < 1:
        issues.add("outputs", "a model block declares at least one output, and the envelope returns them all")
    role = raw.get("role")
    if "role" not in raw:
        issues.add(f"{where}.role", "is required: a role name")
    elif not valid_name(role):
        issues.add(f"{where}.role", f"must be a role name ({NAME_RULE})")

    def text_of(key: str, required: bool) -> str | None:
        if key not in raw:
            if required:
                issues.add(f"{where}.{key}", 'is required: {"file": "prompts/..."} or {"text": "..."}')
            return None
        spec = raw[key]
        loc = f"{where}.{key}"
        if not isinstance(spec, Mapping) or len(spec) != 1 or next(iter(spec)) not in ("file", "text"):
            issues.add(loc, 'must be an object with exactly one key, "file" or "text"')
            return None
        if "text" in spec:
            if not isinstance(spec["text"], str):
                issues.add(f"{loc}.text", "must be a string")
                return None
            body: str | None = spec["text"]
        else:
            ref = spec["file"]
            problem = _reference_issue(ref, "prompts/", None)
            if problem:
                issues.add(f"{loc}.file", problem)
                return None
            body, _digest = p.referenced(ref, f"{loc}.file", keep_text=True, as_text=True)
            if body is None:
                return None
        try:
            found, output_refs = templates.scan_model_text(body, key)
        except templates.TemplateError as exc:
            issues.add(loc, exc.message)
        else:
            p.check_placeholders(found, loc)
            for ref in output_refs:
                if ref.name not in p.declared_outputs:
                    issues.add(loc, f"{ref.text} names an output that is not declared")
        return body

    system = text_of("system", False)
    prompt = text_of("prompt", True)
    task: TaskSettings | None = None
    explicit: set[str] = set()
    if "task" in raw:
        loc = f"{where}.task"
        value = raw["task"]
        if not isinstance(value, Mapping):
            issues.add(loc, "must be an object")
        else:
            for key in value:
                if key not in TASK_KEYS:
                    issues.add(f"{loc}.{key}", "is not a task setting")
            marker = len(issues.items)
            converted = _task_value({k: v for k, v in value.items() if k in TASK_KEYS}, loc, issues)
            if len(issues.items) == marker:
                try:
                    task = parse_task(converted)
                except TaskSettingError as exc:
                    issues.add(loc, str(exc))
                else:
                    explicit = set(converted)
    else:
        task = TaskSettings()
    resets = _task_resets(raw.get("task_resets") if "task_resets" in raw else None, f"{where}.task_resets", issues)
    # A setting written as an explicit null temperature or a blank reasoning level has always meant
    # "the backend's own default"; inside a role that has defaults it is an explicit reset, not an
    # inheritance. The two spellings are the same request, so both end up in `task_resets`.
    for name in [n for n in OVERRIDABLE if n in explicit]:
        value = task.temperature if name == "temperature" else task.reasoning
        if value is None or value == "":
            explicit.discard(name)
            if name not in resets:
                resets = [n for n in OVERRIDABLE if n in {*resets, name}]
    if explicit & set(resets):
        issues.add(f"{where}.task_resets", "must not name a setting that task also sets")
    max_repairs = DEFAULT_MAX_REPAIRS
    if "repair" in raw:
        repair = raw["repair"]
        loc = f"{where}.repair"
        if not isinstance(repair, Mapping):
            issues.add(loc, "must be an object")
        else:
            for key in repair:
                if key != "max_repairs":
                    issues.add(f"{loc}.{key}", "is not a repair setting (the only key is max_repairs)")
            if "max_repairs" in repair:
                value = repair["max_repairs"]
                if not _is_int(value) or not 0 <= value <= 5:
                    issues.add(f"{loc}.max_repairs", "must be an integer from 0 to 5")
                else:
                    max_repairs = value
    return Step(step_id, "model", raw, output=None, role=role if isinstance(role, str) else None, task=task,
                max_repairs=max_repairs, system=system, prompt=prompt,
                response_mode="named_json" if mode == "named_json" else None, task_resets=tuple(resets))


@dataclass
class _Parsed:
    problem: BlockProblem | None
    block: Block | None
    description: str
    kind: str | None
    views: tuple[FileView, ...]
    notes: tuple[str, ...]
    paths: tuple[str, ...]
    contract_pins: tuple[Reference, ...]
    fingerprints: dict
    content: tuple


def _folder_problem(folder: Path, name: str, reason: str) -> _Parsed:
    issues = _Issues()
    issues.add(DIRECTORY + "/" + name, reason, "unreadable")
    return _Parsed(issues.problem(str(folder)), None, "", None, (), (), (), (), {}, ("folder", reason))


def parse_block(folder: Path, name: str, contract_store: ContractStore, *, internal_contracts=False,
                validators=None) -> _Parsed:
    """Load one block folder. Returns the block, or the problem that makes it unusable.

    `contract_store` is anything with `resolve(name, revision, digest)`: the live store's coherent snapshot,
    or the private snapshot resolver of a historical preview. `internal_contracts` and `validators` are
    keyword-only and set only by server code constructing its own assistance or synthesis capture."""
    folder = Path(folder)
    result = read_file(folder, FILE, BLOCK_FILE_LIMIT)
    file_name = str(folder / FILE)
    p = _Parse(folder, name, contract_store, internal_contracts=internal_contracts, validators=validators)
    p.fingerprints[FILE] = result.fingerprint
    if result.data is None:
        p.views.append(FileView(FILE, None, None, result.reason))
        p.issues.add(FILE, result.reason or "the file cannot be read", "unreadable")
        return _Parsed(p.issues.problem(file_name), None, "", None, tuple(p.views), (), (FILE,), (),
                       p.fingerprints, ("block.json", result.reason))
    block_digest = _sha256(result.data)
    text = result.data.decode("utf-8", errors="replace")
    p.files[FILE] = block_digest
    p.texts[FILE] = text[1:] if text.startswith("﻿") else text
    p.views.append(FileView(FILE, block_digest, p.texts[FILE], None))

    def finish(description: str = "", kind: str | None = None, block: Block | None = None,
               problem: BlockProblem | None = None) -> _Parsed:
        content = (tuple(sorted(p.files.items())),
                   tuple((rel, p.fingerprints.get(rel, ())) for rel in p.paths if rel not in p.files),
                   problem.code if problem else None, problem.total if problem else 0)
        return _Parsed(problem, block, description, kind, tuple(p.views), tuple(p.notes), tuple(p.paths),
                       tuple(p.contract_pins), dict(p.fingerprints), content)

    try:
        obj = jsonio.decode_file_exact(result.data)
    except jsonio.JSONSyntax as exc:
        p.issues.add(f"line {exc.line}, column {exc.column}", "the file is not valid JSON")
        return finish(problem=p.issues.problem(file_name))
    except ValueError as exc:
        p.issues.add("block.json", str(exc))
        return finish(problem=p.issues.problem(file_name))
    # format_version first: a newer block is not read further
    version = obj.get("format_version")
    if "format_version" not in obj:
        p.issues.add("format_version", "is required")
    elif not _is_int(version) or version < 1:
        p.issues.add("format_version", "must be an integer of at least 1")
    elif version > WORKFLOW_FORMAT_VERSION:
        p.issues.add("format_version", f"is greater than {WORKFLOW_FORMAT_VERSION}: written for a newer AgentWiz")
        problem = p.issues.problem(file_name)
        return finish(problem=BlockProblem(problem.file, "newer_format", problem.issues, problem.total))
    elif version == 2:
        p.issues.add("format_version", "2 is a Workshop managed head, not an executable definition")
        return finish(problem=p.issues.problem(file_name))
    elif version < FORMAT_VERSION:
        p.issues.add("format_version", f"executable format {version} is no longer run or published: rewrite it as "
                     f"format_version {FORMAT_VERSION} with the new contract definitions; it is never converted "
                     "automatically", "migration_required")
        return finish(problem=p.issues.problem(file_name))
    p.version = version
    context_local_only = obj.get("context_local_only", False)
    if version == WORKFLOW_FORMAT_VERSION and type(context_local_only) is not bool:
        p.issues.add("context_local_only", "must be a boolean")
    for key in obj:
        if key not in _TOP_KEYS and not (key == "context_local_only" and version == WORKFLOW_FORMAT_VERSION):
            p.issues.add(key, "is not a key of a block file")
    block_name = obj.get("name")
    if "name" not in obj:
        p.issues.add("name", "is required")
    elif not valid_name(block_name):
        p.issues.add("name", f"must follow the name rule ({NAME_RULE})")
    elif block_name != name:
        p.issues.add("name", "must equal the folder's name")
    description = obj.get("description", "")
    if "description" in obj and not isinstance(description, str):
        p.issues.add("description", "must be a string")
        description = ""
    budget = Budget()
    if "budget" in obj:
        raw_budget = obj["budget"]
        if not isinstance(raw_budget, Mapping):
            p.issues.add("budget", "must be an object")
        else:
            seconds, tokens = DEFAULT_MAX_SECONDS, None
            for key in raw_budget:
                if key not in ("max_seconds", "max_model_tokens"):
                    p.issues.add(f"budget.{key}", "is not a budget setting")
            if "max_seconds" in raw_budget:
                value = raw_budget["max_seconds"]
                if not _is_int(value) or not 1 <= value <= 86400:
                    p.issues.add("budget.max_seconds", "must be an integer from 1 to 86400")
                else:
                    seconds = value
            if "max_model_tokens" in raw_budget:
                value = raw_budget["max_model_tokens"]
                if value is not None and (not _is_int(value) or value < 1):
                    p.issues.add("budget.max_model_tokens", "must be an integer of at least 1, or absent")
                else:
                    tokens = value
            budget = Budget(seconds, tokens)
    if "inputs" not in obj:
        p.issues.add("inputs", "is required: an object, which may be empty")
        inputs: tuple[Declared, ...] = ()
    else:
        inputs = p.declarations("inputs", obj["inputs"], 0, INPUT_LIMIT)
    p.declared_inputs = {d.name for d in inputs}
    if isinstance(obj.get("inputs"), Mapping):
        p.declared_inputs |= {k for k in obj["inputs"] if IDENT.match(k)}
    if "outputs" not in obj:
        p.issues.add("outputs", "is required: an object with at least one output")
        outputs: tuple[Declared, ...] = ()
    else:
        outputs = p.declarations("outputs", obj["outputs"], 1, OUTPUT_LIMIT)
        if isinstance(obj["outputs"], Mapping):
            p.declared_outputs = [k for k in obj["outputs"] if IDENT.match(k)]
    step = _parse_step(p, obj["steps"], outputs) if "steps" in obj else None
    if "steps" not in obj:
        p.issues.add("steps", "is required: a list of exactly one step")
    kind = step.kind if step else None
    if p.issues:
        return finish(description if isinstance(description, str) else "", kind, None, p.issues.problem(file_name))
    assert step is not None
    block = Block(name, 0, description, inputs, outputs, step, budget, dict(p.files), dict(p.texts),
                  {k: dict(v) for k, v in p.captured.items()}, tuple(p.notes), version, context_local_only)
    return finish(description, kind, block, None)


# --- the store (8.8) ---------------------------------------------------------------------------------------
@dataclass
class _Seen:
    """What the process remembers of a block folder, for every name seen (not only cached ones)."""
    revision: int
    sig: tuple
    content: tuple
    paths: tuple[str, ...]
    contract_pins: tuple[Reference, ...]


class BlockStore:
    def __init__(self, workspace_path: Path, contract_store: ContractStore):
        self.path = Path(workspace_path) / DIRECTORY
        self._contracts = contract_store
        self._cache: "OrderedDict[str, Loaded]" = OrderedDict()
        self._table: dict[str, _Seen] = {}
        self.workshop = None

    # --- folders ---
    def _folder_state(self, name: str) -> str:
        """"missing", "link", "dir" or "none" (a folder without block.json, which is not a block)."""
        folder = self.path / name
        try:
            st = os.lstat(folder)
        except OSError:
            return "missing"
        if stat.S_ISLNK(st.st_mode):
            return "link"
        if not stat.S_ISDIR(st.st_mode):
            return "missing"
        try:
            os.lstat(folder / FILE)
        except OSError:
            return "none"
        return "dir"

    def _contract_sig(self, pins: tuple[Reference, ...]) -> tuple:
        """Exact pins and each pinned revision's state; never the container's latest or a process counter."""
        return tuple((pin.name, pin.revision, pin.digest, self._contracts.pin_state(pin.name, pin.revision, pin.digest))
                     for pin in pins)

    def _signature(self, name: str, seen: _Seen) -> tuple:
        folder = self.path / name
        fingerprints = tuple(_fingerprint(folder, rel) for rel in seen.paths)
        return (fingerprints, self._contract_sig(seen.contract_pins))

    def _forget(self, name: str) -> None:
        self._table.pop(name, None)
        self._cache.pop(name, None)

    def _remember(self, name: str, loaded: Loaded) -> None:
        self._cache[name] = loaded
        self._cache.move_to_end(name)
        while len(self._cache) > CACHE_LIMIT:
            self._cache.popitem(last=False)

    def _load(self, name: str, state: str, seen: _Seen | None) -> Loaded:
        folder = self.path / name
        release = None
        metadata = {}
        if self.workshop is not None and state != "link":
            try:
                from .workshop_assets import head, document, validate_generation
                if head(folder) is not None:
                    folder, release = self.workshop.pin(name)
                    metadata = document(folder, 'workshop.json')
                    validate_generation(folder, metadata)
                    if metadata.get('format_version') != 1:
                        from .workshop_assets import fail
                        fail('newer_format', 409)
            except Exception:
                if release:
                    release()
                    release = None
                parsed = _folder_problem(folder, name, "managed revision is unavailable or unsupported")
            else:
                parsed = parse_block(folder, name, self._contracts.snapshot())
        elif state == "link":
            parsed = _folder_problem(folder, name, "a symbolic link is not followed")
        else:
            parsed = parse_block(folder, name, self._contracts.snapshot())
        contract_pins = parsed.contract_pins
        content = (parsed.content, self._contract_sig(contract_pins))
        if seen is not None and seen.content == content:
            revision = seen.revision
        else:
            revision = _next_revision()
        block = dataclasses.replace(parsed.block, revision=revision) if parsed.block is not None else None
        fingerprints = tuple(parsed.fingerprints.get(rel, _fingerprint(folder, rel)) for rel in parsed.paths)
        sig = (fingerprints, self._contract_sig(contract_pins))
        loaded = Loaded(name, revision, block, parsed.problem, parsed.description, parsed.kind, parsed.views,
                        parsed.notes, parsed.paths, contract_pins, sig, content, metadata, folder)
        if release:
            weakref.finalize(loaded, release)
        self._table[name] = _Seen(revision, sig, content, parsed.paths, contract_pins)
        self._remember(name, loaded)
        return loaded

    def get(self, name: str, reload: bool = False) -> Loaded | None:
        """The block `name` as it is now, or None when there is no such block (no folder, or a folder with no
        `block.json`). Reads file bytes only when the block's fingerprint changed (or `reload`)."""
        if not isinstance(name, str) or NAME_RE.match(name) is None:
            return None
        self._contracts.rescan()
        return self._ensure(name, reload)

    def _ensure(self, name: str, reload: bool) -> Loaded | None:
        state = self._folder_state(name)
        if state in ("missing", "none"):
            self._forget(name)
            return None
        seen = self._table.get(name)
        if self.workshop is not None:
            reload = True
        if seen is not None and not reload and state == "dir" and self._signature(name, seen) == seen.sig:
            cached = self._cache.get(name)
            if cached is not None:
                self._cache.move_to_end(name)
                return cached
        return self._load(name, state, seen)

    # --- listing ---
    def names(self) -> list[str]:
        """The names of the folders that are blocks (a folder with block.json, or a linked folder), sorted."""
        try:
            with os.scandir(self.path) as folder:
                found = [item.name for item in folder if NAME_RE.match(item.name) is not None]
        except OSError:
            return []
        out = []
        for name in sorted(found):
            if self._folder_state(name) in ("dir", "link"):
                out.append(name)
        return out

    def listing(self, reload: bool = False) -> dict:
        """The first LIST_LIMIT blocks (rows, with their problems), `shown` and `total`."""
        self._contracts.rescan()
        names = self.names()
        for gone in [n for n in self._table if n not in names]:
            self._forget(gone)
        rows = []
        for name in names[:LIST_LIMIT]:
            loaded = self._ensure(name, reload)
            if loaded is not None:
                rows.append(loaded.list_row())
        return {"blocks": rows, "shown": len(rows), "total": len(names)}


# --- the detail view (15.3) ---------------------------------------------------------------------------------
def _declared_view(block: Block, section: str, declared: Declared, with_form: bool) -> dict:
    copy_ = block.contracts[section].get(declared.name)
    parsed = declared.contract if declared.contract is not None else (copy_.parsed if copy_ is not None else None)
    row = {"name": declared.name, "description": declared.description,
           "format": parsed.format if parsed is not None else None,
           "contract_text": copy_.text if copy_ is not None else None,
           "reference": declared.reference.view() if declared.reference is not None else None,
           "digest": copy_.digest if copy_ is not None else None}
    if block.format_version == WORKFLOW_FORMAT_VERSION:
        row["local_only"] = declared.local_only
    if with_form:
        form, notes = (tool_forms.form_spec(parsed.schema)
                       if parsed is not None and parsed.format == "json" and parsed.schema is not None
                       else (None, []))
        row["form"] = form
        row["form_notes"] = notes
    else:
        del row["description"]
    return row


def _step_view(step: Step) -> dict:
    out: dict = {"id": step.id, "kind": step.kind}
    if step.kind == "model":
        out.update(role=step.role, max_repairs=step.max_repairs, response_mode=step.response_mode,
                   task_resets=list(step.task_resets))
    elif step.kind == "tool":
        out.update(server=step.server, tool=step.tool)
    elif step.kind == "transform":
        out.update(operation=step.raw["operation"], operation_version=step.raw["operation_version"])
    else:
        out.update(file=step.script_file, timeout_s=step.timeout_s)
    return out


def detail(loaded: Loaded) -> dict:
    """The `GET /api/blocks/<name>` body: the files that could be read safely (even for a block with a problem),
    the contracts as exact text, the declarations and the step (15.3)."""
    out = {"name": loaded.name, "revision": loaded.revision,
           "problem": loaded.problem.view() if loaded.problem else None,
           "description": loaded.description, "files": [v.view() for v in loaded.views],
           "contracts": {"inputs": {}, "outputs": {}}, "inputs": [], "outputs": [], "step": None, "budget": None,
           "notes": list(loaded.notes)}
    if loaded.workshop:
        out['authoring'] = {'display_name': loaded.workshop.get('display_name', loaded.name),
                            'metadata_version': loaded.workshop.get('format_version'),
                            'revisions_url': '/api/workshop/blocks/' + loaded.name + '/revisions'}
    block = loaded.block
    if block is not None:
        out["contracts"] = {section: {name: copy_.view() for name, copy_ in block.contracts[section].items()}
                            for section in ("inputs", "outputs")}
        out["inputs"] = [_declared_view(block, "inputs", d, True) for d in block.inputs]
        out["outputs"] = [_declared_view(block, "outputs", d, False) for d in block.outputs]
        out["step"] = _step_view(block.step)
        out["budget"] = block.budget.view()
    return out
