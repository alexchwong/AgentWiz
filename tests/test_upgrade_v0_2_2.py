"""U-1 to U-5: a workspace written by the published 0.2.2 wheel opens and works (spec v15, 8.3).

The fixture under tests/fixtures/workspaces/v0_2_2/ is frozen and never rewritten: each test copies it under
tmp_path, opens the copy and starts a server on it. Its trust grants name the generator's dead stand-in
addresses, so each test grants its own fake through the ordinary Workshop route before running anything.
"""
import hashlib
import io
import json
import shutil
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from agentwiz import jsonio
from agentwiz.tutorial_support import private_summary
from agentwiz.workspace import open_workspace
from conftest import (block_answer, configure_server, failure_of, get_state, request, run_block, result_of,
                      wait_until)
from test_workshop import send

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'workspaces' / 'v0_2_2'
KEY = 'upgrade-test-key'
CASE = private_summary.examples()[0]
PRIVATE_VALUES = CASE['expectation']['assertions'][0]['values']        # names, emails and record numbers


def fixture_digest():
    h = hashlib.sha256()
    for path in sorted(p for p in FIXTURE.rglob('*') if p.is_file()):
        h.update(str(path.relative_to(FIXTURE)).encode() + b'\0' + path.read_bytes() + b'\0')
    return h.hexdigest()


class StandIn:
    """The packaged stand-in model server, recording each request's path and body."""

    def __init__(self):
        from agentwiz.tutorial_support.backend import Handler
        self.requests = []
        outer = self

        class Recording(Handler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers.get('Content-Length') or 0)
                raw = self.rfile.read(n)
                outer.requests.append({'path': self.path, 'body': raw.decode('utf-8', 'replace')})
                self.rfile = io.BytesIO(raw)
                super().do_POST()

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Recording)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, args=(0.02,), daemon=True).start()
        self.url = f'http://127.0.0.1:{self.httpd.server_address[1]}/v1'

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def upgraded(tmp_path, make_ui, monkeypatch):
    """The fixture copied, opened and served. Canary writes would land in tmp_path."""
    before = fixture_digest()
    canary = tmp_path / 'canary'
    monkeypatch.setenv('AGENTWIZ_UPGRADE_CANARY', str(canary))
    monkeypatch.chdir(tmp_path)
    shutil.copytree(FIXTURE, tmp_path / 'workspaces' / 'upgrade')
    workspace = open_workspace(tmp_path / 'workspaces', 'upgrade')
    make_ui.opened.append(workspace)
    ui = make_ui(workspace)
    ui.canary = canary
    yield ui
    assert not canary.exists() and not (tmp_path / 'agentwiz-upgrade-canary').exists()
    assert fixture_digest() == before, 'the committed fixture must never be rewritten'


@pytest.fixture
def stand_ins():
    made = []

    def make():
        made.append(StandIn())
        return made[-1]
    yield make
    for server in made:
        server.close()


def grant(ui, url, model):
    grants = request(ui, '/api/workshop/destination-trust')
    send(ui, 'destination-trust', expected_revision=grants['revision'], trusted=True,
         destination={'kind': 'openai_compat', 'url': url, 'model': model, 'provider': ''})


def serve(ui, local, cloud=None):
    configure_server(ui, {'kind': 'openai_compat', 'base_url': local.url})
    if cloud is not None:
        configure_server(ui, {'kind': 'openrouter', 'base_url': cloud.url, 'api_key': KEY})


def blocks_by_name(ui):
    return {row['name']: row for row in request(ui, '/api/blocks')['blocks']}


