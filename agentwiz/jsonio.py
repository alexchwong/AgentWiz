"""The one JSON writer, and the file writer and strict reader for AgentWiz's own files
(techspec 7.3, R0-12).

Every HTTP response body and every stream event AgentWiz produces goes through `encode` or
`encode_line`. Non-finite floats (NaN, +inf, -inf), which browsers' JSON.parse rejects, are written
as null, and their locations are listed under the reserved top-level key `nonfinite_replaced`.

AgentWiz's own files (workspace files and the lock file) go through `encode_file` and
`decode_file`. The reader refuses anything AgentWiz could not write back unchanged, because files
are rewritten whole. Contract files, which hold exact numbers, go through `encode_file_exact` and
`decode_file_exact`; run files go through `encode_record_file` (techspec 0.1.3, 7.9). Standard library
only; knows nothing about HTTP.
"""
from __future__ import annotations

import codecs
import json
import math
import re
from decimal import Decimal, InvalidOperation
from collections.abc import Mapping
from typing import Any

RESERVED = "nonfinite_replaced"
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _key_segment(key: str) -> str:
    return "." + key if _IDENT.match(key) else "[" + json.dumps(key, ensure_ascii=True) + "]"


def _join(path: str) -> str:
    return path[1:] if path.startswith(".") else path


def _clean(value: Any, path: str, found: list[str]) -> Any:
    """Return a JSON-safe copy of value; never mutates the input."""
    if value is None or isinstance(value, (str, bool, int)):
        return value  # bool is an int subclass; ints are written exactly at any size
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        found.append(_join(path))
        return None
    if isinstance(value, Mapping):
        out = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(f"mapping keys must be str, got {type(k).__name__}")
            out[k] = _clean(v, path + _key_segment(k), found)
        return out
    if isinstance(value, (list, tuple)):
        return [_clean(v, f"{path}[{i}]", found) for i, v in enumerate(value)]
    raise TypeError(f"cannot encode {type(value).__name__} as JSON")


def encode(payload: Mapping[str, Any]) -> bytes:
    """Serialise a mapping as compact, ASCII-only, standard JSON."""
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a mapping")
    if RESERVED in payload:
        raise TypeError(f"payload must not contain the reserved key {RESERVED!r}")
    found: list[str] = []
    out = _clean(payload, "", found)
    if found:
        out[RESERVED] = found
    return json.dumps(out, allow_nan=False, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def encode_line(payload: Mapping[str, Any]) -> bytes:
    """encode(payload) followed by a newline: one NDJSON event."""
    return encode(payload) + b"\n"


# --- AgentWiz's own files ----------------------------------------------------------------------
def encode_file(payload: Mapping[str, Any]) -> bytes:
    """Indented, ASCII-only JSON with a trailing newline. Raises ValueError on a non-finite float."""
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a mapping")
    return (json.dumps(payload, allow_nan=False, ensure_ascii=True, indent=2) + "\n").encode("ascii")


class _Refused(ValueError):
    """A rule of decode_file was broken; the message is AgentWiz's own text."""


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise _Refused("an object repeats a key")
        out[key] = value
    return out


def _no_constants(name: str) -> Any:
    raise _Refused("NaN and Infinity are not allowed")


def _exact_float(text: str) -> float:
    value = float(text)
    try:
        exact = math.isfinite(value) and Decimal(repr(value)) == Decimal(text)
    except InvalidOperation:
        exact = False
    if not exact:
        raise _Refused("a number cannot be kept exactly when the file is rewritten")
    return value


def decode_file(data: bytes) -> dict:
    """Parse one of AgentWiz's own files strictly; returns the top-level object.

    Raises ValueError naming the rule broken, never the offending text.
    """
    if data.startswith(codecs.BOM_UTF8):
        data = data[len(codecs.BOM_UTF8):]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("the file is not UTF-8 text") from None
    try:
        value = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constants,
                           parse_float=_exact_float)
    except _Refused as exc:
        raise ValueError(str(exc)) from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON (line {exc.lineno}, column {exc.colno})") from None
    except ValueError:  # e.g. an integer with too many digits to convert
        raise ValueError("a number cannot be read") from None
    if not isinstance(value, dict):
        raise ValueError("the top level must be a JSON object")
    return value


