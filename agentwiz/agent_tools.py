"""The built-in tools (0.2.2, section 6; starter blocks addendum, sections 2 and 3).

Every built-in runs in-process, takes no slot, performs no network I/O and has a fixed schema with
`additionalProperties: false`. The utilities are pure: no network, no files, no state and no clock (except
`current_datetime`), so they run without approval or classification and are all `read-only`. Each utility is one
record in `REGISTRY`; `UTILITY_TOOLS`, `UTILITIES`, `SCHEMAS`, `DESCRIPTIONS`, `blocks.BUILTIN_TOOLS` and the
Workshop **Built-in utility** starter list are all derived from it, and nothing else names a utility. A utility can
be bound to a v4 Tool block or offered in agentic Chat as `aw_<name>`. The eight document tools act only on the live
conversation's allowlist and are offered in agentic Chat only (F-10).

A utility returns a plain JSON object or raises `ToolError(code)`. Codes are fixed; a message may carry counts,
indices, record numbers and limit names, never input text, a cell value, a header, a `find` string or replacement
text. No caller-supplied pattern is ever compiled as a regular expression. Knows nothing of HTTP.
"""
from __future__ import annotations

import bisect
import calendar
import csv
import datetime as dt
import difflib
import io
import json
import os
import re
from dataclasses import dataclass
from decimal import (Decimal, DivisionByZero, InvalidOperation, Overflow, Rounded, Inexact, Underflow,
                     Clamped, Subnormal, localcontext)
from typing import Any, Callable, Mapping

from . import identifier_pairs, jsonio

PREFIX = "aw_"
DOCUMENT_TOOLS = ("list_documents", "search_documents", "read_document", "create_draft",
                  "copy_to_draft", "revise_draft", "compare_documents", "read_table")
EXPRESSION_LIMIT = 500
DEPTH_LIMIT = 32
PRECISION = 34
# Work bounds (2.1, 2.2).
ARGUMENT_BYTES = 1 << 20                 # encoded arguments, in total
RESULT_LIMIT = 1 << 20                   # the default (Tool step) encoded result limit
TEXT_CHARS = 1 << 20                     # any single text argument
CHECK_EVERY = 1000                       # lines, rows or matches between Chat's Stop checks
DIFF_LINES = 5000
DIFF_WORK = 2_000_000                    # line pairs after the common prefix and suffix are removed
OUTLINE_LIMIT = 2000
FINDINGS_LIMIT = 1000
UNMAPPED_LIMIT = 100
TABLE_COLUMNS = 200
TABLE_ROWS = 10_000
CELL_CHARS = 10_000
NUMBER_CHARS = 100
CHUNK = 4096


def split_lines(text: str) -> list[str]:
    """The document's own lines: split on newline, a trailing carriage return dropped, no final empty line.
    `agent_documents.split_lines` is this function."""
    if text == "":
        return []
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def join_lines(lines: list[str]) -> str:
    """Text built from lines: joined with newlines, ending with one newline when non-empty (2.1)."""
    return "\n".join(lines) + "\n" if lines else ""


class ToolError(Exception):
    """A built-in refused or failed. `code` is fixed text; `position` is a 1-based position (a character, line,
    record, row or entry, by code). `message` overrides the code's fixed message; callers build it from counts,
    indices and limit names only."""

    def __init__(self, code: str, position: int | None = None, message: str | None = None):
        super().__init__(code)
        self.code = code
        self.position = position
        self.message = message

    def view(self) -> dict:
        out = {"code": self.code,
               "message": self.message or MESSAGES.get(self.code, "the built-in tool refused the request")}
        if self.position is not None:
            out["position"] = self.position
        return out


MESSAGES = {
    "unknown_timezone": "the time zone is not a known IANA name",
    "syntax_error": "the expression is not valid: use numbers, + - * /, unary minus and parentheses",
    "depth_limit": f"the expression nests parentheses deeper than {DEPTH_LIMIT}",
    "division_by_zero": "the expression divides by zero",
    "overflow": "the result is too large",
    "invalid_date": "dates are ISO YYYY-MM-DD, years 1 to 9999",
    "invalid_arguments": "the arguments do not fit the chosen operation",
    "out_of_range": "the result is outside years 1 to 9999",
    "unknown_document": "no document or draft has that ID in this conversation",
    "draft_limit": "the conversation's draft limit is reached",
    "document_limit": "the conversation's document limit is reached",
    "staged_limit": "the conversation's staged bytes limit is reached",
    "invalid_position": "the requested line or column is outside the document",
    "stopped": "the run was stopped",
    "too_large": "the result would be larger than the result limit",
    "count_mismatch": "the number of matches differs from expected_count",
    "invalid_table": "the table's rows must match its columns, and column names must be distinct",
    "invalid_csv": "the CSV text is not valid for the chosen dialect",
    "invalid_number": "a sort key is not a decimal number",
    "invalid_map": "the identifier map is not usable",
    "edit_mismatch": "an edit matched a different number of times than its expected_count",
    "not_a_table": "only .csv and .tsv documents can be read as a table",
    "row_too_large": "the first requested row does not fit the result limit; select fewer columns",
    "staging_unavailable": "staging is unavailable",
}

CHAT_REMEDY = "; use a narrower input, or the same block in Workshop or a workflow"


@dataclass
class Bounds:
    """How one call is bounded: the caller's encoded result limit and, in Chat only, its Stop check."""
    result_limit: int = RESULT_LIMIT
    check: Callable[[], None] | None = None
    ticks: int = 0

    def tick(self, count: int = 1) -> None:
        """Count loop iterations; every CHECK_EVERY, run the Stop check (Chat only)."""
        if self.check is None:
            return
        before = self.ticks // CHECK_EVERY
        self.ticks += count
        if self.ticks // CHECK_EVERY != before:
            self.check()

    def refuse(self, size: int) -> ToolError:
        remedy = CHAT_REMEDY if self.result_limit < RESULT_LIMIT else ""
        return ToolError("too_large", message=f"the result would be about {size} bytes; the limit is "
                                               f"{self.result_limit} bytes{remedy}")

    def require(self, size: int) -> None:
        if size > self.result_limit:
            raise self.refuse(size)


_ESCAPED = re.compile(r'[\x00-\x1f"\\]')


def enc(text: str) -> int:
    """The length of `text` inside jsonio's ASCII-escaped JSON string, without the quotes. JSON escaping is per
    code point, so lengths add; measured in bounded chunks so a large string is never escaped whole."""
    if text.isascii() and _ESCAPED.search(text) is None:
        return len(text)
    total = 0
    for offset in range(0, len(text), CHUNK):
        total += len(json.dumps(text[offset:offset + CHUNK], ensure_ascii=True)) - 2
    return total


def _strings_size(values: list[str]) -> int:
    """Encoded size of a JSON array of strings."""
    return 2 + sum(enc(v) + 2 for v in values) + max(0, len(values) - 1)


def _too_long(name: str, limit: int) -> ToolError:
    return ToolError("invalid_arguments", message=f"{name} is longer than {limit} characters")


def _text(arguments: Mapping, name: str, limit: int = TEXT_CHARS, minimum: int = 0) -> str:
    value = arguments.get(name)
    if not isinstance(value, str):
        raise ToolError("invalid_arguments", message=f"{name} must be text")
    if len(value) > limit:
        raise _too_long(name, limit)
    if len(value) < minimum:
        raise ToolError("invalid_arguments", message=f"{name} must have at least {minimum} characters")
    return value


def _flag(arguments: Mapping, name: str, default: bool) -> bool:
    value = arguments.get(name, default)
    if type(value) is not bool:
        raise ToolError("invalid_arguments", message=f"{name} must be true or false")
    return value


