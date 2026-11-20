"""HTTP regressions for bounded, process-scoped Workshop Start tickets."""
import pytest
from conftest import failure_of, request


def test_ticket_identity_and_invalid_envelopes(ui):
    first = request(ui, '/api/workshop/tickets', {'format_version': 1})
    second = request(ui, '/api/workshop/tickets', {'format_version': 1})
    assert first['ticket'] != second['ticket']
    assert first['expires_in'] == 600
    status = request(ui, '/api/workshop/tickets/' + first['ticket'])
    assert status['state'] == 'unused'
    assert status['ticket'] == first['ticket']
    failure = failure_of(lambda: request(ui, '/api/workshop/tickets/' + '0' * 32))
    assert failure.status == 410 and failure.body['code'] == 'ticket_expired'
    for body in ({}, {'format_version': True}, {'format_version': 2},
                 {'format_version': 1, 'extra': 1}):
        failure = failure_of(lambda: request(ui, '/api/workshop/tickets', body))
        assert failure.status == 400 and failure.body['code'] == 'invalid'


def test_unused_tickets_have_bounded_admission(ui):
    for _ in range(128):
        request(ui, '/api/workshop/tickets', {'format_version': 1})
    failure = failure_of(lambda: request(ui, '/api/workshop/tickets', {'format_version': 1}))
    assert failure.status == 503 and failure.body['code'] == 'busy'


def send(ui, route, **body):
    return request(ui, '/api/workshop/' + route, {'format_version': 1, **body})


def plan_save(ui, draft, expected=None, **options):
    if 'draft_ref' not in draft:
        draft.update(send(ui, 'drafts/import', draft=draft)['draft'])
    body = {'draft': draft, 'expected_identity': expected, **options}
    plan = send(ui, 'plans', action='save', request=body)
    return send(ui, 'save', **body, expected_plan=plan['expected_plan'],
                confirmations=[{'kind': r['kind'], 'digest': r['digest']} for r in plan['required_confirmations']])


def model_draft(key):
    """A v3 block draft: only v3 revisions may be overwritten."""
    import json
    from test_blocks import model_block, MODEL_FILES
    return {'key': key, 'definition_text': json.dumps(model_block(name=key)), 'files': MODEL_FILES, 'metadata': {}}


def test_overwrite_selected_revision_keeps_number_order_and_default(ui, restart):
    draft = model_draft('replace')
    first = plan_save(ui, draft)
    draft = send(ui, 'preview', key='replace')['draft']
    second = plan_save(ui, draft, draft['base_digest'])
    selected = send(ui, 'preview', key='replace', revision=first['durable_id'])['draft']
    selected['metadata']['revision_name'] = 'Replacement'
    body = {'draft': selected, 'expected_identity': selected['base_digest'],
            'overwrite_revision': first['durable_id']}
    plan = send(ui, 'plans', action='save', request=body)
    assert plan['descriptor']['overwrite']['revision_number'] == 1
    assert 'overwrite_revision' in [r['kind'] for r in plan['required_confirmations']]
    failure = failure_of(lambda: send(ui, 'save', **body, expected_plan=plan['expected_plan'], confirmations=[]))
    assert failure.body['code'] == 'confirmation_required'
    physical, release = ui.state.workshop.assets.pin('replace', first['durable_id'])
    try:
        original_bytes = (physical / 'block.json').read_bytes()
        replacement = plan_save(ui, selected, selected['base_digest'], overwrite_revision=first['durable_id'])
        assert (physical / 'block.json').read_bytes() == original_bytes
    finally:
        release()
    assert replacement['revision_number'] == 1
    assert replacement['durable_id'] != first['durable_id']
    rows = request(ui, '/api/workshop/blocks/replace/revisions')
    assert rows['current'] == second['durable_id']
    assert [r['revision_number'] for r in rows['revisions']] == [2, 1]
    assert rows['revisions'][1]['revision_name'] == 'Replacement'
    assert rows['revisions'][1]['id'] == replacement['durable_id']
    stale = failure_of(lambda: plan_save(ui, selected, selected['base_digest'],
                                         overwrite_revision=first['durable_id']))
    assert stale.body['code'] == 'stale'
    ui = restart(ui)
    draft = send(ui, 'preview', key='replace')['draft']
    replaced_head = plan_save(ui, draft, draft['base_digest'], overwrite_revision=second['durable_id'])
    assert replaced_head['revision_number'] == 2
    assert request(ui, '/api/workshop/blocks/replace/revisions')['current'] == replaced_head['durable_id']
    draft = send(ui, 'preview', key='replace')['draft']
    assert plan_save(ui, draft, draft['base_digest'])['revision_number'] == 3


def test_revision_numbers_survive_retention_restore_duplicate_and_restart(ui, restart, monkeypatch):
    """User versions must not shift with retention or trust copied/client numbering."""
    from agentwiz import workshop_assets
    monkeypatch.setattr(workshop_assets, 'REVISION_LIMIT', 3)
    draft = send(ui, 'preview', starter='count_text', starter_version=2, key='versions')['draft']
    for number in range(1, 6):
        draft['metadata'].update(revision_number=900, revision_name='Named' if number == 2 else '')
        saved = plan_save(ui, draft, draft.get('base_digest'))
        assert saved['revision_number'] == number
        draft = send(ui, 'preview', key='versions')['draft']
    rows = request(ui, '/api/workshop/blocks/versions/revisions')['revisions']
    assert [r['revision_number'] for r in rows] == [5, 4, 3]
    restored = send(ui, 'preview', key='versions', revision=rows[-1]['id'])['draft']
    assert plan_save(ui, restored, restored['base_digest'])['revision_number'] == 6
    duplicate = send(ui, 'preview', draft=restored, duplicate_key='copy')['draft']
    assert plan_save(ui, duplicate)['revision_number'] == 1
    ui = restart(ui)
    draft = send(ui, 'preview', key='versions')['draft']
    assert plan_save(ui, draft, draft['base_digest'])['revision_number'] == 7
    draft = send(ui, 'preview', key='copy')['draft']
    draft['metadata']['revision_name'] = '\ninvalid'
    refusal = failure_of(lambda: plan_save(ui, draft, draft['base_digest']))
    assert refusal.body['code'] == 'block_invalid'
    assert refusal.body['issues'][0]['location'] == 'metadata.revision_name'


def test_unnumbered_history_is_anchored_without_rewriting_generations(ui, monkeypatch):
    """Upgrade numbering remains stable as unnumbered generations are pruned."""
    from agentwiz import jsonio, workshop_assets
    monkeypatch.setattr(workshop_assets, 'REVISION_LIMIT', 3)
    draft = send(ui, 'preview', starter='count_text', starter_version=2, key='old')['draft']
    for _ in range(3):
        plan_save(ui, draft, draft.get('base_digest'))
        draft = send(ui, 'preview', key='old')['draft']
    folders = ui.workspace.path / 'blocks' / 'old' / '.workshop' / 'revisions'
    for folder in folders.iterdir():
        path = folder / 'workshop.json'
        metadata = jsonio.decode_exact(path.read_text())
        metadata.pop('revision_number')
        path.write_bytes(jsonio.encode_file_exact(metadata))
    before = {p: p.read_bytes() for p in folders.glob('*/workshop.json')}
    rows = request(ui, '/api/workshop/blocks/old/revisions')['revisions']
    assert [r['revision_number'] for r in rows] == [3, 2, 1]
    assert all(p.read_bytes() == data for p, data in before.items())
    for number in (4, 5, 6, 7):
        draft = send(ui, 'preview', key='old')['draft']
        draft['metadata']['revision_name'] = ' Label ' if number == 4 else ''
        saved = plan_save(ui, draft, draft['base_digest'])
        assert saved['revision_number'] == number
        rows = request(ui, '/api/workshop/blocks/old/revisions')['revisions']
        assert [r['revision_number'] for r in rows] == list(range(number, number - 3, -1))
        if number == 4:
            assert rows[0]['revision_name'] == 'Label'


