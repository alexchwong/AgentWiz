"""Public-surface coverage for the workflow foundation."""
import pytest

from agentwiz import jsonio
from conftest import failure_of, request, wait_until
from test_workshop import plan_save, send


def transform(name, operation, parameters, inputs, output):
    return {'key': name, 'files': {}, 'definition_text': jsonio.encode_exact({
        'format_version': 4, 'name': name, 'inputs': inputs, 'outputs': {'out': output},
        'steps': [{'id': 'run', 'kind': 'transform', 'operation': operation, 'operation_version': 1,
                   'parameters': parameters, 'output': 'out'}]})}


def port(kind='text', restricted=False):
    return {'contract': {'format': kind}, 'local_only': restricted}


def edit(ui, action, request_body, **overrides):
    plan = send(ui, 'plans', action='workflow_' + action, request=request_body)
    return send(ui, f"workflows/{request_body['name']}/{action}", request=request_body,
                expected_plan=plan['expected_plan'], confirmations=plan['required_confirmations'], **overrides)


def test_snapshot_fanout_rejoin_runs_after_workshop_sources_disappear(ui, tmp_path):
    revisions = {}
    for name, unit in [('words', 'whitespace_words'), ('characters', 'characters')]:
        revisions[name] = plan_save(ui, transform(name, 'count_text', {'unit': unit}, {'text': port()}, port('json')))
    revisions['report'] = plan_save(ui, transform(
        'report', 'format_report', {'template': 'Words: {{inputs.words}}; characters: {{inputs.characters}}'},
        {'words': port('json'), 'characters': port('json')}, port()))
    input_binding = {'kind': 'input', 'port': 'text'}
    def from_node(name):
        return {'kind': 'node', 'node': name, 'port': 'out'}
    definition = {
        'name': 'counts', 'purpose': 'Count words and characters', 'inputs': {'text': port()}, 'constants': {},
        'outputs': {'result': {**port(restricted=True), 'binding': from_node('report')}},
        'nodes': {
            'words': {'bindings': {'text': input_binding}},
            'report': {'bindings': {name: from_node(name) for name in ('words', 'characters')}},
            'characters': {'bindings': {'text': input_binding}},
        },
    }
    body = {'name': 'counts', 'file_token': None, 'definition_text': jsonio.encode_exact(definition),
            'selections': {name: {'key': name, 'revision': row['durable_id']} for name, row in revisions.items()}}
    token = edit(ui, 'save', body)['file_token']
    assert len(token) == 64
    for name in revisions:
        (ui.workspace.path / 'blocks' / name).rename(tmp_path / ('moved_' + name))
    assert request(ui, '/api/workshop/workflows')['workflows'] == ['counts']
    selection = {'name': 'counts', 'inputs': {'text': 'one two'}}
    plan = send(ui, 'plans', action='workflow_run', request=selection)
    assert plan['descriptor']['order'] == ['characters', 'words', 'report']
    assert plan['descriptor']['protected_nodes'] == []
    assert plan['descriptor']['issues'] == []
    ticket = send(ui, 'tickets')['ticket']
    start = {'request': selection, 'expected_plan': plan['expected_plan'], 'ticket': ticket}
    ident = send(ui, 'workflows/counts/run', **start)['operation_id']
    assert send(ui, 'workflows/counts/run', **start)['operation_id'] == ident
    route = '/api/workshop/workflow-jobs/' + ident
    wait_until(lambda: request(ui, route)['outcome'] != 'running')
    result = request(ui, route)
    assert result['outcome'] == 'complete', result
    assert result['outputs']['result'] == {'format': 'text', 'text': 'Words: 2; characters: 7', 'local_only': True}
    detail = request(ui, route + '?node=report')
    assert detail['steps'][0]['operation'] == {'name': 'format_report', 'version': 1}
    assert edit(ui, 'delete', {'name': 'counts', 'file_token': token})['deleted']
    assert request(ui, route)['outcome'] == 'complete'