def _bounded_int(arguments: Mapping, name: str, low: int, high: int, default: int | None = None) -> int | None:
    value = arguments.get(name, default)
    if value is None:
        return None
    if type(value) is not int or not low <= value <= high:
        raise ToolError("invalid_arguments", message=f"{name} must be a whole number from {low} to {high}")
    return value


def capability_name(tool: str) -> str:
    return PREFIX + tool


# --- current_datetime (6.5) -----------------------------------------------------------------------------------------
def _offset(delta: dt.timedelta | None) -> str:
    total = int((delta or dt.timedelta()).total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"


def current_datetime(arguments: Mapping, now: dt.datetime | None = None) -> dict:
    """`now` is an aware UTC instant, for tests; the live clock otherwise."""
    instant = now if now is not None else dt.datetime.now(dt.timezone.utc)
    name = arguments.get("timezone")
    if name is not None:
        try:
            from zoneinfo import ZoneInfo
            zone: Any = ZoneInfo(name)
        except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError, ValueError for a path-like key, a missing tz database
            raise ToolError("unknown_timezone") from None
        local = instant.astimezone(zone)
        label = name
    else:
        local = instant.astimezone()
        label = os.environ.get("TZ") or local.tzname() or "local"
    return {"local": local.isoformat(timespec="seconds"), "timezone": label,
            "utc_offset": _offset(local.utcoffset()),
            "utc": instant.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "weekday": calendar.day_name[local.weekday()]}


# --- calculate (6.6) --------------------------------------------------------------------------------------------------
class _Parser:
    """Recursive descent over: expr = term (('+'|'-') term)*; term = unary (('*'|'/') unary)*;
    unary = '-' unary | primary; primary = number | '(' expr ')'. Positions are 1-based."""

    def __init__(self, text: str):
        self.text = text
        self.i = 0
        self.depth = 0

    def skip(self) -> None:
        while self.i < len(self.text) and self.text[self.i] in " \t\r\n":
            self.i += 1

    def peek(self) -> str:
        self.skip()
        return self.text[self.i] if self.i < len(self.text) else ""

    def error(self) -> ToolError:
        self.skip()
        return ToolError("syntax_error", self.i + 1)

    def parse(self) -> Decimal:
        value = self.expr()
        if self.peek() != "":
            raise self.error()
        return value

    def expr(self) -> Decimal:
        value = self.term()
        while self.peek() in ("+", "-"):
            op = self.text[self.i]
            self.i += 1
            right = self.term()
            value = value + right if op == "+" else value - right
        return value

    def term(self) -> Decimal:
        value = self.unary()
        while self.peek() in ("*", "/"):
            op, position = self.text[self.i], self.i + 1
            self.i += 1
            right = self.unary()
            if op == "*":
                value = value * right
            else:
                if right == 0:
                    raise ToolError("division_by_zero", position)
                value = value / right
        return value

    def unary(self) -> Decimal:
        if self.peek() == "-":
            self.i += 1
            self.depth += 1
            if self.depth > DEPTH_LIMIT:
                raise ToolError("depth_limit", self.i)
            try:
                return -self.unary()
            finally:
                self.depth -= 1
        return self.primary()

    def primary(self) -> Decimal:
        c = self.peek()
        if c == "(":
            self.depth += 1
            if self.depth > DEPTH_LIMIT:
                raise ToolError("depth_limit", self.i + 1)
            self.i += 1
            value = self.expr()
            if self.peek() != ")":
                raise self.error()
            self.i += 1
            self.depth -= 1
            return value
        start = self.i
        digits = 0
        while self.i < len(self.text) and self.text[self.i].isascii() and self.text[self.i].isdigit():
            self.i += 1
            digits += 1
        if self.i < len(self.text) and self.text[self.i] == ".":
            self.i += 1
            while self.i < len(self.text) and self.text[self.i].isascii() and self.text[self.i].isdigit():
                self.i += 1
                digits += 1
        if digits == 0:
            self.i = start
            raise self.error()
        return +Decimal(self.text[start:self.i])        # unary plus applies the context: a long literal rounds


def calculate(arguments: Mapping) -> dict:
    text = arguments.get("expression")
    if not isinstance(text, str) or not text or len(text) > EXPRESSION_LIMIT:
        raise ToolError("syntax_error", 1)
    with localcontext() as ctx:
        _calculator_context(ctx)
        try:
            value = _Parser(text).parse()
        except Overflow:
            raise ToolError("overflow") from None
        except (DivisionByZero, InvalidOperation):
            raise ToolError("division_by_zero") from None
        except RecursionError:
            raise ToolError("depth_limit") from None
        rounded = bool(ctx.flags[Inexact] or ctx.flags[Underflow])
    return {"result": decimal_text(value), "rounded": rounded}           # never "-0"


# --- date_arithmetic (6.7) ----------------------------------------------------------------------------------------------
def _date(text: object) -> dt.date:
    if (not isinstance(text, str) or len(text) != 10 or text[4] != "-" or text[7] != "-"
            or not (text[:4] + text[5:7] + text[8:]).isdigit() or not text.isascii()):
        raise ToolError("invalid_date")
    try:
        return dt.date(int(text[:4]), int(text[5:7]), int(text[8:]))
    except ValueError:
        raise ToolError("invalid_date") from None


def _integer(arguments: Mapping, name: str) -> int:
    value = arguments.get(name, 0)
    if type(value) is not int:
        raise ToolError("invalid_arguments")
    return value


def date_arithmetic(arguments: Mapping) -> dict:
    operation = arguments.get("operation")
    fields = set(arguments) - {"operation"}
    if operation == "add":
        if not fields <= {"date", "years", "months", "weeks", "days"} or "date" not in fields:
            raise ToolError("invalid_arguments")
        start = _date(arguments["date"])
        months = start.month - 1 + 12 * _integer(arguments, "years") + _integer(arguments, "months")
        year, month = start.year + months // 12, months % 12 + 1
        if not 1 <= year <= 9999:
            raise ToolError("out_of_range")
        last = calendar.monthrange(year, month)[1]
        clamped = start.day > last
        moved = dt.date(year, month, min(start.day, last))
        delta = 7 * _integer(arguments, "weeks") + _integer(arguments, "days")
        try:
            result = moved + dt.timedelta(days=delta)
        except OverflowError:
            raise ToolError("out_of_range") from None
        return {"date": result.isoformat(), "clamped": clamped}
    if operation == "difference":
        if fields != {"from", "to"}:
            raise ToolError("invalid_arguments")
        return {"days": (_date(arguments["to"]) - _date(arguments["from"])).days}
    if operation == "weekday":
        if fields != {"date"}:
            raise ToolError("invalid_arguments")
        day = _date(arguments["date"])
        return {"weekday": calendar.day_name[day.weekday()], "iso_weekday": day.isoweekday()}
    raise ToolError("invalid_arguments")




# --- find_replace (2.2) -----------------------------------------------------------------------------------------------
def find_replace(arguments: Mapping, bounds: Bounds | None = None) -> dict:
    """Literal, case-sensitive, code-point replacement, left to right without overlaps."""
    bounds = bounds or Bounds()
    text = _text(arguments, "text")
    find = _text(arguments, "find", 1000, 1)
    replace = _text(arguments, "replace", 1000)
    expected = _bounded_int(arguments, "expected_count", 1, 100_000)
    count = text.count(find)
    if expected is not None and count != expected:
        raise ToolError("count_mismatch", message=f"expected {expected} matches, found {count}")
    size = enc(text) + count * (enc(replace) - enc(find)) + len('{"text":"","count":}') + len(str(count))
    bounds.require(size)
    return {"text": text.replace(find, replace) if count else text, "count": count}


# --- the shared heading scanner (2.2) ---------------------------------------------------------------------------------
_ATX = re.compile(r" {0,3}(#{1,6})(?:[ \t]|\Z)")
_CLOSING = re.compile(r"(?:\A|[ \t])#+\Z")
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")


def scan_headings(lines: list[str], bounds: Bounds | None = None) -> list[tuple[int, int, str]]:
    """(line index, level, text) of every ATX heading outside fenced code. Setext headings, indented code, HTML
    blocks and headings inside block quotes or lists are not recognised."""
    found = []
    fence: str | None = None
    for index, line in enumerate(lines):
        if bounds is not None:
            bounds.tick()
        if fence is not None:
            stripped = line.lstrip(" ")
            if len(line) - len(stripped) <= 3 and stripped.startswith(fence) \
                    and stripped.rstrip(" ") == fence[0] * len(stripped.rstrip(" ")):
                fence = None
            continue
        opened = _FENCE.match(line)
        if opened is not None:
            fence = opened.group(1)
            continue
        match = _ATX.match(line)
        if match is None:
            continue
        rest = line[match.end():].rstrip(" \t")
        closing = _CLOSING.search(rest)
        if closing is not None:
            rest = rest[:closing.start()]
        found.append((index, len(match.group(1)), rest.strip(" \t")))
    return found


def _normal(text: str) -> str:
    return " ".join(text.split()).casefold()


def extract_section(arguments: Mapping, bounds: Bounds | None = None) -> dict:
    bounds = bounds or Bounds()
    markdown = _text(arguments, "markdown")
    heading = _text(arguments, "heading", 500, 1)
    subsections = _flag(arguments, "include_subsections", True)
    lines = split_lines(markdown)
    headings = scan_headings(lines, bounds)
    wanted = _normal(heading)
    matches = [h for h in headings if _normal(h[2]) == wanted]
    if not matches:
        return {"found": False, "matches": 0, "section": "", "level": None, "start_line": None, "end_line": None}
    start, level, _ = matches[0]
    end = len(lines)
    for index, other, _text_ in headings:
        if index > start and (other <= level or not subsections):
            end = index
            break
    size = sum(enc(line) + 2 for line in lines[start:end]) + 120
    bounds.require(size)
    return {"found": True, "matches": len(matches), "section": join_lines(lines[start:end]), "level": level,
            "start_line": start + 1, "end_line": end}


def outline(arguments: Mapping, bounds: Bounds | None = None) -> dict:
    bounds = bounds or Bounds()
    markdown = _text(arguments, "markdown")
    rows, truncated = [], False
    size = len('{"headings":[],"truncated":false}')
    for index, level, text in scan_headings(split_lines(markdown), bounds):
        row_size = enc(text) + len('{"level":0,"text":"","line":},') + len(str(index + 1))
        if len(rows) >= OUTLINE_LIMIT or size + row_size > bounds.result_limit:
            truncated = True
            break
        rows.append({"level": level, "text": text, "line": index + 1})
        size += row_size
    return {"headings": rows, "truncated": truncated}


# --- compare_texts (2.2) ----------------------------------------------------------------------------------------------
_HUNK = re.compile(r"@@ -(\d+)((?:,\d+)?) \+(\d+)((?:,\d+)?) @@\Z")


def _diff_lines(before: list[str], after: list[str], context: int, name: str = "texts"):
    if len(before) > DIFF_LINES or len(after) > DIFF_LINES:
        raise ToolError("too_large", message=f"each of the {name} may have at most {DIFF_LINES} lines")
    prefix = 0
    limit = min(len(before), len(after))
    while prefix < limit and before[prefix] == after[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and before[-1 - suffix] == after[-1 - suffix]:
        suffix += 1
    middle_a, middle_b = len(before) - prefix - suffix, len(after) - prefix - suffix
    if middle_a * middle_b > DIFF_WORK:
        raise ToolError("too_large", message=f"the {name} differ in too many lines to compare: "
                                             f"{middle_a} by {middle_b} lines is over {DIFF_WORK} line pairs")
    # The context window around the differing middle: the diff of the window is the diff of the whole, with its
    # hunk ranges shifted by the lines left out in front.
    head = max(0, prefix - context)
    window_a = before[head:len(before) - max(0, suffix - context)]
    window_b = after[head:len(after) - max(0, suffix - context)]
    lines = difflib.unified_diff(window_a, window_b, "before", "after", n=context, lineterm="")
    for line in lines:
        if head and line.startswith("@@ "):
            match = _HUNK.match(line)
            if match is not None:
                a, a_len, b, b_len = match.groups()
                line = f"@@ -{int(a) + head}{a_len} +{int(b) + head}{b_len} @@"
        yield line


def compare(before_text: str, after_text: str, context: int, bounds: Bounds, name: str = "texts") -> dict:
    before, after = split_lines(before_text), split_lines(after_text)
    out: list[str] = []
    size = len('{"changed":false,"diff":"","added_lines":,"removed_lines":}') + 14
    added = removed = 0
    for index, line in enumerate(_diff_lines(before, after, context, name)):
        bounds.tick()
        size += enc(line) + 2
        if size > bounds.result_limit:
            raise bounds.refuse(size)
        if index >= 2 and not line.startswith("@@"):
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                removed += 1
        out.append(line)
    return {"changed": before != after, "diff": "\n".join(out), "added_lines": added, "removed_lines": removed}


def compare_texts(arguments: Mapping, bounds: Bounds | None = None) -> dict:
    bounds = bounds or Bounds()
    before = _text(arguments, "before")
    after = _text(arguments, "after")
    context = _bounded_int(arguments, "context_lines", 0, 10, 3)
    return compare(before, after, context, bounds)


# --- combine_texts (2.2) ----------------------------------------------------------------------------------------------
def combine_texts(arguments: Mapping, bounds: Bounds | None = None) -> dict:
    bounds = bounds or Bounds()
    parts = arguments.get("parts")
    if not isinstance(parts, list) or not 1 <= len(parts) <= 50 or not all(isinstance(p, str) for p in parts):
        raise ToolError("invalid_arguments", message="parts must hold 1 to 50 texts")
    separator = _text({"separator": arguments.get("separator", "\n\n")}, "separator", 100)
    headings = arguments.get("headings")
    if headings is not None:
        if (not isinstance(headings, list) or len(headings) != len(parts)
                or any(not isinstance(h, str) or not 1 <= len(h) <= 200 or "\n" in h or "\r" in h
                       for h in headings)):
            raise ToolError("invalid_arguments", message="headings must match parts in number, each 1 to 200 "
                                                         "characters with no line break")
    size = sum(enc(p) for p in parts) + enc(separator) * (len(parts) - 1) + len('{"text":""}')
    if headings is not None:
        size += sum(enc(h) + enc("## \n\n") for h in headings)
    bounds.require(size)
    pieces = parts if headings is None else [f"## {h}\n\n{p}" for h, p in zip(headings, parts)]
    return {"text": separator.join(pieces)}


# --- the table value (2.2) --------------------------------------------------------------------------------------------
def check_table(columns: object, rows: object, bounds: Bounds | None = None) -> tuple[list[str], list[list[str]]]:
    """Width and header uniqueness, which the contract dialect cannot express. `position` is the first bad row
    (1-based), or 0 for the header."""
    if (not isinstance(columns, list) or not 1 <= len(columns) <= TABLE_COLUMNS
            or any(not isinstance(c, str) or not 1 <= len(c) <= 200 for c in columns)):
        raise ToolError("invalid_table", 0, f"columns must hold 1 to {TABLE_COLUMNS} names of 1 to 200 characters")
    if len(set(columns)) != len(columns):
        raise ToolError("invalid_table", 0, "column names must be distinct")
    if not isinstance(rows, list) or len(rows) > TABLE_ROWS:
        raise ToolError("invalid_table", None, f"rows must be a list of at most {TABLE_ROWS} rows")
    for number, row in enumerate(rows, 1):
        if bounds is not None:
            bounds.tick()
        if not isinstance(row, list) or len(row) != len(columns):
            raise ToolError("invalid_table", number, f"row {number} does not have {len(columns)} cells")
        for cell in row:
            if not isinstance(cell, str) or len(cell) > CELL_CHARS:
                raise ToolError("invalid_table", number, f"row {number} has a cell that is not text of at most "
                                                         f"{CELL_CHARS} characters")
    return columns, rows


def _row_size(row: list[str]) -> int:
    return _strings_size(row) + 1


def _table_arguments(arguments: Mapping, bounds: Bounds) -> tuple[list[str], list[list[str]]]:
    return check_table(arguments.get("columns"), arguments.get("rows"), bounds)


# --- parse_csv (2.2) --------------------------------------------------------------------------------------------------
DELIMITERS = (",", ";", "\t", "|")


def _without_bom(stream):
    """The stream's lines, a leading U+FEFF removed. A stream opened with newline="" keeps each line's ending, so
    a quoted field that spans lines parses as one record."""
    first = True
    for line in stream:
        if first:
            first = False
            if line.startswith("\ufeff"):
                line = line[1:]
        yield line


class CSVReader:
    """Records of one CSV text in the parse_csv dialect, with their logical record numbers (1-based, counting
    the header; records with zero fields are skipped and not counted)."""

    def __init__(self, source: str | Any, delimiter: str):
        stream = io.StringIO(source, newline="") if isinstance(source, str) else source
        self._reader = csv.reader(_without_bom(stream), delimiter=delimiter, quotechar='"',
                                  doublequote=True, strict=True, skipinitialspace=False)
        self.number = 0

    def __iter__(self):
        return self

    def __next__(self) -> list[str]:
        while True:
            try:
                record = next(self._reader)
            except csv.Error:
                raise ToolError("invalid_csv", self.number + 1,
                                f"record {self.number + 1} is not valid CSV in this dialect") from None
            if record:
                self.number += 1
                return record


def csv_header(record: list[str], number: int, has_header: bool) -> list[str]:
    if not has_header:
        return [f"c{i}" for i in range(1, len(record) + 1)]
    if any(name == "" for name in record) or len(set(record)) != len(record):
        raise ToolError("invalid_csv", number, f"record {number}: header names must be non-empty and distinct")
    if any(len(name) > 200 for name in record):
        raise ToolError("invalid_csv", number, f"record {number}: header names must be at most 200 characters")
    return list(record)


def csv_check_record(record: list[str], width: int, number: int) -> None:
    if len(record) != width:
        raise ToolError("invalid_csv", number, f"record {number} has {len(record)} fields; expected {width}")
    for cell in record:
        if len(cell) > CELL_CHARS:
            raise ToolError("too_large", number, f"record {number} has a field over {CELL_CHARS} characters")


def parse_csv(arguments: Mapping, bounds: Bounds | None = None) -> dict:
    bounds = bounds or Bounds()
    text = _text(arguments, "csv")
    delimiter = arguments.get("delimiter", ",")
    if delimiter not in DELIMITERS:
        raise ToolError("invalid_arguments", message="delimiter must be a comma, semicolon, tab or bar")
    has_header = _flag(arguments, "has_header", True)
    reader = CSVReader(text, delimiter)
    columns: list[str] | None = None
    rows: list[list[str]] = []
    size = len('{"columns":,"rows":[],"row_count":}') + 6
    for record in reader:
        bounds.tick()
        number = reader.number
        if columns is None:
            columns = csv_header(record, number, has_header)
            if len(columns) > TABLE_COLUMNS:
                raise ToolError("too_large", number, f"the CSV has more than {TABLE_COLUMNS} columns")
            size += _strings_size(columns)
            if has_header:
                continue
        csv_check_record(record, len(columns), number)
        if len(rows) >= TABLE_ROWS:
            raise ToolError("too_large", number, f"the CSV has more than {TABLE_ROWS} data rows")
        size += _row_size(record)
        if size > bounds.result_limit:
            raise bounds.refuse(size)
        rows.append(record)
    if columns is None:
        raise ToolError("invalid_csv", 1, "the CSV has no records")
    return {"columns": columns, "rows": rows, "row_count": len(rows)}


# --- rows_to_table (2.2) ----------------------------------------------------------------------------------------------
def _cell(text: str) -> str:
    return (text.replace("\\", "\\\\").replace("|", "\\|")
            .replace("\r\n", "<br>").replace("\r", "<br>").replace("\n", "<br>"))


def rows_to_table(arguments: Mapping, bounds: Bounds | None = None) -> dict:
    bounds = bounds or Bounds()
    columns, rows = _table_arguments(arguments, bounds)
    lines: list[str] = []
    size = len('{"markdown":""}')

    def add(cells: list[str]) -> None:
        nonlocal size
        line = "| " + " | ".join(cells) + " |"
        size += enc(line) + 2
        if size > bounds.result_limit:
            raise bounds.refuse(size)
        lines.append(line)
    add([_cell(c) for c in columns])
    add(["---"] * len(columns))
    for row in rows:
        bounds.tick()
        add([_cell(c) for c in row])
    return {"markdown": join_lines(lines)}


# --- sort_dedupe_rows (2.2) -------------------------------------------------------------------------------------------
_DECIMAL = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?\Z")


def decimal_of(text: str) -> Decimal | None:
    """The decimal rule: trimmed; an optional sign, digits with an optional fraction, no exponent, no grouping, at
    most 100 characters. None when the text does not follow it. The value is exact (no context applied)."""
    value = text.strip()
    if len(value) > NUMBER_CHARS or not value.isascii() or _DECIMAL.match(value) is None:
        return None
    return Decimal(value)


def sort_dedupe_rows(arguments: Mapping, bounds: Bounds | None = None) -> dict:
    bounds = bounds or Bounds()
    columns, rows = _table_arguments(arguments, bounds)
    by = arguments.get("by")
    if (not isinstance(by, list) or not 1 <= len(by) <= 5 or len(set(by)) != len(by)
            or any(not isinstance(name, str) or name not in columns for name in by)):
        raise ToolError("invalid_arguments", message="by must name 1 to 5 distinct columns")
    numeric = _flag(arguments, "numeric", False)
    descending = _flag(arguments, "descending", False)
    dedupe = _flag(arguments, "dedupe", False)
    indices = [columns.index(name) for name in by]
    kept, numbers, seen = [], [], set()
    for number, row in enumerate(rows, 1):
        bounds.tick()
        if dedupe:
            identity = tuple(row[i] for i in indices)
            if identity in seen:
                continue
            seen.add(identity)
        kept.append(row)
        numbers.append(number)
    removed = len(rows) - len(kept)
    keys: list[list[Any]] = []
    for number, row in zip(numbers, kept):
        bounds.tick()
        values = []
        for i in indices:
            cell = row[i]
            if cell == "":
                values.append(None)
            elif numeric:
                value = decimal_of(cell)
                if value is None:
                    raise ToolError("invalid_number", number, f"row {number} has a sort key that is not a decimal "
                                                              "number")
                values.append(value)
            else:
                values.append(cell)
        keys.append(values)
    order = list(range(len(kept)))
    # Stable passes from the last key to the first. Empty keys sort after every non-empty key in either direction.
    for position in reversed(range(len(indices))):
        filler: Any = Decimal(0) if numeric else ""
        if descending:
            order.sort(key=lambda i: (keys[i][position] is not None,
                                      filler if keys[i][position] is None else keys[i][position]), reverse=True)
        else:
            order.sort(key=lambda i: (keys[i][position] is None,
                                      filler if keys[i][position] is None else keys[i][position]))
    result = [kept[i] for i in order]
    size = _strings_size(columns) + 2 + sum(_row_size(r) for r in result) + 40
    bounds.require(size)
    return {"columns": list(columns), "rows": result, "removed": removed}


# --- column_stats (2.2) -----------------------------------------------------------------------------------------------
def _calculator_context(ctx) -> None:
    ctx.prec = PRECISION
    ctx.Emax, ctx.Emin = 999, -999
    ctx.traps[Overflow] = True
    ctx.traps[DivisionByZero] = True
    ctx.traps[InvalidOperation] = True
    for flag in (Rounded, Inexact, Underflow, Clamped, Subnormal):
        ctx.traps[flag] = False
    ctx.clear_flags()


def decimal_text(value: Decimal) -> str:
    """A decimal string in the calculator's format."""
    if value == 0:
        value = abs(value)
    return format(value, "f")


def column_stats(arguments: Mapping, bounds: Bounds | None = None) -> dict:
    bounds = bounds or Bounds()
    columns, rows = _table_arguments(arguments, bounds)
    column = arguments.get("column")
    if not isinstance(column, str) or column not in columns:
        raise ToolError("invalid_arguments", message="column must name one of the columns")
    index = columns.index(column)
    numeric = empty = other = 0
    total = low = high = None
    with localcontext() as ctx:
        _calculator_context(ctx)
        try:
            for row in rows:
                bounds.tick()
                cell = row[index]
                if cell == "":
                    empty += 1
                    continue
                value = decimal_of(cell)
                if value is None:
                    other += 1
                    continue
                value = +value                               # the context applies: a long value rounds
                numeric += 1
                total = value if total is None else total + value
                low = value if low is None or value < low else low
                high = value if high is None or value > high else high
            mean = total / numeric if numeric else None
        except Overflow:
            raise ToolError("too_large", message="the sum is outside the calculator's range") from None
        rounded = bool(ctx.flags[Inexact] or ctx.flags[Underflow])
    text = (lambda v: None if v is None else decimal_text(v))
    return {"count": len(rows), "numeric_count": numeric, "empty_count": empty, "other_count": other,
            "sum": text(total), "min": text(low), "max": text(high), "mean": text(mean), "rounded": rounded}


# --- scan_identifiers (2.2) -------------------------------------------------------------------------------------------
KINDS = ("url", "email", "ip_address", "date", "phone", "long_number")
# Each pattern starts only where a run starts (a negative lookbehind), has no nested or overlapping quantifiers,
# and is followed by Python post-processing, so every scan is linear in the text.
_OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9][0-9]|[0-9])"
_MONTH = (r"(?i:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
          r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_DAY = r"(?:[12][0-9]|3[01]|0?[1-9])(?i:st|nd|rd|th)?"
_DMY = r"(?:[12][0-9]|3[01]|0?[1-9])"
PATTERNS = {
    "url": re.compile(r"(?<![A-Za-z0-9])(?:[Hh][Tt][Tt][Pp][Ss]?://|[Ww][Ww][Ww]\.)[^\s<>\"]+"),
    "email": re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]+"),
    "ip_address": re.compile(rf"(?<![0-9.]){_OCTET}\.{_OCTET}\.{_OCTET}\.{_OCTET}(?!\.?[0-9])"),
    "date": re.compile(
        r"(?<![0-9])[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])(?![0-9])"
        rf"|(?<![0-9]){_DMY}(?P<sep>[/.-]){_DMY}(?P=sep)[0-9]{{4}}(?![0-9])"
        rf"|(?<![0-9A-Za-z]){_DAY} +{_MONTH}(?:,? +[0-9]{{4}})?(?![0-9A-Za-z])"
        rf"|(?<![0-9A-Za-z]){_MONTH} +{_DAY}(?:,? +[0-9]{{4}})?(?![0-9A-Za-z])"),
    # A phone has at least 8 digits and a long number at least 8, so a shorter run can never qualify: the minimum
    # length keeps those runs out of Python-level post-processing.
    "phone": re.compile(r"(?<![0-9A-Za-z+(])[+(0][0-9 ().-]{7,}"),
    "long_number": re.compile(r"(?<![0-9])[0-9][0-9 -]{7,}"),
}
_URL_TAIL = ".,;:!?)]}'\""
_URL_PREFIX = re.compile(r"[Hh][Tt][Tt][Pp][Ss]?://|[Ww][Ww][Ww]\.")
_SEPARATOR_RUN = re.compile(r"[ -]{2,}")


