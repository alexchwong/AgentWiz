"""Workshop revision descriptions (block_metadata_refactor spec v2): one sidecar record per retained managed
generation, its read projection, the guarded write, and the server-owned Describe/Refine request.

No HTTP and no lock of its own. Writes take the per-block reservation and the workspace writer lifecycle
through `Service.save_description`. A description never changes an executable asset, the head, history,
examples or the contract registry.
"""
from __future__ import annotations

import copy
import hashlib
import re
import unicodedata
from pathlib import Path

from . import blocks, history, jsonio
from .workshop import TOKEN, WorkshopError
from .workshop_assets import DESCRIPTION_FOLDER, fail
from .workshop_editor import SECTIONS

FORMAT_VERSION = 1
SECTION_LIMIT = 600
PORT_LIMIT = 240
NOTE_LIMIT = 600
RECORD_LIMIT = 16 << 10
READ_LIMIT = 64 << 10             # larger than a record, so an oversize file is reported rather than truncated
ORIGINS = ('manual', 'model')
SIDES = ('inputs', 'outputs')
HEX64 = re.compile(r'[0-9a-f]{64}\Z')

ROLE = 'write_block_description'
PROMPT_ID = 'write_block_description'
PROMPT_VERSION = 1
FEEDBACK_LIMIT = 2000
CONTEXT_LIMIT = 16 << 10
EXCERPT_LIMIT = 4000
OUTPUT_TOKENS = 2048
MEANING_LIMIT = 240
PURPOSE_LIMIT = 2000
GENERATE_FIELDS = {'action', 'mode', 'key', 'revision'}
REFINE_FIELDS = GENERATE_FIELDS | {'proposal_id', 'feedback'}
MOVED = ('Describe now writes a saved revision\'s description. Open the selected revision\'s Standard '
         'description panel to describe or edit it.')


class DescriptionInvalid(Exception):
    """A located, sanitised refusal of a model result or a submission. Messages never quote values."""

    def __init__(self, issues):
        super().__init__('description_invalid')
        self.issues = list(issues)


# --- prose and records ---------------------------------------------------------------------------------
def _prose_issue(text, location, limit, required):
    if not isinstance(text, str):
        return {'location': location, 'message': 'must be text'}
    if any(0xD800 <= ord(c) <= 0xDFFF for c in text):
        return {'location': location, 'message': 'contains invalid Unicode'}
    if any(unicodedata.category(c) == 'Cc' and c not in '\n\t' for c in text):
        return {'location': location, 'message': 'contains a control character other than a newline or tab'}
    stripped = text.strip()
    if required and not stripped:
        return {'location': location, 'message': 'must not be blank'}
    if len(stripped) > limit:
        return {'location': location, 'message': f'must be at most {limit} characters'}
    return None


def normalise(sections, port_summaries, ports, prefix=''):
    """(sections, port_summaries) trimmed and checked against the declared `ports` ({side: [names]}).
    Raises DescriptionInvalid with every located issue."""
    issues = []
    out_sections = {}
    if not isinstance(sections, dict):
        issues.append({'location': prefix + 'sections', 'message': 'must be an object'})
    else:
        for key in sections:
            if key not in SECTIONS:
                issues.append({'location': f'{prefix}sections.{key}' if isinstance(key, str) else prefix + 'sections',
                               'message': 'is not a description section'})
        for key in SECTIONS:
            if key not in sections:
                issues.append({'location': f'{prefix}sections.{key}', 'message': 'is required'})
                continue
            issue = _prose_issue(sections[key], f'{prefix}sections.{key}', SECTION_LIMIT, key == 'use')
            if issue:
                issues.append(issue)
            else:
                out_sections[key] = sections[key].strip()
    out_ports = {side: {} for side in SIDES}
    if not isinstance(port_summaries, dict) or set(port_summaries) != set(SIDES):
        issues.append({'location': prefix + 'port_summaries', 'message': 'must hold exactly inputs and outputs'})
    else:
        for side in SIDES:
            values = port_summaries[side]
            if not isinstance(values, dict):
                issues.append({'location': f'{prefix}port_summaries.{side}', 'message': 'must be an object'})
                continue
            for name, text in values.items():
                location = f'{prefix}port_summaries.{side}.{name}'
                if name not in ports.get(side, ()):
                    issues.append({'location': location, 'message': 'is not a declared port'})
                    continue
                issue = _prose_issue(text, location, PORT_LIMIT, True)
                if issue:
                    issues.append(issue)
                else:
                    out_ports[side][name] = text.strip()
    if issues:
        raise DescriptionInvalid(issues)
    return out_sections, out_ports


