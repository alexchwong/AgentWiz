"""Load the packaged Company catalogue through native component compilation."""
from __future__ import annotations

import hashlib
import re
from contextlib import ExitStack
from pathlib import Path

from jsonschema import Draft202012Validator

from . import checks, components, jsonio, workflow_definition
from .workflow_runner import Graph


def sha(data):
    return hashlib.sha256(data).hexdigest()


def check_schema(schema, value):
    return checks.check_value(checks.parse_contract({'format': 'json', 'schema': schema}), value)['overall'] == 'pass'


RULES = {
    'quotes': ((), ('path',)),
    'row_quotes': (('field',), ('path', 'nullable')),
    'required_truth': (('condition',), ('path',)),
    'ambiguous_group': (('field', 'condition'), ('path',)),
    'contains_value': (('field', 'value', 'condition'), ('path',)),
    'checklist': (('candidate', 'criteria'), ('path',)),
}
STAGE_CHECKS = {
    'quotes': (),
    'candidate': (),
    'same_candidate': (),
    'required_truth': ('condition',),
}


class Catalogue:
    def __init__(self, directory):
        root = directory.resolve()
        raw = (root / 'manifest.json').read_bytes()
        self.revision = sha(raw)
        self.manifest = m = jsonio.decode_file_exact(raw)
        workflow_definition.record(m, ('format', 'format_version', 'files', 'packages', 'entrypoints',
                                      'structures', 'capabilities', 'presets', 'fixtures'), (), 'catalogue')
        if (m['format'] != 'agentwiz.company_catalogue' or type(m['format_version']) is not int
                or m['format_version'] != 1 or not 1 <= len(m['files']) <= 256):
            raise ValueError('invalid Company manifest')
        self.files = {}
        for filename, digest in m['files'].items():
            relative = Path(filename)
            path = root / relative
            if (relative.is_absolute() or '..' in relative.parts or path.resolve() != path
                    or not path.is_file() or path.stat().st_size > components.LIMIT):
                raise ValueError('invalid Company asset path')
            payload = path.read_bytes()
            if sha(payload) != digest:
                raise ValueError('Company asset digest mismatch')
            self.files[filename] = payload
        packaged = [self.json(filename) for filename in m['packages']]
        schema = self.json('schemas/component.json')
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        for package in packaged:
            validator.validate(package)
        pool = {components.identity(package['root']): package['root'] for package in packaged}
        if len(pool) != len(packaged):
            raise ValueError('duplicate packaged component')
        self.compiled = {components.identity(package['root']): components.Compiler(package, pool, shipped=True).run()
                         for package in packaged}
        self.presets = {}
        for row in m['presets']:
            workflow_definition.record(row, ('id', 'title', 'purpose', 'fit', 'workflow', 'parameters',
                                       'input_bindings', 'node_checks', 'evidence_exports', 'examples'),
                                       ('preflight',), 'preset')
            if (not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', row['id'])
                    or any(not isinstance(row[key], str) or not 0 < len(row[key]) <= 4096
                           for key in ('title', 'purpose', 'fit'))):
                raise ValueError('invalid Company preset identity')
            compiled = self.reference(row['workflow'], 'workflow')
            bundle = compiled['bundle']
            with ExitStack() as stack:
                graph = Graph(bundle, bundle['definition']['name'], stack)
                if (graph.gates or graph.restricted or graph.issues or set(row['input_bindings']) != graph.contracts['inputs'].keys()
                        or not row['examples'] or any(key not in m['fixtures'] for key in row['examples'])
                        or any(step.kind != 'model' and not (step.kind == 'transform' or
                               step.kind == 'tool' and step.builtin) for step in (block.step for block in graph.blocks.values()))):
                    raise ValueError('ineligible packaged workflow')
                for check in row['node_checks']:
                    required, optional = RULES[check['op']]
                    workflow_definition.record(check, ('op', 'node', 'port', *required), optional, 'node check')
                    if check['op'] == 'checklist':
                        for key in ('candidate', 'criteria'):
                            workflow_definition.source(check[key], 'node check binding')
                for export in row['evidence_exports']:
                    workflow_definition.record(export, ('id', 'node', 'port', 'type'), ('path',), 'evidence export')
                if len({item['id'] for item in row['evidence_exports']}) != len(row['evidence_exports']):
                    raise ValueError('duplicate evidence export')
                for check in [*row['node_checks'], *row['evidence_exports']]:
                    if check['port'] not in graph.blocks[check['node']].contracts['outputs']:
                        raise ValueError('invalid Company node port')
                count = sum(block.step.kind == 'model' for block in graph.blocks.values())
                uses_tools = any(block.step.builtin for block in graph.blocks.values())
            if row['id'] in self.presets:
                raise ValueError('duplicate Company workflow ID')
            checks.parse_contract({'format':'json','schema':row['parameters']})
            self.presets[row['id']] = {**row, 'bundle': bundle, 'revision': compiled['revision'],
                                        'model_calls': count, 'uses_tools': uses_tools}
        for stage in m['entrypoints'].values():
            workflow_definition.record(stage, ('agent', 'instructions', 'input_contract', 'output_contract'), (), 'stage')
            agent = self.reference(stage['agent'], 'agent')['component']
            if agent['definition']['mode'] != 'cloud':
                raise ValueError('ineligible Company call stage')
            self.files[stage['instructions']].decode('utf-8')
            for key in ('input_contract', 'output_contract'):
                checks.parse_contract({'format':'json','schema':self.json(stage[key])})
        for structure in m['structures'].values():
            workflow_definition.record(structure, ('title', 'allowed_policies', 'stages'), (), 'specialist structure')
            if (not 1 <= len(structure['stages']) <= 8 or set(structure['allowed_policies']) - {'D', 'E'}):
                raise ValueError('invalid Company specialist structure')
            previous = set()
            for stage in structure['stages']:
                workflow_definition.record(stage, ('id', 'entrypoint', 'bindings', 'method_scope', 'checks'), (), 'specialist stage')
                if (stage['id'] in previous or stage['entrypoint'] not in m['entrypoints']
                        or set(stage['method_scope']) - {'reviewed_tools'}):
                    raise ValueError('invalid Company specialist stage')
                for source in stage['bindings'].values():
                    keys = {'brief': (), 'parameter': ('key',), 'stage': ('id', 'field')}[source['from']]
                    workflow_definition.record(source, ('from', *keys), (), 'specialist binding')
                    if source['from'] == 'stage' and source['id'] not in previous:
                        raise ValueError('invalid specialist dependency')
                for check in stage['checks']:
                    workflow_definition.record(check, ('op', 'field', *STAGE_CHECKS[check['op']]), (), 'specialist check')
                previous.add(stage['id'])
        for item in m['capabilities'].values():
            workflow_definition.record(item, ('title', 'block', 'visibility_policies'), (), 'capability')
            block = self.reference(item['block'], 'block')['component']['definition']['block']
            if (block['steps'][0]['kind'] != 'tool' or 'builtin' not in block['steps'][0]
                    or any(port['local_only'] for side in ('inputs', 'outputs') for port in block[side].values())):
                raise ValueError('ineligible packaged capability')

    def json(self, filename):
        return jsonio.decode_file_exact(self.files[filename])

    def reference(self, expression, kind):
        ref = expression['ref']
        if ref['kind'] != kind:
            raise ValueError('unexpected packaged component kind')
        return self.compiled[kind, ref['id'], ref['revision']]

    def prompt(self, stage):
        return self.files[self.manifest['entrypoints'][stage]['instructions']].decode('utf-8')

    def views(self):
        return [{key: row[key] for key in ('id', 'title', 'fit', 'revision', 'model_calls', 'uses_tools')}
                for row in self.presets.values()]


