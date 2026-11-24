"""Native private-summary workflow through the reviewed Workshop HTTP path."""
import json

from conftest import KEY, answer, save_profile, save_roles
from test_release_gate import decide, done, review, trust
from test_workshop import send


def test_contextual_cloud_summary_exact_send(ui, server, make_server):
    cloud = make_server()
    save_profile(ui, 'native_local', model='local', kind='openai_compat',
                 base_url=server.url + '/v1', provider='')
    save_profile(ui, 'native_cloud', model='remote', kind='openrouter',
                 base_url=cloud.url + '/v1', provider='OpenAI', routing_mode='manual', api_key=KEY)
    save_roles(ui, {'private_summary_local': 'native_local', 'private_summary_cloud': 'native_cloud'})
    trust(ui, server, 'local')
    server.reply(
        answer('{"surfaces":[{"surface":"Spot","kind":"person"}],"alias_links":[]}'),
        answer('{"decisions":[{"candidate_id":1,"decision":"identifier","span_id":0},{"candidate_id":2,"decision":"keep"}]}'),
        answer('{"findings":[]}'),
    )
    cloud.reply(answer('{"summary":"[[AWZ1_PERSON_1]] went home."}'))
    name = 'privacy_summary_cloud_v1'
    selection = {'name': name, 'inputs': {'source': 'Spot went home. Spot the difference.'}}
    plan = send(ui, 'plans', action='workflow_run', request=selection)
    assert not [issue for issue in plan['descriptor']['issues'] if issue['severity'] == 'error'], plan
    ident = send(ui, f'workflows/{name}/run', request=selection, expected_plan=plan['expected_plan'],
                 ticket=send(ui, 'tickets')['ticket'],
                 confirmations=plan['required_confirmations'])['operation_id']
    route = '/api/workshop/workflow-jobs/' + ident
    current = review(ui, route)
    assert current['coverage']['status'] == 'complete', current
    assert current['payload'] == '[[AWZ1_PERSON_1]] went home. Spot the difference.'
    assert not cloud.requests
    decide(ui, ident, current, acknowledge_identifiers=True)
    result = done(ui, route)
    assert result['outcome'] == 'complete', result
    assert result['outputs']['restored']['text'] == 'Spot went home.'
    assert result['outputs']['restored']['local_only']
    assert len(cloud.requests) == 1
    wire = cloud.requests[0]
    assert (wire['method'], wire['path']) == ('POST', '/v1/chat/completions')
    assert wire['body']['model'] == 'remote'
    assert wire['body']['messages'][1:] == [{'role': 'user', 'content': current['payload']}]
    assert 'tools' not in wire['body']
    assert 'Spot went home. Spot the difference.' not in json.dumps(wire['body'])
    assert decide(ui, ident, current, acknowledge_identifiers=True)['status'] == 'accepted'
    assert len(cloud.requests) == 1


def test_portable_workflow_runs_its_installed_capture(ui):
    from pathlib import Path

    source = (Path(__file__).parents[1] / 'docs/examples/count_words.component.json').read_text()
    preview = send(ui, 'component-plan', source=source)
    install = {'request': {'source': source}, 'expected_plan': preview['expected_plan'],
               'confirmations': preview['required_confirmations']}
    saved = send(ui, 'component-install', **install)
    assert send(ui, 'component-install', **install)['capture'] == saved['capture']
    workflow = send(ui, 'component-workflow', capture=saved['capture'])
    name = workflow['editor']['name']
    selection = {'name': name, 'capture': saved['capture'], 'inputs': {'text': 'one two three'}}
    plan = send(ui, 'plans', action='workflow_run', request=selection)
    ident = send(ui, f'workflows/{name}/run', request=selection, expected_plan=plan['expected_plan'],
                 ticket=send(ui, 'tickets')['ticket'],
                 confirmations=plan['required_confirmations'])['operation_id']
    result = done(ui, '/api/workshop/workflow-jobs/' + ident)
    assert result['outcome'] == 'complete', result
    assert result['outputs']['count']['text'] == '3'


def test_skill_import_is_inert_and_protected(ui, server):
    source = '---\nname: synthetic\ndescription: Review synthetic feedback\nallowed-tools: read script\n---\n' \
             'Read [the instructions](./script.py).\n'
    request = {'source': source}
    plan = send(ui, 'skill-plan', request=request)
    result = send(ui, 'skill-install', request=request, expected_plan=plan['expected_plan'],
                  confirmations=plan['required_confirmations'])
    assert result['context_local_only'] and result['visibility'] == 'private'
    assert './script.py' in result['instructions']
    assert not server.requests


