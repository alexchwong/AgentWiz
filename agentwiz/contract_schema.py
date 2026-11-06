"""The user contract vocabulary and exact, formatting-independent contract identity.

This module has no storage, HTTP or validator dependency. Recognition must precede
value validation; the richer server-owned assistance schemas do not use this policy.
"""
from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal

from . import jsonio

DEFINITION_BYTES = 1_048_576
MAX_BOUND = 4_194_304
MAX_NODES = 100
MAX_DEPTH = 10
ISSUES_SHOWN = 20
IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,63}\Z')
TYPES = ('string', 'integer', 'number', 'boolean', 'array', 'object')
BOUNDS = {'string': ('minLength', 'maxLength'), 'array': ('minItems', 'maxItems'),
          'integer': ('minimum', 'maximum'), 'number': ('minimum', 'maximum')}
TEXT_RULES = ('min_length', 'max_length', 'exact', 'contains', 'not_contains')


class DefinitionError(ValueError):
    """Bounded located diagnostics, with the complete issue count retained."""

    def __init__(self, issues, total=None):
        self.issues = issues[:ISSUES_SHOWN]
        self.total = len(issues) if total is None else total
        super().__init__('the user contract is invalid')


def _location(path, key):
    if isinstance(key, int):
        return f'{path}[{key}]'
    if IDENT.fullmatch(key):
        return f'{path}.{key}' if path else key
    return f'{path}[{json.dumps(key, ensure_ascii=True)}]'


def _number(value):
    return type(value) is int or isinstance(value, Decimal) and value.is_finite()


def _whole(value):
    if not _number(value):
        return False
    if isinstance(value, int):
        return True
    _, digits, exponent = value.as_tuple()
    return exponent >= 0 or not any(digits[exponent:])


