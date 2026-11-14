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
    draft = service.preview({'starter': 'extract_fields', 'key': DESCRIBE_FIXTURE, 'role': 'fast'})['draft']
    body = {'draft': draft, 'expected_identity': None}
    plan = service.plan('save', body)
    service.save({**body, 'expected_plan': plan['expected_plan'],
                  'confirmations': [{'kind': r['kind'], 'digest': r['digest']} for r in plan['required_confirmations']]})


def publish_fixture(service, draft, expected=None):
    body = {'draft': draft, 'expected_identity': expected}
    plan = service.plan('save', body)
    return service.save({**body, 'expected_plan': plan['expected_plan'],
                         'confirmations': plan['required_confirmations']})


def transform_fixture(service, key, operation, parameters=None, inputs=None):
    draft = service.preview({'starter': operation, 'starter_version': 2, 'key': key})['draft']
    if parameters is not None:
        draft = service.preview({'draft': draft, 'edit': {'kind': 'starter_parameters', 'parameters': parameters}})['draft']
    if inputs is not None:
        definition = jsonio.decode_exact(draft['definition_text'])
        definition['inputs'] = inputs
        draft['definition_text'] = jsonio.encode_exact(definition)
    return publish_fixture(service, draft)['durable_id']


def ensure_stop_fixtures(state):
    service = state.workshop
    if not (service.assets.path / 'tutorial_words').exists():
        transform_fixture(service, 'tutorial_words', 'count_text')
    _assign_role(state, 'tutorial_wait', 'slow-headers')
    if not (service.assets.path / 'tutorial_wait').exists():
        draft = service.preview({'starter': 'write_text', 'key': 'tutorial_wait', 'role': 'tutorial_wait'})['draft']
        definition = jsonio.decode_exact(draft['definition_text'])
        definition['inputs'] = {}
        definition['steps'][0]['prompt'] = {'text': 'Wait for Stop.'}
        draft['definition_text'] = jsonio.encode_exact(definition)
        publish_fixture(service, draft)


def _assign_role(state, role, model):
    profile = role.replace('_', '-')
    state.save_profile({'name': profile, 'kind': 'openai_compat', 'model': model}, state.view()['revision'])
    state.select_profile(None, state.view()['revision'])
    roles = state.view()['roles']
    definitions = {**roles['definitions'], role: {'description': 'Isolated tutorial stand-in', 'location': 'local'}}
    state.save_roles({**roles['map'], role: profile}, roles['revision'], definitions)


def ensure_workflow_fixture(state, backend_url):
    from .. import outbound_policy
    service = state.workshop
    _assign_role(state, 'tutorial_workflow', 'workflow-report')
    destination = {'kind': 'openai_compat', 'url': backend_url, 'model': 'workflow-report', 'provider': ''}
    trust = outbound_policy.view(state)
    if destination not in trust['grants']:
        outbound_policy.update(state, {'expected_revision': trust['revision'], 'destination': destination, 'trusted': True})
    if service.workflows.store.read('tutorial_report')[0] is not None:
        return
    text = {'contract': {'format': 'text'}, 'local_only': False}
    number = {'contract': {'format': 'json'}, 'local_only': False}
    words = transform_fixture(service, 'count_words', 'count_text')
    characters = transform_fixture(service, 'count_characters', 'count_text', {'unit': 'characters'})
    report = transform_fixture(service, 'format_counts', 'format_report',
                               {'template': 'Words: {{inputs.words}}; characters: {{inputs.characters}}'},
                               {'words': number, 'characters': number})
    key = 'tutorial_local_model'
    original = service.preview({'starter': 'write_text', 'key': key, 'role': 'tutorial_workflow'})['draft']
    first = publish_fixture(service, original)
    selected = service.preview({'key': key, 'revision': first['durable_id']})['draft']
    upgraded = service.preview({'draft': selected, 'edit': {'kind': 'upgrade_for_workflows'}})['draft']
    definition = jsonio.decode_exact(upgraded['definition_text'])
    definition['inputs']['text']['local_only'] = True
    definition['outputs']['out']['local_only'] = False
    upgraded['definition_text'] = jsonio.encode_exact(definition)
    model = publish_fixture(service, upgraded, upgraded['base_digest'])['durable_id']

    def result(node):
        return {'kind': 'node', 'node': node, 'port': 'out'}

    external = {'kind': 'input', 'port': 'text'}
    definition = {'name': 'tutorial_report', 'purpose': 'Count words and characters, format the report and inspect the trusted model stage.',
                  'inputs': {'text': text}, 'constants': {},
                  'outputs': {'report': {**text, 'binding': result('format_report')}},
                  'nodes': {'count_words': {'bindings': {'text': external}},
                            'count_characters': {'bindings': {'text': external}},
                            'format_report': {'bindings': {'words': result('count_words'), 'characters': result('count_characters')}},
                            'local_model': {'bindings': {'text': result('format_report')}}}}
    request = {'name': definition['name'], 'file_token': None, 'definition_text': jsonio.encode_exact(definition),
               'selections': {node: {'key': block, 'revision': revision} for node, block, revision in
                              [('count_words', 'count_words', words), ('count_characters', 'count_characters', characters),
                               ('format_report', 'format_counts', report), ('local_model', key, model)]},
               'examples': [{'id': 'two_words', 'version': 1, 'label': 'Two words', 'verified': True, 'inputs': {'text': 'one two'},
                             'expectation': {'kind': 'whole_output', 'values': {'report': 'Words: 2; characters: 7'}}}]}
    plan = service.workflows.plan('workflow_save', request)
    service.workflows.edit('workflow_save', request, {'request': request, 'expected_plan': plan['expected_plan']})



