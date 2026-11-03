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

from . import checks, jsonio
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


def head(root):
    obj = document(root, 'block.json')
    if not isinstance(obj, dict):
        fail('unusable', 409)
    if obj.get('format_version') == 1:
        return None
    if obj.get('format_version') != 2:
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


class Capture:
    def __init__(self, files, key, contracts, metadata=None):
        from . import blocks
        self.directory = tempfile.TemporaryDirectory(prefix='agentwiz-capture-')
        self.root = Path(self.directory.name)
        try:
            write_files(self.root, files)
            self.parsed = blocks.parse_block(self.root, key, contracts)
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

    def pin(self, key):
        root = self.root(key)
        for _ in range(3):
            h = head(root)
            if h is None:
                return root, lambda: None
            identity = (key, h['current'])
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
                physical = root / '.workshop' / 'revisions' / h['current']
                paths = (root / '.workshop', physical.parent, physical)
                if any(p.is_symlink() for p in paths):
                    fail('unusable', 409)
                if any(not p.is_dir() for p in paths):
                    if head(root) != h:
                        release()
                        continue
                    fail('unusable', 409)
                if head(root) == h:
                    return physical, release
            except BaseException:
                release()
                raise
            release()
        fail('stale', 409)

    def contracts(self):
        # Loader resolves all references against a coherent state-owned copy.
        with self.state._lock:
            self.state.contracts.rescan()
            return copy.deepcopy(self.state.contracts)

    def validate(self, draft):
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
        if not isinstance(obj, dict) or obj.get('format_version') != 1 or obj.get('name') != key:
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
        captured = Capture(files, key, self.contracts(), metadata)
        self.check_references(captured)
        if captured.parsed.block is not None and set(files) != set(captured.parsed.paths) | set(support):
            captured.close()
            fail('undeclared_asset', 400)
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
        h = head(root)
        if revision is not None:
            if h is None or revision not in h['retained']:
                fail('missing', 404)
            physical = root / '.workshop' / 'revisions' / revision
            # Pin while the complete revision is copied.
            identity = (key, revision)
            with self.lock:
                self.pins[identity] = self.pins.get(identity, 0) + 1
            def release():
                with self.lock:
                    n = self.pins.get(identity, 1) - 1
                    if n:
                        self.pins[identity] = n
                    else:
                        self.pins.pop(identity, None)
        else:
            physical, release = self.pin(key)
        try:
            parsed = blocks.parse_block(physical, key, self.contracts())
            if parsed.problem:
                fail('unusable', 409)
            metadata = document(physical, 'workshop.json') if h else {'format_version': 1, 'origin': 'manual', 'effects': 'unknown'}
            if metadata.get('format_version') != 1:
                fail('newer_format', 409)
            if h:
                validate_generation(physical, metadata)
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
            metadata = {**metadata, 'support': selected}
            if revision is not None and metadata.get('contracts'):
                definition = jsonio.decode_exact(files['block.json'].decode('utf-8'))
                for side, ports in metadata['contracts'].items():
                    for name, text in ports.items():
                        definition[side][name]['contract'] = jsonio.decode_exact(text)
                files['block.json'] = jsonio.encode_exact(definition).encode('utf-8')
                metadata['restore_contracts'] = True
            return {'key': key, 'definition_text': files.pop('block.json').decode('utf-8'),
                    'files': wire_files(files), 'metadata': metadata,
                    'base_digest': self.identity(key)}
        finally:
            release()

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
        return {'format_version': 1, 'current': h['current'], 'revisions': [
            {'id': i, 'metadata': document(root / '.workshop' / 'revisions' / i, 'workshop.json')}
            for i in h['retained']]}

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
            with self.reserve(key):
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
                        m = {'display_name': key, 'origin': 'manual', 'effects': 'unknown',
                             'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                             'parent': old['current'] if old else generations[0][0] if len(generations) == 2 and ident == revision else None,
                             'contracts_validated_at_save': True, **value['metadata'], 'format_version': 1, 'asset_digest': fingerprint(files),
                             'assets': {p: hashlib.sha256(b).hexdigest() for p, b in files.items()},
                             'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                             'parent': old['current'] if old else generations[0][0] if len(generations) == 2 and ident == revision else None}
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
                    return {'key': key, 'durable_id': revision, 'base_digest': self.identity(key),
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
                except (WorkshopError, OSError, ValueError):
                    # Damaged/newer heads and their assets remain intact for inspection.
                    continue
        finally:
            writer.release()


def validate_generation(root, metadata):
    if metadata.get('format_version') != 1:
        fail('newer_format', 409)
    assets = metadata.get('assets')
    if not isinstance(assets, dict):
        fail('unusable', 409)
    files = {relative(p): read(root, relative(p)) for p in assets}
    if any(hashlib.sha256(b).hexdigest() != assets[p] for p, b in files.items()) or fingerprint(files) != metadata.get('asset_digest'):
        fail('source_changed', 409)