class _Recognition:
    def __init__(self):
        self.issues = []
        self.total = 0
        self.nodes = 0

    def issue(self, location, message, code='contract_invalid'):
        self.total += 1
        if len(self.issues) < ISSUES_SHOWN:
            self.issues.append({'location': location, 'message': message, 'code': code})

    def finish(self):
        if self.total:
            raise DefinitionError(self.issues, self.total)

    def string(self, value, path, limit):
        if not isinstance(value, str):
            self.issue(path, 'must be a string')
            return False
        if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            self.issue(path, 'must not contain unpaired Unicode surrogates')
            return False
        if len(value) > limit:
            self.issue(path, f'must have at most {limit} code points')
            return False
        return True

    def bounds(self, node, path, low, high, numeric=False):
        valid = {}
        for key in (low, high):
            if key not in node:
                continue
            value = node[key]
            if numeric:
                ok = _number(value)
                message = 'must be an exact finite JSON number, not a boolean or float'
            else:
                ok = _whole(value) and 0 <= value <= MAX_BOUND
                message = f'must be a whole JSON number from 0 to {MAX_BOUND}'
            if not ok:
                self.issue(_location(path, key), message)
            else:
                valid[key] = value
        if low in valid and high in valid and valid[low] > valid[high]:
            self.issue(_location(path, high), f'must not be less than {low}')
        return valid

    def schema(self, node, path, depth=0):
        self.nodes += 1
        if self.nodes > MAX_NODES:
            self.issue(path, f'must contain at most {MAX_NODES} schema nodes', 'too_large')
            return
        if depth > MAX_DEPTH:
            self.issue(path, f'schema depth must not exceed {MAX_DEPTH}', 'too_large')
            return
        if not isinstance(node, dict):
            self.issue(path, 'must be a schema node object')
            return
        declared = node.get('type')
        nullable = isinstance(declared, list)
        if nullable:
            base = declared[0] if len(declared) == 2 and declared[1] == 'null' else None
        else:
            base = declared
        if not isinstance(base, str) or base not in TYPES:
            self.issue(_location(path, 'type'), 'must be a supported type or exactly [type,"null"]')
            base = None
        allowed = {'type', 'description'}
        if base in BOUNDS:
            allowed.update(BOUNDS[base])
        if base == 'string':
            allowed.add('enum')
        elif base == 'array':
            allowed.add('items')
        elif base == 'object':
            allowed.update(('properties', 'required', 'additionalProperties'))
        for key in node:
            if key not in allowed:
                self.issue(_location(path, str(key)), 'keyword is not supported for this node', 'unsupported_keyword')
        if 'description' in node:
            self.string(node['description'], _location(path, 'description'), 4096)
        bounds = {}
        if base in BOUNDS:
            bounds = self.bounds(node, path, *BOUNDS[base], numeric=base in ('integer', 'number'))
        if base == 'string' and 'enum' in node:
            choices = node['enum']
            where = _location(path, 'enum')
            if not isinstance(choices, list):
                self.issue(where, 'must be an array of string choices')
            else:
                strings = choices[:-1] if nullable and choices and choices[-1] is None else choices
                if nullable and (not choices or choices[-1] is not None):
                    self.issue(where, 'a nullable Choice requires one final null')
                if not 1 <= len(strings) <= 200:
                    self.issue(where, 'must contain from 1 to 200 string choices')
                seen = set()
                for i, choice in enumerate(strings):
                    location = _location(where, i)
                    if self.string(choice, location, 4096):
                        if choice in seen:
                            self.issue(location, 'must not repeat a choice')
                        seen.add(choice)
                        if ('minLength' in bounds and len(choice) < bounds['minLength'] or
                                'maxLength' in bounds and len(choice) > bounds['maxLength']):
                            self.issue(location, 'choice violates the declared length bounds')
        elif base == 'array':
            if 'items' not in node:
                self.issue(_location(path, 'items'), 'is required')
            else:
                self.schema(node['items'], _location(path, 'items'), depth + 1)
        elif base == 'object':
            props = node.get('properties')
            if not isinstance(props, dict):
                self.issue(_location(path, 'properties'), 'is required and must be an object')
                props = {}
            required = node.get('required', [])
            where = _location(path, 'required')
            if not isinstance(required, list):
                self.issue(where, 'must be an array of distinct property names')
            else:
                seen = set()
                for i, key in enumerate(required):
                    if not isinstance(key, str) or key not in props or key in seen:
                        self.issue(_location(where, i), 'must name a distinct declared property')
                    if isinstance(key, str):
                        seen.add(key)
            if 'additionalProperties' in node and type(node['additionalProperties']) is not bool:
                self.issue(_location(path, 'additionalProperties'), 'must be a boolean')
            for key, child in props.items():
                where = _location(_location(path, 'properties'), str(key))
                if not isinstance(key, str) or IDENT.fullmatch(key) is None:
                    self.issue(where, 'property name must match the port identifier rule')
                self.schema(child, where, depth + 1)

    def definition(self, obj):
        if not isinstance(obj, dict):
            self.issue('contract', 'must be an object')
            return
        fmt = obj.get('format')
        if fmt not in ('text', 'json'):
            self.issue('format', 'must be text or json')
        allowed = {'format', 'description'}
        allowed.update(TEXT_RULES if fmt == 'text' else ('schema',) if fmt == 'json' else ())
        for key in obj:
            if key not in allowed:
                self.issue(str(key), 'definition member is not supported', 'unsupported_keyword')
        if 'description' in obj:
            self.string(obj['description'], 'description', 4096)
        if fmt == 'json' and 'schema' in obj:
            self.schema(obj['schema'], 'schema')
        elif fmt == 'text':
            bounds = self.bounds(obj, '', 'min_length', 'max_length')
            lists = {}
            for key in ('contains', 'not_contains'):
                values = obj.get(key, [])
                lists[key] = []
                if not isinstance(values, list) or len(values) > 100:
                    self.issue(key, 'must be an array of at most 100 distinct nonempty literals')
                    continue
                seen = set()
                for i, value in enumerate(values):
                    where = _location(key, i)
                    if self.string(value, where, 4096):
                        if not value or value in seen:
                            self.issue(where, 'must be nonempty and distinct')
                        seen.add(value)
                        lists[key].append(value)
            for i, literal in enumerate(lists['contains']):
                if any(forbidden in literal for forbidden in lists['not_contains']):
                    self.issue(f'contains[{i}]', 'required literal contains a forbidden literal')
                if 'max_length' in bounds and len(literal) > bounds['max_length']:
                    self.issue(f'contains[{i}]', 'required literal exceeds max_length')
            if 'exact' in obj and self.string(obj['exact'], 'exact', DEFINITION_BYTES):
                exact = obj['exact']
                if len(exact.encode('utf-8')) > DEFINITION_BYTES:
                    self.issue('exact', 'exceeds the exact text byte limit', 'too_large')
                if ('min_length' in bounds and len(exact) < bounds['min_length'] or
                        'max_length' in bounds and len(exact) > bounds['max_length'] or
                        any(v not in exact for v in lists['contains']) or
                        any(v in exact for v in lists['not_contains'])):
                    self.issue('exact', 'exact text violates another supplied rule')


