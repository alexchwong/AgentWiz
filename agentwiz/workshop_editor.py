"""Exact typed field projections, starter generation and proposal compilation."""
from __future__ import annotations

import copy

from . import blocks, checks, jsonio, python_step
from .task_settings import OVERRIDABLE
from .workshop import WorkshopError
from .workshop_assets import fail, wire_files

TYPES = {'Text': 'string', 'Choice': 'string', 'Whole number': 'integer', 'Number': 'number',
         'Yes/No': 'boolean', 'List': 'array', 'Group': 'object'}
BOUNDS = {'minimum': 'minimum', 'maximum': 'maximum', 'min_length': 'minLength',
          'max_length': 'maxLength', 'min_items': 'minItems', 'max_items': 'maxItems'}
SECTIONS = ('use', 'exclusions', 'effects', 'limitations', 'missing_data', 'failures')
# Built-in transforms (version 2 starters). The version-1 generated host-script form is retired (0.2.2, 10.6).
TRANSFORM_IDS = ('count_text', 'select_fields', 'format_report', 'restore_placeholders')
RESTORE_DEFAULTS = {'unknown': 'fail', 'placeholder_syntax': 'bracket_upper'}
MODEL_IDS = ('write_text', 'summarise', 'extract_fields', 'choose_label', 'check_source')
TOOL_IDS = ('call_tool', 'builtin_utility')
STARTERS = MODEL_IDS + TRANSFORM_IDS + TOOL_IDS
BLANK_IDS = ('blank_model', 'blank_tool', 'blank_python')
# The Start with menu, grouped by type (0.2.2, 11). "describe_python" opens Python authoring (10.7).
GROUPS = (('llm', 'LLM', MODEL_IDS + ('blank_model',)),
          ('tool', 'Tool', ('call_tool', 'builtin_utility', 'blank_tool')),
          ('python', 'Python', ('describe_python', 'blank_python')),
          ('transform', 'Transform', TRANSFORM_IDS))
PYTHON_FILE = 'scripts/main.py'
PYTHON_BLANK = ('def transform(inputs):\n'
                '    # Return a dictionary with exactly the declared output names.\n'
                '    return {"out": inputs["text"]}\n')


def typed(value):
    if isinstance(value, dict):
        return {'type': 'object', 'fields': [{'name': k, 'value': typed(v)} for k, v in value.items()]}
    if isinstance(value, list):
        return {'type': 'array', 'items': [typed(v) for v in value]}
    kind = 'null' if value is None else 'boolean' if type(value) is bool else 'text' if isinstance(value, str) else 'number'
    return {'type': kind, 'text': value if kind == 'text' else jsonio.encode_exact(value)}


def input_value(node):
    kind = node['type']
    if kind == 'object':
        pairs = [(field['name'], input_value(field['value'])) for field in node['fields']]
        if len(dict(pairs)) != len(pairs):
            fail()
        return dict(pairs)
    if kind == 'array':
        return [input_value(item) for item in node['items']]
    if kind == 'text':
        if not isinstance(node['text'], str):
            fail()
        return node['text']
    value = jsonio.decode_exact(node['text'])
    if kind == 'null' and value is None or kind == 'boolean' and type(value) is bool:
        return value
    from decimal import Decimal
    if kind == 'number' and type(value) in (int, Decimal):
        return value
    fail()


def field_rows(schema):
    """Readable rows of a user-subset schema, in stored property order.

    The central recogniser decides what a user schema may contain; this only projects a schema it accepted, so
    there is no second keyword allowlist here. Anything else (an internal assistance schema, a hand-edited
    unsupported node) raises WorkshopError and is never projected or overwritten from a partial reading.
    """
    from .contract_schema import DefinitionError, validate_user_schema
    try:
        validate_user_schema(schema)
    except DefinitionError as exc:
        raise WorkshopError('contract_invalid', 400, exc.issues) from None
    rows = []

    def visit(node, path, required=True):
        t = node['type']
        nullable = isinstance(t, list)
        base = t[0] if nullable else t
        row = {'path': path, 'type': 'Choice' if 'enum' in node else next(k for k, v in TYPES.items() if v == base),
               'required': required, 'nullable': nullable, 'description': node.get('description', '')}
        if 'enum' in node:
            row['choices'] = [v for v in node['enum'] if v is not None]
        for k, keyword in BOUNDS.items():
            if keyword in node:
                row[k] = jsonio.encode_exact(node[keyword])
        row['source_text'] = jsonio.encode_exact(node)
        rows.append(row)
        if base == 'object':
            row['additional_properties'] = node.get('additionalProperties', True)
            for name, child in node['properties'].items():
                visit(child, path + [name], name in node.get('required', []))
        if base == 'array':
            visit(node['items'], path + ['[]'])
    visit(schema, [])
    return rows