def test_transform_starter_save_reopen_run_compare_restore(ui):
    preview = send(ui, 'preview', starter='count_text', starter_version=2, key='counter')
    assert preview['issues'] == []
    assert preview['classification'] == 'starter'
    draft = preview['draft']
    saved = plan_save(ui, draft)
    assert request(ui, '/api/blocks/counter')['problem'] is None
    reopened = send(ui, 'preview', key='counter')
    assert reopened['classification'] == 'starter'
    ticket = send(ui, 'tickets')['ticket']
    body = {'key': 'counter', 'inputs': {'text': 'three blue notebooks'}, 'ticket': ticket}
    plan = send(ui, 'plans', action='test', request=body)
    started = send(ui, 'test', **body, expected_plan=plan['expected_plan'], confirmations=[])
    duplicate = send(ui, 'test', **body, expected_plan=plan['expected_plan'], confirmations=[])
    assert duplicate['operation_id'] == started['operation_id']
    batch = completed(ui, started['batch_id'])
    assert batch['state'] == 'complete', batch
    assert batch['rows'][0]['outputs']['out']['text'] == '3'
    history = request(ui, '/api/workshop/blocks/counter/revisions')
    assert history['current'] == saved['durable_id']
    old = request(ui, '/api/workshop/blocks/counter/revisions/' + saved['durable_id'])
    assert old['classification'] == 'starter'


def test_stale_publication_keeps_revision_and_exact_projection(ui):
    import json
    from test_blocks import model_block, MODEL_FILES
    draft = {'key': 'b', 'definition_text': json.dumps(model_block()), 'files': MODEL_FILES, 'metadata': {}}
    result = plan_save(ui, draft)
    refusal = failure_of(lambda: plan_save(ui, draft))
    assert refusal.status == 409
    assert request(ui, '/api/workshop/blocks/b/revisions')['current'] == result['durable_id']
    reopened = send(ui, 'preview', key='b')['draft']
    assert plan_save(ui, reopened, reopened['base_digest'])['revision_number'] == 2


def completed(ui, ident):
    from conftest import wait_until
    def terminal():
        return request(ui, '/api/workshop/batches/' + ident)['state'] != 'running'
    wait_until(terminal)
    return request(ui, '/api/workshop/batches/' + ident)


def start_planned(ui, action, body):
    ticket = send(ui, 'tickets')['ticket']
    body = {**body, 'ticket': ticket}
    plan = send(ui, 'plans', action=action, request=body)
    return send(ui, action, **body, expected_plan=plan['expected_plan'],
                confirmations=[{'kind': r['kind'], 'digest': r['digest']} for r in plan['required_confirmations']])


def test_compare_exact_examples_and_stop_reuse(ui):
    from agentwiz import jsonio
    draft = send(ui, 'preview', starter='count_text', starter_version=2, key='words')['draft']
    saved = plan_save(ui, draft)
    alternate = send(ui, 'preview', draft=draft, edit={'kind': 'starter_parameters', 'parameters': {'unit': 'characters'}})['draft']
    examples = [{'id': 'e1', 'version': 1, 'label': 'one', 'inputs': {'text': 'a b'},
                 'verified': True, 'expectation': {'kind': 'whole_output', 'values': {'out': '2'}}},
                {'id': 'e2', 'version': 1, 'label': 'two', 'inputs': {'text': ''},
                 'verified': True, 'expectation': {'kind': 'whole_output', 'values': {'out': '0'}}}]
    started = start_planned(ui, 'batches', {'candidates': [{'key': 'words'}, {'draft': alternate}], 'examples': examples})
    batch = completed(ui, started['batch_id'])
    assert [r['arm'] for r in batch['rows']] == [0, 1, 1, 0]
    assert [r['correctness'] for r in batch['rows']] == ['correct', 'incorrect', 'correct', 'correct']
    assert batch['metrics'][0]['correct'] == 2
    assert batch['metrics'][1]['correct'] == 1
    assert saved['durable_id']


def test_assist_uses_runner_and_applies_without_publication(ui, server):
    import json
    from conftest import answer, save_profile, save_roles
    save_profile(ui, 'p', model='m', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'assistant': 'p'})
    draft = send(ui, 'preview', starter='count_text', starter_version=2, key='draft_only')['draft']
    response = {'format_version': 1, 'action': 'improve', 'payload': {'purpose': 'Reviewed purpose'},
                'assumptions': [], 'unmet_requirements': []}
    server.reply(answer(json.dumps({'proposal': response})))
    started = start_planned(ui, 'assist', {'action': 'improve', 'role': 'assistant', 'draft': draft,
                                           'task': 'Improve this counter.', 'sections': ['purpose']})
    batch = completed(ui, started['batch_id'])
    assert batch['rows'][0]['outcome'] == 'complete', batch
    proposal = batch['rows'][0]['proposal_id']
    apply_body = {'proposal_id': proposal, 'draft': draft}
    plan = send(ui, 'plans', action='proposals/apply', request=apply_body)
    applied = send(ui, 'proposals/apply', **apply_body, expected_plan=plan['expected_plan'], confirmations=[])
    assert json.loads(applied['draft']['definition_text'])['description'] == 'Reviewed purpose'
    assert request(ui, '/api/blocks')['total'] == 0
    assert len(server.requests) == 1
    # The legacy draft-based Describe shape is retired: standard prose belongs to a saved revision now.
    refusal = failure_of(lambda: send(ui, 'plans', action='assist', request={
        'action': 'describe', 'role': 'assistant', 'draft': draft, 'task': 'Describe this counter.'}))
    assert refusal.status == 409 and refusal.body['code'] == 'describe_moved'
    for sections in (['description_sections'], ['purpose', 'description_sections']):
        refusal = failure_of(lambda: send(ui, 'plans', action='assist', request={
            'action': 'improve', 'role': 'assistant', 'draft': draft, 'task': 'x', 'sections': sections}))
        assert refusal.body['code'] == 'describe_moved'
    assert len(server.requests) == 1


def test_assist_plan_reports_authoring_protection(ui, server):
    """UI-CD v3 CD6: the page shows the permanent protection consequence from the bound plan, not its own guess."""
    from conftest import save_profile, save_roles
    save_profile(ui, 'p', model='m', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'assistant': 'p'})
    grants = request(ui, '/api/workshop/destination-trust')
    send(ui, 'destination-trust', expected_revision=grants['revision'], trusted=True,
         destination={'kind': 'openai_compat', 'url': server.url + '/v1', 'model': 'm', 'provider': ''})
    draft = send(ui, 'preview', starter='count_text', starter_version=2, key='draft_only')['draft']
    body = {'action': 'improve', 'role': 'assistant', 'draft': draft, 'task': 'Improve.', 'sections': ['purpose']}
    assert send(ui, 'plans', action='assist', request=body)['descriptor']['authoring_protected'] is False
    protected = send(ui, 'plans', action='assist', request={**body, 'context_local_only': True})
    assert protected['descriptor']['authoring_protected'] is True
    assert len(server.requests) == 0


def http_method(ui, path, method, body=None):
    import json
    import urllib.request
    from conftest import base
    req = urllib.request.Request(base(ui) + '/api/workshop/' + path,
        data=None if body is None else json.dumps({'format_version': 1, **body}).encode(),
        headers={'Content-Type': 'application/json', 'X-Session-Token': ui.token}, method=method)
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=5) as r:
        return json.loads(r.read())


