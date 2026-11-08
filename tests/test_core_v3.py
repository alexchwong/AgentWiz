"""Core v3: placeholder restoration, text expectations, advisory validation and explanations, saved-use trust
inventory, remedies and the private-summary reference workflows (Checkpoint B)."""
import random
import threading
import time
import tracemalloc
from contextlib import ExitStack
from http.server import ThreadingHTTPServer

import pytest

from agentwiz import identifier_pairs, jsonio, messages, release_gate, transforms
from agentwiz.templates import FillError
from agentwiz.tutorial_support import private_summary
from agentwiz.workflow_runner import Graph
from conftest import KEY, answer, failure_of, request, save_profile, save_roles, wait_until
from test_release_gate import decide, gate, model_draft, node, review
from test_workflows import edit, port, publish_workflow, run_workflow, transform
from test_workshop import completed, plan_save, send, start_planned

RESTORE = {'unknown': 'fail', 'placeholder_syntax': 'bracket_upper'}
PAIRS = [{'placeholder': '[PERSON_1]', 'original': 'Jane Example'}, {'placeholder': '[DATE_1]', 'original': '3 March'}]


def restore(text, pairs=PAIRS, **parameters):
    return transforms.execute('restore_placeholders', {**RESTORE, **parameters}, {'text': text, 'pairs': pairs})


def location(fn):
    with pytest.raises(FillError) as caught:
        fn()
    return caught.value.location


# --- §2 restore_placeholders ------------------------------------------------------------------------------

def test_restore_replaces_once_without_rescanning_and_reports_trace():
    out, details = restore('[PERSON_1] on [DATE_1]; [PERSON_1] again.')
    assert out == 'Jane Example on 3 March; Jane Example again.'
    assert details[0]['replacements']['items'] == [{'placeholder': '[PERSON_1]', 'count': 2},
                                                   {'placeholder': '[DATE_1]', 'count': 1}]
    chained = [{'placeholder': '[A_1]', 'original': '[B_1]'}, {'placeholder': '[B_1]', 'original': 'x'}]
    out, details = restore('[A_1] [B_1]', chained)
    assert out == '[B_1] x' and details[0]['unknown']['items'] == []
    out, details = restore('[PERSON_1] only.')
    assert details[0]['absent']['items'] == ['[DATE_1]']


@pytest.mark.parametrize('pairs,where', [
    (['Jane Example'], 'inputs.pairs[0]'),
    ([{'placeholder': 'P_1', 'original': 'x'}], 'inputs.pairs[0]'),
    ([{'placeholder': '[p_1]', 'original': 'x'}], 'inputs.pairs[0]'),
    ([{'original': 'x'}], 'inputs.pairs[0]'),
    ([{'placeholder': '[A_1]', 'original': 'x'}, {'placeholder': '[A_1]', 'original': 'y'}], 'inputs.pairs[1]'),
    ({'placeholder': '[A_1]'}, 'inputs.pairs'),
])
def test_restore_refuses_irreversible_mappings_at_their_location(pairs, where):
    assert location(lambda: restore('[A_1]', pairs)) == where


def test_restore_unknown_placeholder_fail_or_keep():
    text = 'See [PERSON_1] and [OTHER_2].'
    with pytest.raises(FillError) as caught:
        restore(text)
    assert caught.value.location == 'inputs.text' and str(text.index('[OTHER_2]')) in caught.value.message
    out, details = restore(text, unknown='keep')
    assert out == 'See Jane Example and [OTHER_2].'
    assert details[0]['unknown']['items'] == [{'placeholder': '[OTHER_2]', 'offset': text.index('[OTHER_2]')}]
    duplicate = [PAIRS[0], dict(PAIRS[0])]
    assert restore('[PERSON_1]', duplicate)[0] == 'Jane Example'


@pytest.mark.parametrize('text,pairs', [
    ('é' * 500, []),                     # 2 UTF-8 bytes per character, 6 escaped characters
    ('"' * 600, []),
    ('\x01' * 300, []),
    ('[P_1]' * 3, [{'placeholder': '[P_1]', 'original': 'a' * 400}]),
])
def test_restore_enforces_escaped_limit_before_building(monkeypatch, text, pairs):
    monkeypatch.setattr(transforms, 'VALUE_LIMIT', 1024)
    assert len(text.encode('utf-8')) <= 1024 or pairs
    tracemalloc.start()
    try:
        assert location(lambda: restore(text, pairs)) == 'output'
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * 1024 + 4096    # the bound plus fixed interpreter overhead for the raised error


def test_scan_profile_keeps_gate_behaviour_and_restore_is_stricter():
    accepted = [[{'original': 'Alice', 'placeholder': '[P1]'}], ['Alice'], [{'original': 'Alice'}],
                [{'original': 'Alice', 'placeholder': '[P1]'}, {'original': 'Bob', 'placeholder': '[P1]'}]]
    for mapping in accepted:
        assert release_gate.identifier_check('Alice met Bob', mapping, configured=True)['status'] == 'complete'
    both = release_gate.identifier_check('Alice met Bob', accepted[3], configured=True)['hits']
    assert [hit['identifier'] for hit in both] == ['[P1]', '[P1]']
    for mapping in (accepted[3], accepted[1]):
        with pytest.raises(identifier_pairs.PairsError):
            identifier_pairs.parse(mapping, profile='restore')
    from agentwiz.workshop import WorkshopError
    with pytest.raises(WorkshopError) as caught:
        release_gate.identifier_check('x', 'x', configured=True)
    assert caught.value.code == 'scan_invalid'


