"""Private workflow evidence with one durable intent per node dispatch."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from . import __version__, blocks, history, jsonio, output_protocol
from .workshop import TOKEN
from .workshop_assets import sync_directory
from .workflow_definition import problem
from .workspace import write_atomic_bytes

LIMIT = 128 << 20
MANIFEST_LIMIT = 4 << 20


class EvidenceError(Exception):
    pass


def directory(workspace, *, create=False):
    root = Path(workspace)
    for name in ('runs', 'workflows'):
        root = root / name
        if root.is_symlink():
            raise EvidenceError('unsafe evidence directory')
        if create:
            root.mkdir(mode=0o700, exist_ok=True)
        if root.exists() and not root.is_dir():
            raise EvidenceError('unsafe evidence directory')
    return root


def safe_id(ident):
    if not isinstance(ident, str) or TOKEN.fullmatch(ident) is None:
        problem('invalid', 'job', 'invalid workflow evidence identifier', 400)
    return ident


class Evidence:
    def __init__(self, state, ident, bundle, plan):
        self.state = state
        root = directory(state.workspace.path, create=True)
        folder = Path(tempfile.mkdtemp(prefix='.workflow-', dir=root))
        self.folder = folder
        self.used = 0
        self.case = 0
        self.node = None
        self.error = False
        self.manifest = {'format_version': 1, 'id': ident, 'outcome': 'running', 'intent': None,
                         'records': ['capture.json'], 'name': bundle['definition']['name']}
        capture = {'format_version': 1, 'agentwiz_version': __version__, 'renderer': output_protocol.RENDERER_VERSION,
                   'definition': bundle['definition'], 'snapshots': bundle['snapshots'],
                   'review': {key: plan[key] for key in ('executable_digest', 'order', 'destinations', 'limits', 'examples',
                                                        'mcp_confirmation', 'repetitions', 'restricted_sources', 'saving')}}
        if 'example_plans' in plan:
            capture['review']['example_destinations'] = [review['destinations'] for review in plan['example_plans']]
        try:
            self.write('capture.json', capture, exact=True)
            self.persist()
            target = root / safe_id(ident)
            os.rename(folder, target)
            self.folder = target
            sync_directory(root)
        except BaseException:
            shutil.rmtree(self.folder, ignore_errors=True)
            raise

    def write(self, name, record, *, exact=False):
        redacted, changed = history.redact(record, self.state.credentials())
        if exact and changed:
            raise EvidenceError('known credential in executable capture')
        if changed:
            redacted['redacted'] = True
        data = jsonio.encode_file_exact(redacted)
        if self.used + len(data) > LIMIT - MANIFEST_LIMIT:
            raise EvidenceError('workflow evidence exceeds its limit')
        write_atomic_bytes(self.folder, name, data)
        self.used += len(data)

    def persist(self):
        clean, _ = history.redact(self.manifest, self.state.credentials())
        data = jsonio.encode_file_exact(clean)
        if len(data) > MANIFEST_LIMIT:
            raise EvidenceError('workflow manifest exceeds its limit')
        write_atomic_bytes(self.folder, 'manifest.json', data)
        sync_directory(self.folder)

    def pending(self, node):
        self.node = node
        self.manifest['intent'] = {'example': self.case, 'node': node, 'state': 'dispatch_pending'}
        self.persist()

    @staticmethod
    def file_key(node):
        return node if len(node.encode('utf-8')) <= 128 else hashlib.sha256(node.encode('utf-8')).hexdigest()

    def attempt(self, record):
        file = f"{self.case}-{self.file_key(self.node)}-attempt-{record['attempt']}.json"
        self.write(file, record)
        self.manifest['records'].append(file)

    def node_result(self, node, record):
        file = f'{self.case}-{self.file_key(node)}.json'
        self.write(file, record)
        self.manifest['records'].append(file)
        self.manifest['intent'] = None
        self.persist()

    def finish(self, summary):
        self.write('result.json', summary)
        self.manifest['records'].append('result.json')
        self.manifest.update(outcome=summary['outcome'], result='result.json')
        self.persist()


def read(state, ident):
    safe_id(ident)
    root = directory(state.workspace.path)
    loaded = blocks.read_file(root.parent.parent, f'runs/workflows/{ident}/manifest.json', MANIFEST_LIMIT)
    if loaded.data is None:
        problem('missing', 'evidence', 'workflow evidence is unavailable', 404)
    try:
        return jsonio.decode_file_exact(loaded.data)
    except ValueError:
        problem('evidence_unusable', 'evidence', 'workflow evidence is invalid')


def read_record(state, ident, name, manifest):
    if (not isinstance(name, str) or '/' in name or name.startswith('.')
            or name not in manifest.get('records', [])):
        problem('missing', 'evidence', 'the requested record does not exist', 404)
    root = directory(state.workspace.path)
    loaded = blocks.read_file(root.parent.parent, f'runs/workflows/{ident}/{name}', 32 << 20)
    if loaded.data is None:
        problem('evidence_unusable', 'evidence', 'the requested record cannot be read')
    try:
        return jsonio.decode_file_exact(loaded.data)
    except ValueError:
        problem('evidence_unusable', 'evidence', 'the requested record is invalid')
