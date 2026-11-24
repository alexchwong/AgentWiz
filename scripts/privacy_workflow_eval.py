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
REPORT_VERSION = 2
RUN_ERRORS = (eval_setup.SetupBlocked, Refused, OSError, ValueError, RuntimeError, KeyError, TypeError)
TRIAL_ERRORS = RUN_ERRORS + (KeyboardInterrupt,)
STEP_CODES = {'model_error', 'blank_answer', 'check_incomplete', 'contract_error', 'truncated',
              'repairs_exhausted', 'budget_exceeded', 'destination_changed', 'destination_ineligible',
              'destination_untrusted', 'destination_trust_unavailable', 'destination_proxied'}


class ResumeBlocked(eval_setup.SetupBlocked):
    """Existing evidence could not be safely resumed."""
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
    scores = []
    start = case['candidate'].index(target) if target is not None else None
    end = start + len(target) if start is not None else None
    evidence = []
    for row in findings:
        spans = []
        for loc in row['locations']:
            a, b = loc['start'], loc['end']
            if start is None:
                decision = 'unexpected'
            elif a <= start and b >= end:
                decision = 'too_broad' if b - a > len(target) + 24 else 'covers_target'
            else:
                decision = 'partial_overlap' if a < end and b > start else 'outside_target'
            spans.append({'start': a, 'end': b, 'decision': decision})
        if any(span['decision'] == 'covers_target' for span in spans):
            scores.append(row['severity'])
        evidence.append({'category': row['category'], 'severity': row['severity'], 'spans': spans})
    best = max(scores, key=ranks.get, default=None)
    if target is None:
        score = {'false_alarm': bool(findings), 'true_negative': completed and not findings}
        code = 'unexpected_finding' if findings else None
    else:
        rank_ok = best is not None and ranks[best] >= ranks[case['rank']]
        score = {'matched': best is not None, 'rank_ok': rank_ok}
        code = (None if rank_ok else 'severity_below_minimum' if best else
                'no_qualifying_span' if findings else 'no_findings')
    return {'complete': completed, **score, 'grade': {'code': code,
            'target_span': [start, end] if start is not None else None,
            'minimum_severity': case.get('rank'), 'best_matched_severity': best, 'findings': evidence}}


def log(args, event, client=None, **fields):
    # JSON escape control characters in user-configured labels before rendering one line.
    safe = eval_reports.safe(fields, client)
    print(f'[{event}] ' + ' '.join(f'{key}={json.dumps(value, ensure_ascii=True, separators=(",", ":"))}'
                                  for key, value in safe.items()), file=sys.stderr, flush=True)


