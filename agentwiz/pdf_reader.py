"""The PDF reader, parent side (addendum 4.2): a reader under attachment interface version 1, and `Admission`,
the small manager that `state.pdf` holds.

Extraction runs in a short-lived worker process (`agentwiz.pdf_worker`), the only module that imports `pypdfium2`.
What the worker guarantees depends on the platform:

* every platform: a PDFium crash or hang costs one upload, not the AgentWiz process and the credentials and
  conversation it holds (the child is killed at a 30-second deadline);
* Linux and WSL2 only: a verified address-space limit also makes a runaway allocation cost one upload;
* macOS: no memory containment. macOS does not enforce `RLIMIT_AS`; a runaway allocation is bounded only by the
  deadline, so one worker runs at a time there.

The worker is not a sandbox on any platform: the child runs with the application user's OS privileges.

The parent never uses unbounded `communicate()`, parses the worker's JSON within its caps, checks the interface
shape and every limit, and builds every message itself. Nothing is written outside the worker's own temporary
directory, which is removed. Knows nothing of HTTP.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any

INTERFACE_VERSION = 1
FORMATS = ("pdf",)
SOURCE_LIMIT = 16 << 20            # on both upload routes, checked before the body is read
PAGE_LIMIT = 300
RENDERED_LIMIT = 16 << 20
DEADLINE_S = 30.0                  # wall clock, from launch
REAP_S = 2.0
STDOUT_LIMIT = 24 << 20
STDERR_LIMIT = 64 << 10
WAIT_S = 10.0                      # a waiter's longest wait for a slot
WAITERS = 4
COVERAGE_LIMIT = 300
WORKER_COMMAND = [sys.executable, "-I", "-m", "agentwiz.pdf_worker"]
WORKER_ENVIRONMENT = {"LC_ALL": "C.UTF-8"}
WARNING_CODES = ("no_text_layer", "page_skipped", "content_limited", "no_extractable_text")
LIMIT_NAMES = {"pages": f"the {PAGE_LIMIT}-page limit", "rendered_text": "the 16 MiB rendered-text limit"}

MESSAGES = {
    "password": "The PDF is password-protected. AgentWiz cannot open protected PDFs; save an unprotected copy.",
    "unreadable": "The PDF is damaged or is not a PDF.",
    "no_pages": "The PDF has no pages.",
    "timeout": f"Reading the PDF took longer than {int(DEADLINE_S)} seconds, so it was stopped.",
    "crashed": "The PDF reader stopped unexpectedly.",
    "oversized": "The PDF reader's output was larger than its limit.",
    "malformed": "The PDF reader returned a malformed result.",
    "not_installed": "PDF reading needs the pypdfium2 package, which is not installed.",
    "load_failed": "PDF reading is unavailable: pypdfium2 could not be loaded on this platform.",
    "launch_failed": "The PDF reader could not be started.",
    "no_text": "The PDF has no extractable text; it may be scanned. AgentWiz does not run OCR.",
    "busy": "Other PDF uploads are being read. Try again in a moment.",
    "shutting_down": "AgentWiz is shutting down.",
    "cancelled": "Reading the PDF was cancelled.",
}


class AttachmentError(Exception):
    """A reader failure identified by `code` (interface 6). `status` is the HTTP status a route answers with."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _error(code: str, reason: str, status: int = 400) -> AttachmentError:
    return AttachmentError(code, MESSAGES[reason], status)


# --- admission (4.2) --------------------------------------------------------------------------------------------------
@dataclass(eq=False)
class Slot:
    """One granted extraction slot: a record created under the manager's lock before any process starts."""
    admission: "Admission"
    cancelled: bool = False
    process: Any = None
    socket: Any = None                     # the upload's connection, so shutdown can interrupt its body read
    released: bool = False

    def check(self) -> None:
        """A cancellation checkpoint."""
        if self.cancelled:
            raise _error("extraction_cancelled", "cancelled", 503)

    def attach_socket(self, sock: Any) -> None:
        with self.admission.lock:
            self.socket = sock
            cancelled = self.cancelled
        if cancelled:
            _shutdown_read(sock)

    def release(self) -> None:
        self.admission.release(self)


