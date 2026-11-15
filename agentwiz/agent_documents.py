"""The agentic Chat conversation: staging, the allowlist, search, read, drafts and memory (0.2.2, 6-7, 9).

There is one live conversation per process (F-27). Its documents and drafts are staged under
`<workspace>/agent_documents/<conversation>/`, opened through directory handles kept for the conversation's life:
every create uses `O_CREAT | O_EXCL | O_NOFOLLOW` relative to the folder's descriptor, every read opens by
descriptor with `O_NOFOLLOW` and verifies a regular file, and a staging root that is a link or has been replaced
since it was opened is refused. No built-in tool accepts a path; an ID resolves only through the active run's
allowlist, held in memory.

Memory (9) holds the user's message, the final answer, the drafts created in that run and the run's label. It is
never written to disk.

Takes no lock of AgentWiz's state; holds its own leaf lock, never during file I/O. Knows nothing of HTTP.
"""
from __future__ import annotations

import os
import re
import secrets
import stat
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from . import jsonio
from .agent_tools import ToolError

DIRECTORY = "agent_documents"
ID_RE = re.compile(r"(doc|draft)_[0-9a-f]{8}\Z")
FILE_RE = re.compile(r"(doc|draft)_[0-9a-f]{8}(\.tmp)?\Z")
CONVERSATION_RE = re.compile(r"conv_[0-9a-f]{8}\Z")
# Limits (7.3, F-25), all in bytes of UTF-8 text unless stated.
SOURCE_LIMIT = 16 << 20
RENDERED_LIMIT = 16 << 20
ITEM_LIMIT = 20
DRAFT_LIMIT = 10
STAGED_LIMIT = 64 << 20
MEMORY_LIMIT = 20
# Built-in tool bounds (6.2-6.4).
EXCERPT = 300
LINE_PIECE = 1000
SCAN_CHECK = 1000
DRAFT_CHARS = 200_000
DRAFT_NAME = 80