@pytest.mark.parametrize('bad_label', [None, 0, 'false'])
def test_v4_labels_reject_non_booleans(ui, bad_label):
    draft = transform('counter', 'count_text', {'unit': 'characters'}, {'text': port(restricted=bad_label)}, port('json'))
    draft = send(ui, 'drafts/import', draft=draft)['draft']
    failure = failure_of(lambda: send(ui, 'plans', action='save', request={'draft': draft, 'expected_identity': None}))
    assert failure.status == 409
    assert any(issue['location'] == 'inputs.text.local_only' for issue in failure.body['issues'])


def test_transform_exact_paths_and_bounded_rendering():
    from agentwiz import templates, transforms
    mappings = [{'source_path': ['record', key], 'destination': key, 'required': False} for key in ('null', 'missing')]
    assert transforms.execute('select_fields', {'mappings': mappings}, {'record': {'null': None}})[0] == {'null': None}
    with pytest.raises(templates.FillError):
        transforms.execute('format_report', {'template': '{{inputs.x}}{{inputs.x}}'}, {'x': 'é' * (2 << 20)})


def test_legacy_starter_conversion_adds_script_free_generation(ui):
    legacy = send(ui, 'preview', starter='count_text', key='counter')['draft']
    original = plan_save(ui, legacy)
    old = send(ui, 'preview', key='counter', revision=original['durable_id'])
    result = send(ui, 'preview', draft=old['draft'], edit={'kind': 'convert_transform'})
    assert result['valid'] and result['kind'] == 'transform'
    assert result['draft']['files'] == {}
    assert result['design']['format_version'] == 4
    body = {'draft': result['draft'], 'expected_identity': old['draft']['base_digest']}
    plan = send(ui, 'plans', action='save', request=body)
    assert [item['kind'] for item in plan['required_confirmations']] == ['labels_reviewed']
    created = plan_save(ui, result['draft'], old['draft']['base_digest'])
    assert created['durable_id'] != original['durable_id']
    assert send(ui, 'preview', key='counter', revision=original['durable_id'])['kind'] == 'script'


def test_workflow_cycle_is_refused_before_publication(ui):
    saved = plan_save(ui, transform('report', 'format_report', {'template': '{{inputs.text}}'},
                                    {'text': port()}, port()))
    definition = {
        'name': 'cycle', 'purpose': '', 'inputs': {}, 'constants': {},
        'outputs': {'out': port()},
        'nodes': {'report': {'bindings': {'text': {'kind': 'node', 'node': 'report', 'port': 'out'}}}},
    }
    body = {'name': 'cycle', 'file_token': None, 'definition_text': jsonio.encode_exact(definition),
            'selections': {'report': {'key': 'report', 'revision': saved['durable_id']}}}
    failure = failure_of(lambda: send(ui, 'plans', action='workflow_save', request=body))
    assert failure.status == 409 and failure.body['code'] == 'workflow_cycle'
    assert request(ui, '/api/workshop/workflows')['workflows'] == []


def test_selector_keeps_exact_object_keys_and_indices():
    from agentwiz.workflow_definition import select, source
    from agentwiz.workshop import WorkshopError
    path = ['x.y', 0]
    binding = {'kind': 'input', 'port': 'value', 'path': path}
    assert source(binding, 'input') == ('input', 'value')
    for value in [None, False, 0, '']:
        assert select({'x.y': [value]}, path, 'input') is value
    with pytest.raises(WorkshopError, match='path_missing'):
        select({'x.y': []}, path, 'input')
    with pytest.raises(WorkshopError):
        source({**binding, 'path': [True]}, 'input')


def publish_workflow(ui, block_draft, *, examples=None, budget=None):
    block_name = block_draft['key']
    block = jsonio.decode_exact(block_draft['definition_text'])
    source = plan_save(ui, block_draft)
    node = {'bindings': {name: {'kind': 'input', 'port': name} for name in block['inputs']}}
    definition = {'name': 'chain', 'purpose': '', 'inputs': block['inputs'], 'constants': {},
                  'outputs': {name: {**port, 'binding': {'kind': 'node', 'node': 'only', 'port': name}}
                              for name, port in block['outputs'].items()}, 'nodes': {'only': node}}
    if budget:
        definition['budget'] = budget
    body = {'name': 'chain', 'file_token': None, 'definition_text': jsonio.encode_exact(definition),
            'selections': {'only': {'key': block_name, 'revision': source['durable_id']}}}
    if examples is not None:
        body['examples'] = examples
    return edit(ui, 'save', body)


