"""Deterministic proposals for Phase 2's isolated Workshop tutorial stages."""
from __future__ import annotations

from .. import jsonio
from ..workshop_editor import SECTIONS, examples


def proposal(action):
    fields = [
        {'path': [], 'type': 'Group', 'required': True, 'nullable': False, 'description': 'An extracted order'},
        {'path': ['product'], 'type': 'Text', 'required': True, 'nullable': False, 'description': 'The ordered product'},
        {'path': ['quantity'], 'type': 'Whole number', 'required': False, 'nullable': False, 'description': 'Explicit quantity only'},
    ]
    behaviour = {'kind': 'model', 'instructions': 'Extract product and quantity from {{inputs.text}}. Omit quantity when unspecified.',
                 'system': None, 'role': 'fast', 'output': 'out', 'settings': {'max_tokens': '512'}, 'limits': {'max_repairs': '2'}}
    payloads = {
        'create': {'purpose': 'Extract a product and explicit quantity', 'inputs': [{'name': 'text', 'description': 'Order text', 'format': 'text', 'fields': []}],
                   'outputs': [{'name': 'out', 'description': 'Extracted order', 'format': 'json', 'fields': fields}], 'behaviour': behaviour, 'effects': 'read-only'},
        'improve': {'behaviour': behaviour},
        'describe': {'description_sections': {k: 'Extract explicit information. Missing quantities are omitted.' for k in SECTIONS}},
        'suggest_examples': {'examples': [{'label': 'Missing quantity', 'inputs': {'text': 'some blue notebooks'},
            'expectation': {'kind': 'fields', 'assertions': [{'output': 'out', 'path': ['quantity'], 'operation': 'absent'}]},
            'rationale': 'Distinguish omission from explicit null.'}]},
        'explain_failure': {'observations': [], 'hypotheses': ['The source did not specify a quantity.'], 'corrections': None},
    }
    return jsonio.encode_exact({'format_version': 1, 'action': action, 'payload': payloads[action],
                                'assumptions': [], 'unmet_requirements': []})
