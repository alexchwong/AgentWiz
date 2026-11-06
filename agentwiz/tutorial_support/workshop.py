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
                 'system': None, 'role': 'fast', 'settings': {'max_tokens': '512'}, 'limits': {'max_repairs': '2'}}
    payloads = {
        'create': {'purpose': 'Extract a product and explicit quantity', 'inputs': [{'name': 'text', 'description': 'Order text', 'format': 'text', 'fields': []}],
                   'outputs': [{'name': 'out', 'description': 'Extracted order', 'format': 'json', 'fields': fields}], 'behaviour': behaviour, 'effects': 'read-only'},
        'improve': {'behaviour': behaviour},
        # The Describe envelope (block_metadata_refactor spec v2 4.3) for the managed tutorial fixture, whose
        # extract_fields starter declares exactly the input `text` and the output `out`.
        'describe': {'sections': {k: ('Extract explicit information. Missing quantities are omitted.'
                                      if k in ('use', 'missing_data') else '') for k in SECTIONS},
                     'port_summaries': {'inputs': {'text': 'The order text to read.'},
                                        'outputs': {'out': 'The product and any explicit quantity.'}}},
        'suggest_examples': {'examples': [{'label': 'Missing quantity', 'inputs': {'text': 'some blue notebooks'},
            'expectation': {'kind': 'fields', 'assertions': [{'output': 'out', 'path': ['quantity'], 'operation': 'absent'}]},
            'rationale': 'Distinguish omission from explicit null.'}]},
        'explain_failure': {'observations': [], 'hypotheses': ['The source did not specify a quantity.'], 'corrections': None},
    }
    if action == 'describe':
        return jsonio.encode_exact({'format_version': 1, 'action': 'describe', 'payload': payloads[action]})
    return jsonio.encode_exact({'format_version': 1, 'action': action, 'payload': payloads[action],
                                'assumptions': [], 'unmet_requirements': []})


DESCRIBE_FIXTURE = 'tutorial_described'


def ensure_describe_fixture(service):
    """Publish the saved managed fixture the Describe topic opens, once per tutorial session."""
    root = service.assets.path / DESCRIBE_FIXTURE
    if root.exists():
        return
    from ..workshop_editor import starter
    draft = starter('extract_fields', DESCRIBE_FIXTURE, 'fast', None, None)
    body = {'draft': draft, 'expected_identity': None}
    plan = service.plan('save', body)
    service.save({**body, 'expected_plan': plan['expected_plan'],
                  'confirmations': [{'kind': r['kind'], 'digest': r['digest']} for r in plan['required_confirmations']]})
