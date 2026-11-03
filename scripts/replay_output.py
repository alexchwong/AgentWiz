#!/usr/bin/env python3
"""Replay a deliberately selected recovery against the unchanged acceptance checker.

Fixture format has contract_text, raw_answer, truncated and redacted. A selected
span or a replacement file describes a proposed recovery, never production policy.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from agentwiz import checks, jsonio


def replay(evidence, start=None, end=None, replacement=None):
    if evidence.get('redacted') or not isinstance(evidence.get('raw_answer'), str) or not isinstance(evidence.get('contract_text'), str):
        return {'status': 'unassessable', 'reason': 'incomplete_or_redacted'}
    contract = checks.contract_from_text(evidence['contract_text'])
    answer = evidence['raw_answer']
    if replacement is not None:
        proposed, transformation = replacement, 'explicit_replacement'
        discarded = answer
    else:
        first, last = (0 if start is None else start), (len(answer) if end is None else end)
        if not 0 <= first <= last <= len(answer):
            raise ValueError('span is outside the answer')
        proposed, transformation = answer[first:last], 'explicit_span'
        discarded = answer[:first] + answer[last:]
    old, _ = checks.check_answer_value(contract, answer)
    new, value = checks.check_answer_value(contract, proposed)
    truncated = bool(evidence.get('truncated'))
    return {'status': 'assessed', 'raw_answer': answer, 'transformation': transformation,
            'span': [start, end], 'proposed_answer': proposed, 'discarded_text': discarded,
            'recovered_value_text': jsonio.encode_exact(value) if value is not checks.NO_VALUE else None,
            'original_check': old, 'proposed_check': new, 'truncated': truncated,
            'original_accepted': old['overall'] == 'pass' and not truncated,
            'proposed_accepted': new['overall'] == 'pass' and not truncated}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--fixture', type=Path)
    source.add_argument('--attempt', help='saved attempt ID, with --workspace')
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--start', type=int)
    parser.add_argument('--end', type=int)
    parser.add_argument('--replacement', type=Path)
    args = parser.parse_args()
    if args.fixture:
        evidence = jsonio.decode_exact(args.fixture.read_text())
    else:
        if not args.workspace:
            parser.error('--attempt requires --workspace')
        evidence = None
        for path in (args.workspace / 'runs').glob('*/*.json'):
            if path.is_symlink() or path.stat().st_size > 32 << 20:
                continue
            record = jsonio.decode_exact(path.read_text())
            if record.get('id') == args.attempt:
                evidence = {'raw_answer': (record.get('result') or {}).get('text'),
                            'contract_text': (record.get('contract') or {}).get('text'),
                            'truncated': str(record.get('outcome', '')).startswith('truncated'),
                            'redacted': record.get('redacted', False)}
                break
        if evidence is None:
            evidence = {}
    result = replay(evidence, args.start, args.end, args.replacement.read_text() if args.replacement else None)
    print(jsonio.encode_exact(result))


if __name__ == '__main__':
    main()
