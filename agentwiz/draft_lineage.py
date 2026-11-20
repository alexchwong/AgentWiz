"""Bounded workspace editor references and their design-protection lineage."""
from __future__ import annotations

import secrets
from collections import OrderedDict
from dataclasses import dataclass

from . import jsonio
from .workshop_assets import fail

LIMIT = 512


@dataclass(frozen=True)
class Lineage:
    protection: str | None
    generation: str | None

    @classmethod
    def of(cls, draft, generation=None):
        definition = jsonio.decode_exact(draft['definition_text'])
        metadata = draft.get('metadata', {})
        protection = None
        if definition.get('context_local_only') is True:
            protection = 'manual' if metadata.get('protection_origin') == 'manual' else 'inherited'
        return cls(protection, generation)


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

    def get(self, ident):
        if not isinstance(ident, str):
            fail('draft_expired', 409)
        with self.lock:
            record = self.records.get(ident)
            if record is not None:
                self.records.move_to_end(ident)
                return record
        fail('draft_expired', 409)

