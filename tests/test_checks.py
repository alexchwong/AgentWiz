"""User value validation, fixed answer parsing and server-only rich-schema checks."""
import importlib
import json
import socket
from decimal import Decimal

import pytest

from agentwiz import checks, jsonio


def contract(spec):
    return checks.contract_from_text(spec if isinstance(spec, str) else jsonio.encode_exact(spec))


def run(spec, answer):
    return checks.check_answer(contract(spec), answer)


def entry(result, name):
    return next(e for e in result['checks'] if e['check'] == name)


@pytest.mark.parametrize('rules,text,passes', [
    ({'exact': 'Hello\nworld'}, 'Hello\nworld', True),
    ({'exact': 'Hello\nworld'}, ' Hello\r\nworld\n', False),
    ({'exact': ''}, '', True), ({'exact': ''}, ' ', False),
    ({'contains': ['wor']}, 'Hello world', True),
    ({'contains': ['WOR']}, 'Hello world', False),
    ({'not_contains': ['world']}, 'Hello world', False),
    ({'not_contains': ['xyz']}, 'Hello world', True),
    ({'min_length': 2, 'max_length': 2}, 'é😀', True),
    ({'min_length': 2, 'max_length': 2}, 'é', True),
    ({'max_length': 2}, ' é ', False),
    ({'min_length': 2}, 'a', False),
    ({'exact': 'A\r\n', 'contains': ['A'], 'not_contains': ['a'],
      'min_length': 3, 'max_length': 3}, 'A\r\n', True),
])
def test_text_rules_are_literal_and_value_redacted(rules, text, passes):
    result = run({'format': 'text', **rules}, text)
    assert result['overall'] == ('pass' if passes else 'fail')
    assert all(e['check'].startswith('text.') for e in result['checks'])
    assert all(i['location'] == 'answer' and i['check'].startswith('text.') for i in result['issues'])
    assert 'Hello' not in json.dumps(result['issues'])


def test_text_rules_all_run_with_bounded_diagnostics():
    result = run({'format': 'text', 'contains': [f'literal{i}' for i in range(25)]}, '')
    assert result['issue_count'] == 25 and len(result['issues']) == 20
    assert [e['check'] for e in result['checks']] == [f'text.contains[{i}]' for i in range(25)]
    for value in (None, 1, True, [], {}):
        failed = checks.check_value(contract({'format': 'text'}), value)
        assert failed['overall'] == 'fail' and failed['issues'][0]['check'] == 'text.type'


@pytest.mark.parametrize('spec,location', [
    ({'format': 'text', 'checks': []}, 'checks'),
    ({'format': 'json', 'checks': [{'type': 'json_equals', 'value': 1}]}, 'checks'),
    ({'format': 'text', 'strict': False}, 'strict'),
    ({'format': 'json', 'strict': True}, 'strict'),
    ({'format': 'json', 'format_version': 1}, 'format_version'),
    ({'format': 'text', 'pattern': 'x'}, 'pattern'),
    ({'format': 'text', 'policy': 'internal'}, 'policy'),
    ({'format': 'text', 'future': 1}, 'future'),
    ({'format': 'json', 'schema': None}, 'schema'),
    ({'format': 'json', 'schema': {}}, 'schema.type'),
    ({'format': 'json', 'schema': False}, 'schema'),
    ({'format': 'json', 'schema': {'type': 'string', 'pattern': 'x'}}, 'schema.pattern'),
    ({'format': 'text', 'exact': 'SECRET', 'not_contains': ['SECRET']}, 'exact'),
    ({'format': 'text', 'min_length': True}, 'min_length'),
    ({'format': 'yaml'}, 'format'), ({}, 'format'),
])
def test_user_definition_refusals_are_located(spec, location):
    with pytest.raises(checks.ContractError) as exc:
        contract(spec)
    assert location in [i['location'] for i in exc.value.issues]
    assert 'SECRET' not in str(exc.value) + json.dumps(exc.value.issues)


