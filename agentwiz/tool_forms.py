"""Form specifications from tool input schemas (techspec 0.1.2, Section 9.9; R2-10, R2-29).

The server, not the page, decides whether a tool's input schema gets a form and builds its
description, so the schema's numbers never pass through the page's JavaScript (P14). Anything the
form cannot express exactly opens the raw editor, with notes naming what AgentWiz does not check;
nothing is silently omitted. Standard library only; knows nothing about HTTP.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from .jsonio import encode_exact

SIMPLE = ("string", "number", "integer", "boolean")
_PROPERTY_KEYS = {"type", "enum", "default", "description", "title", "anyOf"}
_TOP_KEYS = {"type", "properties", "required", "additionalProperties", "title", "description", "$schema"}
_UNSUPPORTED_TOP = ("$ref", "oneOf", "allOf", "anyOf", "not", "patternProperties", "$defs", "definitions",
                    "if", "then", "else", "dependentSchemas", "dependentRequired", "unevaluatedProperties")


def _simple(schema: Any) -> tuple[str | None, str | None]:
    """(type, None) for a simple property schema, or (None, reason)."""
    if not isinstance(schema, Mapping):
        return None, "is not a schema object"
    for key in ("$ref", "oneOf", "allOf", "not", "const", "items", "properties", "patternProperties"):
        if key in schema:
            return None, f"uses {key}"
    t = schema.get("type")
    if t not in SIMPLE:
        return None, "is not a string, number, integer or boolean"
    if "enum" in schema:
        if t != "string" or not isinstance(schema["enum"], list) or any(not isinstance(v, str) for v in schema["enum"]):
            return None, "has an enum that is not a list of strings"
    return t, None


def _nullable(schema: Mapping) -> tuple[Mapping | None, bool, str | None]:
    """(inner simple schema, nullable, reason): unwraps `anyOf: [X, null]` and `type: [X, "null"]`."""
    if "anyOf" in schema:
        options = schema["anyOf"]
        if (isinstance(options, list) and len(options) == 2
                and sum(1 for o in options if isinstance(o, Mapping) and o.get("type") == "null"
                        and set(o) <= {"type"}) == 1):
            inner = next(o for o in options if not (isinstance(o, Mapping) and o.get("type") == "null"))
            return inner, True, None
        return None, False, "uses anyOf other than an optional value"
    t = schema.get("type")
    if isinstance(t, list):
        if len(t) == 2 and "null" in t and t[0] != t[1]:
            other = t[0] if t[1] == "null" else t[1]
            return {**schema, "type": other}, True, None
        return None, False, "has a list of types"
    return schema, False, None


def _default_text(t: str, value: Any) -> Any:
    if value is None:
        return None
    if t == "string":
        return value if isinstance(value, str) else None
    if t == "boolean":
        return value if isinstance(value, bool) else None
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    return encode_exact(value)


def form_spec(input_schema: Any) -> tuple[dict | None, list[str]]:
    """(form description or None, notes). `input_schema` is the schema as parsed by decode_exact."""
    notes: list[str] = []
    if not isinstance(input_schema, Mapping):
        return None, ["the input schema is not an object; edit the arguments as JSON"]
    top_type = input_schema.get("type")
    if top_type not in ("object", None) or (top_type is None and "properties" not in input_schema):
        return None, ["the input schema's top level is not an object schema"]
    for key in _UNSUPPORTED_TOP:
        if key in input_schema:
            return None, [f"the input schema uses {key}, which AgentWiz does not check"]
    properties = input_schema.get("properties", {})
    if not isinstance(properties, Mapping):
        return None, ["the input schema's properties are not an object"]
    required = input_schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(r, str) for r in required):
        return None, ["the input schema's required list is malformed"]
    for key in input_schema:
        if key not in _TOP_KEYS and key not in _UNSUPPORTED_TOP:
            notes.append(f"{key} (top level) is not checked by AgentWiz; the server checks it")
    fields = []
    for name, prop in properties.items():
        if not isinstance(prop, Mapping):
            return None, [f"{name} is not a schema object"]
        inner, nullable, reason = _nullable(prop)
        if reason:
            return None, [f"{name} {reason}; edit the arguments as JSON"]
        if nullable and name in required:
            return None, [f"{name} is required and may be null; the form cannot send null"]
        t, reason = _simple(inner)
        if reason:
            return None, [f"{name} {reason}; edit the arguments as JSON"]
        extra = sorted((set(prop) | set(inner)) - _PROPERTY_KEYS)
        if extra:
            notes.append(f"{name}: {', '.join(extra)} not checked by AgentWiz; the server checks them")
        default = prop.get("default", inner.get("default"))
        fields.append({"name": name,
                       "title": prop.get("title") if isinstance(prop.get("title"), str) else name,
                       "type": t, "required": name in required, "nullable": nullable,
                       "description": prop.get("description") if isinstance(prop.get("description"), str)
                       else (inner.get("description") if isinstance(inner.get("description"), str) else None),
                       "default": _default_text(t, default),
                       "enum": list(inner["enum"]) if "enum" in inner else None})
    return {"fields": fields, "notes": notes}, notes