def _shutdown_read(sock: Any) -> None:
    try:
        sock.shutdown(socket.SHUT_RD)
    except OSError:
        pass


def _kill(process: Any) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass


class Admission:
    """At most `slots` extractions at once (two on Linux and WSL2, one on macOS) and at most WAITERS waiting for a
    slot; a further request is refused at once, and a waiter after WAIT_S, as `extraction_busy`. Follows the
    `mcp_ops` pattern: `close()` refuses new requests and wakes every waiter with `shutting_down`;
    `cancel_all()` kills every running child and interrupts every body read still in progress."""

    def __init__(self, slots: int | None = None, waiters: int = WAITERS, wait_s: float = WAIT_S):
        self.slots = slots if slots is not None else (1 if sys.platform == "darwin" else 2)
        self.waiters = waiters
        self.wait_s = wait_s
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.records: set[Slot] = set()
        self.waiting = 0
        self.closed = False

    def acquire(self) -> Slot:
        deadline = time.monotonic() + self.wait_s
        with self.lock:
            if self.closed:
                raise _error("shutting_down", "shutting_down", 503)
            if len(self.records) >= self.slots:
                if self.waiting >= self.waiters:
                    raise _error("extraction_busy", "busy", 503)
                self.waiting += 1
                try:
                    while len(self.records) >= self.slots and not self.closed:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise _error("extraction_busy", "busy", 503)
                        self.condition.wait(remaining)
                finally:
                    self.waiting -= 1
                if self.closed:
                    raise _error("shutting_down", "shutting_down", 503)
            slot = Slot(self)
            self.records.add(slot)
            return slot

    def release(self, slot: Slot) -> None:
        with self.lock:
            if slot.released:
                return
            slot.released = True
            self.records.discard(slot)
            self.condition.notify_all()

    def close(self) -> None:
        with self.lock:
            self.closed = True
            for slot in self.records:
                slot.cancelled = True
            self.condition.notify_all()

    def cancel_all(self) -> None:
        with self.lock:
            records = list(self.records)
            for slot in records:
                slot.cancelled = True
        for slot in records:
            if slot.process is not None:
                _kill(slot.process)
            if slot.socket is not None:
                _shutdown_read(slot.socket)

    def busy(self) -> bool:
        with self.lock:
            return bool(self.records) or self.waiting > 0

    def view(self) -> dict:
        with self.lock:
            return {"running": len(self.records), "waiting": self.waiting, "slots": self.slots,
                    "closed": self.closed}


# --- the worker (4.2) -------------------------------------------------------------------------------------------------
def _reader(stream: Any, cap: int, out: dict, key: str, on_overflow) -> None:
    chunks, total = [], 0
    try:
        while True:
            chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > cap:
                out[key + "_overflow"] = True
                on_overflow()
                break
            chunks.append(chunk)
    except (OSError, ValueError):
        pass
    out[key] = b"".join(chunks)