def encode_record(basis, origin, sections, port_summaries):
    record = {'format_version': FORMAT_VERSION, 'basis': basis, 'origin': origin,
              'sections': {k: sections[k] for k in SECTIONS},
              'port_summaries': {side: dict(port_summaries[side]) for side in SIDES}}
    data = jsonio.encode_file_exact(record)
    if len(data) > RECORD_LIMIT:
        raise DescriptionInvalid([{'location': 'record', 'message': f'must be at most {RECORD_LIMIT} bytes'}])
    return data


def token(data):
    return None if data is None else hashlib.sha256(data).hexdigest()


def ports_of(block):
    return {side: [d.name for d in getattr(block, side)] for side in SIDES}


# --- the sidecar on disk -------------------------------------------------------------------------------
def read_sidecar(root, ident):
    """('absent', None) | ('present', bytes) | ('unusable', None). Never follows a symbolic link."""
    workshop = Path(root) / '.workshop'
    folder = workshop / DESCRIPTION_FOLDER
    if workshop.is_symlink() or folder.is_symlink():
        return 'unusable', None
    result = blocks.read_file(Path(root), f'.workshop/{DESCRIPTION_FOLDER}/{ident}.json', READ_LIMIT)
    if result.data is not None:
        return 'present', result.data
    if result.fingerprint == ('missing',):
        return 'absent', None
    return 'unusable', None


def parse_record(data, basis, ports):
    """(record, None) or (None, reason). Reasons: unreadable, unknown_format, basis_mismatch, invalid."""
    try:
        obj = jsonio.decode_exact(data.decode('utf-8'))
    except (ValueError, UnicodeError):
        return None, 'unreadable'
    if not isinstance(obj, dict):
        return None, 'unreadable'
    if obj.get('format_version') != FORMAT_VERSION or type(obj.get('format_version')) is not int:
        return None, 'unknown_format'
    if set(obj) != {'format_version', 'basis', 'origin', 'sections', 'port_summaries'} \
            or obj['origin'] not in ORIGINS or len(data) > RECORD_LIMIT:
        return None, 'invalid'
    if obj['basis'] != basis:
        return None, 'basis_mismatch'
    try:
        sections, summaries = normalise(obj['sections'], obj['port_summaries'], ports)
    except DescriptionInvalid:
        return None, 'invalid'
    return {'origin': obj['origin'], 'sections': sections, 'port_summaries': summaries}, None


def _legacy(metadata):
    stored = metadata.get('description_sections')
    if not isinstance(stored, dict):
        return None
    sections = {k: stored[k] if isinstance(stored.get(k), str) else '' for k in SECTIONS}
    return sections if any(v.strip() for v in sections.values()) else None


def projection(root, saved, parsed):
    """The `standard_description` projection of one saved generation (spec v2 section 3)."""
    block = parsed.block
    fallback = {'purpose': parsed.description or '',
                'ports': {side: {d.name: d.description for d in getattr(block, side)} for side in SIDES}
                if block is not None else {side: {} for side in SIDES}}
    out = {'format_version': 1, 'key': saved.key, 'revision': saved.revision, 'revision_number': saved.number,
           'status': 'missing', 'basis': None, 'token': None, 'origin': None, 'sections': None,
           'port_summaries': {side: {} for side in SIDES}, 'fallback': fallback, 'issues': [], 'writable': False}
    basis = saved.basis if block is not None else None
    if basis is None:
        out.update(status='unavailable')
        out['issues'].append({'code': 'no_basis', 'message': 'this revision has no usable captured executable basis'})
        return out
    out['basis'] = basis
    state, data = read_sidecar(root, saved.revision)
    if state == 'unusable':
        out.update(status='unavailable')
        out['issues'].append({'code': 'sidecar_unusable', 'message': 'the description record cannot be read safely'})
        return out
    if state == 'present':
        out['token'] = token(data)
        record, reason = parse_record(data, basis, ports_of(block))
        if record is None:
            out.update(status='unavailable', writable=reason in ('basis_mismatch', 'invalid'))
            out['issues'].append({'code': 'sidecar_' + reason, 'message': 'the description record is ' + {
                'unreadable': 'unreadable', 'unknown_format': 'in an unknown format',
                'basis_mismatch': 'for different executable content', 'invalid': 'invalid'}[reason]})
            return out
        out.update(status='saved', writable=True, **record)
        return out
    out['writable'] = True
    legacy = _legacy(saved.metadata)
    if legacy is not None:
        out.update(status='legacy', sections=legacy)
        for key, text in legacy.items():
            if _prose_issue(text, key, SECTION_LIMIT, key == 'use'):
                out['issues'].append({'code': 'legacy_exceeds_bounds', 'location': 'sections.' + key,
                                      'message': 'must be shortened or completed before it can be saved'})
    return out


