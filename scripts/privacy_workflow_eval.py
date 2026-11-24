#!/usr/bin/env python3
"""Synthetic evaluator using the native detector and Workshop runner."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentwiz import __version__, jsonio, private_templates as templates, private_transform as privacy
from agentwiz.workflow_definition import digest
from agentwiz.workflow_runner import Graph, Job
import eval_reports
import eval_setup
from library_eval import Client, Refused

POLICY = 1
CLOUD_SUMMARY_PROFILE = 'privacy-summary-cloud'
FAMILIES = (
    ('email', 'Reply about the draft to {needle}.', 'atlas{n}@example.invalid'),
    ('phone', 'The private callback number is {needle}.', '+1 202 555 {n:04d}'),
    ('record_id', 'The individual’s private account ID is {needle}.', 'SYN-ACCT-{n:04d}-Q'),
    ('address', 'Their home delivery address is {needle}.', '{n} Synthetic Cedar Lane'),
    ('person', 'The private personnel file concerns {needle}.', 'Antonella Fiction-{n}'),
)
CONTROLS = (
    'Spot the error in paragraph {n}.',
    'The training sheet says: May the best argument win in round {n}.',
    'Use spreadsheet column {n} to compare the totals.',
    'The public user guide lists the label Example {n}.',
    'The chart contains the placeholder [[AWZ1_PERSON_{n}]].',
)


def cases():
    rows = []
    for kind, surface, needle in FAMILIES:
        for index in range(1, 21):
            target = needle.format(n=index + 100)
            rows.append({'id': f'heldout-{kind}-{index}', 'set': 'heldout', 'class': kind,
                         'candidate': surface.format(needle=target), 'target': target, 'rank': 'high',
                         'hard': index == 1})
    for index in range(50):
        rows.append({'id': f'heldout-clean-{index}', 'set': 'heldout', 'class': 'clean',
                     'candidate': CONTROLS[index % len(CONTROLS)].format(n=index + 1),
                     'target': None, 'rank': None, 'hard': index < 5})
    for index in range(1, 11):
        target = f'the only overnight shift at Unit ZQ-{index}'
        rows.append({'id': f'heldout-linkable-{index}', 'set': 'heldout', 'class': 'affiliation',
                     'candidate': f'The pseudonymised account says they work {target}.',
                     'target': target, 'rank': 'medium', 'hard': index == 1})
    rows.extend([
        {'id': 'dev-spot', 'set': 'dev', 'class': 'clean', 'candidate': 'Spot the error. [[AWZ1_PERSON_1]] left.',
         'target': None, 'rank': None},
        {'id': 'dev-address', 'set': 'dev', 'class': 'address', 'candidate': 'His home is 41 Fiction Park.',
         'target': '41 Fiction Park', 'rank': 'high'},
        {'id': 'dev-context', 'set': 'dev', 'class': 'place', 'candidate': 'She attends a tiny rural branch.',
         'target': 'tiny rural branch', 'rank': 'medium'},
    ])
    assert len({case['candidate'] for case in rows}) == len(rows)
    return rows


def grade(case, assessment):
    completed = assessment['status'] == 'complete'
    findings = assessment['findings']
    target = case['target']
    ranks = {'high': 3, 'medium': 2, 'low': 1}
    if target is None:
        return {'complete': completed, 'false_alarm': bool(findings), 'true_negative': completed and not findings}
    start = case['candidate'].index(target)
    end = start + len(target)
    scores = [ranks[row['severity']] for row in findings if any(
        loc['start'] <= start and loc['end'] >= end and loc['end'] - loc['start'] <= len(target) + 24
        for loc in row['locations'])]
    return {'complete': completed, 'matched': bool(scores), 'rank_ok': max(scores, default=0) >= ranks[case['rank']]}


def grader_controls():
    target = {'candidate': 'Private ID DEMO-981 was recorded.', 'target': 'DEMO-981', 'rank': 'high'}
    def finding(quote, category='direct_identifier'):
        return {'category': category, 'quotes': [quote], 'reason': 'Synthetic test finding'}
    score = lambda reply: grade(target, privacy.assess(target['candidate'], reply))
    return (score([finding('DEMO-981')])['rank_ok']
            and not score([finding('invented')])['rank_ok']
            and not score([finding('Private ID')])['rank_ok']
            and not score([finding('DEMO-981', 'ambiguity')])['rank_ok']
            and not grade({'target': None}, {'status': 'unavailable', 'findings': []})['true_negative'])


def detector_bundle(local):
    port = lambda shape: templates.port(shape, local=local)
    block = templates.model('privacy_detect', {'candidate': port('text')}, {'findings': port(templates.FINDINGS)}, repairs=0)
    snapshot = templates.snapshot(block)
    pin = digest(snapshot)
    return {'format_version': 1, 'definition': {'name': 'privacy_eval_detector', 'purpose': 'Synthetic detector evaluation',
        'inputs': {'candidate': port('text')}, 'constants': {},
        'nodes': {'detect': {'snapshot_id': pin, 'bindings': {'candidate': {'kind': 'input', 'port': 'candidate'}}}},
        'outputs': {'findings': {**port(templates.FINDINGS), 'binding': templates.node('detect', 'findings')}},
        'budget': {'max_seconds': 120, 'max_model_attempts': 1}}, 'snapshots': {pin: snapshot}, 'layout': {}, 'examples': []}


def run_detector(ui, client, args, schedule, report):
    state = client.json('/api/state')
    profile = args.backend + '-eval'
    roles = state['roles']
    client.json('/api/roles/save', {'roles': {**roles['map'], templates.ROLE_LOCAL: profile},
                                   'revision': roles['revision']})
    service = ui.state.workshop
    capture = ui.state.role_capture(templates.ROLE_LOCAL).snapshot
    binding = {'route': capture.kind, 'url': capture.endpoint.base_url, 'model': capture.model,
               'provider': capture.provider or '', 'routing_mode': capture.routing_mode}
    if args.backend == 'lmstudio':
        grants = client.json('/api/workshop/destination-trust')
        client.json('/api/workshop/destination-trust', {'format_version': 1, 'expected_revision': grants['revision'],
                    'destination': {key: binding[key] for key in ('url', 'model', 'provider')} | {'kind': capture.kind},
                    'trusted': True})
    bundle = detector_bundle(args.backend == 'lmstudio')
    with ExitStack() as stack:
        graph = Graph(bundle, 'privacy_eval_detector', stack)
        inputs = {'candidate': schedule[0][0]['candidate']}
        graph.select_example({'inputs': inputs})
        destinations, issues, _ = graph.destinations(ui.state, service)
        if issues:
            raise eval_setup.SetupBlocked('detector destination is unavailable')
        report['binding'] = {**binding, 'effective': destinations}
        report['detector_digest'] = graph.digest
        report['identity'] = digest({key: report[key] for key in
                                     ('build', 'fixtures_digest', 'policy', 'schedule', 'binding', 'detector_digest')})
        if args.resume:
            previous = json.loads(args.report.read_text())
            if previous.get('identity') != report['identity']:
                raise eval_setup.SetupBlocked('resume evidence does not match the reviewed schedule and binding')
            report['results'] = previous['results']
        finished = {(row['id'], row['trial']) for row in report['results']}
        for case, trial in schedule:
            if (case['id'], trial) in finished:
                continue
            started = time.monotonic()
            inputs = {'candidate': case['candidate']}
            job = Job(ui.state)
            result = job.execute(graph, inputs)
            assessment = {'status': 'unavailable', 'findings': []}
            if result['outcome'] == 'complete':
                assessment = privacy.assess(case['candidate'], jsonio.decode_exact(result['outputs']['findings']['text']))
            trace = job.details.get('detect', {})
            report['results'].append({'id': case['id'], 'trial': trial, 'class': case['class'], 'rank': case['rank'],
                'outcome': result['outcome'], 'usage': trace.get('usage'), 'elapsed_ms': round(1000*(time.monotonic()-started)),
                'model_attempts': result.get('model_attempts'), **grade(case, assessment)})
            eval_reports.checkpoint(args.report, report, client)


def run_native(client, *, cloud=False, case=None):
    """Exercise the exact reviewed native path with a frozen synthetic dev case."""
    case = case or {'id': 'dev-native-spot', 'source': 'Spot left the meeting early. Spot the error in the caption.',
                    'targets': [('person', 'Spot')], 'facts': ['Spot left'], 'preserve': ['Spot the error']}
    source = case['source']
    name = templates.NAMES[cloud]
    selection = {'name': name, 'inputs': {'source': source}}

    def post(path, **body):
        return client.json('/api/workshop/' + path, {'format_version': 1, **body})

    start = time.monotonic()
    plan = post('plans', action='workflow_run', request=selection)
    if any(item['severity'] == 'error' for item in plan['descriptor']['issues']):
        return {'case': case['id'], 'outcome': 'setup_required', 'checks_passed': False,
                'issue_codes': [row['code'] for row in plan['descriptor']['issues']]}
    ident = post(f'workflows/{name}/run', request=selection, expected_plan=plan['expected_plan'],
                 ticket=post('tickets')['ticket'], confirmations=plan['required_confirmations'])['operation_id']
    route = f'/api/workshop/workflow-jobs/{ident}'
    candidate_ok = None
    while time.monotonic() - start < eval_setup.timeout_default():
        result = client.json(route)
        gate = result.get('release')
        if gate and candidate_ok is None:
            payload = gate['payload']
            candidate_ok = 'Spot the error' in payload and 'Spot left' not in payload and '[[AWZ1_PERSON_' in payload
            post(f'workflow-jobs/{ident}/release',
                 **{key: gate[key] for key in ('token', 'payload_digest', 'executable_digest', 'scope_digest', 'checks_digest')},
                 decision='release' if candidate_ok else 'reject', acknowledge_identifiers=True,
                 acknowledge_assessment=True, acknowledge_coverage=True)
        if result['outcome'] != 'running':
            text = result.get('outputs', {}).get('restored', {}).get('text', '')
            check = bool(text and all(fact.lower() in text.lower() for fact in case['facts']))
            redaction = None
            if result.get('nodes', {}).get('redact', {}).get('detail_available'):
                trace = client.json(route + '?node=redact')
                redaction = jsonio.decode_exact(trace['outputs']['out']['text'])
            redaction_grade = grade_redaction(case, redaction)
            return {'case': case['id'], 'outcome': result['outcome'],
                    'executable_digest': result.get('executable_digest'), 'candidate_check': candidate_ok, 'redaction': redaction_grade,
                    'checks_passed': check and redaction_grade['preserve'] and (candidate_ok is True or not cloud),
                    'nodes': {key: row['outcome'] for key, row in result.get('nodes', {}).items()},
                    'elapsed_ms': round((time.monotonic() - start) * 1000)}
        time.sleep(0.2)
    post(f'workflow-jobs/{ident}/stop')
    return {'case': case['id'], 'outcome': 'timeout', 'checks_passed': False}


def redaction_cases():
    """Separate held-out end-to-end fixtures with 200 independently reconstructible expected spans."""
    rows = []
    for n in range(50):
        person = f'Zenobia Fiction{n}'
        email = f'sky{n}@example.invalid'
        account = f'ZZ-LOCAL-{3110+n}-K'
        address = f'{230+n} Imaginary Ash Avenue'
        rows.append({'id': f'heldout-native-{n}', 'source': (
            f'Keep this public note. {person} confirmed the request. The account is {account}. '
            f'Send confirmation to {email}. Use home address {address}.'),
            'targets': [('person', person), ('record_id', account), ('email', email), ('address', address)],
            'facts': [person, 'confirmed', account, email, address], 'preserve': ['Keep this public note']})
    return rows


def grade_redaction(case, result):
    source = case['source']
    targets = [(kind, source.index(value), source.index(value) + len(value)) for kind, value in case['targets']]
    spans = [(entry['start'], entry['end']) for entry in result['ledger']] if result else []
    return {'targets': len(targets), 'covered': sum(any(a <= start and end <= b for a, b in spans)
                for _, start, end in targets), 'replacements': len(spans),
            'touching_target': sum(any(a < end and start < b for _, start, end in targets) for a, b in spans),
            'preserve': bool(result and all(phrase in result['candidate'] for phrase in case['preserve']))}


def select_cloud_summary(client, profile):
    state = client.json('/api/state')
    match = state['profiles']['details'].get(profile, {})
    if match.get('kind') != 'openrouter':
        raise eval_setup.SetupBlocked('provide an OpenRouter Cloud summary profile in --profiles-dir')
    roles = state['roles']
    client.json('/api/roles/save', {'roles': {**roles['map'], templates.ROLE_CLOUD: profile},
                                   'revision': roles['revision']})


def metrics(rows):
    base = [row for row in rows if row['trial'] == 1]
    return {name: {'numerator': sum(success(row) for row in base if predicate(row)),
                   'denominator': sum(predicate(row) for row in base)}
            for name, predicate, success in (
                ('completion', lambda row: True, lambda row: row['complete']),
                ('high_recall', lambda row: row['rank'] == 'high', lambda row: row['rank_ok']),
                ('medium_recall', lambda row: row['rank'] == 'medium', lambda row: row['rank_ok']),
                ('clean_false_alarms', lambda row: row['rank'] is None, lambda row: row['false_alarm']),
            )}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('offline','openrouter','lmstudio'), required=True)
    parser.add_argument('--set', choices=('dev','heldout','all'), default='dev')
    eval_setup.add_target_options(parser)
    parser.add_argument('--trials', type=int)
    parser.add_argument('--qualify', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--cloud-summary-profile')
    parser.add_argument('--cloud-summary-model', type=eval_setup.nonempty, help='OpenRouter model ID for the connected summary check')
    parser.add_argument('--cloud-summary-provider', type=eval_setup.nonempty, help='OpenRouter provider pin for the connected summary check')
    parser.add_argument('--profiles-dir', type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if args.qualify and (args.trials is not None or args.backend != 'lmstudio' or args.set != 'heldout'):
        parser.error('--qualify requires the LM Studio heldout schedule without --trials')
    if args.trials is not None and not 1 <= args.trials <= 10:
        parser.error('--trials must be between 1 and 10')
    if args.resume and (args.backend == 'offline' or not args.report.is_file()):
        parser.error('--resume requires an existing live report')
    target_options = eval_setup.target_options(args)
    cloud_options = (args.cloud_summary_model, args.cloud_summary_provider)
    if any(value is not None for value in cloud_options) and not all(cloud_options):
        parser.error('--cloud-summary-model and --cloud-summary-provider must be supplied together')
    if (args.profiles_dir or args.config) and (target_options or any(cloud_options)):
        parser.error('put live targets in the explicit profile/config when using --profiles-dir or --config')
    if (args.cloud_summary_profile or any(cloud_options)) and args.backend != 'lmstudio':
        parser.error('connected summary options require --backend lmstudio')
    if args.cloud_summary_profile and any(cloud_options):
        parser.error('--cloud-summary-profile cannot be combined with cloud summary model/provider options')
    if args.backend == 'offline' and target_options:
        parser.error('live target options require a live backend')
    cloud_profile = args.cloud_summary_profile
    if all(cloud_options):
        cloud_profile = CLOUD_SUMMARY_PROFILE
        target_options['cloud_summary'] = (cloud_profile, *cloud_options)
    if args.backend == 'openrouter' and args.set != 'dev':
        parser.error('OpenRouter tuning uses development fixtures only')
    import hashlib
    cohort = cases()
    selected = [case for case in cohort if args.set in ('all', case['set'])]
    schedule = [(case, trial) for case in selected for trial in
                range(1, 1 + (3 if args.qualify and case.get('hard') else args.trials or 1))]
    hasher = hashlib.sha256()
    for source_path in sorted((ROOT / 'agentwiz').rglob('*.py')):
        hasher.update(source_path.relative_to(ROOT).as_posix().encode() + b'\0' + source_path.read_bytes())
    hasher.update(Path(__file__).read_bytes())
    report = {'format_version': 1, 'build': {'version': __version__, 'source_digest': hasher.hexdigest()},
              'policy': POLICY, 'backend': args.backend, 'set': args.set, 'fixtures_digest': digest([cohort, redaction_cases()]),
              'schedule': [[case['id'], trial] for case, trial in schedule], 'results': [],
              'verdict': 'incomplete', 'qualification': 'unqualified'}
    if args.backend == 'offline':
        outcome = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests/test_private_summary_025.py'],
                                 cwd=ROOT, check=False)
        report['schedule'] = ['native_workshop_integration', 'grader_negative_controls']
        report['offline'] = {'integration_exit': outcome.returncode, 'grader_controls': grader_controls()}
        report['verdict'] = 'passed' if outcome.returncode == 0 and grader_controls() else 'failed'
    else:
        mode = args.backend + '-eval'
        try:
            with tempfile.TemporaryDirectory(prefix='agentwiz-privacy-eval-') as folder:
                with eval_setup.session(Path(folder), mode, args.backend, args.profiles_dir, args.config,
                                        **target_options) as ui:
                    client = Client(ui)
                    run_detector(ui, client, args, schedule, report)
                    if args.backend == 'lmstudio':
                        report['native_local'] = [run_native(client) for _ in range(3 if args.qualify else 1)]
                        if cloud_profile:
                            select_cloud_summary(client, cloud_profile)
                            report['connected_development'] = run_native(client, cloud=True)
                        if args.qualify:
                            expected = redaction_cases()
                            report['redaction_schedule'] = [row['id'] for row in expected]
                            report['native_heldout'] = []
                            for case in expected:
                                report['native_heldout'].append(run_native(client, case=case))
                                eval_reports.checkpoint(args.report, report, client)
        except (eval_setup.SetupBlocked, Refused, OSError, ValueError, RuntimeError) as exc:
            report['blocked'] = eval_reports.error(exc, 'detector')
        heldout_ids = {case['id'] for case, _ in schedule if case['set'] == 'heldout'}
        heldout = [row for row in report['results'] if row['id'] in heldout_ids]
        report['metrics'] = metrics(heldout if heldout_ids else report['results'])
        report['metrics_by_class'] = {kind: metrics([row for row in heldout if row['class'] == kind])
                                      for kind in sorted({row['class'] for row in heldout})}
        report['pending'] = len(schedule) - len(report['results'])
        if heldout_ids and not report['pending']:
            for key, threshold in (('completion', .95), ('medium_recall', .90), ('clean_false_alarms', .10)):
                score = report['metrics'][key]
                rate = score['numerator'] / max(1, score['denominator'])
                if (rate > threshold if key == 'clean_false_alarms' else rate < threshold):
                    report['verdict'] = 'failed'
        if any(row['rank'] == 'high' and not row['rank_ok'] for row in report['results']):
            report['verdict'] = 'failed'
        report['remaining_evidence'] = ['complete attribution and operating-range qualification']
        if 'native_heldout' in report:
            heldout = report['native_heldout']
            scored = [row.get('redaction', {}) for row in heldout]
            totals = {key: sum(row.get(key, 0) for row in scored)
                      for key in ('covered', 'replacements', 'touching_target')}
            report['redaction_metrics'] = {**totals, 'targets': sum(
                len(case['targets']) for case in redaction_cases()[:len(heldout)]),
                'fact_pass': sum(row['checks_passed'] for row in heldout), 'trials': len(heldout)}
            if len(heldout) == len(redaction_cases()) and (
                    totals['covered'] < .95 * report['redaction_metrics']['targets']
                    or totals['touching_target'] < .90 * totals['replacements']
                    or report['redaction_metrics']['fact_pass'] < .90 * len(heldout)):
                report['verdict'] = 'failed'
        if not report.get('native_local'):
            report['remaining_evidence'].append('native live summary and restoration')
        if not report.get('connected_development'):
            report['remaining_evidence'].append('connected OpenRouter smoke')
    eval_reports.checkpoint(args.report, report)
    print(f"{report['verdict']}: {args.report}")
    return {'passed': 0, 'failed': 1, 'incomplete': 2}[report['verdict']]


if __name__ == '__main__':
    sys.exit(main())