# --- exact numbers for MCP traffic, tool arguments and tool results (techspec 0.1.2, 8.1) --------
INT_DIGITS_LIMIT = 4300
_JSON_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")


class JSONSyntax(ValueError):
    """decode_exact: the text is not valid JSON. `line` and `column` locate the first error (1-based)."""

    def __init__(self, line: int, column: int):
        super().__init__(f"not valid JSON (line {line}, column {column})")
        self.line, self.column = line, column


class ExactLimit(ValueError):
    """decode_exact: a number cannot be represented exactly (an integer of more than 4,300 digits, or a
    decimal whose exponent `Decimal` cannot construct). The message names the rule, never the text.
    `line` and `column` locate the first such number (1-based) when decode_exact could find it again in
    the text; the parser itself does not report where a number was."""

    def __init__(self, message: str, line: int | None = None, column: int | None = None):
        super().__init__(message)
        self.line, self.column = line, column


def _exact_int(text: str) -> int:
    # Counted here, not left to the interpreter's conversion limit, which Python 3.10.0-3.10.6 lack
    # and PYTHONINTMAXSTRDIGITS can lift (D49).
    if len(text.lstrip("-")) > INT_DIGITS_LIMIT:
        raise ExactLimit(f"an integer has more than {INT_DIGITS_LIMIT:,} digits")
    return int(text)


def _exact_decimal(text: str) -> Decimal:
    # Decimal("1e9223372036854775808") raises a bare InvalidOperation, which is not a ValueError.
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ExactLimit("a number's exponent is too large to be represented exactly") from None


def _no_constants_exact(name: str) -> Any:
    raise _Refused("NaN and Infinity are not allowed")


_STRING_OR_NUMBER = re.compile(r'"(?:[^"\\]|\\.)*"|-?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?', re.DOTALL)


def _locate_limit(text: str) -> tuple[int, int] | None:
    """Where the first number breaking a representation limit starts: (line, column), 1-based, counted
    as json.JSONDecodeError counts. Strings are skipped, so digits inside them are never mistaken for
    numbers. Only called after decode_exact has already refused the text."""
    for found in _STRING_OR_NUMBER.finditer(text):
        token = found.group()
        if token.startswith('"'):
            continue
        digits = re.match(r"-?([0-9]+)\Z", token)
        if digits:
            broken = len(digits.group(1)) > INT_DIGITS_LIMIT
        else:
            try:
                Decimal(token)
                broken = False
            except InvalidOperation:
                broken = True
        if broken:
            start = found.start()
            line = text.count("\n", 0, start) + 1
            return line, start - (text.rfind("\n", 0, start) + 1) + 1
    return None


def decode_exact(text: str) -> Any:
    """Parse JSON text keeping every number exactly: fractions and exponents become Decimal, integers
    int (at most 4,300 digits). Refuses NaN, Infinity and repeated keys.

    Raises only ValueError subclasses (techspec 0.1.3, 7.9, R4-31): `JSONSyntax` (with line and column)
    for malformed JSON, `ExactLimit` for a number it cannot represent exactly, and a plain ValueError
    naming the rule for everything else. Messages name the rule broken, never the offending text. No
    float is ever produced."""
    if not isinstance(text, str):
        raise ValueError("JSON text must be a string")
    try:
        return json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constants_exact,
                          parse_float=_exact_decimal, parse_int=_exact_int)
    except _Refused as exc:
        raise ValueError(str(exc)) from None
    except ExactLimit as exc:
        where = _locate_limit(text) if exc.line is None else None
        if where is not None:
            exc.line, exc.column = where
        raise
    except json.JSONDecodeError as exc:
        raise JSONSyntax(exc.lineno, exc.colno) from None
    except (ValueError, RecursionError):
        raise ValueError("a value cannot be read") from None


