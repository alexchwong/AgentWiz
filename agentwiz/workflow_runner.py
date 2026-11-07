"""Deterministic workflow planning and serial execution."""
from __future__ import annotations

import dataclasses
import heapq
import threading
import time

from . import checks, jsonio, model_call, outbound_policy, runner, runs, task_settings
from .state import NoSuchServer, RoleUnresolved
from .workflow_evidence import EvidenceError
from .workflow_definition import bundle_graph, digest, names, problem, select, source, value_text
from .workshop import WorkshopError
from .workshop_service import judgement, load_capture


def issue(code, location, message):
    return {'code': code, 'location': location, 'message': message, 'severity': 'error'}


class Graph:
    def __init__(self, bundle, name, stack):
        self.bundle = bundle
        self.captures, self.contracts = bundle_graph(bundle, name, stack)
        self.definition = definition = bundle['definition']
        self.digest = digest({'definition': definition, 'snapshots': bundle['snapshots']})
        self.blocks = {node: opened.parsed.block for node, opened in self.captures.items()}
        self.sources = {}
        self.source_contracts = {}
        self.restricted = set()
        self.origins = {}
        self.issues = []
        for side, kind in (('inputs', 'input'), ('constants', 'constant')):
            for name, declaration in definition[side].items():
                identity = kind, name
                self.sources[identity] = declaration
                if kind == 'input':
                    self.source_contracts[identity] = self.contracts['inputs'][name]
                if declaration['local_only']:
                    self.restricted.add(identity)
                    self.origins[identity] = f'{side}.{name}'
        for node, block in self.blocks.items():
            for output in block.outputs:
                identity = 'node', node, output.name
                self.sources[identity] = output
                self.source_contracts[identity] = block.contracts['outputs'][output.name].parsed
                if output.local_only:
                    self.restricted.add(identity)
                    self.origins[identity] = f'nodes.{node}.outputs.{output.name}'
        self.bindings, self.final = {}, {}
        dependencies = {node: set() for node in self.blocks}
        for node, block in self.blocks.items():
            supplied = names(definition['nodes'][node]['bindings'], 0, 50, f'nodes.{node}.bindings')
            ports = {port.name: port for port in block.inputs}
            if supplied.keys() - ports.keys():
                problem('invalid', f'nodes.{node}.bindings', 'binding names an undeclared input')
            self.bindings[node] = {}
            for name, port in ports.items():
                location = f'nodes.{node}.inputs.{name}'
                if name not in supplied:
                    self.issues.append(issue('binding_missing', location, 'connect an input source'))
                    continue
                binding = supplied[name]
                identity = self.bind(binding, location, port.local_only)
                self.bindings[node][name] = identity, binding.get('path', []), location
                self.check_compatibility(self.bindings[node][name], block.contracts['inputs'][name].parsed)
                if identity[0] == 'node':
                    dependencies[node].add(identity[1])
        for port, declaration in definition['outputs'].items():
            location = f'outputs.{port}'
            if 'binding' not in declaration:
                self.issues.append(issue('binding_missing', location, 'connect an output source'))
                continue
            binding = declaration['binding']
            identity = self.bind(binding, location, declaration['local_only'])
            self.final[port] = identity, binding.get('path', []), location
            self.check_compatibility(self.final[port], self.contracts['outputs'][port])
        if sum(map(len, self.bindings.values())) + len(self.final) > 512:
            problem('too_large', 'bindings', 'workflow exceeds 512 bindings')
        ready = [node for node, dependencies_for_node in dependencies.items() if not dependencies_for_node]
        heapq.heapify(ready)
        self.order = []
        while ready:
            node = heapq.heappop(ready)
            self.order.append(node)
            for consumer, pending in dependencies.items():
                if node in pending:
                    pending.remove(node)
                    if not pending:
                        heapq.heappush(ready, consumer)
        if len(self.order) != len(self.blocks):
            problem('workflow_cycle', 'nodes', 'the workflow has a cycle')
        self.protected_nodes = set()
        self.runtime = {}
        self.propagate()
        self.base_restricted = frozenset(self.restricted)
        self.base_origins = dict(self.origins)

    def select_example(self, example):
        self.restricted = set(self.base_restricted)
        self.origins = dict(self.base_origins)
        self.protected_nodes = set()
        self.runtime = {}
        for port in example.get('restrictions', {}):
            identity = ('input', port)
            self.restricted.add(identity)
            self.origins.setdefault(identity, 'saved example')
        self.propagate()

    def propagate(self):
        for node in self.order:
            block = self.blocks[node]
            if block.context_local_only or any(identity in self.restricted for identity, _, _ in self.bindings[node].values()):
                self.protected_nodes.add(node)
                inherited = next((self.origins.get(identity, location)
                                  for identity, _, location in self.bindings[node].values()
                                  if identity in self.restricted), f'nodes.{node}.context')
                for port in block.outputs:
                    identity = ('node', node, port.name)
                    self.restricted.add(identity)
                    self.origins.setdefault(identity, inherited)

    def bind(self, binding, location, restricted):
        identity = source(binding, location)
        if identity not in self.sources:
            problem('invalid', location, 'the binding source does not exist')
        if restricted:
            self.restricted.add(identity)
            self.origins.setdefault(identity, location)
        return identity

    def check_compatibility(self, binding, receiver):
        identity, path, location = binding
        sender = self.source_contracts.get(identity)
        if sender is None:
            return
        schema = {'type': 'string'} if sender.format == 'text' else sender.schema
        destination = {'type': 'string'} if receiver.format == 'text' else receiver.schema
        for part in path:
            if not isinstance(schema, dict):
                return
            required = 'object' if isinstance(part, str) else 'array'
            if required not in schema_types(schema):
                self.issues.append(issue('contract_mismatch', location, 'the selector cannot traverse the source type'))
                return
            if required == 'object':
                properties = schema.get('properties', {})
                if part not in properties and schema.get('additionalProperties') is False:
                    self.issues.append(issue('contract_mismatch', location, 'the selected key is absent from the closed source contract'))
                    return
                schema = properties.get(part)
            else:
                if 'prefixItems' in schema:
                    return
                schema = schema.get('items')
        if not schema_types(schema) & schema_types(destination):
            self.issues.append(issue('contract_mismatch', location, 'the source and receiver have disjoint value types'))

    def initial_values(self, inputs):
        if not isinstance(inputs, dict) or inputs.keys() != self.contracts['inputs'].keys():
            problem('invalid', 'inputs', 'provide each workflow input exactly once', 400)
        values = {('constant', name): declaration['value'] for name, declaration in self.definition['constants'].items()}
        for name, contract in self.contracts['inputs'].items():
            text = inputs[name]
            if not isinstance(text, str):
                problem('invalid', f'inputs.{name}', 'submit exact input as text', 400)
            try:
                value = text if contract.format == 'text' else jsonio.decode_exact(text)
            except ValueError:
                problem('contract_mismatch', f'inputs.{name}', 'invalid exact JSON')
            validate(contract, value, f'inputs.{name}')
            values['input', name] = value
        for node, bindings in self.bindings.items():
            for name, binding in bindings.items():
                if binding[0] in values:
                    bound_value(values, binding, self.blocks[node].contracts['inputs'][name].parsed)
        for name, binding in self.final.items():
            if binding[0] in values:
                bound_value(values, binding, self.contracts['outputs'][name])
        return values

    def ancestor_warnings(self):
        issues = []
        origins = [(binding[0], binding[2]) for bindings in self.bindings.values()
                   for binding in bindings.values() if binding[0] in self.restricted]
        origins.extend((binding[0], binding[2]) for binding in self.final.values() if binding[0] in self.restricted)
        for identity, location in origins:
            frontier, seen = [identity], set()
            while frontier:
                source_id = frontier.pop()
                if source_id in seen or source_id[0] != 'node':
                    continue
                seen.add(source_id)
                node = source_id[1]
                if node not in self.protected_nodes and self.blocks[node].step.kind in ('model', 'tool'):
                    warning = issue('unrestricted_ancestor', location, f'upstream node {node} can dispatch before this restriction applies')
                    issues.append({**warning, 'severity': 'warning'})
                    if len(issues) >= 32:
                        return issues
                frontier.extend(binding[0] for binding in self.bindings[node].values())
        return issues

    def destinations(self, state, workshop, model_roles=None, mcp_servers=None):
        destinations, issues, private = {}, [], {}
        if model_roles is None:
            model_roles = {}
        if mcp_servers is None:
            mcp_servers = {}
        required = 0
        for node in self.order:
            block = self.blocks[node]
            step = block.step
            if step.kind == 'transform':
                continue
            location = f'nodes.{node}'
            role = None
            try:
                if step.kind == 'model':
                    required += 1
                    if step.role not in model_roles:
                        role = state.role_capture(step.role)
                        snapshot = role.snapshot
                        key = outbound_policy.credential(snapshot.endpoint)
                        snapshot = dataclasses.replace(snapshot, endpoint=dataclasses.replace(
                            snapshot.endpoint, api_key=key, api_key_env=None))
                        model_roles[step.role] = dataclasses.replace(role, snapshot=snapshot), key
                    role, key = model_roles[step.role]
                    snapshot = role.snapshot
                    if snapshot.kind == 'openrouter' and not key:
                        issues.append(issue('missing_credential', location, 'the configured model needs a credential'))
                        continue
                    effective = task_settings.resolve_effective(step.task, step.task_resets, role.defaults)
                    model_call.task_arguments(effective.task, snapshot.kind, snapshot.provider)
                    view = {'kind': snapshot.kind, 'url': snapshot.endpoint.base_url, 'model': snapshot.model,
                            'provider': snapshot.provider, 'role': step.role,
                            'task_text': jsonio.encode_exact(effective.task.request_shape())}
                    private[node] = workshop.credential_identity(key)
                else:
                    if step.server not in mcp_servers:
                        mcp_servers[step.server] = state.mcp_snapshot_for_block(step.server)
                    snapshot = mcp_servers[step.server]
                    observed = state.mcp_client.observation(snapshot.identity, step.tool)
                    snapshot_id = self.definition['nodes'][node]['snapshot_id']
                    effects = self.bundle['snapshots'][snapshot_id]['provenance'].get('effects', 'unknown')
                    view = {'kind': 'mcp', 'url': snapshot.url, 'server': step.server, 'tool': step.tool,
                            'observation': observed, 'effects': 'unknown', 'effect_claim': effects}
                    private[node] = [workshop.credential_identity(snapshot.token), snapshot.idle_timeout_s,
                                     snapshot.call_deadline_s, snapshot.discovery_deadline_s]
                    entry = state.mcp_client.entry(snapshot.identity)
                    if entry is not None and (step.tool in entry.not_offered or step.tool not in entry.offered):
                        issues.append(issue('tool_not_observed', location, 'the latest tool list does not offer this tool'))
                    elif observed is None:
                        warning = issue('tool_unverified', location, 'refresh tools to verify availability')
                        issues.append({**warning, 'severity': 'warning'})
                destinations[node] = {**view, 'protected': node in self.protected_nodes}
                guard = outbound_policy.block_dispatch(state, block, snapshot, protected=node in self.protected_nodes,
                                                       defaults=role.defaults if role else None)
                destinations[node] = {**view, 'trust_revision': guard.revision, 'protected': guard.destination is not None}
                self.runtime[node] = snapshot, role, guard
            except (outbound_policy.Refused, NoSuchServer, RoleUnresolved, task_settings.TaskSettingError) as exc:
                code = str(exc) if isinstance(exc, outbound_policy.Refused) else 'destination_unavailable'
                issues.append(issue(code, location, 'the reviewed destination is unavailable or cannot receive these values'))
        if required > self.definition.get('budget', {}).get('max_model_attempts', 32):
            issues.append(issue('budget_exceeded', 'budget.max_model_attempts', 'budget is below the required initial model calls'))
        return destinations, issues, private

    def preflight(self, inputs):
        issues = list(self.issues)
        try:
            self.initial_values(inputs)
        except WorkshopError as exc:
            issues.extend(exc.issues)
        return {'executable_digest': self.digest, 'order': self.order, 'issues': issues[:100],
                'protected_nodes': sorted(self.protected_nodes),
                'restricted_sources': [{'source': list(identity), 'origin': self.origins.get(identity, 'saved example')}
                                       for identity in sorted(self.restricted)]}