class StagingError(Exception):
    """A staging refusal with a fixed code: `limit` names the limit when one was reached."""

    def __init__(self, code: str, status: int = 409, limit: str | None = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.limit = limit


@dataclass
class Item:
    id: str
    kind: str                 # "document" | "draft"
    name: str                 # the user's file name, or a draft's suggested download name (metadata only)
    format: str               # txt | md | csv | json for a document; md | txt for a draft
    lines: int
    characters: int
    size: int                 # UTF-8 bytes staged
    local_only: bool
    created_by: str | None = None   # the run that created a draft


@dataclass
class MemoryEntry:
    question: str
    answer: str
    drafts: list              # [(draft_id, name)]
    restricted: bool


def split_lines(text: str) -> list[str]:
    """The document's own lines: split on newline, a trailing carriage return dropped, no final empty line."""
    if text == "":
        return []
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def encoded_size(value: Any) -> int:
    return len(jsonio.encode_exact(value).encode("utf-8"))


def safe_name(name: str, extension: str) -> str:
    """A safe base name plus the extension: never a path."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = re.sub(r"[^A-Za-z0-9._ -]+", "_", base).strip(" .") or "draft"
    stem = base[:-len(extension) - 1] if base.lower().endswith("." + extension) else base
    return (stem[:DRAFT_NAME] or "draft") + "." + extension


class Conversation:
    """One live conversation. Created by `Manager.start`; closed exactly once."""

    def __init__(self, workspace_path: Path, ident: str):
        self.id = ident
        self.lock = threading.Lock()
        self.items: "OrderedDict[str, Item]" = OrderedDict()
        self.memory_on = True                    # O-9
        self.memory: list[MemoryEntry] = []
        self.memory_cleared_reason: str | None = None
        self.closed = False
        self.active_run: Any = None              # agent_loop.AgentRun while one runs
        self._fds: list[int] = []
        self._ids: set[str] = set()
        self._open(Path(workspace_path))

    # --- directory handles (7.4) ---------------------------------------------------------------------------
    def _open(self, workspace: Path) -> None:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            ws = os.open(str(workspace), flags)
            self._fds.append(ws)
            self.root_fd = self._child(ws, DIRECTORY)
            self.fd = self._child(self.root_fd, self.id)
        except BaseException:
            self._close_fds()
            raise

    def _child(self, parent: int, name: str) -> int:
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
        except FileExistsError:
            pass
        try:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        except OSError:
            raise StagingError("staging_unavailable") from None
        self._fds.append(fd)
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode):
            raise StagingError("staging_unavailable")
        return fd

    def check_root(self) -> None:
        """Refuse a staging root that is now a link or was replaced since it was opened."""
        pairs = ((self._fds[0], DIRECTORY, self.root_fd), (self.root_fd, self.id, self.fd))
        for parent, name, held in pairs:
            try:
                now = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except OSError:
                raise StagingError("staging_replaced") from None
            mine = os.fstat(held)
            if stat.S_ISLNK(now.st_mode) or (now.st_dev, now.st_ino) != (mine.st_dev, mine.st_ino):
                raise StagingError("staging_replaced")

    def _close_fds(self) -> None:
        for fd in reversed(self._fds):
            try:
                os.close(fd)
            except OSError:
                pass
        self._fds = []

    # --- staging (7.2) -------------------------------------------------------------------------------------
    def new_id(self, kind: str) -> str:
        prefix = "doc_" if kind == "document" else "draft_"
        while True:
            ident = prefix + secrets.token_hex(4)
            with self.lock:
                if ident not in self._ids:
                    self._ids.add(ident)
                    return ident

    def _totals(self) -> tuple[int, int, int]:
        drafts = sum(1 for i in self.items.values() if i.kind == "draft")
        return len(self.items), drafts, sum(i.size for i in self.items.values())

    def admit(self, kind: str, size: int) -> None:
        """Check the conversation limits for one more item of `size` bytes (7.3). Raises StagingError."""
        with self.lock:
            if self.closed:
                raise StagingError("conversation_closed")
            count, drafts, staged = self._totals()
        if count >= ITEM_LIMIT:
            raise StagingError("limit", 409, "documents_and_drafts")
        if kind == "draft" and drafts >= DRAFT_LIMIT:
            raise StagingError("limit", 409, "drafts")
        if staged + size > STAGED_LIMIT:
            raise StagingError("limit", 409, "staged_bytes")

    def _write_new(self, name: str, data: bytes) -> None:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)

    def stage(self, filename: str, fmt: str, text: str, local_only: bool) -> Item:
        """Stage one rendered document under a new ID. Raises StagingError."""
        data = text.encode("utf-8")
        if len(data) > RENDERED_LIMIT:
            raise StagingError("limit", 413, "rendered_text")
        self.admit("document", len(data))
        self.check_root()
        ident = self.new_id("document")
        self._write_new(ident, data)
        item = Item(ident, "document", filename, fmt, len(split_lines(text)), len(text), len(data), local_only)
        with self.lock:
            if self.closed:
                raise StagingError("conversation_closed")
            self.items[ident] = item
        return item

    def create_draft(self, run_id: str, name: str, fmt: str, content: str, restricted: bool,
                     still_running: Callable[[], bool]) -> Item:
        """A new draft, written under a temporary name and renamed into place (6.4). `still_running()` is
        checked before the commit, so a stopped run leaves no partial draft. Raises ToolError."""
        data = content.encode("utf-8")
        try:
            self.admit("draft", len(data))
        except StagingError as exc:
            raise ToolError({"documents_and_drafts": "document_limit", "drafts": "draft_limit",
                             "staged_bytes": "staged_limit"}.get(exc.limit or "", "document_limit")) from None
        try:
            self.check_root()
        except StagingError:
            raise ToolError("staging_unavailable") from None
        ident = self.new_id("draft")
        temporary = ident + ".tmp"
        self._write_new(temporary, data)
        try:
            if not still_running():
                raise ToolError("stopped")
            os.rename(temporary, ident, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        except BaseException:
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except OSError:
                pass
            raise
        item = Item(ident, "draft", safe_name(name, fmt), fmt, len(split_lines(content)), len(content), len(data),
                    restricted, run_id)
        with self.lock:
            self.items[ident] = item
        return item

    def read_text(self, ident: str) -> str:
        """The staged text of an allowlisted item, opened by descriptor with no link followed."""
        self.check_root()
        fd = os.open(ident, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_size > STAGED_LIMIT:
                raise StagingError("staging_unavailable")
            chunks = []
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(fd)
        return b"".join(chunks).decode("utf-8")

    def item(self, ident: object) -> Item | None:
        if not isinstance(ident, str) or ID_RE.match(ident) is None:
            return None
        with self.lock:
            return self.items.get(ident)

    def items_view(self) -> list[dict]:
        with self.lock:
            return [{"id": i.id, "name": i.name, "kind": i.kind, "format": i.format, "lines": i.lines,
                     "characters": i.characters, "local_only": i.local_only} for i in self.items.values()]

    # --- labels (8.1) -----------------------------------------------------------------------------------------
    def set_label(self, ident: str, local_only: bool) -> bool:
        """Change a label. Tightening clears every retained memory entry; returns whether memory was cleared."""
        with self.lock:
            item = self.items.get(ident)
            if item is None:
                raise StagingError("unknown_document", 404)
            tightened = local_only and not item.local_only
            item.local_only = local_only
            cleared = tightened and bool(self.memory)
            if tightened:
                self.memory = []
                self.memory_cleared_reason = "label_tightened" if cleared else self.memory_cleared_reason
            return cleared

    def remove(self, ident: str) -> None:
        with self.lock:
            item = self.items.pop(ident, None)
        if item is None:
            raise StagingError("unknown_document", 404)
        try:
            os.unlink(ident, dir_fd=self.fd)
        except OSError:
            pass

    # --- memory (9) -------------------------------------------------------------------------------------------
    def memory_view(self) -> dict:
        with self.lock:
            return {"on": self.memory_on, "entries": len(self.memory), "limit": MEMORY_LIMIT,
                    "restricted": any(e.restricted for e in self.memory), "cleared_reason": self.memory_cleared_reason}

    def add_memory(self, entry: MemoryEntry) -> None:
        with self.lock:
            if self.memory_on and len(self.memory) < MEMORY_LIMIT:
                self.memory.append(entry)

    def memory_entries(self) -> list[MemoryEntry]:
        with self.lock:
            return list(self.memory) if self.memory_on else []

    # --- close (7.5) -------------------------------------------------------------------------------------------
    def close(self) -> list[str]:
        """Remove the conversation's app-owned files and folder. Returns names left in place (reported)."""
        with self.lock:
            if self.closed:
                return []
            self.closed = True
            self.items.clear()
            self.memory = []
        left = []
        try:
            for name in os.listdir(self.fd):
                if FILE_RE.match(name):
                    try:
                        os.unlink(name, dir_fd=self.fd)
                    except OSError:
                        left.append(name)
                else:
                    left.append(name)
            if not left:
                os.rmdir(self.id, dir_fd=self.root_fd)
        except OSError:
            pass
        finally:
            self._close_fds()
        return left


def sweep(workspace_path: Path) -> list[str]:
    """Remove app-owned entries under `agent_documents/` (startup and workspace reset). Returns what was left."""
    left: list[str] = []
    root = Path(workspace_path) / DIRECTORY
    try:
        root_fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return left
    try:
        for name in os.listdir(root_fd):
            if not CONVERSATION_RE.match(name):
                left.append(name)
                continue
            try:
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            except OSError:
                left.append(name)
                continue
            try:
                kept = False
                for entry in os.listdir(fd):
                    if FILE_RE.match(entry):
                        try:
                            os.unlink(entry, dir_fd=fd)
                        except OSError:
                            kept = True
                    else:
                        kept = True
                        left.append(f"{name}/{entry}")
            finally:
                os.close(fd)
            if not kept:
                try:
                    os.rmdir(name, dir_fd=root_fd)
                except OSError:
                    left.append(name)
    finally:
        os.close(root_fd)
    return left


# --- the document tools (6.1-6.3) ---------------------------------------------------------------------------------
class Scope:
    """The documents and drafts one run may use: its allowlist (7.4). `inspected` collects every item a list,
    search or read looked at, for its label (8.1)."""

    def __init__(self, conversation: Conversation, allowed: Iterable[str]):
        self.conversation = conversation
        self.allowed = list(dict.fromkeys(allowed))

    def add(self, ident: str) -> None:
        if ident not in self.allowed:
            self.allowed.append(ident)

    def resolve(self, ident: object) -> Item:
        item = self.conversation.item(ident)
        if item is None or item.id not in self.allowed:
            raise ToolError("unknown_document")
        return item

    def items(self) -> list[Item]:
        return [i for i in (self.conversation.item(ident) for ident in self.allowed) if i is not None]


def list_documents(scope: Scope) -> tuple[dict, list[Item]]:
    items = scope.items()
    return ({"documents": [{"id": i.id, "name": i.name, "kind": i.kind, "format": i.format, "lines": i.lines,
                            "characters": i.characters} for i in items]}, items)


def _excerpt(line: str, start: int, length: int) -> str:
    if len(line) <= EXCERPT:
        return line
    left = max(0, min(start - (EXCERPT - length) // 2, len(line) - EXCERPT))
    return line[left:left + EXCERPT]


def search_documents(scope: Scope, arguments: dict, budget: int, check: Callable[[], None]) -> tuple[dict, list[Item]]:
    """Case-insensitive literal search in registry order, one hit per matching line (6.2). The response is
    bounded by `budget` encoded bytes. `check()` raises to stop the scan; it runs every SCAN_CHECK lines."""
    query = arguments["query"]
    max_hits = arguments.get("max_hits", 10)
    skip = arguments.get("skip", 0)
    if "document_id" in arguments:
        targets = [scope.resolve(arguments["document_id"])]
    else:
        targets = scope.items()
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    response: dict = {"hits": [], "next_skip": skip, "more": False}
    found = 0
    scanned = 0
    for item in targets:
        text = scope.conversation.read_text(item.id)
        for number, line in enumerate(split_lines(text), 1):
            scanned += 1
            if scanned % SCAN_CHECK == 0:
                check()
            match = pattern.search(line)
            if match is None:
                continue
            found += 1
            if found <= skip:
                continue
            if len(response["hits"]) >= max_hits:
                response["more"] = True
                break
            hit = {"document_id": item.id, "line": number, "column": match.start() + 1,
                   "excerpt": _excerpt(line, match.start(), match.end() - match.start())}
            response["hits"].append(hit)
            if encoded_size(dict(response, next_skip=skip + len(response["hits"]))) > budget:
                response["hits"].pop()
                response["more"] = True
                break
        if response["more"]:
            break
    response["next_skip"] = skip + len(response["hits"])
    return response, targets


def read_document(scope: Scope, arguments: dict, budget: int) -> tuple[dict, list[Item]]:
    """Lines by the document's own numbers; long lines in pieces by column (6.3)."""
    item = scope.resolve(arguments["document_id"])
    start = arguments["start_line"]
    count = arguments.get("line_count", 40)
    column = arguments.get("start_column", 1)
    if column > 1 and count != 1:
        raise ToolError("invalid_position")
    lines = split_lines(scope.conversation.read_text(item.id))
    total = len(lines)
    if (total == 0 and start != 1) or (total and start > total):
        raise ToolError("invalid_position")
    response: dict = {"document_id": item.id, "total_lines": total, "lines": [], "more": False}
    number = start
    first = True
    while number <= total and len(response["lines"]) < count:
        line = lines[number - 1]
        offset = column - 1 if first else 0
        if offset and offset >= len(line):
            raise ToolError("invalid_position")
        piece = line[offset:offset + LINE_PIECE]
        row: dict = {"line": number, "text": piece}
        if offset:
            row["column"] = offset + 1
        if offset + LINE_PIECE < len(line):
            row["truncated"] = True
            row["next_column"] = offset + LINE_PIECE + 1
        response["lines"].append(row)
        if encoded_size(response) > budget:
            response["lines"].pop()
            # Cut inside the line when a piece alone does not fit; never send a gap or a repeat.
            if not response["lines"] and piece:
                low, high = 0, len(piece)
                while low < high:
                    middle = (low + high + 1) // 2
                    row["text"] = piece[:middle]
                    response["lines"] = [row]
                    if encoded_size({**response, "more": True, "next_line": number,
                                     "next_column": offset + middle + 1}) <= budget:
                        low = middle
                    else:
                        high = middle - 1
                    response["lines"] = []
                if low:
                    row["text"] = piece[:low]
                    row["truncated"] = True
                    row["next_column"] = offset + low + 1
                    response["lines"] = [row]
                    response.update(more=True, next_line=number, next_column=offset + low + 1)
                    return response, [item]
            response.update(more=True, next_line=number)
            if offset:
                response["next_column"] = offset + 1
            return response, [item]
        first = False
        number += 1
    if number <= total:
        response.update(more=True, next_line=number)
    return response, [item]