def ensure_release_fixture(state, backend_url):
    from .. import outbound_policy
    service = state.workshop
    _assign_role(state, 'tutorial_risk', 'release-risk')
    _assign_role(state, 'tutorial_release', 'workflow-report')
    destination = {'kind': 'openai_compat', 'url': backend_url, 'model': 'release-risk', 'provider': ''}
    grants = outbound_policy.view(state)
    if destination not in grants['grants']:
        outbound_policy.update(state, {'expected_revision': grants['revision'], 'destination': destination, 'trusted': True})
    if service.workflows.store.read('tutorial_release')[0] is not None:
        return
    draft = {'key': 'release_consumer', 'files': {}, 'definition_text': jsonio.encode_exact({
        'format_version': 4, 'name': 'release_consumer',
        'inputs': {'text': {'contract': {'format': 'text'}, 'local_only': False}},
        'outputs': {'out': {'contract': {'format': 'text'}, 'local_only': False}},
        'steps': [{'id': 'run', 'kind': 'model', 'role': 'tutorial_release', 'response_mode': 'named_json',
                   'prompt': {'text': '{{inputs.text}}'}}]})}
    draft = service.import_draft({'draft': draft})['draft']
    saved = publish_fixture(service, draft)
    text = {'contract': {'format': 'text'}, 'local_only': False}
    definition = {'name': 'tutorial_release', 'purpose': 'Review synthetic redacted text before releasing it.',
        'inputs': {'text': {**text, 'local_only': True}},
        'constants': {'mapping': {'value': [{'original': 'Alice', 'placeholder': '[P1]'}], 'local_only': True}},
        'outputs': {'out': {**text, 'binding': {'kind': 'node', 'node': 'consumer', 'port': 'out'}}},
        'nodes': {
            'review': {'gate': {'kind': 'release', 'title': 'Review synthetic candidate', 'contract': {'format': 'text'},
                                  'assessment': {'enabled': True, 'role': 'tutorial_risk'}},
                       'bindings': {'value': {'kind': 'input', 'port': 'text'}, 'scan': {'kind': 'constant', 'name': 'mapping'}}},
            'consumer': {'bindings': {'text': {'kind': 'node', 'node': 'review', 'port': 'released'}}}}}
    request = {'name': 'tutorial_release', 'file_token': None, 'definition_text': jsonio.encode_exact(definition),
               'selections': {'consumer': {'key': 'release_consumer', 'revision': saved['durable_id']}}}
    plan = service.workflows.plan('workflow_save', request)
    service.workflows.edit('workflow_save', request, {'request': request, 'expected_plan': plan['expected_plan']})
