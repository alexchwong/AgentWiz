"""Reviewed component install and worker authority through the normal entry points."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from agentwiz import components, private_templates
from agentwiz.scripted_worker import Worker
from agentwiz.workshop import WorkshopError
from conftest import KEY, failure_of, request, save_profile, save_roles
from test_release_gate import done
from test_workshop import send

EXAMPLE = Path(__file__).resolve().parents[1] / 'docs/examples/count_words.component.json'


def count_workflow():
    return json.loads(EXAMPLE.read_text())['root']


def review(ui, root):
    body = {'source': json.dumps({'format': 'agentwiz.component', 'format_version': 1, 'root': root})}
    plan = send(ui, 'component-plan', **body)
    return {'request': body, 'expected_plan': plan['expected_plan'],
            'confirmations': plan['required_confirmations']}, plan['descriptor']


def test_two_step_import_and_stale_review(ui):
    root = count_workflow()
    definition = root['definition']
    body = definition['workflow']
    formatter = deepcopy(definition['blocks']['counter']['inline'])
    formatter.update(id='examples/formatter', title='Format input')
    block = formatter['definition']['block']
    block['name'] = 'formatter'
    block['outputs']['out'] = deepcopy(block['inputs']['text'])
    block['steps'][0].update(operation='format_report', parameters={'template': '{{inputs.text}} four'})
    definition['blocks']['formatter'] = {'inline': formatter}
    body['nodes']['format'] = {'block': 'formatter', 'bindings': {'text': {'kind': 'input', 'port': 'text'}}}
    body['nodes']['count']['bindings']['text'] = {'kind': 'node', 'node': 'format', 'port': 'out'}

    install, _ = review(ui, root)
    stale = deepcopy(install)
    stale['request']['source'] = stale['request']['source'].replace(' four', ' five six')
    assert failure_of(lambda: send(ui, 'component-install', **stale)).body['code'] == 'confirmation_required'
    assert request(ui, '/api/workshop/components')['workspace'] == []

    captured = send(ui, 'component-install', **install)
    selection = {'name': body['name'], 'capture': captured['capture'], 'inputs': {'text': 'one two three'}}
    plan = send(ui, 'plans', action='workflow_run', request=selection)
    start = send(ui, f"workflows/{body['name']}/run", request=selection, expected_plan=plan['expected_plan'],
                 ticket=send(ui, 'tickets')['ticket'], confirmations=plan['required_confirmations'])
    result = done(ui, '/api/workshop/workflow-jobs/' + start['operation_id'])
    assert result['outputs']['count']['text'] == '4'

    body['nodes']['format']['bindings']['text'] = {'kind': 'node', 'node': 'format', 'port': 'out'}
    assert failure_of(lambda: review(ui, root)).status in (400, 409)
    assert failure_of(lambda: send(ui, 'component-plan', source='{"root":{},"root":{}}')).status == 400
    definition['blocks'] = []
    assert failure_of(lambda: review(ui, root)).status == 400


def test_publication_is_recoverable_on_both_sides_of_atomic_write(ui, monkeypatch):
    root = count_workflow()
    install, preview = review(ui, root)
    service = ui.state.workshop.components
    visible = lambda: request(ui, '/api/workshop/components')['workspace']

    def interrupt(*args):
        raise OSError('injected publication interruption')

    with monkeypatch.context() as patch:
        patch.setattr(components, 'write_atomic_bytes', interrupt)
        with pytest.raises(OSError):
            service.install(install)
        assert visible() == []
    with monkeypatch.context() as patch:
        patch.setattr(components.assets, 'sync_directory', interrupt)
        with pytest.raises(OSError):
            service.install(install)
        assert [row['capture'] for row in visible()] == [preview['capture']]
    assert send(ui, 'component-install', **install)['capture'] == preview['capture']
    assert len(visible()) == 1


def test_worker_intersects_cloud_authority_and_refuses_missing_setup(ui, server):
    workflow = count_workflow()
    template = private_templates
    port = template.port('text', local=False)
    definition = template.block('cloud_answer', {'text': port}, {'out': port}, {
        'kind': 'model', 'role': 'proposal_test', 'response_mode': 'named_json',
        'system': {'text': 'Return text.'}, 'prompt': {'text': '{{inputs.text}}'},
        'repair': {'max_repairs': 0},
    })
    native = workflow['definition']
    native['blocks']['counter']['inline']['definition']['block'] = definition
    native['workflow']['inputs']['text'] = port
    native['workflow']['outputs']['count'] = {**port, 'binding': template.node('count')}
    agent = {'kind': 'agent', 'id': 'examples/cloud-worker', 'title': 'Worker', 'visibility': 'private',
             'category': 'general', 'definition': {'instructions': 'Count words', 'mode': 'cloud',
                 'inputs': {}, 'outputs': {}, 'capability_requests': [], 'artifact_requests': [],
                 'workflows': [{'inline': workflow}]}}
    team = {'kind': 'team', 'id': 'examples/workers', 'title': 'Workers', 'visibility': 'private',
            'category': 'general', 'definition': {'purpose': 'Run exact workflows', 'agents': [{'inline': agent}]}}
    install, preview = review(ui, team)
    assert preview['active'] is False
    assert any(row['setup'] for row in preview['components'])
    capture = send(ui, 'component-install', **install)['capture']
    service = ui.state.workshop
    inputs = {'text': 'synthetic'}
    with Worker(service, capture, 0, cloud=True) as worker:
        plan = worker.plan(0, inputs)
        with pytest.raises(WorkshopError):
            worker.execute(0, inputs, {'expected_plan': plan['expected_plan'],
                                       'confirmations': plan['required_confirmations']}, scope='agent')
        with pytest.raises(WorkshopError):
            with Worker(service, capture, 0):
                pytest.fail('second worker acquired the Workshop slot')
        worker.stop()
        assert worker.execute(0, inputs, {}, scope='agent')['outcome'] == 'stopped'

    save_profile(ui, 'proposal', model='remote', kind='openrouter', base_url=server.url + '/v1',
                 provider='OpenAI', routing_mode='manual', api_key=KEY)
    save_roles(ui, {'proposal_test': 'proposal'})
    for mode, allow_cloud in (('local_only', True), ('cloud', False)):
        agent['definition']['mode'] = mode
        install, _ = review(ui, team)
        capture = send(ui, 'component-install', **install)['capture']
        with Worker(service, capture, 0, cloud=allow_cloud) as worker:
            plan = worker.plan(0, inputs)
            with pytest.raises(WorkshopError):
                worker.execute(0, inputs, {'expected_plan': plan['expected_plan'],
                                           'confirmations': plan['required_confirmations']}, scope='agent')
    with Worker(service, capture, 0, cloud=True, model_attempts=0) as worker:
        plan = worker.plan(0, inputs)
        assert plan['descriptor']['issues'] == []
        result = worker.execute(0, inputs, {'expected_plan': plan['expected_plan'],
                                            'confirmations': plan['required_confirmations']}, scope='agent')
        assert result['outcome'] != 'complete' and not result['artifacts']
    with Worker(service, capture, 0, cloud=True) as worker:
        plan = worker.plan(0, inputs)
        save_profile(ui, 'alternate', model='changed', kind='openrouter', base_url=server.url + '/v1',
                     provider='OpenAI', routing_mode='manual', api_key=KEY)
        save_roles(ui, {'proposal_test': 'alternate'})
        with pytest.raises(WorkshopError, match='confirmation_required'):
            worker.execute(0, inputs, {'expected_plan': plan['expected_plan'],
                                       'confirmations': plan['required_confirmations']}, scope='agent')
    assert server.requests == []

    definition['steps'][0] = {'id': 'run', 'kind': 'tool', 'server': 'example', 'tool': 'unknown',
                             'arguments': {}, 'output': 'out'}
    assert failure_of(lambda: review(ui, team)).status == 400


def test_missing_exact_dependency_stays_inert_and_metadata_edit_is_new_revision(ui):
    root = count_workflow()
    original_title = root['title']
    first, original = review(ui, root)
    original_capture = send(ui, 'component-install', **first)['capture']
    root['title'] = 'Renamed word count'
    second, edited = review(ui, root)
    assert edited['revision'] != original['revision']
    send(ui, 'component-install', **second)
    assert ui.state.workshop.components.load(original_capture)['component']['title'] == original_title

    root['definition']['blocks']['counter'] = {'ref': {
        'kind': 'block', 'id': 'examples/unavailable', 'revision': 'f' * 64}}
    install, preview = review(ui, root)
    assert preview['components'][0]['setup'][0]['code'] == 'missing_dependency'
    capture = send(ui, 'component-install', **install)['capture']
    assert failure_of(lambda: send(ui, 'component-workflow', capture=capture)).body['code'] == 'setup_required'
    assert failure_of(lambda: send(ui, 'component-share', capture=capture, action='export')).status == 409


def test_clone_and_reset_keep_pool_separate(make_ui, tmp_path):
    ui = make_ui()
    ui.component_pool = ui.state.component_pool = tmp_path / 'pool'
    root = count_workflow()
    root['visibility'] = 'public'
    root['definition']['blocks']['counter']['inline']['visibility'] = 'public'
    install, _ = review(ui, root)
    capture = send(ui, 'component-install', **install)['capture']
    share = {'capture': capture, 'action': 'promote'}
    preview = send(ui, 'component-share', **share)
    send(ui, 'component-share', **share, expected_plan=preview['expected_plan'],
         confirmations=preview['required_confirmations'])

    def admin(action, **fields):
        return request(ui, '/api/workspaces/' + action, {'format_version': 1, **fields})

    def library():
        return request(ui, '/api/workshop/components')

    admin('clone', source=ui.workspace.name, target='cloned')
    admin('switch', name='cloned')
    assert [row['capture'] for row in library()['workspace']] == [capture]
    admin('reset', name='cloned', confirm='cloned')
    assert library()['workspace'] == []
    assert [row['id'] for row in library()['pool']] == [root['id']]

    ui.component_pool = ui.workspace.path / 'nested-pool'
    assert failure_of(lambda: admin('reset', name='cloned', confirm='cloned')).status == 409


def test_local_only_constant_blocks_component_sharing_and_legacy_pool_reuse(ui, tmp_path):
    workflow = count_workflow()
    workflow['visibility'] = 'public'
    definition = workflow['definition']
    definition['blocks']['counter']['inline']['visibility'] = 'public'
    definition['workflow']['constants']['note'] = {'local_only': True, 'value': 'synthetic fixture'}
    definition['workflow']['nodes']['count']['bindings']['text'] = {'kind': 'constant', 'name': 'note'}
    agent = {'kind': 'agent', 'id': 'examples/counting-agent', 'title': 'Count words',
             'visibility': 'public', 'category': 'analysis',
             'definition': {'instructions': 'Count words', 'mode': 'local_only', 'inputs': {}, 'outputs': {},
                            'capability_requests': [], 'artifact_requests': [], 'workflows': [{'inline': workflow}]}}
    body, _ = review(ui, agent)
    capture = send(ui, 'component-install', **body)['capture']
    pool = ui.state.component_pool = tmp_path / 'pool'
    for action in ('export', 'promote'):
        rejected = failure_of(lambda: send(ui, 'component-share', capture=capture, action=action)).body
        assert rejected['issues'][0]['code'] == 'protected_component'
        assert 'synthetic fixture' not in json.dumps(rejected)
    assert not pool.exists()
    assert send(ui, 'component-workflow', capture=capture, path=[0])['editor']['name'] == 'count_words'

    # Simulate the complete record an earlier build could have published.
    package = json.loads(body['request']['source'])
    legacy = components.Compiler(package, {}).run()
    pool.mkdir()
    (pool / 'legacy.json').write_text(json.dumps(legacy))
    assert request(ui, '/api/workshop/components')['pool'] == []
    pin = {'kind': 'agent', 'id': agent['id'], 'revision': legacy['revision']}
    assert failure_of(lambda: send(ui, 'component-plan', pool=pin)).body['code'] == 'missing_dependency'


def test_synthetic_skill_release_check_uses_reviewed_draft(ui, server, monkeypatch, tmp_path):
    from conftest import Reply, answer

    monkeypatch.syspath_prepend(str(EXAMPLE.parents[2] / 'scripts'))
    import check_skill_proposal

    ui.state.component_pool = tmp_path / 'pool'
    save_profile(ui, 'openrouter-eval', model='example/synthetic', kind='openrouter', base_url=server.url + '/v1',
                 provider='', routing_mode='automatic', routing_data_collection='deny', api_key=KEY)
    endpoint = {'tag': 'eligible', 'provider_name': 'eligible', 'throughput_last_30m': {'p50': 80},
                'latency_last_30m': {'p50': .1}, 'pricing': {'prompt': '.0000001', 'completion': '.0000001'},
                'supported_parameters': ['max_tokens', 'temperature', 'response_format', 'reasoning']}
    server.reply(Reply(body=json.dumps({'data': {'endpoints': [endpoint]}}).encode()),
                 Reply(body=b'{"provider":"eligible","choices":[{"finish_reason":"stop","message":{"content":"ok"}}]}'), answer(json.dumps({'proposal': {'title': 'Counter', 'purpose': 'Count words',
        'instructions': 'Count the words.', 'workflows': [{'title': 'Word count', 'steps': ['word_count']}]}})))
    report = {'model': 'example/synthetic'}
    assert check_skill_proposal.check(check_skill_proposal.Client(ui), report) == 'passed', (report, server.requests)
    assert report['draft']['workflow_count'] == 1
    requests = [row['body'] for row in server.requests if row['method'] == 'POST']
    assert len(requests) == 2
    probe, wire = requests
    assert 'synthetic-count-words' not in json.dumps(probe)
    assert 'synthetic-count-words' in json.dumps(wire)
    assert wire['provider']['data_collection'] == 'deny'
    assert len(wire['messages']) == 2
    assert 'tools' not in wire


def test_built_in_exact_pin_resolves_in_nested_agent(ui):
    from agentwiz import library_install

    built_in = library_install.entry('calculate')
    declaration = {'kind': 'block', 'id': 'agentwiz/calculate', 'title': 'calculate',
                   'visibility': 'public', 'category': 'general',
                   'definition': {'block': built_in.definition, 'files': built_in.files}}
    pin = {'kind': 'block', 'id': declaration['id'], 'revision': components.revision(declaration)}
    step_input = {'kind': 'input', 'port': 'expression'}
    workflow = {'kind': 'workflow', 'id': 'examples/math', 'title': 'Math', 'visibility': 'private',
                'category': 'analysis', 'definition': {'blocks': {'math': {'ref': pin}}, 'workflow': {
                    'name': 'math', 'purpose': 'Calculate an expression', 'constants': {},
                    'inputs': declaration['definition']['block']['inputs'],
                    'outputs': {'out': {**declaration['definition']['block']['outputs']['out'],
                                        'binding': {'kind': 'node', 'node': 'math', 'port': 'out'}}},
                    'nodes': {'math': {'block': 'math', 'bindings': {'expression': step_input}}}}}}
    agent = {'kind': 'agent', 'id': 'examples/maths-agent', 'title': 'Maths agent', 'visibility': 'private',
             'category': 'analysis', 'definition': {'instructions': 'Calculate and count.', 'mode': 'local_only',
                 'inputs': {}, 'outputs': {}, 'capability_requests': [], 'artifact_requests': [],
                 'workflows': [{'inline': workflow}, {'inline': count_workflow()}]}}
    install, descriptor = review(ui, agent)
    assert any(part['id'] == pin['id'] and part['revision'] == pin['revision'] for part in descriptor['components'])
    capture = send(ui, 'component-install', **install)['capture']
    select = {'name': 'math', 'capture': capture, 'path': [0], 'inputs': {'expression': '2+3'}}
    plan = send(ui, 'plans', action='workflow_run', request=select)
    start = send(ui, 'workflows/math/run', request=select, expected_plan=plan['expected_plan'],
                 ticket=send(ui, 'tickets')['ticket'], confirmations=plan['required_confirmations'])
    result = done(ui, '/api/workshop/workflow-jobs/' + start['operation_id'])
    assert json.loads(result['outputs']['out']['text'])['result'] == '5'
    exported = send(ui, 'component-share', action='export', capture=capture)['descriptor']['package']
    assert exported['root']['definition']['workflows'][0]['inline']['definition']['blocks']['math'] == {'ref': pin}
    assert exported['dependencies'] == []