def _candidates(kind: str, text: str):
    """(start, end) spans of one kind, after post-processing."""
    for match in PATTERNS[kind].finditer(text):
        start, end = match.span()
        value = match.group()
        if kind == "url":
            trimmed = value.rstrip(_URL_TAIL)
            if len(trimmed) > _URL_PREFIX.match(value).end():
                yield start, start + len(trimmed)
        elif kind == "email":
            trimmed = value.rstrip(".-")
            domain = trimmed.split("@", 1)[1]
            if "." in domain and not domain.startswith((".", "-")) and ".." not in domain:
                yield start, start + len(trimmed)
        elif kind == "phone":
            trimmed = value.rstrip(" ().-")
            if trimmed.endswith(")"):
                trimmed = trimmed[:-1]
            digits = sum(c.isdigit() for c in trimmed)
            if 8 <= digits <= 15:
                yield start, start + len(trimmed)
        elif kind == "long_number":
            # Only single spaces or hyphens join digits: split at any longer separator run, and each segment,
            # without trailing separators, counts on its own.
            offset = 0
            for gap in [*_SEPARATOR_RUN.finditer(value), None]:
                segment = value[offset:gap.start() if gap is not None else len(value)].rstrip(" -")
                if segment[:1].isdigit() and sum(c.isdigit() for c in segment) >= 8:
                    yield start + offset, start + offset + len(segment)
                if gap is not None:
                    offset = gap.end()
        else:
            yield start, end