def schema_types(schema):
    all_types = {'object', 'array', 'string', 'integer', 'noninteger', 'boolean', 'null'}
    if not isinstance(schema, dict) or 'type' not in schema:
        return all_types
    declared = schema['type']
    types = {declared} if isinstance(declared, str) else set(declared)
    if 'number' in types:
        types.update(('integer', 'noninteger'))
    return types

def validate(contract, value, location):
    text = value_text(value)
    if (contract.format == 'text' and not isinstance(value, str)
            or checks.check_value(contract, value)['overall'] != 'pass'):
        problem('contract_mismatch', location, 'the selected value fails the receiving contract')
    return value if contract.format == 'text' else text


def bound_value(values, binding, contract):
    identity, path, location = binding
    value = select(values[identity], path, location)
    return value, validate(contract, value, location)


class Job(runner.Observer):
    def __init__(self, state):
        self.state = state
        self.lock = threading.Lock()
        self.result = None
        self.settling = False
        self.child = None
        self.details = {}
        self.trace_bytes = 0
        self.reason = None
        self.attempts = 0
        self.attempt_limit = 0
        self.evidence = None
        self.case = 0
        self.batch = False
        self.live_rows = {}
        self.reviews = {}

    def view(self):
        with self.lock:
            if self.result is None:
                return {'outcome': 'running', 'example_index': self.case, 'nodes': dict(self.live_rows)}
            return {**self.result, 'reviews': list(self.reviews.values())} if self.reviews else self.result

    def detail(self, node):
        with self.lock:
            detail = self.details.get(node)
        if detail is None:
            problem('missing', f'nodes.{node}', 'node trace is unavailable', 404)
        return detail

    def cancel(self, reason=runs.USER_STOP):
        with self.lock:
            if self.result is not None or self.settling:
                return
            if self.reason is None:
                self.reason = reason
            child, reason = self.child, self.reason
        if child is not None:
            try:
                self.state.store.stop_run(child, reason)
            except runs.NoSuchRunInMemory:
                pass

    def started(self, event):
        with self.lock:
            self.child = event['run_id']
            reason = self.reason
        if reason is not None:
            self.state.store.stop_run(event['run_id'], reason)

    def attempt_committed(self, record):
        if self.evidence:
            try:
                self.evidence.attempt(record)
            except (OSError, EvidenceError, ValueError):
                self.evidence.error = True

    def allow_attempt(self):
        with self.lock:
            if self.reason is not None or self.attempts >= self.attempt_limit or (self.evidence and self.evidence.error):
                return False
            self.attempts += 1
            return True

    def execute_node(self, graph, node, values):
        opened, block = graph.captures[node], graph.blocks[node]
        texts = {name: bound_value(values, binding, block.contracts['inputs'][name].parsed)[1]
                 for name, binding in graph.bindings[node].items()}
        if self.evidence:
            self.evidence.pending(node)
        with self.lock:
            if self.reason is None:
                self.live_rows[node] = {'outcome': 'dispatch_pending'}
        snapshot, role, guard = graph.runtime.get(node, (None, None, None))
        frozen = runner.FrozenSource(snapshot, False, {'workflow_node': node}, owner=opened, capture=role,
                                     protected=node in graph.protected_nodes, dispatch=guard,
                                     attempt_gate=self.allow_attempt)
        return runner.run_block(self.state, load_capture(opened, block.name), texts, self, frozen=frozen).result

    def execute(self, graph, inputs):
        budget = graph.definition.get('budget', {})
        timeout = budget.get('max_seconds', 1800)
        self.attempt_limit = budget.get('max_model_attempts', 32)
        self.attempts = 0
        deadline = time.monotonic() + timeout
        timer = runner.make_timer(timeout, lambda: self.cancel(runs.BUDGET_EXCEEDED))
        rows = {node: {'outcome': 'not_run'} for node in graph.order}
        with self.lock:
            self.live_rows = dict(rows)
        outputs, issues, outcome = {}, [], 'rejected'
        checking_final = False
        try:
            values = graph.initial_values(inputs)
            working_bytes = sum(len(value_text(value).encode('utf-8')) for value in values.values())
            if working_bytes > 32 << 20:
                problem('too_large', 'inputs', 'working values exceed 32 MiB')
            for node in graph.order:
                if self.reason is not None:
                    break
                started = time.monotonic()
                result = self.execute_node(graph, node, values)
                elapsed_ms = round((time.monotonic() - started) * 1000)
                outcome = result['outcome']
                size = len(jsonio.encode_exact(result).encode('utf-8'))
                with self.lock:
                    self.child = None
                    retained = size <= 1 << 20 and self.trace_bytes + size <= 4 << 20
                    if retained:
                        self.details[f'{self.case}:{node}' if self.batch else node] = result
                        self.trace_bytes += size
                rows[node] = {'outcome': outcome, 'detail_available': retained, 'elapsed_ms': elapsed_ms}
                with self.lock:
                    self.live_rows[node] = rows[node]
                if self.evidence:
                    if self.evidence.error:
                        raise EvidenceError('attempt evidence could not be written')
                    self.evidence.node_result(node, result)
                if outcome != 'complete':
                    steps = result.get('steps', [])
                    failures = [step.get('failure') for step in steps]
                    failures.extend((step.get('operation') or {}).get('reason') for step in steps)
                    messages = [item.get('message') for item in result.get('issues', [])]
                    policy_codes = {'destination_changed', 'destination_ineligible', 'destination_untrusted',
                                    'destination_trust_unavailable', 'destination_proxied'}
                    if policy_codes.intersection(failures + messages):
                        issues.append(issue('destination_changed', f'nodes.{node}', 'the destination is no longer authorised'))
                    break
                for name, view in result['outputs'].items():
                    value = view['text'] if view['format'] == 'text' else jsonio.decode_exact(view['text'])
                    working_bytes += len(value_text(value).encode('utf-8'))
                    if working_bytes > 32 << 20:
                        problem('too_large', f'nodes.{node}.outputs.{name}', 'working values exceed 32 MiB')
                    values['node', node, name] = value
            else:
                checking_final = True
                for name, binding in graph.final.items():
                    contract = graph.contracts['outputs'][name]
                    _, text = bound_value(values, binding, contract)
                    outputs[name] = {'format': contract.format, 'text': text, 'local_only': binding[0] in graph.restricted}
                if sum(len(view['text'].encode('utf-8')) for view in outputs.values()) > 16 << 20:
                    problem('too_large', 'outputs', 'combined final outputs exceed 16 MiB')
                outcome = 'complete'
        except (OSError, EvidenceError):
            issues, outcome = [issue('evidence_write_failed', 'workflow', 'required workflow evidence could not be written')], ('unknown' if outcome == 'unknown' else 'failed')
        except WorkshopError as exc:
            issues, outcome = exc.issues, 'failed' if checking_final else 'rejected'
        except runner.Busy:
            issues, outcome = [issue('admission_busy', 'workflow', 'a child execution slot is busy')], 'rejected'
        finally:
            timer.cancel()
            with self.lock:
                if self.reason is not None or time.monotonic() >= deadline:
                    if outcome != 'unknown':
                        outcome = 'stopped' if self.reason == runs.USER_STOP else 'budget_exceeded'
        reason = issues[0]['code'] if issues else ('call_unknown' if outcome == 'unknown' else outcome)
        for row in rows.values():
            if row['outcome'] == 'not_run':
                row['reason'] = reason
        return {'outcome': outcome, 'nodes': rows, 'issues': issues, 'model_attempts': self.attempts,
                'execution_complete': checking_final, 'checks_passed': outcome == 'complete',
                'outputs': outputs if outcome == 'complete' else {}}

    def execute_batch(self, graph, examples):
        self.batch = len(examples) > 1 or examples[0].get('id') is not None
        rows = []
        terminal = {'unknown', 'stopped'}
        fatal = {'admission_busy', 'evidence_write_failed', 'destination_changed'}
        end_batch = None
        preview_bytes = 0
        for index, example in enumerate(examples):
            ident = {'id': example.get('id'), 'version': example.get('version')}
            if self.reason is not None or end_batch:
                rows.append({**ident, 'outcome': 'not_run', 'correctness': 'unknown',
                             'reason': end_batch or self.reason})
                continue
            self.case = index
            if self.evidence:
                self.evidence.case = index
            graph.select_example(example)
            graph.runtime = graph.example_runtime[index]
            row = self.execute(graph, example['inputs'])
            with self.lock:
                if self.reason == runs.USER_STOP and row['outcome'] != 'unknown':
                    row = {**row, 'outcome': 'stopped', 'checks_passed': False, 'outputs': {}}
            correctness = judgement(row['outputs'], example.get('expectation', {'kind': 'review'}),
                                    example.get('verified', False))
            end_batch = next((item['code'] for item in row['issues'] if item['code'] in fatal), None)
            if not end_batch and row['outcome'] in terminal:
                end_batch = 'call_unknown' if row['outcome'] == 'unknown' else 'user_stop'
            if not self.batch:
                return row
            if not end_batch:
                with self.lock:
                    if self.reason == runs.BUDGET_EXCEEDED:
                        self.reason = None
            result_digest = digest(row['outputs'])
            if self.evidence and row['outputs']:
                try:
                    file = f'{index}-outputs.json'
                    self.evidence.write(file, row['outputs'])
                    self.evidence.manifest['records'].append(file)
                except (OSError, EvidenceError, ValueError):
                    end_batch = 'evidence_write_failed'
                    correctness = 'unknown'
                    row = {**row, 'outcome': 'failed', 'save_error': end_batch, 'outputs': {}}
            size = len(jsonio.encode_exact(row['outputs']).encode('utf-8'))
            if preview_bytes + size > 16 << 20:
                row = {**row, 'outputs': {}, 'outputs_omitted': True}
            else:
                preview_bytes += size
            rows.append({**ident, **row, 'correctness': correctness, 'end_batch': bool(end_batch),
                         'result_digest': result_digest})
        done = [row for row in rows if row['outcome'] != 'not_run']
        outcome = next((row['outcome'] for row in reversed(done) if row.get('end_batch')),
                       'complete' if len(done) == len(rows) and all(row['outcome'] == 'complete' for row in done) else 'failed')
        return {'outcome': outcome, 'examples': rows, 'metrics': {
            'selected': len(rows), 'executed': len(done), 'complete': sum(r['execution_complete'] for r in done),
            'checks_passed': sum(r['checks_passed'] for r in done),
            'correct': sum(r['correctness'] == 'correct' for r in done),
            'incorrect': sum(r['correctness'] == 'incorrect' for r in done),
            'unknown': sum(r['correctness'] == 'unknown' for r in rows)}}