def test_assistance_schema_accepts_restore_and_text_expectations():
    import jsonschema
    from agentwiz import workshop_assist
    schema = workshop_assist.schema('create', stage='behaviour')
    proposal = {'format_version': 1, 'action': 'create', 'assumptions': [], 'unmet_requirements': [],
                'payload': {'behaviour': {'kind': 'transform', 'operation': 'restore_placeholders', 'parameters': RESTORE}}}
    jsonschema.validate(proposal, schema)
    proposal['payload']['behaviour']['parameters'] = {**RESTORE, 'unknown': 'skip'}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(proposal, schema)
    examples = workshop_assist.schema('suggest_examples')
    suggestion = {'format_version': 1, 'action': 'suggest_examples', 'assumptions': [], 'unmet_requirements': [],
                  'payload': {'examples': [{'label': 'l', 'inputs': {'text': 'x'}, 'rationale': 'r', 'expectation':
                      {'kind': 'text', 'assertions': [{'output': 'out', 'operation': 'excludes_pattern', 'pattern': 'bracket_upper'}]}}]}}
    jsonschema.validate(suggestion, examples)


def test_restore_starter_is_v4_and_its_examples_pass_standalone_and_in_a_workflow(ui):
    starters = {row['id']: row for row in request(ui, '/api/workshop/starters')['starters']}
    assert starters['restore_placeholders']['version'] == 2
    draft = send(ui, 'preview', starter='restore_placeholders', starter_version=2, key='restore')['draft']
    definition = jsonio.decode_exact(draft['definition_text'])
    assert definition['format_version'] == 4
    assert all(port['local_only'] is True for side in ('inputs', 'outputs') for port in definition[side].values())
    assert definition['steps'][0]['parameters'] == RESTORE
    plan_save(ui, draft)
    examples = starters['restore_placeholders']['examples']
    batch = completed(ui, start_planned(ui, 'batches', {'candidates': [{'key': 'restore'}], 'examples': examples})['batch_id'])
    assert [row['correctness'] for row in batch['rows']] == ['correct', 'correct']
    fresh = send(ui, 'preview', starter='restore_placeholders', starter_version=2, key='restore_node')['draft']
    publish_workflow(ui, fresh, examples=examples)
    _, result = run_workflow(ui, {'name': 'chain', 'examples': [{'id': e['id'], 'version': 1} for e in examples]})
    assert result['outcome'] == 'complete', result
    assert [row['correctness'] for row in result['examples']] == ['correct', 'correct']


# --- §7.2 text expectations --------------------------------------------------------------------------------

def test_text_expectation_validation_and_judgement(ui):
    from agentwiz.workshop_service import judgement
    draft = send(ui, 'preview', starter='format_report', starter_version=2, key='report')['draft']
    plan_save(ui, draft)
    good = {'kind': 'text', 'assertions': [
        {'output': 'out', 'operation': 'includes', 'values': ['FATIGUE'], 'case': 'insensitive'},
        {'output': 'out', 'operation': 'excludes', 'values': ['Jane'], 'case': 'sensitive'},
        {'output': 'out', 'operation': 'excludes_pattern', 'pattern': 'bracket_upper'}]}
    example = {'id': 'e', 'version': 1, 'label': 'l', 'verified': True, 'inputs': {'text': 'jane has fatigue'}, 'expectation': good}
    batch = completed(ui, start_planned(ui, 'batches', {'candidates': [{'key': 'report'}], 'examples': [example]})['batch_id'])
    assert batch['rows'][0]['correctness'] == 'correct'
    for bad in ({'output': 'out', 'operation': 'includes', 'values': [], 'case': 'insensitive'},
                {'output': 'out', 'operation': 'excludes_pattern', 'pattern': '\\d+'},
                {'output': 'missing', 'operation': 'excludes', 'values': ['x'], 'case': 'sensitive'},
                {'output': 'out', 'operation': 'includes', 'values': ['x'], 'case': 'insensitive', 'extra': 1}):
        refused = failure_of(lambda: start_planned(ui, 'batches', {'candidates': [{'key': 'report'}], 'examples': [
            {**example, 'expectation': {'kind': 'text', 'assertions': [bad]}}]}))
        assert refused.status == 400
    outputs = {'out': {'format': 'text', 'text': 'see [PERSON_1]'}}
    assert judgement(outputs, good, True) == 'incorrect'
    assert judgement(outputs, good, False) == 'unknown'
    assert judgement({'other': outputs['out']}, good, True) == 'incorrect'


def test_text_assertions_require_text_outputs(ui):
    draft = send(ui, 'preview', starter='count_text', starter_version=2, key='words')['draft']
    plan_save(ui, draft)
    example = {'id': 'e', 'version': 1, 'label': 'l', 'verified': True, 'inputs': {'text': 'a'},
               'expectation': {'kind': 'text', 'assertions': [{'output': 'out', 'operation': 'excludes_pattern',
                                                               'pattern': 'bracket_upper'}]}}
    refused = failure_of(lambda: start_planned(ui, 'batches', {'candidates': [{'key': 'words'}], 'examples': [example]}))
    assert refused.body['code'] == 'example_incompatible'


# --- §4.2 explanation projection ---------------------------------------------------------------------------

