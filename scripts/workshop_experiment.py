#!/usr/bin/env python3
"""Prepare R7-10/R7-11 fixtures or submit them to the existing Workshop service.

Use --print-fixture for an offline inventory. Sending requires an already running
AgentWiz server, an assigned role and its session token in AGENTWIZ_SESSION_TOKEN.
The coordinator remains the sole execution/metrics implementation.
"""
from __future__ import annotations

import argparse
import os
import urllib.request

from agentwiz import jsonio


def fixture(role):
    contract = {'format': 'json', 'schema': {'type': 'object', 'properties': {
        'order': {'type': 'object', 'properties': {'id': {'type': 'integer'}, 'quantity': {'type': 'integer'}},
                  'required': ['id'], 'additionalProperties': False}}, 'required': ['order'], 'additionalProperties': False}}
    definition = {'format_version': 1, 'name': 'experiment_extract', 'description': 'Held-out nested exact extraction',
        'inputs': {'source': {'contract': {'format': 'json', 'schema': {'type': 'object', 'properties': {
            'message': {'type': 'string'}}, 'required': ['message'], 'additionalProperties': False}}}},
        'outputs': {'out': {'contract': contract}}, 'steps': [{'id': 'extract', 'kind': 'model', 'role': role,
            'response_mode': 'named_json',
            'prompt': {'text': 'Extract the order ID and explicit quantity. Example: ID 12, two items -> {"order":{"id":12,"quantity":2}}. Omit unspecified quantity.\n{{inputs.source.message}}'},
            'task': {'max_tokens': 512}, 'repair': {'max_repairs': 2}}]}
    first = {'key': definition['name'], 'definition_text': jsonio.encode_exact(definition), 'files': {},
             'metadata': {'format_version': 1, 'origin': 'manual', 'effects': 'read-only'}}
    definition['steps'][0]['prompt']['text'] = 'Extract only the facts. Return JSON matching this explicit schema: ' + jsonio.encode_exact(contract['schema']) + '\n{{inputs.source.message}}'
    second = {**first, 'definition_text': jsonio.encode_exact(definition)}
    examples = []
    for i, (text, output) in enumerate([
        ('Order ID 9007199254740993, three notebooks', '{"order":{"id":9007199254740993,"quantity":3}}'),
        ('Order ID 25, some notebooks', '{"order":{"id":25}}'),
    ]):
        examples.append({'id': 'held-out-' + str(i), 'version': 1, 'label': text,
            'inputs': {'source': jsonio.encode_exact({'message': text})}, 'verified': True, 'held_out': True,
            'origin': 'packaged_human_reviewed', 'expectation': {'kind': 'whole_output', 'values': {'out': output}}})
    return {'candidates': [{'draft': first}, {'draft': second}], 'examples': examples}



def label_fixture(role):
    from agentwiz.workshop_editor import starter
    first = starter('choose_label', 'experiment_label', role=role)
    definition = jsonio.decode_exact(first['definition_text'])
    schema = definition['outputs']['out']['contract']['schema']
    definition['steps'][0]['prompt']['text'] = 'Classify sentiment. Return JSON matching: ' + jsonio.encode_exact(schema) + '\n{{inputs.text}}'
    second = {**first, 'definition_text': jsonio.encode_exact(definition)}
    examples = [{'id': 'sentiment-' + str(i), 'version': 1, 'label': label,
        'inputs': {'text': text}, 'verified': True, 'held_out': True,
        'origin': 'packaged_human_reviewed', 'expectation': {'kind': 'whole_output', 'values': {'out': jsonio.encode_exact(label)}}}
        for i, (text, label) in enumerate([('This product is excellent.', 'positive'), ('This product is terrible.', 'negative')])]
    return {'candidates': [{'draft': first}, {'draft': second}], 'examples': examples}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--role', default='fast')
    p.add_argument('--url')
    p.add_argument('--task', choices=('extract', 'label'), default='extract')
    p.add_argument('--print-fixture', action='store_true')
    args = p.parse_args()
    body = fixture(args.role) if args.task == 'extract' else label_fixture(args.role)
    if args.print_fixture:
        print(jsonio.encode_exact(body))
        return
    token = os.environ.get('AGENTWIZ_SESSION_TOKEN')
    if not args.url or not token:
        p.error('sending requires --url and AGENTWIZ_SESSION_TOKEN')
    def post(path, payload):
        req = urllib.request.Request(args.url.rstrip('/') + '/api/workshop/' + path,
            data=jsonio.encode_exact({'format_version': 1, **payload}).encode(),
            headers={'Content-Type': 'application/json', 'X-Session-Token': token}, method='POST')
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=30) as r:
            return jsonio.decode_exact(r.read().decode())
    body['ticket'] = post('tickets', {})['ticket']
    plan = post('plans', {'action': 'batches', 'request': body})
    if plan['required_confirmations']:
        p.error('the plan requires explicit review in the application')
    print(jsonio.encode_exact(post('batches', {**body, 'expected_plan': plan['expected_plan'], 'confirmations': []})))


if __name__ == '__main__':
    main()
