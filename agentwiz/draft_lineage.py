"""Bounded workspace editor references and historical script provenance."""
from __future__ import annotations

import hashlib
import secrets
from collections import OrderedDict
from dataclasses import dataclass

from . import jsonio
from .workshop import WorkshopError
from .workshop_assets import decode_files, document, fail, head, read

GENERATED = ('llm_generated', 'derived_from_llm')
LIMIT = 512


@dataclass(frozen=True)
class Lineage:
    script_origin: str | None
    protection: str | None
    generation: str | None
    source_hash: str | None = None

    @classmethod
    def of(cls, draft, generation=None):
        definition = jsonio.decode_exact(draft['definition_text'])
        step = next(iter(definition.get('steps', [])), {})
        metadata = draft.get('metadata', {})
        origin = None
        if step.get('kind') == 'script':
            origin = metadata.get('script_origin', metadata.get('origin', 'manual'))
            if metadata.get('origin') in GENERATED:
                origin = 'derived_from_llm'
        protection = None
        if definition.get('context_local_only') is True:
            protection = 'manual' if metadata.get('protection_origin') == 'manual' else 'inherited'
        return cls(origin, protection, generation, main_source_hash(draft) if origin else None)


class References:
    def __init__(self, lock):
        self.lock = lock
        self.records = OrderedDict()

    def issue(self, draft, lineage):
        ident = secrets.token_hex(16)
        with self.lock:
            self.records[ident] = lineage
            while len(self.records) > LIMIT:
                self.records.popitem(last=False)
        draft['draft_ref'] = ident
        return draft

    def generated(self, source_hash):
        with self.lock:
            return any(record.source_hash == source_hash and record.script_origin in GENERATED
                       for record in self.records.values())

    def get(self, ident):
        if not isinstance(ident, str):
            fail('draft_expired', 409)
        with self.lock:
            record = self.records.get(ident)
            if record is not None:
                self.records.move_to_end(ident)
                return record
        fail('draft_expired', 409)


def generated_fingerprint(root, source_hash):
    """Consult durable exact generations. The current pointer does not identify an older source."""
    if not root.exists():
        return False
    for directory in root.iterdir():
        if not directory.is_dir() or directory.is_symlink():
            continue
        try:
            managed = head(directory)
        except WorkshopError:
            continue
        if managed is None:
            continue
        for ident in managed['retained']:
            path = directory / '.workshop' / 'revisions' / ident
            try:
                metadata = document(path, 'workshop.json')
                if not any(metadata.get(field) in GENERATED for field in ('origin', 'script_origin')):
                    continue
                definition = jsonio.decode_file_exact(read(path, 'block.json'))
                step = next(iter(definition.get('steps', [])), {})
                if step.get('kind') == 'script' and metadata.get('assets', {}).get(step.get('file')) == source_hash:
                    return True
            except (ValueError, OSError, WorkshopError):
                continue
    return False


def main_source_hash(draft):
    definition = jsonio.decode_exact(draft['definition_text'])
    step = next(iter(definition.get('steps', [])), {})
    if step.get('kind') != 'script':
        return None
    data = decode_files(draft.get('files', {})).get(step.get('file'))
    return hashlib.sha256(data).hexdigest() if data is not None else None
