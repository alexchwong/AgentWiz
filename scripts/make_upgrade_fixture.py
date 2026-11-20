#!/usr/bin/env python3
"""Regenerate the 0.2.2 existing-workspace fixture (0.2.3 technical specification v15, section 8).

    python scripts/make_upgrade_fixture.py --agentwiz-wheel PATH \
        [--modelspell-wheel PATH --scriptspell-wheel PATH --dependency-wheels DIR] --output DIR

A maintenance tool, outside every release and test path. It verifies the published 0.2.2 wheel's full
SHA-256, installs it in a temporary virtual environment, starts the installed server there and drives it over
HTTP against inline stub model servers, writes the retired v3 host-script block by hand (0.2.2 cannot create
one), removes `.agentwiz.lock`, and fails if any absolute path of the generating machine remains in the output.
The output folder is the workspace itself; its README.md records the command, the wheel hash and the date.
Nothing here is committed or published.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import venv

WHEEL_SHA256 = 'fad5de0c335c60f0bf77bbea06656df7a20d1e66011d62432b94c4cba44141da'
WORKSPACE = 'upgrade'

# Runs inside the 0.2.2 environment. Only agentwiz 0.2.2 and the standard library are imported.
DRIVER = r'''
import json, sys, threading, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import MappingProxyType

import agentwiz
assert agentwiz.__version__ == '0.2.2', agentwiz.__version__
from agentwiz import jsonio
from agentwiz.server_config import ServerSettings
from agentwiz.ui.server import UIServer
from agentwiz.workspace import open_workspace
from agentwiz.tutorial_support import private_summary
from agentwiz.tutorial_support.backend import Handler as StandIn

root, name, canary = Path(sys.argv[1]), sys.argv[2], sys.argv[3]


class Stub:
    """An inline OpenAI-compatible stub: replies from a queue, streamed or plain."""
    def __init__(self):
        self.replies = []
        outer = self
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass
            def do_POST(self):
                n = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(n) or b'{}')
                text = outer.replies.pop(0)
                if body.get('stream'):
                    chunks = [{'choices': [{'index': 0, 'delta': {'content': text}, 'finish_reason': None}]},
                              {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}]
                    data = b''.join(b'data: ' + json.dumps(c).encode() + b'\n\n' for c in chunks) + b'data: [DONE]\n\n'
                    ctype = 'text/event-stream'
                else:
                    data = json.dumps({'id': 'r', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': text},
                                                               'finish_reason': 'stop'}]}).encode()
                    ctype = 'application/json'
                self.send_response(200)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def do_GET(self):
                data = json.dumps({'data': [{'id': 'fixture-model', 'object': 'model'}]}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=self.httpd.serve_forever, args=(0.02,), daemon=True).start()
        self.url = f'http://127.0.0.1:{self.httpd.server_address[1]}/v1'


class StandInServer:
    def __init__(self):
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), StandIn)
        threading.Thread(target=self.httpd.serve_forever, args=(0.02,), daemon=True).start()
        self.url = f'http://127.0.0.1:{self.httpd.server_address[1]}/v1'


stub, local, cloud = Stub(), StandInServer(), StandInServer()
workspace = open_workspace(root, name)
ui = UIServer(0, workspace=workspace, profiles_dir=root / '_machine_profiles', mcp_servers_dir=root / '_machine_mcp',
              servers={'lmstudio': ServerSettings('http://localhost:1234/v1'),
                       'openrouter': ServerSettings(cloud.url, 'fixture-stand-in-key'),
                       'openai_compat': ServerSettings(stub.url)})
ui.state._credentials.add('fixture-stand-in-key')
threading.Thread(target=ui.serve_forever, args=(0.02,), daemon=True).start()
base = f'http://127.0.0.1:{ui.server_address[1]}'
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(path, body=None, stream=False):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json', 'X-Session-Token': ui.token},
                                 method='GET' if body is None else 'POST')
    try:
        with opener.open(req, timeout=60) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        raise SystemExit(f'{path}: HTTP {e.code} {e.read()[:2000]!r}')
    if stream:
        return [json.loads(line) for line in data.splitlines() if line.strip()]
    return json.loads(data)


def ws(route, **body):
    return call('/api/workshop/' + route, {'format_version': 1, **body})


def state():
    return call('/api/state')


def set_server(kind, url):
    servers = dict(ui.state.servers)
    servers[kind] = ServerSettings(url, servers[kind].api_key)
    ui.servers = MappingProxyType(servers)
    ui.state.servers = ui.servers


def profile(name, kind, model):
    call('/api/config', {'kind': kind, 'model': model, 'revision': state()['revision']})
    call('/api/profiles/save', {'name': name, 'revision': state()['revision'], 'kind': kind, 'model': model, 'provider': ''})


def trust(url, model):
    grants = call('/api/workshop/destination-trust')
    ws('destination-trust', expected_revision=grants['revision'], trusted=True,
       destination={'kind': 'openai_compat', 'url': url, 'model': model, 'provider': ''})


def plan_save(draft):
    if 'draft_ref' not in draft:
        draft = ws('drafts/import', draft=draft)['draft']
    body = {'draft': draft, 'expected_identity': None}
    plan = ws('plans', action='save', request=body)
    return ws('save', **body, expected_plan=plan['expected_plan'],
              confirmations=[{'kind': r['kind'], 'digest': r['digest']} for r in plan['required_confirmations']])


call('/api/history/switch', {'save_runs': True, 'revision': state()['history']['revision']})

# Profiles and roles. The local stand-in serves the private-summary models; `fast` is the inline stub.
set_server('openai_compat', local.url)
profile('private', 'openai_compat', private_summary.DEID_MODEL)
profile('private-summary', 'openai_compat', private_summary.SUMMARY_MODEL)
profile('risk', 'openai_compat', private_summary.RISK_MODEL)
profile('cloud', 'openrouter', private_summary.SUMMARY_MODEL)
profile('fast', 'openai_compat', 'fixture-model')
call('/api/roles/save', {'roles': {private_summary.LOCAL_ROLE: 'private', private_summary.SUMMARY_ROLE: 'private-summary',
                                   private_summary.RISK_ROLE: 'risk', private_summary.CLOUD_ROLE: 'cloud', 'fast': 'fast'},
                         'revision': state()['roles']['revision']})
for model in (private_summary.DEID_MODEL, private_summary.SUMMARY_MODEL, private_summary.RISK_MODEL):
    trust(local.url, model)

# The Release Gate workflow and one saved run of it.
workflow = private_summary.ensure(ui.state.workshop, 'release')
selection = {'name': workflow, 'inputs': {'document': 'Jane Example reported fatigue on 3 March. Record TEST-123.'}}
plan = ws('plans', action='workflow_run', request=selection)
ident = ws(f'workflows/{workflow}/run', request=selection, expected_plan=plan['expected_plan'],
           ticket=ws('tickets')['ticket'], confirmations=plan['required_confirmations'])['operation_id']
route = '/api/workshop/workflow-jobs/' + ident
for _ in range(600):
    job = call(route)
    if job.get('phase') == 'awaiting_release':
        break
    time.sleep(0.05)
release = job['release']
ws(f'workflow-jobs/{ident}/release', decision='release', acknowledge_assessment=True,
   **{k: release[k] for k in ('token', 'payload_digest', 'executable_digest', 'scope_digest', 'checks_digest')})
for _ in range(600):
    job = call(route)
    if job['outcome'] != 'running':
        break
    time.sleep(0.05)
assert job['outcome'] == 'complete', job

# The point of the remaining steps: the inline stub answers in order.
set_server('openai_compat', stub.url)
call('/api/config', {'kind': 'openai_compat', 'model': 'fixture-model', 'revision': state()['revision']})

# One saved Checked request run with a failed attempt and its manual repair.
call('/api/contracts/save', {'format_version': 2, 'name': 'pong', 'family': 'General', 'expected_file_token': None,
                             'definition_text': '{"format": "text", "contains": ["pong"]}', 'label': ''})
detail = call('/api/contracts/pong')
stub.replies[:] = ['nope', 'pong']
first = call('/api/complete', {'source': 'checked_request', 'prompt': 'Say pong', 'system': '', 'revision': state()['revision'],
                               'contract': {'name': 'pong', 'revision': detail['revision'], 'digest': detail['digest']}}, stream=True)
record = next(e for e in first if e['type'] == 'final')['record']
assert record['check']['overall'] == 'fail', record
second = call('/api/repair', {'attempt_id': record['id']}, stream=True)
assert next(e for e in second if e['type'] == 'final')['record']['check']['overall'] == 'pass'

# A managed v4 model block with a Local only input and protected design.
guarded = {'format_version': 4, 'name': 'guarded', 'description': 'Summarise a private note',
           'context_local_only': True,
           'inputs': {'text': {'contract': {'format': 'text'}, 'local_only': True}},
           'outputs': {'out': {'contract': {'format': 'text'}, 'local_only': True}},
           'steps': [{'id': 'request', 'kind': 'model', 'role': 'fast', 'system': {'text': ''},
                      'prompt': {'text': 'Summarise: {{inputs.text}}'}, 'response_mode': 'named_json',
                      'repair': {'max_repairs': 0}}]}
plan_save({'key': 'guarded', 'definition_text': jsonio.encode_exact(guarded), 'files': {}, 'metadata': {}})

# A managed v4 Python block saved through Describe it, so its revision carries origin: llm_generated.
stub.replies[:] = ["interface:\n  inputs:\n    text: str\n  outputs:\n    out: str\nexamples:\n"
                   "  - inputs:\n      text: one two\n    outputs:\n      out: '2'\n"
                   "  - inputs:\n      text: a\n    outputs:\n      out: '1'\n"
                   "  - inputs:\n      text: ''\n    outputs:\n      out: '0'\n",
                   "def transform(inputs):\n    return {'out': str(len(inputs['text'].split()))}\n"]
flow = ws('python/start', key='described', instruction='Count the words in the text', role='fast')['flow']
ws('python/examples', flow=flow)
ws('python/generate', flow=flow)
applied = ws('python/apply', flow=flow)['draft']
definition = jsonio.decode_exact(applied['definition_text'])
for side in ('inputs', 'outputs'):
    for declaration in definition[side].values():
        declaration['local_only'] = False                      # the labels the page asks for at Create
applied['definition_text'] = jsonio.encode_exact(definition)
applied.pop('draft_ref', None)
plan_save(applied)

# The retired v3 host-script block, written by hand: its script writes a canary file if it ever runs.
folder = workspace.path / 'blocks' / 'host_script'
(folder / 'scripts').mkdir(parents=True)
(folder / 'block.json').write_text(json.dumps({
    'format_version': 3, 'name': 'host_script', 'inputs': {'text': {'contract': {'format': 'text'}}},
    'outputs': {'x': {'contract': {'format': 'text'}}},
    'steps': [{'id': 's', 'kind': 'script', 'file': 'scripts/go.py'}]}, indent=2) + '\n')
(folder / 'scripts' / 'go.py').write_text(
    'import os\nopen(os.environ.get("AGENTWIZ_UPGRADE_CANARY", %r), "w").write("ran")\n' % canary)

ui.shutdown()
ui.server_close()
ui.close_workspace()
workspace.close()
print(json.dumps({'workflow': workflow}))
'''


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--agentwiz-wheel', required=True, type=Path)
    parser.add_argument('--modelspell-wheel', type=Path)
    parser.add_argument('--scriptspell-wheel', type=Path)
    parser.add_argument('--dependency-wheels', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    if sha256(args.agentwiz_wheel) != WHEEL_SHA256:
        raise SystemExit(f'{args.agentwiz_wheel} is not the published 0.2.2 wheel (SHA-256 {WHEEL_SHA256})')
    if args.output.exists() and any(args.output.iterdir()):
        raise SystemExit(f'{args.output} exists and is not empty')
    with tempfile.TemporaryDirectory(prefix='agentwiz-fixture-') as folder:
        temp = Path(folder)
        venv.create(temp / 'venv', with_pip=True)
        python = temp / 'venv' / ('Scripts' if sys.platform == 'win32' else 'bin') / 'python'
        install = [str(python), '-m', 'pip', 'install', '-q']
        if args.dependency_wheels:
            install += ['--no-index', '--find-links', str(args.dependency_wheels)]
        install += [str(p) for p in (args.modelspell_wheel, args.scriptspell_wheel) if p] + [str(args.agentwiz_wheel)]
        subprocess.run(install, check=True)
        (temp / 'driver.py').write_text(DRIVER, encoding='utf-8')
        roots = temp / 'workspaces'
        subprocess.run([str(python), '-I', str(temp / 'driver.py'), str(roots), WORKSPACE, 'agentwiz-upgrade-canary'],
                       check=True, cwd=temp)
        produced = roots / WORKSPACE
        (produced / '.agentwiz.lock').unlink(missing_ok=True)
        leaks = [str(p.relative_to(produced)) for p in produced.rglob('*') if p.is_file()
                 and any(marker.encode() in p.read_bytes() for marker in (str(temp), folder, str(Path.home())))]
        if leaks:
            raise SystemExit(f'absolute paths of this machine remain in: {", ".join(leaks)}')
        shutil.copytree(produced, args.output, dirs_exist_ok=True)
    today = datetime.date.today().isoformat()
    command = ' '.join(['python', 'scripts/make_upgrade_fixture.py', '--agentwiz-wheel', args.agentwiz_wheel.name,
                        '--output', 'tests/fixtures/workspaces/v0_2_2'])
    (args.output / 'README.md').write_text(
        '# AgentWiz 0.2.2 existing-workspace fixture\n\n'
        'Frozen. Produced by the published AgentWiz 0.2.2 wheel; never rewrite a file here. The upgrade tests '
        '(`tests/test_upgrade_v0_2_2.py`) copy it before opening it. Regenerate only with the command below, then '
        'review the whole diff.\n\n'
        f'- Command: `{command}`\n'
        f'- Wheel: `agentwiz-0.2.2-py3-none-any.whl`, SHA-256 `{WHEEL_SHA256}`\n'
        f'- Date: {today}\n\n'
        'Contents: the managed protected model block `guarded`; the `private_summary_release` workflow with its '
        'blocks, roles and Release Gate and one saved run of it; one saved Checked request run with a failed '
        'attempt and its manual repair; the Python block `described`, saved through Describe it '
        '(`origin: llm_generated`); the unmanaged v3 host-script block `host_script`, written by hand, whose script '
        'writes a canary file if it ever runs; the profiles and roles they need. Trust grants name the generator\'s '
        'stand-in addresses, which no longer exist; the tests grant their own fakes.\n', encoding='utf-8')
    print('fixture written to', args.output)


if __name__ == '__main__':
    raise SystemExit(main())
