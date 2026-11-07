"""Exact workflow bundles and immutable copies of Workshop block generations."""
from __future__ import annotations

import base64
import binascii
import hashlib
from contextlib import ExitStack
from pathlib import Path

from . import blocks, checks, jsonio, workshop_assets as assets
from .workshop import WorkshopError
from .workspace import valid_name, write_atomic_bytes

LIMIT = 32 << 20
MISSING = object()
SIDES = ('inputs', 'outputs')


def problem(code, location, message, status=409):
    raise WorkshopError(code, status, [{'code': code, 'location': location, 'message': message}])


def record(value, required, optional, location):
    if not isinstance(value, dict) or not set(required) <= value.keys() <= set(required) | set(optional):
        problem('invalid', location, 'unexpected or missing fields', 400)
    return value


def names(value, minimum, maximum, location):
    if (not isinstance(value, dict) or not minimum <= len(value) <= maximum
            or any(not isinstance(name, str) or not blocks.IDENT.fullmatch(name) for name in value)):
        problem('invalid', location, 'expected a bounded map of identifiers', 400)
    return value


def boolean(value, location):
    if type(value) is not bool:
        problem('invalid', location, 'expected an explicit boolean', 400)


def integer(value, minimum, maximum, location):
    if type(value) is not int or not minimum <= value <= maximum:
        problem('invalid', location, f'expected an integer from {minimum} to {maximum}', 400)


