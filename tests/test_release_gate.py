"""Release boundaries: prevent premature disclosure, stale consent and advisory-result bypass."""
import copy
import hashlib
from contextlib import ExitStack

import pytest

from agentwiz import jsonio
from agentwiz.workflow_runner import Graph
from conftest import answer, failure_of, request, save_profile, save_roles, switch_history, wait_until
from test_workflows import edit, port, transform
from test_workshop import plan_save, send


def node(name, output='out'):
    return {'kind': 'node', 'node': name, 'port': output}


def gate(value, *, scan=None, assessment=False, contract=None):
    row = {'gate': {'kind': 'release', 'title': 'Review candidate', 'contract': contract or {'format': 'text'},
                    'assessment': {'enabled': assessment, **({'role': 'assessor'} if assessment else {})}},
           'bindings': {'value': value}}
    if scan is not None:
        row['bindings']['scan'] = scan
    return row


def publish_gate(ui, *, candidate='Redacted [P1]', mapping=None, assessment=False, examples=None,
                 contract=None, consumer_local=False):
    declaration = port('json' if contract and contract['format'] == 'json' else 'text', restricted=True)
    if contract:
        declaration['contract'] = contract
    definition = {'name': 'gated', 'purpose': '', 'inputs': {'text': declaration}, 'constants': {},
                  'nodes': {'review': gate({'kind': 'input', 'port': 'text'}, assessment=assessment, contract=contract)},
                  'outputs': {'out': {**port(declaration['contract']['format']), 'binding': node('review', 'released')}}}
    if mapping is not None:
        definition['constants']['mapping'] = {'value': mapping, 'local_only': True}
        definition['nodes']['review']['bindings']['scan'] = {'kind': 'constant', 'name': 'mapping'}
    # A local transform after the gate makes output publication depend on actual approval.
    saved = plan_save(ui, transform('echo', 'format_report', {'template': '{{inputs.text}}'},
                                    {'text': port(restricted=consumer_local)}, port()))
    if declaration['contract']['format'] == 'text':
        definition['nodes']['echo'] = {'bindings': {'text': node('review', 'released')}}
        definition['outputs']['out']['binding'] = node('echo')
        selections = {'echo': {'key': 'echo', 'revision': saved['durable_id']}}
    else:
        selections = {}
    body = {'name': 'gated', 'file_token': None, 'definition_text': jsonio.encode_exact(definition), 'selections': selections}
    if examples:
        body['examples'] = examples
    edit(ui, 'save', body)
    return candidate


def start(ui, *, text='Redacted [P1]', examples=None):
    selection = {'name': 'gated', **({'examples': examples} if examples else {'inputs': {'text': text}})}
    plan = send(ui, 'plans', action='workflow_run', request=selection)
    assert not [issue for issue in plan['descriptor']['issues'] if issue['severity'] == 'error'], plan
    ident = send(ui, 'workflows/gated/run', request=selection, expected_plan=plan['expected_plan'],
                 ticket=send(ui, 'tickets')['ticket'], confirmations=plan['required_confirmations'])['operation_id']
    return ident, '/api/workshop/workflow-jobs/' + ident


def review(ui, route):
    if not wait_until(lambda: request(ui, route).get('phase') == 'awaiting_release'):
        result = request(ui, route)
        details = {name: request(ui, route + '?node=' + name) for name, row in result.get('nodes', {}).items() if row.get('detail_available')}
        pytest.fail(str(details or result))
    return request(ui, route)['release']


def decide(ui, ident, current, decision='release', **ack):
    return send(ui, f'workflow-jobs/{ident}/release', **{
        key: current[key] for key in ('token', 'payload_digest', 'executable_digest', 'scope_digest', 'checks_digest')},
        decision=decision, **ack)


def done(ui, route):
    wait_until(lambda: request(ui, route)['outcome'] != 'running')
    return request(ui, route)


def trust(ui, server, model):
    destination = {'kind': 'openai_compat', 'url': server.url + '/v1', 'model': model, 'provider': ''}
    grants = request(ui, '/api/workshop/destination-trust')
    send(ui, 'destination-trust', expected_revision=grants['revision'], destination=destination, trusted=True)


