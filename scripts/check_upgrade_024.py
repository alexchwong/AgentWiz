#!/usr/bin/env python3
"""Open and run a saved 0.2.4 workflow with the candidate AgentWiz."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import venv

WHEEL = 'agentwiz-0.2.4-py3-none-any.whl'
URL = f'https://github.com/alexchwong/AgentWiz/releases/download/v0.2.4/{WHEEL}'
SHA256 = '65874d77f6ec282c8fca353389361d1d54ecb7a3351e12b713ac0cc6dd3ad204'


def check_workspace(folder, create):
    import agentwiz
    from agentwiz.ui.server import UIServer
    from agentwiz.workspace import open_workspace

    expected = '0.2.4' if create else '0.2.5'
    if agentwiz.__version__ != expected:
        raise RuntimeError(f'unexpected AgentWiz version {agentwiz.__version__}')
    workspace = open_workspace(folder / 'workspaces', 'upgrade')
    ui = UIServer(0, workspace=workspace, profiles_dir=folder / 'profiles',
                  mcp_servers_dir=folder / 'mcp')
    thread = threading.Thread(target=ui.serve_forever, daemon=True)
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def workshop(route, body=None):
        request = urllib.request.Request(
            f'http://127.0.0.1:{ui.server_address[1]}/api/workshop/{route}',
            data=None if body is None else json.dumps({'format_version': 1, **body}).encode(),
            headers={'Content-Type': 'application/json', 'X-Session-Token': ui.token})
        with opener.open(request, timeout=10) as response:
            return json.load(response)

    def reviewed(plan):
        return {'expected_plan': plan['expected_plan'], 'confirmations': plan['required_confirmations']}

    try:
        name = 'upgrade_counter'
        if create:
            draft = workshop('preview', {'starter': 'count_text', 'starter_version': 2, 'key': 'counter'})['draft']
            save = {'draft': draft, 'expected_identity': None}
            saved = workshop('save', {**save, **reviewed(workshop('plans', {'action': 'save', 'request': save}))})
            text = {'contract': {'format': 'text'}, 'local_only': True}
            source = {'kind': 'input', 'port': 'text'}
            definition = {'name': name, 'purpose': 'Count words', 'constants': {},
                          'inputs': {'text': text},
                          'outputs': {'words': {'contract': {'format': 'json'}, 'local_only': True,
                                                'binding': {'kind': 'node', 'node': 'count', 'port': 'out'}}},
                          'nodes': {'count': {'bindings': {'text': source}}}}
            request = {'name': name, 'file_token': None, 'definition_text': json.dumps(definition),
                       'selections': {'count': {'key': 'counter', 'revision': saved['durable_id']}}}
            plan = workshop('plans', {'action': 'workflow_save', 'request': request})
            workshop(f'workflows/{name}/save', {'request': request, **reviewed(plan)})

        request = {'name': name, 'inputs': {'text': 'one two three'}}
        plan = workshop('plans', {'action': 'workflow_run', 'request': request})
        if plan['descriptor']['issues']:
            raise RuntimeError(f"workflow preflight failed: {plan['descriptor']['issues']}")
        job = workshop(f'workflows/{name}/run', {'request': request, **reviewed(plan),
                       'ticket': workshop('tickets', {})['ticket']})
        deadline = time.monotonic() + 10
        while True:
            result = workshop(f"workflow-jobs/{job['operation_id']}")
            if result['outcome'] != 'running' or time.monotonic() >= deadline:
                break
            time.sleep(.02)
        output = result.get('outputs', {}).get('words', {})
        if result['outcome'] != 'complete' or output.get('text') != '3' or not output.get('local_only'):
            raise RuntimeError(f'workspace workflow failed: {result}')
        print(f'{agentwiz.__version__}: saved workflow returned 3 and retained its Local only label')
    finally:
        ui.shutdown()
        thread.join()
        ui.server_close()
        ui.close_workspace()
        workspace.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-wheel', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='agentwiz-upgrade-024-') as scratch:
        folder = Path(scratch)
        wheel = folder / WHEEL
        if args.baseline_wheel:
            shutil.copyfile(args.baseline_wheel, wheel)
        else:
            with urllib.request.urlopen(URL, timeout=60) as response, wheel.open('wb') as stream:
                shutil.copyfileobj(response, stream)
        if hashlib.sha256(wheel.read_bytes()).hexdigest() != SHA256:
            raise SystemExit('baseline wheel SHA-256 does not match the published 0.2.4 wheel')
        baseline = folder / 'baseline'
        venv.create(baseline, with_pip=True)
        python = baseline / 'bin' / 'python'
        subprocess.run([python, '-m', 'pip', 'install', '-q', wheel], check=True, cwd=folder)
        driver = ('import runpy, sys; from pathlib import Path; '
                  'runpy.run_path(sys.argv[1])["check_workspace"](Path(sys.argv[2]), sys.argv[3] == "create")')
        for interpreter, phase in [(python, 'create'), (sys.executable, 'open')]:
            subprocess.run([interpreter, '-I', '-c', driver, Path(__file__).resolve(), folder, phase],
                           check=True, cwd=folder)
    print('0.2.4 workspace upgrade passed')


if __name__ == '__main__':
    main()
