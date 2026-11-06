"""Workshop publication and private captures, using the existing executable loader."""
from __future__ import annotations

import base64
import contextlib
import copy
import datetime
import hashlib
import os
import re
import secrets
import shutil
import stat
import tempfile
from pathlib import Path, PurePosixPath

from . import checks, contract_schema, jsonio
from .workspace import valid_name, write_atomic_bytes
from .workshop import TOKEN, WorkshopError

REVISION_LIMIT = 20
CAPTURE_LIMIT = 32 << 20


def fail(code='invalid', status=400):
    raise WorkshopError(code, status)


def fingerprint(files):
    h = hashlib.sha256()
    for path, data in sorted(files.items()):
        name = path.encode('utf-8')
        h.update(len(name).to_bytes(8, 'big') + name + len(data).to_bytes(8, 'big') + data)
    return h.hexdigest()


def relative(path):
    if not isinstance(path, str) or not path or '\\' in path:
        fail()
    parts = path.split('/')
    if any(p in ('', '.', '..') or p.startswith('.') for p in parts):
        fail()
    if PurePosixPath(path).is_absolute():
        fail()
    return path


def key_name(key):
    if not isinstance(key, str) or not valid_name(key):
        fail()
    return key


def read(root, path, limit=4 << 20):
    from .blocks import read_file
    result = read_file(root, path, limit)
    if result.data is None:
        fail('unusable', 409)
    return result.data


def document(root, name, limit=1 << 20):
    try:
        value = jsonio.decode_exact(read(root, name, limit).decode('utf-8'))
        if not isinstance(value, dict):
            fail('unusable', 409)
        return value
    except (ValueError, UnicodeError):
        fail('unusable', 409)


EXECUTABLE_VERSION = 3


def head(root):
    """The managed head of a block root, or None for an unmanaged root holding an executable definition.

    An executable v3 document is an unmanaged root. An old executable v1 root is also unmanaged here; the
    loader reports it as migration_required and never rewrites it on read."""
    return head_document(document(root, 'block.json'))


def head_document(obj):
    """`head` applied to an already decoded root `block.json`: None for an unmanaged executable root, the
    validated managed head otherwise. Raises WorkshopError. Shared with the reference scan so the managed-head
    invariants have one interpretation."""
    if not isinstance(obj, dict):
        fail('unusable', 409)
    version = obj.get('format_version')
    if type(version) is not int:
        fail('unusable', 409)
    if version in (1, EXECUTABLE_VERSION):
        return None
    if version != 2:
        fail('newer_format', 409)
    if set(obj) != {'format_version', 'current', 'retained'}:
        fail('unusable', 409)
    ids = obj['retained']
    if not isinstance(ids, list) or not 1 <= len(ids) <= REVISION_LIMIT or len(set(ids)) != len(ids):
        fail('unusable', 409)
    if any(not isinstance(i, str) or TOKEN.fullmatch(i) is None for i in ids) or obj['current'] != ids[0]:
        fail('unusable', 409)
    return obj


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_files(root, files):
    for name, data in files.items():
        target = root / relative(name)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(target.parent, 0o700)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    for parent, dirs, names in os.walk(root, topdown=False):
        fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def wire_files(files):
    return {p: {'encoding': 'base64', 'text': base64.b64encode(b).decode('ascii')} for p, b in files.items()}


def decode_files(raw):
    if not isinstance(raw, dict):
        fail()
    files = {}
    for p, v in raw.items():
        relative(p)
        if p in ('block.json', 'workshop.json'):
            fail()
        try:
            if isinstance(v, str):
                data = v.encode('utf-8')
            elif isinstance(v, dict) and set(v) == {'encoding', 'text'} and v['encoding'] == 'base64':
                data = base64.b64decode(v['text'], validate=True)
            else:
                fail()
        except (ValueError, TypeError):
            fail()
        if len(data) > 4 << 20:
            fail('too_large', 400)
        files[p] = data
    if sum(map(len, files.values())) > CAPTURE_LIMIT:
        fail('too_large', 400)
    return files


class SnapshotContracts:
    """The private resolver of a historical preview: a generation's own captured definitions, validated
    against its pins and parsed under user policy. Never used for current execution or new publication."""

    def __init__(self, metadata):
        self.metadata = metadata

    def resolve(self, name, revision, digest):
        from .contracts import Revision
        for side in ('inputs', 'outputs'):
            for port, pin in self.metadata.get('contract_pins', {}).get(side, {}).items():
                if pin == {'name': name, 'revision': revision, 'digest': digest}:
                    text = self.metadata['contracts'][side][port]
                    try:
                        obj = jsonio.decode_exact(text)
                        parsed = checks.parse_contract(obj)
                    except (ValueError, checks.ContractError):
                        return None, ('snapshot_incomplete', 'the captured definition cannot be read')
                    if contract_schema.contract_digest(obj) != digest:
                        return None, ('digest_mismatch', 'the captured definition does not match its pin')
                    return Revision(revision, '', '', 'manual', digest, parsed), None
        return None, ('no_such_contract', 'the generation captured no definition for this pin')

    def pin_state(self, name, revision, digest):
        found, refused = self.resolve(name, revision, digest)
        return ('ok', digest) if found is not None else (refused[0],)