def test_durable_intent_precedes_dispatch_and_owns_deletion(ui, monkeypatch):
    import json
    import threading
    import urllib.error
    from agentwiz import workshop_service
    from conftest import switch_history
    switch_history(ui, True)
    draft = send(ui, 'preview', starter='count_text', starter_version=2, key='persisted')['draft']
    plan_save(ui, draft)
    reached, release = threading.Event(), threading.Event()
    def hold(ident, row):
        reached.set()
        assert release.wait(5)
    monkeypatch.setattr(workshop_service, 'before_trial_dispatch', hold)
    started = start_planned(ui, 'test', {'key': 'persisted', 'inputs': {'text': 'one two'}})
    ident = started['batch_id']
    try:
        assert reached.wait(5)
        path = ui.workspace.path / 'workshop' / 'comparisons' / (ident + '.json')
        manifest = json.loads(path.read_text())
        assert manifest['rows'][0]['state'] == 'dispatch_pending'
        assert not list((ui.workspace.path / 'runs').glob('*'))
        try:
            http_method(ui, 'comparisons/' + ident, 'DELETE')
        except urllib.error.HTTPError as e:
            assert e.code == 409
        else:
            assert False, 'active comparison was deleted'
    finally:
        release.set()
    batch = completed(ui, ident)
    assert batch['rows'][0]['outcome'] == 'complete'
    run_id = batch['rows'][0]['run_id']
    # A second batch evicts the first session entry. Its persisted timings must
    # remain readable through HTTP, with no rerun or evidence recreation.
    completed(ui, start_planned(ui, 'test', {'key':'persisted','inputs':{'text':'new'}})['batch_id'])
    restored = request(ui, '/api/workshop/comparisons/' + ident)
    assert restored['rows'][0]['outputs']['out']['text'] == '2'
    assert restored['rows'][0]['run_id'] == run_id
    assert http_method(ui, 'comparisons/' + ident, 'DELETE')['deleted']
    assert request(ui, '/api/runs/' + run_id)['result']['outcome'] == 'complete'


def test_result_specific_review_and_saved_examples(ui):
    from conftest import switch_history
    switch_history(ui, True)
    draft = send(ui, 'preview', starter='count_text', starter_version=2, key='reviewable')['draft']
    plan_save(ui, draft)
    examples = [{'id': 'e', 'version': 1, 'label': 'two words', 'inputs': {'text': 'a b'},
                 'verified': True, 'expectation': {'kind': 'whole_output', 'values': {'out': '2'}}}]
    saved = http_method(ui, 'blocks/reviewable/examples', 'PUT', {'expected_revision': None, 'examples': examples})
    batch = completed(ui, start_planned(ui, 'batches', {'candidates': [{'key': 'reviewable'}], 'examples': saved['examples']})['batch_id'])
    row = batch['rows'][0]
    context = {'snapshot_id': row['snapshot_id'], 'example_id': 'e', 'example_version': 1, 'result_digest': row['result_digest']}
    reviewed = http_method(ui, 'reviews/' + row['run_id'], 'PUT', {'context': context, 'judgement': 'incorrect',
        'note': 'Human review of this exact answer', 'expected_revision': 0, 'persist': True})
    assert reviewed['revision'] == 1
    history = request(ui, '/api/runs/' + row['run_id'])
    assert history['current_review']['judgement'] == 'incorrect'
    current = request(ui, '/api/workshop/batches/' + batch['id'])
    assert current['metrics'][0]['incorrect'] == 1
    assert current['rows'][0]['machine_judgement'] == 'correct'
    assert current['rows'][0]['human_rating'] == 'incorrect'


def test_exact_editor_preservation_and_unsupported_schema_refusal(ui):
    from agentwiz import jsonio
    from test_blocks import model_block, MODEL_FILES
    obj = model_block()
    obj['outputs']['out']['contract'] = {'format': 'json', 'schema': {'type': 'object', 'properties': {
        'id': {'type': 'integer', 'minimum': 9007199254740993},
        'ratio': {'type': 'number', 'minimum': jsonio.decode_exact('0.10000000000000000001')}},
        'required': ['id'], 'additionalProperties': False}}
    draft = {'key': 'b', 'definition_text': jsonio.encode_exact(obj), 'files': MODEL_FILES, 'metadata': {}}
    result = port_edit(ui, send(ui, 'drafts/import', draft=draft), 'outputs', 'out',
                       {'op': 'set_description', 'path': ['id'], 'description': 'Identifier'})
    edited = jsonio.decode_exact(result['draft']['definition_text'])
    assert edited['outputs']['out']['contract']['schema']['properties']['id']['description'] == 'Identifier'
    assert edited['outputs']['out']['contract']['schema']['properties']['id']['minimum'] == 9007199254740993
    assert edited['outputs']['out']['contract']['schema']['properties']['ratio']['minimum'] == jsonio.decode_exact('0.10000000000000000001')
    assert edited['outputs']['out']['contract']['schema']['additionalProperties'] is False
    plan_save(ui, result['draft'])
    projected = send(ui, 'preview', key='b')['contracts']['outputs']['out']
    assert [row['path'] for row in projected['fields']] == [[], ['id'], ['ratio']]
    assert projected['fields'][2]['minimum'] == '0.10000000000000000001'               # exact text, never a float
    # The shared editor refuses an unsupported user schema; it is never projected from a partial reading.
    unsupported = jsonio.encode_exact(edited['outputs']['out']['contract']).replace(
        '"minimum":0.10000000000000000001', '"multipleOf":0.10000000000000000001')
    refused = failure_of(lambda: request(ui, '/api/contracts/edit', {'format_version': 2, 'definition_text': unsupported,
        'edit': {'op': 'set_description', 'path': ['id'], 'description': 'Identifier'}}))
    assert refused.status == 400 and refused.body['code'] == 'unsupported_keyword'
    carried = send(ui, 'drafts/import', draft=draft)
    operation = design_edit(carried)
    operation['ports']['outputs'][0]['source'] = unsupported
    invalid = send(ui, 'preview', draft=carried['draft'], edit=operation)
    assert invalid['valid'] is False and invalid['issues'] and 'out' not in invalid['contracts']['outputs']
    assert failure_of(lambda: send(ui, 'plans', action='save', request={'draft': invalid['draft'], 'expected_identity': None})).status in (400, 409)


def test_design_binding_edits_and_located_invalid_draft_refusals(ui):
    """Port edits must not hide usable inputs or turn definition errors into file errors."""
    from agentwiz import jsonio
    preview = send(ui, 'preview', starter='write_text', key='bindings', role='fast')
    draft = preview['draft']
    definition = jsonio.decode_exact(draft['definition_text'])
    ports = {side: [dict(original=name, name=name, description='',
                        format=port['contract']['format'], source=jsonio.encode_exact(port['contract']))
                    for name, port in definition[side].items()] for side in ('inputs', 'outputs')}
    operation = dict(kind='design_form', name='Bindings', purpose='', ports=ports,
                     behaviour=preview['design']['behaviour'], system='', binding='fast',
                     settings='{}', budget='{}', repairs='2')
    ports['outputs'][0]['name'] = 'answer'
    renamed = send(ui, 'preview', draft=draft, edit=operation)
    assert renamed['valid'] and renamed['issues'] == []
    renamed_definition = jsonio.decode_exact(renamed['draft']['definition_text'])
    assert list(renamed_definition['outputs']) == ['answer']
    assert renamed_definition['steps'][0]['response_mode'] == 'named_json'
    ports['inputs'][0]['name'] = 'message'
    invalid = send(ui, 'preview', draft=draft, edit=operation)
    assert invalid['valid'] is False
    assert invalid['input_ports'] == ['message']
    assert invalid['contracts']['inputs']['message']['format'] == 'text'
    for action in ('save', 'test'):
        failure = failure_of(lambda: send(ui, 'plans', action=action, request={'draft': invalid['draft']}))
        assert failure.status == 409 and failure.body['code'] == 'block_invalid'
        assert failure.body['issues'][0]['location'] == 'steps[0].prompt'
    ports['inputs'][0]['format'] = 'shared'
    failure = failure_of(lambda: send(ui, 'preview', draft=draft, edit=operation))
    assert failure.status == 400 and failure.body['code'] == 'reference_invalid'
    ports['inputs'][0].update(name='text', source='"shared_text"')               # a name alone is not a pin
    failure = failure_of(lambda: send(ui, 'preview', draft=draft, edit=operation))
    assert failure.status == 400 and failure.body['code'] == 'reference_invalid'
    from conftest import save_contract
    saved = save_contract(ui, 'shared_text', '{"format":"text"}')
    pin = {'name': 'shared_text', 'revision': saved['revision'], 'digest': saved['digest']}
    ports['inputs'][0].update(name='text', source=jsonio.encode_exact(pin))
    shared = send(ui, 'preview', draft=draft, edit=operation)
    assert shared['valid']
    assert jsonio.decode_exact(shared['draft']['definition_text'])['inputs']['text']['contract'] == pin
    result = plan_save(ui, shared['draft'])
    metadata = request(ui, '/api/workshop/blocks/' + shared['draft']['key'] + '/revisions')['revisions'][0]['metadata']
    assert metadata['contract_pins']['inputs'] == {'text': pin} and metadata['contract_pins']['outputs'] == {}
    assert set(metadata['contracts']['inputs']) == {'text'} and result['revision_number'] >= 1