@pytest.mark.parametrize('source', [
    '```json\n{"format":"text"}\n```', '{"format":"text","format":"json"}',
    '{"format":"json","schema":NaN}', '{', '[1]',
    '{"format":"text","description":"\\ud800"}',
])
def test_contract_source_is_one_exact_json_definition(source):
    with pytest.raises(checks.ContractError):
        checks.contract_from_text(source)


def test_source_size_is_checked_before_decoding(monkeypatch):
    def no_decode(*args):
        pytest.fail('decoded oversized source')
    monkeypatch.setattr(jsonio, 'decode_exact', no_decode)
    with pytest.raises(checks.ContractError) as exc:
        checks.contract_from_text(' ' * 1048577)
    assert exc.value.issues[0]['code'] == 'too_large'


def test_fixed_answer_parser_and_locations():
    spec = {'format': 'json'}
    fenced = run(spec, 'Here you go:\n```json\n{"a":1}\n```\nDone.')
    assert fenced['overall'] == 'pass' and fenced['notes'] == ['parsed the single fenced code block']
    assert run(spec, ' \n {"a":1} \n')['notes'] == []
    two = run(spec, '```\n{}\n```\n~~~\n{}\n~~~')
    assert two['overall'] == 'fail' and two['issues'][0]['location'] == 'line 4, column 1'
    assert run(spec, 'intro\n```json\n{"a":1,\n"b":}\n```')['issues'][0]['location'] == 'line 4, column 5'
    assert run(spec, '```json\n{"a":1}')['overall'] == 'pass'
    assert run(spec, '```json\n{"format":"text"}\n```')['overall'] == 'pass'
    for answer in ('prefix {"a":1}', '{} {}', '{"a":1,"a":2}', 'NaN'):
        assert run(spec, answer)['overall'] != 'pass'
    assert checks.fenced_blocks('    ```json\n{}\n    ```') == []
    assert checks.fenced_blocks('```info`invalid\n{}') == []
    whole = run(spec, '"```json\\n{}\\n```"')
    assert whole['overall'] == 'pass'


@pytest.mark.parametrize('schema,answer,overall', [
    ({'type': 'integer'}, '2.0', 'pass'), ({'type': 'integer'}, '2e0', 'pass'),
    ({'type': 'integer'}, 'true', 'fail'), ({'type': 'integer'}, '2.5', 'fail'),
    ({'type': 'number', 'minimum': Decimal('0.10000000000000000001')}, '0.1', 'fail'),
    ({'type': 'number', 'maximum': 9007199254740993}, '9007199254740993', 'pass'),
    ({'type': 'number', 'maximum': 9007199254740993}, '9007199254740994', 'fail'),
    ({'type': 'string'}, '"' + '7' * 5000 + '"', 'pass'),
    ({'type': 'array', 'items': {'type': 'string'}}, '[]', 'pass'),
    ({'type': 'object', 'properties': {}}, '{}', 'pass'),
    ({'type': 'object', 'properties': {}}, '{"extra":1}', 'pass'),
    ({'type': 'object', 'properties': {}, 'additionalProperties': True}, '{"extra":1}', 'pass'),
    ({'type': 'object', 'properties': {}, 'additionalProperties': False}, '{"extra":1}', 'fail'),
    ({'type': ['string', 'null'], 'enum': ['yes', None]}, 'null', 'pass'),
    ({'type': ['string', 'null'], 'enum': ['yes', None]}, '""', 'fail'),
])
def test_user_schema_exact_values(schema, answer, overall):
    assert run({'format': 'json', 'schema': schema}, answer)['overall'] == overall