def run_workflow(ui, selection):
    plan = send(ui, 'plans', action='workflow_run', request=selection)
    assert not [issue for issue in plan['descriptor']['issues'] if issue['severity'] == 'error'], plan
    ident = send(ui, 'workflows/chain/run', request=selection, expected_plan=plan['expected_plan'],
                 ticket=send(ui, 'tickets')['ticket'], confirmations=plan['required_confirmations'])['operation_id']
    route = '/api/workshop/workflow-jobs/' + ident
    wait_until(lambda: request(ui, route)['outcome'] != 'running')
    return ident, request(ui, route)


def test_workflow_model_repairs_share_attempt_budget(ui, server):
    from conftest import answer, save_profile, save_roles

    save_profile(ui, 'p', model='m', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'fast': 'p'})
    draft = {'key': 'writer', 'files': {}, 'definition_text': jsonio.encode_exact({
        'format_version': 4, 'name': 'writer', 'inputs': {'text': port()}, 'outputs': {'out': port()},
        'steps': [{'id': 'write', 'kind': 'model', 'role': 'fast', 'response_mode': 'named_json',
                   'prompt': {'text': '{{inputs.text}}'}, 'repair': {'max_repairs': 2}}]})}
    publish_workflow(ui, draft, budget={'max_model_attempts': 1})
    server.reply(answer('{"wrong":"needs repair"}'))
    _, result = run_workflow(ui, {'name': 'chain', 'inputs': {'text': 'sample'}})
    assert result['outcome'] == 'budget_exceeded', result
    assert result['model_attempts'] == 1
    assert len(server.requests) == 1


def test_workflow_mcp_review_settings_and_saved_evidence(ui, make_fake_mcp):
    from conftest import add_server, simple_tool, switch_history

    fake = make_fake_mcp('modern', tools=[simple_tool('lookup', behave=lambda call: call.result({
        'content': [], 'structuredContent': {'ok': True}, 'isError': False}))])
    add_server(ui, 'docs', fake.url)
    draft = {'key': 'lookup', 'files': {}, 'definition_text': jsonio.encode_exact({
        'format_version': 4, 'name': 'lookup', 'inputs': {'text': port()}, 'outputs': {'out': port('json')},
        'steps': [{'id': 'lookup', 'kind': 'tool', 'server': 'docs', 'tool': 'lookup',
                   'arguments': {'q': '{{inputs.text}}'}, 'output': 'out'}]})}
    examples = [{'id': ident, 'version': 1, 'inputs': {'text': ident}, 'verified': True,
                 'expectation': {'kind': 'whole_output', 'values': {'out': expected}}}
                for ident, expected in [('first', '{"ok":true}'), ('second', '{"ok":false}')]]
    published = publish_workflow(ui, draft, examples=examples)
    selection = {'name': 'chain', 'inputs': {'text': 'one'}}
    old = send(ui, 'plans', action='workflow_run', request=selection)
    assert [confirm['kind'] for confirm in old['required_confirmations']] == ['workflow_mcp']
    send(ui, 'workflow-mcp-confirmation', mode='effectful_batches', expected_revision=None)
    new = send(ui, 'plans', action='workflow_run', request=selection)
    assert new['required_confirmations'] == []
    assert new['expected_plan'] != old['expected_plan']
    assert not fake.methods('tools/call')
    switch_history(ui, True)
    batch = {'name': 'chain', 'examples': [{'id': row['id'], 'version': 1} for row in examples]}
    confirm = send(ui, 'plans', action='workflow_run', request=batch)
    assert [row['kind'] for row in confirm['required_confirmations']] == ['workflow_mcp']
    ident, result = run_workflow(ui, batch)
    assert result['outcome'] == 'complete', result
    assert result['metrics']['checks_passed'] == 2
    assert result['metrics']['correct'] == result['metrics']['incorrect'] == 1
    assert len(fake.methods('tools/call')) == 2
    assert edit(ui, 'delete', {'name': 'chain', 'file_token': published['file_token']})['deleted']
    evidence = request(ui, '/api/workshop/workflow-evidence/' + ident)
    assert evidence['outcome'] == 'complete'
    assert evidence['intent'] is None
    assert {'capture.json', '0-only.json', '1-only.json', 'result.json'} <= set(evidence['records'])
    capture = request(ui, '/api/workshop/workflow-evidence/' + ident + '?record=capture.json')
    assert capture['review']['executable_digest'] == result['executable_digest']
    assert capture['review']['saving'] is True
    assert capture['definition']['name'] == 'chain'


