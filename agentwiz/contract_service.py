"""Pure contract draft projection and targeted edits; never writes a workspace.

Definitions and numeric bounds cross the service boundary as exact JSON text.
Every operation passes the same recognizer, including advanced source edits.
"""
from __future__ import annotations

from . import contract_schema, jsonio


def _invalid(location='edit', message='invalid contract edit'):
    raise contract_schema.DefinitionError([{'location': location, 'message': message,
                                            'code': 'contract_invalid'}])


def _decode(text, location='contract'):
    if not isinstance(text, str):
        _invalid(location, 'must be exact JSON text')
    try:
        size = len(text.encode('utf-8'))
    except UnicodeEncodeError:
        _invalid(location, 'must not contain unpaired Unicode surrogates')
    if size > contract_schema.DEFINITION_BYTES:
        raise contract_schema.DefinitionError([{'location': location, 'message': 'source exceeds 1 MiB',
                                                'code': 'too_large'}])
    try:
        return jsonio.decode_exact(text)
    except jsonio.JSONSyntax as exc:
        _invalid(f'line {exc.line}, column {exc.column}', 'source must be one complete JSON value')
    except ValueError:
        _invalid(location, 'source must be exact JSON with distinct keys and finite numbers')


def _definition(text):
    obj = _decode(text)
    contract_schema.validate_user_definition(obj)
    return obj


def project(definition_text):
    """Validate and project a complete draft, retaining its original source."""
    obj = _definition(definition_text)
    result = {'format': obj['format'], 'definition_text': definition_text,
              'description': obj.get('description', ''),
              'digest': contract_schema.contract_digest(obj), 'warnings': [], 'fields': []}
    if obj['format'] == 'text':
        result['rules'] = {key: obj[key] for key in contract_schema.TEXT_RULES if key in obj}
        # Bounds are exact strings even when a hand author used 2.0 or 2e0.
        for key in ('min_length', 'max_length'):
            if key in result['rules']:
                result['rules'][key] = jsonio.encode_exact(result['rules'][key])
    elif 'schema' in obj:
        from .workshop_editor import field_rows
        result['fields'] = field_rows(obj['schema'])
    else:
        result['warnings'].append({'code': 'unconstrained_json',
                                   'message': 'Any JSON — no field validation'})
    return result


def check(definition_text, value_text):
    """Check a sample without dispatch, storage or a correctness judgement."""
    from . import checks
    obj = _definition(definition_text)
    if not isinstance(value_text, str):
        _invalid('value_text', 'must be text')
    if 'schema' in obj and not checks.schema_support()[0]:
        raise ValidationUnavailable()
    parsed = checks.parse_contract(obj)
    return {'check': checks.check_answer(parsed, value_text)}


class ValidationUnavailable(Exception):
    """The required schema validator installation needs repair."""


_EDIT_FIELDS = {
    'set_description': {'path', 'description'},
    'set_text_rules': {'rules'},
    'set_bound': {'path', 'keyword', 'value_text'},
    'set_nullable': {'path', 'value'},
    'set_required': {'path', 'value'},
    'set_choices': {'path', 'choices'},
    'set_additional_properties': {'path', 'value'},
    'add_property': {'path', 'name', 'node_text', 'required'},
    'rename_property': {'path', 'name'},
    'remove_property': {'path'},
    'set_node': {'path', 'node_text'},
}


def _base(node):
    value = node['type']
    return value[0] if isinstance(value, list) else value


def _path(obj, path):
    if obj['format'] != 'json' or 'schema' not in obj:
        _invalid('edit.path', 'requires a schema-backed JSON definition')
    if not isinstance(path, list) or any(not isinstance(p, str) for p in path):
        _invalid('edit.path', 'must be an array of property names and item segments')
    node = obj['schema']
    parent = None
    for part in path:
        parent = node
        if _base(node) == 'object' and part in node['properties']:
            node = node['properties'][part]
        elif _base(node) == 'array' and part == '[]':
            node = node['items']
        else:
            _invalid('edit.path', 'does not address a declared node')
    return node, parent


def _boolean(edit, key):
    if type(edit[key]) is not bool:
        _invalid('edit.' + key, 'must be a boolean')
    return edit[key]


def _property(parent, path):
    if parent is None or _base(parent) != 'object' or not path or path[-1] == '[]':
        _invalid('edit.path', 'must address a Group property, not the root or List item')