def log_trial(args, row, client):
    level = getattr(args, 'log_level', 'normal')
    if level == 'summary':
        return
    info = row['diagnostics']
    if info['code'] or level == 'verbose':
        grade_info = info.get('grade', {})
        findings = grade_info.get('findings', [])
        first = findings[0] if findings else None
        fields = {'case': row.get('id', row.get('case')), 'trial': row['trial'], 'stage': info['stage'],
                  'code': info['code'], 'outcome': row['outcome']}
        if 'group' in row:
            fields['group'] = row['group']
        if grade_info:
            fields.update(expected=grade_info['minimum_severity'] or 'no_findings',
                          actual=grade_info['best_matched_severity'], finding_count=len(findings))
            if first:
                fields['first_finding'] = {**first, 'spans': first['spans'][:1]}
        if info.get('issue_codes'):
            fields['issue_codes'] = info['issue_codes']
        if info.get('assessment', {}).get('rejection_codes'):
            fields['rejection_codes'] = info['assessment']['rejection_codes']
        if info.get('failed_checks'):
            fields['failed_checks'] = info['failed_checks']
        if level == 'verbose':
            fields['details'] = info
        log(args, 'FAIL' if info['code'] else 'PASS', client, **fields)


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
            report['setup_issue_codes'] = [item['code'] for item in issues]
            raise eval_setup.SetupBlocked('detector destination is unavailable')
        report['binding'] = {**binding, 'effective': destinations}
        report['detector_digest'] = graph.digest
        report['identity'] = digest({key: report[key] for key in
                                     ('build', 'fixtures_digest', 'policy', 'schedule', 'binding', 'detector_digest')})
        if args.resume:
            try:
                previous = json.loads(args.report.read_text())
                compatible = (previous.get('format_version') == REPORT_VERSION and
                              previous.get('identity') == report['identity'] and
                              isinstance(previous.get('results'), list))
            except (OSError, ValueError, AttributeError) as exc:
                raise ResumeBlocked from exc
            if not compatible:
                raise ResumeBlocked
            report['resume_verified'] = True
            report['results'] = previous['results']
        log(args, 'binding', client, model=binding['model'], routing=binding['routing_mode'],
            configured_provider=binding['provider'] or 'none', serving_provider='unknown',
            resumed=len(report['results']))
        finished = {(row['id'], row['trial']) for row in report['results']}
        for case, trial in schedule:
            if (case['id'], trial) in finished:
                continue
            started = time.monotonic()
            inputs = {'candidate': case['candidate']}
            job = Job(ui.state)
            stage = 'execution'
            rejections = []
            decoded_count = None
            try:
                result = job.execute(graph, inputs)
                assessment = {'status': 'not_run', 'findings': []}
                if result['outcome'] == 'complete':
                    stage = 'decode'
                    decoded = jsonio.decode_exact(result['outputs']['findings']['text'])
                    decoded_count = len(decoded)
                    stage = 'assessment'
                    assessment = privacy.assess(case['candidate'], decoded, rejection_codes=rejections)
                stage = 'grade'
                score = grade(case, assessment)
            except TRIAL_ERRORS as exc:
                report['interrupted_trial'] = {'id': case['id'], 'trial': trial,
                                               **eval_reports.error(exc, stage)}
                eval_reports.checkpoint(args.report, report, client)
                if getattr(args, 'log_level', 'normal') != 'summary':
                    log(args, 'ERROR', client, **report['interrupted_trial'])
                raise
            trace = job.details.get('detect', {})
            issue_codes = [item['code'] for item in (*result['issues'], *trace.get('issues', []))]
            issue_codes.extend(step['failure'] for step in trace.get('steps', []) if step.get('failure') in STEP_CODES)
            grade_info = score.pop('grade')
            if result['outcome'] != 'complete':
                stage, code = 'execution', 'workflow_not_complete'
            elif not score['complete']:
                stage, code = 'assessment', 'assessment_unavailable'
            else:
                stage, code = 'grade', grade_info['code']
            row = {'id': case['id'], 'trial': trial, 'class': case['class'], 'rank': case['rank'],
                'outcome': result['outcome'], 'usage': trace.get('usage'), 'elapsed_ms': round(1000*(time.monotonic()-started)),
                'model_attempts': result.get('model_attempts'), **score,
                'diagnostics': {'stage': stage, 'code': code, 'issue_codes': sorted(set(issue_codes)),
                                'node_outcome': result['nodes'].get('detect', {}).get('outcome'),
                                'serving_provider': None, 'resolved_model': None,
                                'assessment': {'status': assessment['status'], 'decoded_count': decoded_count,
                                               'accepted_count': len(assessment['findings']),
                                               'rejection_codes': rejections}, 'grade': grade_info}}
            report['results'].append(row)
            eval_reports.checkpoint(args.report, report, client)
            log_trial(args, row, client)
            if getattr(args, 'log_level', 'normal') == 'normal' and len(report['results']) % 25 == 0:
                log(args, 'progress', client, finalized=len(report['results']), scheduled=len(schedule))


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
        codes = [row['code'] for row in plan['descriptor']['issues']]
        return {'case': case['id'], 'outcome': 'setup_required', 'checks_passed': False,
                'issue_codes': codes, 'diagnostics': {'stage': 'planning', 'code': 'setup_required',
                                                      'issue_codes': codes}}
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
            failed = []
            if result['outcome'] not in ('complete', 'release_rejected', 'release_expired'):
                failed.append('execution')
            if result['outcome'] in ('release_rejected', 'release_expired') or (cloud and candidate_ok is not True):
                failed.append('release_gate')
            if redaction_grade['covered'] < redaction_grade['targets']:
                failed.append('redaction_coverage')
            if not redaction_grade['preserve']:
                failed.append('preservation')
            if not check:
                failed.append('restoration')
            return {'case': case['id'], 'outcome': result['outcome'],
                    'executable_digest': result.get('executable_digest'), 'candidate_check': candidate_ok, 'redaction': redaction_grade,
                    'checks_passed': check and redaction_grade['preserve'] and (candidate_ok is True or not cloud),
                    'nodes': {key: row['outcome'] for key, row in result.get('nodes', {}).items()},
                    'elapsed_ms': round((time.monotonic() - start) * 1000),
                    'diagnostics': {'stage': failed[0] if failed else 'native',
                                    'code': 'native_check_failed' if failed else None, 'failed_checks': failed,
                                    'issue_codes': [item['code'] for item in result.get('issues', [])]}}
        time.sleep(0.2)
    post(f'workflow-jobs/{ident}/stop')
    return {'case': case['id'], 'outcome': 'timeout', 'checks_passed': False,
            'elapsed_ms': round((time.monotonic() - start) * 1000),
            'diagnostics': {'stage': 'execution', 'code': 'timeout'}}