def write_sidecar(root, ident, data):
    from .workspace import write_atomic_bytes
    workshop = Path(root) / '.workshop'
    folder = workshop / DESCRIPTION_FOLDER
    if workshop.is_symlink() or folder.is_symlink() or not workshop.is_dir():
        fail('unusable', 409)
    folder.mkdir(mode=0o700, exist_ok=True)
    if folder.is_symlink() or not folder.is_dir():
        fail('unusable', 409)
    write_atomic_bytes(folder, ident + '.json', data)


def check_write_body(body):
    """('generated' | 'manual', body). Both shapes are closed; `format_version` is removed by the route."""
    if not isinstance(body, dict):
        fail()
    if set(body) == {'proposal_id', 'expected_token'}:
        kind = 'generated'
    elif set(body) == {'expected_token', 'basis', 'sections', 'port_summaries'}:
        kind = 'manual'
        if not isinstance(body['basis'], str) or HEX64.fullmatch(body['basis']) is None:
            fail()
    else:
        fail()
    expected = body['expected_token']
    if expected is not None and (not isinstance(expected, str) or HEX64.fullmatch(expected) is None):
        fail()
    return kind


def invalid(exc):
    return WorkshopError('description_invalid', 400, exc.issues)


# --- Describe and Refine -------------------------------------------------------------------------------
def prompt():
    return (Path(__file__).parent / 'prompts' / 'write_block_description.md').read_text(encoding='utf-8')


def check_request(body):
    """Validate the closed Generate/Refine shapes. Returns (mode, feedback or None)."""
    if not isinstance(body, dict) or body.get('action') != 'describe':
        fail()
    mode = body.get('mode')
    if mode not in ('generate', 'refine'):
        raise WorkshopError('describe_moved', 409, [{'location': 'action', 'message': MOVED}])
    allowed = GENERATE_FIELDS if mode == 'generate' else REFINE_FIELDS
    if set(body) != allowed:
        if set(body) - allowed & {'draft', 'role', 'task', 'evidence', 'sections'}:
            raise WorkshopError('describe_moved', 409, [{'location': 'action', 'message': MOVED}])
        fail()
    if not isinstance(body['key'], str) or not isinstance(body['revision'], str) \
            or TOKEN.fullmatch(body['revision']) is None:
        fail()
    feedback = None
    if mode == 'refine':
        if not isinstance(body['proposal_id'], str) or TOKEN.fullmatch(body['proposal_id']) is None:
            fail('proposal_expired', 410)
        feedback = body['feedback']
        issue = _prose_issue(feedback, 'feedback', FEEDBACK_LIMIT, True)
        if issue:
            raise WorkshopError('invalid', 400, [issue])
        feedback = feedback.strip()
    return mode, feedback


def _excerpt(text):
    text = text if isinstance(text, str) else ''
    return {'text': text[:EXCERPT_LIMIT], 'excerpted': len(text) > EXCERPT_LIMIT, 'characters': len(text)}


_PLACEHOLDER = re.compile(r'\{\{\s*inputs\.([^}\s]+)\s*\}\}')


def binding_structure(value):
    """Argument names and input paths of an MCP binding; literal values replaced by a marker."""
    if isinstance(value, dict):
        return {k: binding_structure(v) for k, v in value.items()}
    if isinstance(value, list):
        return [binding_structure(v) for v in value]
    if isinstance(value, str):
        found = _PLACEHOLDER.findall(value)
        if found and _PLACEHOLDER.fullmatch(value.strip()):
            return {'input': found[0]}
        if found:
            return {'template_inputs': found}
    return '<literal>'


def _contract_facts(text, detailed):
    try:
        obj = jsonio.decode_exact(text)
    except ValueError:
        return {'format': 'unknown'}
    if not isinstance(obj, dict):
        return {'format': 'unknown'}
    facts = {'format': obj.get('format', 'unknown')}
    if obj.get('format') == 'text':
        rules = {k: obj[k] for k in ('min_length', 'max_length') if k in obj}
        if detailed and rules:
            facts['rules'] = {k: jsonio.encode_exact(v) for k, v in rules.items()}
        return facts
    schema = obj.get('schema')
    if isinstance(schema, dict):
        if 'type' in schema:
            facts['type'] = schema['type']
        if detailed and isinstance(schema.get('properties'), dict):
            required = schema.get('required') if isinstance(schema.get('required'), list) else []
            facts['properties'] = [{'name': name, 'type': node.get('type') if isinstance(node, dict) else None,
                                    'required': name in required}
                                   for name, node in list(schema['properties'].items())[:40]]
    return facts