def test_exact_browser_selection_and_deduplicated_scan():
    from agentwiz import private_transform

    source = '😀\r\nJo went home. Jo stayed.'
    correction = private_transform.correction(source, 4, 6, 'person')
    assert correction['start'] == 3 and correction['text'] == 'Jo'
    located = private_transform.locate(source, {'surfaces': [], 'alias_links': []}, [correction])
    candidate = private_transform.redact(source, located, [])
    assert candidate['candidate'] == '😀\r\n[[AWZ1_PERSON_1]] went home. Jo stayed.'
    assert list(private_transform.matches('Straße STRASSE', ['strasse'])) == [
        ('strasse', 0, 6), ('strasse', 7, 14)]
    pairs = [{'original': 'Will', 'placeholder': f'[[AWZ1_PERSON_{i}]]'} for i in range(1, 4)]
    hits = private_transform.scan('will ' * 40, pairs * 2)['hits']
    assert len(hits) == 40
    assert len(hits[0]['labels']) == 3


def test_scoped_scripted_worker_and_explicit_pool_promotion(ui):
    from pathlib import Path

    import pytest

    from agentwiz.scripted_worker import Worker
    from agentwiz.workshop import WorkshopError

    work = json.loads((Path(__file__).parents[1] / 'docs/examples/count_words.component.json').read_text())['root']
    service = ui.state.workshop

    def install(root):
        source = json.dumps({'format': 'agentwiz.component', 'format_version': 1, 'root': root})
        preview = send(ui, 'component-plan', source=source)
        return send(ui, 'component-install', request={'source': source}, expected_plan=preview['expected_plan'],
                    confirmations=preview['required_confirmations'])['capture']

    second = json.loads(json.dumps(work))
    second.update(id='examples/count-words-other')
    second['definition']['workflow']['name'] = 'count_other'
    agents = [{
        'inline': {'kind': 'agent', 'id': f'examples/{name}', 'title': name, 'category': 'analysis',
                   'visibility': 'private', 'definition': {
                       'instructions': 'Count words', 'mode': 'local_only', 'inputs': {}, 'outputs': {},
                       'capability_requests': [], 'artifact_requests': [],
                       'workflows': [{'inline': row} for row in workflows]}}
    } for name, workflows in [('counter', [work, second]), ('observer', [second])]]
    capture = install({'kind': 'team', 'id': 'examples/count-team', 'title': 'Counter team',
                       'visibility': 'private', 'category': 'analysis',
                       'definition': {'purpose': 'Count words', 'agents': agents}})
    from conftest import switch_history
    switch_history(ui, True)
    worker = Worker(service, capture, 0)
    with worker:
        with pytest.raises(WorkshopError) as busy, Worker(service, capture, 1):
            pass
        assert busy.value.code == 'admission_busy'
        first = worker.execute(0, {'text': 'one two'}, worker.plan(0, {'text': 'one two'}), scope='agent')
        second_result = worker.execute(1, {'text': 'one two three'}, worker.plan(1, {'text': 'one two three'}), scope='team')
        first_handle, second_handle = first['artifacts']['count'], second_result['artifacts']['count']
        assert worker.read(first_handle, scope='agent')['text'] == '2'
        assert worker.read(second_handle, scope='team')['text'] == '3'
        with pytest.raises(WorkshopError):
            worker.read(first_handle, scope='team')
    with pytest.raises(WorkshopError):
        worker.read(first_handle, scope='agent')
    from agentwiz import workflow_evidence
    state = ui.state
    for scope, row in (('agent', first), ('team', second_result)):
        manifest = workflow_evidence.read(state, row['evidence_id'])
        trace = workflow_evidence.read_record(state, row['evidence_id'], 'capture.json', manifest)
        assert manifest['outcome'] == 'complete'
        assert trace['producer']['worker_run'] == worker.job.ident
        assert trace['producer']['team_capture'] == capture
        assert trace['producer']['scope'] == scope
        assert 'artifacts' not in workflow_evidence.read_record(state, row['evidence_id'], 'result.json', manifest)

    work['visibility'] = 'public'
    ui.state.component_pool = ui.workspace.path / 'test-pool'
    installed = install(work)
    body = {'capture': installed, 'action': 'promote'}
    plan = send(ui, 'component-share', **body)
    result = send(ui, 'component-share', **body, expected_plan=plan['expected_plan'],
                  confirmations=plan['required_confirmations'])
    assert result['package']['root']['definition']['blocks']['counter']['inline']['visibility'] == 'private'
    assert [row['id'] for row in service.components.listing()['pool']] == [work['id']]