def scan(text: str, kinds: tuple[str, ...], bounds: Bounds | None = None):
    """Every finding, leftmost first; at one position the earlier kind in KINDS wins; no two overlap."""
    spans = []
    for priority, kind in enumerate(KINDS):
        if kind in kinds:
            for start, end in _candidates(kind, text):
                if end > start:
                    spans.append((start, priority, end, kind))
    spans.sort()
    last = 0
    for start, _priority, end, kind in spans:
        if start < last:
            continue
        last = end
        if bounds is not None:
            bounds.tick()
        yield start, end, kind


def scan_identifiers(arguments: Mapping, bounds: Bounds | None = None) -> dict:
    bounds = bounds or Bounds()
    text = _text(arguments, "text")
    kinds = arguments.get("kinds", list(KINDS))
    if (not isinstance(kinds, list) or not kinds or len(set(kinds)) != len(kinds)
            or any(k not in KINDS for k in kinds)):
        raise ToolError("invalid_arguments", message="kinds must be a non-empty subset of " + ", ".join(KINDS))
    selected = tuple(k for k in KINDS if k in kinds)
    starts = [0] + [m.end() for m in re.finditer("\n", text)]
    counts = {k: 0 for k in selected}
    findings, truncated = [], False
    size = len('{"findings":[],"counts":{},"truncated":false}') + sum(len(k) + 12 for k in selected)
    for start, end, kind in scan(text, selected, bounds):
        counts[kind] += 1
        if truncated:
            continue
        line = bisect.bisect_right(starts, start)
        value = text[start:end]
        row = {"kind": kind, "value": value, "line": line, "column": start - starts[line - 1] + 1}
        row_size = len(jsonio.encode_exact(row)) + 1
        if len(findings) >= FINDINGS_LIMIT or size + row_size > bounds.result_limit:
            truncated = True
            continue
        findings.append(row)
        size += row_size
    return {"findings": findings, "counts": counts, "truncated": truncated}