def test_marker_write_failure_dispatches_nothing(ui, monkeypatch):
    from agentwiz import workshop_service
    from conftest import switch_history
    switch_history(ui, True)
    draft = send(ui, 'preview', starter='count_text', starter_version=2, key='disk_failure')['draft']
    plan_save(ui, draft)
    calls = []
    original = workshop_service.write_atomic_bytes
    def fail_marker(root, filename, data):
        from agentwiz import jsonio
        payload = jsonio.decode_file_exact(data)
        if any(r.get('state') == 'dispatch_pending' for r in payload.get('rows', [])):
            raise OSError('injected storage failure')
        return original(root, filename, data)
    monkeypatch.setattr(workshop_service, 'write_atomic_bytes', fail_marker)
    monkeypatch.setattr(workshop_service, 'before_trial_dispatch', lambda *args: calls.append(args))
    started = start_planned(ui, 'test', {'key': 'disk_failure', 'inputs': {'text': 'a b'}})
    result = completed(ui, started['batch_id'])
    assert calls == []
    assert result['state'] == 'stopped'
    assert not list((ui.workspace.path / 'runs').glob('*'))


def test_all_packaged_assistance_actions_use_checked_requests(ui, server):
    from conftest import answer, save_profile, save_roles
    from agentwiz.tutorial_support.workshop import proposal
    save_profile(ui, 'p', model='m', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'assistant': 'p', 'fast': 'p'})
    draft = send(ui, 'preview', starter='extract_fields', key='extractor', role='fast')['draft']
    for action in ('create', 'improve', 'suggest_examples', 'explain_failure'):
        server.reply(answer('{"proposal":' + proposal(action) + '}'))
        batch = completed(ui, start_planned(ui, 'assist', {'action': action, 'role': 'assistant', 'draft': draft,
                          'task': 'Prepare this proposal.', 'evidence': {}})['batch_id'])
        assert batch['rows'][0]['outcome'] == 'complete'
        body = {'proposal_id': batch['rows'][0]['proposal_id'], 'draft': draft}
        plan = send(ui, 'plans', action='proposals/apply', request=body)
        result = send(ui, 'proposals/apply', **body, expected_plan=plan['expected_plan'], confirmations=[])
        assert result
        if action == 'suggest_examples':
            assert not result['examples'][0]['verified']
    assert request(ui, '/api/blocks')['total'] == 0
    assert len(server.requests) == 4
    # The packaged Describe stand-in answers the server-owned request for a saved revision of the same starter.
    save_roles(ui, {'assistant': 'p', 'fast': 'p', 'write_block_description': 'p'})
    saved = plan_save(ui, draft)
    server.reply(answer('{"proposal":' + proposal('describe') + '}'))
    batch = completed(ui, start_planned(ui, 'assist', {'action': 'describe', 'mode': 'generate', 'key': 'extractor',
                                                        'revision': saved['durable_id']})['batch_id'])
    assert batch['outcome'] == 'proposal', batch
    assert batch['proposal']['port_summaries'] == {'inputs': {'text': 'The order text to read.'},
                                                   'outputs': {'out': 'The product and any explicit quantity.'}}


def test_legacy_conversion_confirmation_and_retention(ui):
    import json
    from conftest import write_block
    from test_blocks import model_block, MODEL_FILES
    write_block(ui, 'b', model_block(), MODEL_FILES)
    preview = send(ui, 'preview', key='b')
    draft = preview['draft']
    body = {'draft': draft, 'expected_identity': draft['base_digest']}
    plan = send(ui, 'plans', action='save', request=body)
    assert 'legacy_conversion' in {r['kind'] for r in plan['required_confirmations']}
    refusal = failure_of(lambda: send(ui, 'save', **body, expected_plan=plan['expected_plan'], confirmations=[]))
    assert refusal.status == 409
    assert json.loads((ui.workspace.path / 'blocks/b/block.json').read_text())['format_version'] == 3
    saved = plan_save(ui, draft, draft['base_digest'])
    revisions = request(ui, '/api/workshop/blocks/b/revisions')
    assert len(revisions['revisions']) == 2
    assert [r['revision_number'] for r in revisions['revisions']] == [2, 1]
    original = send(ui, 'preview', key='b', revision=revisions['revisions'][1]['id'])      # history stays readable
    assert original['valid'] and set(revisions['revisions'][1]['metadata']['contracts']['outputs']) == {'out'}
    assert (ui.workspace.path / 'blocks/b/prompts/user.md').read_text() == MODEL_FILES['prompts/user.md']
    for i in range(20):
        draft = send(ui, 'preview', key='b', display_name='revision-' + str(i))['draft']
        saved = plan_save(ui, draft, saved['base_digest'])
    assert len(request(ui, '/api/workshop/blocks/b/revisions')['revisions']) == 20
    assert saved['revision_number'] == 22


def test_changed_immutable_asset_refuses_publication(ui):
    draft = send(ui, 'preview', starter='blank_python', data_handling='not_private', key='integrity')['draft']
    saved = plan_save(ui, draft)
    root = ui.workspace.path / 'blocks/integrity'
    source = root / '.workshop/revisions' / saved['durable_id'] / 'scripts/main.py'
    source.write_text(source.read_text() + '\n# external edit\n')
    refusal = failure_of(lambda: plan_save(ui, draft, saved['base_digest']))
    assert refusal.status == 409
    from agentwiz import jsonio
    assert jsonio.decode_file_exact((root / 'block.json').read_bytes())['current'] == saved['durable_id']