def test_gate_preserves_source_restriction_and_requires_one_shot_decision(ui):
    publish_gate(ui)
    ident, route = start(ui)
    current = review(ui, route)
    assert current['payload'] == 'Redacted [P1]'
    assert current['payload_digest'] == hashlib.sha256(current['payload'].encode()).hexdigest()
    assert request(ui, route)['nodes']['echo']['outcome'] == 'not_run'
    stale = {**current, 'payload_digest': '0' * 64}
    assert failure_of(lambda: decide(ui, ident, stale)).body['code'] == 'release_stale'
    first = decide(ui, ident, current)
    assert decide(ui, ident, current) == first
    assert failure_of(lambda: decide(ui, ident, current, 'reject')).body['code'] == 'release_stale'
    result = done(ui, route)
    assert result['outcome'] == 'complete' and result['outputs']['out']['text'] == current['payload']
    assert result['outputs']['out']['local_only'] is False
    assert not (ui.workspace.path / 'runs' / 'workflows').exists()
    bundle = jsonio.decode_file_exact((ui.workspace.path / 'workflows/gated.json').read_bytes())
    assert bundle['format_version'] == 2
    with ExitStack() as stack:
        graph = Graph(bundle, 'gated', stack)
        assert ('input', 'text') in graph.restricted
        assert ('node', 'review', 'released') not in graph.restricted
    ident2, route2 = start(ui)
    current2 = review(ui, route2)
    assert current2['token'] != current['token']
    assert failure_of(lambda: decide(ui, ident2, current)).body['code'] == 'release_stale'
    decide(ui, ident2, current2, 'reject')
    assert done(ui, route2)['outcome'] == 'release_rejected'


@pytest.mark.parametrize('action,expected', [('reject', 'release_rejected'), ('stop', 'stopped')])
def test_gate_reject_stop_terminate_batch_without_output(ui, action, expected):
    examples = [{'id': name, 'version': 1, 'inputs': {'text': 'same'},
                 'expectation': {'kind': 'review', 'criterion': ''}} for name in ('a', 'b')]
    publish_gate(ui, examples=examples)
    ident, route = start(ui, examples=[{'id': name, 'version': 1} for name in ('a', 'b')])
    current = review(ui, route)
    if action == 'stop':
        send(ui, f'workflow-jobs/{ident}/stop')
    else:
        decide(ui, ident, current, 'reject')
    result = done(ui, route)
    assert result['outcome'] == expected
    assert result['examples'][0]['outputs'] == {}
    assert result['examples'][1]['outcome'] == 'not_run'
    assert failure_of(lambda: decide(ui, ident, current)).body['code'] == 'release_stale'


def test_identical_examples_need_independent_approval(ui):
    examples = [{'id': name, 'version': 1, 'inputs': {'text': 'same'},
                 'expectation': {'kind': 'review', 'criterion': ''}} for name in ('a', 'b')]
    publish_gate(ui, examples=examples)
    ident, route = start(ui, examples=[{'id': name, 'version': 1} for name in ('a', 'b')])
    first = review(ui, route)
    decide(ui, ident, first)
    wait_until(lambda: (request(ui, route).get('release') or {}).get('example_index') == 1)
    second = review(ui, route)
    assert first['payload_digest'] == second['payload_digest'] and first['token'] != second['token']
    decide(ui, ident, first)  # idempotent retry has no effect on second pause
    assert request(ui, route)['phase'] == 'awaiting_release'
    decide(ui, ident, second)
    assert done(ui, route)['outcome'] == 'complete'