def test_required_absent_null_empty_and_sorted_schema_issues():
    spec = {'format': 'json', 'schema': {'type': 'object', 'properties': {
        'value': {'type': ['string', 'null'], 'minLength': 1}}, 'required': ['value']}}
    assert run(spec, '{}')['overall'] == 'fail'
    assert run(spec, '{"value":null}')['overall'] == 'pass'
    assert run(spec, '{"value":""}')['overall'] == 'fail'
    result = run({'format': 'json', 'schema': {'type': 'array', 'items': {'type': 'integer'}}},
                 json.dumps(['x'] * 25))
    assert result['issue_count'] == 25 and len(result['issues']) == 20
    assert [i['location'] for i in result['issues']] == [f'$[{i}]' for i in range(20)]


def test_user_schema_diagnostics_do_not_repeat_sensitive_values_or_choices():
    result = run({'format': 'json', 'schema': {'type': 'string', 'enum': ['SECRET-CHOICE']}},
                 '"SECRET-ANSWER"')
    assert result['overall'] == 'fail'
    assert result['issues'][0]['message'] == 'value violates schema.enum'
    assert 'SECRET' not in json.dumps(result)


@pytest.mark.parametrize('expected,found,equal', [
    ({'a': 1, 'b': [None, Decimal('0.1')]}, {'b': [None, Decimal('0.10')], 'a': Decimal('1.0')}, True),
    (True, 1, False), (False, Decimal('0'), False), (None, '', False),
    ([1, 2], [2, 1], False), ({'a': 1}, {'a': 1, 'b': 2}, False),
    (9007199254740993, 9007199254740992, False),
])
def test_direct_exact_expectations(expected, found, equal):
    assert checks.exact_equal(expected, found) is equal


def internal(schema):
    return checks.parse_internal_contract({'format': 'json', 'schema': jsonio.decode_exact(schema)})


def test_internal_rich_schemas_preserve_exact_validation_and_local_references():
    recursive = internal('{"$defs":{"node":{"type":"object","properties":{'
                         '"v":{"type":"integer"},"next":{"$ref":"#/$defs/node"}}}},'
                         '"$ref":"#/$defs/node"}')
    assert checks.check_answer(recursive, '{"v":1,"next":{"v":2.0}}')['overall'] == 'pass'
    bad = checks.check_answer(recursive, '{"next":{"v":1.5}}')
    assert bad['overall'] == 'fail' and bad['issues'][0]['location'] == '$.next.v'
    assert checks.check_answer(internal('{"const":1}'), 'true')['overall'] == 'fail'
    assert checks.check_answer(internal('{"const":1}'), '1.0')['overall'] == 'pass'
    assert checks.check_answer(internal('{"uniqueItems":true}'), '[1,1.0]')['overall'] == 'fail'
    assert checks.check_answer(internal('{"multipleOf":0.1}'), '0.3')['overall'] == 'pass'
    assert checks.check_answer(internal('{"multipleOf":0.1}'), '0.31')['overall'] == 'fail'
    wide = checks.check_answer(internal('{"multipleOf":0.2}'), '0.' + '1' * 2101)
    assert wide['overall'] == 'incomplete' and entry(wide, 'schema')['result'] == 'not_run'
    unresolved = checks.check_answer(internal('{"$ref":"#/$defs/missing"}'), '1')
    assert unresolved['overall'] == 'contract_error'


def test_internal_anchors_and_literal_schema_keywords_are_not_user_policy():
    c = internal('{"$defs":{"n":{"$anchor":"number","type":"integer"}},'
                 '"properties":{"n":{"$ref":"#number"}}}')
    assert checks.check_answer(c, '{"n":2.0}')['overall'] == 'pass'
    assert checks.check_answer(c, '{"n":2.5}')['overall'] == 'fail'
    literal = internal('{"const":{"$schema":"untrusted-data","$ref":"not-a-reference"}}')
    assert checks.check_answer(literal, '{"$schema":"untrusted-data","$ref":"not-a-reference"}')['overall'] == 'pass'