def test_visual_forms_keep_exact_values_and_remove_required_members(ui):
    """Browser adapters must preserve precision and edit the authoritative shared schema."""
    from agentwiz import jsonio
    text = '{"n":9007199254740993,"d":0.12345678901234567890123456789,"yes":false,"nil":null,"empty":""}'
    value = send(ui, 'render-value', text=text)
    assert jsonio.decode_exact(send(ui, 'input-value', value=value)['text']) == jsonio.decode_exact(text)
    source = '{"format":"json","schema":{"type":"object","properties":{"n":{"type":"number","minimum":0.123456789012345678901,"maximum":0.999999999999999999999}},"required":["n"]}}'

    def edit(text, operation):
        return request(ui, '/api/contracts/edit', {'format_version': 2, 'definition_text': text, 'edit': operation})['definition_text']
    text = edit(source, {'op': 'rename_property', 'path': ['n'], 'name': 'exact'})
    text = edit(text, {'op': 'set_description', 'path': ['exact'], 'description': 'Amount'})
    text = edit(text, {'op': 'set_bound', 'path': ['exact'], 'keyword': 'maximum', 'value_text': None})
    schema = jsonio.decode_exact(text)['schema']
    assert schema['required'] == ['exact'] and 'maximum' not in schema['properties']['exact']
    assert schema['properties']['exact']['minimum'] == jsonio.decode_exact('0.123456789012345678901')
    schema = jsonio.decode_exact(edit(text, {'op': 'remove_property', 'path': ['exact']}))['schema']
    assert schema['properties'] == {} and schema['required'] == []
    opaque = '{"format":"json","schema":{"type":"object","$defs":{},"properties":{}}}'
    assert failure_of(lambda: request(ui, '/api/contracts/project', {'format_version': 2, 'definition_text': opaque})).status == 400
    # The retired Workshop-only contract form route is gone; the shared contract editor is the one path.
    assert failure_of(lambda: send(ui, 'contract-form', source=source)).status == 404


def test_standard_prose_is_not_carried_by_drafts_or_new_saves(ui):
    """The draft-carried description edit and its needs_refresh projection are retired (spec v2 section 3):
    a new revision starts without standard prose and never inherits it, while starter prose may remain a
    draft display hint."""
    from agentwiz import jsonio
    p = send(ui, 'preview', starter='count_text', starter_version=2, key='freshness')
    assert 'description' not in p
    assert p['draft']['metadata']['description_sections']['use']          # starter hint, draft only
    refusal = failure_of(lambda: send(ui, 'preview', draft=p['draft'], edit={'kind': 'description', 'sections': {
        k: 'Reviewed' for k in ('use', 'exclusions', 'effects', 'limitations', 'missing_data', 'failures')}}))
    assert refusal.status == 409 and refusal.body['code'] == 'describe_moved'
    saved = plan_save(ui, p['draft'])
    folder = ui.workspace.path / 'blocks/freshness/.workshop/revisions' / saved['durable_id']
    metadata = jsonio.decode_file_exact((folder / 'workshop.json').read_bytes())
    assert 'description_sections' not in metadata and 'description_basis' not in metadata
    current = send(ui, 'preview', key='freshness')
    assert current['standard_description']['status'] == 'missing'
    assert current['standard_description']['fallback']['purpose'] == 'count text'


def test_review_goes_stale_when_role_defaults_declaration_or_protocol_change(ui, server, monkeypatch):
    """A reviewed model plan is bound to what it will send: the role's saved defaults, the declared outputs
    and the protocol renderer. Changing any of them after review makes Start refuse the old plan, and a
    started batch is judged by the plan it was reviewed under."""
    import json
    from conftest import get_state, save_profile
    from test_blocks import model_block, MODEL_FILES
    from agentwiz import output_protocol
    save_profile(ui, 'p', model='m', kind='openai_compat', base_url=server.url + '/v1', provider='')

    def set_default(temperature):
        definitions = {'fast': {'description': '', 'location': 'local', 'task_defaults': {'temperature': temperature}}}
        request(ui, '/api/roles/save', {'roles': {'fast': 'p'}, 'definitions': definitions,
                                        'revision': get_state(ui)['roles']['revision']})

    def plan_of(body):
        plan = send(ui, 'plans', action='test', request=body)
        return plan, plan['descriptor']['candidates'][0]

    set_default(0.2)
    draft = {'key': 'b', 'definition_text': json.dumps(model_block()), 'files': MODEL_FILES, 'metadata': {}}
    plan_save(ui, draft)
    body = {'key': 'b', 'inputs': {'text': 'hi'}, 'ticket': send(ui, 'tickets')['ticket']}
    reviewed, candidate = plan_of(body)
    assert candidate['role_defaults'] == {'temperature': 0.2}
    assert candidate['settings_source']['temperature'] == 'role_default'
    assert candidate['output_plan']['renderer'] == output_protocol.RENDERER_VERSION

    set_default(0.9)
    refusal = failure_of(lambda: send(ui, 'test', **body, expected_plan=reviewed['expected_plan'], confirmations=[]))
    assert refusal.status == 409 and refusal.body['code'] == 'confirmation_required'
    after_role, candidate = plan_of(body)
    assert after_role['expected_plan'] != reviewed['expected_plan']
    assert candidate['role_defaults'] == {'temperature': 0.9}

    monkeypatch.setattr(output_protocol, 'RENDERER_VERSION', output_protocol.RENDERER_VERSION + 1)
    after_renderer, candidate = plan_of(body)
    assert after_renderer['expected_plan'] != after_role['expected_plan']
    assert candidate['output_plan']['renderer'] == output_protocol.RENDERER_VERSION
    monkeypatch.undo()

    edited = json.loads(draft['definition_text'])
    edited['outputs']['out']['description'] = 'The reply, in one sentence'
    before = plan_of(body)[1]['output_plan']['digest']
    changed = plan_of({'draft': {**draft, 'definition_text': json.dumps(edited)}, 'inputs': {'text': 'hi'},
                       'ticket': body['ticket']})[1]['output_plan']['digest']
    assert changed != before


def test_file_backed_output_token_survives_save_and_preview_equals_what_the_backend_receives(ui, server):
    """A saved block keeps `{{output.NAME}}` in its prompt file; the preview shows the filled prompt and the
    generated section, and the model receives exactly those messages."""
    import json
    from conftest import block_answer, save_profile, save_roles
    from test_blocks import model_block, MODEL_FILES
    from agentwiz import output_protocol
    save_profile(ui, 'p', model='m', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'fast': 'p'})
    block = model_block(outputs={'out': {'description': 'One plain sentence', 'contract': {'format': 'text'}}})
    files = {**MODEL_FILES, 'prompts/user.md': 'Say {{inputs.text}} as {{output.out}}.'}
    plan_save(ui, {'key': 'b', 'definition_text': json.dumps(block), 'files': files, 'metadata': {}})
    reopened = send(ui, 'preview', key='b', inputs={'text': 'hi'})
    assert reopened['issues'] == [] and reopened['design']['behaviour'] == files['prompts/user.md']
    prepared = reopened['prepared']
    assert 'One plain sentence' in prepared['prompt'] and '{{output.out}}' not in prepared['prompt']
    assert prepared['system_sent'] == prepared['system'] + '\n\n' + prepared['generated']
    assert prepared['generated'].startswith(output_protocol.BEGIN) and prepared['generated'].endswith(output_protocol.END)
    server.reply(block_answer(out='hi'))
    batch = completed(ui, start_planned(ui, 'test', {'key': 'b', 'inputs': {'text': 'hi'}})['batch_id'])
    assert batch['state'] == 'complete' and batch['rows'][0]['outputs']['out']['text'] == 'hi'
    sent = server.requests[0]['body']['messages']
    assert sent == [{'role': 'system', 'content': prepared['system_sent']}, {'role': 'user', 'content': prepared['prompt']}]
    assert batch['rows'][0]['raw_answer'] == '{"out":"hi"}'


def schema_block(schema, name='b'):
    from test_blocks import model_block
    return model_block(name, outputs={'out': {'contract': {'format': 'json', 'schema': schema}}})


NODES = {'Group': '{"type":"object","properties":{},"required":[],"additionalProperties":false}', 'Text': '{"type":"string"}'}


def port_edit(ui, preview, side, port, operation):
    """Edit one port's inline definition with the shared contract editor (POST /api/contracts/edit), then carry the
    result into the block draft as an ordinary design edit, exactly as the page does."""
    entry = next(f for f in preview['design'][side] if f['name'] == port)
    edited = request(ui, '/api/contracts/edit', {'format_version': 2, 'definition_text': entry['source'], 'edit': operation})
    design = design_edit(preview)
    for f in design['ports'][side]:
        if f['name'] == port:
            f.update(source=edited['definition_text'], format=edited['format'])
    return send(ui, 'preview', draft=preview['draft'], edit=design)