def opened(ui, name):
    bundle = jsonio.decode_file_exact((ui.workspace.path / f'workflows/{name}.json').read_bytes())
    stack = ExitStack()
    return stack, Graph(bundle, name, stack), bundle


def causes(item, code):
    return [cause for cause in item['causes'] if cause['code'] == code]


def save_definition(ui, definition, selections, examples=None):
    body = {'name': definition['name'], 'file_token': None, 'definition_text': jsonio.encode_exact(definition),
            'selections': selections}
    if examples is not None:
        body['examples'] = examples
    edit(ui, 'save', body)


def echo(ui, key='echo', restricted=False, inputs=None):
    saved = plan_save(ui, transform(key, 'format_report', {'template': '{{inputs.text}}'},
                                    inputs or {'text': port(restricted=restricted)}, port()))
    return {'key': key, 'revision': saved['durable_id']}


def test_explain_reports_fan_out_examples_and_agrees_with_planning(ui):
    plain, labelled = echo(ui, 'plain'), echo(ui, 'labelled', restricted=True)
    definition = {'name': 'fan', 'purpose': '', 'inputs': {'text': port(), 'other': port()}, 'constants': {},
                  'nodes': {'a': {'bindings': {'text': {'kind': 'input', 'port': 'text'}}},
                            'b': {'bindings': {'text': {'kind': 'input', 'port': 'text'}}},
                            'c': {'bindings': {'text': {'kind': 'input', 'port': 'other'}}}},
                  'outputs': {'out': {**port(), 'binding': node('a')}}}
    example = {'id': 'x', 'version': 1, 'inputs': {'text': 't', 'other': 'o'}, 'restrictions': {'other': True},
               'expectation': {'kind': 'review', 'criterion': ''}}
    save_definition(ui, definition, {'a': plain, 'b': labelled, 'c': plain}, [example])
    stack, graph, bundle = opened(ui, 'fan')
    with stack:
        base = graph.explain()
        assert causes(base['values']['input.text'], 'consumer_label') == [
            {'code': 'consumer_label', 'consumer': 'nodes.b.inputs.text'}]
        assert base['values']['input.other']['restricted'] is False
        assert base['nodes']['a']['protected'] and causes(base['nodes']['a'], 'restricted_input')
        chained = base['values']['node.a.out']
        assert causes(chained, 'upstream')[0]['chain'][-1] == ['input', 'text']
        with_example = graph.explain(bundle['examples'][0])
        assert with_example['basis'] == {'example_id': 'x', 'version': 1}
        assert causes(with_example['values']['input.other'], 'example_restriction')
        assert not causes(graph.explain()['values']['input.other'], 'example_restriction')
        for basis in (None, *bundle['examples']):
            view = graph.explain(basis)
            graph.select_example(basis or {})
            assert {key for key, row in view['values'].items() if row['restricted']} == {
                '.'.join(identity) for identity in graph.restricted}
            assert {key for key, row in view['nodes'].items() if row['protected']} == graph.protected_nodes


def test_released_value_stays_unrestricted_beside_a_restricted_mapping(ui):
    pair = echo(ui, 'pair', inputs={'text': port(), 'mapping': port('json')})
    plain = echo(ui, 'plain')
    definition = {'name': 'released', 'purpose': '', 'inputs': {'text': port(restricted=True)},
                  'constants': {'mapping': {'value': ['Alice'], 'local_only': True}},
                  'nodes': {'review': gate({'kind': 'input', 'port': 'text'}),
                            'use': {'bindings': {'text': node('review', 'released'),
                                                 'mapping': {'kind': 'constant', 'name': 'mapping'}}},
                            'after': {'bindings': {'text': node('review', 'released')}}},
                  'outputs': {'out': {**port(), 'binding': node('use')}}}
    save_definition(ui, definition, {'use': pair, 'after': plain})
    stack, graph, _ = opened(ui, 'released')
    with stack:
        view = graph.explain()
        released = view['values']['node.review.released']
        assert released['restricted'] is False and causes(released, 'released')
        assert view['values']['constant.mapping']['restricted'] is True
        assert causes(view['nodes']['use'], 'restricted_input') == [
            {'code': 'restricted_input', 'input': 'mapping', 'value': 'constant.mapping'}]
        assert view['values']['node.use.out']['restricted'] is True
    definition['nodes']['after'] = {'bindings': {'text': node('review', 'released')}}
    save_definition(ui, {**definition, 'name': 'relabelled'}, {'use': pair, 'after': echo(ui, 'local', restricted=True)})
    stack, graph, _ = opened(ui, 'relabelled')
    with stack:
        released = graph.explain()['values']['node.review.released']
        assert released['restricted'] is True and causes(released, 'consumer_label')


# --- §6 validation ------------------------------------------------------------------------------------------

def validate(ui, **body):
    return send(ui, 'workflow-validate', **{'file_token': None, 'generation': 1, **body})


def editor(definition):
    out = dict(definition)
    for side in ('inputs', 'outputs'):
        out[side] = {key: {**value, 'contract': jsonio.encode_exact(value['contract'])} for key, value in definition[side].items()}
    out['constants'] = {key: {**value, 'value': jsonio.encode_exact(value['value'])} for key, value in definition['constants'].items()}
    return out


