"""Deterministic single-step operations over already validated block inputs."""
from __future__ import annotations

import json
import re

from . import identifier_pairs, jsonio, templates

VALUE_LIMIT = 4 << 20
KEY = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
OPERATIONS = ('count_text', 'select_fields', 'format_report', 'restore_placeholders')
RESTORE_PARAMETERS = {'unknown': ('fail', 'keep'), 'placeholder_syntax': tuple(identifier_pairs.SYNTAXES)}
TRACE_LIMIT = 200
CHUNK = 256


def validate(operation, parameters, inputs):
    if operation not in OPERATIONS or not isinstance(parameters, dict):
        raise ValueError('expected a supported transform operation and parameter object')
    if operation == 'count_text':
        if set(parameters) != {'unit'} or parameters['unit'] not in ('whitespace_words', 'characters'):
            raise ValueError('count_text requires unit whitespace_words or characters')
        if 'text' not in inputs:
            raise ValueError('count_text requires the declared input text')
    elif operation == 'restore_placeholders':
        if (set(parameters) != set(RESTORE_PARAMETERS)
                or any(parameters[key] not in allowed for key, allowed in RESTORE_PARAMETERS.items())):
            raise ValueError('restore_placeholders requires unknown fail or keep and placeholder_syntax bracket_upper')
        if not {'text', 'pairs'} <= set(inputs):
            raise ValueError('restore_placeholders requires the declared inputs text and pairs')
    elif operation == 'format_report':
        if set(parameters) != {'template'} or not isinstance(parameters['template'], str):
            raise ValueError('format_report requires a text template')
        for placeholder in templates.scan_text(parameters['template'], 'template'):
            if placeholder.path[0] not in inputs:
                raise ValueError('template references an undeclared input')
    else:
        mappings = parameters.get('mappings')
        if set(parameters) != {'mappings'} or not isinstance(mappings, list) or len(mappings) > 50:
            raise ValueError('select_fields requires up to 50 mappings')
        destinations = set()
        for mapping in mappings:
            if not isinstance(mapping, dict) or set(mapping) != {'source_path', 'destination', 'required'}:
                raise ValueError('each mapping needs source_path, destination and required')
            path, destination = mapping['source_path'], mapping['destination']
            if not isinstance(path, list) or not path or any(not isinstance(part, str) or not KEY.fullmatch(part) for part in path):
                raise ValueError('source_path must be a nonempty list of identifier keys')
            if path[0] not in inputs:
                raise ValueError('source_path references an undeclared input')
            if not isinstance(destination, str) or not KEY.fullmatch(destination) or destination in destinations:
                raise ValueError('mapping destinations must be unique identifiers')
            if type(mapping['required']) is not bool:
                raise ValueError('mapping required must be boolean')
            destinations.add(destination)


def execute(operation, parameters, inputs):
    if operation == 'count_text':
        text = inputs['text']
        if not isinstance(text, str):
            raise templates.FillError('inputs.text', 'expected text')
        result = len(text.split()) if parameters['unit'] == 'whitespace_words' else len(text)
        details = []
    elif operation == 'restore_placeholders':
        result, details = restore(parameters, inputs['text'], inputs['pairs'])
    elif operation == 'format_report':
        result, details = templates.fill_text(parameters['template'], 'template', inputs, byte_limit=VALUE_LIMIT)
    else:
        result, details = {}, []
        for mapping in parameters['mappings']:
            path = mapping['source_path']
            value = inputs
            for key in path:
                if not isinstance(value, dict) or key not in value:
                    if mapping['required']:
                        raise templates.FillError('inputs.' + '.'.join(path), 'required selection is missing')
                    break
                value = value[key]
            else:
                result[mapping['destination']] = value
                details.append({'source_path': path, 'destination': mapping['destination']})
    if len(jsonio.encode_exact(result).encode('utf-8')) > VALUE_LIMIT:
        raise templates.FillError('output', 'transform value exceeds 4 MiB')
    return result, details


def encoded_len(text, start=0, end=None):
    """Length of `text[start:end]` inside jsonio's ASCII-escaped JSON string, without the quotes. Measured in
    bounded chunks so an oversized segment is never escaped whole."""
    end = len(text) if end is None else end
    total = 0
    for offset in range(start, end, CHUNK):
        total += len(json.dumps(text[offset:min(offset + CHUNK, end)], ensure_ascii=True)) - 2
    return total


def restore(parameters, text, pairs):
    """Replace each placeholder token of the input with its original in one linear pass (operation
    `restore_placeholders`, version 1). Inserted text is never rescanned."""
    if not isinstance(text, str):
        raise templates.FillError('inputs.text', 'expected text')
    syntax = parameters['placeholder_syntax']
    try:
        entries = identifier_pairs.parse(pairs, profile='restore', syntax=syntax)
    except identifier_pairs.PairsError as exc:
        location = 'inputs.pairs' if exc.index is None else f'inputs.pairs[{exc.index}]'
        raise templates.FillError(location, exc.message) from None
    mapping = {entry.placeholder: entry.original for entry in entries}
    lengths = {placeholder: encoded_len(original) for placeholder, original in mapping.items()}
    keep = parameters['unknown'] == 'keep'
    parts, counts, unknown = [], {}, {}
    total, last = 2, 0

    def grow(amount):
        nonlocal total
        total += amount
        if total > VALUE_LIMIT:
            raise templates.FillError('output', 'transform value exceeds 4 MiB')

    def copy_text(start, end):
        if start < end:
            for offset in range(start, end, CHUNK):
                grow(encoded_len(text, offset, min(offset + CHUNK, end)))
            parts.append(text[start:end])

    for match in identifier_pairs.SYNTAXES[syntax].finditer(text):
        token, start = match.group(), match.start()
        copy_text(last, start)
        last = match.end()
        if token in mapping:
            grow(lengths[token])
            parts.append(mapping[token])
            counts[token] = counts.get(token, 0) + 1
            continue
        if not keep:
            raise templates.FillError('inputs.text', f'unknown placeholder {token} at offset {start}')
        unknown.setdefault(token, start)
        grow(encoded_len(token))
        parts.append(token)
    copy_text(last, len(text))
    result = ''.join(parts)
    del parts
    absent = [placeholder for placeholder in mapping if placeholder not in counts]

    def bounded(rows):
        return {'items': rows[:TRACE_LIMIT], 'truncated': len(rows) > TRACE_LIMIT}

    details = [{'replacements': bounded([{'placeholder': key, 'count': value} for key, value in counts.items()]),
                'unknown': bounded([{'placeholder': key, 'offset': value} for key, value in unknown.items()]),
                'absent': bounded(absent)}]
    return result, details