def add_property(name, kind, path=()):
    return {'op': 'add_property', 'path': list(path), 'name': name, 'node_text': NODES[kind], 'required': True}


def test_a_new_empty_group_may_be_edited_but_not_saved_or_started_and_a_saved_one_is_kept(ui, server):
    """Authoring may pass through an empty group; Save and Start refuse one the author has just left empty, naming
    it, while a group that was already empty in the saved block is left alone."""
    import json
    from test_blocks import MODEL_FILES
    import pytest
    from conftest import save_profile, save_roles
    pytest.importorskip('jsonschema')
    save_profile(ui, 'p', model='m', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'fast': 'p'})
    closed = {'type': 'object', 'properties': {'a': {'type': 'string'}}, 'required': ['a'], 'additionalProperties': False}
    draft = {'key': 'g', 'definition_text': json.dumps(schema_block(closed, 'g')), 'files': MODEL_FILES, 'metadata': {}}
    plan_save(ui, draft)
    edited = port_edit(ui, send(ui, 'preview', draft=draft), 'outputs', 'out', add_property('nested', 'Group'))   # the empty state is allowed
    assert edited['issues'] == []
    refused = failure_of(lambda: send(ui, 'plans', action='save', request={'draft': edited['draft'], 'expected_identity': None}))
    assert refused.status == 409 and refused.body['code'] == 'block_invalid'
    body = {'draft': edited['draft'], 'inputs': {'text': 'hi'}, 'ticket': send(ui, 'tickets')['ticket']}
    started = failure_of(lambda: send(ui, 'plans', action='test', request=body))
    assert started.status == 409 and started.body['code'] == 'block_invalid'
    assert [i['location'] for i in started.body['issues']] == ['outputs.out.nested']
    filled = port_edit(ui, edited, 'outputs', 'out', add_property('child', 'Text', ['nested']))
    assert send(ui, 'plans', action='test', request={**body, 'draft': filled['draft']})['expected_plan']
    # A group that the saved block already holds empty is not "new": it stays editable and runnable.
    legacy = json.loads(json.dumps(closed))
    legacy['properties']['old'] = {'type': 'object', 'properties': {}, 'additionalProperties': False}
    from conftest import write_block
    write_block(ui, 'old', schema_block(legacy, 'old'), MODEL_FILES)
    reopened = send(ui, 'preview', key='old')
    assert reopened['issues'] == []
    assert send(ui, 'plans', action='test', request={'draft': reopened['draft'], 'inputs': {'text': 'hi'},
                                                     'ticket': body['ticket']})['expected_plan']


def design_edit(preview, **over):
    d = preview['design']
    ports = {side: [{'original': f['name'], 'name': f['name'], 'description': f['description'],
                     'format': f['format'], 'source': f['source']} for f in d[side]] for side in ('inputs', 'outputs')}
    return {'kind': 'design_form', 'name': preview['draft']['key'], 'purpose': d['purpose'], 'ports': ports,
            'arguments': d['arguments'], 'behaviour': d['behaviour'], 'binding': d['binding'], 'system': d['system'],
            'settings': d['settings'], 'sampling': {}, 'resets': [], 'customs': [], 'limits': {}, 'budget': d['budget'],
            'repairs': d['repairs'], **over}


def test_override_controls_inherit_reset_or_set_and_a_blank_custom_is_refused(ui):
    """Each sampling control is Inherit, Custom or Backend default. Custom needs a value, zero is a value, and
    Backend default is the one explicit request not to inherit a role default."""
    import json
    from test_blocks import model_block, MODEL_FILES
    draft = {'key': 'b', 'definition_text': json.dumps(model_block()), 'files': MODEL_FILES, 'metadata': {}}
    plan_save(ui, draft)
    base = send(ui, 'preview', key='b')

    def step_of(**over):
        result = send(ui, 'preview', draft=base['draft'], edit=design_edit(base, **over))
        return json.loads(result['draft']['definition_text'])['steps'][0]

    blank = failure_of(lambda: step_of(customs=['temperature'], sampling={'temperature': ''}))
    assert blank.status == 409 and blank.body['code'] == 'block_invalid'
    assert [i['location'] for i in blank.body['issues']] == ['steps[0].task.temperature']
    inherited = step_of(sampling={'temperature': ''})
    assert 'temperature' not in inherited['task'] and 'task_resets' not in inherited
    reset = step_of(resets=['temperature'], sampling={'temperature': ''})
    assert 'temperature' not in reset['task'] and reset['task_resets'] == ['temperature']
    zero = step_of(customs=['temperature'], sampling={'temperature': '0'})
    assert zero['task']['temperature'] == 0 and 'task_resets' not in zero
    for bad in ({'customs': ['temperature'], 'resets': ['temperature'], 'sampling': {'temperature': '1'}},
                {'customs': ['max_tokens']}, {'customs': ['temperature', 'temperature']}):
        assert failure_of(lambda: step_of(**bad)).status == 400


# --- 0.2.3 (spec v15, 4.3): S-1 clean current selections and E-1 engine recovery -----------------------------------
def _cloud_block(ui, cloud, key='cloudy', local_only=False):
    """An unmanaged v4 model block at a cloud role; no protection, so no destination check unless labelled."""
    from conftest import KEY, save_profile, save_roles, write_block
    save_profile(ui, 'remote', model='remote', kind='openrouter', base_url=cloud.url + '/v1', provider='', api_key=KEY)
    save_roles(ui, {'cloud': 'remote'})
    definition = {'format_version': 4, 'name': key, 'description': 'asks the cloud',
                  'inputs': {'text': {'contract': {'format': 'text'}, 'local_only': local_only}},
                  'outputs': {'out': {'contract': {'format': 'text'}, 'local_only': False}},
                  'steps': [{'id': 'request', 'kind': 'model', 'role': 'cloud', 'system': {'text': ''},
                             'prompt': {'text': 'Say {{inputs.text}}'}, 'response_mode': 'named_json',
                             'repair': {'max_repairs': 0}}]}
    folder = write_block(ui, key, definition)
    return folder, definition


def _effective(ui, key):
    return {r['name']: r for r in request(ui, '/api/blocks')['blocks']}[key]['revision']


def test_clean_current_selection_mismatched_revision_is_stale(ui, server):
    """R-4(d): a `test` plan with a mismatched `expected_revision` answers 409 `stale`, creating no batch."""
    _cloud_block(ui, server)
    revision = _effective(ui, 'cloudy')
    for expected in (revision + 1, revision - 1):
        body = {'key': 'cloudy', 'expected_revision': expected, 'inputs': {'text': 'x'}}
        refusal = failure_of(lambda: send(ui, 'plans', action='test', request=body))
        assert refusal.status == 409 and refusal.body['code'] == 'stale'
    for extra in ({'draft': {}}, {'revision': 'r'}, {'edit': {}}):
        body = {'key': 'cloudy', 'expected_revision': revision, 'inputs': {'text': 'x'}, **extra}
        assert failure_of(lambda: send(ui, 'plans', action='test', request=body)).status == 400
    assert request(ui, '/api/workshop/batches')['active'] is None and server.requests == []


