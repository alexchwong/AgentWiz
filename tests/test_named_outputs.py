"""Named block output acceptance and role-setting intent, independent of HTTP transport."""
from decimal import Decimal

import pytest

from agentwiz import checks, jsonio, output_protocol, roles, task_settings, templates
from agentwiz.model_step import ContractCopy, OutputPlan
from agentwiz import workshop_assist


def contracts():
    return {
        'text': checks.parse_contract({'format': 'text', 'checks': [{'type': 'contains', 'value': 'hello'}]}),
        'data': checks.parse_contract({'format': 'json', 'schema': {
            'type': 'object', 'properties': {'id': {'type': 'integer'}},
            'required': ['id'], 'additionalProperties': False}}),
    }


def test_named_outputs_exact_values_and_atomic_failure():
    declared = contracts()
    result, values = checks.check_named_answer(declared, '{"data":{"id":9007199254740993},"text":"hello"}')
    assert result['overall'] == 'pass'
    assert values == {'text': 'hello', 'data': {'id': 9007199254740993}}
    for answer in ['{"text":"hello"}', '{"text":"hello","data":{"id":"1"}}',
                   '{"text":"hello","data":{"id":1},"extra":0}',
                   '{"text":{},"data":{"id":1}}',
                   '{"text":"hello","text":"hello","data":{"id":1}}',
                   '{"text":"bye","data":{"id":1}}']:
        checked, value = checks.check_named_answer(declared, answer)
        assert checked['overall'] == 'fail'
        assert value is checks.NO_VALUE


def test_plan_roundtrip_and_instructions_exclude_check_definitions():
    declared = contracts()
    plan = OutputPlan(tuple((name, 'Description {{inputs.secret}}', ContractCopy.of(name, 1, contract))
                            for name, contract in declared.items()))
    saved = plan.view()
    restored = OutputPlan.from_record(jsonio.decode_exact(jsonio.encode_exact(saved)))
    assert restored.view() == saved
    text = output_protocol.instructions(restored.specs())
    assert '9007199254740993' not in text
    assert 'contains' not in text and '"checks"' not in text
    assert '"integer"' in text and 'Description {{inputs.secret}}' in text


def test_output_tokens_are_model_only_and_not_recursive():
    text, fills = templates.fill_model_text('{{inputs.source}} / {{output.data}}', 'prompt',
        {'source': '{{output.data}}'}, {'data': 'schema {{inputs.secret}}'})
    assert text == '{{output.data}} / schema {{inputs.secret}}'
    assert [f['source'] for f in fills] == ['inputs.source', 'output.data']
    assert templates.fill_text('{{output.data}}', 'script', {})[0] == '{{output.data}}'
    for invalid in ['{{output.}}', '{{ output.data }}', '{{output.data.child}}']:
        with pytest.raises(templates.TemplateError):
            templates.scan_model_text(invalid, 'prompt')
    with pytest.raises(templates.FillError):
        templates.fill_model_text('{{output.missing}}', 'prompt', {}, {})


def test_role_defaults_override_reset_and_zero():
    defaults = {'temperature': 0.7, 'reasoning': 'high'}
    inherited = task_settings.resolve_effective(task_settings.parse_task({}), (), defaults)
    assert inherited.task.temperature == defaults['temperature']
    assert inherited.sources == {'temperature': 'role_default', 'reasoning': 'role_default'}
    override = task_settings.resolve_effective(task_settings.parse_task({'temperature': 0}), ('reasoning',), defaults)
    assert override.task.temperature == 0 and override.task.reasoning == ''
    assert override.sources == {'temperature': 'block_override', 'reasoning': 'block_reset'}
    assert defaults == {'temperature': 0.7, 'reasoning': 'high'}


def test_roles_v3_keeps_defaults_and_unassigned_roles(tmp_path):
    definitions = {'fast': {'description': '', 'location': 'local',
                             'task_defaults': {'temperature': 0, 'reasoning': 'low'}},
                   'assistant': {'description': 'Unassigned', 'location': 'local'}}
    raw = roles.build_raw({'format_version': 2, 'note': 'retain'}, {'fast': 'profile'}, definitions)
    roles.write(tmp_path, raw)
    loaded = roles.read(tmp_path)
    assert loaded.problem is None and loaded.raw['format_version'] == 3
    assert loaded.definitions == definitions
    assert roles.build_raw(raw, {})['definitions'] == definitions
    assert roles.build_raw(raw, {})['note'] == 'retain'
    with pytest.raises(roles.RolesError):
        roles.parse_definitions({'bad': {'description': '', 'location': 'local', 'task_defaults': {'max_tokens': 1}}})


def test_assistance_settings_are_separate_from_authored_block():
    request = workshop_assist.request({'action': 'create', 'role': 'assistant', 'task': 'Extract facts',
        'settings_text': {'temperature': '0', 'reasoning': 'low'}, 'task_resets': []})
    step = jsonio.decode_exact(request['draft']['definition_text'])['steps'][0]
    assert step['task'] == {'max_tokens': 4096, 'temperature': 0, 'reasoning': 'low'}
    assert step['response_mode'] == 'named_json'
    reset = workshop_assist.request({'action': 'create', 'role': 'assistant', 'task_resets': ['reasoning']})
    assert jsonio.decode_exact(reset['draft']['definition_text'])['steps'][0]['task_resets'] == ['reasoning']