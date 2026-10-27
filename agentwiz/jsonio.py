"""The one JSON writer, and the file writer and strict reader for AgentWiz's own files
(techspec 7.3, R0-12).

Every HTTP response body and every stream event AgentWiz produces goes through `encode` or
`encode_line`. Non-finite floats (NaN, +inf, -inf), which browsers' JSON.parse rejects, are written
as null, and their locations are listed under the reserved top-level key `nonfinite_replaced`.

AgentWiz's own files (workspace files and the lock file) go through `encode_file` and
`decode_file`. The reader refuses anything AgentWiz could not write back unchanged, because files
are rewritten whole. Standard library only; knows nothing about HTTP.
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