def test_skill_proposal_requires_exact_review_and_compiles_private_draft(ui, server):
    from conftest import failure_of

    save_profile(ui, 'proposal', model='remote', kind='openrouter', base_url=server.url + '/v1',
                 provider='OpenAI', routing_mode='manual', api_key=KEY)
    save_roles(ui, {'propose_agent': 'proposal'})
    server.reply(answer(json.dumps({'proposal': {'title': 'Feedback runner', 'purpose': 'Count comments',
        'instructions': 'Count items in feedback.', 'workflows': [{'title': 'Count words', 'steps': ['word_count']}]}})))
    source = '---\nname: feedback\ndescription: Count words\n---\nCount the words in the input.\n'
    request = {'eligible_source': source}
    plan = send(ui, 'skill-proposal', request=request)
    assert plan['descriptor']['issues'] == []
    assert not server.requests
    body = {'request': request, 'ticket': send(ui, 'tickets')['ticket'],
            'confirmations': plan['required_confirmations'], 'expected_plan': plan['expected_plan']}
    failure = failure_of(lambda: send(ui, 'skill-proposal', **{**body, 'request': {'eligible_source': source + 'Changed'}}))
    assert failure.body['code'] == 'confirmation_required'
    assert not server.requests
    result = send(ui, 'skill-proposal', **body)
    assert result['outcome'] == 'complete', result
    component = result['package']['root']
    assert component['visibility'] == 'private'
    assert component['definition']['workflows'][0]['inline']['visibility'] == 'private'
    assert len(server.requests) == 1
    assert send(ui, 'skill-proposal', **body)['outcome'] == 'complete'
    assert len(server.requests) == 1


def test_cloud_rerun_requires_fresh_coverage_bound_approval(ui, server, make_server):
    from conftest import failure_of

    cloud = make_server()
    save_profile(ui, 'native_local', model='local', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_profile(ui, 'native_cloud', model='remote', kind='openrouter', base_url=cloud.url + '/v1',
                 provider='OpenAI', routing_mode='manual', api_key=KEY)
    save_roles(ui, {'private_summary_local': 'native_local', 'private_summary_cloud': 'native_cloud'})
    trust(ui, server, 'local')
    name = 'privacy_summary_cloud_v1'
    source = '😀\r\nSpot left. Spot the difference.'

    def start(corrections=None):
        inputs = {'source': source}
        if corrections:
            inputs['corrections'] = json.dumps(corrections)
        selection = {'name': name, 'inputs': inputs}
        plan = send(ui, 'plans', action='workflow_run', request=selection)
        ident = send(ui, f'workflows/{name}/run', request=selection, expected_plan=plan['expected_plan'],
                     ticket=send(ui, 'tickets')['ticket'], confirmations=plan['required_confirmations'])['operation_id']
        route = '/api/workshop/workflow-jobs/' + ident
        return ident, route, review(ui, route)

    discovery = answer('{"surfaces":[{"surface":"Spot","kind":"person"}],"alias_links":[]}')
    detector = answer('{"findings":[]}')
    server.reply(discovery, answer('{"decisions":[{"candidate_id":2,"decision":"keep"}]}'), detector)
    ident, route, current = start()
    assert current['coverage']['status'] == 'incomplete'
    assert failure_of(lambda: decide(ui, ident, current)).body['code'] == 'release_acknowledgement_required'
    decide(ui, ident, current, 'rerun')
    assert done(ui, route)['outcome'] == 'release_rejected'
    assert not cloud.requests

    corrected = send(ui, 'privacy-correction', source=source, start=4, end=8, kind='person')
    server.reply(discovery, answer('{"decisions":[{"candidate_id":2,"decision":"keep"}]}'), detector)
    ident, route, fresh = start([corrected])
    assert fresh['token'] != current['token']
    assert fresh['payload'] == '😀\r\n[[AWZ1_PERSON_1]] left. Spot the difference.'
    assert fresh['coverage']['status'] == 'complete'
    stale = {**fresh, 'checks_digest': current['checks_digest']}
    assert failure_of(lambda: decide(ui, ident, stale)).body['code'] == 'release_stale'
    decide(ui, ident, fresh, 'reject')
    assert done(ui, route)['outcome'] == 'release_rejected'
    assert not cloud.requests


def test_fully_local_summary_uses_no_classifier_for_empty_queue(ui, server):
    save_profile(ui, 'native_local', model='local', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'private_summary_local': 'native_local'})
    trust(ui, server, 'local')
    server.reply(answer('{"surfaces":[],"alias_links":[]}'), answer('{"findings":[]}'),
                 answer('{"summary":"Contact [[AWZ1_EMAIL_1]] for the minutes."}'))
    name = 'privacy_summary_local_v1'
    selection = {'name': name, 'inputs': {'source': 'Contact test@example.invalid for the minutes.'}}
    plan = send(ui, 'plans', action='workflow_run', request=selection)
    ident = send(ui, f'workflows/{name}/run', request=selection, expected_plan=plan['expected_plan'],
                 ticket=send(ui, 'tickets')['ticket'], confirmations=plan['required_confirmations'])['operation_id']
    result = done(ui, '/api/workshop/workflow-jobs/' + ident)
    assert result['outcome'] == 'complete', result
    assert len(server.requests) == 3
    assert result['outputs']['restored']['text'] == selection['inputs']['source']
    assert '[[AWZ1_EMAIL_1]]' in result['outputs']['pseudonymised']['text']
    assert all(row['local_only'] for row in result['outputs'].values())


