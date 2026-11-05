"""Exact draft edits preserve constraints and never bypass subset recognition."""
from decimal import Decimal

import pytest

from conftest import pinned

from agentwiz import checks, contract_schema, contract_service as service, jsonio


def source():
    return jsonio.encode_exact({'format': 'json', 'description': 'original', 'schema': {
        'type': 'object', 'properties': {
            'amount': {'type': 'number', 'minimum': Decimal('0.12345678901234567890123456789')},
            'labels': {'type': 'array', 'maxItems': 2, 'items': {
                'type': ['string', 'null'], 'enum': ['yes', 'no', None]}}},
        'required': ['amount'], 'additionalProperties': True}})


def test_targeted_edits_preserve_exact_siblings_and_group_policy():
    original = source()
    result = service.apply_edit(original, {'op': 'set_description', 'path': ['labels', '[]'],
                                           'description': 'choice'})
    obj = jsonio.decode_exact(result['definition_text'])
    before = jsonio.decode_exact(original)
    assert obj['schema']['properties']['amount'] == before['schema']['properties']['amount']
    assert obj['schema']['additionalProperties'] is True
    assert obj['schema']['required'] == ['amount']
    assert obj['schema']['properties']['labels']['items']['enum'] == ['yes', 'no', None]
    assert result['fields'][0]['additional_properties'] is True
    assert result['fields'][1]['minimum'] == '0.12345678901234567890123456789'
    result = service.apply_edit(result['definition_text'], {
        'op': 'set_bound', 'path': ['amount'], 'keyword': 'maximum', 'value_text': '9007199254740993.001'})
    assert jsonio.decode_exact(result['definition_text'])['schema']['properties']['amount']['maximum'] == Decimal('9007199254740993.001')


def test_property_operations_keep_order_membership_and_subtrees():
    result = service.apply_edit(source(), {'op': 'rename_property', 'path': ['amount'], 'name': 'total'})
    node = jsonio.decode_exact(result['definition_text'])['schema']
    assert list(node['properties']) == ['total', 'labels'] and node['required'] == ['total']
    result = service.apply_edit(result['definition_text'], {'op': 'add_property', 'path': [], 'name': 'note',
        'node_text': '{"type":"string","minLength":1}', 'required': True})
    result = service.apply_edit(result['definition_text'], {'op': 'set_required', 'path': ['labels'], 'value': True})
    result = service.apply_edit(result['definition_text'], {'op': 'remove_property', 'path': ['total']})
    node = jsonio.decode_exact(result['definition_text'])['schema']
    assert list(node['properties']) == ['labels', 'note'] and node['required'] == ['note', 'labels']
    assert node['properties']['labels']['items']['enum'] == ['yes', 'no', None]


def test_nullable_choices_and_explicit_subtree_replacement():
    result = service.apply_edit(source(), {'op': 'set_nullable', 'path': ['labels', '[]'], 'value': False})
    result = service.apply_edit(result['definition_text'], {'op': 'set_choices', 'path': ['labels', '[]'], 'choices': ['', 'ok']})
    result = service.apply_edit(result['definition_text'], {'op': 'set_nullable', 'path': ['labels', '[]'], 'value': True})
    node = jsonio.decode_exact(result['definition_text'])['schema']['properties']['labels']['items']
    assert node == {'type': ['string', 'null'], 'enum': ['', 'ok', None]}
    result = service.apply_edit(result['definition_text'], {'op': 'set_node', 'path': ['labels'],
                                                           'node_text': '{"type":"boolean"}'})
    node = jsonio.decode_exact(result['definition_text'])['schema']
    assert node['properties']['labels'] == {'type': 'boolean'} and node['required'] == ['amount']
    result = service.apply_edit(result['definition_text'], {'op': 'set_additional_properties', 'path': [], 'value': False})
    assert jsonio.decode_exact(result['definition_text'])['schema']['additionalProperties'] is False