@pytest.mark.parametrize('before_trial', [1, 2])
def test_clean_current_selection_rechecks_before_every_trial(ui, server, monkeypatch, before_trial):
    """R-4(e): S-1's per-trial guard alone. The hook marks the input Local only on disk before trial n: that trial
    and every later one send nothing, earlier trials keep their results, and the batch ends dependency_changed."""
    import json
    from agentwiz import jsonio, workshop_service
    from conftest import block_answer
    folder, definition = _cloud_block(ui, server)
    server.reply(block_answer(out='fine'))
    calls = []

    def hook(batch_id, row):
        calls.append(row['trial_id'])
        if len(calls) == before_trial:
            definition['inputs']['text']['local_only'] = True
            (folder / 'block.json').write_bytes(jsonio.encode_exact(definition, indent=2).encode())
    monkeypatch.setattr(workshop_service, 'before_trial_dispatch', hook)
    examples = [{'id': f'e{n}', 'version': 1, 'inputs': {'text': str(n)},
                 'expectation': {'kind': 'review', 'criterion': ''}, 'verified': False} for n in (1, 2)]
    started = start_planned(ui, 'batches', {'candidates': [{'key': 'cloudy', 'expected_revision': _effective(ui, 'cloudy')}],
                                            'examples': examples})
    batch = completed(ui, started['batch_id'])
    assert (batch['state'], batch['reason']) == ('stopped', 'dependency_changed'), json.dumps(batch)[:2000]
    assert len(server.requests) == before_trial - 1
    if before_trial == 2:
        assert batch['rows'][0]['outcome'] == 'complete'


def test_clean_current_selection_unusable_contract_pin_is_stale(ui, server):
    """R-4(g): a pinned contract revision that becomes unusable after opening answers stale with zero requests,
    as `block_for_run` does."""
    import os
    from conftest import save_contract, write_block
    saved = save_contract(ui, 'tc', '{"format": "text"}')
    _cloud_block(ui, server)
    from test_blocks import model_block, MODEL_FILES
    block = model_block('pinned', outputs={'out': {'contract': {'name': 'tc', 'revision': 1, 'digest': saved['digest']}}})
    write_block(ui, 'pinned', block, MODEL_FILES)
    opened = send(ui, 'preview', key='pinned')
    revision = opened['effective_revision']
    path = ui.workspace.path / 'contracts' / 'tc.json'
    path.write_bytes(b'{ damaged')
    os.utime(path, ns=(os.stat(path).st_mtime_ns + 5_000_000_000,) * 2)
    body = {'key': 'pinned', 'expected_revision': revision, 'inputs': {'text': 'x'}}
    refusal = failure_of(lambda: send(ui, 'plans', action='test', request=body))
    assert refusal.status == 409 and refusal.body['code'] == 'stale'
    assert server.requests == []


def test_export_race_never_pairs_old_bytes_with_new_revision(ui, server, monkeypatch):
    """R-4(h): a disk edit injected between the two reads of `export` answers stale, never a draft paired with the
    newer revision."""
    from agentwiz import workshop_assets
    folder, _ = _cloud_block(ui, server)
    real = workshop_assets.read
    state = {'done': False}

    def racing(root, path, *args, **kwargs):
        data = real(root, path, *args, **kwargs)
        import sys
        caller = sys._getframe(1).f_code.co_name
        if path == 'block.json' and not state['done'] and caller in ('export', '<dictcomp>'):
            state['done'] = True
            text = (folder / 'block.json').read_text().replace('asks the cloud', 'edited on disk')
            (folder / 'block.json').write_text(text)
        return data
    monkeypatch.setattr(workshop_assets, 'read', racing)
    refusal = failure_of(lambda: send(ui, 'preview', key='cloudy'))
    assert refusal.status == 409 and refusal.body['code'] == 'stale'
    monkeypatch.setattr(workshop_assets, 'read', real)
    opened = send(ui, 'preview', key='cloudy')
    assert 'edited on disk' in opened['draft']['definition_text']
    assert opened['effective_revision'] == _effective(ui, 'cloudy')


def _engine_changed_block(ui, key='old_engine', protected=False):
    """An unmanaged, indented v4 Python block whose recorded engine differs from the installed one."""
    from conftest import python_block, write_block
    definition = python_block(key, inputs={'text': {'format': 'text'}}, outputs={'out': {'format': 'text'}})
    definition['steps'][0]['engine'] = {**definition['steps'][0]['engine'], 'codec': 999}
    if protected:
        definition['context_local_only'] = True
    folder = write_block(ui, key, definition, {'scripts/go.py': "def transform(inputs):\n    return {'out': inputs['text']}\n"})
    return folder


def _bytes(folder):
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob('*')) if p.is_file()}


def _recover(ui, key):
    return send(ui, 'preview', key=key, engine_recovery=True)['draft']


def test_engine_recovery_save_checkpoints_and_refusals(ui, monkeypatch):
    """R-4(k), server part: Open as draft writes the installed engine into a draft while disk is unchanged; Save
    archives the exact original bytes as the first retained revision; a disk edit before the plan, before
    Confirm or at the publication checkpoint answers stale and publishes nothing; a restart discards the record."""
    import hashlib
    from agentwiz import jsonio, python_step, workshop_assets
    folder = _engine_changed_block(ui)
    original = _bytes(folder)
    headless = failure_of(lambda: request(ui, '/api/blocks/run', {'name': 'old_engine',
                                                                  'revision': _effective(ui, 'old_engine'),
                                                                  'inputs': {'text': 'x'}}))
    assert headless.body['code'] == 'block_unusable'
    current = failure_of(lambda: send(ui, 'plans', action='test', request={
        'key': 'old_engine', 'expected_revision': _effective(ui, 'old_engine'), 'inputs': {'text': 'x'}}))
    assert current.body['code'] == 'block_unusable'
    assert 'engine_changed' in str(current.body['issues']) and 'engine_changed' in str(headless.body['problem'])
    draft = _recover(ui, 'old_engine')
    assert jsonio.decode_exact(draft['definition_text'])['steps'][0]['engine'] == python_step.engine()
    assert 'engine_recovery' not in draft['metadata'] and 'recovery_id' not in draft['metadata']
    assert _bytes(folder) == original
    # Staleness before the Save plan, before Confirm, and at the publication checkpoint.
    def edit_disk():
        (folder / 'scripts' / 'go.py').write_text("def transform(inputs):\n    return {'out': 'edited'}\n")

    def restore():
        for rel, data in original.items():
            (folder / rel).write_bytes(data)
    for when in ('plan', 'confirm', 'checkpoint'):
        restore()
        draft = _recover(ui, 'old_engine')
        body = {'draft': draft, 'expected_identity': draft['base_digest']}
        if when == 'plan':
            edit_disk()
            assert failure_of(lambda: send(ui, 'plans', action='save', request=body)).body['code'] == 'stale'
        else:
            plan = send(ui, 'plans', action='save', request=body)
            if when == 'confirm':
                edit_disk()
            else:
                monkeypatch.setattr(workshop_assets, 'publish_checkpoint', lambda key, stage: edit_disk())
            refusal = failure_of(lambda: send(ui, 'save', **body, expected_plan=plan['expected_plan'],
                                              confirmations=[{'kind': r['kind'], 'digest': r['digest']}
                                                             for r in plan['required_confirmations']]))
            assert refusal.body['code'] == 'stale'
            monkeypatch.setattr(workshop_assets, 'publish_checkpoint', lambda key, stage: None)
        edited = _bytes(folder)
        assert not (folder / '.workshop').exists() and edited != original
    # A block with another issue besides engine_changed offers no recovery.
    restore()
    broken = jsonio.decode_exact((folder / 'block.json').read_text())
    broken['unknown_key'] = 1
    (folder / 'block.json').write_text(jsonio.encode_exact(broken, indent=2))
    assert failure_of(lambda: _recover(ui, 'old_engine')).body['code'] == 'unusable'
    restore()
    # Explicit revision and authoring fields are refused with engine_recovery.
    for extra in ({'revision': 'r'}, {'edit': {}}, {'draft': {}}):
        assert failure_of(lambda: send(ui, 'preview', key='old_engine', engine_recovery=True, **extra)).status in (400, 409)
    # Save succeeds: the first retained revision keeps the original bytes; the current carries the installed engine.
    draft = _recover(ui, 'old_engine')
    saved = plan_save(ui, draft, draft['base_digest'])
    rows = request(ui, '/api/workshop/blocks/old_engine/revisions')['revisions']
    assert [r['revision_number'] for r in rows] == [2, 1]
    first = folder / '.workshop' / 'revisions' / rows[1]['id']
    for rel, data in original.items():
        assert (first / rel).read_bytes() == data, rel
    meta = jsonio.decode_file_exact((first / 'workshop.json').read_bytes())
    assert meta['assets'] == {rel: hashlib.sha256(data).hexdigest() for rel, data in original.items()}
    assert 'contract_pins' in meta and 'contracts' in meta
    workshop_assets.validate_contract_maps(jsonio.decode_file_exact((first / 'block.json').read_bytes()), meta)
    current_def = jsonio.decode_file_exact((folder / '.workshop' / 'revisions' / saved['durable_id'] / 'block.json').read_bytes())
    assert current_def['steps'][0]['engine'] == python_step.engine()
    result = completed(ui, start_planned(ui, 'test', {'key': 'old_engine', 'expected_revision': _effective(ui, 'old_engine'),
                                                      'inputs': {'text': 'pong'}})['batch_id'])
    assert result['rows'][0]['outcome'] == 'complete', result['rows'][0]