def test_validation_collects_issues_and_withholds_projection_for_cycles(ui, tmp_path):
    source = echo(ui, 'plain')
    json_source = echo(ui, 'json_in', inputs={'text': port('json')})
    definition = {'name': 'draft', 'purpose': '', 'inputs': {'text': port()}, 'constants': {},
                  'nodes': {'a': {'bindings': {}}, 'b': {'bindings': {'text': {'kind': 'input', 'port': 'text', 'path': ['x']}}},
                            'j': {'bindings': {'text': {'kind': 'input', 'port': 'text'}}}},
                  'outputs': {'out': {**port(), 'binding': node('a')}}}
    selections = {'a': source, 'b': source, 'j': json_source}
    before = sorted((path.name, path.stat().st_mtime_ns) for path in ui.workspace.path.rglob('*'))
    result = validate(ui, name='draft', editor=editor(definition), selections=selections)
    codes = {(issue['code'], issue['location']) for issue in result['structural']}
    assert ('binding_missing', 'nodes.a.inputs.text') in codes
    assert ('contract_mismatch', 'nodes.b.inputs.text') in codes
    assert result['available'] is True and result['generation'] == 1
    definition['nodes']['a']['bindings'] = {'text': node('b')}
    definition['nodes']['b']['bindings'] = {'text': node('a')}
    result = validate(ui, name='draft', editor=editor(definition), selections=selections, generation=7)
    assert result['available'] is False and result['generation'] == 7
    assert result['unplannable']['cycle'] == ['a', 'b']
    assert 'restriction' not in result and 'destinations' not in result and set(result['ports']) == {'a', 'b', 'j'}
    definition['nodes']['j']['bindings'] = {'text': node('gone')}
    result = validate(ui, name='draft', editor=editor(definition), selections=selections)
    assert result['unplannable']['unknown_source'] == ['nodes.j.inputs.text'] and 'restriction' not in result
    assert sorted((path.name, path.stat().st_mtime_ns) for path in ui.workspace.path.rglob('*')) == before


def test_validation_never_reports_a_labelled_shared_source_unrestricted(ui):
    plain, labelled = echo(ui, 'plain'), echo(ui, 'labelled', restricted=True)
    pair = echo(ui, 'pair', inputs={'text': port(restricted=True), 'other': port()})
    definition = {'name': 'shared', 'purpose': '', 'inputs': {'text': port()}, 'constants': {},
                  'nodes': {'a': {'bindings': {'text': {'kind': 'input', 'port': 'text'}}},
                            'b': {'bindings': {'text': {'kind': 'input', 'port': 'text'}}}},
                  'outputs': {'out': {**port(), 'binding': node('b')}}}
    for broken in ({}, {'other': {'kind': 'input', 'port': 'nope'}}):
        definition['nodes']['a']['bindings'] = {'text': {'kind': 'input', 'port': 'text'}, **broken}
        result = validate(ui, name='shared', editor=editor(definition), selections={'a': pair, 'b': plain})
        assert not result['available'] or result['restriction']['values']['input.text']['restricted'] is True
    del labelled


def test_validation_rebind_is_advisory_and_reuses_snapshots(ui):
    first = echo(ui, 'plain')
    definition = {'name': 'saved', 'purpose': '', 'inputs': {'text': port()}, 'constants': {},
                  'nodes': {'a': {'bindings': {'text': {'kind': 'input', 'port': 'text'}}}},
                  'outputs': {'out': {**port(), 'binding': node('a')}}}
    save_definition(ui, definition, {'a': first})
    token = request(ui, '/api/workshop/workflows/saved')['file_token']
    draft = send(ui, 'preview', key='plain')['draft']
    body = jsonio.decode_exact(draft['definition_text'])
    body['inputs']['extra'] = port()
    draft['definition_text'] = jsonio.encode_exact(body)
    second = plan_save(ui, draft, draft['base_digest'])
    assert validate(ui, name='saved', file_token=token, editor=editor(definition))['ports']['a']['inputs'].keys() == {'text'}
    selection = {'a': {'key': 'plain', 'revision': second['durable_id']}}
    assert failure_of(lambda: validate(ui, name='saved', file_token=token, editor=editor(definition),
                                       selections=selection)).status == 409
    result = validate(ui, name='saved', file_token=token, editor=editor(definition), selections=selection, rebind=['a'])
    assert set(result['ports']['a']['inputs']) == {'text', 'extra'}
    assert failure_of(lambda: validate(ui, name='saved', file_token='0' * 64, editor=editor(definition))).body['code'] == 'stale'


def test_validation_and_inventory_read_configuration_only(ui, server, monkeypatch):
    from agentwiz import state as state_module
    from agentwiz.state import ProcessState as State
    save_profile(ui, 'local', model='deid', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'local': 'local'})
    saved = plan_save(ui, model_draft('deid', 'local', {'text': port()}, {'out': port()}))
    definition = {'name': 'model', 'purpose': '', 'inputs': {'text': port(restricted=True)}, 'constants': {},
                  'nodes': {'m': {'bindings': {'text': {'kind': 'input', 'port': 'text'}}}},
                  'outputs': {'out': {**port(), 'binding': node('m')}}}
    selections = {'m': {'key': 'deid', 'revision': saved['durable_id']}}
    save_definition(ui, definition, selections)

    def forbidden(*_args, **_kwargs):
        raise AssertionError('credential or refresh path used')

    class Servers(dict):
        def __getitem__(self, kind):
            settings = dict.__getitem__(self, kind)
            return type('S', (), {'base_url': settings.base_url, 'api_key': property(forbidden)})()

    for attribute in ('_endpoint', 'view', '_refresh_mcp_locked', '_token_for_locked', 'role_capture'):
        monkeypatch.setattr(State, attribute, forbidden)
    monkeypatch.setattr(ui.state, 'servers', Servers(ui.state.servers))
    monkeypatch.setattr(state_module, '_endpoint', forbidden)
    from agentwiz import outbound_policy
    result = ui.state.workshop.workflows.validate({'name': 'probe', 'file_token': None, 'generation': 0,
                                                   'editor': editor({**definition, 'name': 'probe'}),
                                                   'selections': selections})
    assert result['destinations']['m']['protected'] is True and result['destinations']['m']['trusted'] is False
    assert result['destinations']['m']['remedy']['action'] == 'grant'
    inventory = outbound_policy.saved_uses(ui.state)
    assert inventory['scan']['complete'] and any(row['requires_trust'] for row in inventory['rows'])
    assert server.requests == []