def csv_source(preset, brief):
    from . import agent_tools
    selector = next(iter(preset['preflight']['selectors'].values()))
    language = re.escape(selector['language'])
    matches = re.findall(rf'^\x60{{3}}{language}[ \t]*\r?\n(.*?)^\x60{{3}}[ \t]*\r?$', brief, re.M | re.S | re.I)
    if len(matches) > 1:
        raise ValueError('ambiguous source')
    value = matches[0] if matches else brief
    parsed = agent_tools.parse_csv({'csv': value, 'has_header': True})
    if len(parsed['columns']) < 2 and not matches:
        raise ValueError('expected a CSV table')
    return value, parsed['columns']


def bind(preset, brief, parameters):
    if not check_schema(preset['parameters'], parameters):
        raise ValueError('invalid workflow parameters')
    result = {}
    for name, binding in preset['input_bindings'].items():
        if binding['from'] == 'parameter':
            value = parameters[binding['key']]
        elif binding['from'] == 'first_valid':
            value, columns = csv_source(preset, brief)
            if any(item not in columns for item in parameters.values()):
                raise ValueError('unknown CSV column')
        elif binding['from'] == 'brief':
            value = brief
        else:
            raise ValueError('unknown workflow source binding')
        contract = preset['bundle']['definition']['inputs'][name]['contract']
        result[name] = value if contract['format'] == 'text' else jsonio.encode_exact(value)
    return result
