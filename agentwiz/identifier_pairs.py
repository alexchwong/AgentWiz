"""One parser for identifier mappings, with a scanning profile (Release Gate) and a restoring profile
(`restore_placeholders`). It raises only `PairsError`; each caller maps it to its own located error."""
from __future__ import annotations

import re
from dataclasses import dataclass

ENTRY_LIMIT = 1000
ORIGINAL_LIMIT = 4096
PLACEHOLDER_LIMIT = 256
SYNTAXES = {'bracket_upper': re.compile(r'\[[A-Z][A-Z0-9_]*\]')}


class PairsError(ValueError):
    """`index` is the failing entry, or None for a list-level problem. The message never names a value."""

    def __init__(self, index, message):
        super().__init__(message)
        self.index = index
        self.message = message


@dataclass(frozen=True)
class Entry:
    original: str
    placeholder: str      # scan profile: the label (the entry index when none was given)


def parse(value, *, profile, syntax='bracket_upper'):
    if profile not in ('scan', 'restore'):
        raise ValueError('unknown identifier mapping profile')
    if not isinstance(value, list) or len(value) > ENTRY_LIMIT:
        raise PairsError(None, 'expected a bounded list of identifiers')
    pattern = SYNTAXES[syntax] if profile == 'restore' else None
    entries, seen = [], {}
    for index, item in enumerate(value):
        if profile == 'scan' and isinstance(item, str):
            original, placeholder = item, str(index)
        elif (isinstance(item, dict) and isinstance(item.get('original'), str)
              and isinstance(item.get('placeholder', ''), str)):
            original, placeholder = item['original'], item.get('placeholder', '')
            if profile == 'scan':
                placeholder = placeholder or str(index)
        else:
            raise PairsError(index, 'each entry needs nonempty original text' if profile == 'scan'
                             else 'each entry needs an object with placeholder and original text')
        if not original or len(original) > ORIGINAL_LIMIT or len(placeholder) > PLACEHOLDER_LIMIT:
            raise PairsError(index, 'identifier entries must be nonempty and bounded')
        if profile == 'restore':
            if not placeholder or pattern.fullmatch(placeholder) is None:
                raise PairsError(index, 'the placeholder does not match the declared placeholder syntax')
            previous = seen.get(placeholder)
            if previous is not None:
                if previous != original:
                    raise PairsError(index, 'the placeholder is mapped to different originals')
                continue
            seen[placeholder] = original
        entries.append(Entry(original, placeholder))
    return entries