# --- check_identifier_map (2.2) ---------------------------------------------------------------------------------------
_TOKEN = identifier_pairs.SYNTAXES["bracket_upper"]


def _map_error(position: int, message: str) -> ToolError:
    return ToolError("invalid_map", position, message)


def check_identifier_map(arguments: Mapping, bounds: Bounds | None = None) -> dict:
    bounds = bounds or Bounds()
    original = _text(arguments, "original")
    text = _text(arguments, "text")
    pairs = arguments.get("pairs")
    if not isinstance(pairs, list) or len(pairs) > identifier_pairs.ENTRY_LIMIT:
        raise _map_error(0, f"pairs must be a list of at most {identifier_pairs.ENTRY_LIMIT} entries")
    # 1. Restore's own parser: placeholder syntax, empty or oversized values, one placeholder for two originals.
    try:
        identifier_pairs.parse(pairs, profile="restore", syntax="bracket_upper")
    except identifier_pairs.PairsError as exc:
        position = 0 if exc.index is None else exc.index + 1
        raise _map_error(position, ("entry %d: " % position if position else "") + exc.message) from None
    checked, by_original, seen = [], {}, set()
    for number, item in enumerate(pairs, 1):
        bounds.tick()
        identity = (item["placeholder"], item["original"])
        if identity in seen:
            continue
        seen.add(identity)
        # 2. No original under two placeholders: with check 1, the map is one-to-one.
        if item["original"] in by_original:
            raise _map_error(number, f"entry {number}: the original is already mapped to another placeholder "
                                     f"(entry {by_original[item['original']]})")
        by_original[item["original"]] = number
        checked.append((number, identity))
    # A bracket token contains no bracket, so one linear pass finds every placeholder the source contains.
    source_tokens = {m.group() for m in _TOKEN.finditer(original)}
    for number, (placeholder, value) in checked:
        bounds.tick()
        # 3. Every original occurs in the source text.
        if value not in original:
            raise _map_error(number, f"entry {number}: the original does not occur in the source text")
        # 4. Every placeholder occurs in the de-identified text.
        if placeholder not in text:
            raise _map_error(number, f"entry {number}: the placeholder does not occur in the de-identified text")
        # 5. No new placeholder collides with a token the source already contained.
        if placeholder in source_tokens:
            raise _map_error(number, f"entry {number}: the placeholder already occurs in the source text")
    mapped = {placeholder for _number, (placeholder, _value) in checked}
    unmapped, truncated = [], False
    for match in _TOKEN.finditer(text):
        bounds.tick()
        token = match.group()
        if token in mapped or token in unmapped:
            continue
        if len(unmapped) >= UNMAPPED_LIMIT:
            truncated = True
            break
        unmapped.append(token)
    result_pairs = [{"placeholder": p, "original": o} for _number, (p, o) in checked]
    size = (enc(text) + sum(enc(p) + enc(o) + 34 for p, o in (i for _n, i in checked))
            + _strings_size(unmapped) + 90)
    bounds.require(size)
    return {"text": text, "pairs": result_pairs, "entries": len(result_pairs), "unmapped_tokens": unmapped,
            "unmapped_truncated": truncated}


