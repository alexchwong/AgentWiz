"""Reference workflows `private_summary_local` and `private_summary_release` (core v3 §7.3): fixed synthetic
documents, the scripted stand-in redaction and summary models, and the preparation shared by the tutorial and
the tests. All names are fictional, email domains end in `.invalid` and record numbers start with `TEST-`.

Passing these examples says nothing about real documents; see the tutorial topics' limits."""
from __future__ import annotations

import re

from .. import jsonio

DEID_MODEL = 'private-deid'
SUMMARY_MODEL = 'private-summary'
LOCAL_ROLE = 'tutorial_private'
SUMMARY_ROLE = 'tutorial_private_summary'
CLOUD_ROLE = 'tutorial_cloud'
RISK_ROLE = 'tutorial_private_risk'
RISK_MODEL = 'release-risk'
START, END = 'Document:\n<<<\n', '\n>>>'

NAMES = ('Jane Example', 'John Sample', 'Mary Placeholder', 'Omar Fictional', 'Lena Synthetic', 'Ravi Testcase',
         'Ana Demo', 'Tom Specimen', 'Kiri Mockup', 'Sam Dummy')
EMAIL = re.compile(r'[A-Za-z0-9._-]+@[A-Za-z0-9.-]+\.invalid')
RECORD = re.compile(r'TEST-[0-9]+')
# (document, one key fact the summary keeps)
DOCUMENTS = (
    ('Jane Example reported fatigue after the night shift. Contact jane@clinic.invalid about record TEST-123.', 'fatigue'),
    ('John Sample described a mild headache since Monday. His record is TEST-204 and email john@mail.invalid.', 'headache'),
    ('Mary Placeholder needs a repeat prescription for asthma. Reach her at mary@care.invalid; record TEST-311.', 'asthma'),
    ('Omar Fictional reports improved sleep this week. Record TEST-418 lists omar@home.invalid as contact.', 'sleep'),
    ('Lena Synthetic noticed a rash on both arms. Follow up through lena@mail.invalid, record TEST-502.', 'rash'),
    ('Ravi Testcase completed physiotherapy for his knee. Record TEST-617; email ravi@clinic.invalid.', 'knee'),
    ('Ana Demo asked about dietary advice for anaemia. Her record TEST-733 uses ana@care.invalid.', 'anaemia'),
    ('Tom Specimen reported dizziness when standing. Record TEST-845, contact tom@home.invalid.', 'dizziness'),
    ('Kiri Mockup started a new blood pressure plan. Email kiri@mail.invalid regarding record TEST-956.', 'blood pressure'),
    ('Sam Dummy has a follow-up for a sprained wrist. Record TEST-1001; sam@clinic.invalid.', 'wrist'),
)
TEXT = {'contract': {'format': 'text'}}
MAPPING = {'contract': {'format': 'json', 'schema': {
    'type': 'array', 'maxItems': 1000, 'items': {
        'type': 'object', 'properties': {'placeholder': {'type': 'string'}, 'original': {'type': 'string'}},
        'required': ['placeholder', 'original'], 'additionalProperties': False}}}}


def redact(text):
    """Deterministic redaction of the synthetic identifiers: (redacted text, [{placeholder, original}])."""
    found = []
    for name in NAMES:
        if name in text:
            found.append((text.index(name), 'PERSON', name))
    for kind, pattern in (('EMAIL', EMAIL), ('RECORD', RECORD)):
        for match in pattern.finditer(text):
            found.append((match.start(), kind, match.group()))
    mapping, counters = [], {}
    for _, kind, original in sorted(found):
        if any(row['original'] == original for row in mapping):
            continue
        counters[kind] = counters.get(kind, 0) + 1
        mapping.append({'placeholder': f'[{kind}_{counters[kind]}]', 'original': original})
    for row in sorted(mapping, key=lambda row: -len(row['original'])):
        text = text.replace(row['original'], row['placeholder'])
    return text, mapping


def summarize(text):
    """The stand-in summary: the first sentence, placeholders kept as they are."""
    head, dot, _ = text.partition('. ')
    return head + ('.' if dot else '')


def prompt_text(messages):
    """The text between the fixture prompt's markers in any message, or ''."""
    for message in messages:
        content = message.get('content') if isinstance(message, dict) else None
        if isinstance(content, str) and START in content:
            return content.split(START, 1)[1].split(END, 1)[0]
    return ''


def answer(model, messages):
    """The named-output envelope a stand-in model returns for this request."""
    text = prompt_text(messages)
    if model == DEID_MODEL:
        redacted, mapping = redact(text)
        return jsonio.encode_exact({'redacted_text': redacted, 'mapping': mapping})
    return jsonio.encode_exact({'summary': summarize(text)})


def examples():
    rows = []
    for index, (document, fact) in enumerate(DOCUMENTS):
        _, mapping = redact(document)
        rows.append({'id': f'case_{index + 1}', 'version': 1, 'label': f'Synthetic case {index + 1}', 'verified': True,
                     'origin': 'packaged_human_reviewed', 'inputs': {'document': document},
                     'expectation': {'kind': 'text', 'assertions': [
                         {'output': 'redacted_text', 'operation': 'excludes',
                          'values': [row['original'] for row in mapping], 'case': 'insensitive'},
                         {'output': 'restored', 'operation': 'excludes_pattern', 'pattern': 'bracket_upper'},
                         {'output': 'restored', 'operation': 'includes', 'values': [fact], 'case': 'insensitive'}]}})
    return rows


