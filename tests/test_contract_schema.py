"""Functional subset and identity boundaries for authored contract definitions."""
from decimal import Decimal, localcontext
import hashlib

import pytest

from agentwiz import contract_schema as schema, jsonio


@pytest.mark.parametrize('definition', [
    {'format': 'text'},
    {'format': 'text', 'exact': '', 'min_length': 0, 'contains': [], 'not_contains': []},
    {'format': 'text', 'exact': 'é\n', 'contains': ['é'], 'max_length': Decimal('2.0')},
    {'format': 'json'},
    {'format': 'json', 'schema': {'type': 'string', 'enum': ['', 'x'], 'maxLength': 1}},
    {'format': 'json', 'schema': {'type': ['string', 'null'], 'enum': ['x', None]}},
    {'format': 'json', 'schema': {'type': 'number', 'minimum': Decimal('0.1234567890123456789')}},
    {'format': 'json', 'schema': {'type': 'object', 'properties': {}}},
    {'format': 'json', 'schema': {'type': 'boolean'}},
    {'format': 'json', 'schema': {'type': 'integer'}},
    *({'format': 'json', 'schema': {'type': [kind, 'null'], **extra}}
      for kind, extra in [('string', {}), ('integer', {}), ('number', {}), ('boolean', {}),
                          ('array', {'items': {'type': 'string'}}), ('object', {'properties': {}})]),
    {'format': 'json', 'schema': {'type': 'array', 'items': {
        'type': 'object', 'properties': {'a': {'type': ['integer', 'null']}},
        'required': ['a'], 'additionalProperties': False}}},
])
def test_supported_definitions(definition):
    schema.validate_user_definition(definition)
    assert schema.contract_digest(definition) == schema.contract_digest(
        jsonio.decode_exact(jsonio.encode_exact(definition)))


@pytest.mark.parametrize('definition, location', [
    ({'format': 'text', 'strict': False}, 'strict'),
    ({'format': 'text', 'checks': []}, 'checks'),
    ({'format': 'text', 'schema': {}}, 'schema'),
    ({'format': 'text', 'min_length': True}, 'min_length'),
    ({'format': 'text', 'max_length': 4194305}, 'max_length'),
    ({'format': 'text', 'min_length': 2, 'max_length': 1}, 'max_length'),
    ({'format': 'text', 'contains': ['a', 'a']}, 'contains[1]'),
    ({'format': 'text', 'contains': ['']}, 'contains[0]'),
    ({'format': 'text', 'contains': ['secret'], 'not_contains': ['cret']}, 'contains[0]'),
    ({'format': 'text', 'exact': ' a ', 'max_length': 1}, 'exact'),
    ({'format': 'text', 'description': '\ud800'}, 'description'),
    ({'format': 'json', 'schema': None}, 'schema'),
    ({'format': 'json', 'schema': {}}, 'schema.type'),
    ({'format': 'json', 'schema': False}, 'schema'),
    ({'format': 'json', 'schema': {'type': 'number', 'minimum': 0.1}}, 'schema.minimum'),
    ({'format': 'json', 'schema': {'type': 'string', 'enum': ['x', None]}}, 'schema.enum[1]'),
    ({'format': 'json', 'schema': {'type': ['string', 'null'], 'enum': ['x']}}, 'schema.enum'),
    ({'format': 'json', 'schema': {'type': ['null', 'string']}}, 'schema.type'),
    ({'format': 'json', 'schema': {'type': 'array', 'items': []}}, 'schema.items'),
    ({'format': 'json', 'schema': {'type': 'object', 'properties': {}, 'required': ['x']}}, 'schema.required[0]'),
    ({'format': 'json', 'schema': {'type': 'object', 'properties': {'bad-key': {'type': 'string'}}}},
     'schema.properties["bad-key"]'),
])
def test_invalid_definitions_are_located(definition, location):
    with pytest.raises(schema.DefinitionError) as exc:
        schema.validate_user_definition(definition)
    assert location in [issue['location'] for issue in exc.value.issues]


@pytest.mark.parametrize('keyword, value', [
    ('$ref', '#'), ('$defs', {}), ('$schema', 'https://json-schema.org/draft/2020-12/schema'),
    ('pattern', 'a'), ('format', 'date'), ('default', ''), ('const', ''), ('examples', []),
    ('oneOf', []), ('multipleOf', 1), ('exclusiveMinimum', 1), ('title', 'x'),
])
def test_unsupported_nested_keywords_are_not_ignored(keyword, value):
    with pytest.raises(schema.DefinitionError) as exc:
        schema.validate_user_schema({'type': 'array', 'items': {'type': 'string', keyword: value}})
    assert any(i['code'] == 'unsupported_keyword' and i['location'].startswith('schema.items')
               for i in exc.value.issues)


def test_resource_limits_and_issue_count():
    leaf = {'type': 'string'}
    schema.validate_user_schema({'type': 'object', 'properties': {f'p{i}': leaf for i in range(99)}})
    with pytest.raises(schema.DefinitionError):
        schema.validate_user_schema({'type': 'object', 'properties': {f'p{i}': leaf for i in range(100)}})
    nested = leaf
    for _ in range(10):
        nested = {'type': 'array', 'items': nested}
    schema.validate_user_schema(nested)
    with pytest.raises(schema.DefinitionError):
        schema.validate_user_schema({'type': 'array', 'items': nested})
    with pytest.raises(schema.DefinitionError) as exc:
        schema.validate_user_schema({'type': 'string', **{f'unknown{i}': 1 for i in range(25)}})
    assert exc.value.total == 25 and len(exc.value.issues) == 20
    with pytest.raises(schema.DefinitionError):
        schema.validate_user_definition({'format': 'text', 'exact': 'é' * 524289})


@pytest.mark.parametrize('spellings, canonical', [
    (['1', '1.0', '1e0'], '1e0'),
    (['100', '100.00', '1e2'], '1e2'),
    (['0.10', '1e-1'], '1e-1'),
    (['0', '-0.0', '0e999999'], '0'),
    (['-12.300', '-123e-1'], '-123e-1'),
    (['1e-999999'], '1e-999999'),
])
def test_canonical_numeric_identity(spellings, canonical):
    expected = 'sha256:' + hashlib.sha256(('{"x":' + canonical + '}').encode('ascii')).hexdigest()
    with localcontext() as context:
        context.prec = 2
        for spelling in spellings:
            assert schema.contract_digest(jsonio.decode_exact('{"x":' + spelling + '}')) == expected


def test_digest_preserves_structural_distinctions():
    digest = schema.contract_digest
    assert digest({'b': 1, 'a': 'é'}) == digest({'a': 'é', 'b': Decimal('1.00')})
    assert digest({'x': True}) != digest({'x': 1})
    assert digest({'x': [1, 2]}) != digest({'x': [2, 1]})
    assert digest({'format': 'text'}) != digest({'format': 'text', 'description': ''})
    with pytest.raises(ValueError):
        digest({'x': 0.1})


def test_python_boundary_refuses_cycles_nonstring_keys_and_oversized_integers():
    cycle = {'type': 'object', 'properties': {}}
    cycle['properties']['again'] = cycle
    for node in (cycle, {'type': 'string', 7: 'invalid'},
                 {'type': 'number', 'minimum': 10 ** 4300}):
        with pytest.raises(schema.DefinitionError):
            schema.validate_user_schema(node)