def test_validation_cost_on_64_node_fixture(ui):
    source = echo(ui, 'plain')
    nodes = {f'n{index}': {'bindings': {'text': node(f'n{index - 1}') if index else {'kind': 'input', 'port': 'text'}}}
             for index in range(64)}
    definition = {'name': 'long', 'purpose': '', 'inputs': {'text': port(restricted=True)}, 'constants': {},
                  'nodes': nodes, 'outputs': {'out': {**port(), 'binding': node('n63')}}}
    started = time.monotonic()
    result = validate(ui, name='long', editor=editor(definition), selections={name: source for name in nodes})
    assert time.monotonic() - started < 2 and result['available']


def test_validation_agrees_with_readiness_on_generated_graphs(ui):
    plain, labelled = echo(ui, 'plain'), echo(ui, 'labelled', restricted=True)
    pair = echo(ui, 'pair', inputs={'text': port(), 'other': port()})
    generator = random.Random(20261108)
    for case in range(6):
        nodes, choices = {}, [{'kind': 'input', 'port': 'text'}, {'kind': 'input', 'port': 'secret'}]
        selections = {}
        for index in range(generator.randint(2, 6)):
            name = f'n{index}'
            block = generator.choice([plain, labelled, pair])
            bindings = {'text': generator.choice(choices)}
            if block is pair:
                bindings['other'] = generator.choice(choices)
            nodes[name], selections[name] = {'bindings': bindings}, block
            choices.append(node(name))
        last = sorted(nodes)[-1]
        definition = {'name': f'g{case}', 'purpose': '', 'inputs': {'text': port(), 'secret': port(restricted=True)},
                      'constants': {}, 'nodes': nodes, 'outputs': {'out': {**port(), 'binding': node(last)}}}
        save_definition(ui, definition, selections)
        token = request(ui, f'/api/workshop/workflows/g{case}')['file_token']
        result = validate(ui, name=f'g{case}', file_token=token, editor=editor(definition))
        plan = send(ui, 'plans', action='workflow_run', request={'name': f'g{case}', 'inputs': {'text': 'a', 'secret': 'b'}})
        protected = {key for key, row in result['restriction']['nodes'].items() if row['protected']}
        assert protected == set(plan['descriptor']['protected_nodes'])
        findings = lambda issues: sorted((i['code'], i['location']) for i in issues if i['code'] in (
            'release_unneeded', 'scan_unrestricted', 'unrestricted_ancestor'))
        assert findings(result['structural']) == findings(plan['descriptor']['issues'])


# --- §3 inventory, remedies and catalogue -----------------------------------------------------------------

def test_candidates_offer_fix_address_for_localhost_and_grant_from_configuration(ui, server):
    save_profile(ui, 'loop', model='m', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'fast': 'loop'})
    rows = request(ui, '/api/workshop/destination-trust-candidates')
    row = next(row for row in rows['candidates'] if {'kind': 'role', 'name': 'fast'} in row['used_by'])
    assert row['remedy'] == {'action': 'grant', 'target': row['destination']}
    save_profile(ui, 'named', model='m', kind='openai_compat', base_url='http://localhost:' + server.url.rsplit(':', 1)[1] + '/v1', provider='')
    save_roles(ui, {'fast': 'named'})
    row = next(row for row in request(ui, '/api/workshop/destination-trust-candidates')['candidates']
               if {'kind': 'role', 'name': 'fast'} in row['used_by'])
    assert row['reason'] == 'hostname' and row['remedy']['action'] == 'fix_address'


def test_inventory_tracks_saved_protected_use_across_a_model_change(ui, server):
    save_profile(ui, 'local', model='old', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'local': 'local'})
    saved = plan_save(ui, model_draft('deid', 'local', {'text': port()}, {'out': port()}))
    save_definition(ui, {'name': 'model', 'purpose': '', 'inputs': {'text': port(restricted=True)}, 'constants': {},
                         'nodes': {'m': {'bindings': {'text': {'kind': 'input', 'port': 'text'}}}},
                         'outputs': {'out': {**port(), 'binding': node('m')}}},
                    {'m': {'key': 'deid', 'revision': saved['durable_id']}})
    grants = request(ui, '/api/workshop/destination-trust')
    old = {'kind': 'openai_compat', 'url': server.url + '/v1', 'model': 'old', 'provider': ''}
    send(ui, 'destination-trust', expected_revision=grants['revision'], destination=old, trusted=True)
    assert request(ui, '/api/workshop/destination-trust-candidates')['trust_gaps'] == []
    save_profile(ui, 'local', model='new', kind='openai_compat', provider='')
    rows = request(ui, '/api/workshop/destination-trust-candidates')
    assert rows['unused_grants'] == [old] and rows['scan']['complete'] is True
    [gap] = rows['trust_gaps']
    assert gap['destination']['model'] == 'new' and gap['requires_trust'] is True
    assert {'kind': 'workflow', 'name': 'model'} in gap['used_by']
    plan = send(ui, 'plans', action='workflow_run', request={'name': 'model', 'inputs': {'text': 'x'}})
    refusal = next(issue for issue in plan['descriptor']['issues'] if issue['code'] == 'destination_untrusted')
    assert refusal['remedy']['action'] == 'grant' and refusal['facts']['available'] is True