def test_text_rules_and_unconstrained_projection():
    result = service.apply_edit('{"format":"text","max_length":5}', {
        'op': 'set_text_rules', 'rules': {'min_length': '2.0', 'exact': 'é\n'}})
    assert result['rules'] == {'min_length': '2.0', 'exact': 'é\n'}
    assert 'max_length' not in jsonio.decode_exact(result['definition_text'])
    assert service.project('{"format":"json"}')['warnings'][0]['code'] == 'unconstrained_json'


@pytest.mark.parametrize('edit', [
    {'op': 'set_bound', 'path': ['amount'], 'keyword': 'maxLength', 'value_text': '1'},
    {'op': 'set_bound', 'path': ['amount'], 'keyword': 'minimum', 'value_text': 0.1},
    {'op': 'set_bound', 'path': ['labels'], 'keyword': 'maxItems', 'value_text': 'true'},
    {'op': 'set_required', 'path': [], 'value': False},
    {'op': 'set_required', 'path': ['labels', '[]'], 'value': False},
    {'op': 'set_nullable', 'path': ['amount'], 'value': 1},
    {'op': 'set_choices', 'path': ['labels', '[]'], 'choices': ['x', None]},
    {'op': 'set_node', 'path': [], 'node_text': '{"type":"string","pattern":"x"}'},
    {'op': 'rename_property', 'path': ['amount'], 'name': 'labels'},
    {'op': 'set_description', 'path': ['missing'], 'description': 'x'},
    {'op': 'set_description', 'path': None, 'description': 'x', 'policy': 'internal'},
])
def test_invalid_edits_are_refused_without_changing_source(edit):
    text = source()
    with pytest.raises(contract_schema.DefinitionError):
        service.apply_edit(text, edit)
    assert text == source()


@pytest.mark.parametrize('text', [
    '{"format":"json","format":"text"}', '{"format":"json"} trailing',
    '{"format":"json","schema":{"type":"number","minimum":NaN}}',
    '{"format":"text","exact":"\\ud800"}',
])
def test_advanced_source_uses_entire_exact_definition(text):
    with pytest.raises(contract_schema.DefinitionError):
        service.project(text)


