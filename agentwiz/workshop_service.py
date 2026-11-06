"""Headless Workshop authoring, frozen trials and serial comparison coordinator."""
from __future__ import annotations

import ast
import copy
import dataclasses
import hashlib
import secrets
import threading
import time
from pathlib import Path

from . import blocks, checks, contract_schema, history, jsonio, model_step, runner, runs, task_settings as ts, templates
from . import workshop_editor as editor
from .state import RoleUnresolved
from .workshop import TOKEN, WorkshopError
from .workshop_assets import Assets, Capture, decode_files, document, fail, fingerprint, head, key_name, read, relative, wire_files
from .workspace import write_atomic_bytes

PREVIEW_LIMIT = 32 << 20
MANIFEST_LIMIT = 64 << 20

# Contract synthesis (overhaul spec 14). Server-owned; the caller never chooses a policy or validator.
SYNTHESIS_ROLE = 'write_contract'
SYNTHESIS_PROMPT_ID = 'write_contract'
SYNTHESIS_PROMPT_VERSION = 1
SYNTHESIS_VALIDATOR = 'contract_synthesis_v1'
SYNTHESIS_TASK_LIMIT = 16384
SYNTHESIS_FIELDS = {'task_description', 'output_name', 'definition_text', 'role'}
SYNTHESIS_OPTIONAL = {'settings_text', 'max_tokens'}
_SYNTHESIS_WRAPPER = {
    'type': 'object',
    'properties': {
        'description': {'type': 'string', 'maxLength': 4096},
        'schema': {'type': 'object'},
        'notes': {'type': 'array', 'items': {'type': 'string', 'maxLength': 2048}, 'maxItems': 20},
    },
    'required': ['description', 'schema', 'notes'],
    'additionalProperties': False,
}


def synthesis_prompt():
    return (Path(__file__).parent / 'prompts' / 'write_contract.md').read_text(encoding='utf-8')


def source_digest(definition_text):
    """The draft a synthesis is bound to; a null definition is a new empty draft and is bound too."""
    return digest({'definition_text': definition_text})


def synthesis_request(body):
    """Validate a synthesis body and build the complete ephemeral internal definition on the server."""
    from . import contract_service, workshop_assist
    from .contract_schema import IDENT, DefinitionError
    if not isinstance(body, dict) or not SYNTHESIS_FIELDS <= set(body) or set(body) - SYNTHESIS_FIELDS - SYNTHESIS_OPTIONAL:
        fail()
    task = body['task_description']
    if not isinstance(task, str) or not task.strip() or len(task) > SYNTHESIS_TASK_LIMIT \
            or any(0xD800 <= ord(c) <= 0xDFFF for c in task):
        fail()
    name = body['output_name']
    if not isinstance(name, str) or IDENT.fullmatch(name) is None:
        fail()
    if body['role'] != SYNTHESIS_ROLE:
        fail()
    current = 'none'
    if body['definition_text'] is not None:
        try:
            projected = contract_service.project(body['definition_text'])
        except DefinitionError as exc:
            raise WorkshopError('contract_invalid', 400, exc.issues) from None
        if projected['format'] != 'json':
            # Text is authored with the readable form; synthesis is for schema-backed JSON.
            raise WorkshopError('invalid', 400, [{'location': 'definition_text', 'message': 'synthesis needs a JSON contract'}])
        definition = jsonio.decode_exact(body['definition_text'])
        current = jsonio.encode_exact(definition['schema']) if 'schema' in definition else 'none'
    if 'max_tokens' in body and (type(body['max_tokens']) is not int or not 1 <= body['max_tokens'] <= 1 << 20):
        fail()
    task_settings = workshop_assist._assist_task({k: body[k] for k in ('settings_text', 'max_tokens') if k in body})
    prompt = synthesis_prompt()
    text = {'format': 'text'}
    definition = {'format_version': blocks.FORMAT_VERSION, 'name': 'contract_synthesis',
                  'description': 'Contract synthesis',
                  'inputs': {'task_description': {'contract': text}, 'output_name': {'contract': text},
                             'current_schema': {'contract': text}},
                  'outputs': {'proposal': {'description': 'The proposed contract',
                                           'contract': {'format': 'json', 'schema': _SYNTHESIS_WRAPPER}}},
                  'steps': [{'id': 'synthesize', 'kind': 'model', 'role': SYNTHESIS_ROLE, 'system': {'text': ''},
                             'prompt': {'text': prompt}, 'response_mode': 'named_json', 'task': task_settings,
                             'repair': {'max_repairs': 1}}],
                  'budget': {'max_seconds': 1800}}
    return {'draft': {'key': definition['name'], 'definition_text': jsonio.encode_exact(definition), 'files': {},
                      'metadata': {'format_version': 1, 'origin': 'manual', 'prompt_id': SYNTHESIS_PROMPT_ID,
                                   'prompt_version': SYNTHESIS_PROMPT_VERSION}},
            'inputs': {'task_description': task, 'output_name': name, 'current_schema': current}}


def proposal_definition(response):
    """The draft definition a validated synthesis response yields: a direct subset schema, never saved. Built by
    the same `contract_schema.synthesis_definition` that acceptance validated; validated again defensively."""
    definition = contract_schema.synthesis_definition(response)
    contract_schema.validate_user_definition(definition)
    return definition


def before_trial_dispatch(batch_id, row):
    """No-op ordering hook after durable intent and before a child can dispatch."""


def before_manifest_write(batch_id):
    """No-op ordering hook, including the final write while coordinator owns deletion."""


def digest(value):
    return hashlib.sha256(jsonio.encode_exact(value).encode('utf-8')).hexdigest()


def load_capture(capture, key):
    p = capture.parsed
    if p.problem is not None:
        fail('unusable', 409)
    return blocks.Loaded(key, 1, p.block, None, p.description, p.kind, p.views, p.notes,
                         p.paths, p.contract_pins, (), (), capture.metadata, capture.root)


def exact_equal(a, b):
    return checks.exact_equal(a, b)


def judgement(outputs, expectation, verified):
    if not outputs:
        return 'unknown'
    if not verified or expectation.get('kind') == 'review':
        return 'unknown'
    ok = True
    if expectation.get('kind') == 'whole_output':
        for name, text in expectation['values'].items():
            output = outputs.get(name)
            if output is None:
                return 'incorrect'
            expected = text if output['format'] == 'text' else jsonio.decode_exact(text)
            actual = output['text'] if output['format'] == 'text' else jsonio.decode_exact(output['text'])
            ok = ok and exact_equal(actual, expected)
    elif expectation.get('kind') == 'fields':
        for item in expectation['assertions']:
            current = jsonio.decode_exact(outputs[item['output']]['text'])
            present = True
            for segment in item['path']:
                if isinstance(current, dict) and segment in current:
                    current = current[segment]
                elif isinstance(current, list) and type(segment) is int and 0 <= segment < len(current):
                    current = current[segment]
                else:
                    present = False
                    break
            op = item['operation']
            ok = ok and ((not present) if op == 'absent' else present if op == 'present' else
                         present and current is None if op == 'null' else
                         present and exact_equal(current, jsonio.decode_exact(item['value_text'])))
    else:
        return 'unknown'
    return 'correct' if ok else 'incorrect'


