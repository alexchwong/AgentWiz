"""The named JSON envelope a model block returns, and the instructions that ask for it.

A declared output is exactly one top-level key of the answer object, and its contract describes only
the value under that key. This module owns the *wording*: one renderer produces both the generated
system section and the compact text an output placeholder inserts, so the two can never disagree. It
is pure and deterministic — no HTTP, no storage, no transport — and acceptance is not here:
`checks.check_named_answer` decides what an answer means.

Nothing beyond a name, an author description, a format and the declared schema is sent: no check
definitions, example data or saved names (0.2.0 decision 4).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Mapping

from . import checks, jsonio

MODE = "named_json"
RENDERER_VERSION = 1
BEGIN = "=== AgentWiz output protocol (generated; do not edit) ==="
END = "=== end output protocol ==="
NO_DESCRIPTION = "no description was given"
_SIMPLE_TYPES = ("object", "array", "string", "integer", "number", "boolean")


@dataclass(frozen=True)
class Spec:
    """One declared output, as the instructions describe it."""

    name: str
    description: str
    contract: checks.Contract

    def label(self) -> str:
        """The value's format in words: `text`, `JSON object`, or `JSON value`."""
        if self.contract.format == "text":
            return "text"
        schema = self.contract.schema
        if isinstance(schema, Mapping):
            declared = schema.get("type")
            if isinstance(declared, str) and declared in _SIMPLE_TYPES:
                return f"JSON {declared}"
            if isinstance(declared, list) and len(declared) == 2 and "null" in declared:
                other = next((t for t in declared if t != "null"), None)
                if other in _SIMPLE_TYPES:
                    return f"JSON {other} or null"
        return "JSON value"

    def purpose(self) -> str:
        text = (self.description or "").strip()
        return text if text else NO_DESCRIPTION

    def schema_text(self) -> str | None:
        """The complete declared schema, exactly as a reader would parse it, or None."""
        if self.contract.format != "json" or self.contract.schema is None:
            return None
        return jsonio.encode_exact(self.contract.schema)


def specs_for(declared: Iterable, captured: Mapping) -> tuple[Spec, ...]:
    """The specs of a block's outputs, in declaration order, from its captured contracts."""
    return tuple(Spec(item.name, item.description, captured[item.name].parsed) for item in declared)


def _value_rule(spec: Spec) -> str:
    if spec.contract.format == "text":
        return ("Value: a JSON string holding the complete text. Escape quotes, backslashes and line "
                "breaks as JSON requires, and put any formatting the task asks for inside that string. "
                "Do not send an object or an array here.")
    return (f"Value: the {spec.label().replace('JSON ', '')} itself, placed directly under its key — "
            "not a string containing JSON.")


def instructions(specs: Iterable[Spec]) -> str:
    """The generated section appended to a block's system instructions."""
    specs = tuple(specs)
    names = [spec.name for spec in specs]
    lines = [BEGIN,
             "Return exactly one JSON object and nothing else: no words before or after it, "
             "and no Markdown fence around it.",
             "Its top-level keys are exactly these output names, in any order, one value each:"]
    lines.extend(f"  {jsonio.encode_exact(name)}" for name in names)
    lines.extend(["Every key must be present. Do not add keys, rename them, or nest one output inside "
                  "another, and do not split one output across several keys.",
                  "Place each output's complete value directly under its own key. A value may be empty "
                  "only where its schema allows that."])
    for spec in specs:
        lines.extend(["", f"Output {jsonio.encode_exact(spec.name)} ({spec.label()})",
                      f"Purpose: {spec.purpose()}", _value_rule(spec)])
        schema = spec.schema_text()
        if schema is not None:
            lines.extend(["Schema (JSON Schema 2020-12); the value must satisfy it exactly:", schema])
    lines.extend(["", END])
    return "\n".join(lines)


def describe(spec: Spec) -> str:
    """What `{{output.<name>}}` inserts: the same declaration, in one line."""
    text = (f"`{spec.name}` ({spec.label()}, purpose: {spec.purpose()}) — returned under the envelope "
            f"key {jsonio.encode_exact(spec.name)}")
    schema = spec.schema_text()
    return f"{text}, matching: {schema}" if schema is not None else text


def identity(specs: Iterable[Spec]) -> dict:
    """What the generated instructions depend on, for a captured plan's freshness digest."""
    described = tuple(specs)
    return {"mode": MODE, "renderer": RENDERER_VERSION,
            "outputs": [{"name": spec.name,
                         "description": hashlib.sha256(spec.description.encode("utf-8")).hexdigest(),
                         "contract": hashlib.sha256(
                             jsonio.encode_exact(dict(spec.contract.raw)).encode("utf-8")).hexdigest()}
                        for spec in described]}


def digest(specs: Iterable[Spec]) -> str:
    """A stable digest of `identity`, so a plan can record exactly what it was rendered from."""
    return hashlib.sha256(jsonio.encode_exact(identity(specs)).encode("utf-8")).hexdigest()
