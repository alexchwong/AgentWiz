"""Small shared reporting helpers for the two non-shipped evaluation runners."""
from __future__ import annotations

import os
import traceback
from pathlib import Path

from agentwiz import history, jsonio
from agentwiz.workspace import write_atomic_bytes


def credentials(client=None):
    values = [os.environ.get('OPENROUTER_API_KEY', '')]
    values.extend(value for key, value in os.environ.items()
                  if key.startswith('AGENTWIZ_LIVE_') and key.endswith(('_KEY', '_TOKEN')))
    if client is not None and getattr(client, 'ui', None) is not None:
        values.extend(client.ui.state.credentials())
    if client is not None:
        values.append(getattr(client, 'token', ''))
    return values


def safe(value, client=None):
    return history.redact(value, credentials(client))[0]


def error(exc, stage):
    # External messages may contain credentials or backend text. Keep only locations and class.
    return {'stage': stage, 'class': type(exc).__name__,
            'locations': [{'file': Path(frame.filename).name, 'line': frame.lineno, 'function': frame.name}
                          for frame in traceback.extract_tb(exc.__traceback__)[-8:]]}


def checkpoint(path, report, client=None):
    if path is None:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic_bytes(path.parent, path.name, jsonio.encode_file(safe(report, client)))


def counts(rows):
    return {status: sum(row.get('verdict') == status for row in rows)
            for status in ('passed', 'failed', 'blocked')}


def verdict(rows):
    statuses = {row.get('verdict') for row in rows}
    return next((status for status in ('failed', 'blocked', 'incomplete') if status in statuses),
                'passed' if rows and statuses == {'passed'} else 'incomplete')