def test_fork_standalone_component_through_reviewed_save(ui):
    from pathlib import Path
    from conftest import failure_of

    work = json.loads((Path(__file__).parents[1] / 'docs/examples/count_words.component.json').read_text())
    work['root'] = work['root']['definition']['blocks']['counter']['inline']
    work['root']['definition']['block']['context_local_only'] = True
    source = json.dumps(work)
    plan = send(ui, 'component-plan', source=source)
    installed = send(ui, 'component-install', request={'source': source}, expected_plan=plan['expected_plan'],
                     confirmations=plan['required_confirmations'])
    request = {'capture': installed['capture'], 'target': 'private_counter'}
    fork = send(ui, 'component-block-fork', request=request)
    assert fork['visibility'] == 'private'
    plan = fork['save_plan']
    draft = fork['save_request']['draft']
    assert json.loads(draft['definition_text'])['context_local_only'] is True
    result = send(ui, 'save', **fork['save_request'], expected_plan=plan['expected_plan'],
                  confirmations=plan['required_confirmations'])
    assert result['durable_id']
    assert failure_of(lambda: send(ui, 'component-block-fork', request=request)).status == 400


def test_evaluator_uses_native_cloud_summary_and_grades_exact_redaction(ui, server, make_server, monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'scripts'))
    import privacy_workflow_eval as evaluate

    cloud = make_server()
    save_profile(ui, 'native_local', model='local', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_profile(ui, 'native_cloud', model='remote', kind='openrouter', base_url=cloud.url + '/v1',
                 provider='OpenAI', routing_mode='manual', api_key=KEY)
    save_roles(ui, {'private_summary_local': 'native_local', 'private_summary_cloud': 'native_cloud'})
    trust(ui, server, 'local')
    server.reply(
        answer('{"surfaces":[{"surface":"Spot","kind":"person"}],"alias_links":[]}'),
        answer('{"decisions":[{"candidate_id":1,"decision":"identifier","span_id":0},{"candidate_id":2,"decision":"keep"}]}'),
        answer('{"findings":[]}'),
    )
    cloud.reply(answer('{"summary":"[[AWZ1_PERSON_1]] left the meeting early."}'))
    result = evaluate.run_native(evaluate.Client(ui), cloud=True)
    assert result['checks_passed'], result
    assert result['redaction'] == {'targets': 1, 'covered': 1, 'replacements': 1, 'touching_target': 1, 'preserve': True}
    assert len(cloud.requests) == 1
    assert evaluate.grader_controls()
    assert sum(case['rank'] == 'high' and case['set'] == 'heldout' for case in evaluate.cases()) == 100
    assert sum(len(case['targets']) for case in evaluate.redaction_cases()) == 200


def test_evaluator_checkpoints_and_resumes_exact_detector_binding(ui, server, monkeypatch, tmp_path):
    from pathlib import Path
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'scripts'))
    import privacy_workflow_eval as evaluate

    save_profile(ui, 'openrouter-eval', model='synthetic', kind='openrouter', base_url=server.url + '/v1',
                 provider='OpenAI', routing_mode='manual', api_key=KEY)
    case = next(row for row in evaluate.cases() if row['id'] == 'dev-address')
    response = {'findings': [{'category': 'direct_identifier', 'quotes': [case['target']], 'reason': 'Exact home address'}]}
    server.reply(answer(json.dumps(response)))
    args = SimpleNamespace(backend='openrouter', resume=False, report=tmp_path / 'report.json')
    schedule = [(case, 1)]
    initial = {'build': {}, 'fixtures_digest': 'test', 'policy': 1, 'schedule': [[case['id'], 1]], 'results': []}
    report = dict(initial, results=[])
    client = evaluate.Client(ui)
    evaluate.run_detector(ui, client, args, schedule, report)
    assert report['results'][0]['rank_ok']
    assert len(server.requests) == 1
    args.resume = True
    resumed = dict(initial, results=[])
    evaluate.run_detector(ui, client, args, schedule, resumed)
    assert resumed['results'] == report['results']
    assert len(server.requests) == 1