# --- the registry (2.1) -----------------------------------------------------------------------------------------------
def _string(maximum: int, minimum: int = 0) -> dict:
    node: dict = {"type": "string", "maxLength": maximum}
    if minimum:
        node["minLength"] = minimum
    return node


def _object(properties: dict, required: tuple = (), *, closed: bool = True) -> dict:
    node: dict = {"type": "object", "properties": properties, "additionalProperties": not closed}
    node["required"] = list(required)
    return node


_COLUMNS = {"type": "array", "minItems": 1, "maxItems": TABLE_COLUMNS, "items": _string(200, 1)}
_ROWS = {"type": "array", "maxItems": TABLE_ROWS,
         "items": {"type": "array", "maxItems": TABLE_COLUMNS, "items": _string(CELL_CHARS)}}
_DECIMAL_TEXT = {"type": ["string", "null"], "maxLength": 1100}
_PAIR = _object({"placeholder": _string(identifier_pairs.PLACEHOLDER_LIMIT),
                 "original": _string(identifier_pairs.ORIGINAL_LIMIT)}, ("placeholder", "original"))


@dataclass(frozen=True)
class Utility:
    """One built-in utility: its fixed name, schemas, description, limits and function."""
    name: str
    schema: dict                         # arguments (flat, closed)
    result: dict                         # the result, as a contract-dialect schema (a Tool block's `out`)
    description: str
    limits: Mapping[str, int]
    function: Callable[[Mapping, Bounds], dict]


