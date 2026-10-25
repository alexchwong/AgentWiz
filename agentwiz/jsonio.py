"""The one JSON writer (techspec 7.3, R0-12).

Every HTTP response body and every stream event AgentWiz produces goes through `encode` or
`encode_line`. Non-finite floats (NaN, +inf, -inf), which browsers' JSON.parse rejects, are written
as null, and their locations are listed under the reserved top-level key `nonfinite_replaced`.
Standard library only; knows nothing about HTTP.
"""
from __future__ import annotations

import json
import math
import re
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