@pytest.mark.parametrize('a,b,equal', [
    (True, 1, False), ({'x': False}, {'x': 0}, False),
    ({'a': 1, 'b': [Decimal('0.10')]}, {'b': [Decimal('0.1')], 'a': Decimal('1.0')}, True),
    ([1, 2], [2, 1], False), ({'x': None}, {}, False),
    (Decimal('9007199254740993.001'), Decimal('9007199254740993.002'), False),
])
def test_exact_expectations_do_not_construct_runtime_contracts(a, b, equal, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('equality must not construct a runtime contract')
    monkeypatch.setattr(checks, 'parse_contract', forbidden)
    from agentwiz.workshop_service import exact_equal
    assert checks.exact_equal(a, b) is equal
    assert exact_equal(a, b) is equal


def test_http_draft_operations_are_strict_exact_and_do_not_persist(ui):
    from conftest import request, failure_of
    result = request(ui, '/api/contracts/project', {'format_version': 2, 'definition_text': source()})
    assert result['format_version'] == 2 and result['fields'][1]['minimum'] == '0.12345678901234567890123456789'
    changed = request(ui, '/api/contracts/edit', {'format_version': 2, 'definition_text': source(),
        'edit': {'op': 'set_description', 'path': None, 'description': 'updated'}})
    assert changed['description'] == 'updated' and changed['digest'] != result['digest']
    assert not (ui.workspace.path / 'contracts').exists()
    for body in ({'format_version': True, 'definition_text': source()},
                 {'format_version': 1, 'definition_text': source()},
                 {'format_version': 2, 'definition_text': source(), 'policy': 'internal'}):
        failure = failure_of(lambda: request(ui, '/api/contracts/project', body))
        assert failure.status == 400 and failure.body['code'] == 'invalid'
    failure = failure_of(lambda: request(ui, '/api/contracts/project', {'format_version': 2,
        'definition_text': '{"format":"json","schema":{"type":"string","pattern":"x"}}'}))
    assert failure.status == 400 and failure.body['code'] == 'unsupported_keyword'


def test_rich_internal_parser_is_separate_and_disallows_legacy_members():
    definition = {'format': 'json', 'schema': {'oneOf': [{'type': 'integer'}, {'const': 'unknown'}]}}
    internal = checks.parse_internal_contract(definition)
    assert checks.check_value(internal, Decimal('2.0'))['overall'] == 'pass'
    assert checks.check_value(internal, 'unknown')['overall'] == 'pass'
    assert checks.check_value(internal, False)['overall'] == 'fail'
    with pytest.raises(contract_schema.DefinitionError):
        service.project(jsonio.encode_exact(definition))
    for field, value in (('strict', False), ('checks', []), ('policy', 'internal')):
        with pytest.raises(checks.ContractError):
            checks.parse_internal_contract({**definition, field: value})
    with pytest.raises(checks.ContractError):
        checks.parse_internal_contract({'format': 'json', 'schema': {'$ref': 'https://example.invalid/schema'}})


def test_applied_assistance_cannot_broaden_user_contracts():
    from agentwiz import workshop_assist
    from agentwiz.workshop import WorkshopError
    response = {'action': 'improve', 'payload': {'outputs': [{'name': 'out', 'format': 'json',
        'description': '', 'fields': [{'path': [], 'type': 'string', 'description': '',
        'required': True, 'nullable': False, 'enum': [], 'constraints': {'pattern': 'x'}}]}]},
        'assumptions': [], 'unmet_requirements': []}
    with pytest.raises(WorkshopError):
        workshop_assist.apply({'draft': {'definition_text': '{"inputs":{},"outputs":{},"steps":[]}',
                                        'metadata': {}}}, response)


def test_schema_save_refuses_missing_validator_without_creating_file(ui, monkeypatch):
    from conftest import failure_of, save_contract
    monkeypatch.setattr(checks, 'schema_support', lambda: (False, 'damaged'))
    failure = failure_of(lambda: save_contract(ui, 'number', '{"format":"json","schema":{"type":"integer"}}'))
    assert failure.status == 503 and failure.body['code'] == 'validation_unavailable'
    assert not (ui.workspace.path / 'contracts' / 'number.json').exists()


@pytest.mark.parametrize('definition, value, outcome', [
    ({'format': 'text', 'exact': ' x '}, ' x ', 'pass'),
    ({'format': 'text', 'exact': ' x '}, 'x', 'fail'),
    ({'format': 'text', 'min_length': 2}, 'é\n', 'pass'),
    ({'format': 'text', 'max_length': 1}, ' x ', 'fail'),
    ({'format': 'text', 'contains': ['required']}, 'private sample', 'fail'),
    ({'format': 'text', 'not_contains': ['private']}, 'private sample', 'fail'),
    ({'format': 'json', 'schema': {'type': 'integer'}}, '2.0', 'pass'),
    ({'format': 'json', 'schema': {'type': 'integer'}}, 'true', 'fail'),
    ({'format': 'json'}, 'before\n```json\n[1]\n```\nafter', 'pass'),
])
def test_v2_sample_check_has_fixed_exact_policy(ui, definition, value, outcome):
    from conftest import request
    response = request(ui, '/api/contracts/check', {'format_version': 2,
        'definition_text': jsonio.encode_exact(definition), 'value_text': value})
    assert response['format_version'] == 2 and response['check']['overall'] == outcome
    if definition['format'] == 'text':
        assert all(i['check'].startswith('text.') for i in response['check']['checks'])
        assert all('private sample' not in i['message'] for i in response['check']['issues'])
    assert not (ui.workspace.path / 'contracts').exists()


def test_sample_check_validator_failure_is_not_a_pass(ui, monkeypatch):
    from conftest import request, failure_of
    monkeypatch.setattr(checks, 'schema_support', lambda: (False, 'damaged'))
    failure = failure_of(lambda: request(ui, '/api/contracts/check', {'format_version': 2,
        'definition_text': '{"format":"json","schema":{"type":"string"}}', 'value_text': '"x"'}))
    assert failure.status == 503 and failure.body['code'] == 'validation_unavailable'
    assert service.check('{"format":"json"}', '1')['check']['overall'] == 'pass'
    assert service.check('{"format":"text"}', 'x')['check']['overall'] == 'pass'


@pytest.mark.parametrize('value', [None, True, 1, [], {}])
def test_text_nonstring_is_a_located_failure(value):
    result = checks.check_value(checks.parse_contract({'format': 'text'}), value)
    assert result['overall'] == 'fail'
    assert result['issues'][0]['check'] == 'text.type'


def test_renderer_uses_captured_rules_descriptions_and_exact_schema():
    from agentwiz import output_protocol
    text = checks.Contract('text', False, 'contract purpose', None, (), {
        'format': 'text', 'min_length': 1, 'max_length': 5, 'exact': 'é\n',
        'contains': ['é'], 'not_contains': ['private']})
    instructions = output_protocol.instructions([output_protocol.Spec('out', 'port purpose', text)])
    assert 'Purpose: port purpose' in instructions and 'Contract description: contract purpose' in instructions
    assert 'Minimum code-point count: 1' in instructions and 'Maximum code-point count: 5' in instructions
    assert 'Exact text: "\\u00e9\\n"' in instructions
    assert 'Required literal: "\\u00e9"' in instructions and 'Forbidden literal: "private"' in instructions
    changed = checks.Contract('text', False, text.description, None, (), {**text.raw, 'min_length': 2})
    assert output_protocol.digest([output_protocol.Spec('out', '', text)]) != output_protocol.digest([
        output_protocol.Spec('out', '', changed)])
    exact = checks.parse_internal_contract({'format': 'json', 'schema': {'type': 'number',
        'minimum': Decimal('9007199254740993.0000000000000000001')}})
    rendered = output_protocol.single_value_instructions(exact)
    assert '9007199254740993.0000000000000000001' in rendered
    assert 'top-level keys' not in rendered
    blank = output_protocol.describe(output_protocol.Spec('value', '', checks.parse_contract({'format': 'json'})))
    assert 'Purpose:' not in blank and 'no description was given' not in blank
    assert 'Any JSON — no field validation' in blank


def test_rich_internal_capture_roundtrip_retains_policy():
    from agentwiz.model_step import ContractCopy, OutputPlan
    parsed = checks.parse_internal_contract({'format': 'json', 'schema': {'oneOf': [
        {'type': 'integer'}, {'const': 'unknown'}]}})
    captured = pinned(None, None, parsed)
    plan = OutputPlan((('proposal', '', captured),))
    restored = OutputPlan.from_record(plan.view())
    assert restored.outputs[0][2].parsed.policy == 'internal'
    assert checks.check_named_answer(restored.contracts(), '{"proposal":"unknown"}')[0]['overall'] == 'pass'
    from agentwiz.model_step import SnapshotUnavailable
    with pytest.raises(SnapshotUnavailable):
        ContractCopy.from_record({**captured.view(), 'policy': 'arbitrary'})
    with pytest.raises(SnapshotUnavailable):                                   # an old record: no snapshot fields
        ContractCopy.from_record({'name': None, 'revision': None, 'text': captured.text})
    with pytest.raises(SnapshotUnavailable):                                   # redaction changed the text
        ContractCopy.from_record({**captured.view(), 'text': captured.text.replace('unknown', '[REDACTED]')})


def test_new_group_compilation_preserves_extra_properties_and_rejects_bad_choices():
    from agentwiz import workshop_editor
    from agentwiz.workshop import WorkshopError
    root = {'path': [], 'type': 'Group', 'required': True, 'nullable': False,
            'description': '', 'additional_properties': True}
    choice = {'path': ['label'], 'type': 'Choice', 'required': False, 'nullable': True,
              'choices': ['x'], 'min_length': '1'}
    node = workshop_editor.compile_rows([root, choice])
    assert node['additionalProperties'] is True
    assert node['properties']['label']['enum'] == ['x', None]
    with pytest.raises(WorkshopError):
        workshop_editor.compile_rows([root, {**choice, 'min_length': '2'}])