def test_scan_acknowledgement_is_server_enforced_and_evidence_omits_originals(ui):
    publish_gate(ui, mapping=[{'original': 'Alice', 'placeholder': '[P1]'}])
    switch_history(ui, True)
    ident, route = start(ui, text='ALICE remains')
    current = review(ui, route)
    assert current['identifier_check']['hits'][0]['start'] == 0
    assert failure_of(lambda: decide(ui, ident, current)).body['code'] == 'release_acknowledgement_required'
    decide(ui, ident, current, acknowledge_identifiers=True)
    assert done(ui, route)['outcome'] == 'complete'
    evidence = request(ui, '/api/workshop/workflow-evidence/' + ident)
    filename = next(name for name in evidence['records'] if name.endswith('release-decision.json'))
    record = request(ui, '/api/workshop/workflow-evidence/' + ident + '?record=' + filename)
    encoded = jsonio.encode_exact(record)
    assert 'ALICE' not in encoded and 'Alice' not in encoded and current['token'] not in encoded
    assert request(ui, '/api/workshop/workflow-active') == {}


@pytest.mark.parametrize('mapping', [[{'original': ''}], [None], [''], 'not a list'])
def test_invalid_scan_never_shows_clean_or_releases(ui, mapping):
    publish_gate(ui, mapping=mapping)
    _, route = start(ui)
    result = done(ui, route)
    assert result['outcome'] == 'rejected'
    assert result['issues'][0]['code'] == 'scan_invalid'
    assert result['nodes']['echo']['outcome'] == 'not_run'


def test_json_unicode_scan_and_restriction_round_trip(ui):
    contract = {'format': 'json'}
    publish_gate(ui, mapping=['STRASSE'], contract=contract)
    ident, route = start(ui, text='{"Straße": [null, false, 9007199254740993, "Straße"]}')
    current = review(ui, route)
    hits = current['identifier_check']['hits']
    assert len(hits) == 2 and all(hit['start'] == 0 and hit['end'] == 6 for hit in hits)
    assert hits[0]['part'] == 'key' and hits[1]['path'] == ['Straße', 3]
    decide(ui, ident, current, acknowledge_identifiers=True)
    result = done(ui, route)
    assert result['outcome'] == 'complete'
    assert jsonio.decode_exact(result['outputs']['out']['text']) == {'Straße': [None, False, 9007199254740993, 'Straße']}


@pytest.mark.parametrize('findings', [[], [{'concern': 'Context', 'location': 'candidate', 'explanation': 'Exact date may identify someone.'}]])
def test_local_assessment_protected_and_acknowledged(ui, server, findings):
    save_profile(ui, 'local', model='risk', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'assessor': 'local'})
    trust(ui, server, 'risk')
    publish_gate(ui, assessment=True, mapping=['Alice'])
    server.reply(answer(jsonio.encode_exact({'assessment': {'findings': findings}})))
    ident, route = start(ui)
    current = review(ui, route)
    assert current['assessment'] == {'status': 'complete', 'findings': findings}
    assert len(server.requests) == 1
    if findings:
        assert failure_of(lambda: decide(ui, ident, current)).body['code'] == 'release_acknowledgement_required'
    decide(ui, ident, current, acknowledge_assessment=True)
    result = done(ui, route)
    assert result['outcome'] == 'complete' and result['model_attempts'] == 1
    assert 'Exact date' not in result['outputs']['out']['text']
    trace = request(ui, route + '?node=review')
    assert trace['release_review']['assessment']['findings'] == findings
    assert current['token'] not in jsonio.encode_exact(trace)


def test_assessment_unavailable_requires_acknowledgement(ui, server):
    save_profile(ui, 'local', model='risk', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'assessor': 'local'})
    trust(ui, server, 'risk')
    publish_gate(ui, assessment=True)
    server.reply(answer('bad json'), answer('still invalid'))
    ident, route = start(ui)
    current = review(ui, route)
    assert current['assessment']['status'] == 'unavailable'
    assert failure_of(lambda: decide(ui, ident, current)).body['code'] == 'release_acknowledgement_required'
    decide(ui, ident, current, acknowledge_assessment=True)
    assert done(ui, route)['outcome'] == 'complete'