def test_engine_recovery_record_is_discarded_by_a_restart(ui, restart):
    from agentwiz import jsonio
    from conftest import result_of, run_block, switch_history
    folder = _engine_changed_block(ui)
    original = _bytes(folder)
    draft = _recover(ui, 'old_engine')
    ui = restart(ui)
    body = {'draft': draft, 'expected_identity': draft['base_digest']}
    assert failure_of(lambda: send(ui, 'plans', action='save', request=body)).body['code'] == 'draft_expired'
    assert _bytes(folder) == original
    draft = _recover(ui, 'old_engine')
    switch_history(ui, True)
    draft['metadata'].update(engine_recovery=True, recovery_id='discard this marker')
    body = {'draft': draft, 'inputs': {'text': 'pong'}}
    batch = completed(ui, start_planned(ui, 'test', body)['batch_id'])
    assert batch['rows'][0]['outcome'] == 'complete'
    saved = plan_save(ui, draft, draft['base_digest'])
    workspace = ui.workspace.path
    for directory in (folder, workspace / 'runs', workspace / 'workshop' / 'comparisons'):
        for evidence in directory.rglob('*.json'):
            assert not any(key in evidence.read_bytes() for key in (b'"engine_recovery"', b'"recovery_id"'))
    assert saved['revision_number'] == 2
    ui = restart(ui)
    reopened = send(ui, 'preview', key='old_engine')['draft']
    definition = jsonio.decode_exact(reopened['definition_text'])
    definition['description'] = 'Changed purpose after restart'
    reopened['definition_text'] = jsonio.encode_exact(definition)
    assert plan_save(ui, reopened, reopened['base_digest'])['revision_number'] == 3
    reopened = send(ui, 'preview', key='old_engine')['draft']
    reopened['files']['scripts/go.py'] = "def transform(inputs):\n    return {'out': 'recovered ' + inputs['text']}\n"
    assert plan_save(ui, reopened, reopened['base_digest'])['revision_number'] == 4
    body = {'key': 'old_engine', 'expected_revision': _effective(ui, 'old_engine'), 'inputs': {'text': 'pong'}}
    result = completed(ui, start_planned(ui, 'test', body)['batch_id'])['rows'][0]
    assert result['outcome'] == 'complete' and result['outputs']['out']['text'] == 'recovered pong'
    assert result_of(run_block(ui, 'old_engine', body['inputs']))['outcome'] == 'complete'


def test_engine_recovery_expires_on_workspace_switch(ui):
    from test_workspace_lifecycle import make_second
    folder = _engine_changed_block(ui)
    original = _bytes(folder)
    draft = _recover(ui, 'old_engine')
    name = ui.workspace.name
    make_second(ui)
    for target in ('second', name):
        request(ui, '/api/workspaces/switch', {'format_version': 1, 'name': target})
    body = {'draft': draft, 'expected_identity': draft['base_digest']}
    assert failure_of(lambda: send(ui, 'plans', action='save', request=body)).body['code'] == 'draft_expired'
    assert _bytes(folder) == original
    draft = _recover(ui, 'old_engine')
    assert plan_save(ui, draft, draft['base_digest'])['revision_number'] == 2


def test_engine_recovery_binds_only_the_issued_draft_ref(ui):
    """R-4(n): a second draft for the same protected key, given A's base_digest, is not a recovery; A keeps its
    inherited protection and saves with it."""
    import json
    from agentwiz import jsonio
    folder = _engine_changed_block(ui, protected=True)
    original = _bytes(folder)
    a = _recover(ui, 'old_engine')
    assert a['metadata']['protection_origin'] == 'inherited'
    definition = jsonio.decode_exact(a['definition_text'])
    definition.pop('context_local_only', None)
    b = send(ui, 'drafts/import', draft={'key': 'old_engine', 'definition_text': jsonio.encode_exact(definition),
                                          'files': a['files'], 'metadata': {}})['draft']
    b['base_digest'] = a['base_digest']
    refusal = failure_of(lambda: send(ui, 'plans', action='save', request={'draft': b, 'expected_identity': a['base_digest']}))
    assert refusal.status in (400, 409), refusal.body
    assert _bytes(folder) == original
    cleared = dict(a, definition_text=jsonio.encode_exact(definition))
    assert failure_of(lambda: send(ui, 'preview', draft=cleared)).body['code'] == 'protection_inherited'
    plan_save(ui, a, a['base_digest'])
    current = send(ui, 'preview', key='old_engine')['draft']
    assert jsonio.decode_exact(current['definition_text'])['context_local_only'] is True
    assert current['metadata']['protection_origin'] == 'inherited'
    assert json.dumps(current['metadata']).count('recovery') == 0


def test_protected_apply_preserves_engine_recovery(ui, server):
    from agentwiz import jsonio
    from conftest import answer, save_profile, save_roles
    _engine_changed_block(ui)
    draft = _recover(ui, "old_engine")
    save_profile(ui, "p", model="m", kind="openai_compat", base_url=server.url + "/v1", provider="")
    save_roles(ui, {"assistant": "p"})
    grants = request(ui, "/api/workshop/destination-trust")
    send(ui, "destination-trust", expected_revision=grants["revision"], trusted=True,
         destination={"kind": "openai_compat", "url": server.url + "/v1", "model": "m", "provider": ""})
    response = {"format_version": 1, "action": "improve", "payload": {"purpose": "Reviewed purpose"},
                "assumptions": [], "unmet_requirements": []}
    server.reply(answer(jsonio.encode_exact({"proposal": response})))
    started = start_planned(ui, "assist", {"action": "improve", "role": "assistant", "draft": draft,
                                           "task": "Improve.", "sections": ["purpose"], "context_local_only": True})
    batch = completed(ui, started["batch_id"])
    apply_body = {"proposal_id": batch["rows"][0]["proposal_id"], "draft": draft}
    plan = send(ui, "plans", action="proposals/apply", request=apply_body)
    applied = send(ui, "proposals/apply", **apply_body, expected_plan=plan["expected_plan"],
                   confirmations=[{"kind": r["kind"], "digest": r["digest"]} for r in plan["required_confirmations"]])["draft"]
    assert applied["draft_ref"] != draft["draft_ref"]
    assert applied["metadata"]["protection_origin"] == "inherited"
    published = plan_save(ui, applied, draft["base_digest"])
    assert published["revision_number"] == 2
    stored = send(ui, "preview", key="old_engine")["draft"]
    assert stored["metadata"]["protection_origin"] == "inherited"