def contract_view(text):
    """The shared headless projection of one port's exact user definition (overhaul spec 12)."""
    from . import contract_service
    return {**contract_service.project(text), 'source_text': text}


def empty_groups(definition):
    """The guided groups of a block definition that declare no field, as (side, port, path) triples.

    A guided group is an object closed to extra properties, which is what the field editor writes. A
    schema outside the guided subset is source-only and is never judged here, and neither is a
    contract held by reference.
    """
    found = set()
    for side in ('inputs', 'outputs'):
        for name, declaration in definition.get(side, {}).items():
            contract = declaration.get('contract') if isinstance(declaration, dict) else None
            schema = contract.get('schema') if isinstance(contract, dict) else None
            if schema is None:
                continue
            try:
                rows = field_rows(schema)
            except WorkshopError:
                continue
            parents = {tuple(row['path'][:-1]) for row in rows if row['path']}
            for row in rows:
                if (row['type'] == 'Group' and tuple(row['path']) not in parents
                        and jsonio.decode_exact(row['source_text']).get('additionalProperties') is False):
                    found.add((side, name, tuple(row['path'])))
    return found


def compile_rows(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        fail()
    nodes = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) - {'path', 'type', 'required', 'nullable', 'description', 'choices', 'additional_properties', *BOUNDS}:
            fail()
        path = row.get('path')
        if not isinstance(path, list) or len(path) > 10 or any(not isinstance(p, str) or (p != '[]' and blocks.IDENT.fullmatch(p) is None) for p in path):
            fail()
        key = tuple(path)
        if key in nodes or row.get('type') not in TYPES or type(row.get('required')) is not bool or type(row.get('nullable')) is not bool:
            fail()
        t = TYPES[row['type']]
        node = {'type': [t, 'null'] if row['nullable'] else t, 'description': row.get('description', '')}
        if row['type'] == 'Choice':
            choices = row.get('choices')
            if not isinstance(choices, list) or not choices or not all(isinstance(c, str) for c in choices) or len(set(choices)) != len(choices):
                fail()
            node['enum'] = choices + ([None] if row['nullable'] else [])
        for k, keyword in BOUNDS.items():
            if k in row:
                if not isinstance(row[k], str):
                    fail()
                node[keyword] = jsonio.decode_exact(row[k])
        if t == 'object':
            extra = row.get('additional_properties', False)
            if type(extra) is not bool:
                fail()
            node.update(properties={}, required=[], additionalProperties=extra)
        nodes[key] = (node, row)
    if () not in nodes or not nodes[()][1]['required']:
        fail()
    for path, (node, row) in nodes.items():
        if not path:
            continue
        parent = nodes.get(path[:-1])
        if parent is None:
            fail()
        p = parent[0]
        t = p['type'][0] if isinstance(p['type'], list) else p['type']
        if path[-1] == '[]' and t == 'array' and row['required']:
            p['items'] = node
        elif t == 'object' and path[-1] != '[]':
            p['properties'][path[-1]] = node
            if row['required']:
                p['required'].append(path[-1])
        else:
            fail()
    if any((n['type'] == 'array' or n['type'] == ['array', 'null']) and 'items' not in n for n, _ in nodes.values()):
        fail()
    from .contract_schema import DefinitionError, validate_user_schema
    try:
        validate_user_schema(nodes[()][0])
    except DefinitionError as exc:
        raise WorkshopError('contract_invalid', 400, exc.issues) from None
    return nodes[()][0]


def port(spec):
    if not isinstance(spec, dict) or set(spec) != {'name', 'description', 'format', 'fields'}:
        fail()
    if not isinstance(spec['name'], str) or blocks.IDENT.fullmatch(spec['name']) is None:
        fail()
    contract = {'format': spec['format']}
    if spec['format'] == 'json':
        contract['schema'] = compile_rows(spec['fields'])
    elif spec['format'] != 'text' or spec['fields'] != []:
        fail()
    checks.parse_contract(contract)
    return {'description': spec['description'], 'contract': contract}