def test_assessment_without_grant_fails_preflight(ui, server):
    save_profile(ui, 'local', model='risk', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'assessor': 'local'})
    publish_gate(ui, assessment=True)
    plan = send(ui, 'plans', action='workflow_run', request={'name': 'gated', 'inputs': {'text': 'candidate'}})
    assert any(issue['code'] == 'destination_untrusted' for issue in plan['descriptor']['issues'])
    assert server.requests == []


def test_gate_rerestriction_remains_local(ui):
    publish_gate(ui, consumer_local=True)
    ident, route = start(ui)
    current = review(ui, route)
    decide(ui, ident, current)
    assert done(ui, route)['outputs']['out']['local_only'] is True


def model_draft(name, role, inputs, outputs):
    return {'key': name, 'files': {}, 'definition_text': jsonio.encode_exact({
        'format_version': 4, 'name': name, 'inputs': inputs, 'outputs': outputs,
        'steps': [{'id': 'run', 'kind': 'model', 'role': role, 'response_mode': 'named_json',
                   'prompt': {'text': '\n'.join('{{inputs.' + key + '}}' for key in inputs)}}]})}


def publish_cloud_chain(ui, local, cloud, *, unsafe=False, second_gate=False, mapping_to_cloud=False):
    from conftest import KEY
    save_profile(ui, 'local', model='local', kind='openai_compat', base_url=local.url + '/v1', provider='')
    save_profile(ui, 'remote', model='remote', kind='openrouter', base_url=cloud.url + '/v1', provider='', api_key=KEY)
    save_roles(ui, {'local': 'local', 'cloud': 'remote'})
    trust(ui, local, 'local')
    mapping = {'contract': {'format': 'json'}, 'local_only': unsafe}
    drafts = [model_draft('deid', 'local', {'text': port()},
                          {'redacted': port(restricted=unsafe), 'mapping': mapping}),
              model_draft('cloud', 'cloud', {'text': port(), **({'mapping': port('json')} if mapping_to_cloud else {})}, {'out': port()}),
              model_draft('reid', 'local', {'text': port(), 'mapping': port('json')}, {'out': port()})]
    saved = {draft['key']: plan_save(ui, draft)['durable_id'] for draft in drafts}
    definition = {'name': 'gated', 'purpose': '', 'inputs': {'text': port(restricted=not unsafe)}, 'constants': {},
                  'outputs': {'out': {**port(), 'binding': node('reid')}},
                  'nodes': {'deid': {'bindings': {'text': {'kind': 'input', 'port': 'text'}}},
                            'review': gate(node('deid', 'redacted'), scan=node('deid', 'mapping')),
                            'cloud': {'bindings': {'text': node('review', 'released'),
                                                   **({'mapping': node('deid', 'mapping')} if mapping_to_cloud else {})}},
                            'reid': {'bindings': {'text': node('cloud'), 'mapping': node('deid', 'mapping')}}}}
    if second_gate:
        definition['nodes']['review_again'] = gate(node('cloud'))
        definition['nodes']['reid']['bindings']['text'] = node('review_again', 'released')
    body = {'name': 'gated', 'file_token': None, 'definition_text': jsonio.encode_exact(definition),
            'selections': {name: {'key': name, 'revision': revision} for name, revision in saved.items()}}
    edit(ui, 'save', body)
    return definition


def test_actual_cloud_dispatch_contains_only_approved_candidate(ui, make_server):
    local, cloud = make_server(), make_server()
    publish_cloud_chain(ui, local, cloud)
    local.reply(answer('{"redacted":"[P1] has a diagnosis", "mapping":[{"original":"Alice", "placeholder":"[P1]"}]}'),
                answer('{"out":"Alice has a diagnosis"}'))
    cloud.reply(answer('{"out":"[P1]: diagnosis"}'))
    ident, route = start(ui, text='Alice has a diagnosis')
    current = review(ui, route)
    assert set(current['destinations']) == {'cloud', 'reid'}
    assert cloud.requests == []
    decide(ui, ident, current)
    result = done(ui, route)
    assert result['outcome'] == 'complete', result
    assert result['outputs']['out']['local_only'] is True
    assert len(cloud.requests) == 1 and len(local.requests) == 2
    encoded = jsonio.encode_exact(cloud.requests[0]['body'])
    assert '[P1] has a diagnosis' in encoded and 'Alice' not in encoded and 'mapping' not in encoded


