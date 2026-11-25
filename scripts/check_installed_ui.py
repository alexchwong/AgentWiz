#!/usr/bin/env python3
"""Run the installed-wheel checks outside the checkout against an already installed wheel.

    python scripts/check_installed_ui.py --expect-version X.Y.Z --workflow-outcome {f-a,f-b,removal} [--shape-only]

Install the candidate wheel and dev dependencies in this interpreter first. No build, publish,
model call or owner interaction is performed here. The repository is never copied as a package.

Both arguments are required and have no default. The script refuses to run unless `agentwiz` is
imported from site-packages, its version equals --expect-version, and the installed Workflow Design
page has the shape --workflow-outcome names (exit 3 on a shape mismatch). Pytest then runs the
module list with --junitxml, and every REQUIRED row must match exactly one passed JUnit case
(0.2.3 technical specification v15, section 9.4).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
OUTCOMES = ('f-a', 'f-b', 'removal')

MODULES = (
    'tests/test_page.py',
    'tests/test_page_library.py',
    'tests/test_tutorial.py',
    'tests/test_upgrade_v0_2_2.py',
    'tests/test_host_script_retired.py',
    'tests/test_server_config.py',
    'tests/test_retired_surfaces.py',
    'tests/test_attempts.py',
    'tests/test_workshop.py',
    'tests/test_label_review.py',
    'tests/test_workflows.py',
)

# Workflow Design surfaces, found by the test identifiers the page code writes with its test() helper.
CANVAS = 'test("canvas-pane")'
LIST_ADDITIONS = ('test("list-insert-gate")', 'test("list-restriction")')
SHAPES = {
    'f-a': (True, False),
    'f-b': (True, True),
    'removal': (False, True),
}

_PAGE = 'tests/test_page.py::'
# One row per required case: (spec label, exact pytest node ID). Rows for every outcome.
REQUIRED = (
    ('D-1', _PAGE + 'test_page_documents_pdf_to_local_only_block_input'),
    ('R-2-current', _PAGE + 'test_open_attached_current_loads_latest_revision'),
    ('R-4-managed', _PAGE + 'test_managed_engine_open_as_draft'),
    ('R-4-compare', _PAGE + 'test_compare_stale_reload_keeps_arms_and_examples'),
    ('R-4-retained', _PAGE + 'test_current_problem_keeps_valid_retained_revision_runnable'),
    ('R-7-active', _PAGE + 'test_compact_attempt_download_is_shared_while_batch_is_active'),
    ('R-4k-restart', 'tests/test_workshop.py::test_engine_recovery_record_is_discarded_by_a_restart'),
    ('R-4k-switch', 'tests/test_workshop.py::test_engine_recovery_expires_on_workspace_switch'),
    ('R-4k-assist', 'tests/test_workshop.py::test_protected_apply_preserves_engine_recovery'),
    ('N-2', _PAGE + 'test_page_startup_initialises_every_screen'),
    ('N-1a', _PAGE + 'test_page_retired_screen_fragments_open_workshop[checked-request]'),
    ('N-1b', _PAGE + 'test_page_retired_screen_fragments_open_workshop[blocks]'),
    ('R-1', _PAGE + 'test_page_workshop_trial_attempts_show_check_and_repair_reason'),
    ('R-1e', _PAGE + 'test_page_workshop_trial_attempts_after_eviction'),
    ('R-7p', _PAGE + 'test_page_workshop_compare_loads_attempts_only_on_demand'),
    ('R-7s', 'tests/test_attempts.py::test_compact_attempt_view'),
    ('R-2a', _PAGE + 'test_page_indicator_stop_after_reload'),
    ('R-2b', _PAGE + 'test_page_indicator_reattaches_clean_run_read_only'),
    ('R-2b2', _PAGE + 'test_page_indicator_reattaches_explicit_revision'),
    ('R-2b3', _PAGE + 'test_page_indicator_reattaches_edited_draft'),
    ('R-2b4', _PAGE + 'test_page_indicator_falls_back_when_draft_ref_evicted'),
    ('R-2b5', _PAGE + 'test_page_indicator_reattaches_after_leaving_without_reload'),
    ('R-2b6', _PAGE + 'test_page_indicator_open_keeps_own_draft'),
    ('R-2c', _PAGE + 'test_page_indicator_stop_when_block_deleted'),
    ('R-2d', _PAGE + 'test_page_indicator_clears_when_batch_completes'),
    ('R-3', _PAGE + 'test_page_workshop_stops_looping_python_block'),
    ('R-4a', _PAGE + 'test_page_workshop_stale_saved_block_keeps_inputs'),
    ('R-4b', _PAGE + 'test_page_workshop_stale_local_only_change_then_refused'),
    ('R-4c', _PAGE + 'test_page_workshop_edited_draft_ignores_disk_edit'),
    ('R-4f', _PAGE + 'test_page_workshop_explicit_revision_stays_selected'),
    ('R-4i', _PAGE + 'test_page_workshop_clean_indented_python_block_runs'),
    ('R-4j', _PAGE + 'test_page_workshop_clean_indented_model_block_runs'),
    ('R-4k', _PAGE + 'test_page_workshop_engine_recovery_open_as_draft_and_save'),
    ('R-5', _PAGE + 'test_page_workshop_shows_located_block_problem'),
    ('R-4d', 'tests/test_workshop.py::test_clean_current_selection_mismatched_revision_is_stale'),
    ('R-4e1', 'tests/test_workshop.py::test_clean_current_selection_rechecks_before_every_trial[1]'),
    ('R-4e2', 'tests/test_workshop.py::test_clean_current_selection_rechecks_before_every_trial[2]'),
    ('R-4g', 'tests/test_workshop.py::test_clean_current_selection_unusable_contract_pin_is_stale'),
    ('R-4h', 'tests/test_workshop.py::test_export_race_never_pairs_old_bytes_with_new_revision'),
    ('R-4k-server', 'tests/test_workshop.py::test_engine_recovery_save_checkpoints_and_refusals'),
    ('R-4n', 'tests/test_workshop.py::test_engine_recovery_binds_only_the_issued_draft_ref'),
    ('R-4l', 'tests/test_label_review.py::'
             'test_acknowledged_retained_labels_stop_restricting_but_examples_stay_restricted'),
    ('R-4m', 'tests/test_label_review.py::test_clean_current_compare_keeps_saved_example_restriction'),
    ('R-6h', 'tests/test_server_config.py::test_headless_repair_after_server_change_is_unchanged'),
    ('R-6', 'tests/test_server_config.py::test_workshop_repair_refuses_changed_server_before_reservation[changed]'),
    ('R-6c', 'tests/test_server_config.py::test_workshop_repair_refuses_changed_server_before_reservation[unchanged]'),
    ('R-0-1', 'tests/test_retired_surfaces.py::test_synthesis_inline_route_is_retired'),
    ('R-0-3b-complete', 'tests/test_retired_surfaces.py::test_checked_request_source_is_refused'),
    ('R-0-3b-repair', 'tests/test_retired_surfaces.py::test_repair_routes_are_retired'),
    ('R-0-3b-instructions', 'tests/test_retired_surfaces.py::test_contract_instructions_route_is_retired'),
    ('H-1', 'tests/test_host_script_retired.py::test_h1_unmanaged_v3_script_block_is_refused_everywhere'),
    ('H-2', 'tests/test_host_script_retired.py::test_h2_imported_script_draft_is_invalid_and_keeps_provenance'),
    ('H-3a', 'tests/test_host_script_retired.py::test_h3a_script_shaped_assist_reply_is_refused_by_schema'),
    ('H-3b', 'tests/test_host_script_retired.py::test_h3b_seeded_script_proposal_is_refused_at_apply'),
    ('H-4', 'tests/test_host_script_retired.py::test_h4_inherited_protection_cannot_be_cleared_in_a_draft'),
    ('U-1', 'tests/test_upgrade_v0_2_2.py::test_u1_open'),
    ('U-2', 'tests/test_upgrade_v0_2_2.py::test_u2_history'),
    ('U-3', 'tests/test_upgrade_v0_2_2.py::test_u3_protected_block'),
    ('U-4', 'tests/test_upgrade_v0_2_2.py::test_u4_host_script_and_provenance'),
    ('U-5', 'tests/test_upgrade_v0_2_2.py::test_u5_gate_workflow_separate_runs'),
)

_WF = (
    ('WF-a', _PAGE + 'test_workflow_canvas_wires_by_tap_keeps_geometry_undoes_and_persists_layout'),
    ('WF-b', _PAGE + 'test_workflow_design_bands_keep_pane_minimums'),
    ('WF-c', _PAGE + 'test_workflow_design_insert_gate_offers_the_sibling_map_and_undoes_in_one_step'),
    ('WF-d', _PAGE + 'test_workflow_design_validation_ignores_stale_responses_and_reports_failures'),
    ('WF-e', _PAGE + 'test_workflow_rebind_keeps_verified_expectation_and_snapshot_runs'),
    ('WF-f', _PAGE + 'test_workflow_form_only_edit_save_preflight_run_and_trace'),
    ('WF-g', _PAGE + 'test_workflow_form_round_trips_exact_constant'),
    ('WF-h', _PAGE + 'test_release_gate_form_approval_and_browser_reconnect'),
)
_W_LIST = (
    ('W-1', _PAGE + 'test_workflow_list_insert_gate_on_a_connection_undoes_in_one_step'),
    ('W-2', _PAGE + 'test_workflow_list_restriction_explains_each_value'),
    *((f'W-5{x}', _PAGE + f'test_workflow_add_release_gate_selects_the_finding_connection[{case}]')
      for x, case in (('a', 'single'), ('b', 'node_level'), ('b-ex', 'node_level_example'), ('c', 'named'),
                      ('c-ex', 'named_example'))),
)
_W_REMOVAL = (
    ('W-3', 'tests/test_workflows.py::test_save_without_layout_keeps_positions_and_defaults_new_nodes'),
    ('W-4', _PAGE + 'test_workflow_design_list_fits_narrow_and_wide'),
    ('W-6', _PAGE + 'test_workflow_list_only_lifecycle_and_inspectors'),
)
WORKFLOW_ROWS = {
    'f-a': _WF,
    'f-b': _WF + _W_LIST,
    'removal': _W_LIST + _W_REMOVAL,
}


def shape_of(text: str) -> dict:
    """Which Workflow Design surfaces a workflows.js text ships, by its test identifiers."""
    return {'canvas': CANVAS in text,
            'list_insert_gate': LIST_ADDITIONS[0] in text,
            'list_restriction': LIST_ADDITIONS[1] in text}


def shape_matches(outcome: str, shape: dict) -> bool:
    canvas, additions = SHAPES[outcome]
    return (shape['canvas'] is canvas and shape['list_insert_gate'] is additions
            and shape['list_restriction'] is additions)


def required_rows(outcome: str) -> tuple:
    return REQUIRED + WORKFLOW_ROWS[outcome]


# The later trim tests deliberately fail against the published baseline. Select the Stage 0
# checklist and the original F-a Workflow Design checks by exact node ID for that wheel.
BASELINE_LABELS = {'D-1', 'N-2', 'R-6h', 'H-1', 'H-2', 'H-3a', 'H-3b', 'H-4',
                   'U-1', 'U-2', 'U-3', 'U-4', 'U-5'}
PRIVATE_CHAT = (
    ('Chat-private', 'tests/test_agent_document_tools.py::test_a_draft_from_a_local_only_source_is_labelled_and_promotes_before_reading'),
    ('Chat-refusal', 'tests/test_agent.py::test_restricted_memory_refuses_a_non_local_model_with_remedies'),
    ('Chat-stop', 'tests/test_agent.py::test_stop_during_approval_prevents_every_later_send'),
)


def junit_key(node_id: str) -> tuple[str, str]:
    """The (classname, name) pytest's JUnit writer records for a node ID, with --rootdir at the copy."""
    path, _, rest = node_id.partition('::')
    parts = rest.split('::')
    module = path[:-3] if path.endswith('.py') else path
    classname = '.'.join([module.replace('/', '.')] + parts[:-1])
    return classname, parts[-1]