REFUSAL_CODES = ('destination_untrusted', 'destination_ineligible', 'destination_unavailable', 'destination_changed',
                 'destination_trust_unavailable', 'destination_proxied', 'protected_host_script')


def test_every_refusal_path_offers_an_action(ui, server, monkeypatch):
    from agentwiz import outbound_policy
    from agentwiz.workshop_service import Service
    fact = {'available': True, 'destination': {}, 'locality': 'local', 'eligible': False, 'trusted': False}
    for code in REFUSAL_CODES:
        assert outbound_policy.remedy(code, {**fact, 'reason': 'remote'}) is not None, code
    assert outbound_policy.remedy('destination_unavailable', {'available': False, 'reason': 'x'}) is None
    assert outbound_policy.remedy('role_unresolved', target='fast')['action'] == 'configure_role'
    assert outbound_policy.remedy('server_missing', target='s')['action'] == 'configure_server'
    # Workshop Test: a protected model call to an untrusted local server.
    save_profile(ui, 'local', model='m', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'local': 'local'})
    plan_save(ui, model_draft('private', 'local', {'text': port(restricted=True)}, {'out': port()}))
    refused = failure_of(lambda: start_planned(ui, 'batches', {'candidates': [{'key': 'private'}], 'examples': [
        {'id': 'e', 'version': 1, 'label': 'l', 'inputs': {'text': 'x'}, 'expectation': {'kind': 'review', 'criterion': ''}}]}))
    assert refused.body['code'] == 'destination_untrusted'
    assert refused.body['issues'][0]['remedy']['action'] == 'grant'
    del Service, monkeypatch


def test_catalogue_covers_its_codes_and_falls_back():
    for code in messages.CODES:
        assert messages.explain({'code': code})['title']
    assert messages.explain({'code': 'not_a_code'})['title'] == messages.FALLBACK[0]
    assert messages.explain({'action': 'grant', 'target': {'model': 'm'}})['remedy_label']
    assert messages.explain(None)['title']


# --- §7.3 reference workflows and scenarios ----------------------------------------------------------------

class Backend:
    """The packaged stand-in backend on an ephemeral port, counting requests."""

    def __init__(self):
        from agentwiz.tutorial_support.backend import Handler
        self.requests = []
        outer = self

        class Counting(Handler):
            def do_POST(self):  # noqa: N802
                outer.requests.append(self.path)
                super().do_POST()

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Counting)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, args=(0.02,), daemon=True).start()
        self.url = f'http://127.0.0.1:{self.httpd.server_address[1]}/v1'

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def backends():
    made = []

    def make():
        made.append(Backend())
        return made[-1]
    yield make
    for backend in made:
        backend.close()


def prepare(ui, variant, local, cloud=None):
    save_profile(ui, 'private', model=private_summary.DEID_MODEL, kind='openai_compat', base_url=local.url, provider='')
    save_profile(ui, 'private-summary', model=private_summary.SUMMARY_MODEL, kind='openai_compat', provider='')
    save_profile(ui, 'risk', model=private_summary.RISK_MODEL, kind='openai_compat', provider='')
    roles = {private_summary.LOCAL_ROLE: 'private', private_summary.SUMMARY_ROLE: 'private-summary',
             private_summary.RISK_ROLE: 'risk'}
    if cloud is not None:
        save_profile(ui, 'cloud', model=private_summary.SUMMARY_MODEL, kind='openrouter', base_url=cloud.url, provider='', api_key=KEY)
        roles[private_summary.CLOUD_ROLE] = 'cloud'
    save_roles(ui, roles)
    for model in (private_summary.DEID_MODEL, private_summary.SUMMARY_MODEL, private_summary.RISK_MODEL):
        grants = request(ui, '/api/workshop/destination-trust')
        send(ui, 'destination-trust', expected_revision=grants['revision'], trusted=True,
             destination={'kind': 'openai_compat', 'url': local.url, 'model': model, 'provider': ''})
    return private_summary.ensure(ui.state.workshop, variant)


def run_examples(ui, name, ids, *, approve=None):
    selection = {'name': name, 'examples': [{'id': ident, 'version': 1} for ident in ids]}
    plan = send(ui, 'plans', action='workflow_run', request=selection)
    assert not [issue for issue in plan['descriptor']['issues'] if issue['severity'] == 'error'], plan
    ident = send(ui, f'workflows/{name}/run', request=selection, expected_plan=plan['expected_plan'],
                 ticket=send(ui, 'tickets')['ticket'], confirmations=plan['required_confirmations'])['operation_id']
    route = '/api/workshop/workflow-jobs/' + ident
    if approve:
        for _ in ids:
            current = review(ui, route)
            decide(ui, ident, current, acknowledge_assessment=True)
            wait_until(lambda: request(ui, route).get('phase') != 'awaiting_release' or request(ui, route)['release']['token'] != current['token'])
    wait_until(lambda: request(ui, route)['outcome'] != 'running', timeout=60)
    return {**request(ui, route), 'route': route}


