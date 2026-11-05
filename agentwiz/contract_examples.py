"""Illustrative starter contracts and synthetic example values (overhaul spec 16).

The single source for the editor's starter choices and the cross-task tests. Every function returns a fresh,
exact definition built from the same small user vocabulary; nothing here is a domain engine. The examples are
synthetic and illustrative: no real patient, employee or customer data, and not a regulatory or clinical
standard. A structural pass establishes neither correctness, completeness, privacy nor permission to act; the
"semantic_wrong" values pass their contract on purpose, and only separate example assertions or review reject
them. Standard library only; no HTTP or storage.
"""
from __future__ import annotations

import copy
from decimal import Decimal

ILLUSTRATIVE = "Illustrative only: not a regulatory, clinical or financial standard."


def _string(minimum=None, nullable=False, description=None):
    node = {"type": ["string", "null"] if nullable else "string"}
    if minimum is not None:
        node["minLength"] = minimum
    if description:
        node["description"] = description
    return node


def _choice(choices, nullable=False):
    return {"type": ["string", "null"] if nullable else "string",
            "enum": list(choices) + ([None] if nullable else [])}


def _group(properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def identifier_occurrences() -> dict:
    """De-identification support: where identifiers occur in a source. Offsets are code-point, half-open."""
    item = _group({
        "kind": _choice(["person_name", "record_id", "contact", "address", "date", "other"]),
        "value": _string(1),
        "source_id": _string(1),
        "start": {"type": "integer", "minimum": 0},
        "end": {"type": "integer", "minimum": 0},
    })
    return {"format": "json",
            "description": "Every identifier occurrence found in the source, with its exact text and "
                           "half-open code-point offsets. " + ILLUSTRATIVE,
            "schema": {"type": "array", "items": item}}


def clinical_observations() -> dict:
    """Observations as reported; no reference ranges or interpretation are invented."""
    item = _group({
        "parameter": _string(1),
        "status": _choice(["present", "not_found", "uncertain", "conflicting", "not_applicable"]),
        "value": {"type": ["number", "null"]},
        "raw_value": _string(nullable=True),
        "comparator": _choice(["eq", "lt", "le", "gt", "ge"], nullable=True),
        "unit": _string(nullable=True),
        "observed_at": _string(nullable=True),
        "source_id": _string(1),
    })
    return {"format": "json",
            "description": "Requested observations exactly as the source reports them. " + ILLUSTRATIVE,
            "schema": {"type": "array", "items": item}}


def classification() -> dict:
    """A scalar output: under a block's named envelope the value is the string itself."""
    return {"format": "json", "description": "The queue a message belongs in.",
            "schema": _choice(["routine", "urgent", "needs_review"])}


def invoice() -> dict:
    line = _group({"description": {"type": "string"}, "quantity": {"type": ["number", "null"]},
                   "amount": {"type": "number"}})
    return {"format": "json",
            "description": "One invoice's header and lines; amounts may be negative. Totals are not checked "
                           "arithmetically by the contract. " + ILLUSTRATIVE,
            "schema": _group({"invoice_id": _string(1),
                              "currency": _choice(["AUD", "NZD", "USD", "EUR", "GBP", "other"]),
                              "lines": {"type": "array", "items": line},
                              "total": {"type": "number"}})}


def action_items() -> dict:
    item = _group({"task": _string(1), "owner": _string(nullable=True), "due_raw": _string(nullable=True),
                   "due_date": _string(nullable=True), "source_id": _string(1)})
    return {"format": "json", "description": "Action items stated in a source, with owners and due dates as "
                                             "written. Dates are strings without date assertions.",
            "schema": {"type": "array", "items": item}}


STARTERS = {
    "identifier_occurrences": ("Identifier occurrences", identifier_occurrences),
    "clinical_observations": ("Clinical observations", clinical_observations),
    "classification": ("Classification", classification),
    "invoice": ("Invoice", invoice),
    "action_items": ("Action items", action_items),
}


def starter(identifier: str) -> dict:
    """A fresh definition for one starter. Raises KeyError for an unknown ID."""
    return STARTERS[identifier][1]()


def _examples() -> dict:
    """{starter: {"valid": [...], "invalid": [...], "semantic_wrong": [...]}}; synthetic values only."""
    source = "Synthetic note S1: Alex Example (record 004512) called on 2026-01-02."
    return {
        "identifier_occurrences": {
            "valid": [[{"kind": "person_name", "value": "Alex Example", "source_id": "S1", "start": 19, "end": 31},
                       {"kind": "record_id", "value": "004512", "source_id": "S1", "start": 40, "end": 46}], []],
            "invalid": [[{"kind": "name", "value": "Alex Example", "source_id": "S1", "start": 19, "end": 31}],
                        [{"kind": "record_id", "value": 4512, "source_id": "S1", "start": 40, "end": 46}]],
            # Well formed, but the offsets point at the wrong text: only a span check against the source finds it.
            "semantic_wrong": [[{"kind": "person_name", "value": "Alex Example", "source_id": "S1",
                                 "start": 0, "end": 12}]],
            "source": source,
        },
        "clinical_observations": {
            "valid": [[{"parameter": "systolic_bp", "status": "present", "value": 128, "raw_value": "128",
                        "comparator": "eq", "unit": "mmHg", "observed_at": "2026-01-02", "source_id": "S2"},
                       {"parameter": "hba1c", "status": "not_found", "value": None, "raw_value": None,
                        "comparator": None, "unit": None, "observed_at": None, "source_id": "S2"}]],
            "invalid": [[{"parameter": "systolic_bp", "status": "present", "value": "128", "raw_value": "128",
                          "comparator": "eq", "unit": "mmHg", "observed_at": None, "source_id": "S2"}],
                        [{"parameter": "systolic_bp", "status": "present"}]],
            # Structurally valid, but reports a value the source never stated.
            "semantic_wrong": [[{"parameter": "hba1c", "status": "present", "value": Decimal("6.1"),
                                 "raw_value": "6.1", "comparator": "eq", "unit": "%", "observed_at": None,
                                 "source_id": "S2"}]],
        },
        "classification": {"valid": ["urgent"], "invalid": ["URGENT", None, 1], "semantic_wrong": []},
        "invoice": {
            "valid": [{"invoice_id": "INV-0007", "currency": "AUD",
                       "lines": [{"description": "Widgets", "quantity": 2, "amount": Decimal("19.90")},
                                 {"description": "Credit", "quantity": None, "amount": Decimal("-5.00")}],
                       "total": Decimal("14.90")}],
            "invalid": [{"invoice_id": "INV-0007", "currency": "AUD", "lines": [], "total": "14.90"},
                        {"invoice_id": "", "currency": "AUD", "lines": [], "total": 0, "extra": 1}],
            # Structurally valid, but the total disagrees with the lines: deterministic arithmetic must check it.
            "semantic_wrong": [{"invoice_id": "INV-0007", "currency": "AUD",
                                "lines": [{"description": "Widgets", "quantity": 2, "amount": Decimal("19.90")}],
                                "total": Decimal("99.00")}],
        },
        "action_items": {
            "valid": [[{"task": "Send the synthetic report", "owner": None, "due_raw": "next Friday",
                        "due_date": None, "source_id": "S3"}], []],
            "invalid": [[{"task": "", "owner": None, "due_raw": None, "due_date": None, "source_id": "S3"}],
                        [{"task": "Send", "source_id": "S3"}]],
            "semantic_wrong": [],
        },
    }


def examples(identifier: str) -> dict:
    """Fresh synthetic examples for one starter."""
    return copy.deepcopy(_examples()[identifier])
