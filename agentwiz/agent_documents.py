"""The agentic Chat conversation: staging, the allowlist, search, read, drafts and memory (0.2.2, 6-7, 9).

There is one live conversation per process (F-27). Its documents and drafts are staged under
`<workspace>/agent_documents/<conversation>/`, opened through directory handles kept for the conversation's life:
every create uses `O_CREAT | O_EXCL | O_NOFOLLOW` relative to the folder's descriptor, every read opens by
descriptor with `O_NOFOLLOW` and verifies a regular file, and a staging root that is a link or has been replaced
since it was opened is refused. No built-in tool accepts a path; an ID resolves only through the active run's
allowlist, held in memory.

Memory (9) holds the user's message, the final answer, the drafts created in that run and the run's label. It is
never written to disk.

Every staged item, upload or draft, is published in four steps (addendum 3.2): **reserve** (`begin_write`: an early
limit check and a pending-write count), **write** the bytes to the private name `<id>.tmp` outside any lock,
**commit** (one hold of the conversation lock that rechecks the limits, renames the file into place and registers
the item, so no file exists under a final name without its item) and **release** (`end_write`, in a `finally`).
`close()` sets `closed`, then waits for the pending writes before it deletes anything.

Takes no lock of AgentWiz's state. Its own lock is taken after the manager's lock, never before it; it is held for
the commit's rename and registration, never for a content write or read. Knows nothing of HTTP.
"""
from __future__ import annotations

import io
import os
import re
import secrets
import stat
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from . import agent_tools, jsonio
from .agent_tools import ToolError, join_lines, split_lines

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
    format: str               # an attachments.FORMATS extension for a document; md | txt for a draft
    lines: int
    characters: int
    size: int                 # UTF-8 bytes staged
    local_only: bool
    created_by: str | None = None   # the run that created a draft
    coverage: dict | None = None    # a PDF's bounded coverage record (addendum 4.2)