def test_private_summary_local_reference_passes_its_examples(ui, backends):
    local = backends()
    name = prepare(ui, 'local', local)
    result = run_examples(ui, name, [f'case_{index}' for index in range(1, 11)])
    assert result['outcome'] == 'complete', result
    assert [row['correctness'] for row in result['examples']] == ['correct'] * 10
    assert all(row['outputs']['restored']['local_only'] for row in result['examples'])


def test_private_summary_release_sends_only_the_approved_candidate(ui, backends):
    local, cloud = backends(), backends()
    name = prepare(ui, 'release', local, cloud)
    result = run_examples(ui, name, ['case_1'], approve=True)
    assert result['outcome'] == 'complete', result
    assert result['examples'][0]['correctness'] == 'correct'
    assert len(cloud.requests) == 1


def test_port_projection_carries_captured_contract_and_identifier_map(ui):
    """UI-CD v3 CD1.14: Design inserts a Gate with a port's exact captured contract and proposes identifier
    maps from a server-side flag. Both come from the capture, also after the source revisions are gone."""
    from agentwiz import checks, workflow_service
    name = private_summary.ensure(ui.state.workshop, 'release')
    ports = request(ui, '/api/workshop/workflows/' + name)['node_ports']
    deid = ports['deid']['outputs']
    assert deid['mapping']['identifier_map'] is True
    assert deid['redacted_text']['identifier_map'] is False
    assert jsonio.decode_exact(deid['mapping']['contract']) == private_summary.MAPPING['contract']
    assert jsonio.decode_exact(deid['redacted_text']['contract']) == {'format': 'text'}
    assert ports['cloud_summary']['outputs']['summary']['identifier_map'] is False
    assert ports['restore']['outputs']['out']['identifier_map'] is False
    assert ports['gate']['inputs']['scan']['contract'] is None and ports['gate']['inputs']['scan']['identifier_map'] is False
    assert jsonio.decode_exact(ports['gate']['outputs']['released']['contract']) == {'format': 'text'}
    shape = jsonio.decode_exact(jsonio.encode_exact(private_summary.MAPPING['contract']))
    del shape['schema']['items']['properties']['original']
    shape['schema']['items']['required'] = ['placeholder']
    assert workflow_service.identifier_map(checks.parse_contract(shape)) is False


def one_repair(draft):
    definition = jsonio.decode_exact(draft['definition_text'])
    definition['steps'][0]['repair'] = {'max_repairs': 1}
    return {**draft, 'definition_text': jsonio.encode_exact(definition)}


def cloud_chain(ui, local, cloud, *, gate_node):
    """deid → (gate) → cloud summary → restore, with honest labels."""
    save_profile(ui, 'local', model='local', kind='openai_compat', base_url=local.url + '/v1', provider='')
    save_profile(ui, 'remote', model='remote', kind='openrouter', base_url=cloud.url + '/v1', provider='', api_key=KEY)
    save_roles(ui, {'local': 'local', 'cloud': 'remote'})
    grants = request(ui, '/api/workshop/destination-trust')
    send(ui, 'destination-trust', expected_revision=grants['revision'], trusted=True,
         destination={'kind': 'openai_compat', 'url': local.url + '/v1', 'model': 'local', 'provider': ''})
    deid = plan_save(ui, one_repair(model_draft('deid', 'local', {'text': port()},
                                     {'redacted_text': port(), 'mapping': {**private_summary.MAPPING, 'local_only': True}})))
    summary = plan_save(ui, model_draft('cloud', 'cloud', {'text': port()}, {'summary': port()}))
    restorer = plan_save(ui, send(ui, 'preview', starter='restore_placeholders', starter_version=2, key='restore')['draft'])
    nodes = {'deid': {'bindings': {'text': {'kind': 'input', 'port': 'document'}}},
             'cloud_summary': {'bindings': {'text': node('deid', 'redacted_text')}},
             'restore': {'bindings': {'text': node('cloud_summary', 'summary'), 'pairs': node('deid', 'mapping')}}}
    if gate_node:
        nodes['gate'] = gate(node('deid', 'redacted_text'), scan=node('deid', 'mapping'))
        nodes['cloud_summary']['bindings']['text'] = node('gate', 'released')
    definition = {'name': 'chain', 'purpose': '', 'inputs': {'document': port(restricted=True)}, 'constants': {},
                  'nodes': nodes, 'outputs': {'restored': {**port(restricted=True), 'binding': node('restore')}}}
    save_definition(ui, definition, {'deid': {'key': 'deid', 'revision': deid['durable_id']},
                                     'cloud_summary': {'key': 'cloud', 'revision': summary['durable_id']},
                                     'restore': {'key': 'restore', 'revision': restorer['durable_id']}})


def test_s3_cloud_summary_without_gate_is_refused_with_add_release_gate(ui, make_server):
    local, cloud = make_server(), make_server()
    cloud_chain(ui, local, cloud, gate_node=False)
    plan = send(ui, 'plans', action='workflow_run', request={'name': 'chain', 'inputs': {'document': 'Jane Example'}})
    refusal = next(issue for issue in plan['descriptor']['issues'] if issue['location'] == 'nodes.cloud_summary')
    assert refusal['severity'] == 'error' and refusal['remedy']['action'] == 'add_release_gate'
    assert local.requests == cloud.requests == []