def _writer(stream: Any, data: bytes) -> None:
    try:
        stream.write(data)
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def run_worker(data: bytes, slot: Slot | None = None) -> dict:
    """Launch the worker, feed it, read its capped output and reap it. Returns the parsed JSON object. Raises
    AttachmentError (extraction_failed, extraction_cancelled)."""
    if slot is not None:
        slot.check()
    folder = tempfile.mkdtemp(prefix="agentwiz-pdf-")
    process = None
    try:
        try:
            process = subprocess.Popen(list(WORKER_COMMAND), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, cwd=folder, env=dict(WORKER_ENVIRONMENT),
                                       close_fds=True, start_new_session=True)
        except OSError:
            raise _error("extraction_failed", "launch_failed") from None
        started = time.monotonic()
        if slot is not None:
            # Launch without a gap: store the process under the lock; a record cancelled meanwhile kills it now.
            with slot.admission.lock:
                slot.process = process
                cancelled = slot.cancelled
            if cancelled:
                _kill(process)
        output: dict = {}
        threads = [threading.Thread(target=_writer, args=(process.stdin, data), daemon=True),
                   threading.Thread(target=_reader, args=(process.stdout, STDOUT_LIMIT, output, "stdout",
                                                          lambda: _kill(process)), daemon=True),
                   threading.Thread(target=_reader, args=(process.stderr, STDERR_LIMIT, output, "stderr",
                                                          lambda: _kill(process)), daemon=True)]
        for thread in threads:
            thread.start()
        timed_out = False
        try:
            process.wait(timeout=max(0.0, DEADLINE_S - (time.monotonic() - started)))
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill(process)
        try:
            process.wait(timeout=REAP_S)
        except subprocess.TimeoutExpired:
            pass
        for thread in threads:
            thread.join(REAP_S)
        if slot is not None:
            slot.check()
        if timed_out:
            raise _error("extraction_failed", "timeout")
        if output.get("stdout_overflow") or output.get("stderr_overflow"):
            raise _error("extraction_failed", "oversized")
        if process.returncode != 0:
            raise _error("extraction_failed", "crashed")
        try:
            value = json.loads(output.get("stdout", b"").decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise _error("extraction_failed", "malformed") from None
        if not isinstance(value, dict):
            raise _error("extraction_failed", "malformed")
        return value
    finally:
        if process is not None:
            if process.poll() is None:
                _kill(process)
                try:
                    process.wait(timeout=REAP_S)
                except subprocess.TimeoutExpired:
                    pass
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass
        shutil.rmtree(folder, ignore_errors=True)


# --- result validation (4.2) ------------------------------------------------------------------------------------------
def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _malformed() -> AttachmentError:
    return _error("extraction_failed", "malformed")


def _warning(code: str, page: int | None, limit: str | None = None) -> dict:
    if code == "no_text_layer":
        message = f"Page {page} has no text layer; its content is missing."
    elif code == "page_skipped":
        message = f"Page {page} could not be read and is missing."
    elif code == "content_limited":
        message = f"Reading stopped at {LIMIT_NAMES[limit]}" + (f", after page {page}." if page else ".")
    else:
        message = MESSAGES["no_text"]
    return {"code": code, "message": message, "page": page}


def _normalise(text: str) -> str:
    from .pdf_worker import normalise
    return normalise(text)


def check_result(value: dict) -> dict:
    """The worker's result rebuilt from what checks out: the interface shape, every limit, and messages written
    here. Raises AttachmentError."""
    if "error" in value:
        code, reason = value.get("error"), value.get("reason")
        if code == "damaged_file" and reason in ("password", "unreadable", "no_pages"):
            raise _error("damaged_file", reason)
        if code == "pdf_unavailable" and reason in ("not_installed", "load_failed"):
            raise _error("pdf_unavailable", reason, 503)
        raise _malformed()
    if (set(value) != {"interface_version", "format", "page_count", "blocks", "warnings"}
            or value["interface_version"] != INTERFACE_VERSION or value["format"] != "pdf"):
        raise _malformed()
    page_count, blocks, warnings = value["page_count"], value["blocks"], value["warnings"]
    if not _is_int(page_count) or page_count < 1 or not isinstance(blocks, list) or not isinstance(warnings, list):
        raise _malformed()
    if len(blocks) > PAGE_LIMIT or len(warnings) > PAGE_LIMIT + 2:
        raise _malformed()
    out_blocks, previous, rendered = [], 0, 0
    for block in blocks:
        if (not isinstance(block, dict) or set(block) != {"type", "text", "page_start", "page_end"}
                or block["type"] != "text" or not isinstance(block["text"], str)
                or not _is_int(block["page_start"]) or block["page_start"] != block["page_end"]
                or not previous < block["page_start"] <= min(page_count, PAGE_LIMIT)):
            raise _malformed()
        text = _normalise(block["text"])
        previous = block["page_start"]
        rendered += len(f"--- page {previous} ---\n".encode("utf-8")) + len(text.encode("utf-8")) + 2
        if rendered > RENDERED_LIMIT or not text.strip():
            raise _malformed()
        out_blocks.append({"type": "text", "text": text, "page_start": previous, "page_end": previous})
    out_warnings = []
    for warning in warnings:
        if not isinstance(warning, dict) or warning.get("code") not in WARNING_CODES:
            raise _malformed()
        code, page = warning["code"], warning.get("page")
        if code == "no_extractable_text":
            if set(warning) != {"code", "page"} or page is not None or out_blocks:
                raise _malformed()
            out_warnings.append(_warning(code, None))
            continue
        if code == "content_limited":
            limit = warning.get("limit")
            if (set(warning) != {"code", "page", "limit"} or limit not in LIMIT_NAMES
                    or not (page is None or _is_int(page) and 1 <= page <= min(page_count, PAGE_LIMIT))):
                raise _malformed()
            out_warnings.append(_warning(code, page, limit))
            continue
        if set(warning) != {"code", "page"} or not _is_int(page) or not 1 <= page <= min(page_count, PAGE_LIMIT):
            raise _malformed()
        out_warnings.append(_warning(code, page))
    if not out_blocks and not any(w["code"] == "no_extractable_text" for w in out_warnings):
        out_warnings.append(_warning("no_extractable_text", None))
    return {"interface_version": INTERFACE_VERSION, "format": "pdf", "page_count": page_count,
            "blocks": out_blocks, "warnings": out_warnings}


# --- the reader interface (2) -----------------------------------------------------------------------------------------
def extract(source: Any, *, filename: str | None = None, slot: Slot | None = None) -> dict:
    """Read one PDF in the worker and return its checked document result. With `slot`, the extraction is one
    admitted upload: its record is checked for cancellation when the worker returns. Raises AttachmentError."""
    if isinstance(source, (bytes, bytearray, memoryview)):
        if filename is None:
            raise AttachmentError("extraction_failed", "A filename is required with file bytes.")
        data = bytes(source)
    else:
        try:
            with open(os.fspath(source), "rb") as handle:
                data = handle.read(SOURCE_LIMIT + 1)
        except OSError:
            raise AttachmentError("damaged_file", "The file cannot be read.") from None
    if len(data) > SOURCE_LIMIT:
        raise AttachmentError("too_large", f"A PDF may be at most {SOURCE_LIMIT} bytes.", 413)
    return check_result(run_worker(data, slot))


def render(document: dict, pages: tuple[int, int] | None = None) -> dict:
    """Interface Section 5: `--- page N ---` before each page's text; blocks separated by one blank line. With
    `pages`, every block that overlaps the range is included whole (a PDF block never spans pages)."""
    page_count = document["page_count"]
    if pages is not None:
        first, last = pages
        if not (_is_int(first) and _is_int(last)) or first < 1 or last < first or last > page_count:
            raise AttachmentError("invalid_page_range", "The page range is outside the document.")
        chosen = [b for b in document["blocks"] if b["page_start"] <= last and b["page_end"] >= first]
    else:
        chosen = list(document["blocks"])
    parts, seen = [], set()
    for block in chosen:
        piece = block["text"]
        if block["page_start"] not in seen:
            seen.add(block["page_start"])
            piece = f"--- page {block['page_start']} ---\n" + piece
        parts.append(piece)
    if not chosen:
        where = f"pages {pages[0]}–{pages[1]}" if pages is not None else "the document"
        return {"text": "", "pages": list(pages) if pages is not None else None, "included_pages": None,
                "notice": f"No content was extracted from {where}."}
    included = [min(b["page_start"] for b in chosen), max(b["page_end"] for b in chosen)]
    notice = None
    if pages is not None and (included[0] < pages[0] or included[1] > pages[1]):
        notice = f"Included content from pages {included[0]}–{included[1]}, outside the requested pages."
    return {"text": "\n\n".join(parts), "pages": list(pages) if pages is not None else None,
            "included_pages": included, "notice": notice}


def coverage(document: dict) -> dict:
    """The bounded coverage record a staged PDF keeps and both previews show: the actual page count, the pages
    read and the pages with text kept distinct, and warning codes with pages (at most COVERAGE_LIMIT)."""
    page_count = document["page_count"]
    limited = next((w for w in document["warnings"] if w["code"] == "content_limited"), None)
    if limited is not None:
        read = limited["page"] or 0
    else:
        read = min(page_count, PAGE_LIMIT)
    return {"page_count": page_count, "pages_read": read,
            "pages_with_text": len({b["page_start"] for b in document["blocks"]}),
            "warnings": [{"code": w["code"], "page": w["page"]} for w in document["warnings"]][:COVERAGE_LIMIT]}