class Service:
    def __init__(self, state):
        self.state = state
        self.operations = state.workshop_operations
        self.lock = self.operations.lock
        self.assets = Assets(state, self.lock)
        self.batches = {}
        self.active = None
        self.latest = None
        self.proposals = {}
        self.session_reviews = {}
        self.salt = secrets.token_hex(32)
        self.credential_identities = {}
        self.state.blocks.workshop = self.assets
        self.assets.recover()

    def source(self, body):
        if 'draft' in body:
            draft = copy.deepcopy(body['draft'])
        elif 'starter' in body:
            draft = editor.starter(body['starter'], body.get('key', 'new_block'), body.get('role'), body.get('server'), body.get('tool'))
        else:
            draft = self.assets.export(body.get('key'), body.get('revision'), body.get('support'))
        root = self.assets.path / key_name(draft['key'])
        if root.exists():
            current = head(self.assets.root(draft['key']))
            if current:
                metadata = document(root / '.workshop' / 'revisions' / current['current'], 'workshop.json')
                if metadata.get('origin') in ('llm_generated', 'derived_from_llm'):
                    draft.setdefault('metadata', {})['origin'] = 'derived_from_llm'
        if 'edit' in body:
            draft = editor.edit(draft, body['edit'])
        if 'duplicate_key' in body:
            obj = jsonio.decode_exact(draft['definition_text'])
            draft['key'] = key_name(body['duplicate_key'])
            obj['name'] = draft['key']
            draft['definition_text'] = jsonio.encode_exact(obj)
            draft.pop('base_digest', None)
            draft.pop('root_sources', None)
            draft.setdefault('metadata', {}).pop('revision_number', None)
            draft['metadata'].pop('revision_name', None)
        if 'display_name' in body:
            draft.setdefault('metadata', {})['display_name'] = body['display_name']
        return draft

    # Fields an explicit saved selector never accepts (spec v2 2.1(3)): a saved request is a clean selection.
    SAVED_FORBIDDEN = ('draft', 'starter', 'edit', 'duplicate_key', 'display_name', 'support', 'metadata')

    def saved_selection(self, body, *, key_only=False):
        """The SavedGeneration a request explicitly selects, or None for the draft/key-only paths.

        An explicit `{key, revision}` is always a clean saved selection and refuses authoring fields. With
        `key_only`, a bare `{key}` of a managed block also resolves its current generation once (previews);
        runs keep the existing key-only path."""
        if not isinstance(body, dict):
            fail()
        if 'revision' in body:
            bad = [k for k in self.SAVED_FORBIDDEN if k in body]
            if bad:
                raise WorkshopError('invalid', 400, [{'location': name, 'message': 'is not accepted with an explicit saved revision; '
                                                      'use Restore to draft to edit a revision'} for name in bad])
            return self.assets.saved(key_name(body.get('key')), body['revision'])
        if not key_only or any(k in body for k in self.SAVED_FORBIDDEN):
            return None
        root = self.assets.path / key_name(body.get('key'))
        if not root.exists() or head(self.assets.root(body['key'])) is None:
            return None
        return self.assets.saved(body['key'])

    def preview(self, body):
        saved = self.saved_selection(body, key_only=True)
        if saved is not None:
            return self.saved_preview(saved, body)
        draft = self.source(body)
        with self.assets.validate(draft) as captured:
            return self._project(draft, captured, body)

    def saved_preview(self, saved, body):
        """The read-only projection of a saved generation, built from its own captured contracts. Live
        contract validation that a run would apply is reported separately as `run_issues`."""
        from . import workshop_descriptions as descriptions
        with self.assets.snapshot(saved) as captured:
            draft = saved.draft(base_digest=self.assets.identity(saved.key))
            result = self._project(draft, captured, body)
            result['selector'] = saved.selector
            result['revision_number'] = saved.number
            result['revision_name'] = saved.name
            result['run_issues'] = self.live_issues(saved)
            result['standard_description'] = descriptions.projection(self.assets.root(saved.key), saved,
                                                                     captured.parsed)
            return result

    def live_issues(self, saved):
        """The located issues a run of `saved` would meet under live contract validation, or []."""
        try:
            with self.assets.validate(saved.draft()) as live:
                return list(live.parsed.problem.issues) if live.parsed.problem else []
        except WorkshopError as exc:
            return [{'location': 'block', 'message': 'the revision cannot be validated (' + exc.code + ')'}]

    def _project(self, draft, captured, body):
        p = captured.parsed
        result = {'format_version': 1, 'draft': draft, 'source_digest': captured.digest,
                  'issues': list(p.problem.issues) if p.problem else [], 'kind': p.kind,
                  'classification': editor.classification(draft) if p.kind == 'script' else None}
        definition = jsonio.decode_exact(draft['definition_text'])
        result['valid'] = p.problem is None
        result['input_ports'] = list(definition.get('inputs', {}))
        result['contracts'] = {'inputs': {}, 'outputs': {}}
        contract_store = self.assets.contracts()
        for side in ('inputs', 'outputs'):
            for name, declaration in definition.get(side, {}).items():
                raw = declaration.get('contract') if isinstance(declaration, dict) else None
                if isinstance(raw, dict) and 'format' not in raw:
                    try:
                        found, _refused = contract_store.resolve(raw.get('name'), raw.get('revision'), raw.get('digest'))
                    except (TypeError, AttributeError):
                        found = None
                    raw = dict(found.contract.raw) if found is not None else None
                if isinstance(raw, dict):
                    try:
                        result['contracts'][side][name] = editor.contract_view(jsonio.encode_exact(raw))
                    except (checks.ContractError, contract_schema.DefinitionError, WorkshopError):
                        pass
        step = next(iter(definition.get('steps', [])), {})
        def asset_text(value):
            if 'file' in value:
                return captured.files[value['file']].decode()
            return value.get('text', '')
        result['design'] = {'purpose': definition.get('description', ''), 'kind': step.get('kind'),
            'binding': step.get('role', step.get('server', '')), 'system': asset_text(step.get('system', {})),
            'behaviour': asset_text(step.get('prompt', {})) if step.get('kind') == 'model' else
                captured.files.get(step.get('file'), b'').decode() if step.get('kind') == 'script' else step.get('tool', ''),
            'arguments': jsonio.encode_exact(step.get('arguments', {})), 'budget': jsonio.encode_exact(definition.get('budget', {})), 'settings': jsonio.encode_exact(step.get('task', {})),
            'repairs': str(step.get('repair', {}).get('max_repairs', 2)),
            'sampling': {k:str(v) for k,v in step.get('task',{}).items() if k != 'advanced'},
            'resets': [name for name in step.get('task_resets', []) if isinstance(name, str)],
            'limits': {k:str(v) for k,v in definition.get('budget',{}).items() if v is not None}}
        for side in ('inputs', 'outputs'):
            result['design'][side] = [{'name': name, 'description': declaration.get('description', ''),
                'format': (declaration['contract'].get('format', 'shared') if isinstance(declaration['contract'], dict) else 'shared'),
                'reference': ({k: declaration['contract'].get(k) for k in ('name', 'revision', 'digest')}
                              if isinstance(declaration['contract'], dict) and 'format' not in declaration['contract'] else None),
                'source': jsonio.encode_exact(declaration['contract'])} for name, declaration in definition.get(side, {}).items()]
        if p.block:
            result['contracts'] = {side: {name: editor.contract_view(c.text) for name, c in ports.items()}
                                   for side, ports in p.block.contracts.items()}
            result['requires_checks'] = any(port.parsed.schema is not None for ports in p.block.contracts.values() for port in ports.values())
            result['setup_issues'] = []
            try:
                if p.kind == 'model':
                    snapshot = self.state.resolve_role(p.block.step.role)
                    result['destination'] = {'role': p.block.step.role, 'kind': snapshot.kind, 'base_url': snapshot.endpoint.base_url, 'model': snapshot.model, 'provider': snapshot.provider}
                    from .model_call import task_arguments
                    task_arguments(p.block.step.task, snapshot.kind, snapshot.provider)
                elif p.kind == 'tool':
                    snapshot = self.state.mcp_snapshot_for_block(p.block.step.server)
                    result['destination'] = {'server': p.block.step.server, 'tool': p.block.step.tool, 'url': snapshot.url}
                elif p.kind == 'script':
                    result['script_source'] = captured.files[p.block.step.script_file].decode('utf-8')
                    result['script_warning'] = 'Python runs with user-level filesystem and network access.'
            except Exception:
                result['setup_issues'].append({'location': 'behaviour', 'code': 'setup_unavailable'})
            result['facts'] = {'purpose': p.description, 'kind': p.kind,
                               'inputs': [d.name for d in p.block.inputs], 'outputs': [d.name for d in p.block.outputs],
                               'field_meanings': {side: {d.name: d.description for d in getattr(p.block, side)} for side in ('inputs', 'outputs')},
                               'role': p.block.step.role, 'server': p.block.step.server,
                               'limits': p.block.budget.view()}
            # Freshness follows executable meaning and captured bytes, not
            # JSON formatting or the draft's base64/plain-text wire spelling.
            step = p.block.step
            try:
                basis_defaults = dict(self.state.role_capture(step.role).defaults) if p.kind == 'model' else {}
                sources = dict(ts.resolve_effective(step.task, step.task_resets, basis_defaults).sources) \
                    if p.kind == 'model' else {}
            except Exception:                       # an unresolved role is reported by setup_issues
                basis_defaults, sources = {}, {}
            result['design']['sampling_source'] = sources
            result['design']['role_defaults'] = basis_defaults
            inputs = body.get('inputs')
            if inputs is not None:
                self.input_shape(p.block, inputs)
                checked = runner.check_inputs(p.block, inputs)
                result['input_checks'] = checked.results
                if checked.passed and p.kind == 'model':
                    # The one preparation path: a preview shows exactly what a run would send.
                    try:
                        role_capture = self.state.role_capture(p.block.step.role)
                        messages = runner.model_messages(p.block, checked.values, role_capture.defaults)
                    except RoleUnresolved as exc:
                        result.setdefault('prepared_issues', []).append({'location': 'steps[0].role',
                                                                         'message': str(exc)})
                    except (ts.TaskSettingError, templates.FillError) as exc:
                        location = getattr(exc, 'location', None)
                        result.setdefault('prepared_issues', []).append(
                            {'location': 'steps[0].task' if isinstance(exc, ts.TaskSettingError) else location,
                             'message': getattr(exc, 'message', None) or str(exc)})
                    else:
                        result['prepared'] = {
                            'system': messages.system_author, 'generated': messages.generated,
                            'system_sent': messages.system_sent, 'prompt': messages.prompt,
                            'response_mode': p.block.step.response_mode,
                            'task': messages.effective.task.request_shape(),
                            'sources': dict(messages.effective.sources),
                            'role_defaults': dict(role_capture.defaults),
                            'outputs': [{'name': spec.name, 'description': spec.description,
                                         'format': spec.label(), 'schema': spec.schema_text()}
                                        for spec in messages.plan.specs()]}
                elif checked.passed and p.kind == 'tool':
                    result['prepared'] = {'arguments_text': jsonio.encode_exact(templates.fill_arguments(p.block.step.arguments, checked.values)[0])}
        return result

    def check_new_groups(self, draft):
        """Save and Start refuse a guided group with no field that the saved block did not already hold empty.

        Editing may pass through an empty group, and a group that was already empty in the saved block stays
        as it is, so only a group the author has just left empty is refused, with its location.
        """
        definition = jsonio.decode_exact(draft['definition_text'])
        empty = editor.empty_groups(definition)
        if not empty:
            return
        saved = set()
        loaded = self.state.block_loaded(draft['key'])
        if loaded is not None and loaded.block is not None:
            try:
                saved = editor.empty_groups(jsonio.decode_exact(loaded.block.texts['block.json']))
            except Exception:                               # an unreadable saved block cannot excuse a new group
                saved = set()
        new = sorted(empty - saved)
        if new:
            raise WorkshopError('block_invalid', 409, [
                {'location': '.'.join([side, port, *path]), 'message': 'is a new group with no field: add at least one field to it'}
                for side, port, path in new])

    def input_shape(self, block, values):
        if not isinstance(values, dict) or set(values) != {d.name for d in block.inputs} or not all(isinstance(t, str) for t in values.values()):
            fail()

    def collection(self, key):
        key_name(key)
        root = self.state.workspace.path / 'workshop' / 'examples'
        if not (root / (key + '.json')).exists():
            return {'format_version': 1, 'revision': None, 'examples': []}
        obj = document(root, key + '.json', MANIFEST_LIMIT)
        if obj.get('format_version') != 1:
            fail('newer_format', 409)
        return obj

    def validate_examples(self, examples, block, arm=None):
        if not isinstance(examples, list) or len(examples) > 50:
            fail()
        if arm is not None:
            # A shared example that does not fit one arm's inputs is named with its revision (spec v2 2.1(5)).
            names = {d.name for d in block.inputs}
            for index, ex in enumerate(examples):
                if isinstance(ex, dict) and isinstance(ex.get('inputs'), dict):
                    missing, extra = sorted(names - set(ex['inputs'])), sorted(set(ex['inputs']) - names)
                    if missing or extra:
                        label = ex.get('label') if isinstance(ex.get('label'), str) and ex.get('label') else ex.get('id')
                        raise WorkshopError('example_incompatible', 400, [{
                            'location': f'examples[{index}].inputs',
                            'message': f'example "{label}" does not fit {arm}'
                                       + (': missing ' + ', '.join(missing) if missing else '')
                                       + ('; extra ' + ', '.join(extra) if extra else '')
                                       + '. Run the revisions individually, or choose a compatible example.'}])
        ids = set()
        for ex in examples:
            if not isinstance(ex, dict) or set(ex) - {'id', 'version', 'label', 'inputs', 'expectation', 'origin', 'verified', 'held_out'}:
                fail()
            if not isinstance(ex.get('id'), str) or ex['id'] in ids or type(ex.get('version')) is not int or ex['version'] < 1:
                fail()
            ids.add(ex['id'])
            self.input_shape(block, ex.get('inputs'))
            e = ex.get('expectation')
            if not isinstance(e, dict) or e.get('kind') not in ('whole_output', 'fields', 'review') or type(ex.get('verified', False)) is not bool:
                fail()
            if e['kind'] == 'whole_output':
                if set(e) != {'kind', 'values'} or set(e['values']) != {d.name for d in block.outputs} or not all(isinstance(t, str) for t in e['values'].values()):
                    fail()
            elif e['kind'] == 'fields':
                if set(e) != {'kind', 'assertions'} or not isinstance(e['assertions'], list):
                    fail()
                for row in e['assertions']:
                    if set(row) - {'output', 'path', 'operation', 'value_text'} or row.get('output') not in block.contracts['outputs'] or row.get('operation') not in ('equals', 'absent', 'present', 'null') or not isinstance(row.get('path'), list):
                        fail()
                    if any(type(s) not in (str, int) for s in row['path']):
                        fail()
                    if row['operation'] == 'equals':
                        jsonio.decode_exact(row['value_text'])
            elif set(e) != {'kind', 'criterion'} or not isinstance(e['criterion'], str):
                fail()

    def save_examples(self, key, body):
        key_name(key)
        if self.state.workspace.read_only:
            fail('read_only', 409)
        writer = self.state.register_writer('Workshop examples')
        try:
            with self.assets.reserve(key, 'examples'):
                previous = self.collection(key)
                if previous['revision'] != body.get('expected_revision'):
                    fail('stale', 409)
                draft = self.assets.export(key, support=body.get('support'))
                with self.assets.validate(draft) as c:
                    self.validate_examples(body.get('examples'), c.parsed.block)
                old = {e['id']: e for e in previous['examples']}
                examples = copy.deepcopy(body['examples'])
                for ex in examples:
                    before = old.get(ex['id'])
                    if before and (before['inputs'] != ex['inputs'] or before['expectation'] != ex['expectation']):
                        if ex['version'] <= before['version']:
                            fail('stale', 409)
                        ex['verified'] = False
                result = {'format_version': 1, 'revision': secrets.token_hex(16), 'examples': examples}
                self.reject_credentials(result)
                root = self.safe_directory('examples')
                write_atomic_bytes(root, key + '.json', jsonio.encode_file_exact(result))
                return result
        finally:
            writer.release()

    def safe_directory(self, name):
        root = self.state.workspace.path / 'workshop'
        for p in (root, root / name):
            if p.is_symlink():
                fail('unusable', 409)
            p.mkdir(exist_ok=True, mode=0o700)
        return root / name

    def prepare(self, action, body):
        captures = []
        original = body
        validators = None
        describe = None
        if action == 'assist' and isinstance(body, dict) and body.get('action') == 'describe':
            # Server-owned Describe/Refine (spec v2 4.2), routed before capability enumeration, role selection
            # or draft forwarding. The builder owns the role, context, prompt, schema and options.
            if not checks.schema_support()[0]:
                fail('validation_unavailable', 503)
            describe = self.describe_request({k: v for k, v in body.items()
                                              if k not in ('expected_plan', 'confirmations', 'ticket')})
            body = {**body, 'draft': describe['draft'], 'inputs': describe['inputs']}
        elif action == 'contract_synthesize':
            if not checks.schema_support()[0]:
                fail('validation_unavailable', 503)
            # Entirely server-built: nothing from the caller is merged into the definition.
            body = synthesis_request({k: v for k, v in body.items() if k not in ('expected_plan', 'confirmations', 'ticket')})
            validators = {('outputs', 'proposal'): SYNTHESIS_VALIDATOR}
            try:
                self.state.resolve_role(SYNTHESIS_ROLE)
            except RoleUnresolved as exc:
                # A setup failure, shown before anything is admitted; never repaired.
                raise WorkshopError('role_unresolved', 409, [{'location': 'role', 'message': str(exc)}]) from None
        elif action == 'assist':
            from . import workshop_assist
            if not checks.schema_support()[0]:
                fail('validation_unavailable', 503)
            selected = dict(body)
            with self.state._lock:
                roles = list(self.state._roles)
                servers = list(self.state._mcp.servers)
            offered = {}
            for server in servers:
                snap = self.state.mcp_snapshot_for_block(server)
                offered[server] = self.state.mcp_client.observed_tools(snap.identity)
            if body.get('draft'):
                original_definition = jsonio.decode_exact(body['draft']['definition_text'])
                for step in original_definition.get('steps', []):
                    if step.get('role') and step['role'] not in roles:
                        roles.append(step['role'])
                    if step.get('server') and step.get('tool'):
                        names = offered.setdefault(step['server'], [])
                        if step['tool'] not in names:
                            names.append(step['tool'])
            selected['capabilities'] = {'roles': roles, 'tools': offered}
            for field in ('draft', 'task', 'evidence', 'capabilities'):
                if field in selected:
                    selected[field], changed = history.redact(selected[field], self.state.credentials())
            body = {**body, **workshop_assist.request(selected)}
        descriptor = {'format_version': 1, 'action': action, 'body_digest': digest({k: v for k, v in original.items() if k not in ('expected_plan', 'confirmations', 'ticket')}),
                      # Describe/Refine is session-only even when Save runs is on (spec v2 4.3).
                      'saving': False if describe is not None else self.state.save_runs_now(),
                      'read_only': self.state.workspace.read_only}
        confirmations = []
        if action == 'save':
            draft = self.source(body)
            c = self.assets.validate(draft)
            captures.append(c)
            if c.parsed.problem:
                problem = c.parsed.problem
                c.close()
                raise WorkshopError('unusable' if problem.code == 'unreadable' else 'block_invalid', 409, problem.issues)
            self.check_schema_available(c.parsed.block)
            try:
                self.check_new_groups(draft)
            except WorkshopError:
                c.close()
                raise
            descriptor['source'] = c.digest
            descriptor['current'] = self.assets.identity(draft['key'])
            if descriptor['current'] != body.get('expected_identity'):
                fail('stale', 409)
            descriptor['metadata'] = draft.get('metadata', {})
            if descriptor['current'] is not None:
                root = self.assets.root(draft['key'])
                h = head(root)
                if h is None:
                    confirmations.append('legacy_conversion')
                else:
                    descriptor['pruned'] = h['retained'][19:]
                    previous_metadata = document(root / '.workshop' / 'revisions' / h['current'], 'workshop.json')
                    descriptor['previous_starter_version'] = previous_metadata.get('starter_version')
                    descriptor['previous_runtime_digest'] = previous_metadata.get('runtime_digest')
                    descriptor['runtime_updated'] = previous_metadata.get('runtime_digest') != draft.get('metadata', {}).get('runtime_digest')
            if c.parsed.kind == 'script':
                confirmations.append('support_set')
            source_hash = fingerprint({p: b for p, b in c.files.items() if p != 'block.json'})
            if c.parsed.kind == 'script' and draft.get('metadata', {}).get('origin') in ('llm_generated', 'derived_from_llm'):
                descriptor['source_review'] = source_hash
                if not self.reviewed(draft['key'], source_hash):
                    confirmations.append('generated_source')
            descriptor['draft'] = draft
        elif action in ('test', 'batches', 'assist', 'contract_synthesize'):
            candidates = body.get('candidates') if action == 'batches' else [body]
            if not isinstance(candidates, list) or not 1 <= len(candidates) <= 2:
                fail()
            descriptor['candidates'] = []
            examples = body.get('examples') if action == 'batches' else [{'id': 'one_off', 'version': 1, 'inputs': body.get('inputs', {}), 'expectation': {'kind': 'review', 'criterion': ''}, 'verified': False}]
            internal = action in ('assist', 'contract_synthesize')
            if action == 'batches' and 'example_selection' in body:
                selection = body['example_selection']
                collection = self.collection(selection['key'])
                if collection['revision'] != selection.get('revision'):
                    fail('stale', 409)
                by_id = {e['id']: e for e in collection['examples']}
                if any(i not in by_id for i in selection['ids']):
                    fail('stale', 409)
                examples = [by_id[i] for i in selection['ids']]
            for candidate in candidates:
                # The private policy is only for the complete ephemeral definition
                # generated above. Do not apply caller edit/duplicate/source options
                # to that server-owned definition before granting internal policy.
                saved = None if internal else self.saved_selection(candidate)
                if internal:
                    draft = copy.deepcopy(body['draft'])
                elif saved is not None:
                    # A clean saved selection bypasses source(): the generation's own metadata, no authoring
                    # mutation and no current-head token in the run binding (spec v2 2.1(3)-(4)).
                    draft = saved.draft()
                else:
                    draft = self.source(candidate)
                if captures and draft['key'] != jsonio.decode_exact(captures[0].files['block.json'].decode())['name']:
                    fail('different_block', 400)
                c = (self.assets._validate(draft, internal_contracts=True, validators=validators) if internal
                     else self.assets.validate(draft))
                captures.append(c)
                if c.parsed.problem:
                    problem = c.parsed.problem
                    for capture in captures:
                        capture.close()
                    raise WorkshopError('unusable' if problem.code == 'unreadable' else 'block_invalid', 409, problem.issues)
                b = c.parsed.block
                self.check_schema_available(b)
                if 'draft' in candidate:
                    try:
                        self.check_new_groups(draft)
                    except WorkshopError:
                        for capture in captures:
                            capture.close()
                        raise
                arm = ('revision ' + str(saved.number)) if saved is not None else 'the current draft' if 'draft' in candidate else 'the saved block'
                self.validate_examples(examples, b, arm if action == 'batches' else None)
                snapshot = self.state.resolve_role(b.step.role) if b.step.kind == 'model' else self.state.mcp_snapshot_for_block(b.step.server) if b.step.kind == 'tool' else None
                role_capture = None
                if b.step.kind == 'model':
                    role_capture = self.state.role_capture(b.step.role)
                    snapshot = role_capture.snapshot
                    key = self.state.key_for(snapshot.kind, snapshot.endpoint.base_url)
                    snapshot = dataclasses.replace(snapshot, endpoint=dataclasses.replace(snapshot.endpoint, api_key=key, api_key_env=None))
                    role_capture = dataclasses.replace(role_capture, snapshot=snapshot)
                    try:
                        effective = ts.resolve_effective(b.step.task, b.step.task_resets, role_capture.defaults)
                    except ts.TaskSettingError as exc:
                        for capture in captures:
                            capture.close()
                        raise WorkshopError('block_invalid', 409, [{'location': 'steps[0].task',
                                                                     'message': str(exc)}]) from None
                    from .model_call import task_arguments
                    task_arguments(effective.task, snapshot.kind, snapshot.provider)
                    fresh_identity = secrets.token_hex(16)
                    with self.lock:
                        credential_identity = self.credential_identities.setdefault(key, fresh_identity)
                c.snapshot = snapshot
                c.role_capture = role_capture
                record = {'source_digest': c.digest, 'draft': draft, 'kind': b.step.kind, 'effects': draft.get('metadata', {}).get('effects', 'unknown'),
                          'contracts': {side: {n: port.text for n, port in ports.items()} for side, ports in b.contracts.items()},
                          'backend_conditions': {'quantisation': 'unknown', 'backend_template': 'unknown', 'implementation': 'unknown'}}
                if saved is not None:
                    # The explicit durable ID is part of the binding, so byte-identical generations stay distinct.
                    record.update(saved.provenance)
                if b.step.kind == 'model':
                    record['destination'] = {'kind': snapshot.kind, 'base_url': snapshot.endpoint.base_url, 'model': snapshot.model, 'provider': snapshot.provider}
                    record['settings'] = editor.typed(effective.task.request_shape())
                    record['settings_source'] = dict(effective.sources)
                    record['role_defaults'] = dict(role_capture.defaults)
                    # The protocol renderer and what it renders are part of what was reviewed, and Start
                    # freezes them with the batch (0.2.0 decision 4).
                    plan_view = model_step.OutputPlan.of(b.outputs, b.contracts['outputs']).view()
                    record['output_plan'] = {k: plan_view[k] for k in ('mode', 'renderer', 'digest')}
                    record['credential_identity'] = credential_identity
                elif b.step.kind == 'tool':
                    # Configuration snapshot stays in memory. Only sanitized identity is displayed.
                    record['destination'] = {'server': b.step.server, 'tool': b.step.tool}
                    record['configuration_identity'] = digest(str(snapshot))
                    record['schema_observation'] = self.state.mcp_client.observation(snapshot.identity, b.step.tool)
                if b.step.kind == 'script' and draft.get('metadata', {}).get('origin') in ('llm_generated', 'derived_from_llm'):
                    source_hash = fingerprint({p: data for p, data in c.files.items() if p != 'block.json'})
                    if not self.reviewed(draft['key'], source_hash):
                        confirmations.append('generated_source')
                descriptor['candidates'].append(record)
            descriptor['examples'] = examples
            descriptor['trial_count'] = len(examples) * len(captures)
            descriptor['comparison_kind'] = 'general'
            descriptor['repair_bounds'] = [c.parsed.block.step.max_repairs if c.parsed.kind == 'model' else None for c in captures]
            if descriptor['trial_count'] > 1 and any(c.parsed.kind != 'model' and r['effects'] != 'read-only' for c, r in zip(captures, descriptor['candidates'])):
                confirmations.append('repeat_effects')
        elif action == 'proposals/apply':
            from . import workshop_assist
            proposal = self.proposal(body['proposal_id'])
            if proposal.get('kind') != 'block':
                fail('invalid', 400)
            descriptor['proposal'] = proposal
            candidate = workshop_assist.apply({**proposal['request'], 'draft': body.get('draft')}, proposal['response'])
            if 'draft' in candidate:
                definition = jsonio.decode_exact(candidate['draft']['definition_text'])
                old = body.get('draft') or {}
                previous = jsonio.decode_exact(old.get('definition_text', '{}'))
                descriptor['changes'] = [{'name': field, 'before': jsonio.encode_exact(previous.get(field)),
                                          'after': jsonio.encode_exact(definition.get(field))}
                                         for field in ('description','inputs','outputs','steps','budget')
                                         if previous.get(field) != definition.get(field)]
                if definition.get('steps') and definition['steps'][0].get('kind') == 'script':
                    path = definition['steps'][0]['file']
                    before = decode_files(old.get('files', {})).get(path, b'')
                    after = decode_files(candidate['draft']['files']).get(path, b'')
                    if before != after:
                        descriptor['changes'].append({'name':'Python source','before':before.decode(),'after':after.decode()})

                if definition.get('steps') and definition['steps'][0].get('kind') == 'script' and editor.classification(candidate['draft']) == 'custom':
                    descriptor['generated_source'] = fingerprint(decode_files(candidate['draft']['files']))
                    confirmations.append('generated_source')
        else:
            fail()
        # Private credential identities participate in binding but are never returned.
        if action == 'contract_synthesize':
            descriptor['synthesis'] = {k: v for k, v in original.items() if k not in ('expected_plan', 'confirmations', 'ticket')}
            descriptor['synthesis']['source_digest'] = source_digest(original.get('definition_text'))
            descriptor['synthesis']['prompt'] = {'id': SYNTHESIS_PROMPT_ID, 'version': SYNTHESIS_PROMPT_VERSION}
        if describe is not None:
            # Private: binds the target, the prior proposal and the feedback. Public routes use describe_public.
            descriptor['describe'] = describe['target']
            descriptor['assistance'] = {k: v for k, v in original.items() if k not in ('expected_plan', 'confirmations', 'ticket')}
        elif action == 'assist':
            descriptor['assistance'] = {k: v for k, v in original.items() if k not in ('expected_plan', 'confirmations', 'ticket')}
            descriptor['assistance']['capabilities'] = selected['capabilities']
        binding = copy.deepcopy(descriptor)
        for candidate in binding.get('candidates', []):
            candidate.pop('schema_observation', None)
        identity = digest({'salt': self.salt, 'descriptor': binding})
        public = copy.deepcopy(descriptor)
        for item in public.get('candidates', []):
            item.pop('credential_identity', None)
            item.pop('configuration_identity', None)
        required = [{'kind': kind, 'digest': digest({'plan': identity, 'kind': kind}), 'detail': kind.replace('_', ' ')} for kind in sorted(set(confirmations))]
        return {'format_version': 1, 'expected_plan': identity, 'descriptor': public, 'required_confirmations': required}, captures

    def plan(self, action, body):
        plan, captures = self.prepare(action, body)
        for c in captures:
            c.close()
        return self.public_plan(plan)

    def public_plan(self, plan):
        if plan['descriptor'].get('describe'):
            return {**plan, 'descriptor': self.describe_public(plan['descriptor'])}
        return plan

    def check_plan(self, plan, body):
        expected = {(r['kind'], r['digest']) for r in plan['required_confirmations']}
        actual = {(r.get('kind'), r.get('digest')) for r in body.get('confirmations', []) if isinstance(r, dict)}
        if body.get('expected_plan') != plan['expected_plan'] or not expected <= actual:
            error = WorkshopError('confirmation_required', 409)
            error.plan = self.public_plan(plan)
            raise error

    def save(self, body):
        plan, captures = self.prepare('save', body)
        try:
            self.check_plan(plan, body)
            draft = plan['descriptor']['draft']
            metadata = copy.deepcopy(draft.get('metadata', {}))
            metadata['format_version'] = 1
            # A new generation starts without standard prose and never inherits one (spec v2 section 3).
            metadata.pop('description_sections', None)
            metadata.pop('description_basis', None)
            if 'source_review' in plan['descriptor']:
                metadata['source_review'] = plan['descriptor']['source_review']
            from .workshop_assets import captured_maps, contract_maps_digest
            metadata['contracts'], metadata['contract_pins'] = captured_maps(captures[0].parsed.block)
            metadata['contracts_digest'] = contract_maps_digest(metadata['contracts'], metadata['contract_pins'])
            self.reject_credentials({'definition': draft['definition_text'], 'files': {p: b.decode('utf-8', errors='replace') for p, b in decode_files(draft['files']).items()}, 'metadata': metadata})
            result = self.assets.publish(draft, body.get('expected_identity'), metadata, captures[0])
            result['effective_revision'] = self.state.block_loaded(draft['key'], reload=True).revision
            return result
        finally:
            for c in captures:
                c.close()

    def start(self, action, body):
        duplicate = self.operations.lookup(body.get('ticket'), body)
        if duplicate['state'] != 'unused':
            return duplicate
        plan, captures = self.prepare(action, body)
        try:
            self.check_plan(plan, body)
            ident = secrets.token_hex(16)
            writer = self.state.register_writer('Workshop coordinator ' + ident)
            with self.lock:
                busy = self.active is not None
                if not busy:
                    self.active = ident
            if busy:
                writer.release()
                duplicate = self.operations.lookup(body.get('ticket'), body)
                if duplicate['state'] != 'unused':
                    return duplicate
                fail('busy', 503)
            try:
                status, claimed = self.operations.claim(body['ticket'], body, ident)
                if not claimed:
                    with self.lock:
                        self.active = None
                    writer.release()
                    return status
                rows = []
                exs = plan['descriptor']['examples']
                for i, ex in enumerate(exs):
                    order = list(range(len(captures)))
                    if i % 2:
                        order.reverse()
                    for arm in order:
                        rows.append({'trial_id': secrets.token_hex(16), 'arm': arm, 'example': ex, 'state': 'not_run',
                                     **revision_provenance(plan['descriptor'], arm)})
                batch = {'format_version': 1, 'id': ident, 'action': action, 'state': 'running', 'plan': plan['descriptor'],
                         'rows': rows, 'saving': plan['descriptor']['saving'], 'cancelled': False, 'active_run': None,
                         'preview_bytes': 0, 'ticket': body['ticket']}
                if len(jsonio.encode_exact(batch).encode()) > MANIFEST_LIMIT:
                    fail('too_large', 400)
                with self.lock:
                    self.batches[ident] = batch
                self.persist(batch)
                thread = threading.Thread(target=self.work, args=(batch, captures, writer), daemon=True)
                thread.start()
                captures = []
                return {**status, 'batch_id': ident}
            except BaseException:
                with self.lock:
                    self.active = None
                writer.release()
                self.operations.finish(body['ticket'], {'outcome': 'rejected'})
                raise
        finally:
            for c in captures:
                c.close()

    def persist(self, batch):
        if not batch['saving']:
            return
        before_manifest_write(batch['id'])
        with self.lock:
            snapshot = copy.deepcopy({k: v for k, v in batch.items() if k != 'ticket'})
        public, redacted = history.redact(snapshot, self.state.credentials())
        public['redacted'] = redacted
        write_atomic_bytes(self.safe_directory('comparisons'), batch['id'] + '.json', jsonio.encode_file_exact(public))

    def work(self, batch, captures, writer):
        service = self
        class Observer(runner.Observer):
            def __init__(self):
                self.first = None
                self.attempts = 0
                self.truncated = 0
                self.raw_answer = None
                self.output_checks = {}
                self.model_error = None

            def attempt_committed(self, record):
                self.attempts += 1
                if self.first is None:
                    self.first = {'outcome': record['outcome'], 'check': (record.get('check') or {}).get('overall')}
                self.truncated += record['outcome'].startswith('truncated')
                answer = (record.get('result') or {}).get('text')
                self.raw_answer = answer if isinstance(answer, str) and len(answer.encode('utf-8')) <= 512 << 10 else None
                self.output_checks = (record.get('check') or {}).get('outputs', {})
                self.model_error = record.get('error')

            def started(self, event):
                with service.lock:
                    batch['active_run'] = event['run_id']
                    stop = batch['cancelled']
                if stop:
                    service.state.store.stop_run(event['run_id'], runs.USER_STOP)
        reason = None
        try:
            for row in batch['rows']:
                with self.lock:
                    cancelled = batch['cancelled']
                if cancelled:
                    reason = 'stopped'
                    break
                capture = captures[row['arm']]
                if capture.parsed.kind == 'tool' and self.state.mcp_snapshot_for_block(capture.parsed.block.step.server) != capture.snapshot:
                    reason = 'dependency_changed'
                    break
                if fingerprint({p: read(capture.root, p) for p in capture.files}) != capture.digest:
                    reason = 'dependency_changed'
                    break
                with self.lock:
                    row['state'] = 'dispatch_pending'
                self.persist(batch)  # Durable intent MUST precede the first possible external action.
                before_trial_dispatch(batch['id'], row)
                loaded = load_capture(capture, jsonio.decode_exact(capture.files['block.json'].decode())['name'])
                metadata = loaded.workshop
                if metadata.get('origin') in ('llm_generated', 'derived_from_llm'):
                    metadata['source_review'] = fingerprint({p: b for p, b in capture.files.items() if p != 'block.json'})
                start = time.monotonic()
                frozen = runner.FrozenSource(capture.snapshot, batch['saving'], {'batch_id': batch['id'], 'trial_id': row['trial_id'],
                    'snapshot_id': capture.digest, 'example_id': row['example']['id'], 'example_version': row['example']['version'],
                    **revision_provenance(batch['plan'], row['arm'])}, capture,
                    getattr(capture, 'role_capture', None))
                observer = Observer()
                try:
                    result = runner.run_block(self.state, loaded, row['example']['inputs'], observer, frozen=frozen)
                except runner.Busy:
                    with self.lock:
                        row.update(state='not_run', reason='admission_busy')
                    reason = 'admission_busy'
                    break
                record = result.result
                text = jsonio.encode_exact(record['outputs'])
                complete = batch['preview_bytes'] + len(text.encode()) <= PREVIEW_LIMIT
                machine = judgement(record['outputs'], row['example']['expectation'], row['example'].get('verified', False))
                with self.lock:
                    row.update(state='terminal', run_id=result.run.run_id, snapshot_id=capture.digest,
                               outcome=record['outcome'], result_digest=digest(record['outputs']),
                               outputs=record['outputs'] if complete else {}, preview_incomplete=not complete,
                               correctness=machine, machine_judgement=machine,
                               seconds=time.monotonic() - start,
                               steps=[{k: v for k, v in step.items() if k not in ('operation', 'fills')} for step in record['steps']],
                               first_attempt=observer.first, attempts=observer.attempts, repairs=max(0, observer.attempts - 1),
                               truncated_attempts=observer.truncated, usage=record['usage'], save_status=result.save_status)
                    if observer.raw_answer is not None and batch['preview_bytes'] + len(observer.raw_answer.encode('utf-8')) <= PREVIEW_LIMIT:
                        row['raw_answer'] = observer.raw_answer
                        batch['preview_bytes'] += len(observer.raw_answer.encode('utf-8'))
                    row['output_checks'] = observer.output_checks
                    if batch['action'] == 'contract_synthesize' and observer.model_error:
                        # Already sanitized by model_step; never expose a raw backend exception.
                        row['model_error'] = observer.model_error
                if complete:
                    batch['preview_bytes'] += len(text.encode())
                with self.lock:
                    batch['active_run'] = None
                if batch['action'] == 'contract_synthesize' and record['outcome'] == 'complete':
                    response = jsonio.decode_exact(record['outputs']['proposal']['text'])
                    proposal_id = row['run_id']
                    with self.lock:
                        self.proposals[proposal_id] = {'kind': 'contract', 'response': response,
                            'base_digest': batch['plan']['synthesis']['source_digest'],
                            'provenance': synthesis_provenance(batch['plan'])}
                        while len(self.proposals) > 200:
                            self.proposals.pop(next(iter(self.proposals)))
                    row['proposal_id'] = proposal_id
                if batch['plan'].get('describe') and record['outcome'] == 'complete':
                    self.describe_complete(batch, row, record)
                elif batch['action'] == 'assist' and record['outcome'] == 'complete':
                    response_text = record['outputs']['proposal']['text']
                    if len(response_text.encode()) > 512 << 10:
                        fail('proposal_too_large', 409)
                    response = jsonio.decode_exact(response_text)
                    request = batch['plan']['assistance']
                    proposal_id = row['run_id']
                    with self.lock:
                        self.proposals[proposal_id] = {'kind': 'block', 'request': request, 'response': response, 'base_digest': digest(request.get('draft'))}
                        while len(self.proposals) > 200:
                            self.proposals.pop(next(iter(self.proposals)))
                    row['proposal_id'] = proposal_id
                self.persist(batch)  # Settle this row before admitting another child.
                if record['outcome'] in ('unknown', 'stopped', 'budget_exceeded') or result.save_status == 'failed':
                    reason = 'unknown_or_stopped' if result.save_status != 'failed' else 'save_failed'
                    break
        except Exception as exc:
            from .errors import report_internal
            report_internal('Workshop batch', exc)
            reason = 'coordinator_failed'
        finally:
            for row in batch['rows']:
                if row['state'] == 'dispatch_pending':
                    row.update(state='terminal', outcome='unknown', reason=reason)
                elif row['state'] == 'not_run':
                    row['reason'] = reason or 'not_run'
            batch['state'] = 'stopped' if reason else 'complete'
            batch['reason'] = reason
            try:
                self.persist(batch)
            except Exception:
                batch['persistence_failure'] = True
            # Own deletion until the final possible write has settled.
            with self.lock:
                self.active = None
                old = self.latest
                self.latest = batch['id']
                if old and old != batch['id']:
                    expired = self.batches.pop(old, None)
                    if expired:
                        for old_row in expired['rows']:
                            self.session_reviews.pop(old_row.get('run_id'), None)
            self.operations.finish(batch['ticket'], {'batch_id': batch['id'], 'outcome': batch['state']})
            writer.release()
            # The captured source is also held by a retained script cleanup's Env.
            # Dropping our reference allows cleanup only after the last actual owner exits.

    def batch(self, ident):
        with self.lock:
            b = self.batches.get(ident)
            result = copy.deepcopy(b) if b else None
        if result is None:
            root = self.state.workspace.path / 'workshop' / 'comparisons'
            # Coordinator records contain finite runtime timings. Exact authored data
            # is already stored as source strings, like the normal run records.
            # Use the strict record reader so a restored timing remains HTTP-safe;
            # it refuses any numeric spelling it cannot preserve.
            try:
                result = jsonio.decode_file(read(root, self.valid_id(ident) + '.json', MANIFEST_LIMIT))
            except ValueError:
                fail('unusable', 409)
            if result.get('format_version') != 1:
                fail('newer_format', 409)
            for row in result.get('rows', []):
                if row['state'] == 'dispatch_pending':
                    row.update(state='terminal', outcome='unknown', reason='interrupted')
            if result['state'] == 'running':
                result['state'] = 'interrupted'
        result.pop('ticket', None)
        for row in result.get('rows', []):
            if result.get('saving') and row.get('run_id'):
                try:
                    evidence = self.state.history.read_run(row['run_id'])
                    row['evidence_available'] = bool(evidence.get('result'))
                    review = evidence.get('current_review')
                    if review and review['context'].get('result_digest') == row.get('result_digest'):
                        row['human_rating'] = review['judgement']
                        row['correctness'] = review['judgement']
                except history.NoSuchRun:
                    row['evidence_available'] = False
        if result.get('plan', {}).get('describe'):
            return self.describe_public_batch(result)
        result['metrics'] = self.metrics(result)
        return result

    def metrics(self, batch):
        result = []
        for arm in range(len(batch['plan'].get('candidates', []))):
            model_only = batch['plan']['candidates'][arm]['kind'] == 'model'
            rows = [r for r in batch['rows'] if r['arm'] == arm]
            started = [r for r in rows if r['state'] != 'not_run']
            accepted = [r for r in rows if r.get('outcome') == 'complete']
            judged = [r for r in accepted if r.get('correctness') in ('correct', 'incorrect')]
            correct = [r for r in judged if r['correctness'] == 'correct']
            result.append({'arm': arm, 'started': len(started), 'accepted': len(accepted), 'judged': len(judged),
                           'correct': len(correct), 'not_run': len(rows) - len(started),
                           'incorrect': len(judged) - len(correct), 'unreviewed': len(accepted) - len(judged),
                           'no_accepted_output': len(started) - len(accepted),
                           'first_pass': sum(r.get('first_attempt') == {'outcome': 'complete', 'check': 'pass'} for r in rows),
                           'first_attempts_observed': sum(bool(r.get('first_attempt')) for r in rows),
                           'no_first_attempt': sum(not r.get('first_attempt') for r in started),
                           'repairs': sum(r.get('repairs', 0) for r in rows),
                           'reported_tokens': sum(r.get('usage', {}).get('reported_tokens', 0) for r in rows),
                           'usage_partial': any(r.get('usage', {}).get('unreported_attempts') for r in started),
                           'timing_partial': any('seconds' not in r for r in started),
                           'seconds_per_correct': str(sum(r.get('seconds', 0) for r in started) / len(correct)) if correct else None})
            if not model_only:
                for name in ('first_pass', 'first_attempts_observed', 'no_first_attempt', 'repairs'):
                    result[-1][name] = None
        return result

    def valid_id(self, ident):
        from .workshop import TOKEN
        if not isinstance(ident, str) or TOKEN.fullmatch(ident) is None:
            fail()
        return ident

    def stop(self, ident):
        with self.lock:
            b = self.batches.get(ident)
            if b is None:
                fail('missing', 404)
            b['cancelled'] = True
            run = b['active_run']
        if run:
            try:
                self.state.store.stop_run(run, runs.USER_STOP)
            except runs.NoSuchRunInMemory:
                pass
        return {'status': 'stopping'}

    def delete_comparison(self, ident):
        self.valid_id(ident)
        if self.state.workspace.read_only:
            fail('read_only', 409)
        writer = self.state.register_writer('Workshop comparison deletion')
        try:
            with self.lock:
                if self.active == ident:
                    fail('busy', 409)
                self.batches.pop(ident, None)
            root = self.state.workspace.path / 'workshop' / 'comparisons'
            target = root / (ident + '.json')
            if root.is_symlink() or target.is_symlink():
                fail('unusable', 409)
            target.unlink(missing_ok=True)
            return {'deleted': True}
        finally:
            writer.release()

    def proposal(self, ident):
        if not isinstance(ident, str) or not runs.valid_run_id(ident):
            fail('proposal_expired', 410)
        try:
            result = self.state.store.run_envelope(ident)['result']
        except (runs.NotHeld, KeyError):
            try:
                saved = self.state.history.read_run(ident)
                result = saved.get('result')
                if not result or result.get('redacted'):
                    fail('proposal_expired', 410)
            except history.NoSuchRun:
                fail('proposal_expired', 410)
        with self.lock:
            value = copy.deepcopy(self.proposals.get(ident))
        if value is not None:
            return value
        try:
            batch = self.batch(result['workshop']['batch_id'])
            if batch.get('redacted'):
                fail('proposal_expired', 410)
            response = jsonio.decode_exact(result['outputs']['proposal']['text'])
            if batch.get('action') == 'contract_synthesize':
                return {'kind': 'contract', 'response': response,
                        'base_digest': batch['plan']['synthesis']['source_digest'],
                        'provenance': synthesis_provenance(batch['plan'])}
            request = batch['plan']['assistance']
            return {'kind': 'block', 'request': request, 'response': response,
                    'base_digest': digest(request.get('draft'))}
        except (KeyError, ValueError, WorkshopError):
            fail('proposal_expired', 410)

    def support(self, key):
        root = self.assets.root(key)
        paths = []
        import os
        for folder, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith('.') and not (Path(folder) / d).is_symlink()]
            for name in files:
                path = Path(folder) / name
                if name in ('block.json', 'workshop.json') or path.is_symlink():
                    continue
                rel = path.relative_to(root).as_posix()
                if len(paths) >= 200:
                    break
                data = read(root, rel)
                paths.append({'path': rel, 'digest': hashlib.sha256(data).hexdigest()})
            if len(paths) >= 200:
                break
        current = self.assets.revisions(key)
        captured = []
        details = []
        if current['current']:
            m = current['revisions'][0]['metadata']
            captured = m.get('support', [])
            for path in captured:
                expected = m.get('assets', {}).get(path)
                observed = blocks.read_file(root, relative(path), 4 << 20)
                status = ('missing' if observed.fingerprint == ('missing',) else 'unavailable') if observed.data is None else 'same' if hashlib.sha256(observed.data).hexdigest() == expected else 'changed'
                details.append({'path': path, 'digest': expected, 'root_copy': status})
        return {'format_version': 1, 'candidates': paths, 'captured': captured, 'captured_details': details}

    def support_preview(self, key, body):
        draft = self.source({'draft': body['draft']}) if 'draft' in body else self.assets.export(key, support=body.get('initial_support'))
        if draft['key'] != key:
            fail()
        files = decode_files(draft['files'])
        root = self.assets.root(key)
        selected = list(draft.get('metadata', {}).get('support', []))
        sources = dict(draft.get('root_sources', {}))
        for path in body.get('remove', []):
            relative(path)
            if path in selected:
                selected.remove(path)
                files.pop(path, None)
                sources.pop(path, None)
        for path in body.get('replace', []):
            relative(path)
            files[path] = read(root, path)
            sources[path] = hashlib.sha256(files[path]).hexdigest()
            if path not in selected:
                selected.append(path)
        draft['files'] = wire_files(files)
        draft['root_sources'] = sources
        draft['metadata']['support'] = selected
        with self.assets.validate(draft) as c:
            return {'draft': draft, 'source_digest': c.digest, 'support': selected}

    def comparisons(self):
        from .workshop import TOKEN
        root = self.state.workspace.path / 'workshop' / 'comparisons'
        if root.is_symlink():
            fail('unusable', 409)
        names = [] if not root.exists() else [p.stem for p in root.iterdir() if p.suffix == '.json' and TOKEN.fullmatch(p.stem) and not p.is_symlink()]
        return {'format_version': 1, 'comparisons': sorted(names)[:200]}

    def assist(self, body):
        if not checks.schema_support()[0]:
            fail('validation_unavailable', 503)
        return self.start('assist', body)

    # --- revision descriptions (block_metadata_refactor spec v2) -----------------------------------------
    def describe_request(self, body):
        """The server-built ephemeral Describe/Refine request for one saved revision. Raises before dispatch."""
        from . import workshop_descriptions as descriptions
        mode, feedback = descriptions.check_request(body)
        key = key_name(body['key'])
        try:
            self.state.resolve_role(descriptions.ROLE)
        except RoleUnresolved as exc:
            # A setup failure, shown before anything is admitted. Manual editing stays available.
            raise WorkshopError('role_unresolved', 409, [{'location': 'role', 'message': str(exc)}]) from None
        saved = self.assets.saved(key, body['revision'])
        root = self.assets.root(key)
        prior = proposal_id = None
        with self.assets.snapshot(saved) as captured:
            parsed = captured.parsed
            if parsed.block is None or saved.basis is None:
                raise WorkshopError('description_unavailable', 409,
                                    list(parsed.problem.issues) if parsed.problem else [])
            ports = descriptions.ports_of(parsed.block)
            if mode == 'refine':
                proposal = self.description_proposal(body['proposal_id'])
                if (proposal['key'], proposal['revision']) != (key, saved.revision):
                    fail('proposal_target', 409)
                if proposal['basis'] != saved.basis:
                    fail('stale', 409)
                prior, base_token, proposal_id = proposal['candidate'], proposal['base_token'], body['proposal_id']
            else:
                state, data = descriptions.read_sidecar(root, saved.revision)
                base_token = descriptions.token(data) if state == 'present' else None
            context = descriptions.build_context(saved, parsed, proposal=prior, feedback=feedback,
                                                 credentials=self.state.credentials())
        definition = descriptions.ephemeral_definition(mode, ports)
        # The response schema names every port, so a credential in a field name would still travel there.
        if history.redact(jsonio.encode_exact(definition), self.state.credentials())[1]:
            raise WorkshopError('credential_in_context', 409, [
                {'location': 'context', 'message': 'a known credential remains in the outbound request; remove it first'}])
        draft = {'key': definition['name'], 'definition_text': jsonio.encode_exact(definition), 'files': {},
                 'metadata': {'format_version': 1, 'origin': 'manual', 'prompt_id': descriptions.PROMPT_ID,
                              'prompt_version': descriptions.PROMPT_VERSION}}
        target = {'mode': mode, 'key': key, 'revision': saved.revision, 'revision_number': saved.number,
                  'basis': saved.basis, 'base_token': base_token, 'ports': ports, 'prior': prior,
                  'proposal_id': proposal_id,
                  'prompt': {'id': descriptions.PROMPT_ID, 'version': descriptions.PROMPT_VERSION}}
        return {'draft': draft, 'inputs': {'context': context}, 'target': target}

    def describe_complete(self, batch, row, record):
        """Validate a completed Describe/Refine answer and store a session-only proposal. A failure is the
        normal terminal `description_invalid` outcome: no proposal ID, the prior proposal kept."""
        from . import workshop_descriptions as descriptions
        target = batch['plan']['describe']
        try:
            response = jsonio.decode_exact(record['outputs']['proposal']['text'])
            candidate, note, changed = descriptions.materialise(
                target['mode'], response, target['ports'], target['prior'], self.state.credentials())
        except descriptions.DescriptionInvalid as exc:
            with self.lock:
                row['description_issues'] = exc.issues
                batch['describe_outcome'] = 'description_invalid'
            return
        except (KeyError, TypeError, ValueError):
            with self.lock:
                row['description_issues'] = [{'location': 'proposal', 'message': 'could not be read'}]
                batch['describe_outcome'] = 'description_invalid'
            return
        before = target['prior']
        if before is None:
            root = self.assets.path / target['key']
            try:
                saved = self.assets.saved(target['key'], target['revision'])
                with self.assets.snapshot(saved) as captured:
                    current = descriptions.projection(root, saved, captured.parsed)
                if current['sections'] is not None:
                    before = {'sections': current['sections'], 'port_summaries': current['port_summaries']}
            except WorkshopError:
                before = None
        labels = descriptions.changed_labels(before, candidate)
        with self.lock:
            if batch['cancelled']:
                batch['describe_outcome'] = 'stopped'          # cancellation won: no new candidate
                return
            row['description_note'] = note
            if target['mode'] == 'refine' and not changed:
                batch['describe_outcome'] = 'unchanged'         # the prior proposal stays the reviewable one
                row['description_proposal_id'] = target['proposal_id']
                return
            proposal_id = secrets.token_hex(16)
            self.proposals[proposal_id] = {
                'kind': 'description', 'key': target['key'], 'revision': target['revision'],
                'revision_number': target['revision_number'], 'basis': target['basis'],
                'base_token': target['base_token'], 'candidate': candidate, 'note': note, 'labels': labels}
            while len(self.proposals) > 200:
                self.proposals.pop(next(iter(self.proposals)))
            row['description_proposal_id'] = proposal_id
            batch['describe_outcome'] = 'proposal'

    def description_proposal(self, ident):
        """A session-only description proposal from the bounded cache. Never consults run envelopes, batch
        retention or history. Expired, evicted, wrong-kind or pruned-target proposals answer 410."""
        if not isinstance(ident, str) or TOKEN.fullmatch(ident) is None:
            fail('proposal_expired', 410)
        with self.lock:
            value = copy.deepcopy(self.proposals.get(ident))
        if value is None or value.get('kind') != 'description':
            fail('proposal_expired', 410)
        try:
            self.assets.retained(value['key'], value['revision'])
        except WorkshopError:
            fail('proposal_expired', 410)
        return value

    def describe_public(self, descriptor):
        """The allowlisted plan projection of Describe/Refine: target, destination and persistence only. Never
        the context, feedback, prior proposal, prepared request or child run identifiers."""
        from types import SimpleNamespace

        from .roles import profile_location
        target = descriptor['describe']
        candidate = (descriptor.get('candidates') or [{}])[0]
        destination = dict(candidate.get('destination') or {})
        locality = 'unknown'
        if destination.get('kind'):
            locality = profile_location(SimpleNamespace(kind=destination['kind'],
                                                        base_url=destination.get('base_url')))
        return {'format_version': 1, 'action': 'assist',
                'describe': {'mode': target['mode'], 'key': target['key'], 'revision': target['revision'],
                             'revision_number': target['revision_number']},
                'destination': {'role': 'write_block_description', **destination, 'locality': locality},
                'saving': False, 'read_only': descriptor.get('read_only', False), 'trial_count': 1,
                'persistence': 'Session only. Only an accepted description is saved.'}

    def describe_public_batch(self, batch):
        rows = []
        proposal = None
        for row in batch.get('rows', []):
            rows.append({k: row[k] for k in ('state', 'outcome', 'reason', 'description_issues',
                                              'description_note') if k in row})
            ident = row.get('description_proposal_id')
            if ident:
                with self.lock:
                    value = copy.deepcopy(self.proposals.get(ident))
                if value is not None and value.get('kind') == 'description':
                    proposal = {'proposal_id': ident, 'key': value['key'], 'revision': value['revision'],
                                'revision_number': value['revision_number'], 'base_token': value['base_token'],
                                'sections': value['candidate']['sections'],
                                'port_summaries': value['candidate']['port_summaries'],
                                'note': row.get('description_note') or value['note'], 'changed': value['labels']}
        outcome = batch.get('describe_outcome') or ('stopped' if batch.get('state') in ('stopped', 'interrupted')
                                                    else None)
        return {'format_version': 1, 'id': batch['id'], 'action': 'assist', 'state': batch['state'],
                'reason': batch.get('reason'), 'cancelled': batch.get('cancelled', False),
                'outcome': outcome, 'plan': self.describe_public(batch['plan']), 'rows': rows,
                'proposal': proposal, 'saving': False}

    def save_description(self, key, ident, body):
        """PUT .../revisions/<id>/description: an accepted proposal or a manual edit. Writes only the sidecar."""
        from . import workshop_descriptions as descriptions
        kind = descriptions.check_write_body(body)
        key_name(key)
        if not isinstance(ident, str) or TOKEN.fullmatch(ident) is None:
            fail('missing', 404)
        proposal = None
        if kind == 'generated':
            proposal = self.description_proposal(body['proposal_id'])
            if (proposal['key'], proposal['revision']) != (key, ident):
                fail('proposal_target', 409)
        if self.state.workspace.read_only:
            fail('read_only', 409)
        writer = self.state.register_writer('Workshop description')
        try:
            with self.assets.reserve(key):
                saved = self.assets.saved(key, ident)                  # recheck retention: 404 when pruned
                root = self.assets.root(key)
                with self.assets.snapshot(saved) as captured:
                    parsed = captured.parsed
                    basis = saved.basis if parsed.block is not None else None
                    if basis is None:
                        fail('description_unavailable', 409)
                    ports = descriptions.ports_of(parsed.block)
                    if (body['basis'] if kind == 'manual' else proposal['basis']) != basis:
                        fail('stale', 409)
                    if kind == 'generated' and proposal['base_token'] != body['expected_token']:
                        fail('stale', 409)
                    source = proposal['candidate'] if kind == 'generated' else body
                    try:
                        sections, summaries = descriptions.normalise(source['sections'], source['port_summaries'],
                                                                     ports)
                        data = descriptions.encode_record(basis, 'model' if kind == 'generated' else 'manual',
                                                          sections, summaries)
                    except descriptions.DescriptionInvalid as exc:
                        raise descriptions.invalid(exc) from None
                    self.reject_credentials({'sections': sections, 'port_summaries': summaries})
                    state, existing = descriptions.read_sidecar(root, ident)
                    if state == 'unusable':
                        fail('description_unavailable', 409)
                    if state == 'present':
                        _record, reason = descriptions.parse_record(existing, basis, ports)
                        if reason in ('unreadable', 'unknown_format'):
                            fail('description_unavailable', 409)       # never overwrite what cannot be read
                    if descriptions.token(existing) != body['expected_token']:
                        fail('stale', 409)
                    descriptions.write_sidecar(root, ident, data)
                    return descriptions.projection(root, saved, parsed)
        finally:
            writer.release()

    # --- contract synthesis (overhaul spec 14) ----------------------------------------------------------
    def synthesis_plan(self, body):
        plan = self.plan('contract_synthesize', body)
        candidate = plan['descriptor']['candidates'][0]
        return {'plan': plan, 'destination': candidate.get('destination'),
                'source_draft_digest': plan['descriptor']['synthesis']['source_digest']}

    def synthesis_start(self, body):
        return self.start('contract_synthesize', body)

    def contract_proposal(self, proposal_id):
        proposal = self.proposal(proposal_id)
        if proposal.get('kind') != 'contract':
            fail('invalid', 400)
        return proposal

    def synthesis_apply(self, proposal_id, definition_text):
        """Bind to the source draft and return a reviewable draft. No write; the proposal is not consumed."""
        from . import contract_service
        proposal = self.contract_proposal(proposal_id)
        if source_digest(definition_text) != proposal['base_digest']:
            fail('proposal_stale', 409)
        try:
            definition = proposal_definition(proposal['response'])
        except contract_schema.DefinitionError:
            # Defensive only: contract_synthesis_v1 already refused this definition at acceptance, so only a
            # proposal that bypassed acceptance reaches here. It keeps the existing public expiry code.
            fail('proposal_expired', 410)
        text = jsonio.encode_exact(definition, indent=2)
        return {'proposal_id': proposal_id, 'definition_text': text, 'projection': contract_service.project(text),
                'notes': list(proposal['response'].get('notes', []))}

    def synthesis_origin(self, proposal_id, definition_text):
        """(origin, basis) for a Save naming a proposal: equal digest means synthesized, otherwise derived.
        The basis is built from server-captured provenance only."""
        proposal = self.contract_proposal(proposal_id)
        try:
            derived = contract_schema.contract_digest(proposal_definition(proposal['response']))
        except contract_schema.DefinitionError:
            fail('proposal_expired', 410)
        submitted = contract_schema.contract_digest(jsonio.decode_exact(definition_text))
        return ('synthesized' if submitted == derived else 'derived_from_synthesis'), dict(proposal['provenance'])

    def apply(self, body):
        from . import workshop_assist
        plan, captures = self.prepare('proposals/apply', body)
        self.check_plan(plan, body)
        proposal = self.proposal(body.get('proposal_id'))
        if digest(body.get('draft')) != proposal['base_digest']:
            fail('stale_proposal', 409)
        result = workshop_assist.apply({**proposal['request'], 'draft': body.get('draft')}, proposal['response'])
        if 'examples' in result:
            with self.assets.validate(body['draft']) as captured:
                if captured.parsed.block is None:
                    fail('unusable', 409)
                examples = []
                for item in result['examples']:
                    examples.append({k: item[k] for k in ('label', 'inputs', 'expectation')})
                    examples[-1].update(id=secrets.token_hex(16), version=1, origin='llm_generated', verified=False)
                self.validate_examples(examples, captured.parsed.block)
                result['examples'] = examples
        if 'draft' in result:
            # Application produces a draft. Invalid/incomplete bindings remain inspectable.
            result['preview'] = self.preview({'draft': result['draft']})
            if proposal['response']['action'] == 'describe' and 'description' in result['preview']:
                result['draft']['metadata']['description_basis'] = result['preview']['description']['basis']
        return result

    def review(self, run_id, body):
        if body.get('judgement') not in ('correct', 'incorrect', 'unknown'):
            fail()
        if not isinstance(body.get('note', ''), str) or len(body.get('note', '').encode()) > 4096:
            fail()
        with self.lock:
            row = next((copy.deepcopy(r) for b in self.batches.values() for r in b['rows'] if r.get('run_id') == run_id), None)
        saved = None
        try:
            saved = self.state.history.read_run(run_id)
        except history.NoSuchRun:
            pass
        if row is None and saved and saved.get('result'):
            result = saved['result']
            provenance = result.get('workshop', {})
            row = {'snapshot_id': provenance.get('snapshot_id'), 'example': {'id': provenance.get('example_id'),
                   'version': provenance.get('example_version')}, 'result_digest': digest(result.get('outputs', {})),
                   'outcome': result['outcome'], 'preview_incomplete': result.get('redacted', False)}
        if not row or row.get('preview_incomplete') or row.get('outcome') != 'complete':
            fail('evidence_unavailable', 409)
        expected = {'snapshot_id': row['snapshot_id'], 'example_id': row['example']['id'],
                    'example_version': row['example']['version'], 'result_digest': row['result_digest']}
        if body.get('context') != expected:
            fail('stale', 409)
        persistent = bool(body.get('persist'))
        if persistent and self.state.workspace.read_only:
            fail('read_only', 409)
        writer = self.state.register_writer('Workshop review') if persistent else None
        reserved = False
        run = None
        try:
            run = self.state.store.reserve_review(run_id)
            reserved = True
            if persistent:
                saved = self.state.history.read_run(run_id)
                folder = self.state.history.saved_folder(run_id)
                if not saved.get('result') or saved['result'].get('redacted'):
                    fail('evidence_unavailable', 409)
                old = saved['reviews']
            else:
                with self.lock:
                    old = list(self.session_reviews.get(run_id, []))
            revision = old[-1]['revision'] if old else 0
            if body.get('expected_revision', 0) != revision:
                fail('stale', 409)
            review = {'format_version': 1, 'revision': revision + 1, 'run_id': run_id, 'context': expected,
                      'judgement': body['judgement'], 'note': body.get('note', ''), 'timestamp': runs.utc_now()}
            if persistent:
                redacted, changed = history.redact(review, self.state.credentials())
                redacted['redacted'] = changed
                # The store reservation prevents both another review and history deletion.
                with (folder / ('review-' + str(review['revision']) + '.json')).open('xb') as f:
                    f.write(jsonio.encode_file_exact(redacted))
                    f.flush()
                    import os
                    os.fsync(f.fileno())
            with self.lock:
                self.session_reviews[run_id] = [*old, review][-100:]
                for b in self.batches.values():
                    for r in b['rows']:
                        if r.get('run_id') == run_id:
                            r['human_rating'] = review['judgement']
                            r['correctness'] = review['judgement']
            return {**review, 'session_only': not persistent}
        finally:
            if reserved:
                self.state.store.release_review(run_id, run)
            if writer:
                writer.release()

    def check_schema_available(self, block):
        if any(port.parsed.schema is not None for ports in block.contracts.values() for port in ports.values()) and not checks.schema_support()[0]:
            fail('validation_unavailable', 503)

    def reviewed(self, key, source_hash):
        root = self.assets.path / key_name(key)
        if not root.exists():
            return False
        h = head(self.assets.root(key))
        if h is None:
            return False
        for revision in h['retained']:
            metadata = document(root / '.workshop' / 'revisions' / revision, 'workshop.json')
            if metadata.get('source_review') == source_hash:
                return True
        return False

    def reject_credentials(self, value):
        redacted, changed = history.redact(value, self.state.credentials())
        if changed:
            fail('credential_in_asset', 409)


def revision_provenance(descriptor, arm):
    """The additive saved-revision provenance of one arm (block_revision_id, block_revision_number), or {}."""
    candidates = descriptor.get('candidates') or []
    if arm >= len(candidates):
        return {}
    return {k: candidates[arm][k] for k in ('block_revision_id', 'block_revision_number') if k in candidates[arm]}


def synthesis_provenance(plan):
    """Minimal basis: prompt identity, role and destination. No task text, notes or records."""
    destination = plan['candidates'][0].get('destination') or {}
    basis = {'prompt_id': SYNTHESIS_PROMPT_ID, 'prompt_version': SYNTHESIS_PROMPT_VERSION, 'role': SYNTHESIS_ROLE,
             'provider_kind': destination.get('kind') or 'unknown', 'model': destination.get('model') or 'unknown'}
    if isinstance(destination.get('provider'), str) and destination['provider'].strip():
        basis['provider'] = destination['provider'][:256]
    return basis