@dataclass
class MemoryEntry:
    question: str
    answer: str
    drafts: list              # [(draft_id, name)]
    restricted: bool


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
        self.private = False
        self.chat_save = False
        self.chat_save_error = None
        self.chat_turns = []
        self.chat_model = None
        self.chat_totals = None
        self.lock = threading.Lock()
        self.drained = threading.Condition(self.lock)
        self.pending_writes = 0
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

    def _check_limits_locked(self, kind: str, size: int) -> None:
        """The conversation limits for one more item of `size` bytes against the registered items (7.3)."""
        count, drafts, staged = self._totals()
        if count >= ITEM_LIMIT:
            raise StagingError("limit", 409, "documents_and_drafts")
        if kind == "draft" and drafts >= DRAFT_LIMIT:
            raise StagingError("limit", 409, "drafts")
        if staged + size > STAGED_LIMIT:
            raise StagingError("limit", 409, "staged_bytes")

    # --- publication (addendum 3.2) -----------------------------------------------------------------------
    def begin_write(self, kind: str, size: int) -> str:
        """Step 1, reserve: refuse a closed conversation, run the limit checks as an early refusal and count one
        pending write. Returns the new item's ID. The caller must call `end_write()` in a `finally`."""
        with self.lock:
            if self.closed:
                raise StagingError("conversation_closed")
            self._check_limits_locked(kind, size)
            self.pending_writes += 1
        try:
            return self.new_id(kind)
        except BaseException:
            self.end_write()
            raise

    def write_temporary(self, ident: str, data: bytes) -> None:
        """Step 2, write: the bytes under the private name `<id>.tmp`, outside any lock."""
        self.check_root()
        self._write_new(ident + ".tmp", data)

    def commit(self, item: Item, still_running: Callable[[], bool] | None = None) -> Item:
        """Step 3, commit, in one hold of the lock: the conversation is not closed, the limits hold against the
        registered items, a draft's run is still running; then the rename and the registration. On any refusal
        the temporary file is removed and the usual code raised."""
        temporary = item.id + ".tmp"
        with self.lock:
            try:
                if self.closed:
                    raise StagingError("conversation_closed")
                self._check_limits_locked(item.kind, item.size)
                if still_running is not None and not still_running():
                    raise ToolError("stopped")
                os.rename(temporary, item.id, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            except BaseException:
                self.discard(item.id)
                raise
            self.items[item.id] = item
        return item

    def discard(self, ident: str) -> None:
        """Remove a temporary file that will not be committed. Never raises."""
        try:
            os.unlink(ident + ".tmp", dir_fd=self.fd)
        except OSError:
            pass

    def end_write(self) -> None:
        """Step 4, release."""
        with self.lock:
            self.pending_writes -= 1
            self.drained.notify_all()

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

    def prepare_stage(self, filename: str, fmt: str, text: str, local_only: bool,
                      coverage: dict | None = None) -> Item:
        """Steps 1 and 2 for one rendered document: reserve and write. Returns the item to commit; the caller
        commits it (inside the manager's `while_idle`), discards it on any failure, and calls `end_write()`.
        Raises StagingError, with nothing reserved."""
        data = text.encode("utf-8")
        if len(data) > RENDERED_LIMIT:
            raise StagingError("limit", 413, "rendered_text")
        ident = self.begin_write("document", len(data))
        try:
            self.write_temporary(ident, data)
        except BaseException:
            self.discard(ident)
            self.end_write()
            raise
        return Item(ident, "document", filename, fmt, len(split_lines(text)), len(text), len(data), local_only,
                    coverage=coverage)

    def stage(self, filename: str, fmt: str, text: str, local_only: bool, coverage: dict | None = None) -> Item:
        """Stage one rendered document under a new ID through the four steps. Raises StagingError."""
        item = self.prepare_stage(filename, fmt, text, local_only, coverage)
        try:
            return self.commit(item)
        finally:
            self.end_write()

    def create_draft(self, run_id: str, name: str, fmt: str, content: str, restricted: bool,
                     still_running: Callable[[], bool]) -> Item:
        """A new draft through the four steps (6.4; addendum 3.2). `still_running()` is checked inside the commit,
        so a stopped run leaves no draft and no temporary file. Raises ToolError."""
        data = content.encode("utf-8")
        try:
            ident = self.begin_write("draft", len(data))
        except StagingError as exc:
            raise _draft_error(exc) from None
        try:
            try:
                self.write_temporary(ident, data)
            except StagingError:
                self.discard(ident)
                raise ToolError("staging_unavailable") from None
            except BaseException:
                self.discard(ident)
                raise
            item = Item(ident, "draft", safe_name(name, fmt), fmt, len(split_lines(content)), len(content),
                        len(data), restricted, run_id)
            try:
                return self.commit(item, still_running)
            except StagingError as exc:
                raise _draft_error(exc) from None
        finally:
            self.end_write()

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

    def open_text(self, ident: str):
        """The staged text of an allowlisted item as a stream (newline="", so line endings stay as staged), opened
        by descriptor with no link followed. For an incremental reader; the caller closes it."""
        self.check_root()
        fd = os.open(ident, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_size > STAGED_LIMIT:
                raise StagingError("staging_unavailable")
            return io.TextIOWrapper(io.FileIO(fd, closefd=True), encoding="utf-8", newline="")
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            raise

    def item(self, ident: object) -> Item | None:
        if not isinstance(ident, str) or ID_RE.match(ident) is None:
            return None
        with self.lock:
            return self.items.get(ident)

    def items_view(self) -> list[dict]:
        with self.lock:
            return [{"id": i.id, "name": i.name, "kind": i.kind, "format": i.format, "lines": i.lines,
                     "characters": i.characters, "local_only": i.local_only,
                     **({"coverage": i.coverage} if i.coverage is not None else {})} for i in self.items.values()]

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

    def unregister(self, ident: str) -> Item:
        """Take an item out of the registry (the decision; `unlink` removes its file afterwards)."""
        with self.lock:
            item = self.items.pop(ident, None)
        if item is None:
            raise StagingError("unknown_document", 404)
        return item

    def unlink(self, item: Item) -> None:
        try:
            os.unlink(item.id, dir_fd=self.fd)
        except OSError:
            pass

    def remove(self, ident: str) -> None:
        self.unlink(self.unregister(ident))

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
        """Remove the conversation's app-owned files and folder. Returns names left in place (reported).

        Sets `closed` under the lock, then waits for every pending write to release before it lists, deletes or
        closes anything: a writer that reaches its commit after this is refused and removes its temporary file.
        The wait is short, because a pending write is a bounded local write (extraction finishes before it)."""
        with self.lock:
            if self.closed:
                return []
            self.closed = True
            self.items.clear()
            self.memory = []
            while self.pending_writes > 0:
                self.drained.wait()
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


def _draft_error(exc: StagingError) -> ToolError:
    if exc.code == "conversation_closed":
        return ToolError("stopped")
    if exc.code != "limit":
        return ToolError("staging_unavailable")
    return ToolError({"documents_and_drafts": "document_limit", "drafts": "draft_limit",
                      "staged_bytes": "staged_limit"}.get(exc.limit or "", "document_limit"))


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
    # A permitted PDF item carries its coverage, so the agent can see that a document is partial (addendum 4.2).
    return ({"documents": [{"id": i.id, "name": i.name, "kind": i.kind, "format": i.format, "lines": i.lines,
                            "characters": i.characters, **({"coverage": i.coverage} if i.coverage else {})}
                           for i in items]}, items)


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


# --- the addendum's document tools (3.1-3.5) -------------------------------------------------------------------------
TABLE_DELIMITERS = {"csv": ",", "tsv": "\t"}
TABLE_ROWS_DEFAULT = 50


def draft_inputs(scope: Scope, tool: str, arguments: dict) -> list[Item]:
    """The items a draft-producing call reads, resolved before anything is read (3.1): the source, plus the base
    when `append_to` is given."""
    items = [scope.resolve(arguments["source_id"])]
    if tool == "copy_to_draft" and "append_to" in arguments:
        base = scope.resolve(arguments["append_to"])
        if base.kind != "draft":
            raise ToolError("invalid_arguments", message="append_to must name a draft")
        items.append(base)
    return items


def _too_large_draft(characters: int) -> ToolError:
    return ToolError("too_large", message=f"the draft would have {characters} characters; the limit is "
                                          f"{DRAFT_CHARS}")


def copy_content(scope: Scope, arguments: dict, items: list[Item]) -> tuple[str, int]:
    """copy_to_draft (3.3): lines `start_line`..`end_line` of the staged text, verbatim, joined as in 2.1; with
    `append_to`, after the base draft's content (and one newline if it does not end with one)."""
    start, end = arguments["start_line"], arguments["end_line"]
    lines = split_lines(scope.conversation.read_text(items[0].id))
    if not start <= end <= len(lines):
        raise ToolError("invalid_position")
    copied = join_lines(lines[start - 1:end])
    if len(items) == 2:
        base = scope.conversation.read_text(items[1].id)
        if base and not base.endswith("\n"):
            base += "\n"
        copied = base + copied
    if len(copied) > DRAFT_CHARS:
        raise _too_large_draft(len(copied))
    return copied, end - start + 1


def revise_content(scope: Scope, arguments: dict, source: Item) -> tuple[str, list[int]]:
    """revise_draft (3.3): literal, case-sensitive edits in order, each on the result of those before it. Any edit
    whose actual count differs from its expected_count (default 1) refuses the whole call as `edit_mismatch`."""
    text = scope.conversation.read_text(source.id)
    ceiling = max(len(text), DRAFT_CHARS)
    counts = []
    for number, edit in enumerate(arguments["edits"], 1):
        find, replace = edit["find"], edit["replace"]
        expected = edit.get("expected_count", 1)
        count = text.count(find)
        if count != expected:
            raise ToolError("edit_mismatch", number, f"edit {number} matched {count} times; expected {expected}")
        if len(text) + count * (len(replace) - len(find)) > ceiling:
            raise _too_large_draft(len(text) + count * (len(replace) - len(find)))
        text = text.replace(find, replace)
        counts.append(count)
    if len(text) > DRAFT_CHARS:
        raise _too_large_draft(len(text))
    return text, counts


def compare_documents(scope: Scope, arguments: dict, budget: int, check: Callable[[], None],
                      items: list[Item]) -> dict:
    """compare_texts on two staged texts with the Chat result limit (3.4)."""
    before = scope.conversation.read_text(items[0].id)
    after = scope.conversation.read_text(items[1].id)
    return agent_tools.compare(before, after, arguments.get("context_lines", 3), agent_tools.Bounds(budget, check),
                               "documents")


def read_table(scope: Scope, arguments: dict, budget: int, check: Callable[[], None], item: Item) -> dict:
    """Rows of a .csv or .tsv document by page (3.5). Reads the staged text incrementally and stops after the page
    plus one lookahead record; never parses past it. Rows are added while the encoded result fits `budget`."""
    if item.kind != "document" or item.format not in TABLE_DELIMITERS:
        raise ToolError("not_a_table")
    start = arguments.get("start_row", 1)
    page = arguments.get("max_rows", TABLE_ROWS_DEFAULT)
    bounds = agent_tools.Bounds(budget, check)
    stream = scope.conversation.open_text(item.id)
    try:
        reader = agent_tools.CSVReader(stream, TABLE_DELIMITERS[item.format])
        try:
            header = next(reader)
        except StopIteration:
            raise ToolError("invalid_position") from None
        columns = agent_tools.csv_header(header, reader.number, True)
        chosen = arguments.get("columns")
        if chosen is None:
            indices = list(range(len(columns)))
        else:
            if len(set(chosen)) != len(chosen) or any(name not in columns for name in chosen):
                raise ToolError("invalid_arguments", message="columns must name distinct existing columns")
            indices = [columns.index(name) for name in chosen]

        def record():
            try:
                found = next(reader)
            except StopIteration:
                return None
            bounds.tick()
            agent_tools.csv_check_record(found, len(columns), reader.number)
            return found
        for _ in range(start - 1):
            if record() is None:
                raise ToolError("invalid_position")
        selected = [columns[i] for i in indices]
        response: dict = {"document_id": item.id, "columns": selected, "rows": [], "start_row": start,
                          "next_row": start, "eof": False}
        # Room for the cursor fields at their largest, and for row_count.
        size = encoded_size(response) + 2 * 12 + len(',"row_count":') + 12
        rows, lookahead, exhausted = [], None, False
        for _ in range(page):
            found = record()
            if found is None:
                exhausted = True
                break
            row = [found[i] for i in indices]
            row_size = len(jsonio.encode_exact(row)) + 1
            if size + row_size > budget:
                if not rows:
                    raise ToolError("row_too_large", start, f"row {start} does not fit the result limit of {budget} "
                                                            "bytes; ask for fewer columns")
                lookahead = found                      # the record that did not fit is the lookahead
                break
            rows.append(row)
            size += row_size
        if not rows and exhausted:
            raise ToolError("invalid_position")
        if lookahead is None and not exhausted:
            lookahead = record()
            exhausted = lookahead is None
        response.update(rows=rows, next_row=start + len(rows), eof=exhausted)
        if exhausted:
            response["row_count"] = start - 1 + len(rows)
        return response
    finally:
        stream.close()