def test_sequential_gate_allows_previously_approved_cloud_ancestor(ui, make_server):
    local, cloud = make_server(), make_server()
    publish_cloud_chain(ui, local, cloud, second_gate=True)
    local.reply(answer('{"redacted":"[P1]", "mapping":["Alice"]}'), answer('{"out":"Alice"}'))
    cloud.reply(answer('{"out":"[P1] summary"}'))
    ident, route = start(ui, text='Alice')
    first = review(ui, route)
    assert set(first['destinations']) == {'cloud'}
    decide(ui, ident, first)
    wait_until(lambda: (request(ui, route).get('release') or {}).get('gate') == 'review_again')
    second = review(ui, route)
    assert second['token'] != first['token'] and len(local.requests) == 1
    decide(ui, ident, second)
    assert done(ui, route)['outcome'] == 'complete'


@pytest.mark.parametrize('unsafe,mapping_to_cloud,code', [(True, False, 'unrestricted_ancestor'),
                                                         (False, True, 'destination_ineligible')])
def test_unsafe_ancestry_and_mapping_cloud_paths_fail_preflight(ui, make_server, unsafe, mapping_to_cloud, code):
    local, cloud = make_server(), make_server()
    publish_cloud_chain(ui, local, cloud, unsafe=unsafe, mapping_to_cloud=mapping_to_cloud)
    plan = send(ui, 'plans', action='workflow_run', request={'name': 'gated', 'inputs': {'text': 'Alice'}})
    assert any(issue['code'] == code and issue['severity'] == 'error' for issue in plan['descriptor']['issues'])
    assert local.requests == cloud.requests == []


def test_expiry_ends_run_and_sends_nothing(ui, monkeypatch):
    from agentwiz import release_gate
    monkeypatch.setattr(release_gate, 'review_wait', lambda config: 0)
    publish_gate(ui)
    _, route = start(ui)
    result = done(ui, route)
    assert result['outcome'] == 'release_expired' and result['nodes']['echo']['outcome'] == 'not_run'


def test_decision_evidence_failure_prevents_downstream_execution(ui, monkeypatch):
    from agentwiz.workflow_evidence import Evidence
    publish_gate(ui)
    switch_history(ui, True)
    original = Evidence.release_record
    def fail_decision(evidence, node_id, kind, record):
        if kind == 'decision':
            raise OSError('synthetic write failure')
        return original(evidence, node_id, kind, record)
    monkeypatch.setattr(Evidence, 'release_record', fail_decision)
    ident, route = start(ui)
    current = review(ui, route)
    decide(ui, ident, current)
    result = done(ui, route)
    assert result['outcome'] == 'failed'
    assert result['nodes']['echo']['outcome'] == 'not_run'
    assert result['issues'][0]['code'] == 'evidence_write_failed'


def test_indirect_destination_through_transform_is_in_scope(ui, server):
    save_profile(ui, 'remote', model='remote', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'remote': 'remote'})
    publish_gate(ui)
    saved = plan_save(ui, model_draft('consumer', 'remote', {'text': port()}, {'out': port()}))
    bundle = request(ui, '/api/workshop/workflows/gated')
    definition = jsonio.decode_exact(bundle['definition_text'])
    definition['nodes']['consumer'] = {'bindings': {'text': node('echo')}}
    definition['outputs']['out']['binding'] = node('consumer')
    edit(ui, 'save', {'name': 'gated', 'file_token': bundle['file_token'], 'definition_text': jsonio.encode_exact(definition),
                      'selections': {'consumer': {'key': 'consumer', 'revision': saved['durable_id']}}})
    ident, route = start(ui)
    current = review(ui, route)
    assert set(current['destinations']) == {'consumer'} and server.requests == []
    decide(ui, ident, current, 'reject')
    assert done(ui, route)['outcome'] == 'release_rejected' and server.requests == []