def test_local_only_final_alias_protects_shared_source_before_any_model_call(ui, server):
    from conftest import save_profile, save_roles

    save_profile(ui, 'p', model='m', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'fast': 'p'})
    draft = {'key': 'writer', 'files': {}, 'definition_text': jsonio.encode_exact({
        'format_version': 4, 'name': 'writer', 'inputs': {'text': port()}, 'outputs': {'out': port()},
        'steps': [{'id': 'write', 'kind': 'model', 'role': 'fast', 'response_mode': 'named_json',
                   'prompt': {'text': '{{inputs.text}}'}}]})}
    source = plan_save(ui, draft)
    binding = {'kind': 'input', 'port': 'text'}
    definition = {'name': 'chain', 'purpose': '', 'inputs': {'text': port()}, 'constants': {},
                  'outputs': {'out': {**port(restricted=True), 'binding': binding}},
                  'nodes': {'only': {'bindings': {'text': binding}}}}
    edit(ui, 'save', {'name': 'chain', 'file_token': None, 'definition_text': jsonio.encode_exact(definition),
                      'selections': {'only': {'key': 'writer', 'revision': source['durable_id']}}})
    selection = {'name': 'chain', 'inputs': {'text': 'protected'}}
    plan = send(ui, 'plans', action='workflow_run', request=selection)
    assert plan['descriptor']['protected_nodes'] == ['only']
    assert any(row['code'] == 'destination_untrusted' for row in plan['descriptor']['issues'])
    refusal = failure_of(lambda: send(ui, 'workflows/chain/run', request=selection,
        expected_plan=plan['expected_plan'], ticket=send(ui, 'tickets')['ticket']))
    assert refusal.body['code'] == 'preflight_refused'
    assert not server.requests


def test_workflow_evidence_intent_failure_stops_before_node(ui, monkeypatch):
    from conftest import switch_history
    from agentwiz.workflow_evidence import Evidence
    from agentwiz import transforms

    publish_workflow(ui, transform('count', 'count_text', {'unit': 'characters'}, {'text': port()}, port('json')))
    switch_history(ui, True)

    def fail(*_args):
        raise OSError('injected write error')

    monkeypatch.setattr(Evidence, 'pending', fail)
    monkeypatch.setattr(transforms, 'execute', lambda *_args: pytest.fail('node ran without durable intent'))
    ident, result = run_workflow(ui, {'name': 'chain', 'inputs': {'text': 'test'}})
    assert result['outcome'] == 'failed'
    assert result['nodes']['only']['outcome'] == 'not_run'
    assert result['nodes']['only']['reason'] == 'evidence_write_failed'
    assert result['issues'][0]['code'] == 'evidence_write_failed'
    assert request(ui, '/api/workshop/workflow-evidence/' + ident)['outcome'] == 'failed'