def _registry() -> dict[str, Utility]:
    text = _string(TEXT_CHARS)
    entries = [
        Utility("current_datetime",
                _object({"timezone": _string(64, 1)}),
                _object({k: _string(64) for k in ("local", "timezone", "utc_offset", "utc", "weekday")},
                        ("local", "timezone", "utc_offset", "utc", "weekday")),
                "The current date and time, optionally in an IANA time zone such as Europe/Paris.",
                {}, lambda a, b: current_datetime(a)),
        Utility("calculate",
                _object({"expression": _string(EXPRESSION_LIMIT, 1)}, ("expression",)),
                _object({"result": _string(1100), "rounded": {"type": "boolean"}}, ("result", "rounded")),
                "Exact decimal arithmetic: numbers, + - * /, unary minus and parentheses.",
                {"expression": EXPRESSION_LIMIT}, lambda a, b: calculate(a)),
        Utility("date_arithmetic",
                _object({"operation": {"type": "string", "enum": ["add", "difference", "weekday"]},
                         "date": _string(10), "years": {"type": "integer", "minimum": -9999, "maximum": 9999},
                         "months": {"type": "integer", "minimum": -119988, "maximum": 119988},
                         "weeks": {"type": "integer", "minimum": -521775, "maximum": 521775},
                         "days": {"type": "integer", "minimum": -3652425, "maximum": 3652425},
                         "from": _string(10), "to": _string(10)}, ("operation",)),
                _object({"date": _string(10), "clamped": {"type": "boolean"},
                         "days": {"type": "integer", "minimum": -3652425, "maximum": 3652425},
                         "weekday": _string(16), "iso_weekday": {"type": "integer", "minimum": 1, "maximum": 7}}),
                "Date arithmetic on ISO YYYY-MM-DD dates. operation add: date plus years, months, weeks, days. "
                "operation difference: signed days from `from` to `to`. operation weekday: the weekday of date.",
                {}, lambda a, b: date_arithmetic(a)),
        Utility("find_replace",
                _object({"text": text, "find": _string(1000, 1), "replace": _string(1000),
                         "expected_count": {"type": "integer", "minimum": 1, "maximum": 100_000}},
                        ("text", "find", "replace")),
                _object({"text": _string(TEXT_CHARS), "count": {"type": "integer", "minimum": 0}},
                        ("text", "count")),
                "Replace every occurrence of `find` in `text` with `replace`: literal, case-sensitive, left to right "
                "without overlaps. count 0 is a result. With expected_count, a different count is refused as "
                "count_mismatch. No case-insensitive or whole-word mode.",
                {"text": TEXT_CHARS, "find": 1000, "replace": 1000}, find_replace),
        Utility("extract_section",
                _object({"markdown": text, "heading": _string(500, 1), "include_subsections": {"type": "boolean"}},
                        ("markdown", "heading")),
                _object({"found": {"type": "boolean"}, "matches": {"type": "integer", "minimum": 0},
                         "section": _string(TEXT_CHARS),
                         "level": {"type": ["integer", "null"], "minimum": 1, "maximum": 6},
                         "start_line": {"type": ["integer", "null"], "minimum": 1},
                         "end_line": {"type": ["integer", "null"], "minimum": 1}},
                        ("found", "matches", "section", "level", "start_line", "end_line")),
                "The Markdown section under a heading: from the heading line to the line before the next heading of "
                "the same or a higher level (any level without subsections). Heading text is compared after trimming "
                "and collapsing spaces, ignoring case; the first match wins and matches counts all. Only ATX (#) "
                "headings outside fenced code are recognised; Setext headings, indented code, HTML blocks, block "
                "quotes and lists are not. A heading that is not found gives found false.",
                {"markdown": TEXT_CHARS, "heading": 500}, extract_section),
        Utility("outline",
                _object({"markdown": text}, ("markdown",)),
                _object({"headings": {"type": "array", "maxItems": OUTLINE_LIMIT, "items": _object(
                            {"level": {"type": "integer", "minimum": 1, "maximum": 6}, "text": _string(TEXT_CHARS),
                             "line": {"type": "integer", "minimum": 1}}, ("level", "text", "line"))},
                         "truncated": {"type": "boolean"}}, ("headings", "truncated")),
                "The ATX (#) headings of a Markdown text with their levels and 1-based line numbers, outside fenced "
                "code; Setext headings, block quotes and lists are not recognised. At most 2,000 headings; truncated "
                "says when the list stopped early.",
                {"markdown": TEXT_CHARS, "headings": OUTLINE_LIMIT}, outline),
        Utility("compare_texts",
                _object({"before": text, "after": text, "context_lines": {"type": "integer", "minimum": 0,
                                                                          "maximum": 10}}, ("before", "after")),
                _object({"changed": {"type": "boolean"}, "diff": _string(TEXT_CHARS),
                         "added_lines": {"type": "integer", "minimum": 0},
                         "removed_lines": {"type": "integer", "minimum": 0}},
                        ("changed", "diff", "added_lines", "removed_lines")),
                "A line-based unified diff (--- before, +++ after) of two texts of at most 5,000 lines each, with "
                "context_lines of context (default 3). Differences only in line endings or the final newline are not "
                "reported. Texts that differ in too many lines are refused as too_large.",
                {"before": TEXT_CHARS, "after": TEXT_CHARS, "lines": DIFF_LINES, "work": DIFF_WORK}, compare_texts),
        Utility("combine_texts",
                _object({"parts": {"type": "array", "minItems": 1, "maxItems": 50, "items": {"type": "string"}},
                         "separator": _string(100),
                         "headings": {"type": "array", "minItems": 1, "maxItems": 50, "items": _string(200, 1)}},
                        ("parts",)),
                _object({"text": _string(TEXT_CHARS)}, ("text",)),
                "Join 1 to 50 texts with a separator (default a blank line). With headings, one per part, each part "
                "is preceded by '## <heading>' and a blank line.",
                {"parts": 50, "separator": 100, "heading": 200}, combine_texts),
        Utility("parse_csv",
                _object({"csv": text, "delimiter": {"type": "string", "enum": list(DELIMITERS)},
                         "has_header": {"type": "boolean"}}, ("csv",)),
                _object({"columns": _COLUMNS, "rows": _ROWS, "row_count": {"type": "integer", "minimum": 0}},
                        ("columns", "rows", "row_count")),
                "Parse CSV text into columns and rows of verbatim strings (never trimmed or converted). Standard "
                "quoting with doubled quotes; blank lines are skipped; a leading byte-order mark is removed. Without "
                "a header, columns are c1 to cN. At most 10,000 data rows, 200 columns and 10,000 characters a "
                "field. Errors name the record number.",
                {"csv": TEXT_CHARS, "rows": TABLE_ROWS, "columns": TABLE_COLUMNS, "cell": CELL_CHARS}, parse_csv),
        Utility("rows_to_table",
                _object({"columns": _COLUMNS, "rows": _ROWS}, ("columns", "rows")),
                _object({"markdown": _string(TEXT_CHARS)}, ("markdown",)),
                "Render columns and rows as a Markdown table. Backslashes and bars are escaped and line breaks "
                "become <br>. Every row must have one cell per column.",
                {"rows": TABLE_ROWS, "columns": TABLE_COLUMNS, "cell": CELL_CHARS}, rows_to_table),
        Utility("sort_dedupe_rows",
                _object({"columns": _COLUMNS, "rows": _ROWS,
                         "by": {"type": "array", "minItems": 1, "maxItems": 5, "items": _string(200, 1)},
                         "numeric": {"type": "boolean"}, "descending": {"type": "boolean"},
                         "dedupe": {"type": "boolean"}}, ("columns", "rows", "by")),
                _object({"columns": _COLUMNS, "rows": _ROWS, "removed": {"type": "integer", "minimum": 0}},
                        ("columns", "rows", "removed")),
                "Sort table rows by up to 5 columns (stable; text by code point, or as decimal numbers with numeric), "
                "optionally descending. Empty keys sort last either way. With dedupe, first keeps the first row for "
                "each exact combination of the by values.",
                {"rows": TABLE_ROWS, "columns": TABLE_COLUMNS, "by": 5}, sort_dedupe_rows),
        Utility("column_stats",
                _object({"columns": _COLUMNS, "rows": _ROWS, "column": _string(200, 1)},
                        ("columns", "rows", "column")),
                _object({"count": {"type": "integer", "minimum": 0},
                         "numeric_count": {"type": "integer", "minimum": 0},
                         "empty_count": {"type": "integer", "minimum": 0},
                         "other_count": {"type": "integer", "minimum": 0},
                         "sum": _DECIMAL_TEXT, "min": _DECIMAL_TEXT, "max": _DECIMAL_TEXT, "mean": _DECIMAL_TEXT,
                         "rounded": {"type": "boolean"}},
                        ("count", "numeric_count", "empty_count", "other_count", "sum", "min", "max", "mean",
                         "rounded")),
                "Count, sum, minimum, maximum and mean of one column's decimal values (exact decimal arithmetic, 34 "
                "digits). Empty cells and cells that are not plain decimal numbers are counted separately; the "
                "numbers are null when there are none.",
                {"rows": TABLE_ROWS, "columns": TABLE_COLUMNS}, column_stats),
        Utility("scan_identifiers",
                _object({"text": text, "kinds": {"type": "array", "minItems": 1, "maxItems": len(KINDS),
                                                 "items": {"type": "string", "enum": list(KINDS)}}}, ("text",)),
                _object({"findings": {"type": "array", "maxItems": FINDINGS_LIMIT, "items": _object(
                            {"kind": {"type": "string", "enum": list(KINDS)}, "value": _string(TEXT_CHARS),
                             "line": {"type": "integer", "minimum": 1}, "column": {"type": "integer", "minimum": 1}},
                            ("kind", "value", "line", "column"))},
                         "counts": _object({k: {"type": "integer", "minimum": 0} for k in KINDS}),
                         "truncated": {"type": "boolean"}}, ("findings", "counts", "truncated")),
                "Find identifier-like patterns: URLs, email addresses, IPv4 addresses (not IPv6), dates, phone "
                "numbers and long digit runs. It finds patterns, not names: an empty result is not proof that a text "
                "is de-identified. counts are exact totals; findings stop at 1,000 and truncated says so.",
                {"text": TEXT_CHARS, "findings": FINDINGS_LIMIT}, scan_identifiers),
        Utility("check_identifier_map",
                _object({"original": text, "text": text,
                         "pairs": {"type": "array", "maxItems": identifier_pairs.ENTRY_LIMIT,
                                   "items": {"type": "object"}}}, ("original", "text", "pairs")),
                _object({"text": _string(TEXT_CHARS),
                         "pairs": {"type": "array", "maxItems": identifier_pairs.ENTRY_LIMIT, "items": _PAIR},
                         "entries": {"type": "integer", "minimum": 0},
                         "unmapped_tokens": {"type": "array", "maxItems": UNMAPPED_LIMIT, "items": _string(TEXT_CHARS)},
                         "unmapped_truncated": {"type": "boolean"}},
                        ("text", "pairs", "entries", "unmapped_tokens", "unmapped_truncated")),
                "Check an identifier map locally before a Release Gate: it must parse as Restore placeholders does, "
                "be one-to-one, every original must occur in the source text, every placeholder in the de-identified "
                "text, and no placeholder in the source. Returns the text unchanged, the checked pairs and any "
                "bracket tokens in the text without a map entry. It does not look for originals left behind.",
                {"original": TEXT_CHARS, "text": TEXT_CHARS, "pairs": identifier_pairs.ENTRY_LIMIT},
                check_identifier_map),
    ]
    return {u.name: u for u in entries}