def test_u1_open(upgraded):
    ui = upgraded
    assert ui.workspace.problems == [] and ui.workspace.read_only is False
    rows = blocks_by_name(ui)
    assert set(rows) >= {'guarded', 'described', 'host_script', 'private_deid', 'cloud_summarize',
                         'restore_identifiers'}
    guarded = send(ui, 'preview', key='guarded')
    definition = jsonio.decode_exact(guarded['draft']['definition_text'])
    assert definition['context_local_only'] is True
    assert definition['inputs']['text']['local_only'] is True and definition['outputs']['out']['local_only'] is True
    revision = request(ui, '/api/workshop/blocks/guarded/revisions')['revisions'][0]['id']
    stored = jsonio.decode_file_exact((ui.workspace.path / 'blocks' / 'guarded' / '.workshop' / 'revisions'
                                       / revision / 'workshop.json').read_bytes())
    assert guarded['draft']['metadata']['protection_origin'] == stored['protection_origin'] == 'inherited'
    problem = rows['host_script']['problem']
    assert problem['code'] == 'unusable' and problem['issues'][0]['message'].startswith('host_script_retired:')
    for name in ('guarded', 'described', 'private_deid', 'cloud_summarize', 'restore_identifiers'):
        assert rows[name]['problem'] is None, rows[name]


def test_u2_history(upgraded):
    ui = upgraded
    listing = request(ui, '/api/runs')
    checked = [row for row in listing['runs'] if row.get('source') == 'checked_request']
    assert len(checked) == 1, listing
    run = request(ui, '/api/runs/' + checked[0]['run_id'])
    attempts = run['attempts']
    assert run['run']['source'] == 'checked_request' and run['run']['agentwiz_version'] == '0.2.2'
    assert [a['attempt'] for a in attempts] == [1, 2]
    assert [a['check']['overall'] for a in attempts] == ['fail', 'pass']
    assert attempts[0]['parent_id'] is None and attempts[1]['parent_id'] == attempts[0]['id']
    jobs = request(ui, '/api/workshop/workflow-evidence')['jobs']
    assert len(jobs) == 1, jobs
    detail = request(ui, '/api/workshop/workflow-evidence/' + jobs[0])
    assert 'complete' in json.dumps(detail), detail
    request(ui, '/api/runs/delete', {'run_id': checked[0]['run_id']})
    assert not [row for row in request(ui, '/api/runs')['runs'] if row.get('source') == 'checked_request']
    assert request(ui, '/api/workshop/workflow-evidence/' + jobs[0]) == detail


def test_u3_protected_block(upgraded, server):
    ui = upgraded
    cloud = type(server)()
    try:
        configure_server(ui, {'kind': 'openai_compat', 'base_url': server.url + '/v1'})
        configure_server(ui, {'kind': 'openrouter', 'base_url': cloud.url + '/v1', 'api_key': KEY})
        state = get_state(ui)['roles']
        roles, definitions = state['map'], state['definitions']
        request(ui, '/api/roles/save', {'roles': {**roles, 'fast': 'cloud'}, 'revision': state['revision'],
                                        'definitions': {**definitions, 'fast': {'description': '', 'location': 'cloud'}}})
        refused = result_of(run_block(ui, 'guarded', {'text': 'Jane Example is unwell.'}))
        assert refused['outcome'] == 'rejected' and refused['steps'] == [], refused
        assert refused['issues'] == [{'location': 'destination', 'message': 'destination_ineligible'}]
        assert cloud.requests == [] and server.requests == []
        request(ui, '/api/roles/save', {'roles': roles, 'revision': get_state(ui)['roles']['revision'],
                                        'definitions': definitions})
        grant(ui, server.url + '/v1', 'fixture-model')
        server.reply(block_answer(out='A short summary.'))
        result = result_of(run_block(ui, 'guarded', {'text': 'Jane Example is unwell.'}))
        assert result['outcome'] == 'complete', result
        assert len(server.requests) == 1 and cloud.requests == []
    finally:
        cloud.close()


