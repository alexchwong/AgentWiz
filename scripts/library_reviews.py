"""Small explicit owner-review bundles; no model calls and no automatic approvals."""
from __future__ import annotations

import hashlib
import copy
import json
from pathlib import Path

import eval_reports


class ReviewError(ValueError):
    pass


def source(path):
    data = Path(path).read_bytes()
    report = json.loads(data)
    if not isinstance(report, dict) or report.get('format_version') != 2 or not isinstance(report.get('blocks'), dict) or not report['blocks']:
        raise ReviewError('review needs a format-2 report with block results')
    return report, hashlib.sha256(data).hexdigest()


def bundle(report, digest):
    cases = []
    for key, block in report['blocks'].items():
        groups = {}
        for row in block.get('runs', []):
            if 'rubric' in row:
                groups.setdefault(row['case'], []).append(row)
        for case, rows in sorted(groups.items()):
            cases.append({'block': key, 'case': case, 'owner_mark': None,
                          'inputs': rows[0].get('inputs'), 'rubric': rows[0]['rubric'],
                          'trials': [{'trial': row['trial'], 'outcome': row['outcome'],
                                      'existing_owner_mark': row.get('owner_mark'), 'owner_mark': None,
                                      'outputs': row.get('review_outputs')} for row in rows]})
    return {'review_format': 1, 'source_sha256': digest,
            'instructions': 'Review EVERY displayed trial before setting a case owner_mark to true or false. '
                            'One explicit decision applies to all its displayed trials. Leave null to keep existing marks. '
                            'Use a trial owner_mark to override a case decision for that trial. Edit only owner_mark fields; all evidence is checked on import.',
            'cases': cases}


def export_review(source_path, destination):
    if Path(source_path).resolve() == Path(destination).resolve():
        raise ReviewError('review output must differ from the source report')
    report, digest = source(source_path)
    review = bundle(report, digest)
    eval_reports.checkpoint(destination, review)
    return len(review['cases'])


def apply_review(review_path, source_path):
    if Path(source_path).resolve() == Path(review_path).resolve():
        raise ReviewError('review file must differ from the source report')
    report, digest = source(source_path)
    review = json.loads(Path(review_path).read_text(encoding='utf-8'))
    expected = bundle(report, digest)
    if (not isinstance(review, dict) or review.get('review_format') != 1 or review.get('source_sha256') != digest
            or set(review) != set(expected) or review.get('instructions') != expected['instructions']):
        raise ReviewError('review is stale or its metadata changed; export a fresh review file')
    entries = review.get('cases')
    if not isinstance(entries, list) or len(entries) != len(expected['cases']):
        raise ReviewError('review case coverage changed')
    checked = {}
    seen = set()
    lookup = {(case['block'], case['case']): case for case in expected['cases']}
    for case in entries:
        if (not isinstance(case, dict) or not isinstance(case.get('block'), str)
                or not isinstance(case.get('case'), str) or not isinstance(case.get('trials'), list)
                or any(not isinstance(trial, dict) for trial in case['trials'])):
            raise ReviewError('invalid review entry')
        identity = (case.get('block'), case.get('case'))
        original = lookup.get(identity)
        if original is None or identity in seen:
            raise ReviewError('unknown or duplicate review case')
        seen.add(identity)
        mark = case.get('owner_mark')
        if mark is not None and type(mark) is not bool:
            raise ReviewError('owner_mark must be true, false or null')
        evidence = copy.deepcopy(case)
        evidence['owner_mark'] = None
        for trial in evidence.get('trials', []):
            trial_mark = trial.get('owner_mark')
            if trial_mark is not None and type(trial_mark) is not bool:
                raise ReviewError('trial owner_mark must be true, false or null')
            trial['owner_mark'] = None
        if evidence != original:
            raise ReviewError('review evidence changed; edit only owner_mark')
        for trial, original_trial in zip(case['trials'], original['trials']):
            effective = trial['owner_mark'] if trial['owner_mark'] is not None else mark
            if effective is True and (original_trial['outcome'] not in ('correct', 'incorrect')
                                      or not original_trial['outputs']):
                raise ReviewError('cannot approve a trial without completed review outputs')
            checked[(identity[0], identity[1], trial['trial'])] = effective
    changed = 0
    for key, block in report['blocks'].items():
        for row in block.get('runs', []):
            mark = checked.get((key, row['case'], row['trial']))
            if 'rubric' in row and mark is not None:
                row['owner_mark'] = mark
                changed += 1
    # Caller refreshes the existing qualification verdict before publishing. No report is written here.
    return report, digest, changed


def publish_review(source_path, report, expected_digest):
    if hashlib.sha256(Path(source_path).read_bytes()).hexdigest() != expected_digest:
        raise ReviewError('source report changed while importing review; no marks written')
    eval_reports.checkpoint(source_path, report)