REGISTRY = _registry()
UTILITY_TOOLS = tuple(REGISTRY)
UTILITIES = {name: u.function for name, u in REGISTRY.items()}

# --- the document tools' schemas and descriptions (0.2.2, 6.1-6.4; addendum 3) --------------------------------------
DOCUMENT_SCHEMAS: dict[str, dict] = {
    "list_documents": {"type": "object", "properties": {}, "additionalProperties": False},
    "search_documents": {
        "type": "object",
        "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 200},
                       "document_id": {"type": "string", "maxLength": 64},
                       "max_hits": {"type": "integer", "minimum": 1, "maximum": 20},
                       "skip": {"type": "integer", "minimum": 0}},
        "required": ["query"], "additionalProperties": False},
    "read_document": {
        "type": "object",
        "properties": {"document_id": {"type": "string", "maxLength": 64},
                       "start_line": {"type": "integer", "minimum": 1},
                       "line_count": {"type": "integer", "minimum": 1, "maximum": 200},
                       "start_column": {"type": "integer", "minimum": 1}},
        "required": ["document_id", "start_line"], "additionalProperties": False},
    "create_draft": {
        "type": "object",
        "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 80},
                       "format": {"type": "string", "enum": ["md", "txt"]},
                       "content": {"type": "string", "maxLength": 200000}},
        "required": ["name", "format", "content"], "additionalProperties": False},
    "copy_to_draft": {
        "type": "object",
        "properties": {"source_id": {"type": "string", "maxLength": 64},
                       "start_line": {"type": "integer", "minimum": 1},
                       "end_line": {"type": "integer", "minimum": 1},
                       "name": {"type": "string", "minLength": 1, "maxLength": 80},
                       "format": {"type": "string", "enum": ["md", "txt"]},
                       "append_to": {"type": "string", "maxLength": 64}},
        "required": ["source_id", "start_line", "end_line", "name", "format"], "additionalProperties": False},
    "revise_draft": {
        "type": "object",
        "properties": {"source_id": {"type": "string", "maxLength": 64},
                       "edits": {"type": "array", "minItems": 1, "maxItems": 20, "items": {
                           "type": "object",
                           "properties": {"find": {"type": "string", "minLength": 1, "maxLength": 1000},
                                          "replace": {"type": "string", "maxLength": 10000},
                                          "expected_count": {"type": "integer", "minimum": 1, "maximum": 1000}},
                           "required": ["find", "replace"], "additionalProperties": False}},
                       "name": {"type": "string", "minLength": 1, "maxLength": 80},
                       "format": {"type": "string", "enum": ["md", "txt"]}},
        "required": ["source_id", "edits"], "additionalProperties": False},
    "compare_documents": {
        "type": "object",
        "properties": {"before_id": {"type": "string", "maxLength": 64},
                       "after_id": {"type": "string", "maxLength": 64},
                       "context_lines": {"type": "integer", "minimum": 0, "maximum": 10}},
        "required": ["before_id", "after_id"], "additionalProperties": False},
    "read_table": {
        "type": "object",
        "properties": {"document_id": {"type": "string", "maxLength": 64},
                       "start_row": {"type": "integer", "minimum": 1},
                       "max_rows": {"type": "integer", "minimum": 1, "maximum": 200},
                       "columns": {"type": "array", "minItems": 1, "maxItems": TABLE_COLUMNS,
                                   "items": {"type": "string", "minLength": 1, "maxLength": 200}}},
        "required": ["document_id"], "additionalProperties": False},
}

DOCUMENT_DESCRIPTIONS = {
    "list_documents": "List the documents and drafts attached to this conversation: id, name, kind, format, "
                      "lines and characters.",
    "search_documents": "Find lines that contain the query text (case-insensitive, literal). Optionally limit "
                        "to one document_id. Returns hits with document_id, line, column and a short excerpt; "
                        "pass next_skip as skip to continue.",
    "read_document": "Read lines of a document or draft by its id, from start_line (1-based), up to line_count "
                     "lines (default 40, at most 200). Long lines are returned in pieces: continue with "
                     "start_column and line_count 1.",
    "create_draft": "Create a new downloadable draft (md or txt) in this conversation. It never changes an "
                    "existing document or draft. Returns draft_id.",
    "copy_to_draft": "Copy lines start_line to end_line (1-based, inclusive) of a document or draft into a new "
                     "draft, verbatim. With append_to, the new draft starts with that draft's content. Nothing "
                     "existing changes. Prefer this to retyping text.",
    "revise_draft": "Create a new draft from a document or draft by applying literal, case-sensitive edits in "
                    "order; each later edit sees the earlier replacements. Each edit must match exactly "
                    "expected_count times (default 1), or nothing is created: add context to find to make it "
                    "unique. The source is never changed.",
    "compare_documents": "A line-based unified diff of two documents or drafts (at most 5,000 lines each). "
                         "Line-ending and final-newline differences are not reported.",
    "read_table": "Read rows of a .csv or .tsv document (header required) from start_row (1-based), up to "
                  "max_rows (default 50, at most 200), optionally only some columns. Continue from next_row; "
                  "row_count is given at the end of the table.",
}

SCHEMAS: dict[str, dict] = {**DOCUMENT_SCHEMAS, **{name: u.schema for name, u in REGISTRY.items()}}
DESCRIPTIONS: dict[str, str] = {**DOCUMENT_DESCRIPTIONS, **{name: u.description for name, u in REGISTRY.items()}}
EFFECTS = {"create_draft": "changes state", "copy_to_draft": "changes state", "revise_draft": "changes state"}


def run_utility(name: str, arguments: Mapping, *, result_limit: int = RESULT_LIMIT,
                check: Callable[[], None] | None = None) -> dict:
    """Run one utility on already schema-checked arguments. Raises ToolError.

    A Tool step uses the default 1 MiB result limit; agentic Chat passes its own limit and its Stop check. The
    work bounds apply here, so they hold on both routes."""
    utility = REGISTRY[name]
    size = len(jsonio.encode_exact(dict(arguments)))
    if size > ARGUMENT_BYTES:
        raise ToolError("too_large", message=f"the arguments are {size} bytes; the limit is {ARGUMENT_BYTES} bytes")
    bounds = Bounds(result_limit, check)
    result = utility.function(arguments, bounds)
    # The backstop: every utility has refused an oversized result before building it.
    size = len(jsonio.encode_exact(result))
    if size > result_limit:
        raise bounds.refuse(size)
    return result
