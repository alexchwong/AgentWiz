"""Workflow-ready creation, retained-label acknowledgement, overwrite of labelled history and trust
candidates (UI-AB v2 backend)."""
from agentwiz import jsonio
import json
import urllib.error

from conftest import HTTPFailure, failure_of, request, save_profile, save_roles
from test_workshop import http_method, plan_save, send


def put_review(ui, body):
    try:
        return http_method(ui, 'blocks/history/retained-labels', 'PUT', body)
    except urllib.error.HTTPError as e:
        raise HTTPFailure(e.code, json.loads(e.read())) from None


def labels(draft):
    definition = jsonio.decode_exact(draft['definition_text'])
    return definition['format_version'], {side: {n: p.get('local_only') for n, p in definition[side].items()}
                                          for side in ('inputs', 'outputs')}


def test_data_handling_creates_workflow_ready_model_and_tool_blocks(ui):
    for choice, label in (('private', True), ('not_private', False), ('per_port', None)):
        for starter in ('write_text', 'blank_model', 'blank_tool'):
            draft = send(ui, 'preview', starter=starter, key='made', role='fast', server='srv', tool='t',
                         data_handling=choice)['draft']
            assert labels(draft) == (4, {'inputs': {'text': label}, 'outputs': {'out': label}})
    # Without a choice the block stays standalone (v3); script and transform starters refuse a choice.
    assert labels(send(ui, 'preview', starter='write_text', key='made', role='fast')['draft'])[0] == 3
    for starter, version in (('blank_script', 1), ('count_text', 2)):
        refused = failure_of(lambda: send(ui, 'preview', starter=starter, starter_version=version, key='made',
                                          data_handling='private'))
        assert refused.body['code'] == 'invalid'
    # Decide per port leaves the labels unset, so saving is refused until they are chosen.
    unset = send(ui, 'preview', starter='write_text', key='later', role='fast', data_handling='per_port')['draft']
    assert failure_of(lambda: plan_save(ui, unset)).status == 409


def restricted_history(ui, server):
    """A saved block whose revision 1 labelled `text` Local only and whose revision 2 does not."""
    save_profile(ui, 'p', model='m', kind='openai_compat', base_url=server.url + '/v1', provider='')
    save_roles(ui, {'fast': 'p'})
    draft = send(ui, 'preview', starter='write_text', key='history', role='fast', data_handling='private')['draft']
    first = plan_save(ui, draft)
    selected = send(ui, 'preview', key='history', revision=first['durable_id'])['draft']
    definition = jsonio.decode_exact(selected['definition_text'])
    definition['inputs']['text']['local_only'] = False
    definition['outputs']['out']['local_only'] = False
    selected['definition_text'] = jsonio.encode_exact(definition)
    second = plan_save(ui, selected, selected['base_digest'])
    return first, second


def test_acknowledged_retained_labels_stop_restricting_but_examples_stay_restricted(ui, server):
    first, second = restricted_history(ui, server)
    body = {'key': 'history', 'inputs': {'text': 'value'}}
    refused = failure_of(lambda: send(ui, 'plans', action='test', request=body))
    assert refused.body['code'] == 'destination_untrusted'
    sources = refused.body['issues'][0]['restriction_sources']
    assert {'code': 'retained_label', 'input': 'text', 'revision_number': 1} in sources
    assert refused.body['issues'][0]['eligible'] is True and refused.body['issues'][0]['trusted'] is False
    # An example saved while the label is retained keeps its restriction after acknowledgement.
    examples = [{'id': 'kept', 'version': 1, 'inputs': {'text': 'captured'},
                 'expectation': {'kind': 'review', 'criterion': 'review'}}]
    http_method(ui, 'blocks/history/examples', 'PUT', {'expected_revision': None, 'examples': examples})

    review = request(ui, '/api/workshop/blocks/history/retained-labels')
    assert [(p['revision'], p['input'], p['acknowledged']) for p in review['pairs']] == [(first['durable_id'], 'text', False)]
    assert review['restricted_examples'][0]['inputs'] == ['text']
    pair = [{'revision': first['durable_id'], 'input': 'text'}]
    stale = failure_of(lambda: put_review(ui, {'expected_revision': 'other', 'acknowledged': pair}))
    assert stale.body['code'] == 'stale'
    unknown = failure_of(lambda: put_review(ui, {
        'expected_revision': None, 'acknowledged': [{'revision': second['durable_id'], 'input': 'text'}]}))
    assert unknown.body['code'] == 'stale'
    acknowledged = put_review(ui, {'expected_revision': None, 'acknowledged': pair})
    assert acknowledged['pairs'][0]['acknowledged'] is True

    plan = send(ui, 'plans', action='test', request=body)
    assert not any(c.get('protected') for c in plan['descriptor']['candidates'])
    with_example = {'candidates': [{'key': 'history'}], 'examples': examples}
    assert failure_of(lambda: send(ui, 'plans', action='batches', request=with_example)).body['code'] == 'destination_untrusted'
    assert not server.requests
    # Clearing the record restores the retained restriction.
    put_review(ui, {'expected_revision': acknowledged['revision'], 'acknowledged': []})
    assert failure_of(lambda: send(ui, 'plans', action='test', request=body)).body['code'] == 'destination_untrusted'


def test_overwrite_cannot_replace_a_labelled_revision_from_a_standalone_draft(ui, server):
    _, second = restricted_history(ui, server)
    standalone = send(ui, 'preview', starter='write_text', key='history', role='fast')['draft']
    current = send(ui, 'preview', key='history')['draft']
    refused = failure_of(lambda: plan_save(ui, standalone, current['base_digest'],
                                           overwrite_revision=second['durable_id']))
    assert refused.body['code'] == 'new_revision_required'


def test_trust_candidates_report_locality_eligibility_and_trust(ui, server):
    save_profile(ui, 'loop', model='m', kind='openai_compat', base_url=server.url + '/v1', provider='')
    host = server.url.replace('127.0.0.1', 'localhost')
    save_profile(ui, 'named', model='m', kind='lmstudio', base_url=host + '/v1')
    save_roles(ui, {'fast': 'loop', 'slow': 'named'})
    rows = {c['used_by'][0]['name']: c for c in request(ui, '/api/workshop/destination-trust-candidates')['candidates']}
    assert (rows['fast']['locality'], rows['fast']['eligible'], rows['fast']['trusted']) == ('local', True, False)
    assert (rows['slow']['locality'], rows['slow']['eligible'], rows['slow']['reason']) == ('local', False, 'hostname')
    send(ui, 'destination-trust', expected_revision=None, destination=rows['fast']['destination'], trusted=True)
    after = {c['used_by'][0]['name']: c for c in request(ui, '/api/workshop/destination-trust-candidates')['candidates']}
    assert after['fast']['trusted'] is True