def build_context(saved, parsed, *, proposal=None, feedback=None, credentials=()):
    """The allowlisted, redacted, bounded context. Raises WorkshopError before any dispatch."""
    block = parsed.block
    step = block.step

    def assemble(detailed, meanings, excerpt_limit):
        def ex(text):
            value = _excerpt(text)
            if len(value['text']) > excerpt_limit:
                value['text'] = value['text'][:excerpt_limit]
                value['excerpted'] = True
            return value
        ports = {}
        for side in SIDES:
            ports[side] = []
            for d in getattr(block, side):
                row = {'name': d.name, 'contract': _contract_facts(block.contracts[side][d.name].text, detailed)}
                if meanings and d.description:
                    row['declared_meaning'] = d.description[:MEANING_LIMIT]
                ports[side].append(row)
        ctx = {'key': saved.key, 'kind': parsed.kind, 'purpose': (parsed.description or '')[:PURPOSE_LIMIT],
               'inputs': ports['inputs'], 'outputs': ports['outputs'],
               'interface_note': 'Every declared input port requires a value. Optional nested JSON properties '
                                 'remain properties of their contract.'}
        if parsed.kind == 'model':
            ctx['model'] = {'role': step.role, 'system': ex(step.system or ''), 'prompt': ex(step.prompt or '')}
        elif parsed.kind == 'script':
            raw = saved.files.get(step.script_file, b'')
            try:
                source = raw.decode('utf-8')
            except UnicodeError:
                source = ''
            ctx['script'] = {'starter': saved.metadata.get('starter'), 'main_source': ex(source),
                             'support_files': 'omitted'}
        elif parsed.kind == 'tool':
            ctx['tool'] = {'server': step.server, 'tool': step.tool, 'bindings': binding_structure(step.arguments),
                           'tool_description': 'unavailable'}
        if proposal is not None:
            ctx['current_proposal'] = {'sections': proposal['sections'],
                                       'port_summaries': proposal['port_summaries']}
            ctx['feedback'] = feedback
        if not (detailed and meanings and excerpt_limit >= EXCERPT_LIMIT):
            ctx['omissions'] = 'Some optional constraints, meanings or source text were omitted to fit the limit.'
        return ctx

    attempts = [(True, True, EXCERPT_LIMIT), (False, True, EXCERPT_LIMIT), (False, False, EXCERPT_LIMIT),
                (False, False, 2000), (False, False, 1000), (False, False, 500), (False, False, 0)]
    for detailed, meanings, limit in attempts:
        ctx, _changed = history.redact(assemble(detailed, meanings, limit), list(credentials))
        text = jsonio.encode_exact(ctx)
        _still, remaining = history.redact(text, list(credentials))
        if remaining:
            raise WorkshopError('credential_in_context', 409, [
                {'location': 'context', 'message': 'a known credential remains in the outbound context; remove it first'}])
        if len(text.encode('utf-8')) <= CONTEXT_LIMIT:
            return text
    raise WorkshopError('context_too_large', 400, [
        {'location': 'context', 'message': f'the required interface, proposal and feedback exceed {CONTEXT_LIMIT} bytes'}])


def response_schema(mode, ports):
    def text(limit, nonblank, nullable=False):
        node = {'type': ['string', 'null'] if nullable else 'string', 'maxLength': limit}
        if nonblank:
            node['pattern'] = r'\S'
        return node
    refine = mode == 'refine'
    sections = {'type': 'object', 'properties': {k: text(SECTION_LIMIT, k == 'use') for k in SECTIONS},
                'required': [] if refine else list(SECTIONS), 'additionalProperties': False}

    def side(names):
        return {'type': 'object', 'properties': {n: text(PORT_LIMIT, True, refine) for n in names},
                'additionalProperties': False}
    summaries = {'type': 'object', 'properties': {s: side(ports[s]) for s in SIDES},
                 'required': [] if refine else list(SIDES), 'additionalProperties': False}
    payload = {'type': 'object', 'properties': {'sections': sections, 'port_summaries': summaries},
               'required': [] if refine else ['sections', 'port_summaries'], 'additionalProperties': False}
    return {'type': 'object', 'properties': {'format_version': {'const': 1}, 'action': {'const': 'describe'},
                                             'payload': payload, 'note': text(NOTE_LIMIT, True)},
            'required': ['format_version', 'action', 'payload'], 'additionalProperties': False}


