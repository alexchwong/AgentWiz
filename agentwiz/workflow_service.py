"""Reviewed workflow edits and jobs using the Workshop coordinator."""
from __future__ import annotations

import os
from itertools import islice
import secrets
import threading
import copy
from types import SimpleNamespace
from contextlib import ExitStack

from . import checks, runs, workflow_evidence, workflow_settings
from .errors import report_internal
from .workflow_definition import Store, capture, digest, names, problem, record, unmanaged
from .workflow_runner import Graph, Job
from .workshop import WorkshopError
from .workspace import valid_name


class Workflows:
    def __init__(self, workshop):
        self.workshop = workshop
        self.store = Store(workshop.state, workshop.assets)
        self.jobs = {}

    def listing(self):
        folder = self.store.root / 'workflows'
        if folder.is_symlink():
            problem('snapshot_unusable', 'workflows', 'the workflow directory is a symbolic link')
        if not folder.exists():
            return {'workflows': []}
        with os.scandir(folder) as entries:
            names = [entry.name[:-5] for entry in islice(entries, 4096)
                     if entry.name.endswith('.json') and valid_name(entry.name[:-5])]
        return {'workflows': sorted(names)[:256]}

    def source_fingerprint(self, key):
        _, _, identity = unmanaged(self.workshop.assets, key)
        return {'key': key, 'fingerprint': identity}

    @staticmethod
    def block_ports(block):
        def describe(side, port):
            contract = block.contracts[side][port.name].parsed
            schema = contract.schema
            value_type = 'string' if contract.format == 'text' else schema.get('type') if isinstance(schema, dict) else None
            return {'format': contract.format, 'description': port.description, 'local_only': port.local_only,
                    'value_types': [value_type] if isinstance(value_type, str) else value_type}

        ports = {side: {port.name: describe(side, port) for port in getattr(block, side)}
                 for side in ('inputs', 'outputs')}
        if block.step.kind == 'gate':
            ports['inputs'].setdefault('scan', {'format': 'json', 'local_only': False, 'value_types': None})
        return ports

    @classmethod
    def block_view(cls, block):
        return {'kind': block.step.kind, 'role': block.step.role, 'tool': block.step.tool,
                'server': block.step.server, 'context_local_only': block.context_local_only, **cls.block_ports(block)}

    def source_options(self, key, revision=None):
        revisions = self.workshop.assets.revisions(key)
        if revision is None and revisions['revisions']:
            return {'revisions': [{'id': row['id'], 'number': row['revision_number'], 'title': row['revision_name']}
                                  for row in revisions['revisions']]}
        if revision is not None:
            saved = self.workshop.assets.saved(key, revision)
            with self.workshop.assets.snapshot(saved) as capture:
                block = capture.parsed.block
                selection = {'key': key, 'revision': revision}
                version = saved.definition.get('format_version')
        else:
            loaded = self.workshop.state.block_loaded(key)
            block = loaded.block if loaded else None
            version = block.format_version if block else None
            selection = self.source_fingerprint(key) if version == 4 else None
        if block is None:
            problem('snapshot_unusable', 'source', 'The selected block could not be opened.')
        return {'block': self.block_view(block), 'version': version, 'selection': selection}

    def read(self, name):
        bundle, token = self.store.read(name)
        if bundle is None:
            problem('missing', name, 'workflow does not exist', 404)
        with ExitStack() as stack:
            graph = Graph(bundle, name, stack)
            definition = graph.definition
            editor = {**definition, **{
                side: {key: {**port, 'contract': exact(port['contract']),
                             'format': graph.contracts[side][key].format}
                       for key, port in definition[side].items()} for side in ('inputs', 'outputs')},
                'constants': {key: {**item, 'value': exact(item['value'])}
                              for key, item in definition['constants'].items()}}
            editor = copy.deepcopy(editor)
            for row in editor['nodes'].values():
                if 'gate' in row:
                    row['gate']['contract'] = exact(row['gate']['contract'])
            return {'format_version': 1, 'file_token': token, 'definition_text': exact(definition),
                    'editor': editor, 'node_ports': {node: self.block_view(block) for node, block in graph.blocks.items()},
                    'input_ports': {port: contract.format for port, contract in graph.contracts['inputs'].items()},
                    'layout': bundle['layout'], 'examples': bundle['examples'], 'issues': graph.issues,
                    'sources': {node: (bundle['snapshots'][row['snapshot_id']]['provenance'] if 'snapshot_id' in row else
                                      {'title': row['gate']['title'], 'kind': 'gate'})
                                for node, row in graph.definition['nodes'].items()}}

    def editor_definition(self, editor):
        from .jsonio import decode_exact
        definition = copy.deepcopy(record(editor, ('name', 'purpose', 'inputs', 'outputs', 'constants', 'nodes'),
                                          ('budget',), 'definition'))
        for side, field in (('inputs', 'contract'), ('outputs', 'contract'), ('constants', 'value')):
            for name, item in names(definition[side], 0, 64, side).items():
                location = f'{side}.{name}.{field}'
                if not isinstance(item, dict) or not isinstance(item.get(field), str):
                    problem('invalid', location, 'Expected an exact value as text.', 400)
                try:
                    item[field] = decode_exact(item[field])
                except ValueError:
                    problem('invalid', location, 'Correct the exact value before saving.', 400)
                if field == 'contract':
                    item.pop('format', None)
        for row in definition['nodes'].values():
            if 'gate' in row and isinstance(row['gate'].get('contract'), str):
                row['gate']['contract'] = decode_exact(row['gate']['contract'])
        return definition

    def edit(self, action, request, review=None):
        record(request, ('name', 'file_token'), () if action == 'workflow_delete' else
               ('definition_text', 'editor', 'selections', 'layout', 'examples'), 'request')
        name = request['name']
        current, token = self.store.read(name)
        if token != request['file_token']:
            problem('stale', name, 'the workflow changed since review')
        candidate, issues, changes = None, [], []
        if action == 'workflow_delete':
            if current is None:
                problem('missing', name, 'workflow does not exist', 404)
        else:
            from .jsonio import decode_exact
            if ('editor' in request) == ('definition_text' in request):
                problem('invalid', 'definition', 'Supply one workflow definition.', 400)
            definition = (self.editor_definition(request['editor']) if 'editor' in request else
                          decode_exact(request['definition_text']))
            record(definition, ('name', 'purpose', 'inputs', 'outputs', 'constants', 'nodes'), ('budget',), 'definition')
            nodes = names(definition['nodes'], 0, 64, 'nodes')
            selections = names(request.get('selections', {}), 0, 64, 'selections')
            if action == 'workflow_rebind' and not selections.keys() & old_node_keys(current):
                problem('invalid', 'selections', 'choose an existing node to rebind', 400)
            if selections.keys() - nodes.keys():
                problem('invalid', 'selections', 'selection names an absent node', 400)
            snapshots = {} if current is None else current['snapshots']
            old_nodes = {} if current is None else current['definition']['nodes']
            copied = {}
            for node, row in nodes.items():
                record(row, ('bindings',), ('snapshot_id', 'gate'), f'nodes.{node}')
                if 'gate' in row:
                    if 'snapshot_id' in row or node in selections:
                        problem('invalid', f'nodes.{node}', 'a gate cannot capture a block', 400)
                    continue
                old_id = old_nodes.get(node, {}).get('snapshot_id')
                if node in selections:
                    if old_id is not None and action != 'workflow_rebind':
                        problem('invalid', f'nodes.{node}', 'use Rebind to replace a captured node')
                    snapshot = capture(self.workshop.assets, selections[node])
                    snapshot_id = digest(snapshot)
                    if old_id is not None:
                        before = snapshots[old_id]
                        changes.append({'node': node, 'before': before['provenance'], 'after': snapshot['provenance'],
                                        'ports': {side: {'removed': sorted(before['contracts'][side].keys() - snapshot['contracts'][side].keys()),
                                                         'added': sorted(snapshot['contracts'][side].keys() - before['contracts'][side].keys()),
                                                         'changed': sorted(port for port in before['contracts'][side].keys() & snapshot['contracts'][side].keys()
                                                                           if before['contracts'][side][port] != snapshot['contracts'][side][port])}
                                                  for side in ('inputs', 'outputs')}})
                else:
                    snapshot_id = old_id
                    if old_id is None or row.get('snapshot_id', old_id) != old_id:
                        problem('snapshot_unusable', f'nodes.{node}', 'choose an exact block revision')
                    snapshot = snapshots[old_id]
                copied[snapshot_id] = snapshot
                row['snapshot_id'] = snapshot_id
            layout = request.get('layout', {} if current is None else
                                 {node: position for node, position in current['layout'].items() if node in nodes})
            names(layout, 0, 64, 'layout')
            for node, position in layout.items():
                record(position, ('x', 'y'), (), f'layout.{node}')
                if any(type(value) is not int for value in position.values()):
                    problem('invalid', f'layout.{node}', 'coordinates must be integers', 400)
            used = {(point['x'], point['y']) for point in layout.values()}
            for index, node in enumerate(sorted(nodes)):
                if node not in layout:
                    while (index % 4 * 220, index // 4 * 130) in used:
                        index += 1
                    point = index % 4 * 220, index // 4 * 130
                    used.add(point)
                    layout[node] = {'x': point[0], 'y': point[1]}
            candidate = {'format_version': 2 if any('gate' in row for row in nodes.values()) else 1, 'definition': definition, 'snapshots': copied, 'layout': layout,
                         'examples': copy.deepcopy(request.get('examples', [] if current is None else current['examples']))}
            with ExitStack() as stack:
                graph = Graph(candidate, name, stack)
                self.update_examples(graph, candidate['examples'], [] if current is None else current['examples'])
                self.check_capture_credentials(graph)
                issues = graph.issues
                if action == 'workflow_rebind':
                    _, destination_issues, _ = graph.destinations(self.workshop.state, self.workshop)
                    issues = list(issues) + [
                        item for item in destination_issues if item['severity'] == 'error']
                    if issues:
                        raise WorkshopError('rebind_refused', 409, issues[:100])
        plan = self.plan_view(action, request, {'bundle': digest(candidate), 'issues': issues[:100], 'changes': changes},
                              confirmation='workflow_delete' if action == 'workflow_delete' else
                              'workflow_rebind' if action == 'workflow_rebind' else None)
        if review is None:
            return plan
        record(review, ('request', 'expected_plan'), ('confirmations',), 'review')
        self.workshop.check_plan(plan, review)
        new_token = self.store.publish(name, token, candidate)
        return {'deleted': candidate is None, 'file_token': None if candidate is None else new_token}

    def check_capture_credentials(self, graph):
        bundle = graph.bundle
        self.workshop.reject_credentials(bundle['definition'])
        for snapshot in bundle['snapshots'].values():
            self.workshop.reject_credentials(snapshot['contracts'])
        for opened in set(graph.captures.values()):
            self.workshop.reject_credentials([part for path, data in opened.files.items()
                                              for part in (path, data.decode('utf-8', errors='replace'))])

    def plan_view(self, action, request, descriptor, confirmation=None, private=None):
        identity = digest([self.workshop.salt, action, request, descriptor, private])
        required = [] if confirmation is None else [
            {'kind': confirmation, 'digest': digest([identity, confirmation]), 'detail': confirmation.replace('_', ' ')}]
        return {'format_version': 1, 'expected_plan': identity, 'descriptor': descriptor,
                'required_confirmations': required}

    def validate_example(self, graph, example):
        ports = SimpleNamespace(inputs=[SimpleNamespace(name=name) for name in graph.contracts['inputs']],
                                outputs=[SimpleNamespace(name=name) for name in graph.contracts['outputs']],
                                contracts=graph.contracts)
        self.workshop.validate_examples([example], ports)
        from .jsonio import decode_exact
        expected = example['expectation']
        if expected['kind'] == 'whole_output':
            for name, text in expected['values'].items():
                if graph.contracts['outputs'][name].format == 'json':
                    decode_exact(text)
        elif expected['kind'] == 'fields':
            if any(graph.contracts['outputs'][assertion['output']].format != 'json'
                   for assertion in expected['assertions']):
                problem('example_incompatible', 'expectation', 'field assertions require JSON outputs')

    def update_examples(self, graph, examples, previous):
        old = {example['id']: example for example in previous}
        labels = {source[1] for source in graph.restricted if source[0] == 'input'}
        for example in examples:
            before = old.get(example['id'])
            changed = before is not None and any(before[key] != example[key] for key in ('inputs', 'expectation'))
            if changed:
                if example['version'] != before['version'] + 1:
                    problem('stale', 'examples', 'editing an example requires its next version')
                example['verified'] = False
            if before is None or changed:
                self.validate_example(graph, example)
            self.workshop.mark_examples([example], labels, old)

    def prepare_run(self, request, stack):
        record(request, ('name',), ('inputs', 'examples'), 'request')
        if ('inputs' in request) == ('examples' in request):
            problem('invalid', 'request', 'select examples or supply one-off inputs', 400)
        name = request['name']
        bundle, _ = self.store.read(name)
        if bundle is None:
            problem('missing', name, 'workflow does not exist', 404)
        graph = Graph(bundle, name, stack)
        try:
            checks.require_validation(contract for ports in graph.contracts.values() for contract in ports.values())
            for block in graph.blocks.values():
                self.workshop.check_schema_available(block)
            for opened in graph.assessments.values():
                if opened is not None:
                    self.workshop.check_schema_available(opened.parsed.block)
        except checks.ValidationUnavailable:
            problem('validation_unavailable', 'contracts', 'the validator is unavailable', 503)
        if 'inputs' in request:
            examples = [{'inputs': request['inputs']}]
        else:
            selected = request['examples']
            if not isinstance(selected, list) or not 1 <= len(selected) <= 20:
                problem('invalid', 'examples', 'select from one to twenty examples', 400)
            stored = {example['id']: example for example in bundle['examples']}
            examples, seen = [], set()
            for choice in selected:
                record(choice, ('id', 'version'), (), 'examples')
                ident = choice['id']
                if not isinstance(ident, str):
                    problem('invalid', 'examples', 'example IDs must be text', 400)
                example = stored.get(ident)
                if not example or type(choice['version']) is not int or example['version'] != choice['version'] or ident in seen:
                    problem('stale', 'examples', 'a selected example changed or is unavailable')
                seen.add(ident)
                examples.append(example)

        graph.examples = examples
        graph.example_runtime = []
        graph.example_destinations = []
        reviews, private = [], []
        model_roles, mcp_servers = {}, {}
        for index, example in enumerate(examples):
            graph.select_example(example)
            review = graph.preflight(example['inputs'])
            destinations, issues, identities = graph.destinations(self.workshop.state, self.workshop, model_roles, mcp_servers)
            graph.example_runtime.append(dict(graph.runtime))
            graph.example_destinations.append(destinations)
            private.append(identities)
            review['destinations'] = destinations
            review['issues'].extend(issues + graph.ancestor_warnings())
            if 'id' in example:
                review['example'] = {'id': example['id'], 'version': example['version']}
                try:
                    self.validate_example(graph, example)
                except (WorkshopError, KeyError, TypeError, ValueError):
                    review['issues'].append({'code': 'example_incompatible', 'location': 'expectation',
                                             'message': 'review this example against the current workflow ports',
                                             'severity': 'error'})
                review['issues'] = [{**row, 'location': f"examples[{index}].{row['location']}"} for row in review['issues']]
            reviews.append(review)
        descriptor = dict(reviews[0])
        all_issues = [row for review in reviews for row in review['issues']]
        descriptor['issues'] = sorted(all_issues, key=lambda row: row.get('severity') == 'warning')[:100]
        if len(reviews) > 1:
            descriptor['example_plans'] = [{**review, 'issues': review['issues'][:20]} for review in reviews]
        destinations = descriptor['destinations']
        mode = workflow_settings.read(self.workshop.state)
        graph.saving = self.workshop.state.save_runs_now()
        if graph.saving:
            self.check_capture_credentials(graph)
        descriptor.update(destinations=destinations, mcp_confirmation=mode, saving=graph.saving,
                          repetitions=len(examples), examples=[{key: example[key] for key in ('id', 'version') if key in example} for example in examples], limits=graph.definition.get('budget', {}))
        confirmation = ('workflow_mcp' if (mode['mode'] == 'every_run' or len(examples) > 1)
                        and any(value['kind'] == 'mcp' for value in destinations.values()) else None)
        return graph, self.plan_view('workflow_run', request, descriptor, confirmation, [private, digest(examples)])

    def plan(self, action, request):
        if action == 'workflow_run':
            with ExitStack() as stack:
                return self.prepare_run(request, stack)[1]
        if action in ('workflow_save', 'workflow_rebind', 'workflow_delete'):
            return self.edit(action, request)
        problem('invalid', 'action', 'unknown workflow operation', 400)

    def start(self, body):
        record(body, ('request', 'expected_plan', 'ticket'), ('confirmations',), 'request')
        operations = self.workshop.operations
        ticket = body.get('ticket')
        status = operations.lookup(ticket, body)
        if status['state'] != 'unused':
            return status
        stack = ExitStack()
        writer = None
        ident = secrets.token_hex(16)
        claimed = False
        reserved = False
        started = False
        try:
            graph, plan = self.prepare_run(body['request'], stack)
            self.workshop.check_plan(plan, body)
            blocking = [item for item in plan['descriptor']['issues'] if item.get('severity', 'error') == 'error']
            if blocking:
                raise WorkshopError('preflight_refused', 409, blocking)
            job = Job(self.workshop.state)
            job.ident = ident
            job.name = name = body['request']['name']
            job.is_batch = 'examples' in body['request']
            writer = self.workshop.state.register_writer(f'workflow job {ident}')
            with self.workshop.lock:
                if self.workshop.active is None:
                    self.workshop.active = ident
                    self.jobs[ident] = job
                    reserved = True
            if not reserved:
                status = operations.lookup(ticket, body)
                if status['state'] != 'unused':
                    return status
                problem('admission_busy', 'workflow', 'the Workshop slot is busy', 503)
            status, claimed = operations.claim(ticket, body, ident)
            if not claimed:
                return status
            if graph.saving:
                try:
                    job.evidence = workflow_evidence.Evidence(self.workshop.state, ident, graph.bundle, plan['descriptor'])
                except (OSError, ValueError, workflow_evidence.EvidenceError):
                    problem('evidence_write_failed', 'evidence', 'initial workflow evidence could not be written')
            thread = threading.Thread(target=self.work, args=(ident, ticket, job, graph, stack, writer), daemon=True)
            thread.start()
            started = True
            return status
        finally:
            if not started:
                try:
                    stack.close()
                finally:
                    if claimed:
                        operations.finish(ticket, {'outcome': 'rejected', 'start_failed': True})
                    if reserved:
                        with self.workshop.lock:
                            self.jobs.pop(ident, None)
                            self.workshop.active = None
                    if writer is not None:
                        writer.release()

    def work(self, ident, ticket, job, graph, stack, writer):
        try:
            summary = {**job.execute_batch(graph, graph.examples), 'executable_digest': graph.digest}
            with job.lock:
                if job.reason == runs.USER_STOP and summary['outcome'] not in ('unknown', 'stopped'):
                    summary = {**summary, 'outcome': 'stopped', 'outputs': {}}
                job.settling = True
            if job.evidence:
                try:
                    job.evidence.finish(summary)
                except (OSError, ValueError, workflow_evidence.EvidenceError):
                    summary = {**summary, 'outcome': 'unknown' if summary['outcome'] == 'unknown' else 'failed',
                               'outputs': {}, 'save_error': 'evidence_write_failed'}
            with job.lock:
                job.result = summary
        except Exception as exc:
            report_internal('workflow job', exc)
            with job.lock:
                job.result = {'outcome': 'failed', 'issues': [{'code': 'internal_error', 'location': 'workflow'}]}
        finally:
            try:
                try:
                    stack.close()
                finally:
                    self.workshop.operations.finish(ticket, {'outcome': job.view()['outcome']})
            finally:
                with self.workshop.lock:
                    self.workshop.active = None
                    while len(self.jobs) > 8:
                        self.jobs.pop(next(iter(self.jobs)))
                writer.release()

    def review(self, ident, body):
        record(body, ('example', 'version', 'result_digest', 'executable_digest', 'judgement'), ('note',), 'review')
        if body['judgement'] not in ('correct', 'incorrect', 'unknown') or not isinstance(body.get('note', ''), str) or len(body.get('note', '')) > 4096:
            problem('invalid', 'review', 'invalid human judgement', 400)
        job = self.job(ident)
        summary = job.view()
        row = next((item for item in summary.get('examples', []) if item.get('id') == body['example']), None)
        if (not row or row.get('outcome') != 'complete' or row['version'] != body['version']
                or row['result_digest'] != body['result_digest'] or summary['executable_digest'] != body['executable_digest']):
            problem('stale', 'review', 'the reviewed workflow result changed')
        state = self.workshop.state
        writer = state.register_writer('workflow human review')
        try:
            with self.workshop.assets.reserve(ident, 'workflow_evidence'):
                review = dict(body)
                if job.evidence:
                    if state.workspace.read_only:
                        problem('read_only', 'review', 'the workspace is read-only')
                    if not job.evidence.folder.is_dir() or job.evidence.folder.is_symlink():
                        problem('missing', 'evidence', 'saved workflow evidence is unavailable', 404)
                    filename = 'review-' + digest([body['example'], body['version']]) + '.json'
                    job.evidence.write(filename, review)
                    if filename not in job.evidence.manifest['records']:
                        job.evidence.manifest['records'].append(filename)
                    job.evidence.persist()
                with job.lock:
                    job.reviews[body['example']] = review
                return {'review': review, 'saving': job.evidence is not None}
        finally:
            writer.release()

    def evidence(self, ident=None, record_name=None):
        state = self.workshop.state
        if ident is None:
            root = workflow_evidence.directory(state.workspace.path)
            if not root.exists():
                return {'jobs': []}
            from .workshop import TOKEN
            with os.scandir(root) as entries:
                ids = [entry.name for entry in islice(entries, 4096) if TOKEN.fullmatch(entry.name)]
            return {'jobs': sorted(ids)[:256]}
        result = workflow_evidence.read(state, ident)
        if record_name is not None:
            return workflow_evidence.read_record(state, ident, record_name, result)
        with self.workshop.lock:
            active = self.workshop.active == ident
        if not active and result.get('outcome') == 'running':
            result = {**result, 'outcome': 'interrupted'}
        return result

    def delete_evidence(self, ident):
        import shutil
        state = self.workshop.state
        workflow_evidence.safe_id(ident)
        if state.workspace.read_only:
            problem('read_only', 'evidence', 'the workspace is read-only')
        writer = state.register_writer('workflow evidence deletion')
        try:
            with self.workshop.assets.reserve(ident, 'workflow_evidence'):
                with self.workshop.lock:
                    active = self.workshop.active == ident
                if active:
                    problem('busy', 'evidence', 'workflow evidence is in use')
                target = workflow_evidence.directory(state.workspace.path) / ident
                if not target.is_dir() or target.is_symlink():
                    problem('missing', 'evidence', 'workflow evidence is unavailable', 404)
                shutil.rmtree(target)
                return {'deleted': True}
        finally:
            writer.release()

    def active_view(self):
        with self.workshop.lock:
            ident = self.workshop.active
            job = self.jobs.get(ident)
        return ({'operation_id': ident, 'name': job.name, 'batch': job.is_batch}
                if job is not None and job.view()['outcome'] == 'running' else {})

    def job(self, ident):
        with self.workshop.lock:
            job = self.jobs.get(ident)
        if job is None:
            problem('missing', 'job', 'workflow job does not exist', 404)
        return job

    def stop(self, ident, *, shutdown=False):
        try:
            self.job(ident).cancel(runs.USER_STOP)
        except (WorkshopError, runs.NoSuchRunInMemory):
            if not shutdown:
                raise
        return {'status': 'stopping'}


def old_node_keys(bundle):
    return bundle['definition']['nodes'].keys() if bundle is not None else set()


def exact(value):
    from .jsonio import encode_exact
    return encode_exact(value, indent=2)

