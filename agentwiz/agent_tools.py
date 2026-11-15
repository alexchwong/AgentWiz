"""The seven built-in tools (0.2.2, section 6).

All seven run in-process, take no slot, perform no network I/O and have fixed schemas with
`additionalProperties: false`. The three utilities (`current_datetime`, `calculate`, `date_arithmetic`) can also
be bound to a v4 Tool block (6.8); the four document tools act only on the live conversation's allowlist and are
offered in agentic Chat only (F-10).

A utility returns a plain JSON object or raises `ToolError(code)`; codes are fixed and never carry a value.
Knows nothing of HTTP.
"""
from __future__ import annotations

import calendar
import datetime as dt
import os
from decimal import (Decimal, DivisionByZero, InvalidOperation, Overflow, Rounded, Inexact, Underflow,
                     Clamped, Subnormal, localcontext)
from typing import Any, Mapping

PREFIX = "aw_"
DOCUMENT_TOOLS = ("list_documents", "search_documents", "read_document", "create_draft")
UTILITY_TOOLS = ("current_datetime", "calculate", "date_arithmetic")
EXPRESSION_LIMIT = 500
DEPTH_LIMIT = 32
PRECISION = 34


class ToolError(Exception):
    """A built-in refused or failed. `code` is fixed text; `position` is a 1-based character position."""

    def __init__(self, code: str, position: int | None = None):
        super().__init__(code)
        self.code = code
        self.position = position

    def view(self) -> dict:
        out = {"code": self.code, "message": MESSAGES.get(self.code, "the built-in tool refused the request")}
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
}

# --- schemas -----------------------------------------------------------------------------------------------------
SCHEMAS: dict[str, dict] = {
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
    "current_datetime": {
        "type": "object", "properties": {"timezone": {"type": "string", "minLength": 1, "maxLength": 64}},
        "additionalProperties": False},
    "calculate": {
        "type": "object", "properties": {"expression": {"type": "string", "minLength": 1, "maxLength": EXPRESSION_LIMIT}},
        "required": ["expression"], "additionalProperties": False},
    "date_arithmetic": {
        "type": "object",
        "properties": {"operation": {"type": "string", "enum": ["add", "difference", "weekday"]},
                       "date": {"type": "string", "maxLength": 10},
                       "years": {"type": "integer", "minimum": -9999, "maximum": 9999},
                       "months": {"type": "integer", "minimum": -119988, "maximum": 119988},
                       "weeks": {"type": "integer", "minimum": -521775, "maximum": 521775},
                       "days": {"type": "integer", "minimum": -3652425, "maximum": 3652425},
                       "from": {"type": "string", "maxLength": 10},
                       "to": {"type": "string", "maxLength": 10}},
        "required": ["operation"], "additionalProperties": False},
}

DESCRIPTIONS = {
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
    "current_datetime": "The current date and time, optionally in an IANA time zone such as Europe/Paris.",
    "calculate": "Exact decimal arithmetic: numbers, + - * /, unary minus and parentheses.",
    "date_arithmetic": "Date arithmetic on ISO YYYY-MM-DD dates. operation add: date plus years, months, weeks, "
                       "days. operation difference: signed days from `from` to `to`. operation weekday: the "
                       "weekday of date.",
}


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
        ctx.prec = PRECISION
        ctx.Emax, ctx.Emin = 999, -999
        ctx.traps[Overflow] = True
        ctx.traps[DivisionByZero] = True
        ctx.traps[InvalidOperation] = True
        for flag in (Rounded, Inexact, Underflow, Clamped, Subnormal):
            ctx.traps[flag] = False
        ctx.clear_flags()
        try:
            value = _Parser(text).parse()
        except Overflow:
            raise ToolError("overflow") from None
        except (DivisionByZero, InvalidOperation):
            raise ToolError("division_by_zero") from None
        except RecursionError:
            raise ToolError("depth_limit") from None
        rounded = bool(ctx.flags[Inexact] or ctx.flags[Underflow])
    if value == 0:
        value = abs(value)                               # never "-0"
    return {"result": format(value, "f"), "rounded": rounded}


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


UTILITIES = {"current_datetime": current_datetime, "calculate": calculate, "date_arithmetic": date_arithmetic}


def run_utility(name: str, arguments: Mapping) -> dict:
    """Run one utility on already schema-checked arguments. Raises ToolError."""
    return UTILITIES[name](arguments)