def test_release_tutorial_prepares_in_its_temporary_workspace(ui):
    request(ui, '/api/tutorial/start', {})
    child = ui.tutorial.child
    try:
        result = request(child, '/api/tutorial/step', {'topic': 'release-gate'})
    except Exception as exc:
        if hasattr(exc, 'body'):
            pytest.fail(str(exc.body))
        raise
    assert result['topic'] == 'release-gate'
    assert request(child, '/api/workshop/workflows/tutorial_release')['node_ports']['review']['kind'] == 'gate'


def test_concurrent_decisions_accept_exactly_one(ui):
    import threading
    publish_gate(ui)
    ident, route = start(ui)
    current = review(ui, route)
    barrier = threading.Barrier(3)
    results = []
    def submit(choice):
        barrier.wait()
        try:
            results.append(decide(ui, ident, current, choice)['status'])
        except Exception as exc:
            results.append(getattr(exc, 'body', {}).get('code', type(exc).__name__))
    workers = [threading.Thread(target=submit, args=(choice,)) for choice in ('release', 'reject')]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(10)
        assert not worker.is_alive()
    assert sorted(results) == ['accepted', 'release_stale']
    assert done(ui, route)['outcome'] in ('complete', 'release_rejected')


def test_scan_limit_cannot_be_mistaken_for_clean(ui, monkeypatch):
    from agentwiz import release_gate
    monkeypatch.setattr(release_gate, 'SCAN_HITS', 1)
    publish_gate(ui, mapping=['Alice'])
    _, route = start(ui, text='Alice Alice')
    result = done(ui, route)
    assert result['outcome'] == 'rejected' and result['issues'][0]['code'] == 'scan_limit'


def test_stale_execution_timer_cannot_cancel_human_wait(ui, monkeypatch):
    from agentwiz import runner
    callbacks = []
    make_timer = runner.make_timer
    def capture_timer(seconds, callback):
        callbacks.append(callback)
        return make_timer(seconds, callback)
    monkeypatch.setattr(runner, 'make_timer', capture_timer)
    publish_gate(ui)
    ident, route = start(ui)
    current = review(ui, route)
    callbacks[0]()  # fired after cancellation: must not consume human review time
    assert request(ui, route)['phase'] == 'awaiting_release'
    decide(ui, ident, current)
    assert done(ui, route)['outcome'] == 'complete'


def test_stop_interrupts_assessment_without_creating_released_output(ui, server):
    from conftest import Reply
    save_profile(ui, 'local', model='risk', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'assessor': 'local'})
    trust(ui, server, 'risk')
    publish_gate(ui, assessment=True)
    server.reply(Reply(hold_headers=True))
    ident, route = start(ui)
    assert server.wait_holding(1)
    send(ui, f'workflow-jobs/{ident}/stop')
    result = done(ui, route)
    assert result['outcome'] == 'stopped' and result['outputs'] == {}
    assert len(server.requests) == 1


def test_assessment_accepts_existing_hyphenated_role_names(ui, server):
    save_profile(ui, 'local', model='risk', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'risk-review': 'local'})
    trust(ui, server, 'risk')
    publish_gate(ui)
    saved = request(ui, '/api/workshop/workflows/gated')
    definition = jsonio.decode_exact(saved['definition_text'])
    definition['nodes']['review']['gate']['assessment'] = {'enabled': True, 'role': 'risk-review'}
    edit(ui, 'save', {'name': 'gated', 'file_token': saved['file_token'], 'definition_text': jsonio.encode_exact(definition)})
    server.reply(answer('{"assessment":{"findings":[]}}'))
    ident, route = start(ui)
    current = review(ui, route)
    assert current['assessment']['status'] == 'complete'
    decide(ui, ident, current)
    assert done(ui, route)['outcome'] == 'complete'