def python_definition(key, inputs, outputs, labels=None):
    """A v4 Python block definition with the installed engine (0.2.2, 10.1). `inputs` and `outputs` map port
    names to contracts; `labels` is True/False for every port, or None to leave labels for the author."""
    obj = {'format_version': blocks.WORKFLOW_FORMAT_VERSION, 'name': key, 'description': '',
           'inputs': {n: {'contract': c} for n, c in inputs.items()},
           'outputs': {n: {'contract': c} for n, c in outputs.items()},
           'steps': [{'id': 'run', 'kind': 'python', 'file': PYTHON_FILE, 'engine': python_step.engine()}]}
    if labels is not None:
        for side in ('inputs', 'outputs'):
            for declaration in obj[side].values():
                declaration['local_only'] = labels
    return obj


def classification(draft):
    """Starter classification is meaningful only for version-2 transforms now."""
    return 'custom'


DATA_HANDLING = {'private': True, 'not_private': False, 'per_port': None}


def starter(identifier, key, role=None, server=None, tool=None, *, version=1, data_handling=None):
    """A new draft. For model and tool kinds, `data_handling` creates a workflow-ready (v4) block whose
    starter ports carry the chosen label: `private` → Local only, `not_private` → no label, `per_port` →
    unset, so Save stays refused until each port is chosen. Without it the block is v3, as before."""
    if data_handling is not None and data_handling not in DATA_HANDLING:
        fail()
    if identifier == 'blank_python':
        label = DATA_HANDLING[data_handling] if data_handling is not None else None
        text = {'format': 'text'}
        obj = python_definition(key, {'text': text}, {'out': text}, label)
        return {'key': key, 'definition_text': jsonio.encode_exact(obj),
                'files': wire_files({PYTHON_FILE: PYTHON_BLANK.encode('utf-8')}),
                'metadata': {'format_version': 1, 'origin': 'manual', 'effects': 'read-only'}}
    if identifier in ('blank_model', 'blank_tool'):
        kind = identifier.split('_')[1]
        draft = starter({'model':'write_text','tool':'call_tool'}[kind], key, role, server, tool,
                        data_handling=data_handling)
        definition = jsonio.decode_exact(draft['definition_text'])
        definition['description'] = ''
        if kind == 'model':
            definition['steps'][0]['prompt'] = {'text':''}
        draft['metadata'] = {'format_version':1,'origin':'manual','effects':'unknown'}
        draft['definition_text'] = jsonio.encode_exact(definition)
        return draft
    if identifier not in STARTERS or type(version) is not int or version not in (1, 2):
        fail()
    if data_handling is not None and (identifier in TRANSFORM_IDS or version != 1):
        fail()
    if identifier in TRANSFORM_IDS and version != 2:
        fail()
    text = {'format': 'text'}
    obj = {'format_version': blocks.FORMAT_VERSION, 'name': key, 'description': identifier.replace('_', ' '),
           'inputs': {'text': {'contract': text}}, 'outputs': {'out': {'contract': text}}, 'steps': []}
    files, m = {}, {'format_version': 1, 'starter': identifier, 'origin': 'starter', 'effects': 'read-only'}
    if identifier in MODEL_IDS:
        instructions = {'write_text': 'Follow the supplied task on the text.', 'summarise': 'Summarise the text concisely.',
                        'extract_fields': 'Extract product and quantity as JSON. Omit quantity when unspecified. Example: three notebooks -> {"product":"notebooks","quantity":3}.',
                        'choose_label': 'Return one label, positive or negative, as a JSON string.',
                        'check_source': 'Identify discrepancies between the text and the supplied source.'}[identifier]
        if identifier == 'extract_fields':
            obj['outputs']['out']['contract'] = {'format': 'json', 'schema': {'type': 'object', 'properties': {'product': {'type': 'string'}, 'quantity': {'type': 'integer'}}, 'required': ['product'], 'additionalProperties': False}}
        if identifier == 'choose_label':
            obj['outputs']['out']['contract'] = {'format': 'json', 'schema': {'type': 'string', 'enum': ['positive', 'negative']}}
        if identifier == 'check_source':
            obj['inputs']['source'] = {'contract': text}
            instructions += '\nSource: {{inputs.source}}'
        obj['steps'] = [{'id': 'request', 'kind': 'model', 'role': role, 'system': {'text': ''},
                         'prompt': {'text': instructions + '\n{{inputs.text}}'},
                         'response_mode': 'named_json'}]
    elif identifier in TRANSFORM_IDS:
        params = {'count_text': {'unit': 'whitespace_words'}, 'select_fields': {'mappings': [{'source_path': ['text'], 'destination': 'text', 'required': True}]}, 'format_report': {'template': '{{inputs.text}}'},
                  'restore_placeholders': dict(RESTORE_DEFAULTS)}[identifier]
        if identifier == 'restore_placeholders':
            obj['inputs']['pairs'] = {'contract': {'format': 'json'}}
            obj['description'] = 'restore placeholders'
        obj['format_version'] = blocks.WORKFLOW_FORMAT_VERSION
        for side in ('inputs', 'outputs'):
            for declaration in obj[side].values():
                # The mapping holds originals, so every restore port is Local only.
                declaration['local_only'] = identifier == 'restore_placeholders'
        m.update(starter_version=2, parameters=params)
        obj['steps'] = [{'id': 'transform', 'kind': 'transform', 'operation': identifier,
                         'operation_version': 1, 'parameters': params, 'output': 'out'}]
        if identifier not in ('format_report', 'restore_placeholders'):
            obj['outputs']['out']['contract'] = {'format': 'json'}
    elif identifier == 'builtin_utility':
        # A Tool block bound to a built-in utility (6.8): local execution, read-only, workflow-ready.
        obj['format_version'] = blocks.WORKFLOW_FORMAT_VERSION
        obj['description'] = 'calculate'
        obj['steps'] = [{'id': 'call', 'kind': 'tool', 'builtin': 'calculate',
                         'arguments': {'expression': '{{inputs.text}}'}, 'output': 'out'}]
        obj['outputs']['out']['contract'] = {'format': 'json'}
        if data_handling is None:
            for side in ('inputs', 'outputs'):
                for declaration in obj[side].values():
                    declaration['local_only'] = False
    else:
        m['effects'] = 'unknown'
        obj['steps'] = [{'id': 'call', 'kind': 'tool', 'server': server, 'tool': tool, 'arguments': {}, 'output': 'out'}]
        obj['outputs']['out']['contract'] = {'format': 'json'}
    if data_handling is not None:
        obj['format_version'] = blocks.WORKFLOW_FORMAT_VERSION
        label = DATA_HANDLING[data_handling]
        for side in ('inputs', 'outputs'):
            for declaration in obj[side].values():
                if label is not None:
                    declaration['local_only'] = label
    m['description_sections'] = {'use': obj['description'], 'exclusions': 'Use for one independent block operation.',
        'effects': 'Review the selected external tool before running.' if identifier == 'call_tool' else 'Produces a returned value for review.',
        'limitations': 'Model answers require correctness review.' if identifier in MODEL_IDS else
                       'Replaces exact placeholder tokens only; a placeholder the text reworded is not restored.' if identifier == 'restore_placeholders' else 'Runs locally inside AgentWiz.' if identifier in TRANSFORM_IDS or identifier == 'builtin_utility' else 'Remote behavior may change.',
        'missing_data': 'Omit unspecified quantity.' if identifier == 'extract_fields' else
                        'An unknown placeholder fails the step unless set to leave it in place; unused pairs are listed.' if identifier == 'restore_placeholders' else 'Review required inputs before running.',
        'failures': 'Inspect located input, execution and output-check issues.'}
    return {'key': key, 'definition_text': jsonio.encode_exact(obj), 'files': wire_files(files), 'metadata': m}