class Capture:
    def __init__(self, files, key, contracts, metadata=None, *, internal_contracts=False, validators=None):
        from . import blocks
        self.directory = tempfile.TemporaryDirectory(prefix='agentwiz-capture-')
        self.root = Path(self.directory.name)
        try:
            write_files(self.root, files)
            self.parsed = blocks.parse_block(self.root, key, contracts, internal_contracts=internal_contracts,
                                             validators=validators)
            self.files = files
            self.metadata = metadata or {}
            self.digest = fingerprint(files)
        except BaseException:
            self.close()
            raise

    def close(self):
        self.directory.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class SavedGeneration:
    """One retained managed generation read for a clean saved selection. Immutable by convention."""

    def __init__(self, key, revision, number, name, metadata, files, definition):
        self.key = key
        self.revision = revision
        self.number = number
        self.name = name
        self.metadata = metadata
        self.files = files                  # every executable asset, including block.json
        self.definition = definition

    @property
    def selector(self):
        return {'key': self.key, 'revision': self.revision}

    @property
    def provenance(self):
        return {'block_revision_id': self.revision, 'block_revision_number': self.number}

    @property
    def basis(self):
        return description_basis(self.key, self.revision, self.metadata, self.definition)

    def draft(self, base_digest=None):
        """The wire draft of this generation. A run draft carries no head token (spec v2 2.1(4)); Restore and
        the projection pass the current identity so an edit stays optimistic against the current head."""
        files = {p: b for p, b in self.files.items() if p != 'block.json'}
        metadata = copy.deepcopy(self.metadata)
        metadata['support'] = list(self.metadata.get('support', []))
        draft = {'key': self.key, 'definition_text': self.files['block.json'].decode('utf-8'),
                 'files': wire_files(files), 'metadata': metadata}
        if base_digest is not None:
            draft['base_digest'] = base_digest
        return draft


def exact_digest(value):
    """The Workshop's exact JSON digest (the same helper as workshop_service.digest)."""
    return hashlib.sha256(jsonio.encode_exact(value).encode('utf-8')).hexdigest()


def description_basis(key, revision, metadata, definition):
    """The revision-description basis: None unless the generation is executable v3 with verified asset and
    captured-contract digests. Excludes role/profile settings, sidecar prose and the default pointer."""
    if not isinstance(definition, dict) or definition.get('format_version') != EXECUTABLE_VERSION:
        return None
    assets, contracts = metadata.get('asset_digest'), metadata.get('contracts_digest')
    if not isinstance(assets, str) or not isinstance(contracts, str):
        return None
    return exact_digest({'key': key, 'revision': revision, 'asset_digest': assets, 'contracts_digest': contracts})


DESCRIPTION_FOLDER = 'descriptions'


def prune_descriptions(root, retained):
    """Best effort: remove regular sidecar files with recognised IDs whose generations are no longer retained.
    Symbolic links and foreign file names are skipped. Never raises."""
    try:
        workshop = root / '.workshop'
        folder = workshop / DESCRIPTION_FOLDER
        if workshop.is_symlink() or folder.is_symlink() or not folder.is_dir():
            return
        for path in folder.iterdir():
            name = path.name
            if not name.endswith('.json') or TOKEN.fullmatch(name[:-5]) is None or name[:-5] in retained:
                continue
            try:
                if stat.S_ISREG(os.lstat(path).st_mode):
                    path.unlink()
            except OSError:
                pass
    except OSError:
        pass


