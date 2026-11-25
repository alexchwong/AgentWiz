"""Synthetic evaluator verdict controls, with no model or workflow execution."""
from argparse import Namespace
import json
from pathlib import Path
import sys
from contextlib import contextmanager

import pytest


@pytest.mark.parametrize(('scenario', 'expected'), [
    ('complete', 'passed'), ('missing', 'incomplete'), ('duplicate', 'incomplete'),
    ('blocked', 'incomplete'), ('control_failed', 'failed'), ('grade_failed', 'failed'),
])
def test_detector_verdict_requires_the_exact_verified_schedule(monkeypatch, scenario, expected):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    import privacy_workflow_eval as evaluator

    def row(ident, code=None):
        return {'id': ident, 'trial': 1, 'rank': None, 'complete': True, 'diagnostics': {'code': code}}

    report = {'schedule': [['a', 1], ['b', 1]], 'results': [row('a'), row('b')], 'grader_controls': True}
    if scenario == 'missing':
        report['results'].pop()
    elif scenario == 'duplicate':
        report['results'][1] = row('a')
    elif scenario == 'blocked':
        report['blocked'] = {'stage': 'execution'}
    elif scenario == 'control_failed':
        report['grader_controls'] = False
    elif scenario == 'grade_failed':
        report['results'][1] = row('b', 'unexpected_finding')
    assert evaluator.detector_verdict(report, heldout=False) == expected


def test_completed_live_detector_does_not_qualify_workflow(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    import privacy_workflow_eval as evaluator

    @contextmanager
    def session(*args, **kwargs):
        yield None

    def detector(ui, client, args, schedule, report):
        report['results'] = [
            {'id': case['id'], 'trial': trial, 'class': case['class'], 'rank': case['rank'],
             'rank_ok': True, 'false_alarm': False, 'complete': True, 'outcome': 'complete',
             'diagnostics': {'stage': 'grade', 'code': None}}
            for case, trial in schedule
        ]

    destination = tmp_path / 'report.json'
    monkeypatch.setattr(evaluator.eval_setup, 'session', session)
    monkeypatch.setattr(evaluator, 'Client', lambda ui: Namespace(ui=None))
    monkeypatch.setattr(evaluator, 'run_detector', detector)
    monkeypatch.setattr(sys, 'argv', ['privacy_workflow_eval.py', '--backend', 'openrouter',
                                      '--report', str(destination)])
    assert evaluator.main() == 2
    report = json.loads(destination.read_text())
    assert report['detector_verdict'] == 'passed'
    assert report['verdict'] == 'incomplete'
    assert report['qualification'] == 'unqualified'