def _check_values(obj, recognition):
    """Reject non-JSON Python values and the reader's oversized whole numbers."""
    stack = [(obj, '', False)]
    active = set()
    while stack:
        value, path, leaving = stack.pop()
        if leaving:
            active.remove(id(value))
            continue
        if isinstance(value, (dict, list)):
            if id(value) in active:
                recognition.issue(path or 'contract', 'cyclic Python containers are not JSON')
                continue
            active.add(id(value))
            stack.append((value, path, True))
            pairs = value.items() if isinstance(value, dict) else enumerate(value)
            for key, child in pairs:
                if isinstance(value, dict) and not isinstance(key, str):
                    recognition.issue(path or 'contract', 'object keys must be strings')
                stack.append((child, _location(path, key if isinstance(key, (str, int)) else str(key)), False))
        elif isinstance(value, str):
            if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
                recognition.issue(path, 'must not contain unpaired Unicode surrogates')
        elif value is None or type(value) is bool:
            continue
        elif not _number(value):
            recognition.issue(path, 'must be an exact JSON value, not a float')
        elif _whole(value):
            digits = (Decimal(value).adjusted() + 1 if type(value) is int and value else
                      1 if type(value) is int else
                      1 if value == 0 else value.adjusted() + 1)
            if digits > jsonio.INT_DIGITS_LIMIT:
                recognition.issue(path, 'whole number exceeds the exact reader digit limit')


def validate_user_schema(schema):
    """Raise DefinitionError for any unsupported or inconsistent schema node."""
    recognition = _Recognition()
    _check_values(schema, recognition)
    recognition.finish()
    recognition.schema(schema, 'schema')
    recognition.finish()


def synthesis_definition(response):
    """The direct user definition a synthesis `proposal` value yields (spec 14.2), not yet validated: format json,
    the description when nonblank, and the proposed schema. Acceptance (contract_synthesis_v1) and application
    both build the candidate here, so a proposal accepted by one is exactly the definition the other uses."""
    definition = {'format': 'json'}
    if response.get('description'):
        definition['description'] = response['description']
    definition['schema'] = response['schema']
    return definition


def validate_user_definition(obj):
    """Recognize a complete user definition; never discard unknown members."""
    recognition = _Recognition()
    _check_values(obj, recognition)
    recognition.finish()
    recognition.definition(obj)
    recognition.finish()
    def byte_size(value):
        if isinstance(value, str):
            return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
        if isinstance(value, dict):
            return 2 + max(0, len(value) - 1) + sum(
                byte_size(key) + 1 + byte_size(child) for key, child in value.items())
        if isinstance(value, list):
            return 2 + max(0, len(value) - 1) + sum(map(byte_size, value))
        return len(jsonio.encode_exact(value))
    try:
        size = byte_size(obj)
    except (ValueError, RecursionError, TypeError):
        raise DefinitionError([{'location': 'contract', 'message': 'cannot encode the definition'}]) from None
    if size > DEFINITION_BYTES:
        raise DefinitionError([{'location': 'contract', 'message': 'definition exceeds 1 MiB', 'code': 'too_large'}])


def contract_digest(definition):
    """SHA-256 of the exact canonical definition, not a schema-equivalence test.

    Deliberately does not select a policy: internal snapshots use the same digest.
    Decimal tuple processing avoids expansion and context-rounded normalization.
    """
    def canonical(value):
        if isinstance(value, dict):
            if any(not isinstance(k, str) for k in value):
                raise ValueError('object keys must be strings')
            return '{' + ','.join(canonical(k) + ':' + canonical(value[k]) for k in sorted(value)) + '}'
        if isinstance(value, list):
            return '[' + ','.join(canonical(v) for v in value) + ']'
        if isinstance(value, str):
            if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
                raise ValueError('unpaired Unicode surrogate')
            return json.dumps(value, ensure_ascii=True)
        if value is None:
            return 'null'
        if type(value) is bool:
            return 'true' if value else 'false'
        if not _number(value):
            raise ValueError('numbers must be exact and finite')
        number = Decimal(value) if type(value) is int else value
        sign, digits, exponent = number.as_tuple()
        if not any(digits):
            return '0'
        digits = list(digits)
        while digits[-1] == 0:
            digits.pop()
            exponent += 1
        return ('-' if sign else '') + ''.join(map(str, digits)) + 'e' + str(exponent)

    return 'sha256:' + hashlib.sha256(canonical(definition).encode('ascii')).hexdigest()