from urllib.parse import urlsplit

import pytest

from agentwiz import jsonio, outbound_policy
from conftest import answer, failure_of, result_of, run_block, save_profile, save_roles, write_block
from test_workshop import plan_save, send
from test_workshop_descriptions import script_draft


def test_protected_model_requires_exact_grant_and_proxy_bypass(ui, server, monkeypatch):
    import urllib.request
    endpoint = server.url + '/v1'
    save_profile(ui, 'p', model='m', kind='openai_compat', base_url=endpoint, provider='')
    save_roles(ui, {'fast': 'p'})
    definition = {'format_version': 4, 'name': 'protected', 'inputs': {
        'text': {'contract': {'format': 'text'}, 'local_only': True}}, 'outputs': {
        'out': {'contract': {'format': 'text'}, 'local_only': False}}, 'steps': [
        {'id': 'run', 'kind': 'model', 'role': 'fast', 'prompt': {'text': '{{inputs.text}}'},
         'response_mode': 'named_json'}]}
    write_block(ui, 'protected', definition)
    inputs = {'text': 'protected material'}
    assert result_of(run_block(ui, 'protected', inputs))['outcome'] == 'rejected'
    assert not server.requests
    dest = {'kind': 'openai_compat', 'url': endpoint, 'model': 'm', 'provider': ''}
    granted = send(ui, 'destination-trust', expected_revision=None, destination=dest, trusted=True)
    for name in ('NO_PROXY', 'no_proxy', 'HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy', 'ALL_PROXY', 'all_proxy'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('http_proxy', 'http://127.0.0.1:1')
    assert urllib.request.getproxies()['http']
    blocked = result_of(run_block(ui, 'protected', inputs))
    assert blocked['outcome'] == 'rejected'
    assert not server.requests
    monkeypatch.setenv('no_proxy', urlsplit(endpoint).hostname)
    server.reply(answer('{"out":"ok"}'))
    result = result_of(run_block(ui, 'protected', inputs))
    assert result['outcome'] == 'complete', result
    assert len(server.requests) == 1
    send(ui, 'destination-trust', expected_revision=granted['revision'], destination=dest, trusted=False)
    assert result_of(run_block(ui, 'protected', inputs))['outcome'] == 'rejected'
    assert len(server.requests) == 1


@pytest.mark.parametrize('endpoint', ['http://localhost:1234/v1', 'http://10.0.0.1/v1',
                                      'http://me@127.0.0.1/v1', 'http://127.0.0.1/v1?'])
def test_restricted_endpoint_requires_eligible_literal(endpoint):
    with pytest.raises(outbound_policy.Refused, match='destination_ineligible'):
        outbound_policy.canonical_url(endpoint)


def test_generated_script_descendant_remains_refused_after_duplication(ui):
    source = 'print(\'{"out": 709}\')\n'
    created = plan_save(ui, script_draft('generated', [], source=source, origin='llm_generated'))
    draft = send(ui, 'preview', key='generated', revision=created['durable_id'])['draft']
    draft['files']['scripts/main.py'] = 'print(\'{"out": 710}\')\n'
    draft['metadata']['origin'] = 'manual'
    draft['metadata'].pop('script_origin', None)
    duplicate = send(ui, 'preview', draft=draft, duplicate_key='copy')['draft']
    plan_save(ui, duplicate)
    result = result_of(run_block(ui, 'copy', {}))
    assert result['outcome'] == 'rejected'
    assert not result['steps']
    bare = script_draft('exact_copy', [], source=source)
    write_block(ui, 'exact_copy', jsonio.decode_exact(bare['definition_text']), bare['files'])
    assert result_of(run_block(ui, 'exact_copy', {}))['outcome'] == 'rejected'
    duplicate['draft_ref'] = 'bad'
    response = failure_of(lambda: send(ui, 'preview', draft=duplicate))
    assert response.body['code'] == 'draft_expired'


def test_trust_revoked_after_model_reply_blocks_repair(ui, server, monkeypatch):
    from agentwiz import model_step

    endpoint = server.url + '/v1'
    monkeypatch.setattr(outbound_policy, 'getproxies', lambda: {})
    save_profile(ui, 'p', model='m', kind='openai_compat', base_url=endpoint, provider='')
    save_roles(ui, {'fast': 'p'})
    definition = {'format_version': 4, 'name': 'repair', 'inputs': {
        'text': {'contract': {'format': 'text'}, 'local_only': True}}, 'outputs': {
        'out': {'contract': {'format': 'text'}, 'local_only': False}}, 'steps': [
        {'id': 'run', 'kind': 'model', 'role': 'fast', 'prompt': {'text': '{{inputs.text}}'},
         'response_mode': 'named_json'}]}
    write_block(ui, 'repair', definition)
    destination = {'kind': 'openai_compat', 'url': endpoint, 'model': 'm', 'provider': ''}
    grant = send(ui, 'destination-trust', expected_revision=None, destination=destination, trusted=True)
    server.reply(answer('{"wrong":"bad"}'))
    original = model_step.run_attempt_with_value

    def revoke_after_attempt(*args, **kwargs):
        result = original(*args, **kwargs)
        send(ui, 'destination-trust', expected_revision=grant['revision'], destination=destination, trusted=False)
        return result

    monkeypatch.setattr(model_step, 'run_attempt_with_value', revoke_after_attempt)
    result = result_of(run_block(ui, 'repair', {'text': 'protected'}))
    assert result['steps'][0]['failure'] == 'destination_changed'
    assert len(server.requests) == 1


def test_saved_manual_protection_clears_only_with_reviewed_new_revision(ui):
    draft = send(ui, 'preview', starter='count_text', starter_version=2, key='counter')['draft']
    definition = jsonio.decode_exact(draft['definition_text'])
    definition['context_local_only'] = True
    draft['definition_text'] = jsonio.encode_exact(definition)
    first = plan_save(ui, draft)
    selected = send(ui, 'preview', key='counter', revision=first['durable_id'])['draft']
    definition['context_local_only'] = False
    selected['definition_text'] = jsonio.encode_exact(definition)
    body = {'draft': selected, 'expected_identity': selected['base_digest']}
    plan = send(ui, 'plans', action='save', request=body)
    assert 'clear_design_protection' in {item['kind'] for item in plan['required_confirmations']}
    refusal = failure_of(lambda: send(ui, 'save', **body, expected_plan=plan['expected_plan'], confirmations=[]))
    assert refusal.body['code'] == 'confirmation_required'
    second = plan_save(ui, selected, selected['base_digest'])
    assert send(ui, 'preview', key='counter', revision=first['durable_id'])['design']['context_local_only']
    assert not send(ui, 'preview', key='counter', revision=second['durable_id'])['design']['context_local_only']


def test_protected_mcp_grant_is_tool_specific_and_rechecked_after_connect(ui, make_fake_mcp, monkeypatch):
    from agentwiz.mcp_client import MCPClient
    from test_block_tool import setup_tool, tool_block

    definition = tool_block()
    definition['format_version'] = 4
    definition['inputs']['n']['local_only'] = True
    definition['outputs']['out']['local_only'] = False
    fake = setup_tool(ui, make_fake_mcp, lambda call: call.result({
        'content': [], 'structuredContent': {'ok': True}, 'isError': False}), definition)
    identity = {'kind': 'mcp', 'server': 'docs', 'url': fake.url, 'tool': 'lookup'}
    wrong = send(ui, 'destination-trust', expected_revision=None, destination={**identity, 'tool': 'other'}, trusted=True)
    assert result_of(run_block(ui, 't', {'n': 'protected'}))['outcome'] == 'rejected'
    assert not fake.methods('tools/call')
    grant = send(ui, 'destination-trust', expected_revision=wrong['revision'], destination=identity, trusted=True)
    assert result_of(run_block(ui, 't', {'n': 'protected'}))['outcome'] == 'complete'
    original = MCPClient._open

    def revoke_after_connect(client, ctx, snapshot):
        opened = original(client, ctx, snapshot)
        send(ui, 'destination-trust', expected_revision=grant['revision'], destination=identity, trusted=False)
        return opened

    monkeypatch.setattr(MCPClient, '_open', revoke_after_connect)
    result = result_of(run_block(ui, 't', {'n': 'protected'}))
    assert result['outcome'] == 'failed'
    assert len(fake.methods('tools/call')) == 1


def test_protecting_input_marks_existing_examples_and_blocks_v3_compare_arm(ui, server):
    from test_workshop import http_method

    save_profile(ui, 'p', model='m', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'fast': 'p'})
    draft = send(ui, 'preview', starter='write_text', key='shared', role='fast')['draft']
    old = plan_save(ui, draft)
    examples = [{'id': 'example', 'version': 1, 'inputs': {'text': 'protected'},
                 'expectation': {'kind': 'review', 'criterion': 'review'}}]
    http_method(ui, 'blocks/shared/examples', 'PUT', {'expected_revision': None, 'examples': examples})
    selected = send(ui, 'preview', key='shared', revision=old['durable_id'])['draft']
    upgraded = send(ui, 'preview', draft=selected, edit={'kind': 'upgrade_for_workflows'})['draft']
    definition = jsonio.decode_exact(upgraded['definition_text'])
    definition['inputs']['text']['local_only'] = True
    definition['outputs']['out']['local_only'] = False
    upgraded['definition_text'] = jsonio.encode_exact(definition)
    new = plan_save(ui, upgraded, upgraded['base_digest'])
    from conftest import request
    collection = request(ui, '/api/workshop/blocks/shared/examples')
    assert collection['examples'][0]['restrictions'] == {'text': True}
    refused = failure_of(lambda: send(ui, 'plans', action='batches', request={
        'candidates': [{'key': 'shared', 'revision': old['durable_id']},
                       {'key': 'shared', 'revision': new['durable_id']}], 'examples': examples}))
    assert refused.body['code'] == 'destination_untrusted'
    assert not server.requests