def apply_edit(definition_text, edit):
    """Edit one subtree/rule, preserving every unrelated exact-parsed member."""
    obj = _definition(definition_text)
    if not isinstance(edit, dict) or not isinstance(edit.get('op'), str):
        _invalid()
    op = edit['op']
    if op not in _EDIT_FIELDS or set(edit) != _EDIT_FIELDS[op] | {'op'}:
        _invalid()
    if op == 'set_text_rules':
        rules = edit['rules']
        if obj['format'] != 'text' or not isinstance(rules, dict) or set(rules) - set(contract_schema.TEXT_RULES):
            _invalid('edit.rules', 'must contain only Text constraints')
        obj = {key: value for key, value in obj.items() if key not in contract_schema.TEXT_RULES}
        for key, value in rules.items():
            obj[key] = _decode(value, 'edit.rules.' + key) if key in ('min_length', 'max_length') else value
    elif op == 'set_description' and edit['path'] is None:
        obj['description'] = edit['description']
    else:
        path = edit['path']
        node, parent = _path(obj, path)
        base = _base(node)
        if op == 'set_description':
            node['description'] = edit['description']
        elif op == 'set_bound':
            keyword = edit['keyword']
            if not isinstance(keyword, str) or keyword not in contract_schema.BOUNDS.get(base, ()):
                _invalid('edit.keyword', 'bound does not apply to this node type')
            if edit['value_text'] is None:
                node.pop(keyword, None)
            else:
                node[keyword] = _decode(edit['value_text'], 'edit.value_text')
        elif op == 'set_nullable':
            nullable = _boolean(edit, 'value')
            node['type'] = [base, 'null'] if nullable else base
            if 'enum' in node:
                node['enum'] = [v for v in node['enum'] if v is not None] + ([None] if nullable else [])
        elif op == 'set_required':
            _property(parent, path)
            required = _boolean(edit, 'value')
            members = parent.get('required', [])
            if required and path[-1] not in members:
                parent['required'] = [*members, path[-1]]
            elif not required and path[-1] in members:
                parent['required'] = [p for p in members if p != path[-1]]
        elif op == 'set_choices':
            if base != 'string' or not isinstance(edit['choices'], list) or any(
                    not isinstance(v, str) for v in edit['choices']):
                _invalid('edit.choices', 'requires string choices on a string node')
            node['enum'] = edit['choices'] + ([None] if isinstance(node['type'], list) else [])
        elif op == 'set_additional_properties':
            if base != 'object':
                _invalid('edit.path', 'requires a Group')
            node['additionalProperties'] = _boolean(edit, 'value')
        elif op == 'add_property':
            name = edit['name']
            if base != 'object' or not isinstance(name, str) or contract_schema.IDENT.fullmatch(name) is None:
                _invalid('edit.name', 'requires a Group and a valid property name')
            if name in node['properties']:
                _invalid('edit.name', 'property already exists')
            child = _decode(edit['node_text'], 'edit.node_text')
            contract_schema.validate_user_schema(child)
            node['properties'][name] = child
            if _boolean(edit, 'required'):
                node['required'] = [*node.get('required', []), name]
        elif op == 'rename_property':
            _property(parent, path)
            name = edit['name']
            if not isinstance(name, str) or contract_schema.IDENT.fullmatch(name) is None:
                _invalid('edit.name', 'must be a valid property name')
            if name != path[-1] and name in parent['properties']:
                _invalid('edit.name', 'property already exists')
            parent['properties'] = {name if key == path[-1] else key: value
                                    for key, value in parent['properties'].items()}
            if 'required' in parent:
                parent['required'] = [name if key == path[-1] else key for key in parent['required']]
        elif op == 'remove_property':
            _property(parent, path)
            del parent['properties'][path[-1]]
            if 'required' in parent:
                parent['required'] = [key for key in parent['required'] if key != path[-1]]
        elif op == 'set_node':
            replacement = _decode(edit['node_text'], 'edit.node_text')
            contract_schema.validate_user_schema(replacement)
            if parent is None:
                obj['schema'] = replacement
            elif _base(parent) == 'array':
                parent['items'] = replacement
            else:
                parent['properties'][path[-1]] = replacement
    contract_schema.validate_user_definition(obj)
    return project(jsonio.encode_exact(obj))