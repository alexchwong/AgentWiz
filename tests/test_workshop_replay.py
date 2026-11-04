"""Offline replay preserves exact recovered values and the truncation gate."""
import subprocess

from agentwiz import jsonio


def test_replay_cli_exact_values_discarded_evidence_and_truncation(tmp_path):
    import sys
    from pathlib import Path
    contract = {'format': 'json', 'checks': []}
    answer = 'Example {"id":1} followed by {"id":9007199254740993,"ratio":0.10000000000000000001}'
    fixture = tmp_path / 'fixture.json'
    fixture.write_text(jsonio.encode_exact({'contract_text': jsonio.encode_exact(contract), 'raw_answer': answer, 'truncated': True, 'redacted': False}))
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'replay_output.py'
    result = subprocess.run([sys.executable, str(script), '--fixture', str(fixture), '--start', str(answer.rindex('{'))],
                            capture_output=True, text=True, check=True)
    report = jsonio.decode_exact(result.stdout)
    assert report['original_check']['overall'] == 'fail'
    assert report['proposed_check']['overall'] == 'pass'
    assert report['proposed_accepted'] is False
    assert jsonio.decode_exact(report['recovered_value_text'])['id'] == 9007199254740993
    assert report['discarded_text'] == answer[:answer.rindex('{')]
    fixture.write_text(jsonio.encode_exact({'contract_text': jsonio.encode_exact(contract), 'raw_answer': answer, 'redacted': True}))
    result = subprocess.run([sys.executable, str(script), '--fixture', str(fixture)], capture_output=True, text=True, check=True)
    assert jsonio.decode_exact(result.stdout)['status'] == 'unassessable'


def _replay(tmp_path, evidence, *args):
    """Run the replay CLI on one evidence fixture and return its decoded report."""
    import sys
    from pathlib import Path
    fixture = tmp_path / 'named.json'
    fixture.write_text(jsonio.encode_exact(evidence))
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'replay_output.py'
    result = subprocess.run([sys.executable, str(script), '--fixture', str(fixture), *args],
                            capture_output=True, text=True, check=True)
    return jsonio.decode_exact(result.stdout)


def _plan():
    """A captured two-output named plan, in the form an attempt record stores."""
    from agentwiz import checks
    from agentwiz.model_step import ContractCopy, OutputPlan
    return OutputPlan((
        ('note', 'A short note', ContractCopy.of('note', 1, checks.parse_contract({'format': 'text'}))),
        ('data', 'The numbers', ContractCopy.of('data', 1, checks.parse_contract({'format': 'json', 'schema': {
            'type': 'object', 'properties': {'id': {'type': 'integer'}}, 'required': ['id'],
            'additionalProperties': False}})))),).view()


def test_replay_checks_the_captured_named_plan_per_output(tmp_path):
    """A named attempt is replayed against the plan it was captured with: exact values for a passing envelope,
    located per-output failures, and no recovered value when any one field fails."""
    plan = _plan()
    good = '{"data":{"id":9007199254740993},"note":"hello"}'
    report = _replay(tmp_path, {'output_plan': plan, 'raw_answer': good, 'truncated': False, 'redacted': False})
    assert report['status'] == 'assessed' and report['original_accepted'] is True
    assert jsonio.decode_exact(report['recovered_value_text']) == {'note': 'hello', 'data': {'id': 9007199254740993}}
    bad = '{"data":{"id":"x"},"note":"hello"}'
    report = _replay(tmp_path, {'output_plan': plan, 'raw_answer': bad, 'truncated': False, 'redacted': False})
    assert report['status'] == 'assessed' and report['original_accepted'] is False
    assert report['recovered_value_text'] is None
    assert report['original_check']['outputs']['note']['overall'] == 'pass'
    assert report['original_check']['outputs']['data']['overall'] == 'fail'
    # A selected span is judged by the same plan: dropping the preamble recovers nothing unless it passes.
    report = _replay(tmp_path, {'output_plan': plan, 'raw_answer': 'Sure: ' + good, 'truncated': False,
                                'redacted': False}, '--start', '6')
    assert report['original_accepted'] is False and report['proposed_accepted'] is True


def test_replay_refuses_named_evidence_it_cannot_trust(tmp_path):
    """Replay must never report a legacy or damaged check as validating a multi-output answer."""
    plan = _plan()
    answer = '{"data":{"id":1},"note":"hello"}'
    unreadable = dict(plan, outputs=[dict(plan['outputs'][0], text='{not json'), plan['outputs'][1]])
    variants = {'another mode': dict(plan, mode='single'), 'another renderer': dict(plan, renderer=99),
                'no outputs': dict(plan, outputs=[]), 'altered contract': dict(plan, digest='0' * 64),
                'unreadable contract': unreadable, 'not an object': ['named_json']}
    for name, saved in variants.items():
        report = _replay(tmp_path, {'output_plan': saved, 'raw_answer': answer, 'redacted': False})
        assert report == {'status': 'unsupported_evidence', 'reason': 'invalid_output_plan'}, name
    redacted = _replay(tmp_path, {'output_plan': plan, 'raw_answer': answer, 'redacted': True})
    assert redacted['status'] == 'unassessable'