@pytest.mark.parametrize('schema', [
    '{"$ref":"https://example.invalid/schema"}', '{"$dynamicRef":"other.json#x"}',
    '{"$schema":"https://json-schema.org/draft-07/schema"}', '{"type":"wrong"}',
    '{"minimum":1e5000}',
])
def test_internal_schema_refusals_never_fetch(monkeypatch, schema):
    def no_network(*args, **kwargs):
        pytest.fail('schema validation attempted network access')
    monkeypatch.setattr(socket, 'create_connection', no_network)
    with pytest.raises(checks.ContractError):
        internal(schema)


def test_validator_unavailability_and_unexpected_failure_never_pass(monkeypatch):
    c = contract({'format': 'json', 'schema': {'type': 'integer'}})
    monkeypatch.setattr(checks, 'schema_support', lambda: (False, 'damaged'))
    result = checks.check_answer(c, '1')
    assert result['overall'] == 'incomplete'
    assert 'validation_unavailable' in result['issues'][0]['message']
    assert 'repair the AgentWiz installation' in result['issues'][0]['message']
    with pytest.raises(checks.ValidationUnavailable):
        checks.require_validation([c])
    checks.require_validation([contract({'format': 'text'}), contract({'format': 'json'})])
    # Recognition never broadens when the installation is broken.
    with pytest.raises(checks.ContractError):
        contract({'format': 'json', 'schema': {'oneOf': []}})
    monkeypatch.setattr(checks, 'schema_support', lambda: (True, 'ok'))
    def broken():
        raise RuntimeError('SECRET')
    monkeypatch.setattr(checks, '_libs', broken)
    failed = checks.check_answer(c, '1')
    assert failed['overall'] == 'contract_error' and 'SECRET' not in json.dumps(failed)


def test_validator_supported_versions_and_corrupt_import(monkeypatch):
    for version, expected in [('4.26.0', True), ('4.99.1', True), ('4.25.9', False),
                              ('5.0.0', False), ('not-a-version', False), (None, False)]:
        monkeypatch.setattr(checks, '_jsonschema_version', lambda: version)
        assert checks.schema_support()[0] is expected
    monkeypatch.setattr(checks, '_jsonschema_version', lambda: '4.26.0')
    def broken(name):
        raise RuntimeError('SECRET')
    monkeypatch.setattr(importlib, 'import_module', broken)
    assert checks.schema_support() == (False, 'jsonschema cannot be imported')


def test_answer_values_and_repair_use_captured_rules():
    c = contract({'format': 'text', 'exact': 'pong'})
    result, value = checks.check_answer_value(c, ' pong ')
    assert value == ' pong ' and result['overall'] == 'fail'
    message = checks.repair_message(c, result)
    assert 'text.exact' in message and 'pong' not in message
    cut = checks.repair_message(c, result, truncated=True)
    assert cut.startswith('The previous answer was cut off at the output length limit')
    assert cut.endswith('It must fit within the same length limit.')
    c = contract({'format': 'json'})
    result, value = checks.check_answer_value(c, '```json\n{"n":9007199254740993,"f":0.10000000000000000001}\n```')
    assert result['overall'] == 'pass'
    assert value == {'n': 9007199254740993, 'f': Decimal('0.10000000000000000001')}
    assert checks.check_answer_value(c, 'null')[1] is None
    assert checks.check_answer_value(c, 'not json')[1] is checks.NO_VALUE


def test_decode_exact_error_contract():
    for text, kind in [('{', jsonio.JSONSyntax), ('1e9223372036854775808', jsonio.ExactLimit),
                       ('1' * 4301, jsonio.ExactLimit), ('NaN', ValueError),
                       ('{"a":1,"a":2}', ValueError), ('[' * 100000 + '1', ValueError)]:
        with pytest.raises(kind):
            jsonio.decode_exact(text)
    with pytest.raises(jsonio.JSONSyntax) as exc:
        jsonio.decode_exact('{\n  "a": ,}')
    assert (exc.value.line, exc.value.column) == (2, 8)
    assert jsonio.decode_exact('1' * 4300) == int('1' * 4300)