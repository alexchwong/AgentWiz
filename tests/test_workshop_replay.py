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
