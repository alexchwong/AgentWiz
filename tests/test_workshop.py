"""HTTP regressions for bounded, process-scoped Workshop Start tickets."""
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


def plan_save(ui, draft, expected=None):
    body = {'draft': draft, 'expected_identity': expected}
    plan = send(ui, 'plans', action='save', request=body)
    return send(ui, 'save', **body, expected_plan=plan['expected_plan'],
                confirmations=[{'kind': r['kind'], 'digest': r['digest']} for r in plan['required_confirmations']])


def test_revision_numbers_survive_retention_restore_duplicate_and_restart(ui, restart, monkeypatch):
    """User versions must not shift with retention or trust copied/client numbering."""
    from agentwiz import workshop_assets
    monkeypatch.setattr(workshop_assets, 'REVISION_LIMIT', 3)
    draft = send(ui, 'preview', starter='count_text', key='versions')['draft']
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
    draft = send(ui, 'preview', starter='count_text', key='old')['draft']
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


def test_script_starter_save_reopen_run_compare_restore(ui):
    preview = send(ui, 'preview', starter='count_text', key='counter')
    assert preview['issues'] == []
    assert preview['classification'] == 'starter'
    draft = preview['draft']
    saved = plan_save(ui, draft)
    assert request(ui, '/api/blocks/counter')['problem'] is None
    reopened = send(ui, 'preview', key='counter')
    assert reopened['classification'] == 'starter'
    assert reopened['draft']['metadata']['runtime_digest'] == draft['metadata']['runtime_digest']
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
    draft = send(ui, 'preview', starter='count_text', key='words')['draft']
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
    draft = send(ui, 'preview', starter='count_text', key='draft_only')['draft']
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
    draft = send(ui, 'preview', starter='count_text', key='persisted')['draft']
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
    draft = send(ui, 'preview', starter='count_text', key='reviewable')['draft']
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
    result = port_edit(ui, send(ui, 'preview', draft=draft), 'outputs', 'out',
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
    carried = send(ui, 'preview', draft=draft)
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
    draft = send(ui, 'preview', starter='count_text', key='disk_failure')['draft']
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


def test_custom_script_support_versions_are_frozen(ui):
    from agentwiz import jsonio
    obj = {'format_version': 3, 'name': 'helper', 'inputs': {}, 'outputs': {'out': {'contract': {'format': 'json'}}},
           'steps': [{'id': 'run', 'kind': 'script', 'file': 'scripts/main.py', 'timeout_s': 5}]}
    files = {'scripts/main.py': 'from pathlib import Path\nimport helper\nprint(\'{"out":\' + str(helper.N + int(Path(__file__).with_name("data.txt").read_text())) + "}")\n',
             'scripts/helper.py': 'N = 2\n', 'scripts/data.txt': '3'}
    draft = {'key': 'helper', 'definition_text': jsonio.encode_exact(obj), 'files': files,
             'metadata': {'support': ['scripts/helper.py', 'scripts/data.txt'], 'origin': 'manual'}}
    first = plan_save(ui, draft)
    root = ui.workspace.path / 'blocks/helper/scripts'
    root.mkdir()
    (root / 'helper.py').write_text('N = 7\n')
    changed = send(ui, 'blocks/helper/support/preview', replace=['scripts/helper.py'])['draft']
    # Publication must recheck the working-copy bytes selected by support Preview.
    (root / 'helper.py').write_text('N = 99\n')
    refusal = failure_of(lambda: plan_save(ui, changed, first['base_digest']))
    assert refusal.status == 409 and refusal.body['code'] == 'source_changed'
    assert request(ui, '/api/workshop/blocks/helper/revisions')['current'] == first['durable_id']
    (root / 'helper.py').write_text('N = 7\n')
    plan_save(ui, changed, first['base_digest'])
    old_batch = completed(ui, start_planned(ui, 'test', {'key': 'helper', 'revision': first['durable_id'], 'inputs': {}})['batch_id'])
    current = completed(ui, start_planned(ui, 'test', {'key': 'helper', 'inputs': {}})['batch_id'])
    assert old_batch['rows'][0]['outputs']['out']['text'] == '5'
    assert current['rows'][0]['outputs']['out']['text'] == '10'


def test_captured_runtime_upgrade_preserves_starter_controls(ui, monkeypatch, tmp_path):
    from agentwiz import jsonio
    original = send(ui, 'preview', starter='count_text', key='upgrade')['draft']
    first = plan_save(ui, original)
    changed_runtime = tmp_path / 'jsonio.py'
    changed_runtime.write_text(__import__('pathlib').Path(jsonio.__file__).read_text() + '\n# simulated installed update\n')
    monkeypatch.setattr(jsonio, '__file__', str(changed_runtime))
    reopened = send(ui, 'preview', key='upgrade')
    assert reopened['classification'] == 'starter'
    changed = send(ui, 'preview', key='upgrade', edit={'kind': 'starter_parameters', 'parameters': {'unit': 'characters'}})['draft']
    assert changed['metadata']['runtime_digest'] != original['metadata']['runtime_digest']
    plan_save(ui, changed, first['base_digest'])
    old = completed(ui, start_planned(ui, 'test', {'key': 'upgrade', 'revision': first['durable_id'], 'inputs': {'text': 'a b'}})['batch_id'])
    assert old['rows'][0]['outputs']['out']['text'] == '2'


def test_generated_source_requires_ack_and_compatibility_enforces_review(ui):
    from agentwiz import jsonio
    obj = {'format_version': 3, 'name': 'generated', 'inputs': {}, 'outputs': {'out': {'contract': {'format': 'json'}}},
           'steps': [{'id': 'run', 'kind': 'script', 'file': 'scripts/main.py', 'timeout_s': 5}]}
    draft = {'key': 'generated', 'definition_text': jsonio.encode_exact(obj),
             'files': {'scripts/main.py': 'print(\'{"out":3}\')\n'},
             'metadata': {'origin': 'llm_generated', 'support': [], 'effects': 'unknown'}}
    body = {'draft': draft, 'expected_identity': None}
    plan = send(ui, 'plans', action='save', request=body)
    assert 'generated_source' in {r['kind'] for r in plan['required_confirmations']}
    assert failure_of(lambda: send(ui, 'save', **body, expected_plan=plan['expected_plan'], confirmations=[])).status == 409
    assert not (ui.workspace.path / 'blocks/generated').exists()
    saved = plan_save(ui, draft)
    # Simulate an unreviewed managed generation and exercise the compatibility route.
    path = ui.workspace.path / 'blocks/generated/.workshop/revisions' / saved['durable_id'] / 'workshop.json'
    metadata = jsonio.decode_file_exact(path.read_bytes())
    metadata.pop('source_review')
    path.write_bytes(jsonio.encode_file_exact(metadata))
    from conftest import run_block, result_of
    result = result_of(run_block(ui, 'generated', {}))
    assert result['outcome'] == 'rejected'
    assert result['steps'] == []


def test_changed_immutable_asset_refuses_publication(ui):
    draft = send(ui, 'preview', starter='count_text', key='integrity')['draft']
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
    p = send(ui, 'preview', starter='count_text', key='freshness')
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
