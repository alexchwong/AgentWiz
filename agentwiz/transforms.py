"""Deterministic single-step operations over already validated block inputs."""
from __future__ import annotations

import re

from . import jsonio, templates

VALUE_LIMIT = 4 << 20
KEY = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
OPERATIONS = ('count_text', 'select_fields', 'format_report')


def validate(operation, parameters, inputs):
    if operation not in OPERATIONS or not isinstance(parameters, dict):
        raise ValueError('expected a supported transform operation and parameter object')
    if operation == 'count_text':
        if set(parameters) != {'unit'} or parameters['unit'] not in ('whitespace_words', 'characters'):
            raise ValueError('count_text requires unit whitespace_words or characters')
        if 'text' not in inputs:
            raise ValueError('count_text requires the declared input text')
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
