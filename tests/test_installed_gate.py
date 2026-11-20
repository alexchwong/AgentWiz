"""The installed-wheel gate's Workflow Design shape check (0.2.3 spec v15, section 9.4).

The script must reject a workflow outcome that does not match the shipped page, and the checkout's own
workflows.js must have the shape the checked-in setting names, so a stage that changes Workflow Design
and forgets scripts/installed_gate.json fails here as well as in CI.
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def gate():
    spec = importlib.util.spec_from_file_location('check_installed_ui', ROOT / 'scripts' / 'check_installed_ui.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CANVAS = 'const pane=h("div",{...test("canvas-pane")});'
INSERT = 'h("button",{...test("list-insert-gate")})'
RESTRICT = 'h("details",{...test("list-restriction")})'


@pytest.mark.parametrize('text,accepted', [
    (CANVAS, {'f-a'}),
    (CANVAS + INSERT + RESTRICT, {'f-b'}),
    (INSERT + RESTRICT, {'removal'}),
    (CANVAS + INSERT, set()),
    (CANVAS + RESTRICT, set()),
    (INSERT, set()),
    (RESTRICT, set()),
    ('', set()),
])
def test_shape_table(text, accepted):
    module = gate()
    shape = module.shape_of(text)
    assert {outcome for outcome in module.OUTCOMES if module.shape_matches(outcome, shape)} == accepted


def test_checkout_matches_the_checked_in_setting():
    module = gate()
    setting = json.loads((ROOT / 'scripts' / 'installed_gate.json').read_text(encoding='utf-8'))
    assert set(setting) == {'workflow_outcome'}
    shape = module.shape_of((ROOT / 'agentwiz' / 'ui' / 'workflows.js').read_text(encoding='utf-8'))
    assert module.shape_matches(setting['workflow_outcome'], shape), (setting, shape)


def test_both_arguments_are_required(capsys):
    module = gate()
    for argv in ([], ['--expect-version', '0.2.3'], ['--workflow-outcome', 'f-a']):
        with pytest.raises(SystemExit) as stop:
            module.parse(argv)
        assert stop.value.code == 2
        assert 'missing required argument' in capsys.readouterr().err


def test_junit_keys_and_required_rows_are_unique():
    module = gate()
    assert module.junit_key('tests/test_a.py::TestK::test_m') == ('tests.test_a.TestK', 'test_m')
    assert module.junit_key('tests/test_a.py::test_p[1]') == ('tests.test_a', 'test_p[1]')
    for outcome in module.OUTCOMES:
        rows = module.required_rows(outcome)
        assert len({label for label, _ in rows}) == len(rows)
        assert len({node for _, node in rows}) == len(rows)
    assert len(module.WORKFLOW_ROWS['f-a']) == 8


def test_report_needs_exactly_one_passed_case(tmp_path, capsys):
    module = gate()
    junit = tmp_path / 'j.xml'
    junit.write_text('<testsuites><testsuite>'
                     '<testcase classname="tests.test_a" name="ok"/>'
                     '<testcase classname="tests.test_a" name="skip"><skipped/></testcase>'
                     '<testcase classname="tests.test_a" name="twice"/>'
                     '<testcase classname="tests.test_a" name="twice"/>'
                     '<testcase classname="tests.test_a" name="bad"><failure/></testcase>'
                     '</testsuite></testsuites>')
    found = module.results_of(junit)
    assert module.report([('A', 'tests/test_a.py::ok')], found)
    for name in ('skip', 'twice', 'bad', 'absent'):
        assert not module.report([('A', f'tests/test_a.py::{name}')], found)


def test_installed_junit_keys_survive_a_symlinked_temp_root(tmp_path):
    module = gate()
    real_root = tmp_path / 'physical'
    tests = real_root / 'tests'
    tests.mkdir(parents=True)
    (tests / 'test_probe.py').write_text('def test_probe():\n    assert True\n')
    alias = tmp_path / 'alias'
    alias.symlink_to(real_root, target_is_directory=True)
    assert module.run_pytest(alias, ['tests/test_probe.py']) == 0
    found = module.results_of(real_root / 'junit.xml')
    assert module.report([('probe', 'tests/test_probe.py::test_probe')], found)