def test_saved_workflow_refuses_capture_with_newly_known_credential(ui, monkeypatch):
    from conftest import switch_history

    secret = 'known-credential-now'
    publish_workflow(ui, transform('report', 'format_report', {'template': secret + ' {{inputs.text}}'},
                                   {'text': port()}, port()))
    switch_history(ui, True)
    selection = {'name': 'chain', 'inputs': {'text': 'test'}}
    plan = send(ui, 'plans', action='workflow_run', request=selection)
    monkeypatch.setattr(ui.state, 'credentials', lambda: [secret])
    failure = failure_of(lambda: send(ui, 'workflows/chain/run', request=selection,
        expected_plan=plan['expected_plan'], ticket=send(ui, 'tickets')['ticket']))
    assert failure.body['code'] == 'credential_in_asset'
    assert secret not in str(failure.body)
    assert not (ui.workspace.path / 'runs' / 'workflows').exists()


def test_workflow_stop_after_mcp_send_preserves_unknown_and_skips_remaining_examples(ui, make_fake_mcp):
    from conftest import add_server, simple_tool

    fake = make_fake_mcp('modern', tools=[simple_tool('lookup', behave=lambda call: (call.start_sse(), call.hold()))])
    add_server(ui, 'docs', fake.url)
    draft = {'key': 'lookup', 'files': {}, 'definition_text': jsonio.encode_exact({
        'format_version': 4, 'name': 'lookup', 'inputs': {'text': port()}, 'outputs': {'out': port('json')},
        'steps': [{'id': 'lookup', 'kind': 'tool', 'server': 'docs', 'tool': 'lookup',
                   'arguments': {'q': '{{inputs.text}}'}, 'output': 'out'}]})}
    examples = [{'id': ident, 'version': 1, 'inputs': {'text': ident},
                 'expectation': {'kind': 'review', 'criterion': ''}} for ident in ('first', 'second', 'third')]
    publish_workflow(ui, draft, examples=examples)
    selection = {'name': 'chain', 'examples': [{'id': example['id'], 'version': 1} for example in examples]}
    plan = send(ui, 'plans', action='workflow_run', request=selection)
    ident = send(ui, 'workflows/chain/run', request=selection, expected_plan=plan['expected_plan'],
                 ticket=send(ui, 'tickets')['ticket'], confirmations=plan['required_confirmations'])['operation_id']
    route = '/api/workshop/workflow-jobs/' + ident
    try:
        wait_until(lambda: fake.holding > 0)
        send(ui, 'workflow-jobs/' + ident + '/stop')
        wait_until(lambda: request(ui, route)['outcome'] != 'running')
        result = request(ui, route)
        assert result['outcome'] == 'unknown'
        assert [row['outcome'] for row in result['examples']] == ['unknown', 'not_run', 'not_run']
        assert [row['reason'] for row in result['examples'][1:]] == ['call_unknown', 'call_unknown']
        assert len(fake.methods('tools/call')) == 1
        assert not (ui.workspace.path / 'runs' / 'workflows').exists()
    finally:
        fake.release.set()


def test_workflow_stop_wins_before_example_output_publication(ui, monkeypatch):
    from agentwiz.workflow_runner import Job
    from conftest import switch_history

    examples = [{'id': ident, 'version': 1, 'inputs': {'text': ident},
                 'expectation': {'kind': 'review', 'criterion': ''}} for ident in ('first', 'second')]
    publish_workflow(ui, transform('count', 'count_text', {'unit': 'characters'}, {'text': port()}, port('json')),
                     examples=examples)
    switch_history(ui, True)
    execute = Job.execute

    def stop_before_publication(job, *args):
        row = execute(job, *args)
        assert row['outcome'] == 'complete'
        job.cancel()
        return row

    monkeypatch.setattr(Job, 'execute', stop_before_publication)
    ident, result = run_workflow(ui, {'name': 'chain', 'examples': [
        {'id': example['id'], 'version': 1} for example in examples]})
    first, second = result['examples']
    assert result['outcome'] == first['outcome'] == 'stopped'
    assert first['nodes']['only']['outcome'] == 'complete'
    assert first['outputs'] == {}
    assert second['outcome'] == 'not_run' and second['reason'] == 'user_stop'
    saved = request(ui, '/api/workshop/workflow-evidence/' + ident)
    assert '0-outputs.json' not in saved['records']