def decode_file_exact(data: bytes) -> dict:
    """Read a contract file: UTF-8 (a leading byte-order mark is allowed), then `decode_exact`; the top
    level must be an object. Raises ValueError (or its subclasses `JSONSyntax` and `ExactLimit`) naming
    the rule and, where known, the location; never the offending text."""
    if data.startswith(codecs.BOM_UTF8):
        data = data[len(codecs.BOM_UTF8):]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("the file is not UTF-8 text") from None
    value = decode_exact(text)
    if not isinstance(value, dict):
        raise ValueError("the top level must be a JSON object")
    return value


def _number_text(value: Any) -> str:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("a non-finite number cannot be written as JSON")
        text = str(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("a non-finite number cannot be written as JSON")
        text = repr(value)
    else:
        text = str(int(value))
    if not _JSON_NUMBER.match(text):
        raise ValueError("a number does not match JSON's number grammar")
    return text


def _write_exact(value: Any, out: list[str], indent: int | None, level: int) -> None:
    """Append `value` as JSON. With `indent` set, the layout is json.dumps(..., indent=indent)'s."""
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, str):
        out.append(json.dumps(value, ensure_ascii=True))
    elif isinstance(value, (int, float, Decimal)):
        out.append(_number_text(value))
    elif isinstance(value, Mapping):
        if not value:
            out.append("{}")
            return
        if indent is None:
            separator, colon, opener, closer = ",", ":", "{", "}"
        else:
            inner = "\n" + " " * (indent * (level + 1))
            separator, colon = "," + inner, ": "
            opener, closer = "{" + inner, "\n" + " " * (indent * level) + "}"
        out.append(opener)
        first = True
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(f"mapping keys must be str, got {type(k).__name__}")
            if not first:
                out.append(separator)
            first = False
            out.append(json.dumps(k, ensure_ascii=True))
            out.append(colon)
            _write_exact(v, out, indent, level + 1)
        out.append(closer)
    elif isinstance(value, (list, tuple)):
        if not value:
            out.append("[]")
            return
        if indent is None:
            separator, opener, closer = ",", "[", "]"
        else:
            inner = "\n" + " " * (indent * (level + 1))
            separator, opener, closer = "," + inner, "[" + inner, "\n" + " " * (indent * level) + "]"
        out.append(opener)
        for i, v in enumerate(value):
            if i:
                out.append(separator)
            _write_exact(v, out, indent, level + 1)
        out.append(closer)
    else:
        raise TypeError(f"cannot encode {type(value).__name__} as JSON")


def encode_exact(value: Any, indent: int | None = None) -> str:
    """ASCII-only JSON text; Decimal written as str(value), keeping its digits and exponent. Compact by
    default; with `indent`, the layout of json.dumps(..., indent=indent). Raises ValueError for a
    non-finite number, TypeError for an unsupported type."""
    out: list[str] = []
    _write_exact(value, out, indent, 0)
    return "".join(out)


def encode_file_exact(payload: Mapping[str, Any]) -> bytes:
    """A contract file: encode_exact(payload, indent=2) and a newline, ASCII-only. Refuses non-finite
    numbers (ValueError)."""
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a mapping")
    return (encode_exact(payload, indent=2) + "\n").encode("ascii")


def encode_record_file(payload: Mapping[str, Any]) -> bytes:
    """A run file: indented, ASCII-only JSON with R0-12's rule (non-finite floats written as null, their
    locations listed under `nonfinite_replaced`). Refuses Decimal (TypeError): records hold none."""
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a mapping")
    if RESERVED in payload:
        raise TypeError(f"payload must not contain the reserved key {RESERVED!r}")
    found: list[str] = []
    out = _clean(payload, "", found)
    if found:
        out[RESERVED] = found
    return (json.dumps(out, allow_nan=False, ensure_ascii=True, indent=2) + "\n").encode("ascii")
