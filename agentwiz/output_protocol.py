"""The named JSON envelope a model block returns, and the instructions that ask for it.

A declared output is exactly one top-level key of the answer object, and its contract describes only
the value under that key. This module owns the *wording*: one renderer produces both the generated
system section and the compact text an output placeholder inserts, so the two can never disagree. It
is pure and deterministic — no HTTP, no storage, no transport — and acceptance is not here:
`checks.check_named_answer` decides what an answer means.

Only captured value requirements are rendered: names, descriptions, schemas and Text rules;
no examples, saved names, histories or hidden context.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Mapping

from . import checks, contract_schema, jsonio

MODE = "named_json"
RENDERER_VERSION = 2
BEGIN = "=== AgentWiz output protocol (generated; do not edit) ==="
END = "=== end output protocol ==="
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
        return text

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


def definition_lines(contract: checks.Contract, purpose: str = '') -> list[str]:
    """The same captured one-value requirements for blocks and Checked request."""
    lines = []
    if purpose:
        lines.append('Purpose: ' + purpose)
    if contract.description:
        lines.append('Contract description: ' + contract.description)
    lines.append('Value requirements:')
    if contract.format == 'json':
        if contract.schema is None:
            lines.append('Any JSON — no field validation.')
        else:
            lines.extend(['Schema (JSON Schema 2020-12); the value must satisfy it exactly:',
                          jsonio.encode_exact(contract.schema)])
    else:
        lines.append('Text; literal comparisons and lengths use the decoded string without normalization.')
        raw = contract.raw
        for key, phrase in (('min_length', 'Minimum code-point count'),
                            ('max_length', 'Maximum code-point count'), ('exact', 'Exact text')):
            if key in raw:
                lines.append(phrase + ': ' + jsonio.encode_exact(raw[key]))
        for key, phrase in (('contains', 'Required literal'), ('not_contains', 'Forbidden literal')):
            for literal in raw.get(key, []):
                lines.append(phrase + ': ' + jsonio.encode_exact(literal))
    return lines


def single_value_instructions(contract: checks.Contract) -> str:
    """A direct value, never the named block transport envelope."""
    reply = ('Return plain text directly, not a JSON string or output-name wrapper.'
             if contract.format == 'text' else
             'Return the JSON value directly, without Markdown fences or an output-name wrapper.')
    return '\n'.join([BEGIN, reply,
                      *definition_lines(contract), END])


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
        lines.extend(["", f"Output {jsonio.encode_exact(spec.name)} ({spec.label()})", _value_rule(spec),
                      *definition_lines(spec.contract, spec.description)])
    lines.extend(["", END])
    return "\n".join(lines)


def describe(spec: Spec) -> str:
    """What `{{output.<name>}}` inserts: the same declaration, in one line."""
    text = (f"`{spec.name}` ({spec.label()}) — returned under the envelope "
            f"key {jsonio.encode_exact(spec.name)}")
    return text + '\n' + '\n'.join(definition_lines(spec.contract, spec.description))


def identity(specs: Iterable[Spec]) -> dict:
    """What the generated instructions and acceptance depend on, for a captured plan's freshness digest: the
    renderer version, each output's canonical contract digest, policy and the exact rendered requirements."""
    described = tuple(specs)
    return {"mode": MODE, "renderer": RENDERER_VERSION,
            "outputs": [{"name": spec.name,
                         "description": hashlib.sha256(spec.description.encode("utf-8")).hexdigest(),
                         "contract": contract_schema.contract_digest(spec.contract.raw),
                         "policy": spec.contract.policy, "validator": spec.contract.validator,
                         "rendered": hashlib.sha256("\n".join(
                             definition_lines(spec.contract, spec.description)).encode("utf-8")).hexdigest()}
                        for spec in described]}


def digest(specs: Iterable[Spec]) -> str:
    """A stable digest of `identity`, so a plan can record exactly what it was rendered from."""
    return hashlib.sha256(jsonio.encode_exact(identity(specs)).encode("utf-8")).hexdigest()
