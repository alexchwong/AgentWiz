"""Workspace setting for reviewed MCP workflow execution."""
from __future__ import annotations

import secrets

from . import workspace
from .workshop import WorkshopError

KEY = 'workflow_mcp_confirmation'
MODES = ('every_run', 'effectful_batches')


def read(state):
    raw, problem = workspace.read_workspace(state.workspace)
    raw = raw or {}
    mode = raw.get(KEY, MODES[0])
    invalid = problem is not None or mode not in MODES
    return {'mode': MODES[0] if invalid else mode, 'invalid': invalid,
            'revision': raw.get(KEY + '_revision')}


def update(state, body):
    if set(body) != {'mode', 'expected_revision'} or body['mode'] not in MODES:
        raise WorkshopError('invalid', 400)
    writer = state.register_writer('workflow MCP confirmation')
    try:
        with state._lock:
            raw, problem = workspace.read_workspace(state.workspace)
            if problem or state.workspace.read_only:
                raise WorkshopError('read_only', 409)
            if raw.get(KEY + '_revision') != body['expected_revision']:
                raise WorkshopError('stale', 409)
            raw[KEY] = body['mode']
            raw[KEY + '_revision'] = secrets.token_hex(16)
            workspace.write_workspace(state.workspace, raw)
            return read(state)
    finally:
        writer.release()