def edit(draft, operation):
    """Apply a targeted field edit to the authoritative contract on the server."""
    result = copy.deepcopy(draft)
    obj = jsonio.decode_exact(result['definition_text'])
    if operation.get('kind') == 'upgrade_for_workflows':
        step = obj['steps'][0]
        if obj.get('format_version') != blocks.FORMAT_VERSION:
            fail('invalid', 400)
        if step['kind'] not in ('model', 'tool'):
            fail('invalid', 400)
        obj['format_version'] = blocks.WORKFLOW_FORMAT_VERSION
        result['definition_text'] = jsonio.encode_exact(obj)
    elif operation.get('kind') == 'design_form':
        result.setdefault('metadata', {})['display_name'] = operation['name']
        if 'revision_name' in operation:
            result['metadata']['revision_name'] = operation['revision_name']
        obj['description'] = operation['purpose']
        if 'context_local_only' in operation:
            if obj.get('format_version') != blocks.WORKFLOW_FORMAT_VERSION or type(operation['context_local_only']) is not bool:
                fail()
            obj['context_local_only'] = operation['context_local_only']
        for side, entries in operation['ports'].items():
            if side not in ('inputs', 'outputs'):
                fail()
            ports = {}
            for e in entries:
                name = e['name']
                if not blocks.IDENT.fullmatch(name) or name in ports:
                    fail()
                original = obj[side].get(e['original'], {})
                contract = jsonio.decode_exact(e['source']) if e['source'] else {'format': e['format']}
                if e['format'] == 'shared':
                    # A saved contract is held by its exact pin; replacing one is an explicit edit.
                    if not isinstance(contract, dict) or set(contract) != {'name', 'revision', 'digest'}:
                        fail('reference_invalid', 400)
                elif isinstance(contract, dict):
                    contract['format'] = e['format']
                else:
                    fail()
                ports[name] = {**original, 'description': e['description'], 'contract': contract}
                if obj.get('format_version') == blocks.WORKFLOW_FORMAT_VERSION and 'local_only' in e:
                    ports[name]['local_only'] = e['local_only']
            obj[side] = ports
        step = obj['steps'][0]
        output_renames = {e['original']: e['name'] for e in operation['ports'].get('outputs', [])}
        if step.get('kind') in ('tool', 'transform') and step.get('output') in output_renames:
            step['output'] = output_renames[step['output']]
        if step['kind'] == 'model':
            for key, value in [('prompt', operation['behaviour']), ('system', operation['system'])]:
                if 'file' in step.get(key, {}):
                    result['files'][step[key]['file']] = value
                else:
                    step[key] = {'text': value}
            step['role'] = operation['binding'] or None
            step['task'] = jsonio.decode_exact(operation['settings'])
            for key, text in operation.get('sampling', {}).items():
                if key not in ('reasoning','temperature','max_tokens'):
                    fail()
                if text:
                    step['task'][key] = text if key == 'reasoning' else jsonio.decode_exact(text)
                else:
                    step['task'].pop(key, None)
            # An overriding field asks for a value; a reset asks for the backend's own default instead of
            # the role's, and is the only way to say so once a role has defaults.
            resets = operation.get('resets', [])
            if not isinstance(resets, list) or any(name not in OVERRIDABLE for name in resets) or len(set(resets)) != len(resets):
                fail()
            # A control left on Custom with no value is neither an override nor a reset, and guessing which
            # one was meant would change what the block sends. It is refused where the author can see it;
            # Inherit and Backend default are the two ways to say what a blank value might have meant.
            customs = operation.get('customs', [])
            if not isinstance(customs, list) or any(name not in OVERRIDABLE for name in customs) \
                    or len(set(customs)) != len(customs) or set(customs) & set(resets):
                fail()
            blank = [name for name in customs if not operation.get('sampling', {}).get(name)]
            if blank:
                raise WorkshopError('block_invalid', 409, [
                    {'location': f'steps[0].task.{name}',
                     'message': 'is set to Custom without a value: enter one, or choose Inherit or Backend default'}
                    for name in blank])
            for name in resets:
                step['task'].pop(name, None)
            step['task_resets'] = [name for name in OVERRIDABLE if name in resets]
            if not step['task_resets']:
                step.pop('task_resets')
            step['response_mode'] = 'named_json'
            step['repair'] = {'max_repairs': jsonio.decode_exact(operation['repairs'])}
        elif step['kind'] == 'python':
            result['files'][step['file']] = operation['behaviour']
        elif step['kind'] == 'tool' and 'builtin' in step:
            step.update(builtin=operation['binding'], arguments=jsonio.decode_exact(operation.get('arguments', '{}')))
        elif step['kind'] == 'tool':
            step.update(server=operation['binding'], tool=operation['behaviour'], arguments=jsonio.decode_exact(operation.get('arguments','{}')))
        obj['budget'] = jsonio.decode_exact(operation['budget'])
        for key, text in operation.get('limits', {}).items():
            if key not in ('max_seconds','max_model_tokens'):
                fail()
            if text:
                obj['budget'][key] = jsonio.decode_exact(text)
            else:
                obj['budget'].pop(key, None)
        result['definition_text'] = jsonio.encode_exact(obj)
    elif operation.get('kind') == 'starter_parameters':
        if obj['steps'][0].get('kind') == 'transform' and result['metadata'].get('starter_version') == 2:
            obj['steps'][0]['parameters'] = operation['parameters']
            result['metadata']['parameters'] = operation['parameters']
            result['definition_text'] = jsonio.encode_exact(obj)
            return result
        fail('custom_source', 409)
    elif operation.get('kind') == 'make_local_copy':
        # Explicit only: replace one failed reference with the definition this draft's generation captured.
        from .contract_schema import contract_digest
        side, name = operation.get('side'), operation.get('port')
        if side not in ('inputs', 'outputs') or name not in obj.get(side, {}):
            fail()
        reference = obj[side][name].get('contract')
        if not isinstance(reference, dict) or 'format' in reference:
            fail('not_a_reference', 409)
        captured = result.get('metadata', {}).get('contracts', {}).get(side, {}).get(name)
        if not isinstance(captured, str):
            fail('no_captured_definition', 409)
        definition = jsonio.decode_exact(captured)
        if contract_digest(definition) != reference.get('digest'):
            fail('digest_mismatch', 409)
        checks.parse_contract(definition)
        obj[side][name]['contract'] = definition
        result['definition_text'] = jsonio.encode_exact(obj)
    elif operation.get('kind') == 'description':
        # Standard descriptions belong to a saved revision now (block_metadata_refactor spec v2, section 3).
        from .workshop_assist import moved
        raise moved()
    else:
        fail()
    return result


