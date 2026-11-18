#!/usr/bin/env python3
"""Read a full AgentWiz library evaluation and write a small, read-only assessment.

Run from the AgentWiz checkout (Python 3.10+, standard library only):
  python scripts/summarize_library_eval.py reports/library-openrouter.json --repo .
  python scripts/summarize_library_eval.py reports/library-openrouter.json --repo . --include-excerpts

The original report is never modified. No network requests or model calls are made.
Exit 0 means the summary was written, not that qualification passed. Read assessment.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from eval_attempts import attempt_diagnostics


def read(path):
    with Path(path).open(encoding='utf-8') as stream:
        return json.load(stream)


def clipped(value, limit=240):
    text = str(value)
    for key, secret in os.environ.items():
        if len(secret) >= 8 and (key == 'OPENROUTER_API_KEY' or
                (key.startswith('AGENTWIZ_LIVE_') and key.endswith(('_KEY', '_TOKEN')))):
            text = text.replace(secret, '[REDACTED]')
    text = re.sub(r'\bsk-(?:or-v1-)?[A-Za-z0-9_-]{16,}', '[REDACTED]', text)
    return text if len(text) <= limit else text[:limit] + ' [truncated]'


def counts(values):
    return dict(sorted(Counter(clipped(value) for value in values).items()))


def combine(statuses):
    for status in ('failed', 'blocked', 'incomplete', 'rubric_review_pending'):
        if status in statuses:
            return status
    return 'passed' if statuses else 'incomplete'


def catalog(repo):
    try:
        root = Path(repo) / 'agentwiz' / 'library'
        keys = read(root / 'index.json')['keys']
        return [key for key in keys if read(root / key / 'library.json')['group'] == 'llm'], None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [], type(exc).__name__


def assess_block(key, block, report, repo, excerpts=False):
    rows = block.get('runs', [])
    issues = []
    try:
        cases = read(Path(repo) / 'tests' / 'library_eval' / (key + '.json'))['cases']
        ids = [case['id'] for case in cases]
        if len(ids) != 10 or len(set(ids)) != 10:
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        cases = []
        issues.append('Fixture coverage unavailable: ' + type(exc).__name__)
    expected = {(case['id'], trial) for case in cases for trial in (1, 2, 3)}
    observed = [(row.get('case'), row.get('trial')) for row in rows]
    unique = set(observed)
    valid_records = all(isinstance(row.get('case'), str) and type(row.get('trial')) is int for row in rows)
    coverage = bool(expected) and valid_records and len(rows) == 30 and len(unique) == 30 and unique == expected
    if report.get('trials') != 3 or report.get('set') != 'all':
        coverage = False
        issues.append('Requires --trials 3 --set all.')
    if len(unique) != len(rows):
        issues.append('Duplicate case/trial records.')
    critical = {case['id'] for case in cases if case.get('critical')}
    rubric_ids = {case['id'] for case in cases if case.get('rubric')}
    executed = [row for row in rows if row.get('outcome') in ('correct', 'incorrect')]
    correct = sum(row.get('outcome') == 'correct' for row in rows)
    critical_bad = sorted({clipped(row.get('case')) for row in rows
                           if row.get('case') in critical and row.get('outcome') not in ('correct', 'blocked', 'evaluator_error')})
    critical_blocked = sorted({clipped(row.get('case')) for row in rows
                               if row.get('case') in critical and row.get('outcome') in ('blocked', 'evaluator_error')})
    hard_bad = any(row.get('hard_violation') is True for row in rows)
    hard_unknown = any(type(row.get('hard_violation')) is not bool for row in executed)
    rubric_rows = [row for row in rows if row.get('case') in rubric_ids or 'rubric' in row]
    rubric_pending = sum(row.get('owner_mark') is not True and row.get('owner_mark') is not False
                         for row in rubric_rows)
    rubric_failed = sum(row.get('owner_mark') is False for row in rubric_rows)
    blocked = any(row.get('outcome') in ('blocked', 'evaluator_error') for row in rows)
    thresholds = coverage and len(executed) >= 29 and correct >= 24 and not critical_bad and not hard_bad
    if hard_bad or rubric_failed or critical_bad:
        status = 'failed'
    elif blocked:
        status = 'blocked'
    elif not coverage or hard_unknown:
        status = 'incomplete'
    elif not thresholds:
        status = 'failed'
    elif rubric_pending:
        status = 'rubric_review_pending'
    else:
        status = 'passed'
    if hard_unknown:
        issues.append('Completed runs lack explicit hard-invariant results.')
    saved_bar = block.get('bar', {})
    for field, actual in (('contract_executions', len(executed)), ('correct', correct),
                          ('full_qualification', coverage)):
        if field in saved_bar and saved_bar[field] != actual:
            issues.append('Stored bar differs from recomputation: ' + field)
    if block.get('verdict') not in (None, status):
        issues.append('Stored verdict differs from recomputation; inspect diagnostics and candidate fixtures.')
    if issues and status == 'passed':
        status = 'incomplete'
    failures = []
    for case_id in sorted({clipped(row.get('case')) for row in rows}):
        group = [row for row in rows if clipped(row.get('case')) == case_id]
        bad = [row for row in group if row.get('outcome') != 'correct']
        if not bad:
            continue
        failures.append({
            'case': case_id,
            'critical': any(row.get('case') in critical for row in group),
            'outcomes': counts(row.get('outcome') for row in group),
            'failed_trials': [row.get('trial') for row in bad],
            'reasons': counts(row.get('failure_reason') or row.get('outcome') for row in bad),
            'violations': sorted({clipped(v) for row in bad for v in row.get('violations', [])})[:12],
            'error_classes': counts((row.get('model_error') or {}).get('class')
                                    or (row.get('diagnostic') or {}).get('class') or 'none' for row in bad),
            'http_statuses': counts((row.get('model_error') or {}).get('status') for row in bad
                                    if (row.get('model_error') or {}).get('status') is not None),
            'attempt_checks': attempt_checks(bad),
            'attempt_diagnostics': [{'trial': row.get('trial'),
                                     'attempts': row.get('attempt_diagnostics')
                                                 or attempt_diagnostics(row.get('attempts', []))}
                                    for row in bad],
        })
    result = {
        'assessment': status, 'reported_verdict': block.get('verdict'), 'coverage_complete': coverage,
        'recorded_runs': len(rows), 'unique_case_trials': len(unique), 'expected_runs': 30,
        'missing_case_trials': [{'case': clipped(case), 'trial': trial}
                                for case, trial in sorted(expected - unique)],
        'unexpected_case_trials': [{'case': clipped(case), 'trial': trial}
                                   for case, trial in sorted(unique - expected, key=str)],
        'outcomes': counts(row.get('outcome') for row in rows),
        'contract_executions': len(executed), 'correct': correct,
        'critical_cases_failed': critical_bad, 'critical_cases_blocked': critical_blocked,
        'hard_invariant_violation': hard_bad,
        'rubrics': {'total': len(rubric_rows), 'pending': rubric_pending, 'failed': rubric_failed},
        'pending_rubric_cases': sorted({clipped(row.get('case')) for row in rubric_rows
                                       if row.get('owner_mark') is not True and row.get('owner_mark') is not False}),
        'repairs_used': counts(row.get('repairs_used') for row in rows),
        'effective_models': counts(row.get('model') for row in rows if row.get('model')),
        'served_by': counts(row.get('served_by') for row in rows if row.get('served_by')),
        'provider_slugs': counts((row.get('routing') or {}).get('selected_slug') for row in rows
                                 if (row.get('routing') or {}).get('selected_slug')),
        'effective_tasks': counts(json.dumps({field: task[field] for field in
                                  ('max_tokens', 'temperature', 'reasoning', 'reasoning_effort') if field in task},
                                  sort_keys=True) for row in rows for task in [row.get('task') or {}]),
        'required_context': block.get('required_context'),
        'failure_patterns': failures, 'issues': issues,
    }
    if excerpts:
        # At most two failed and two pending rubric outputs per block; never prompts/full records.
        bad = [row for row in rows if row.get('outcome') != 'correct'][:2]
        pending = [row for row in rubric_rows if row.get('owner_mark') is not True and row.get('owner_mark') is not False][:2]
        result['output_excerpts'] = [{'case': clipped(row.get('case')), 'trial': row.get('trial'),
                                     'outcome': row.get('outcome'),
                                     'outputs': clipped(json.dumps(row.get('outputs') or row.get('review_outputs') or {},
                                                                  ensure_ascii=False), 1200)}
                                    for row in bad + pending]
    return result


def attempt_checks(rows):
    """Keep bounded rule/location diagnostics, never raw prompts or model reply bodies."""
    result = []
    for row in rows:
        for attempt in row.get('attempts', []):
            for key in ('check', 'checks', 'validation'):
                checked = attempt.get(key)
                if not isinstance(checked, dict):
                    continue
                for issue in checked.get('issues', [])[:6]:
                    if isinstance(issue, dict):
                        item = {field: clipped(issue[field]) for field in ('location', 'check', 'code', 'message')
                                if field in issue}
                        if item and item not in result:
                            result.append(item)
    return result[:8]


def summarize(report, repo, excerpts=False):
    expected, catalog_error = catalog(repo)
    supplied = report.get('blocks') or {}
    blocks = {key: assess_block(key, value, report, repo, excerpts) for key, value in supplied.items()}
    missing = sorted(set(expected) - set(supplied))
    extra = sorted(set(supplied) - set(expected)) if expected else []
    statuses = [block['assessment'] for block in blocks.values()]
    selected_statuses = list(statuses)
    if report.get('execution') in ('running', 'interrupted'):
        selected_statuses.append('incomplete')
    if report.get('execution') == 'blocked':
        selected_statuses.append('blocked')
    selected_assessment = combine(selected_statuses)
    if missing or catalog_error or extra:
        statuses.append('incomplete')
    if report.get('execution') in ('running', 'interrupted'):
        statuses.append('incomplete')
    if report.get('execution') == 'blocked' or report.get('verdict') == 'blocked':
        statuses.append('blocked')
    overall = combine(statuses)
    reported = report.get('verdict')
    disagreement = reported is not None and reported != selected_assessment
    if disagreement and overall == 'passed':
        overall = 'incomplete'
    target = report.get('target') or {}
    summary = {
        'summary_format': 1, 'assessment': overall, 'selected_blocks_assessment': selected_assessment,
        'catalog_coverage_complete': not missing and not extra and not catalog_error,
        'reported_verdict_disagrees': disagreement,
        'scope': 'Library qualification only; does not assess Chat, CI, installed-wheel or PDF gates.',
        'reported_verdict': report.get('verdict'), 'execution': report.get('execution', 'legacy_unspecified'),
        'reason': clipped(report.get('reason')) if report.get('reason') else None,
        'versions': {key: report.get(key) for key in ('agentwiz_version', 'modelspell_version')},
        'target': {key: clipped(target[key]) for key in ('profile', 'route', 'model', 'provider') if key in target},
        'owner_settings': {key: (clipped(value) if isinstance(value, str) else value) for key, value in (target.get('owner_fill') or {}).items()
                           if key in ('context_length', 'reasoning', 'temperature', 'token_budget')},
        'expected_model_blocks': expected, 'missing_blocks': missing, 'unexpected_blocks': extra,
        'catalog_error': catalog_error, 'block_assessments': counts(block['assessment'] for block in blocks.values()),
        'rules': {'contract_executions_minimum': 29, 'correct_minimum': 24, 'trials': 3,
                  'cases_per_block': 10, 'critical_cases': 'correct on all trials',
                  'hard_invariants': 'no violations', 'rubric_review': 'all required marks true'},
        'blocks': blocks,
        'limitations': ['Failure details and optional outputs are bounded excerpts.',
                        'Pending rubric reviews require semantic review; an objective score cannot replace them.',
                        'Use fixtures/catalog from the same candidate that produced the full report.'],
    }
    if excerpts:
        summary['limitations'].append('Output excerpts may include synthetic source content; inspect before sharing.')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('report', type=Path)
    parser.add_argument('--repo', type=Path, default=Path.cwd(), help='AgentWiz checkout matching the evaluated candidate')
    parser.add_argument('--output', type=Path, help='default: next to input, named <stem>-summary.json')
    parser.add_argument('--include-excerpts', action='store_true', help='include small failed/pending output samples')
    args = parser.parse_args()
    destination = args.output or args.report.with_name(args.report.stem + '-summary.json')
    if destination.resolve() == args.report.resolve():
        parser.error('output must differ from the original report')
    try:
        report = read(args.report)
        if report.get('format_version') != 2 or not isinstance(report.get('blocks'), dict):
            parser.error('expected an AgentWiz format-2 library evaluation report')
        summary = summarize(report, args.repo, args.include_excerpts)
        digest = hashlib.sha256()
        with args.report.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b''):
                digest.update(chunk)
        summary['source_sha256'] = digest.hexdigest()
        summary['source_bytes'] = args.report.stat().st_size
        data = (json.dumps(summary, ensure_ascii=True, indent=2) + '\n').encode()
        if len(data) > 512 * 1024:
            parser.error('summary exceeds 512 KiB; input is outside the expected catalog size')
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=destination.parent, prefix='.summary-')
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        print('Catalog assessment:', summary['assessment'])
        print('Selected blocks:', summary['selected_blocks_assessment'])
        print('Block assessments:', json.dumps(summary['block_assessments']))
        print('Missing blocks:', ', '.join(summary['missing_blocks']) or 'none')
        print('Summary:', destination, '(' + str(len(data)) + ' bytes)')
        return 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        parser.exit(2, 'Cannot summarize report (' + type(exc).__name__ + '). Original report unchanged.\n')


if __name__ == '__main__':
    raise SystemExit(main())
