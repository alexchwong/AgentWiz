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
    response = {'format_version': 1, 'action': 'describe', 'payload': {'description_sections': {
        k: 'Reviewed description' for k in ('use', 'exclusions', 'effects', 'limitations', 'missing_data', 'failures')}},
        'assumptions': [], 'unmet_requirements': []}
    server.reply(answer(json.dumps(response)))
    started = start_planned(ui, 'assist', {'action': 'describe', 'role': 'assistant', 'draft': draft, 'task': 'Describe this counter.'})
    batch = completed(ui, started['batch_id'])
    assert batch['rows'][0]['outcome'] == 'complete', batch
    proposal = batch['rows'][0]['proposal_id']
    apply_body = {'proposal_id': proposal, 'draft': draft}
    plan = send(ui, 'plans', action='proposals/apply', request=apply_body)
    applied = send(ui, 'proposals/apply', **apply_body, expected_plan=plan['expected_plan'], confirmations=[])
    assert applied['draft']['metadata']['description_sections']['use'] == 'Reviewed description'
    assert request(ui, '/api/blocks')['total'] == 0
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


def test_exact_editor_and_unsupported_schema_preservation(ui):
    from agentwiz import jsonio
    from test_blocks import model_block, MODEL_FILES
    obj = model_block()
    obj['outputs']['out']['contract'] = {'format': 'json', 'schema': {'type': 'object', 'properties': {
        'id': {'type': 'integer', 'minimum': 9007199254740993},
        'ratio': {'type': 'number', 'multipleOf': jsonio.decode_exact('0.10000000000000000001')}},
        'required': ['id'], 'additionalProperties': False}, 'custom_annotation': 'preserve'}
    draft = {'key': 'b', 'definition_text': jsonio.encode_exact(obj), 'files': MODEL_FILES, 'metadata': {}}
    result = send(ui, 'preview', draft=draft, edit={'kind': 'contract', 'side': 'outputs', 'port': 'out', 'path': ['id'], 'set': {'description': '"Identifier"'}})
    edited = jsonio.decode_exact(result['draft']['definition_text'])
    assert edited['outputs']['out']['contract']['schema']['properties']['id']['minimum'] == 9007199254740993
    assert edited['outputs']['out']['contract']['schema']['properties']['ratio']['multipleOf'] == jsonio.decode_exact('0.10000000000000000001')
    assert edited['outputs']['out']['contract']['custom_annotation'] == 'preserve'
    plan_save(ui, result['draft'])
    assert send(ui, 'preview', key='b')['contracts']['outputs']['out']['projection']


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
    for action in ('create', 'improve', 'describe', 'suggest_examples', 'explain_failure'):
        server.reply(answer(proposal(action)))
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
    assert len(server.requests) == 5


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
    assert json.loads((ui.workspace.path / 'blocks/b/block.json').read_text())['format_version'] == 1
    saved = plan_save(ui, draft, draft['base_digest'])
    revisions = request(ui, '/api/workshop/blocks/b/revisions')
    assert len(revisions['revisions']) == 2
    assert (ui.workspace.path / 'blocks/b/prompts/user.md').read_text() == MODEL_FILES['prompts/user.md']
    for i in range(20):
        draft = send(ui, 'preview', key='b', display_name='revision-' + str(i))['draft']
        saved = plan_save(ui, draft, saved['base_digest'])
    assert len(request(ui, '/api/workshop/blocks/b/revisions')['revisions']) == 20


def test_custom_script_support_versions_are_frozen(ui):
    from agentwiz import jsonio
    obj = {'format_version': 1, 'name': 'helper', 'inputs': {}, 'outputs': {'out': {'contract': {'format': 'json'}}},
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
    obj = {'format_version': 1, 'name': 'generated', 'inputs': {}, 'outputs': {'out': {'contract': {'format': 'json'}}},
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
    source = '{"format":"json","schema":{"type":"object","properties":{"n":{"type":"number","minimum":0.123456789012345678901,"multipleOf":0.000000000000000000001}},"required":["n"]}}'
    result = send(ui, 'contract-form', source=source, field_edit={
        'path':['n'], 'addition':False, 'name':'exact', 'type':'Number', 'description':'Amount',
        'required':True, 'nullable':False, 'choices':[], 'bounds':['0.123456789012345678901','']})
    schema = jsonio.decode_exact(result['source_text'])['schema']
    assert schema['required'] == ['exact']
    assert schema['properties']['exact']['minimum'] == jsonio.decode_exact('0.123456789012345678901')
    assert schema['properties']['exact']['multipleOf'] == jsonio.decode_exact('0.000000000000000000001')
    result = send(ui, 'contract-form', source=result['source_text'], field_edit={'path':['exact'], 'remove':True})
    schema = jsonio.decode_exact(result['source_text'])['schema']
    assert schema['properties'] == {} and schema['required'] == []
    opaque = '{"format":"json","schema":{"type":"object","$defs":{},"properties":{}}}'
    assert send(ui,'contract-form',source=opaque)['source_only']


def test_display_rename_keeps_description_review_until_behaviour_changes(ui):
    """A visual no-op must not invalidate reviewed prose by changing wire formatting."""
    p = send(ui,'preview',starter='count_text',key='freshness')
    p = send(ui,'preview',draft=p['draft'],edit={'kind':'description','sections':{
        k:'Reviewed' for k in ('use','exclusions','effects','limitations','missing_data','failures')}})
    d = p['design']
    edit = {'kind':'design_form','name':'Renamed','purpose':d['purpose'],
        'ports':{side:[{**r,'original':r['name']} for r in d[side]] for side in ('inputs','outputs')},
        'behaviour':d['behaviour'],'binding':d['binding'],'system':d['system'],
        'settings':d['settings'],'budget':d['budget'],'repairs':d['repairs']}
    renamed = send(ui,'preview',draft=p['draft'],edit=edit)
    assert not renamed['description']['needs_refresh']
    changed = send(ui,'preview',draft=renamed['draft'],edit={**edit,'behaviour':d['behaviour']+'\n# changed source\n'})
    assert changed['description']['needs_refresh']