def examples(identifier):
    """Small fixed examples, with packaged human review distinct from user verification."""
    if identifier == 'restore_placeholders':
        return restore_examples()
    text_cases = [('short', 'three blue notebooks'), ('empty', '')]
    result = []
    for i, (label, text) in enumerate(text_cases):
        inputs = {'text': text}
        expectation = {'kind': 'review', 'criterion': 'Review the answer against the stated task and source.'}
        if identifier == 'count_text':
            expectation = {'kind': 'whole_output', 'values': {'out': '3' if i == 0 else '0'}}
        elif identifier == 'format_report':
            expectation = {'kind': 'whole_output', 'values': {'out': text}}
        elif identifier == 'select_fields':
            expectation = {'kind': 'whole_output', 'values': {'out': jsonio.encode_exact({'text': text})}}
        elif identifier == 'check_source':
            inputs['source'] = 'An order for three blue notebooks.'
        elif identifier == 'extract_fields':
            inputs['text'] = 'send three blue notebooks' if i == 0 else 'send some blue notebooks'
            expectation = {'kind': 'fields', 'assertions': [{'output': 'out', 'path': ['quantity'], 'operation': 'equals', 'value_text': '3'}] if i == 0 else [{'output': 'out', 'path': ['quantity'], 'operation': 'absent'}]}
        result.append({'id': identifier + '-' + str(i + 1), 'version': 1, 'label': label, 'inputs': inputs,
                       'expectation': expectation, 'origin': 'packaged_human_reviewed', 'verified': True, 'held_out': i == 1})
    return result


RESTORE_PAIRS = [{'placeholder': '[PERSON_1]', 'original': 'Jane Example'},
                 {'placeholder': '[DATE_1]', 'original': '3 March'}]


def restore_examples():
    """Both pass under the default parameters; the second leaves one mapped placeholder unused."""
    cases = [('restored', '[PERSON_1] reported fatigue on [DATE_1].', 'Jane Example reported fatigue on 3 March.'),
             ('one pair unused', '[PERSON_1] reported fatigue.', 'Jane Example reported fatigue.')]
    return [{'id': 'restore_placeholders-' + str(i + 1), 'version': 1, 'label': label,
             'inputs': {'text': text, 'pairs': jsonio.encode_exact(RESTORE_PAIRS)},
             'expectation': {'kind': 'whole_output', 'values': {'out': out}},
             'origin': 'packaged_human_reviewed', 'verified': True, 'held_out': i == 1}
            for i, (label, text, out) in enumerate(cases)]