def ephemeral_definition(mode, ports):
    return {'format_version': blocks.FORMAT_VERSION, 'name': 'workshop_description',
            'description': 'Workshop block description',
            'inputs': {'context': {'contract': {'format': 'text'}}},
            'outputs': {'proposal': {'description': 'The proposed standard description',
                                     'contract': {'format': 'json', 'schema': response_schema(mode, ports)}}},
            'steps': [{'id': 'describe', 'kind': 'model', 'role': ROLE, 'system': {'text': ''},
                       'prompt': {'text': prompt() + '\nMode: ' + mode + '\n\nContext data:\n{{inputs.context}}'},
                       'response_mode': 'named_json', 'task': {'max_tokens': OUTPUT_TOKENS},
                       'repair': {'max_repairs': 1}}],
            'budget': {'max_seconds': 1800}}


def materialise(mode, response, ports, prior=None, credentials=()):
    """(candidate {sections, port_summaries}, note, changed). Raises DescriptionInvalid; never quotes values."""
    if not isinstance(response, dict) or response.get('format_version') != 1 or response.get('action') != 'describe' \
            or set(response) - {'format_version', 'action', 'payload', 'note'} or not isinstance(response.get('payload'), dict):
        raise DescriptionInvalid([{'location': 'proposal', 'message': 'is not a description envelope'}])
    note = response.get('note', '')
    issue = _prose_issue(note, 'proposal.note', NOTE_LIMIT, False)
    if issue:
        raise DescriptionInvalid([issue])
    note = note.strip()
    payload = response['payload']
    if set(payload) - {'sections', 'port_summaries'}:
        raise DescriptionInvalid([{'location': 'proposal.payload', 'message': 'holds an unknown member'}])
    if mode == 'generate':
        sections, summaries = normalise(payload.get('sections'), payload.get('port_summaries'), ports,
                                        'proposal.payload.')
        changed = True
    else:
        merged = copy.deepcopy(prior)
        patch_sections = payload.get('sections', {})
        patch_ports = payload.get('port_summaries', {})
        if not isinstance(patch_sections, dict) or not isinstance(patch_ports, dict) or set(patch_ports) - set(SIDES):
            raise DescriptionInvalid([{'location': 'proposal.payload', 'message': 'is not a description patch'}])
        empty = not patch_sections and not any(patch_ports.get(s) for s in SIDES)
        if empty and not note:
            raise DescriptionInvalid([{'location': 'proposal.note',
                                       'message': 'an empty patch must explain why the feedback was not applied'}])
        for key, value in patch_sections.items():
            if key not in SECTIONS:
                raise DescriptionInvalid([{'location': 'proposal.payload.sections.' + str(key),
                                           'message': 'is not a description section'}])
            merged['sections'][key] = value
        for side, values in patch_ports.items():
            if not isinstance(values, dict):
                raise DescriptionInvalid([{'location': 'proposal.payload.port_summaries.' + side,
                                           'message': 'must be an object'}])
            for name, value in values.items():
                if value is None:
                    merged['port_summaries'][side].pop(name, None)
                else:
                    merged['port_summaries'][side][name] = value
        sections, summaries = normalise(merged['sections'], merged['port_summaries'], ports, 'proposal.')
        changed = {'sections': sections, 'port_summaries': summaries} != prior
    encode_record('0' * 64, 'model', sections, summaries)          # the whole-record byte limit
    _value, found = history.redact({'sections': sections, 'port_summaries': summaries, 'note': note},
                                   list(credentials))
    if found:
        raise DescriptionInvalid([{'location': 'proposal', 'message': 'contains a known credential'}])
    return {'sections': sections, 'port_summaries': summaries}, note, changed


def changed_labels(before, after):
    """Section keys and side.port labels whose text differs, for review rendering."""
    before = before or {'sections': {}, 'port_summaries': {s: {} for s in SIDES}}
    labels = [k for k in SECTIONS if (before.get('sections') or {}).get(k, '') != after['sections'].get(k, '')]
    for side in SIDES:
        old = (before.get('port_summaries') or {}).get(side, {})
        new = after['port_summaries'][side]
        labels.extend(f'{side}.{n}' for n in sorted(set(old) | set(new)) if old.get(n) != new.get(n))
    return labels
