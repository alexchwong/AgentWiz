"""Compact, content-free attempt diagnostics shared by live reports and offline summaries."""
from __future__ import annotations


def attempt_diagnostics(records):
    """Keep blank/truncated distinctions and token evidence, without prompt or reasoning text."""
    diagnostics = []
    for number, record in enumerate(records, 1):
        result = record.get('result') or {}
        usage = result.get('usage') or {}
        task = record.get('task') or {}
        text, reasoning = result.get('text'), result.get('reasoning')
        diagnostics.append({
            'attempt': number,
            'outcome': record.get('outcome'),
            'check_skipped': record.get('check_skipped'),
            'finish_reason': result.get('finish_reason'),
            'truncated': result.get('truncated'),
            'answer_characters': len(text) if isinstance(text, str) else None,
            'reasoning_characters': len(reasoning) if isinstance(reasoning, str) else None,
            'usage': {key: usage.get(key) for key in
                      ('input_tokens', 'output_tokens', 'reasoning_tokens', 'total_tokens')},
            'task': {key: task[key] for key in ('max_tokens', 'reasoning', 'temperature') if key in task},
        })
    return diagnostics