def model_block(key, role, outputs, label_input=False):
    return {'key': key, 'files': {}, 'definition_text': jsonio.encode_exact({
        'format_version': 4, 'name': key, 'description': key.replace('_', ' '),
        'inputs': {'text': {**TEXT, 'local_only': label_input}},
        'outputs': outputs,
        'steps': [{'id': 'run', 'kind': 'model', 'role': role, 'response_mode': 'named_json',
                   'prompt': {'text': START + '{{inputs.text}}' + END}, 'repair': {'max_repairs': 1}}]})}


def blocks(variant):
    """Block drafts for one variant, keyed by node."""
    drafts = {'deid': model_block('private_deid', LOCAL_ROLE,
                                  {'redacted_text': {**TEXT, 'local_only': False}, 'mapping': {**MAPPING, 'local_only': True}})}
    if variant == 'local':
        drafts['summarize'] = model_block('private_summarize', SUMMARY_ROLE, {'summary': {**TEXT, 'local_only': True}})
    else:
        drafts['cloud_summary'] = model_block('cloud_summarize', CLOUD_ROLE, {'summary': {**TEXT, 'local_only': False}})
    return drafts


def definition(variant):
    def node(name, port):
        return {'kind': 'node', 'node': name, 'port': port}

    summary = 'summarize' if variant == 'local' else 'cloud_summary'
    nodes = {'deid': {'bindings': {'text': {'kind': 'input', 'port': 'document'}}},
             'restore': {'bindings': {'text': node(summary, 'summary'), 'pairs': node('deid', 'mapping')}}}
    if variant == 'local':
        nodes['summarize'] = {'bindings': {'text': node('deid', 'redacted_text')}}
    else:
        nodes['gate'] = {'gate': {'kind': 'release', 'title': 'Review redacted document', 'contract': {'format': 'text'},
                                  'assessment': {'enabled': True, 'role': RISK_ROLE}},
                         'bindings': {'value': node('deid', 'redacted_text'), 'scan': node('deid', 'mapping')}}
        nodes['cloud_summary'] = {'bindings': {'text': node('gate', 'released')}}
    return {'name': f'private_summary_{variant}',
            'purpose': 'Summarise a synthetic private document after de-identification, then restore the identifiers locally.',
            'inputs': {'document': {**TEXT, 'local_only': True}}, 'constants': {},
            'outputs': {'redacted_text': {**TEXT, 'local_only': True, 'binding': node('deid', 'redacted_text')},
                        'restored': {**TEXT, 'local_only': True, 'binding': node('restore', 'out')}},
            'nodes': nodes}


def ensure(service, variant):
    """Save the variant's blocks and workflow through ordinary Workshop operations unless the workflow exists.
    Roles, profiles and trust grants are the caller's (the tutorial or a test)."""
    from .workshop import publish_fixture
    name = f'private_summary_{variant}'
    if service.workflows.store.read(name)[0] is not None:
        return name
    drafts = {node: service.import_draft({'draft': draft})['draft'] for node, draft in blocks(variant).items()}
    drafts['restore'] = service.preview({'starter': 'restore_placeholders', 'starter_version': 2,
                                         'key': 'restore_identifiers'})['draft']
    selections = {}
    for node, draft in drafts.items():
        if service.assets.revisions(draft['key'])['revisions'] if (service.assets.path / draft['key']).exists() else False:
            revision = service.assets.revisions(draft['key'])['revisions'][0]['id']
        else:
            revision = publish_fixture(service, draft)['durable_id']
        selections[node] = {'key': draft['key'], 'revision': revision}
    request = {'name': name, 'file_token': None, 'definition_text': jsonio.encode_exact(definition(variant)),
               'selections': selections, 'examples': examples()}
    plan = service.workflows.plan('workflow_save', request)
    service.workflows.edit('workflow_save', request, {'request': request, 'expected_plan': plan['expected_plan']})
    return name


TUTORIAL_KEY = 'tutorial-stand-in-not-a-real-key'


def prepare_tutorial(state, backend_url, variant):
    """Tutorial preparation: stand-in roles and grants on the packaged backend, then the shared fixture.
    The cloud summariser is an OpenRouter-kind profile whose server, in the tutorial, is the same local stand-in;
    its placeholder key lives only in the tutorial session's memory."""
    from .. import outbound_policy
    from .workshop import _assign_role
    _assign_role(state, LOCAL_ROLE, DEID_MODEL)
    _assign_role(state, SUMMARY_ROLE, SUMMARY_MODEL)
    grants = [DEID_MODEL, SUMMARY_MODEL]
    if variant == 'release':
        _assign_role(state, RISK_ROLE, RISK_MODEL)
        grants.append(RISK_MODEL)
        view = state.view()
        state.set_openrouter_key(TUTORIAL_KEY, view['revision'])
        state.save_profile({'name': 'tutorial-cloud', 'kind': 'openrouter', 'model': SUMMARY_MODEL, 'provider': ''},
                           state.view()['revision'])
        state.select_profile(None, state.view()['revision'])
        roles = state.view()['roles']
        definitions = {**roles['definitions'], CLOUD_ROLE: {'description': 'Isolated tutorial cloud stand-in', 'location': 'cloud'}}
        state.save_roles({**roles['map'], CLOUD_ROLE: 'tutorial-cloud'}, roles['revision'], definitions)
    for model in grants:
        destination = {'kind': 'openai_compat', 'url': backend_url, 'model': model, 'provider': ''}
        trust = outbound_policy.view(state)
        if destination not in trust['grants']:
            outbound_policy.update(state, {'expected_revision': trust['revision'], 'destination': destination, 'trusted': True})
    return ensure(state.workshop, variant)