def record_native(client, args, report, group, trial, *, case=None, cloud=False):
    case_id = case['id'] if case else 'dev-native-spot'
    try:
        row = run_native(client, case=case, cloud=cloud)
    except TRIAL_ERRORS as exc:
        report['interrupted_trial'] = {'id': case_id,
                                       'trial': trial, 'group': group, **eval_reports.error(exc, 'native')}
        eval_reports.checkpoint(args.report, report, client)
        if args.log_level != 'summary':
            log(args, 'ERROR', client, **report['interrupted_trial'])
        raise
    row.update(trial=trial, group=group)
    if group == 'connected_development':
        report[group] = row
    else:
        report.setdefault(group, []).append(row)
    eval_reports.checkpoint(args.report, report, client)
    log_trial(args, row, client)


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
    parser.add_argument('--log-level', choices=('summary', 'normal', 'verbose'), default='normal')
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
    report = {'format_version': REPORT_VERSION, 'build': {'version': __version__, 'source_digest': hasher.hexdigest()},
              'policy': POLICY, 'backend': args.backend, 'set': args.set, 'fixtures_digest': digest([cohort, redaction_cases()]),
              'schedule': [[case['id'], trial] for case, trial in schedule], 'results': [],
              'verdict': 'incomplete', 'qualification': 'unqualified'}
    log(args, 'run', backend=args.backend, set=args.set, scheduled=len(schedule) if args.backend != 'offline' else 2,
        resume=args.resume)
    client = None
    if args.backend == 'offline':
        outcome = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests/test_private_summary_025.py'],
                                 cwd=ROOT, check=False)
        report['schedule'] = ['native_workshop_integration', 'grader_negative_controls']
        report['offline'] = {'integration_exit': outcome.returncode, 'grader_controls': grader_controls()}
        report['verdict'] = 'passed' if outcome.returncode == 0 and report['offline']['grader_controls'] else 'failed'
        log(args, 'offline', **report['offline'])
    else:
        mode = args.backend + '-eval'
        stage = 'setup'
        try:
            with tempfile.TemporaryDirectory(prefix='agentwiz-privacy-eval-') as folder:
                with eval_setup.session(Path(folder), mode, args.backend, args.profiles_dir, args.config,
                                        **target_options) as ui:
                    client = Client(ui)
                    run_detector(ui, client, args, schedule, report)
                    if args.backend == 'lmstudio':
                        stage = 'native'
                        for trial in range(1, 4 if args.qualify else 2):
                            record_native(client, args, report, 'native_local', trial)
                        if cloud_profile:
                            stage = 'connected_setup'
                            select_cloud_summary(client, cloud_profile)
                            stage = 'native'
                            record_native(client, args, report, 'connected_development', 1, cloud=True)
                        if args.qualify:
                            expected = redaction_cases()
                            report['redaction_schedule'] = [row['id'] for row in expected]
                            for case in expected:
                                record_native(client, args, report, 'native_heldout', 1, case=case)
        except RUN_ERRORS as exc:
            if args.resume and not report.get('resume_verified'):
                log(args, 'BLOCKED', client, stage='resume', code='resume_not_verified', error_class=type(exc).__name__)
                print(f"incomplete: {args.report}")
                return 2
            error_stage = report.get('interrupted_trial', {}).get('stage', stage)
            report['blocked'] = eval_reports.error(exc, error_stage)
            if 'interrupted_trial' not in report or args.log_level == 'summary':
                interrupted = report.get('interrupted_trial', {})
                log(args, 'BLOCKED', client, stage=error_stage, error_class=type(exc).__name__,
                    **{key: interrupted[key] for key in ('id', 'trial', 'group') if key in interrupted},
                    issue_codes=report.get('setup_issue_codes', []))
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
    eval_reports.checkpoint(args.report, report, client)
    if args.backend != 'offline':
        rows = report['results']
        log(args, 'done', client, workflow_completed=sum(row['outcome'] == 'complete' for row in rows),
            assessed=sum(row['complete'] for row in rows), scheduled=len(schedule),
            grading_failures=sum(row['diagnostics']['stage'] == 'grade' and row['diagnostics']['code'] is not None
                                 for row in rows), pending=report['pending'], verdict=report['verdict'],
            qualification=report['qualification'], remaining_evidence=report['remaining_evidence'])
    print(f"{report['verdict']}: {args.report}")
    return {'passed': 0, 'failed': 1, 'incomplete': 2}[report['verdict']]


if __name__ == '__main__':
    sys.exit(main())
