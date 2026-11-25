#!/usr/bin/env python3
"""Run one reviewed synthetic OpenRouter skill proposal in a disposable workspace."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentwiz import __version__
import eval_reports
import eval_setup
from library_eval import Client, Refused

SOURCE = """---
name: synthetic-count-words
description: Count whitespace-separated words in synthetic input
---
Draft one workflow using only the word_count step.
"""


def check(client, report):
    profile = 'openrouter-eval'
    state = client.json('/api/state')
    roles = state['roles']
    client.json('/api/roles/save', {'roles': {**roles['map'], 'propose_agent': profile},
                                   'revision': roles['revision']})

    def post(route, **body):
        return client.json('/api/workshop/' + route, {'format_version': 1, **body})

    report['stage'] = 'preview'
    request = {'eligible_source': SOURCE}
    plan = post('skill-proposal', request=request)
    descriptor = plan['descriptor']
    report['destination'] = destination = descriptor['destinations'].get('propose', {})
    report['issues'] = descriptor['issues']
    if (report['issues'] or destination.get('kind') != 'openrouter'
            or destination.get('model') != report['model']
            or destination.get('routing', {}).get('routing_data_collection') != 'deny'):
        return 'blocked'
    if descriptor['context'] != {'skill': SOURCE, 'workflows': []}:
        return 'failed'

    report['stage'] = 'proposal'
    ticket = post('tickets')['ticket']
    result = post('skill-proposal', request=request, ticket=ticket, expected_plan=plan['expected_plan'],
                  confirmations=plan['required_confirmations'])
    if result.get('outcome') != 'complete':
        report['outcome'] = result.get('outcome')
        return 'failed'
    package = result['package']
    if client.json('/api/workshop/components')['workspace']:
        return 'failed'
    report['stage'] = 'draft_review'
    plan = post('component-plan', source=json.dumps(package))
    pending = plan['descriptor']
    if pending['kind'] != 'agent' or pending['active'] or package['root']['visibility'] != 'private':
        return 'failed'
    report['draft'] = {'id': pending['id'], 'revision': pending['revision'],
                       'workflow_count': len(package['root']['definition']['workflows'])}
    return 'passed'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True, type=eval_setup.nonempty)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    source = hashlib.sha256()
    for path in sorted((ROOT / 'agentwiz').rglob('*.py')):
        source.update(path.relative_to(ROOT).as_posix().encode() + b'\0' + path.read_bytes())
    source.update(Path(__file__).read_bytes())
    report = {'format_version': 1, 'version': __version__, 'source_digest': source.hexdigest(),
              'model': args.model, 'stage': 'setup',
              'fixture_sha256': hashlib.sha256(SOURCE.encode()).hexdigest(), 'verdict': 'blocked'}
    client = None
    try:
        with tempfile.TemporaryDirectory(prefix='agentwiz-skill-check-') as scratch:
            with eval_setup.session(Path(scratch), 'openrouter-eval', 'openrouter', model=args.model,
                                    routing_data_collection='deny') as ui:
                ui.state.component_pool = Path(scratch) / 'pool'
                client = Client(ui)
                report['verdict'] = check(client, report)
    except (eval_setup.SetupBlocked, Refused, OSError, ValueError, RuntimeError) as exc:
        report['error'] = eval_reports.error(exc, report['stage'])
        if report['stage'] != 'setup':
            report['verdict'] = 'failed'
    eval_reports.checkpoint(args.report, report, client)
    print(f"{report['verdict']}: {args.report}")
    return {'passed': 0, 'failed': 1, 'blocked': 2}[report['verdict']]


if __name__ == '__main__':
    sys.exit(main())