def results_of(junit: Path) -> dict:
    found: dict = {}
    for case in ET.parse(junit).getroot().iter('testcase'):
        key = (case.get('classname', ''), case.get('name', ''))
        if case.find('failure') is not None:
            result = 'failed'
        elif case.find('error') is not None:
            result = 'error'
        elif case.find('skipped') is not None:
            result = 'skipped'
        else:
            result = 'passed'
        found.setdefault(key, []).append(result)
    return found


def report(rows, found) -> bool:
    ok = True
    for label, node in rows:
        matches = found.get(junit_key(node), [])
        if not matches:
            result = 'missing'
        elif len(matches) > 1:
            result = f'matched {len(matches)} cases'
        else:
            result = matches[0]
        ok = ok and result == 'passed'
        print(f'  {label:<20} {node} {result}', flush=True)
    return ok


def parse(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--expect-version')
    parser.add_argument('--workflow-outcome', choices=OUTCOMES)
    parser.add_argument('--shape-only', action='store_true',
                        help='stop after the version and Workflow Design shape checks')
    args = parser.parse_args(argv)
    missing = [name for name, value in (('--expect-version', args.expect_version),
                                        ('--workflow-outcome', args.workflow_outcome)) if not value]
    if missing:
        parser.print_usage(sys.stderr)
        print(f'check_installed_ui: missing required argument(s): {", ".join(missing)}', file=sys.stderr)
        raise SystemExit(2)
    return args


def run_pytest(destination: Path, modules) -> int:
    # On macOS TMPDIR may pass through /var -> /private/var. Match the child's physical cwd
    # to pytest's rootdir so JUnit classnames remain relative to the copied test tree.
    root = destination.resolve()
    return subprocess.run([sys.executable, '-I', '-m', 'pytest', '-vv', '-p', 'no:cacheprovider',
                           '-o', 'faulthandler_timeout=120', '--rootdir', str(root),
                           f'--junitxml={root / "junit.xml"}', '-m', 'not live', *modules],
                          cwd=root).returncode


def main(argv=None):
    args = parse(argv)
    with tempfile.TemporaryDirectory(prefix='agentwiz-installed-ui-') as folder:
        destination = Path(folder)
        for name in ('tests', 'scripts'):
            shutil.copytree(ROOT / name, destination / name,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        result = subprocess.run([sys.executable, '-I', '-c',
                                 'import agentwiz,json;print(json.dumps([agentwiz.__file__, agentwiz.__version__]))'],
                                cwd=destination, capture_output=True, text=True, check=True)
        location, version = json.loads(result.stdout)
        package = Path(location).resolve()
        if 'site-packages' not in package.parts or ROOT in package.parents:
            raise RuntimeError('page checks must import the installed candidate wheel, not the checkout')
        print('Installed UI package:', package, flush=True)
        print('Installed version:', version, flush=True)
        print('Expected version:', args.expect_version, flush=True)
        print('Workflow outcome:', args.workflow_outcome, flush=True)
        if version != args.expect_version:
            print(f'check_installed_ui: installed version {version} is not {args.expect_version}', file=sys.stderr)
            return 2
        shape = shape_of((package.parent / 'ui' / 'workflows.js').read_text(encoding='utf-8'))
        print('Workflow Design shape:', json.dumps(shape), flush=True)
        if not shape_matches(args.workflow_outcome, shape):
            print(f'check_installed_ui: outcome {args.workflow_outcome} does not match the installed '
                  f'Workflow Design shape {json.dumps(shape)}', file=sys.stderr)
            return 3
        if args.shape_only:
            return 0
        if version == '0.2.2':
            rows = tuple(row for row in REQUIRED if row[0] in BASELINE_LABELS) + _WF
            modules = [node for _, node in rows]
        else:
            rows = required_rows(args.workflow_outcome) + PRIVATE_CHAT
            modules = [*MODULES, *(node for _, node in PRIVATE_CHAT)]
        if version.split(".")[:2] == ["0", "2"] and int(version.split(".")[2]) >= 4:
            split = (
                ("Split-origins", "tests/test_024_surfaces.py::test_two_origins_and_tool_inventory"),
                ("Private-reconnect-stop", "tests/test_024_surfaces.py::test_private_reconnect_then_stop"),
            )
            rows += split
            modules += [node for _, node in split]
        if version == '0.2.5':
            shutil.copytree(ROOT / 'docs' / 'examples', destination / 'docs' / 'examples')
            foundation = (
                ('Component-primary', 'tests/test_component_foundations.py::test_two_step_import_and_stale_review'),
                ('Shared-Pool-worker', 'tests/test_private_summary_025.py::test_scoped_scripted_worker_and_explicit_pool_promotion'),
                ('Private-Gate', 'tests/test_private_summary_025.py::test_contextual_cloud_summary_exact_send'),
                ('Pool-reuse-UI', 'tests/test_page.py::test_component_pool_review_installs_exact_revision'),
            )
            rows += foundation
            modules += ['tests/test_component_foundations.py', 'tests/test_private_summary_025.py']
        print('Modules:', ' '.join(modules), flush=True)
        print('Required cases:', flush=True)
        for label, node in rows:
            print(f'  {label:<20} {node}', flush=True)
        junit = destination / 'junit.xml'
        code = run_pytest(destination, modules)
        if not junit.is_file():
            print('check_installed_ui: pytest wrote no JUnit report', file=sys.stderr)
            return code or 1
        print('Required-case report:', flush=True)
        ok = report(rows, results_of(junit))
        if code != 0:
            print(f'check_installed_ui: the module run failed (pytest exit {code})', file=sys.stderr)
            return code
        if not ok:
            print('check_installed_ui: a required case is missing, duplicated or did not pass', file=sys.stderr)
            return 1
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