def test_s7_contract_violation_after_repair_stops_before_downstream(ui, make_server):
    local, cloud = make_server(), make_server()
    cloud_chain(ui, local, cloud, gate_node=True)
    bad = answer('{"redacted_text":"[PERSON_1]","mapping":"Jane Example"}')
    local.reply(bad, answer('{"redacted_text":"[PERSON_1]","mapping":"still not a list"}'))
    selection = {'name': 'chain', 'inputs': {'document': 'Jane Example'}}
    plan = send(ui, 'plans', action='workflow_run', request=selection)
    ident = send(ui, 'workflows/chain/run', request=selection, expected_plan=plan['expected_plan'],
                 ticket=send(ui, 'tickets')['ticket'], confirmations=plan['required_confirmations'])['operation_id']
    route = '/api/workshop/workflow-jobs/' + ident
    wait_until(lambda: request(ui, route)['outcome'] != 'running')
    result = request(ui, route)
    assert result['nodes']['deid']['outcome'] != 'complete' and result['outcome'] != 'complete'
    assert result['model_attempts'] == 2 and len(local.requests) == 2 and cloud.requests == []
    assert result['nodes']['gate']['outcome'] == result['nodes']['cloud_summary']['outcome'] == 'not_run'
    assert result['nodes']['restore']['outcome'] == 'not_run'


def test_s8_rewritten_placeholder_is_absent_and_judged_incorrect(ui, make_server):
    local = make_server()
    save_profile(ui, 'local', model='local', kind='openai_compat', base_url=local.url + '/v1', provider='')
    save_roles(ui, {'local': 'local'})
    grants = request(ui, '/api/workshop/destination-trust')
    send(ui, 'destination-trust', expected_revision=grants['revision'], trusted=True,
         destination={'kind': 'openai_compat', 'url': local.url + '/v1', 'model': 'local', 'provider': ''})
    deid = plan_save(ui, model_draft('deid', 'local', {'text': port()},
                                     {'redacted_text': port(), 'mapping': {**private_summary.MAPPING, 'local_only': True}}))
    summary = plan_save(ui, model_draft('summary', 'local', {'text': port()}, {'summary': port(restricted=True)}))
    restorer = plan_save(ui, send(ui, 'preview', starter='restore_placeholders', starter_version=2, key='restore')['draft'])
    definition = {'name': 'local', 'purpose': '', 'inputs': {'document': port(restricted=True)}, 'constants': {},
                  'nodes': {'deid': {'bindings': {'text': {'kind': 'input', 'port': 'document'}}},
                            'summarize': {'bindings': {'text': node('deid', 'redacted_text')}},
                            'restore': {'bindings': {'text': node('summarize', 'summary'), 'pairs': node('deid', 'mapping')}}},
                  'outputs': {'restored': {**port(restricted=True), 'binding': node('restore')}}}
    example = {'id': 's8', 'version': 1, 'verified': True, 'inputs': {'document': 'Jane Example has fatigue.'},
               'expectation': {'kind': 'text', 'assertions': [
                   {'output': 'restored', 'operation': 'excludes_pattern', 'pattern': 'bracket_upper'},
                   {'output': 'restored', 'operation': 'includes', 'values': ['Jane Example'], 'case': 'sensitive'}]}}
    save_definition(ui, definition, {'deid': {'key': 'deid', 'revision': deid['durable_id']},
                                     'summarize': {'key': 'summary', 'revision': summary['durable_id']},
                                     'restore': {'key': 'restore', 'revision': restorer['durable_id']}}, [example])
    local.reply(answer(jsonio.encode_exact({'redacted_text': '[PERSON_1] has fatigue.',
                                            'mapping': [{'placeholder': '[PERSON_1]', 'original': 'Jane Example'}]})),
                answer('{"summary":"The patient has fatigue."}'))
    result = run_examples(ui, 'local', ['s8'])
    row = result['examples'][0]
    assert row['outcome'] == 'complete' and row['correctness'] == 'incorrect'
    trace = jsonio.encode_exact(request(ui, result['route'] + '?node=0:restore'))
    assert '"absent":{"items":["[PERSON_1]"]' in trace


def test_s9_stop_around_restore_dispatches_nothing_after_stop(monkeypatch):
    from agentwiz import executor
    calls = []
    original = transforms.execute

    def watched(*args):
        calls.append(args[0])
        return original(*args)
    monkeypatch.setattr(transforms, 'execute', watched)

    class Token:
        cancelled = True

    class Run:
        token = Token()
    context = type('Ctx', (), {'block': type('B', (), {'step': type('S', (), {
        'raw': {'operation': 'restore_placeholders', 'operation_version': 1, 'parameters': RESTORE}, 'output': 'out'})()})(),
        'run': Run(), 'values': {'text': '[PERSON_1]', 'pairs': PAIRS}})()
    monkeypatch.setattr(executor, '_base', lambda ctx, kind, issues: {'kind': kind})
    monkeypatch.setattr(executor, '_stopped', lambda ctx, result: {**result, 'outcome': 'stopped'})
    assert executor._execute_transform(context)['outcome'] == 'stopped'
    assert calls == []