def canonical(value):
    if isinstance(value, dict):
        return {key: canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


def digest(value):
    return hashlib.sha256(jsonio.encode_exact(canonical(value)).encode('utf-8')).hexdigest()


def value_text(value):
    text = jsonio.encode_exact(value)
    if len(text.encode('utf-8')) > 4 << 20:
        problem('too_large', 'value', 'an exact value exceeds 4 MiB')
    return text


def source(binding, location):
    if not isinstance(binding, dict):
        problem('invalid', location, 'expected a binding object', 400)
    kind = binding.get('kind')
    if kind == 'input':
        fields = ('port',)
    elif kind == 'constant':
        fields = ('name',)
    elif kind == 'node':
        fields = ('node', 'port')
    else:
        problem('invalid', location, 'unknown binding kind', 400)
    record(binding, ('kind', *fields), ('path',), location)
    for field in fields:
        if not isinstance(binding[field], str) or not blocks.IDENT.fullmatch(binding[field]):
            problem('invalid', location, 'binding source must use identifiers', 400)
    path = binding.get('path', [])
    if not isinstance(path, list) or len(path) > 16:
        problem('path_invalid', location, 'expected at most 16 path segments', 400)
    for segment in path:
        if isinstance(segment, str) and len(segment) <= 256:
            continue
        integer(segment, 0, (1 << 31) - 1, location)
    return kind, *(binding[field] for field in fields)


def select(value, path, location):
    for part in path:
        if isinstance(part, str):
            value = value.get(part, MISSING) if isinstance(value, dict) else MISSING
        else:
            value = value[part] if isinstance(value, list) and part < len(value) else MISSING
        if value is MISSING:
            problem('path_missing', location, 'source does not have the selected field or index')
    return value


def capture(assets_service, selection):
    record(selection, ('key',), ('revision', 'fingerprint'), 'source')
    if 'revision' in selection and 'fingerprint' not in selection:
        if not isinstance(selection['revision'], str):
            problem('invalid', 'source.revision', 'expected an exact generation ID', 400)
        saved = assets_service.saved(selection['key'], selection['revision'])
        files, metadata = saved.files, saved.metadata
        provenance = {'key': saved.key, 'revision': saved.revision, 'revision_number': saved.number,
                      'title': metadata.get('display_name', saved.key),
                      'effects': metadata.get('effects', 'unknown')}
    elif 'fingerprint' in selection and 'revision' not in selection:
        key = selection['key']
        files, metadata, identity = unmanaged(assets_service, key)
        if selection['fingerprint'] != identity:
            problem('source_changed', 'source', 'the reviewed unmanaged source changed')
        provenance = {'key': key, 'fingerprint': identity, 'title': key, 'effects': 'unknown'}
    else:
        problem('invalid', 'source', 'select a generation or reviewed fingerprint', 400)
    return {'files': assets.wire_files(files), 'asset_digest': assets.fingerprint(files),
            'contracts': metadata['contracts'], 'contract_pins': metadata['contract_pins'],
            'provenance': provenance}



def unmanaged(assets_service, key):
    draft = assets_service.export(key)
    if draft['metadata'].get('assets'):
        problem('source_changed', 'source', 'select an exact managed revision')
    with assets_service.validate(draft) as current:
        if current.parsed.problem or current.parsed.block is None:
            problem('snapshot_unusable', 'source', 'the source block is invalid')
        block = current.parsed.block
        if block.format_version != blocks.WORKFLOW_FORMAT_VERSION:
            problem('block_upgrade_required', 'source', 'workflow nodes require a v4 block')
        contracts, pins = assets.captured_maps(block)
        identity = digest({'assets': current.digest, 'contracts': contracts, 'pins': pins})
        # Resolve again to make changes during capture visible before accepting the review.
        with assets_service.validate(assets_service.export(key)) as verified:
            if verified.parsed.block is None or verified.digest != current.digest or assets.captured_maps(verified.parsed.block) != (contracts, pins):
                problem('source_changed', 'source', 'the unmanaged block or its contracts changed')
        return current.files, {'contracts': contracts, 'contract_pins': pins}, identity

def open_snapshot(snapshot_id, snapshot, stack, location):
    try:
        record(snapshot, ('files', 'asset_digest', 'contracts', 'contract_pins', 'provenance'), (), location)
        if digest(snapshot) != snapshot_id:
            raise ValueError
        provenance = record(snapshot['provenance'], ('key', 'title', 'effects'),
                            ('revision', 'revision_number', 'fingerprint'), location)
        key = assets.key_name(provenance['key'])
        raw = dict(snapshot['files'])
        main = record(raw.pop('block.json'), ('encoding', 'text'), (), location)
        if main['encoding'] != 'base64' or not isinstance(main['text'], str):
            raise ValueError
        raw = assets.decode_files(raw)
        raw['block.json'] = base64.b64decode(main['text'], validate=True)
        if len(raw['block.json']) > blocks.BLOCK_FILE_LIMIT or assets.fingerprint(raw) != snapshot['asset_digest']:
            raise ValueError
        definition = jsonio.decode_file_exact(raw['block.json'])
        if definition.get('format_version') == 3:
            problem('block_upgrade_required', location, 'workflow nodes require a v4 block')
        metadata = {side: snapshot[side] for side in ('contracts', 'contract_pins')}
        metadata['contracts_digest'] = assets.contract_maps_digest(metadata['contracts'], metadata['contract_pins'])
        assets.validate_contract_maps(definition, metadata)
        opened = stack.enter_context(assets.Capture(raw, key, assets.SnapshotContracts(metadata)))
        if opened.parsed.problem or set(opened.parsed.paths) != set(raw):
            raise ValueError
        return opened
    except WorkshopError as exc:
        if exc.code == 'block_upgrade_required':
            raise
    except (ValueError, TypeError, KeyError, AttributeError, binascii.Error):
        pass
    problem('snapshot_unusable', location, 'the captured block could not be verified')


def bundle_graph(bundle, name, stack):
    record(bundle, ('format_version', 'definition', 'snapshots', 'layout', 'examples'), (), name)
    integer(bundle['format_version'], 1, 2, 'format_version')
    definition = record(bundle['definition'], ('name', 'purpose', 'inputs', 'outputs', 'constants', 'nodes'),
                        ('budget',), 'definition')
    if definition['name'] != name or not isinstance(definition['purpose'], str) or len(definition['purpose']) > 4096:
        problem('invalid', 'definition', 'expected the workflow name and bounded purpose', 400)
    budget = record(definition.get('budget', {}), (), ('max_seconds', 'max_model_attempts'), 'budget')
    integer(budget.get('max_seconds', 1800), 1, 86400, 'budget.max_seconds')
    integer(budget.get('max_model_attempts', 32), 1, 256, 'budget.max_model_attempts')
    contracts = {}
    for side in SIDES:
        contracts[side] = {}
        for port, declaration in names(definition[side], int(side == 'outputs'), 50, side).items():
            location = f'{side}.{port}'
            record(declaration, ('contract', 'local_only'), ('description', 'binding') if side == 'outputs'
                   else ('description',), location)
            boolean(declaration['local_only'], location)
            if not isinstance(declaration.get('description', ''), str):
                problem('invalid', location, 'expected a text description', 400)
            try:
                contracts[side][port] = checks.parse_contract(declaration['contract'])
            except (checks.ContractError, TypeError, AttributeError):
                problem('contract_mismatch', location, 'the inline contract is invalid')
    for key, constant in names(definition['constants'], 0, 64, 'constants').items():
        record(constant, ('value', 'local_only'), (), f'constants.{key}')
        boolean(constant['local_only'], f'constants.{key}')
        value_text(constant['value'])
    nodes = names(definition['nodes'], 0, 64, 'nodes')
    snapshots = bundle['snapshots']
    if not isinstance(snapshots, dict) or len(snapshots) > 64:
        problem('invalid', 'snapshots', 'expected a bounded snapshot map', 400)
    layout = names(bundle['layout'], 0, 64, 'layout')
    for node, position in layout.items():
        if node not in nodes:
            problem('invalid', f'layout.{node}', 'unknown node', 400)
        record(position, ('x', 'y'), (), f'layout.{node}')
        for coordinate in position.values():
            integer(coordinate, -100000, 100000, f'layout.{node}')
    examples = bundle['examples']
    if not isinstance(examples, list) or len(examples) > 20:
        problem('invalid', 'examples', 'expected at most 20 workflow examples', 400)
    seen = set()
    for index, example in enumerate(examples):
        location = f'examples[{index}]'
        record(example, ('id', 'version', 'inputs', 'expectation'), ('label', 'verified', 'held_out', 'restrictions', 'origin'), location)
        ident = example['id']
        if not isinstance(ident, str) or not ident or len(ident) > 80 or ident in seen:
            problem('invalid', location, 'expected a unique example ID', 400)
        seen.add(ident)
        integer(example['version'], 1, (1 << 31) - 1, location)
        for flag in ('verified', 'held_out'):
            boolean(example.get(flag, False), location)
        if any(not isinstance(text, str) for text in names(example['inputs'], 0, 50, location).values()):
            problem('invalid', location, 'example inputs must be exact text', 400)
        restrictions = names(example.get('restrictions', {}), 0, 50, location)
        if any(mark is not True for mark in restrictions.values()):
            problem('invalid', location, 'example restrictions must be true', 400)
        if not isinstance(example['expectation'], dict):
            problem('invalid', location, 'expected a workflow expectation', 400)
    has_gates = any(isinstance(row, dict) and 'gate' in row for row in nodes.values())
    if has_gates and bundle['format_version'] != 2:
        problem('invalid', 'format_version', 'release gates require bundle version 2', 400)
    opened, by_node = {}, {}
    for node, row in nodes.items():
        if isinstance(row, dict) and 'gate' in row:
            from . import release_gate
            record(row, ('gate', 'bindings'), (), f'nodes.{node}')
            release_gate.parse(row['gate'], f'nodes.{node}.gate')
            names(row['bindings'], 0, 2, f'nodes.{node}.bindings')
            if row['bindings'].keys() - {'value', 'scan'}:
                problem('invalid', f'nodes.{node}', 'unknown gate input', 400)
            continue
        record(row, ('snapshot_id', 'bindings'), (), f'nodes.{node}')
        snapshot_id = row['snapshot_id']
        if not isinstance(snapshot_id, str) or snapshot_id not in snapshots:
            problem('snapshot_unusable', f'nodes.{node}', 'the node has no captured block')
        if snapshot_id not in opened:
            opened[snapshot_id] = open_snapshot(snapshot_id, snapshots[snapshot_id], stack, f'nodes.{node}')
        by_node[node] = opened[snapshot_id]
    if opened.keys() != snapshots.keys():
        problem('invalid', 'snapshots', 'unreferenced snapshots are not allowed', 400)
    return by_node, contracts


class Store:
    def __init__(self, state, asset_service):
        self.state = state
        self.assets = asset_service
        self.root = Path(state.workspace.path)

    def read(self, name):
        if not valid_name(name):
            problem('invalid', 'name', 'invalid workflow name', 400)
        loaded = blocks.read_file(self.root, f'workflows/{name}.json', LIMIT)
        if loaded.fingerprint == ('missing',):
            return None, None
        if loaded.data is None:
            problem('snapshot_unusable', name, loaded.reason)
        try:
            return jsonio.decode_file_exact(loaded.data), hashlib.sha256(loaded.data).hexdigest()
        except ValueError:
            problem('snapshot_unusable', name, 'the workflow file is invalid')

    def publish(self, name, expected, bundle):
        if self.state.workspace.read_only:
            problem('read_only', name, 'the workspace is read-only')
        writer = self.state.register_writer(f'workflow edit {name}')
        try:
            with self.assets.reserve(name, namespace='workflow'):
                _, token = self.read(name)
                if token != expected:
                    problem('stale', name, 'the workflow changed since review')
                folder = self.root / 'workflows'
                if bundle is None:
                    if token is None:
                        problem('missing', name, 'workflow does not exist', 404)
                    (folder / f'{name}.json').unlink()
                else:
                    data = jsonio.encode_file_exact(bundle)
                    if len(data) > LIMIT:
                        problem('too_large', name, 'bundle exceeds 32 MiB; base64 leaves at most about 24 MiB for raw assets', 413)
                    folder.mkdir(mode=0o700, exist_ok=True)
                    write_atomic_bytes(folder, f'{name}.json', data)
                    token = hashlib.sha256(data).hexdigest()
                assets.sync_directory(folder)
                return token
        finally:
            writer.release()