def test_u4_host_script_and_provenance(upgraded, server):
    ui = upgraded
    configure_server(ui, {'kind': 'openai_compat', 'base_url': server.url + '/v1'})
    row = blocks_by_name(ui)['host_script']
    refusal = failure_of(lambda: run_block(ui, 'host_script', {'text': 'x'}, revision=row['revision']))
    assert refusal.status == 409 and refusal.body['code'] == 'block_unusable'
    inventory = request(ui, f"/api/agent/inventory?revision={get_state(ui)['revision']}")
    assert 'host_script' not in json.dumps([c for c in inventory['capabilities'] if c['kind'] == 'block'])
    described = send(ui, 'preview', key='described')['draft']
    assert described['metadata']['origin'] == 'llm_generated'
    revision = request(ui, '/api/workshop/blocks/described/revisions')['revisions'][0]['id']
    stored = jsonio.decode_file_exact((ui.workspace.path / 'blocks' / 'described' / '.workshop' / 'revisions'
                                       / revision / 'workshop.json').read_bytes())
    assert stored['origin'] == 'llm_generated'
    assert server.requests == [] and not ui.canary.exists()


def start_run(ui, name):
    selection = {'name': name, 'inputs': {'document': CASE['inputs']['document']}}
    plan = send(ui, 'plans', action='workflow_run', request=selection)
    assert not [i for i in plan['descriptor']['issues'] if i['severity'] == 'error'], plan['descriptor']['issues']
    ident = send(ui, f'workflows/{name}/run', request=selection, expected_plan=plan['expected_plan'],
                 ticket=send(ui, 'tickets')['ticket'], confirmations=plan['required_confirmations'])['operation_id']
    return ident, '/api/workshop/workflow-jobs/' + ident


def under_review(ui, route):
    assert wait_until(lambda: request(ui, route).get('phase') == 'awaiting_release', timeout=30), request(ui, route)
    return request(ui, route)['release']


def decide(ui, ident, current, decision='release'):
    return send(ui, f'workflow-jobs/{ident}/release', decision=decision, acknowledge_assessment=True,
                **{k: current[k] for k in ('token', 'payload_digest', 'executable_digest', 'scope_digest',
                                           'checks_digest')})


def finished(ui, route):
    assert wait_until(lambda: request(ui, route)['outcome'] != 'running', timeout=60)
    return request(ui, route)


def test_u5_gate_workflow_separate_runs(upgraded, stand_ins, monkeypatch):
    ui = upgraded
    local, cloud = stand_ins(), stand_ins()
    serve(ui, local, cloud)
    for model in (private_summary.DEID_MODEL, private_summary.SUMMARY_MODEL, private_summary.RISK_MODEL):
        grant(ui, local.url, model)
    name = 'private_summary_release'
    # Run A: ready, review, approve; exactly one cloud request carrying only the released value.
    ident_a, route_a = start_run(ui, name)
    current_a = under_review(ui, route_a)
    assert cloud.requests == []
    first = decide(ui, ident_a, current_a)
    result = finished(ui, route_a)
    assert result['outcome'] == 'complete', result
    assert len(cloud.requests) == 1
    sent = cloud.requests[0]['body']
    assert json.dumps(current_a['payload'])[1:-1] in sent
    for value in PRIVATE_VALUES:
        assert value.lower() not in sent.lower()
    assert '"original"' not in sent and 'placeholder' not in sent.lower()
    assert decide(ui, ident_a, current_a) == first
    assert len(cloud.requests) == 1
    # Run B: a changed decision on run A's review, and run A's decision on run B, are stale; then reject.
    ident_b, route_b = start_run(ui, name)
    current_b = under_review(ui, route_b)
    assert failure_of(lambda: decide(ui, ident_a, current_a, 'reject')).body['code'] == 'release_stale'
    assert failure_of(lambda: decide(ui, ident_b, current_a)).body['code'] == 'release_stale'
    assert len(cloud.requests) == 1
    decide(ui, ident_b, current_b, 'reject')
    assert finished(ui, route_b)['outcome'] == 'release_rejected'
    assert len(cloud.requests) == 1
    # Run C: the review expires.
    from agentwiz import release_gate
    monkeypatch.setattr(release_gate, 'review_wait', lambda config: 0)
    _, route_c = start_run(ui, name)
    assert finished(ui, route_c)['outcome'] == 'release_expired'
    assert len(cloud.requests) == 1