def test_rerestricted_cloud_value_reports_the_port_that_caused_it(ui, server):
    from conftest import KEY
    save_profile(ui, 'remote', model='remote', kind='openrouter', base_url=server.url + '/v1', provider='', api_key=KEY)
    save_roles(ui, {'cloud': 'remote'})
    publish_gate(ui)
    model = plan_save(ui, model_draft('cloud', 'cloud', {'text': port(restricted=True)}, {'out': port()}))
    saved = request(ui, '/api/workshop/workflows/gated')
    definition = jsonio.decode_exact(saved['definition_text'])
    definition['nodes']['cloud'] = {'bindings': {'text': node('review', 'released')}}
    edit(ui, 'save', {'name': 'gated', 'file_token': saved['file_token'], 'definition_text': jsonio.encode_exact(definition),
                      'selections': {'cloud': {'key': 'cloud', 'revision': model['durable_id']}}})
    plan = send(ui, 'plans', action='workflow_run', request={'name': 'gated', 'inputs': {'text': 'candidate'}})
    assert any(issue['code'] == 'destination_ineligible' and issue['location'] == 'nodes.cloud.inputs.text'
               for issue in plan['descriptor']['issues'])
    assert server.requests == []


def test_changed_destination_after_review_cannot_receive_approved_value(ui, server):
    save_profile(ui, 'remote', model='before', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'remote': 'remote'})
    publish_gate(ui)
    model = plan_save(ui, model_draft('cloud', 'remote', {'text': port()}, {'out': port()}))
    saved = request(ui, '/api/workshop/workflows/gated')
    definition = jsonio.decode_exact(saved['definition_text'])
    definition['nodes']['cloud'] = {'bindings': {'text': node('review', 'released')}}
    definition['outputs']['out']['binding'] = node('cloud')
    edit(ui, 'save', {'name': 'gated', 'file_token': saved['file_token'], 'definition_text': jsonio.encode_exact(definition),
                      'selections': {'cloud': {'key': 'cloud', 'revision': model['durable_id']}}})
    ident, route = start(ui)
    current = review(ui, route)
    save_profile(ui, 'remote', model='after', kind='openai_compat', base_url=server.url + '/v1', provider='')
    decide(ui, ident, current)
    result = done(ui, route)
    assert result['outcome'] != 'complete' and result['outputs'] == {}
    assert any(issue['code'] == 'destination_changed' for issue in result['issues'])
    assert server.requests == []


@pytest.mark.parametrize('unsafe_branch', [False, True])
def test_later_gate_handles_public_context_and_checks_separate_model_ancestors(ui, make_server, unsafe_branch):
    local, cloud = make_server(), make_server()
    publish_cloud_chain(ui, local, cloud, second_gate=True)
    saved = request(ui, '/api/workshop/workflows/gated')
    definition = jsonio.decode_exact(saved['definition_text'])
    definition['inputs']['context'] = port()
    extra = {'kind': 'input', 'port': 'context'}
    selections = {}
    if unsafe_branch:
        draft = model_draft('unreviewed', 'cloud', {'text': port()}, {'out': port()})
        revision = plan_save(ui, draft)['durable_id']
        definition['nodes']['unreviewed'] = {'bindings': {'text': extra}}
        selections['unreviewed'] = {'key': 'unreviewed', 'revision': revision}
        extra = node('unreviewed')
    draft = model_draft('cloud_context', 'cloud', {'text': port(), 'context': port()}, {'out': port()})
    revision = plan_save(ui, draft)['durable_id']
    selections['cloud'] = {'key': 'cloud_context', 'revision': revision}
    definition['nodes']['cloud']['bindings']['context'] = extra
    edit(ui, 'rebind', {'name': 'gated', 'file_token': saved['file_token'], 'definition_text': jsonio.encode_exact(definition),
                        'selections': selections})
    plan = send(ui, 'plans', action='workflow_run', request={'name': 'gated', 'inputs': {'text': 'Alice', 'context': 'Public question'}})
    ancestor_errors = [issue for issue in plan['descriptor']['issues'] if issue['code'] == 'unrestricted_ancestor' and issue['severity'] == 'error']
    assert bool(ancestor_errors) is unsafe_branch
    assert local.requests == cloud.requests == []