class Assets:
    def __init__(self, state, lock):
        self.state = state
        self.path = state.workspace.path / 'blocks'
        self.lock = lock
        self.reservations = set()
        self.pins = {}

    def root(self, key):
        key_name(key)
        root = self.path / key
        if self.path.is_symlink() or root.is_symlink():
            fail('unusable', 409)
        if not root.is_dir():
            fail('missing', 404)
        return root

    @contextlib.contextmanager
    def reserve(self, key, namespace='block'):
        key_name(key)
        identity = (namespace, key.casefold())
        with self.lock:
            if identity in self.reservations:
                fail('busy', 503)
            self.reservations.add(identity)
        try:
            yield
        finally:
            with self.lock:
                self.reservations.discard(identity)

    def pin(self, key, revision=None):
        """(physical generation folder, release) for the current generation, or for one explicitly retained
        generation. The pin is registered under the lock before the head is re-read, so publication's reaper
        either sees the pin or this reader sees the generation leave retention. Release in `finally`.

        An explicit ID that is malformed or no longer retained is missing (404). A retained ID without its
        folder is a damaged generation (unusable, 409). An unmanaged root has no generation: it returns the
        root itself for the current request and refuses an explicit ID."""
        root = self.root(key)
        if revision is not None and (not isinstance(revision, str) or TOKEN.fullmatch(revision) is None):
            fail('missing', 404)
        for _ in range(3):
            h = head(root)
            if h is None:
                if revision is not None:
                    fail('missing', 404)
                return root, lambda: None
            ident = h['current'] if revision is None else revision
            if revision is not None and ident not in h['retained']:
                fail('missing', 404)
            identity = (key, ident)
            with self.lock:
                self.pins[identity] = self.pins.get(identity, 0) + 1
            released = [False]
            def release(identity=identity, released=released):
                with self.lock:
                    if released[0]:
                        return
                    released[0] = True
                    count = self.pins.get(identity, 0)
                    if count <= 1:
                        self.pins.pop(identity, None)
                    else:
                        self.pins[identity] = count - 1
            try:
                after = head(root)
                if revision is None:
                    if after != h:
                        release()
                        continue
                elif after is None or ident not in after['retained']:
                    fail('missing', 404)
                physical = root / '.workshop' / 'revisions' / ident
                paths = (root / '.workshop', physical.parent, physical)
                if any(p.is_symlink() for p in paths):
                    fail('unusable', 409)
                if any(not p.is_dir() for p in paths):
                    latest = head(root)
                    if revision is None:
                        if latest != h:
                            release()
                            continue
                    elif latest is None or ident not in latest['retained']:
                        fail('missing', 404)
                    fail('unusable', 409)
                return physical, release
            except BaseException:
                release()
                raise
        fail('stale', 409)

    def retained(self, key, revision):
        """Raise missing (404) unless `revision` is still retained by the managed head of `key`."""
        h = head(self.root(key))
        if h is None or revision not in h['retained']:
            fail('missing', 404)
        return h

    def contracts(self):
        # Loader resolves all references against one coherent snapshot of the parsed entries.
        with self.state._lock:
            self.state.contracts.rescan()
            return self.state.contracts.snapshot()

    @contextlib.contextmanager
    def registry(self):
        """The logical contract-registry write reservation (spec 11): a busy flag claimed under C6, which is
        released before any I/O. A competing registry writer is refused at once with busy (409)."""
        identity = ('contract_registry', '')
        with self.lock:
            if identity in self.reservations:
                fail('busy', 409)
            self.reservations.add(identity)
        try:
            yield
        finally:
            with self.lock:
                self.reservations.discard(identity)

    def validate(self, draft):
        return self._validate(draft)

    def _validate(self, draft, *, internal_contracts=False, validators=None):
        if not isinstance(draft, dict) or set(draft) - {'key', 'definition_text', 'files', 'metadata', 'base_digest', 'root_sources'}:
            fail()
        key = key_name(draft.get('key'))
        text = draft.get('definition_text')
        if not isinstance(text, str):
            fail()
        try:
            obj = jsonio.decode_exact(text)
        except ValueError:
            fail()
        if isinstance(obj, dict) and type(obj.get('format_version')) is int and obj['format_version'] < EXECUTABLE_VERSION:
            fail('migration_required', 400)
        if not isinstance(obj, dict) or obj.get('format_version') != EXECUTABLE_VERSION or obj.get('name') != key:
            fail()
        files = decode_files(draft.get('files', {}))
        sources = draft.get('root_sources', {})
        if not isinstance(sources, dict) or any(relative(p) not in files or
                not isinstance(d, str) or hashlib.sha256(files[p]).hexdigest() != d for p, d in sources.items()):
            fail()
        files['block.json'] = text.encode('utf-8')
        if sum(map(len, files.values())) > CAPTURE_LIMIT:
            fail('too_large', 400)
        metadata = draft.get('metadata', {})
        if not isinstance(metadata, dict):
            fail()
        name = metadata.get('revision_name', '')
        if not isinstance(name, str) or len(name) > 120 or any(ord(c) < 32 for c in name):
            raise WorkshopError('block_invalid', 409, [
                {'location': 'metadata.revision_name', 'message': 'must be at most 120 characters without control characters'}])
        if type(metadata.get('format_version', 1)) is not int or metadata.get('format_version', 1) != 1:
            fail('newer_format', 409)
        if metadata.get('origin', 'manual') not in ('starter', 'manual', 'llm_generated', 'derived_from_llm'):
            fail()
        if metadata.get('effects', 'unknown') not in ('read-only', 'changes state', 'unknown'):
            fail()
        from .workshop_editor import SCRIPT_IDS
        if metadata.get('starter') in SCRIPT_IDS:
            version = metadata.get('starter_version')
            runtime_digest = metadata.get('runtime_digest')
            if type(version) is not int or version < 1 or not isinstance(metadata.get('parameters'), dict):
                fail()
            if not isinstance(runtime_digest, str) or re.fullmatch('[0-9a-f]{64}', runtime_digest) is None:
                fail()
        support = metadata.get('support', [])
        if not isinstance(support, list) or any(not isinstance(p, str) for p in support) or len(set(support)) != len(support):
            fail()
        if any(relative(p) not in files or p == 'block.json' for p in support):
            fail()
        captured = Capture(files, key, self.contracts(), metadata, internal_contracts=internal_contracts,
                           validators=validators)
        self.check_references(captured)
        if captured.parsed.block is not None and set(files) != set(captured.parsed.paths) | set(support):
            captured.close()
            fail('undeclared_asset', 400)
        return captured

    def snapshot(self, saved):
        """A read-only capture of a saved generation parsed with its own captured contract maps (the server-
        owned snapshot resolver). Used for previews and description context only, never for execution or
        publication, and never granted to a caller-supplied draft."""
        captured = Capture(dict(saved.files), saved.key, SnapshotContracts(saved.metadata), saved.metadata)
        try:
            self.check_references(captured)
        except BaseException:
            captured.close()
            raise
        return captured

    def check_references(self, captured):
        from . import blocks, templates
        from .workshop_editor import field_rows
        block = captured.parsed.block
        if block is None:
            return
        step = block.step
        found = []
        if step.kind == 'model':
            for name, text in (('prompt', step.prompt or ''), ('system', step.system or '')):
                found.extend((name, p) for p in templates.scan_text(text, name))
        elif step.kind == 'tool':
            found = templates.scan_arguments(step.arguments)
        elif captured.metadata.get('starter') == 'format_report':
            found = [('report', p) for p in templates.scan_text(captured.metadata['parameters']['template'], 'report')]
        for location, placeholder in found:
            port = block.contracts['inputs'].get(placeholder.path[0])
            if port is None:
                captured.parsed.problem = blocks.BlockProblem(str(captured.root / 'block.json'), 'invalid',
                    ({'location': location, 'message': 'the placeholder names an undeclared input'},), 1)
                continue
            contract = port.parsed
            node = contract.schema
            if node is None:
                if contract.format == 'text' and len(placeholder.path) > 1:
                    captured.parsed.problem = blocks.BlockProblem(str(captured.root / 'block.json'), 'invalid',
                        ({'location': location, 'message': 'a text input has no nested fields'},), 1)
                continue
            try:
                field_rows(node)
            except WorkshopError:
                continue
            for segment in placeholder.path[1:]:
                properties = node.get('properties', {})
                if segment not in properties:
                    if node.get('additionalProperties', True) is False or node.get('type') not in ('object', ['object', 'null']):
                        captured.parsed.problem = blocks.BlockProblem(str(captured.root / 'block.json'), 'invalid',
                            ({'location': location, 'message': 'the placeholder names an undeclared structured field'},), 1)
                    break
                node = properties[segment]

    def export(self, key, revision=None, support=None):
        from . import blocks
        root = self.root(key)
        # Pin while the complete revision is copied (the shared current/historical pin).
        physical, release = self.pin(key, revision)
        h = head(root)
        try:
            metadata = document(physical, 'workshop.json') if h else {'format_version': 1, 'origin': 'manual', 'effects': 'unknown'}
            if metadata.get('format_version') != 1:
                fail('newer_format', 409)
            if h:
                validate_generation(physical, metadata)
            # A historical generation is read with its own captured definitions, so a damaged or later live
            # contract file does not prevent viewing it. The restored draft keeps its original references;
            # publication validates them against the live store again.
            resolver = SnapshotContracts(metadata) if revision is not None else self.contracts()
            parsed = blocks.parse_block(physical, key, resolver)
            if parsed.problem:
                fail('migration_required', 400) if parsed.problem.code == 'migration_required' else fail('unusable', 409)
            paths = set(parsed.paths)
            selected = metadata.get('support', []) if support is None else support
            if parsed.kind == 'script' and not h and support is None:
                fail('support_required', 409)
            if not isinstance(selected, list):
                fail()
            paths.update(relative(p) for p in selected)
            files = {p: read(physical, p) for p in paths}
            if sum(map(len, files.values())) > CAPTURE_LIMIT:
                fail('too_large', 400)
            # Detect observed changes during capture.
            if any(read(physical, p) != b for p, b in files.items()):
                fail('stale', 409)
            if revision is not None:
                self.retained(key, revision)
            metadata = {**metadata, 'support': selected}
            return {'key': key, 'definition_text': files.pop('block.json').decode('utf-8'),
                    'files': wire_files(files), 'metadata': metadata,
                    'base_digest': self.identity(key)}
        finally:
            release()

    def saved(self, key, revision=None):
        """A clean saved selection of one managed generation: every executable asset read from disk with the
        generation's own metadata, unmodified. `revision=None` resolves the current generation once, with the
        copy. Never applies authoring mutations or current-head provenance (spec v2 2.1(3))."""
        root = self.root(key)
        physical, release = self.pin(key, revision)
        try:
            if physical == root:
                fail('not_managed', 409)
            ident = physical.name
            metadata = document(physical, 'workshop.json')
            if metadata.get('format_version') != 1:
                fail('newer_format', 409)
            validate_generation(physical, metadata)
            files = {relative(p): read(physical, relative(p)) for p in metadata['assets']}
            if any(hashlib.sha256(b).hexdigest() != metadata['assets'][p] for p, b in files.items()):
                fail('stale', 409)
            try:
                definition = jsonio.decode_exact(files['block.json'].decode('utf-8'))
            except (KeyError, ValueError, UnicodeError):
                fail('unusable', 409)
            version = definition.get('format_version') if isinstance(definition, dict) else None
            if type(version) is int and version < EXECUTABLE_VERSION:
                fail('migration_required', 400)
            if version != EXECUTABLE_VERSION:
                fail('newer_format', 409)
            self.retained(key, ident)
        finally:
            release()
        row = next((r for r in self.revisions(key)['revisions'] if r['id'] == ident), None)
        if row is None:
            fail('missing', 404)
        return SavedGeneration(key, ident, row['revision_number'], row['revision_name'], metadata, files,
                               definition)

    def identity(self, key):
        root = self.path / key_name(key)
        if not root.exists():
            return None
        self.root(key)
        h = head(root)
        if h:
            physical, release = self.pin(key)
            try:
                validate_generation(physical, document(physical, 'workshop.json'))
                return fingerprint({'block.json': read(root, 'block.json')})
            finally:
                release()
        from . import blocks
        parsed = blocks.parse_block(root, key, self.contracts())
        if parsed.problem:
            fail('unusable', 409)
        return fingerprint({p: read(root, p) for p in parsed.paths})

    def revisions(self, key):
        root = self.root(key)
        h = head(root)
        if h is None:
            return {'format_version': 1, 'current': None, 'revisions': []}
        rows = [
            {'id': i, 'metadata': document(root / '.workshop' / 'revisions' / i, 'workshop.json')}
            for i in h['retained']]
        # Retention is a contiguous newest-first suffix. A numbered generation anchors
        # older unnumbered history without rewriting immutable generations on read.
        anchor = next(((index, r['metadata']['revision_number']) for index, r in enumerate(rows)
                       if type(r['metadata'].get('revision_number')) is int
                       and r['metadata']['revision_number'] > 0), None)
        newest = anchor[0] + anchor[1] if anchor else len(rows)
        for index, row in enumerate(rows):
            row['revision_number'] = newest - index
            row['revision_name'] = row['metadata'].get('revision_name', '')
        return {'format_version': 1, 'current': h['current'], 'revisions': rows}

    def publish(self, draft, expected, metadata, capture=None):
        key = key_name(draft['key'])
        if self.state.workspace.read_only:
            fail('read_only', 409)
        writer = self.state.register_writer('Workshop save')
        stage = None
        def check_root_sources():
            for path, expected_digest in draft.get('root_sources', {}).items():
                if hashlib.sha256(read(self.root(key), path)).hexdigest() != expected_digest:
                    fail('source_changed', 409)
        try:
            with self.registry(), self.reserve(key):
                if capture is not None and capture.parsed.contract_pins:
                    # Recheck the references against the live store under the registry reservation, so a
                    # concurrent contract deletion cannot remove a revision this publication pins.
                    live = self.contracts()
                    for pin in capture.parsed.contract_pins:
                        if live.resolve(pin.name, pin.revision, pin.digest)[0] is None:
                            fail('reference_invalid', 409)
                if self.identity(key) != expected:
                    fail('stale', 409)
                check_root_sources()
                self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
                if self.path.is_symlink():
                    fail('unusable', 409)
                if any(p.name.casefold() == key.casefold() and p.name != key for p in self.path.iterdir()):
                    fail('conflict', 409)
                with (self.validate(draft) if capture is None else contextlib.nullcontext(capture)) as captured:
                    if captured.parsed.problem:
                        fail('unusable', 409)
                    root = self.path / key
                    old = head(root) if expected is not None else None
                    if old:
                        next_number = self.revisions(key)['revisions'][0]['revision_number'] + 1
                    else:
                        next_number = 2 if expected is not None else 1
                    stage = Path(tempfile.mkdtemp(prefix='.workshop-stage-', dir=self.path))
                    (stage / '.owner').write_bytes(b'AgentWiz Workshop staging v1\n')
                    os.chmod(stage / '.owner', 0o600)
                    generations = []
                    if expected is not None and old is None:
                        origin = self.export(key, support=metadata.get('support', []))
                        generations.append((secrets.token_hex(16), origin))
                    revision = secrets.token_hex(16)
                    generations.append((revision, {**draft, 'metadata': metadata}))
                    retained = [revision] + ([generations[0][0]] if len(generations) == 2 else old['retained'] if old else [])
                    for ident, value in generations:
                        files = decode_files(value['files'])
                        files['block.json'] = value['definition_text'].encode('utf-8')
                        if ident != revision and 'contract_pins' not in value['metadata']:
                            # The converted original root gets its own captured maps, so its history stays
                            # readable through the snapshot resolver like every other generation.
                            with Capture(files, key, self.contracts()) as original:
                                if original.parsed.block is not None:
                                    contracts_map, pins = captured_maps(original.parsed.block)
                                    value = {**value, 'metadata': {**value['metadata'], 'contracts': contracts_map,
                                             'contract_pins': pins,
                                             'contracts_digest': contract_maps_digest(contracts_map, pins)}}
                        m = {'display_name': key, 'origin': 'manual', 'effects': 'unknown',
                             'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                             'parent': old['current'] if old else generations[0][0] if len(generations) == 2 and ident == revision else None,
                             'contracts_validated_at_save': True, **value['metadata'], 'format_version': 1, 'asset_digest': fingerprint(files),
                             'assets': {p: hashlib.sha256(b).hexdigest() for p, b in files.items()},
                             'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                             'parent': old['current'] if old else generations[0][0] if len(generations) == 2 and ident == revision else None}
                        m['revision_number'] = next_number if ident == revision else 1
                        m['revision_name'] = value['metadata'].get('revision_name', '').strip()
                        files['workshop.json'] = jsonio.encode_file_exact(m)
                        if sum(map(len, files.values())) > CAPTURE_LIMIT:
                            fail('too_large', 400)
                        write_files(stage / ident, files)
                    if self.identity(key) != expected:
                        fail('stale', 409)
                    check_root_sources()
                    h = {'format_version': 2, 'current': revision, 'retained': retained[:REVISION_LIMIT]}
                    if expected is None:
                        initial = stage / 'new'
                        destination = initial / '.workshop' / 'revisions'
                        destination.mkdir(parents=True, mode=0o700)
                        for ident, _ in generations:
                            os.rename(stage / ident, destination / ident)
                        write_atomic_bytes(initial, 'block.json', jsonio.encode_file_exact(h))
                        if root.exists():
                            fail('conflict', 409)
                        os.rename(initial, root)
                    else:
                        destination = root / '.workshop' / 'revisions'
                        if (root / '.workshop').is_symlink() or destination.is_symlink():
                            fail('unusable', 409)
                        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
                        for ident, _ in generations:
                            os.rename(stage / ident, destination / ident)
                        sync_directory(destination)
                        try:
                            write_atomic_bytes(root, 'block.json', jsonio.encode_file_exact(h))
                        except OSError:
                            if head(root) != h:
                                raise
                            return {'key': key, 'durable_id': revision, 'base_digest': self.identity(key),
                                    'revision_number': next_number, 'revision_name': metadata.get('revision_name', '').strip(),
                                    'publication': 'durability_uncertain'}
                    durability_uncertain = False
                    try:
                        sync_directory(root)
                        sync_directory(self.path)
                    except OSError:
                        durability_uncertain = True
                    # A later save also reaps generations whose old readers have released pins.
                    for p in (root / '.workshop' / 'revisions').iterdir():
                        with self.lock:
                            pinned = self.pins.get((key, p.name), 0)
                        if TOKEN.fullmatch(p.name) and p.name not in h['retained'] and not pinned and not p.is_symlink():
                            try:
                                shutil.rmtree(p)
                            except OSError:
                                pass  # Reap this unreferenced generation at a later save/startup.
                    # Descriptions of pruned generations; never fails or rolls back the committed head.
                    prune_descriptions(root, h['retained'])
                    return {'key': key, 'durable_id': revision, 'base_digest': self.identity(key),
                            'revision_number': next_number, 'revision_name': metadata.get('revision_name', '').strip(),
                            'publication': 'durability_uncertain' if durability_uncertain else 'committed'}
        finally:
            if stage:
                shutil.rmtree(stage, ignore_errors=True)
            writer.release()


    def recover(self):
        """Reap only our private staging and understood unreferenced generations."""
        if self.state.workspace.read_only or not self.path.exists() or self.path.is_symlink():
            return
        writer = self.state.register_writer('Workshop startup cleanup')
        try:
            for root in self.path.iterdir():
                if root.is_symlink() or not root.is_dir():
                    continue
                if root.name.startswith('.workshop-stage-'):
                    marker = root / '.owner'
                    if marker.is_file() and not marker.is_symlink() and marker.read_bytes() == b'AgentWiz Workshop staging v1\n':
                        shutil.rmtree(root)
                    continue
                if not valid_name(root.name):
                    continue
                try:
                    h = head(root)
                    if h is None:
                        continue
                    revisions = root / '.workshop' / 'revisions'
                    if revisions.is_symlink() or (root / '.workshop').is_symlink():
                        continue
                    for ident in h['retained']:
                        metadata = document(revisions / ident, 'workshop.json')
                        validate_generation(revisions / ident, metadata)
                    for folder in revisions.iterdir():
                        if TOKEN.fullmatch(folder.name) and folder.name not in h['retained'] and folder.is_dir() and not folder.is_symlink():
                            shutil.rmtree(folder)
                    prune_descriptions(root, h['retained'])
                except (WorkshopError, OSError, ValueError):
                    # Damaged/newer heads and their assets remain intact for inspection.
                    continue
        finally:
            writer.release()


def contract_maps_digest(contracts, pins):
    return hashlib.sha256(jsonio.encode_exact({'contracts': contracts, 'contract_pins': pins}).encode('utf-8')).hexdigest()


def captured_maps(block):
    """Generation metadata maps for a parsed block: every port's exact captured text, and the exact pin of
    every referenced port only."""
    contracts = {side: {d.name: block.contracts[side][d.name].text for d in getattr(block, side)}
                 for side in ('inputs', 'outputs')}
    pins = {side: {d.name: d.reference.view() for d in getattr(block, side) if d.reference is not None}
            for side in ('inputs', 'outputs')}
    return contracts, pins


def validate_contract_maps(definition, metadata):
    """Every declared port in `contracts`, exactly the referenced ports in `contract_pins`, each pin equal to
    its declaration and its captured text's digest, each inline snapshot equal to the asset's definition."""
    contracts, pins = metadata.get('contracts'), metadata.get('contract_pins')
    if not isinstance(contracts, dict) or not isinstance(pins, dict):
        fail('unusable', 409)
    if set(contracts) != {'inputs', 'outputs'} or set(pins) != {'inputs', 'outputs'}:
        fail('unusable', 409)
    if metadata.get('contracts_digest') != contract_maps_digest(contracts, pins):
        fail('source_changed', 409)
    for side in ('inputs', 'outputs'):
        declared = definition.get(side)
        if not isinstance(declared, dict) or not isinstance(contracts[side], dict) or not isinstance(pins[side], dict):
            fail('unusable', 409)
        if set(contracts[side]) != set(declared):
            fail('unusable', 409)
        referenced = {n for n, d in declared.items() if isinstance(d, dict) and isinstance(d.get('contract'), dict)
                      and 'format' not in d['contract']}
        if set(pins[side]) != referenced:
            fail('unusable', 409)
        for port, text in contracts[side].items():
            try:
                captured = jsonio.decode_exact(text)
                digest = contract_schema.contract_digest(captured)
            except (TypeError, ValueError):
                fail('unusable', 409)
            declaration = declared[port].get('contract') if isinstance(declared[port], dict) else None
            if port in referenced:
                if pins[side][port] != declaration or digest != declaration.get('digest'):
                    fail('source_changed', 409)
            else:
                try:
                    if not isinstance(declaration, dict) or digest != contract_schema.contract_digest(declaration):
                        fail('source_changed', 409)
                except ValueError:
                    fail('source_changed', 409)


def validate_generation(root, metadata):
    if metadata.get('format_version') != 1:
        fail('newer_format', 409)
    assets = metadata.get('assets')
    if not isinstance(assets, dict):
        fail('unusable', 409)
    files = {relative(p): read(root, relative(p)) for p in assets}
    if any(hashlib.sha256(b).hexdigest() != assets[p] for p, b in files.items()) or fingerprint(files) != metadata.get('asset_digest'):
        fail('source_changed', 409)
    try:
        definition = jsonio.decode_exact(files['block.json'].decode('utf-8'))
    except (KeyError, ValueError, UnicodeError):
        fail('unusable', 409)
    if isinstance(definition, dict) and definition.get('format_version') == EXECUTABLE_VERSION:
        validate_contract_maps(definition, metadata)


# --- references to saved contracts (spec 10.4) -------------------------------------------------------------
_SCANNED_EXECUTABLE_VERSIONS = (1, EXECUTABLE_VERSION)


def _declared_references(definition, name):
    """((side, port, revision) of every raw declaration naming contract `name`, inspectable).

    Scans raw declarations without resolving anything. Every executable format (v1 and v3) requires `inputs`
    and `outputs` objects whose declarations are objects holding `contract`, so no absent or malformed
    collection, declaration or contract member is legitimate: each makes the definition not inspectable,
    because absence of use cannot then be established. Identifiable references are still reported from a
    broken definition. An old string reference counts conservatively, with no revision; so does a
    reference-shaped object naming the contract whatever its other members."""
    found = []
    if not isinstance(definition, dict):
        return found, False
    version = definition.get('format_version')
    inspectable = type(version) is int and version in _SCANNED_EXECUTABLE_VERSIONS
    for side in ('inputs', 'outputs'):
        ports = definition.get(side)
        if not isinstance(ports, dict):
            inspectable = False
            continue
        for port, declaration in ports.items():
            if not isinstance(declaration, dict) or 'contract' not in declaration:
                inspectable = False
                continue
            contract = declaration['contract']
            if isinstance(contract, str):
                if contract == name:
                    found.append((side, port, None))
            elif isinstance(contract, dict):
                pin_like = 'format' not in contract and bool({'name', 'revision', 'digest'} & set(contract))
                if contract.get('name') == name:
                    revision = contract.get('revision')
                    found.append((side, port, revision if type(revision) is int and revision >= 1 else None))
                elif pin_like and not isinstance(contract.get('name'), str):
                    inspectable = False                     # a pin whose target cannot be read
            else:
                inspectable = False
    return found, inspectable


def reference_scan(blocks_path, name, latest=None):
    """Blockers among current unmanaged definitions and every retained managed generation (current once),
    and the paths that could not be inspected. Orphaned generations and run records are not inspected.

    Fails closed: a root or generation whose structure does not let every declaration be enumerated (a
    managed head breaking the `head` invariants, a newer or unknown format, a non-object definition, a
    malformed port collection or declaration) contributes its path to the unreadable list, which refuses
    deletion with reference_scan_incomplete. Broken files are never rewritten."""
    from .blocks import read_file
    blockers, unreadable = [], []
    path = Path(blocks_path)
    if not path.exists() and not path.is_symlink():
        return blockers, unreadable
    if path.is_symlink() or not path.is_dir():
        return blockers, [str(path)]
    try:
        roots = sorted(path.iterdir())
    except OSError:
        return blockers, [str(path)]

    def inspect(definition, where, block, generation, current):
        found, inspectable = _declared_references(definition, name)
        if not inspectable:
            unreadable.append(where)
        for side, port, revision in found:
            blockers.append({'block': block, 'generation': generation, 'current': current,
                             'side': side, 'port': port, 'revision': revision})

    def decoded(folder, rel):
        result = read_file(folder, rel, 1 << 20)
        if result.data is None:
            return None, result.fingerprint == ('missing',)
        try:
            return jsonio.decode_exact(result.data.decode('utf-8')), False
        except (ValueError, UnicodeError):
            return None, False

    for root in roots:
        if not valid_name(root.name):
            continue
        if root.is_symlink():
            unreadable.append(str(root))
            continue
        if not root.is_dir():
            continue
        where = str(root / 'block.json')
        obj, missing = decoded(root, 'block.json')
        if obj is None:
            if not missing:
                unreadable.append(where)
            continue
        version = obj.get('format_version') if isinstance(obj, dict) else None
        if type(version) is int and version in _SCANNED_EXECUTABLE_VERSIONS:
            inspect(obj, where, root.name, None, True)
            continue
        try:
            managed = head_document(obj)
        except WorkshopError:
            managed = None
        if managed is None:                                  # unknown, newer or broken root: not inspectable
            unreadable.append(where)
            continue
        for index, ident in enumerate(managed['retained']):
            rel = f'.workshop/revisions/{ident}/block.json'
            definition, _missing = decoded(root, rel)        # a retained generation must exist
            if definition is None:
                unreadable.append(str(root / rel))
                continue
            inspect(definition, str(root / rel), root.name, ident, index == 0)
    for row in blockers:
        row['newer_available'] = bool(latest and row['revision'] and row['revision'] < latest)
    blockers.sort(key=lambda r: (r['block'], not r['current'], r['side'], r['port'], r['revision'] or 0))
    return blockers, sorted(set(unreadable))
