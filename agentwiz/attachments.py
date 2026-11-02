"""The basic attachment reader and the consumer-side check of a reader's result (techspec 0.1.2,
Section 8.3; *AgentWiz attachment interface*, docs/attachment_interface.md).

Reads `.txt`, `.md`, `.csv` and `.json` files as text, implementing the interface's basic subset
(its Section 8). `check_document` checks any reader's result against the interface's Sections 3 and 4,
so that docSpell's results meet the same check from 0.1.4. This module and
`docs/attachment_interface.md` change together until docSpell's build freezes the interface (D35).

Nothing is written to disk. Messages are AgentWiz's own text and never contain file content.
Standard library only; knows nothing about HTTP.
"""
from __future__ import annotations

import codecs
import os
from collections.abc import Mapping
from pathlib import Path

INTERFACE_VERSION = 1
FORMATS = ("txt", "md", "csv", "json")
ACCEPTED_INTERFACE_VERSIONS = frozenset({1})

_BLOCK_TYPES = ("text", "heading", "paragraph", "list", "table", "figure")
_UNSUPPORTED = ("AgentWiz reads .txt, .md, .csv and .json files. PDF, Word and other formats need docSpell, "
                "which arrives in a later release.")
_UNDECODABLE = "The file is not UTF-8 text (or UTF-16 with a byte-order mark). Save a UTF-8 copy and attach that."
_MALFORMED = "The reader returned a malformed result."


class AttachmentError(Exception):
    """A reader's failure, or the consumer check's refusal, identified by `code`.

    Reader codes: unsupported_format, undecodable_text, damaged_file, extraction_failed,
    invalid_page_range. Consumer code (check_document only): unsupported_interface_version.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _format_of(filename: str) -> str:
    name = Path(filename).name
    ext = name.rsplit(".", 1)[1].lower() if "." in name.lstrip(".") else ""
    if ext not in FORMATS:
        raise AttachmentError("unsupported_format", _UNSUPPORTED)
    return ext


def _decode(data: bytes) -> str:
    if data.startswith(codecs.BOM_UTF32_BE) or data.startswith(codecs.BOM_UTF32_LE):
        raise AttachmentError("undecodable_text", _UNDECODABLE)
    try:
        if data.startswith(codecs.BOM_UTF8):
            return data[len(codecs.BOM_UTF8):].decode("utf-8", "strict")
        if data.startswith(codecs.BOM_UTF16_BE):
            return data[2:].decode("utf-16-be", "strict")
        if data.startswith(codecs.BOM_UTF16_LE):
            return data[2:].decode("utf-16-le", "strict")
        return data.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise AttachmentError("undecodable_text", _UNDECODABLE) from None


def extract(source: bytes | str | os.PathLike, *, filename: str | None = None) -> dict:
    """Read one file and return its document result (interface Section 3; basic subset, Section 8)."""
    if isinstance(source, (bytes, bytearray, memoryview)):
        if filename is None:
            raise AttachmentError("extraction_failed", "A filename is required with file bytes.")
        data = bytes(source)
    else:
        path = Path(source)
        filename = path.name if filename is None else filename
        fmt = _format_of(filename)
        try:
            data = path.read_bytes()
        except OSError:
            raise AttachmentError("damaged_file", "The file cannot be read.") from None
        return _document(fmt, data)
    return _document(_format_of(filename), data)


def _document(fmt: str, data: bytes) -> dict:
    text = _decode(data).replace("\r\n", "\n").replace("\r", "\n")
    warnings = [] if text else [{"code": "empty_file", "message": "The file is empty.", "page": None}]
    return {"interface_version": INTERFACE_VERSION, "format": fmt, "page_count": None,
            "blocks": [{"type": "text", "text": text, "page_start": None, "page_end": None}],
            "warnings": warnings}


def render(document: dict, pages: tuple[int, int] | None = None) -> dict:
    """The text to insert (interface Section 5). The basic formats have no pages."""
    if pages is not None:
        raise AttachmentError("invalid_page_range", "This file has no pages, so no page range can be chosen.")
    blocks = document["blocks"]
    text = blocks[0]["text"] if blocks else ""
    return {"text": text, "pages": None, "included_pages": None, "notice": None}


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def check_document(document: object) -> None:
    """Raise AttachmentError if a reader's result breaks the interface (Sections 3 and 4)."""
    def bad():
        return AttachmentError("extraction_failed", _MALFORMED)

    if not isinstance(document, Mapping):
        raise bad()
    version = document.get("interface_version")
    if _is_int(version) and version not in ACCEPTED_INTERFACE_VERSIONS:
        accepted = ", ".join(str(v) for v in sorted(ACCEPTED_INTERFACE_VERSIONS))
        raise AttachmentError("unsupported_interface_version",
                              f"The reader implements attachment interface version {version}; "
                              f"this AgentWiz accepts version {accepted}.")
    if not _is_int(version):
        raise bad()
    for key in ("format", "page_count", "blocks", "warnings"):
        if key not in document:
            raise bad()
    if not isinstance(document["format"], str):
        raise bad()
    page_count = document["page_count"]
    if page_count is not None and not (_is_int(page_count) and page_count >= 1):
        raise bad()
    blocks, warnings = document["blocks"], document["warnings"]
    if not isinstance(blocks, list) or not isinstance(warnings, list):
        raise bad()
    for block in blocks:
        if not isinstance(block, Mapping) or block.get("type") not in _BLOCK_TYPES:
            raise bad()
        if not isinstance(block.get("text"), str) or "page_start" not in block or "page_end" not in block:
            raise bad()
        start, end = block["page_start"], block["page_end"]
        if page_count is None:
            if start is not None or end is not None:
                raise bad()
        elif not (_is_int(start) and _is_int(end) and 1 <= start <= end <= page_count):
            raise bad()
        kind = block["type"]
        if kind == "heading" and not (_is_int(block.get("level")) and 1 <= block["level"] <= 6):
            raise bad()
        if kind == "table":
            rows = block.get("rows")
            if not isinstance(rows, list) or any(not isinstance(r, list) or any(not isinstance(c, str) for c in r)
                                                 for r in rows):
                raise bad()
            if not (_is_int(block.get("header_rows")) and block["header_rows"] >= 0):
                raise bad()
            if not isinstance(block.get("joined"), bool):
                raise bad()
            if block["joined"]:
                pages = block.get("source_pages")
                if not isinstance(pages, list) or not all(_is_int(p) for p in pages):
                    raise bad()
    for warning in warnings:
        if not isinstance(warning, Mapping) or not isinstance(warning.get("code"), str) \
                or not isinstance(warning.get("message"), str) or "page" not in warning:
            raise bad()
        if warning["page"] is not None and not _is_int(warning["page"]):
            raise bad()
