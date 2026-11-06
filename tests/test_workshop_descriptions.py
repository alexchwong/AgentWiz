"""Headless acceptance for block_metadata_refactor spec v2: operating saved revisions (M01, M02) and
revision descriptions (M03-M07). Offline: the model is the local test server; no MCP service is used."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from agentwiz import jsonio, workshop_assets, workshop_descriptions, workshop_service
from conftest import (HTTPFailure, Reply, answer, base, failure_of, request, run_block, save_contract, save_profile,
                      save_roles, strict_loads, switch_history, wait_until)

SECTIONS = ('use', 'exclusions', 'effects', 'limitations', 'missing_data', 'failures')


def send(ui, route, **body):
    return request(ui, '/api/workshop/' + route, {'format_version': 1, **body})


def put(ui, path, body):
    req = urllib.request.Request(base(ui) + '/api/workshop/' + path, method='PUT',
                                 data=json.dumps({'format_version': 1, **body}).encode(),
                                 headers={'Content-Type': 'application/json', 'X-Session-Token': ui.token})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10) as resp:
            return strict_loads(resp.read())
    except urllib.error.HTTPError as e:
        raise HTTPFailure(e.code, strict_loads(e.read())) from None


def plan_save(ui, draft, expected=None):
    body = {'draft': draft, 'expected_identity': expected}
    plan = send(ui, 'plans', action='save', request=body)
    return send(ui, 'save', **body, expected_plan=plan['expected_plan'],
                confirmations=[{'kind': r['kind'], 'digest': r['digest']} for r in plan['required_confirmations']])


def start_planned(ui, action, body):
    ticket = send(ui, 'tickets')['ticket']
    body = {**body, 'ticket': ticket}
    plan = send(ui, 'plans', action=action, request=body)
    return send(ui, action, **body, expected_plan=plan['expected_plan'],
                confirmations=[{'kind': r['kind'], 'digest': r['digest']} for r in plan['required_confirmations']])


def completed(ui, ident):
    wait_until(lambda: request(ui, '/api/workshop/batches/' + ident)['state'] != 'running')
    return request(ui, '/api/workshop/batches/' + ident)


def counter(ui, key='counter'):
    """Two retained revisions of one script block with observably different behaviour."""
    draft = send(ui, 'preview', starter='count_text', key=key)['draft']
    first = plan_save(ui, draft)
    alternate = send(ui, 'preview', draft=draft, edit={'kind': 'starter_parameters',
                                                       'parameters': {'unit': 'characters'}})['draft']
    second = plan_save(ui, alternate, first['base_digest'])
    return first, second


def envelope(sections=None, ports=None, note=None):
    sections = sections or {k: ('Counts the supplied text.' if k == 'use' else '') for k in SECTIONS}
    value = {'format_version': 1, 'action': 'describe',
             'payload': {'sections': sections, 'port_summaries': ports or {'inputs': {}, 'outputs': {}}}}
    if note is not None:
        value['note'] = note
    return value


def patch(payload, note=None):
    value = {'format_version': 1, 'action': 'describe', 'payload': payload}
    if note is not None:
        value['note'] = note
    return value


def reply_with(server, value):
    server.reply(answer(jsonio.encode_exact({'proposal': value})))


def describer(ui, server):
    save_profile(ui, 'p', model='m', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'write_block_description': 'p', 'fast': 'p'})


def describe(ui, key, revision, mode='generate', **extra):
    body = {'action': 'describe', 'mode': mode, 'key': key, 'revision': revision, **extra}
    return completed(ui, start_planned(ui, 'assist', body)['batch_id'])


def projection(ui, key, revision):
    return request(ui, f'/api/workshop/blocks/{key}/revisions/{revision}')['standard_description']


def manual(ui, key, revision, sections=None, ports=None, token=None, basis=None):
    current = projection(ui, key, revision)
    body = {'expected_token': current['token'] if token is None else token,
            'basis': current['basis'] if basis is None else basis,
            'sections': sections or {k: ('Manual use.' if k == 'use' else '') for k in SECTIONS},
            'port_summaries': ports or {'inputs': {}, 'outputs': {}}}
    return put(ui, f'blocks/{key}/revisions/{revision}/description', body)


def executable_bytes(ui, key):
    root = ui.workspace.path / 'blocks' / key
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*')
            if p.is_file() and 'descriptions' not in p.parts}


# --- M01 ---------------------------------------------------------------------------------------------------
def test_retained_revisions_run_independently_with_provenance(ui):
    first, second = counter(ui)
    rows = request(ui, '/api/workshop/blocks/counter/revisions')
    assert rows['current'] == second['durable_id']
    numbers = {r['id']: r['revision_number'] for r in rows['revisions']}
    for saved, expected in ((first, '2'), (second, '3')):
        batch = completed(ui, start_planned(ui, 'test', {'key': 'counter', 'revision': saved['durable_id'],
                                                         'inputs': {'text': 'a b'}})['batch_id'])
        row = batch['rows'][0]
        assert row['outputs']['out']['text'] == expected
        assert row['block_revision_id'] == saved['durable_id']
        assert row['block_revision_number'] == numbers[saved['durable_id']]
        assert batch['plan']['candidates'][0]['block_revision_id'] == saved['durable_id']
        held = request(ui, '/api/block_runs/' + row['run_id'])['result']['workshop']
        assert held['block_revision_id'] == saved['durable_id']
        assert held['block_revision_number'] == numbers[saved['durable_id']]
    assert request(ui, '/api/workshop/blocks/counter/revisions')['current'] == second['durable_id']
    # Compare two compatible saved revisions; each arm carries its own provenance.
    example = {'id': 'e1', 'version': 1, 'label': 'Two words', 'inputs': {'text': 'a b'},
               'expectation': {'kind': 'review', 'criterion': ''}}
    batch = completed(ui, start_planned(ui, 'batches', {'candidates': [
        {'key': 'counter', 'revision': first['durable_id']}, {'key': 'counter', 'revision': second['durable_id']}],
        'examples': [example]})['batch_id'])
    by_arm = {r['arm']: r for r in batch['rows']}
    assert by_arm[0]['outputs']['out']['text'] == '2' and by_arm[1]['outputs']['out']['text'] == '3'
    assert by_arm[0]['block_revision_id'] == first['durable_id']
    assert by_arm[1]['block_revision_id'] == second['durable_id']
    # A dirty draft keeps the draft path and carries no saved-revision provenance.
    draft = send(ui, 'preview', key='counter')['draft']
    batch = completed(ui, start_planned(ui, 'test', {'draft': draft, 'inputs': {'text': 'a b'}})['batch_id'])
    assert 'block_revision_id' not in batch['rows'][0]
    # The current-only run endpoint is unchanged.
    from conftest import result_of
    assert result_of(run_block(ui, 'counter', {'text': 'a b'}))['outputs']['out']['text'] == '3'


def test_saved_selectors_refuse_authoring_fields_and_bind_the_durable_id(ui):
    first, second = counter(ui)
    for extra in ({'edit': {'kind': 'starter_parameters', 'parameters': {'unit': 'characters'}}},
                  {'duplicate_key': 'copy'}, {'display_name': 'x'}, {'support': []}, {'draft': {}}):
        refusal = failure_of(lambda: send(ui, 'plans', action='test', request={
            'key': 'counter', 'revision': first['durable_id'], 'inputs': {'text': 'a'}, **extra}))
        assert refusal.status == 400 and refusal.body['issues'][0]['location'] == next(iter(extra))
    # Two byte-identical generations stay distinguishable, and a plan survives a default-pointer change.
    same = send(ui, 'preview', key='counter')['draft']
    third = plan_save(ui, same, same['base_digest'])
    plan_a = send(ui, 'plans', action='test', request={'key': 'counter', 'revision': second['durable_id'],
                                                       'inputs': {'text': 'a'}})
    plan_b = send(ui, 'plans', action='test', request={'key': 'counter', 'revision': third['durable_id'],
                                                       'inputs': {'text': 'a'}})
    assert plan_a['descriptor']['candidates'][0]['source_digest'] == plan_b['descriptor']['candidates'][0]['source_digest']
    assert plan_a['expected_plan'] != plan_b['expected_plan']
    assert 'base_digest' not in plan_a['descriptor']['candidates'][0]['draft']
    body = {'key': 'counter', 'revision': second['durable_id'], 'inputs': {'text': 'a'}}
    plan = send(ui, 'plans', action='test', request=body)
    moved = send(ui, 'preview', key='counter')['draft']
    plan_save(ui, moved, moved['base_digest'])                                  # the default moves on
    ticket = send(ui, 'tickets')['ticket']
    started = send(ui, 'test', **body, ticket=ticket, expected_plan=plan['expected_plan'], confirmations=[])
    assert completed(ui, started['batch_id'])['state'] == 'complete'


def test_restore_to_draft_publishes_a_new_default(ui):
    first, second = counter(ui)
    selected = send(ui, 'preview', key='counter', revision=first['durable_id'])
    assert selected['selector'] == {'key': 'counter', 'revision': first['durable_id']}
    assert selected['draft']['base_digest'] == second['base_digest']
    restored = plan_save(ui, selected['draft'], selected['draft']['base_digest'])
    current = send(ui, 'preview', key='counter')
    assert current['selector']['revision'] == restored['durable_id']
    assert current['revision_number'] == 3
    batch = completed(ui, start_planned(ui, 'test', {'key': 'counter', 'inputs': {'text': 'a b'}})['batch_id'])
    assert batch['rows'][0]['outputs']['out']['text'] == '2'


# --- M02 ---------------------------------------------------------------------------------------------------
def script_draft(key, inputs, source='print(\'{"out": 1}\')\n', origin='manual'):
    obj = {'format_version': 3, 'name': key, 'inputs': {n: {'contract': {'format': 'text'}} for n in inputs},
           'outputs': {'out': {'contract': {'format': 'json'}}},
           'steps': [{'id': 'run', 'kind': 'script', 'file': 'scripts/main.py', 'timeout_s': 5}]}
    return {'key': key, 'definition_text': jsonio.encode_exact(obj), 'files': {'scripts/main.py': source},
            'metadata': {'support': [], 'origin': origin, 'effects': 'read-only'}}


def test_old_interfaces_snapshot_contracts_and_example_diagnostics(ui):
    first = plan_save(ui, script_draft('shapes', ['a']))
    second = plan_save(ui, script_draft('shapes', ['b']), first['base_digest'])
    old = send(ui, 'preview', key='shapes', revision=first['durable_id'])
    new = send(ui, 'preview', key='shapes', revision=second['durable_id'])
    assert old['input_ports'] == ['a'] and new['input_ports'] == ['b']
    example = {'id': 'e1', 'version': 1, 'label': 'Shared', 'inputs': {'a': 'x'},
               'expectation': {'kind': 'review', 'criterion': ''}}
    refusal = failure_of(lambda: send(ui, 'plans', action='batches', request={'candidates': [
        {'key': 'shapes', 'revision': first['durable_id']}, {'key': 'shapes', 'revision': second['durable_id']}],
        'examples': [example]}))
    assert refusal.status == 400 and refusal.body['code'] == 'example_incompatible'
    message = refusal.body['issues'][0]['message']
    assert 'Shared' in message and 'revision 2' in message and 'missing b' in message and 'extra a' in message


def test_saved_preview_reads_snapshots_while_runs_refuse_damaged_pins(ui, server):
    saved = save_contract(ui, 'report', '{"format": "text", "min_length": 1}')
    pin = {'name': 'report', 'revision': 1, 'digest': saved['digest']}
    draft = send(ui, 'preview', starter='format_report', key='reporter')['draft']
    definition = jsonio.decode_exact(draft['definition_text'])
    definition['outputs']['out']['contract'] = pin
    draft['definition_text'] = jsonio.encode_exact(definition)
    first = plan_save(ui, draft)
    path = ui.workspace.path / 'contracts' / 'report.json'
    good = path.read_bytes()
    path.write_bytes(b'{ damaged')
    preview = send(ui, 'preview', key='reporter', revision=first['durable_id'])
    assert preview['valid'] and preview['run_issues']
    assert preview['contracts']['outputs']['out']['rules'] == {'min_length': '1'}
    assert preview['standard_description']['basis']
    refusal = failure_of(lambda: send(ui, 'plans', action='test', request={
        'key': 'reporter', 'revision': first['durable_id'], 'inputs': {'text': 'x'}}))
    assert refusal.status == 409 and refusal.body['code'] == 'block_invalid'
    # Describe still works from the intact snapshot.
    describer(ui, server)
    reply_with(server, envelope(ports={'inputs': {'text': 'Report text.'}, 'outputs': {}}))
    batch = describe(ui, 'reporter', first['durable_id'])
    assert batch['outcome'] == 'proposal', batch
    path.write_bytes(good)


def test_pruned_selection_before_start_and_after_capture(ui, monkeypatch):
    monkeypatch.setattr(workshop_assets, 'REVISION_LIMIT', 2)
    first, second = counter(ui)
    body = {'key': 'counter', 'revision': first['durable_id'], 'inputs': {'text': 'a b'}}
    pruning = {'done': False}

    def prune(batch_id, row):
        if not pruning['done']:
            pruning['done'] = True
            for _ in range(2):
                draft = send(ui, 'preview', key='counter')['draft']
                plan_save(ui, draft, draft['base_digest'])
    monkeypatch.setattr(workshop_service, 'before_trial_dispatch', prune)
    batch = completed(ui, start_planned(ui, 'test', body)['batch_id'])
    assert pruning['done'] and batch['rows'][0]['outputs']['out']['text'] == '2'      # the admitted copy ran
    ids = [r['id'] for r in request(ui, '/api/workshop/blocks/counter/revisions')['revisions']]
    assert first['durable_id'] not in ids
    assert failure_of(lambda: send(ui, 'plans', action='test', request=body)).status == 404
    assert failure_of(lambda: request(ui, f'/api/workshop/blocks/counter/revisions/{first["durable_id"]}')).status == 404
    for bad in ('0' * 32, 'not-an-id'):
        assert failure_of(lambda: send(ui, 'preview', key='counter', revision=bad)).status == 404


def test_old_manual_script_keeps_its_provenance_under_a_generated_head(ui):
    manual_rev = plan_save(ui, script_draft('lineage', []))
    generated = script_draft('lineage', [], source='print(\'{"out": 2}\')\n', origin='llm_generated')
    plan_save(ui, generated, manual_rev['base_digest'])
    plan = send(ui, 'plans', action='test', request={'key': 'lineage', 'revision': manual_rev['durable_id'], 'inputs': {}})
    assert 'generated_source' not in {r['kind'] for r in plan['required_confirmations']}
    assert plan['descriptor']['candidates'][0]['draft']['metadata']['origin'] == 'manual'
    batch = completed(ui, start_planned(ui, 'test', {'key': 'lineage', 'revision': manual_rev['durable_id'],
                                                     'inputs': {}})['batch_id'])
    assert batch['rows'][0]['outputs']['out']['text'] == '1'


def test_unsupported_executable_formats_have_no_basis():
    assert workshop_assets.description_basis('k', '0' * 32, {'asset_digest': 'a', 'contracts_digest': 'c'},
                                             {'format_version': 1}) is None
    assert workshop_assets.description_basis('k', '0' * 32, {'asset_digest': 'a'}, {'format_version': 3}) is None
    one = workshop_assets.description_basis('k', '0' * 32, {'asset_digest': 'a', 'contracts_digest': 'c'},
                                            {'format_version': 3})
    two = workshop_assets.description_basis('k', '1' * 32, {'asset_digest': 'a', 'contracts_digest': 'c'},
                                            {'format_version': 3})
    assert one and two and one != two


# --- M03, M04 ----------------------------------------------------------------------------------------------
def extractor(ui, key='extractor'):
    draft = send(ui, 'preview', starter='extract_fields', key=key, role='fast')['draft']
    first = plan_save(ui, draft)
    draft = send(ui, 'preview', key=key)['draft']
    draft['metadata']['revision_name'] = 'second'
    second = plan_save(ui, draft, draft['base_digest'])
    return first, second


EXTRACTOR_PORTS = {'inputs': {'text': 'Order text.'}, 'outputs': {'out': 'Product and explicit quantity.'}}


def test_describe_and_manual_edit_write_only_the_selected_revision(ui, server):
    save_profile(ui, 'p', model='m', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'fast': 'p'})
    first, second = extractor(ui)
    before = executable_bytes(ui, 'extractor')
    prepared = send(ui, 'preview', key='extractor', inputs={'text': 'three notebooks'})['prepared']
    # M04: unassigned role refuses before dispatch; manual editing needs no model.
    refusal = failure_of(lambda: send(ui, 'plans', action='assist', request={
        'action': 'describe', 'mode': 'generate', 'key': 'extractor', 'revision': first['durable_id']}))
    assert refusal.status == 409 and refusal.body['code'] == 'role_unresolved'
    assert server.requests == []
    assert projection(ui, 'extractor', first['durable_id'])['status'] == 'missing'
    written = manual(ui, 'extractor', first['durable_id'], ports=EXTRACTOR_PORTS)
    assert written['status'] == 'saved' and written['origin'] == 'manual'
    assert written['port_summaries'] == EXTRACTOR_PORTS
    # M03: Generate and accept for the older revision, then for the current one.
    save_roles(ui, {'fast': 'p', 'write_block_description': 'p'})
    reply_with(server, envelope(ports=EXTRACTOR_PORTS))
    batch = describe(ui, 'extractor', first['durable_id'])
    assert batch['outcome'] == 'proposal' and batch['plan']['destination']['locality'] == 'local'
    proposal = batch['proposal']
    assert proposal['base_token'] == written['token']
    accepted = put(ui, f'blocks/extractor/revisions/{first["durable_id"]}/description',
                   {'proposal_id': proposal['proposal_id'], 'expected_token': written['token']})
    assert accepted['status'] == 'saved' and accepted['origin'] == 'model'
    assert projection(ui, 'extractor', second['durable_id'])['status'] == 'missing'
    batch = describe(ui, 'extractor', second['durable_id'])
    put(ui, f'blocks/extractor/revisions/{second["durable_id"]}/description',
        {'proposal_id': batch['proposal']['proposal_id'], 'expected_token': None})
    assert projection(ui, 'extractor', second['durable_id'])['origin'] == 'model'
    assert projection(ui, 'extractor', first['durable_id'])['origin'] == 'model'
    # Nothing executable changed: head, generations, assets, and the prepared request.
    assert executable_bytes(ui, 'extractor') == before
    assert len(request(ui, '/api/workshop/blocks/extractor/revisions')['revisions']) == 2
    assert send(ui, 'preview', key='extractor', inputs={'text': 'three notebooks'})['prepared'] == prepared
    assert sorted(p.name for p in (ui.workspace.path / 'blocks/extractor/.workshop/descriptions').iterdir()) == \
        sorted([first['durable_id'] + '.json', second['durable_id'] + '.json'])


def test_describe_uses_only_the_dedicated_role_and_replans_on_destination_change(ui, server):
    save_profile(ui, 'p', model='m', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_profile(ui, 'q', model='other', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'fast': 'q', 'assistant': 'q', 'write_block_description': 'p'})
    first, _second = extractor(ui)
    reply_with(server, envelope(ports=EXTRACTOR_PORTS))
    describe(ui, 'extractor', first['durable_id'])
    assert [r['body']['model'] for r in server.requests] == ['m']
    body = {'action': 'describe', 'mode': 'generate', 'key': 'extractor', 'revision': first['durable_id']}
    plan = send(ui, 'plans', action='assist', request=body)
    assert plan['descriptor']['destination']['model'] == 'm'
    save_roles(ui, {'fast': 'q', 'assistant': 'q', 'write_block_description': 'q'})
    ticket = send(ui, 'tickets')['ticket']
    refusal = failure_of(lambda: send(ui, 'assist', **body, ticket=ticket, expected_plan=plan['expected_plan'],
                                      confirmations=[]))
    assert refusal.status == 409 and refusal.body['code'] == 'confirmation_required'
    assert refusal.body['plan']['descriptor']['destination']['model'] == 'other'
    for extra in ({'role': 'p'}, {'draft': {}}, {'context': 'x'}, {'evidence': {}}, {'settings_text': {}}):
        assert failure_of(lambda: send(ui, 'plans', action='assist', request={**body, **extra})).status in (400, 409)


# --- M05 ---------------------------------------------------------------------------------------------------
def test_refine_failures_and_conflicts_never_overwrite_accepted_prose(ui, server, monkeypatch):
    describer(ui, server)
    first, second = extractor(ui)
    rev = first['durable_id']
    path = f'blocks/extractor/revisions/{rev}/description'
    reply_with(server, envelope(ports=EXTRACTOR_PORTS))
    generated = describe(ui, 'extractor', rev)['proposal']
    # Refine one field; everything else is unchanged.
    reply_with(server, patch({'sections': {'limitations': 'Misses implied quantities.'}}))
    refined = describe(ui, 'extractor', rev, 'refine', proposal_id=generated['proposal_id'],
                       feedback='Mention implied quantities.')
    assert refined['outcome'] == 'proposal' and refined['proposal']['changed'] == ['limitations']
    assert refined['proposal']['proposal_id'] != generated['proposal_id']
    assert {k: v for k, v in refined['proposal']['sections'].items() if k != 'limitations'} == \
        {k: v for k, v in generated['sections'].items() if k != 'limitations'}
    assert refined['proposal']['port_summaries'] == generated['port_summaries']
    # A null summary removes it; an empty patch needs a note; with a note it leaves the proposal intact.
    reply_with(server, patch({'port_summaries': {'outputs': {'out': None}}}))
    dropped = describe(ui, 'extractor', rev, 'refine', proposal_id=refined['proposal']['proposal_id'], feedback='x')
    assert dropped['proposal']['port_summaries']['outputs'] == {}
    reply_with(server, patch({}))
    empty = describe(ui, 'extractor', rev, 'refine', proposal_id=refined['proposal']['proposal_id'], feedback='y')
    assert empty['outcome'] == 'description_invalid' and empty['proposal'] is None
    assert empty['rows'][0]['description_issues'][0]['location'] == 'proposal.note'
    reply_with(server, patch({}, note='Changing behaviour is outside this action.'))
    unchanged = describe(ui, 'extractor', rev, 'refine', proposal_id=refined['proposal']['proposal_id'], feedback='z')
    assert unchanged['outcome'] == 'unchanged'
    assert unchanged['proposal']['proposal_id'] == refined['proposal']['proposal_id']
    assert unchanged['proposal']['note'] == 'Changing behaviour is outside this action.'
    # Schema-invalid output gets the single existing repair; post-merge invalid output is a readable refusal.
    calls = len(server.requests)
    reply_with(server, envelope(ports={'inputs': {'unknown_port': 'x'}, 'outputs': {}}))
    bad = describe(ui, 'extractor', rev)
    assert bad['proposal'] is None and len(server.requests) == calls + 2
    calls = len(server.requests)
    reply_with(server, envelope(sections={k: ('Bad\u0001control' if k == 'use' else '') for k in SECTIONS}))
    late = describe(ui, 'extractor', rev)
    assert late['state'] == 'complete' and late['outcome'] == 'description_invalid' and late['proposal'] is None
    assert late['rows'][0]['description_issues'][0]['location'] == 'proposal.payload.sections.use'
    assert len(server.requests) == calls + 1
    # Child run results evicted and no history: the proposal still refines and saves.
    from agentwiz import history, runs
    monkeypatch.setattr(ui.state.store, 'run_envelope', lambda run_id: (_ for _ in ()).throw(runs.NotHeld()))
    monkeypatch.setattr(ui.state.history, 'read_run', lambda run_id: (_ for _ in ()).throw(history.NoSuchRun(run_id)))
    reply_with(server, patch({'sections': {'failures': 'Check the order text.'}}))
    again = describe(ui, 'extractor', rev, 'refine', proposal_id=refined['proposal']['proposal_id'], feedback='w')
    assert again['outcome'] == 'proposal'
    monkeypatch.undo()
    # Wrong target, expired proposal and stale token are refused; accepted prose stays.
    wrong = failure_of(lambda: put(ui, f'blocks/extractor/revisions/{second["durable_id"]}/description',
                                   {'proposal_id': again['proposal']['proposal_id'], 'expected_token': None}))
    assert wrong.status == 409 and wrong.body['code'] == 'proposal_target'
    expired = failure_of(lambda: put(ui, path, {'proposal_id': 'f' * 32, 'expected_token': None}))
    assert expired.status == 410
    saved = put(ui, path, {'proposal_id': again['proposal']['proposal_id'], 'expected_token': None})
    stale = failure_of(lambda: put(ui, path, {'proposal_id': generated['proposal_id'], 'expected_token': None}))
    assert stale.status == 409 and stale.body['code'] == 'stale'
    stale = failure_of(lambda: manual(ui, 'extractor', rev, token='0' * 64))
    assert stale.status == 409
    assert projection(ui, 'extractor', rev)['token'] == saved['token']
    # Invalid submissions are located 400s.
    for sections, ports in (({**{k: '' for k in SECTIONS}}, None),
                            ({**{k: '' for k in SECTIONS}, 'use': 'x' * 601}, None),
                            ({**{k: '' for k in SECTIONS}, 'use': 'ok', 'operation': 'x'}, None),
                            (None, {'inputs': {'nope': 'x'}, 'outputs': {}}),
                            (None, {'inputs': {'text': ''}, 'outputs': {}})):
        refusal = failure_of(lambda: manual(ui, 'extractor', rev, sections=sections, ports=ports))
        assert refusal.status == 400 and refusal.body['code'] == 'description_invalid'
        assert refusal.body['issues'][0]['location']
    for body in ({'expected_token': None}, {'proposal_id': 'f' * 32, 'expected_token': None, 'basis': 'x'},
                 {'expected_token': 'nothex', 'basis': '0' * 64, 'sections': {}, 'port_summaries': {}}):
        assert failure_of(lambda: put(ui, path, body)).status == 400
    assert projection(ui, 'extractor', rev)['token'] == saved['token']


def test_concurrent_description_writes_cannot_both_win(ui):
    first, _second = counter(ui)
    rev = first['durable_id']
    current = projection(ui, 'counter', rev)
    results = []
    assert current['token'] is None

    def write_none(text):
        body = {'expected_token': None, 'basis': current['basis'],
                'sections': {k: (text if k == 'use' else '') for k in SECTIONS},
                'port_summaries': {'inputs': {}, 'outputs': {}}}
        try:
            results.append(('ok', put(ui, f'blocks/counter/revisions/{rev}/description', body)))
        except HTTPFailure as exc:
            results.append((exc.status, exc.body['code']))
    threads = [threading.Thread(target=write_none, args=(t,)) for t in ('First writer', 'Second writer')]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    winners = [r for r in results if r[0] == 'ok']
    assert len(winners) == 1, results
    assert [r for r in results if r[0] != 'ok'][0][0] in (409, 503)
    assert projection(ui, 'counter', rev)['sections']['use'] == winners[0][1]['sections']['use']


def test_cancelled_describe_exposes_no_candidate(ui, server):
    from conftest import DONE, delta
    describer(ui, server)
    first, _second = extractor(ui)
    body = jsonio.encode_exact({'proposal': envelope(ports=EXTRACTOR_PORTS)})
    server.reply(Reply(body=[delta(body)], headers={'Content-Type': 'text/event-stream'}, hold=True,
                       after_release=[delta(finish='stop'), DONE]))
    started = start_planned(ui, 'assist', {'action': 'describe', 'mode': 'generate', 'key': 'extractor',
                                           'revision': first['durable_id']})
    server.wait_holding(1)
    send(ui, f'batches/{started["batch_id"]}/stop')
    server.release.set()
    batch = completed(ui, started['batch_id'])
    assert batch['proposal'] is None and batch['outcome'] == 'stopped'
    assert projection(ui, 'extractor', first['durable_id'])['status'] == 'missing'


# --- M06 ---------------------------------------------------------------------------------------------------
def test_outbound_context_is_allowlisted_redacted_and_session_only(ui, server, monkeypatch):
    describer(ui, server)
    switch_history(ui, True)
    obj = {'format_version': 3, 'name': 'helper', 'description': 'Adds a helper constant',
           'inputs': {'text': {'description': 'Words to count', 'contract': {'format': 'text'}}},
           'outputs': {'out': {'contract': {'format': 'json'}}},
           'steps': [{'id': 'run', 'kind': 'script', 'file': 'scripts/main.py', 'timeout_s': 5}]}
    source = '# MAINSOURCEMARKER uses CREDENTIALVALUE123\nimport helper\nprint(\'{"out": 1}\')\n'
    draft = {'key': 'helper', 'definition_text': jsonio.encode_exact(obj),
             'files': {'scripts/main.py': source, 'scripts/helper.py': 'SUPPORTSECRETMARKER = 1\n'},
             'metadata': {'support': ['scripts/helper.py'], 'origin': 'manual', 'effects': 'read-only'}}
    saved = plan_save(ui, draft)
    # The credential becomes known after publication (publication itself refuses a known credential).
    monkeypatch.setattr(ui.state, 'credentials', lambda: ['CREDENTIALVALUE123'])
    runs_before = request(ui, '/api/runs')['runs']
    comparisons = ui.workspace.path / 'workshop' / 'comparisons'
    manifests_before = sorted(comparisons.iterdir()) if comparisons.exists() else []
    reply_with(server, envelope(ports={'inputs': {'text': 'Words.'}, 'outputs': {}}))
    request_body = {'action': 'describe', 'mode': 'generate', 'key': 'helper', 'revision': saved['durable_id']}
    plan = send(ui, 'plans', action='assist', request=request_body)
    batch = describe(ui, 'helper', saved['durable_id'])
    sent = json.dumps(server.requests[-1]['body'])
    assert 'MAINSOURCEMARKER' in sent and 'Words to count' in sent
    assert 'SUPPORTSECRETMARKER' not in sent and 'CREDENTIALVALUE123' not in sent
    # Refine feedback reaches the model but never the public plan or batch.
    reply_with(server, patch({'sections': {'limitations': 'Depends on a helper module.'}}))
    refine = {'action': 'describe', 'mode': 'refine', 'key': 'helper', 'revision': saved['durable_id'],
              'proposal_id': batch['proposal']['proposal_id'], 'feedback': 'FEEDBACKMARKER please'}
    refine_plan = send(ui, 'plans', action='assist', request=refine)
    refined = describe(ui, 'helper', saved['durable_id'], 'refine', proposal_id=refine['proposal_id'],
                       feedback=refine['feedback'])
    assert 'FEEDBACKMARKER' in json.dumps(server.requests[-1]['body'])
    for public in (plan, refine_plan, batch, refined, request(ui, '/api/workshop/batches/' + refined['id'])):
        text = json.dumps(public)
        for marker in ('MAINSOURCEMARKER', 'FEEDBACKMARKER', 'context', 'run_id', 'raw_answer', 'prepared'):
            assert marker not in text, marker
    # Session only even with Save runs on; nothing executed.
    assert request(ui, '/api/runs')['runs'] == runs_before
    assert (sorted(comparisons.iterdir()) if comparisons.exists() else []) == manifests_before
    assert not (ui.workspace.path / 'blocks/helper/__pycache__').exists()


def test_credentials_in_field_names_refuse_dispatch(ui, server, monkeypatch):
    describer(ui, server)
    saved = plan_save(ui, script_draft('leaky', ['secretportname99']))
    monkeypatch.setattr(ui.state, 'credentials', lambda: ['secretportname99'])
    refusal = failure_of(lambda: send(ui, 'plans', action='assist', request={
        'action': 'describe', 'mode': 'generate', 'key': 'leaky', 'revision': saved['durable_id']}))
    assert refusal.status == 409 and refusal.body['code'] == 'credential_in_context'
    assert server.requests == []


def test_mcp_binding_structure_omits_literals():
    structure = workshop_descriptions.binding_structure(
        {'query': '{{inputs.text}}', 'limit': 5, 'options': {'mode': 'SECRETLITERAL', 'tags': ['a', '{{inputs.tag}}']},
         'title': 'Find {{inputs.text}} now'})
    assert structure == {'query': {'input': 'text'}, 'limit': '<literal>',
                         'options': {'mode': '<literal>', 'tags': ['<literal>', {'input': 'tag'}]},
                         'title': {'template_inputs': ['text']}}
    assert 'SECRETLITERAL' not in json.dumps(structure)


def test_tool_block_describe_never_contacts_mcp(ui, server):
    describer(ui, server)
    obj = {'format_version': 3, 'name': 'searcher', 'inputs': {'text': {'contract': {'format': 'text'}}},
           'outputs': {'out': {'contract': {'format': 'json'}}},
           'steps': [{'id': 'call', 'kind': 'tool', 'server': 'nowhere', 'tool': 'search',
                      'arguments': {'q': '{{inputs.text}}', 'key': 'LITERALVALUE'}, 'output': 'out'}]}
    saved = plan_save(ui, {'key': 'searcher', 'definition_text': jsonio.encode_exact(obj), 'files': {},
                           'metadata': {'origin': 'manual', 'effects': 'unknown'}})
    reply_with(server, envelope(ports={'inputs': {'text': 'Query.'}, 'outputs': {}}))
    batch = describe(ui, 'searcher', saved['durable_id'])
    assert batch['outcome'] == 'proposal'
    sent = json.dumps(server.requests[-1]['body'])
    assert 'LITERALVALUE' not in sent and 'search' in sent and 'unavailable' in sent


# --- M07 ---------------------------------------------------------------------------------------------------
def test_legacy_fallback_new_revisions_and_historical_bytes(ui):
    first, second = counter(ui)
    folder = ui.workspace.path / 'blocks/counter/.workshop/revisions' / first['durable_id']
    metadata = jsonio.decode_file_exact((folder / 'workshop.json').read_bytes())
    metadata['description_sections'] = {k: ('Legacy use.' if k == 'use' else 'x' * 700 if k == 'failures' else '')
                                        for k in SECTIONS}
    (folder / 'workshop.json').write_bytes(jsonio.encode_file_exact(metadata))
    before = executable_bytes(ui, 'counter')
    legacy = projection(ui, 'counter', first['durable_id'])
    assert legacy['status'] == 'legacy' and legacy['sections']['use'] == 'Legacy use.'
    assert legacy['issues'][0]['code'] == 'legacy_exceeds_bounds'
    assert projection(ui, 'counter', second['durable_id'])['status'] == 'missing'
    manual(ui, 'counter', first['durable_id'])
    assert projection(ui, 'counter', first['durable_id'])['status'] == 'saved'
    assert executable_bytes(ui, 'counter') == before
    draft = send(ui, 'preview', key='counter', revision=first['durable_id'])['draft']
    restored = plan_save(ui, draft, draft['base_digest'])
    assert projection(ui, 'counter', restored['durable_id'])['status'] == 'missing'


def test_orphan_cleanup_and_non_destructive_refusals(ui, monkeypatch, restart):
    monkeypatch.setattr(workshop_assets, 'REVISION_LIMIT', 2)
    first, second = counter(ui)
    manual(ui, 'counter', first['durable_id'])
    descriptions = ui.workspace.path / 'blocks/counter/.workshop/descriptions'
    foreign = descriptions / 'notes.txt'
    foreign.write_text('keep')
    outside = ui.workspace.path / 'outside.json'
    outside.write_text('{}')
    orphan_link = descriptions / ('e' * 32 + '.json')
    orphan_link.symlink_to(outside)
    draft = send(ui, 'preview', key='counter')['draft']
    plan_save(ui, draft, draft['base_digest'])                    # prunes the first generation
    assert not (descriptions / (first['durable_id'] + '.json')).exists()
    assert foreign.exists() and orphan_link.is_symlink() and outside.read_text() == '{}'
    target = descriptions / (second['durable_id'] + '.json')
    path = f'blocks/counter/revisions/{second["durable_id"]}/description'
    basis = projection(ui, 'counter', second['durable_id'])['basis']
    for data in (b'{ malformed', jsonio.encode_file_exact({'format_version': 2, 'basis': basis})):
        target.write_bytes(data)
        shown = projection(ui, 'counter', second['durable_id'])
        assert shown['status'] == 'unavailable' and not shown['writable']
        refusal = failure_of(lambda: manual(ui, 'counter', second['durable_id'], token=shown['token'] or '0' * 64))
        assert refusal.status == 409 and target.read_bytes() == data
    target.unlink()
    target.symlink_to(outside)
    shown = projection(ui, 'counter', second['durable_id'])
    assert shown['status'] == 'unavailable' and not shown['writable']
    refusal = failure_of(lambda: put(ui, path, {'expected_token': None, 'basis': basis,
                                                'sections': {k: ('x' if k == 'use' else '') for k in SECTIONS},
                                                'port_summaries': {'inputs': {}, 'outputs': {}}}))
    assert refusal.status == 409 and outside.read_text() == '{}'
    target.unlink()
    # A mismatched basis shows unavailable but can be replaced deliberately with its token.
    mismatched = jsonio.encode_file_exact({'format_version': 1, 'basis': '0' * 64, 'origin': 'manual',
                                           'sections': {k: ('x' if k == 'use' else '') for k in SECTIONS},
                                           'port_summaries': {'inputs': {}, 'outputs': {}}})
    target.write_bytes(mismatched)
    shown = projection(ui, 'counter', second['durable_id'])
    assert shown['status'] == 'unavailable' and shown['writable']
    assert manual(ui, 'counter', second['durable_id'], token=shown['token'])['status'] == 'saved'
    # Startup recovery also removes recognised orphans.
    stray = descriptions / ('d' * 32 + '.json')
    stray.write_bytes(mismatched)
    ui = restart(ui)
    assert not stray.exists() and target.exists() and foreign.exists()


def test_read_only_workspace_refuses_description_writes(ui):
    first, _second = counter(ui)
    current = projection(ui, 'counter', first['durable_id'])
    ui.workspace.read_only = True
    try:
        refusal = failure_of(lambda: manual(ui, 'counter', first['durable_id'], token=current['token'] or None))
        assert refusal.status == 409 and refusal.body['code'] == 'read_only'
    finally:
        ui.workspace.read_only = False


@pytest.mark.parametrize('field, value', [('use', '   '), ('use', 'a\u0000b'), ('limitations', 'x' * 601)])
def test_prose_rules(field, value):
    sections = {k: ('ok' if k == 'use' else '') for k in SECTIONS}
    sections[field] = value
    with pytest.raises(workshop_descriptions.DescriptionInvalid) as caught:
        workshop_descriptions.normalise(sections, {'inputs': {}, 'outputs': {}}, {'inputs': [], 'outputs': []})
    assert caught.value.issues[0]['location'] == 'sections.' + field
    assert value not in json.dumps(caught.value.issues)


def test_describe_destination_locality_is_reported(ui):
    service = ui.state.workshop
    def locality(kind, base_url):
        descriptor = {'describe': {'mode': 'generate', 'key': 'k', 'revision': '0' * 32, 'revision_number': 1},
                      'candidates': [{'destination': {'kind': kind, 'base_url': base_url, 'model': 'm', 'provider': ''}}]}
        return service.describe_public(descriptor)['destination']['locality']
    assert locality('openai_compat', 'http://127.0.0.1:9/v1') == 'local'
    assert locality('openai_compat', 'https://models.example.com/v1') == 'cloud'
    assert locality('openrouter', 'https://openrouter.ai/api/v1') == 'cloud'
