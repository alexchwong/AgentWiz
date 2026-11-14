"""Deterministic workflow planning and serial execution."""
from __future__ import annotations

import dataclasses
import heapq
import hashlib
import secrets
import copy
import threading
import time

from . import release_gate, checks, jsonio, model_call, outbound_policy, runner, runs, task_settings
from .state import NoSuchServer, RoleUnresolved
from .workflow_evidence import EvidenceError
from .workflow_definition import bundle_graph, digest, names, problem, select, source, value_text
from .workshop import WorkshopError
from .workshop_service import judgement, load_capture


def issue(code, location, message):
    return {'code': code, 'location': location, 'message': message, 'severity': 'error'}


class Graph:
    def __init__(self, bundle, name, stack, *, lenient=False):
        """`lenient` (design-time validation only) collects located binding problems instead of raising, and
        records a cycle or an unknown source in `unplannable`; such a graph has no effective projection."""
        self.bundle = bundle
        self.lenient = lenient
        self.unplannable = {'cycle': [], 'unknown_source': []}
        self.captures, self.contracts = bundle_graph(bundle, name, stack)
        self.definition = definition = bundle['definition']
        self.gates = {node: row['gate'] for node, row in definition['nodes'].items() if 'gate' in row}
        identity = {'definition': definition, 'snapshots': bundle['snapshots']}
        if self.gates:
            identity.update(bundle_format=bundle['format_version'], assessment_version=release_gate.ASSESSMENT_VERSION,
                            assessment_prompt=release_gate.ASSESSMENT_PROMPT, assessment_contract=release_gate.ASSESSMENT_CONTRACT)
        self.digest = digest(identity)
        self.blocks = {node: opened.parsed.block for node, opened in self.captures.items()}
        self.assessments = {node: release_gate.assessment_capture(config, stack) for node, config in self.gates.items()}
        self.blocks.update({node: release_gate.block(config, definition['nodes'][node]['bindings'])
                            for node, config in self.gates.items()})
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
            supplied = self.node_bindings(node, block)
            ports = {port.name: port for port in block.inputs}
            self.bindings[node] = {}
            for name, port in ports.items():
                location = f'nodes.{node}.inputs.{name}'
                if name not in supplied:
                    self.issues.append(issue('binding_missing', location, 'connect an input source'))
                    continue
                binding = supplied[name]
                identity = self.bind(binding, location, port.local_only)
                if identity is None:
                    continue
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
            if identity is None:
                continue
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
            if not lenient:
                problem('workflow_cycle', 'nodes', 'the workflow has a cycle')
            self.unplannable['cycle'] = sorted(set(self.blocks) - set(self.order))
        self.available = not any(self.unplannable.values())
        self.protected_nodes = set()
        self.runtime = {}
        if self.available:
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
            if node in self.gates:
                if self.assessments[node] is not None:
                    self.protected_nodes.add(node)
                continue
            if block.context_local_only or any(identity in self.restricted for identity, _, _ in self.bindings[node].values()):
                self.protected_nodes.add(node)
                inherited = next((self.origins.get(identity, location)
                                  for identity, _, location in self.bindings[node].values()
                                  if identity in self.restricted), f'nodes.{node}.context')
                for port in block.outputs:
                    identity = ('node', node, port.name)
                    self.restricted.add(identity)
                    self.origins.setdefault(identity, inherited)

    def node_bindings(self, node, block):
        location = f'nodes.{node}.bindings'
        try:
            supplied = names(self.definition['nodes'][node]['bindings'], 0, 50, location)
        except WorkshopError as exc:
            if not self.lenient:
                raise
            self.issues.extend({**row, 'severity': 'error'} for row in exc.issues)
            return {}
        undeclared = supplied.keys() - {port.name for port in block.inputs}
        if undeclared:
            if not self.lenient:
                problem('invalid', location, 'binding names an undeclared input')
            self.issues.append(issue('invalid', location, 'binding names an undeclared input'))
            supplied = {key: value for key, value in supplied.items() if key not in undeclared}
        return supplied

    def bind(self, binding, location, restricted):
        try:
            identity = source(binding, location)
        except WorkshopError as exc:
            if not self.lenient:
                raise
            self.issues.extend({**row, 'severity': 'error'} for row in exc.issues)
            return None
        if identity not in self.sources:
            if not self.lenient:
                problem('invalid', location, 'the binding source does not exist')
            self.unplannable['unknown_source'].append(location)
            self.issues.append(issue('invalid', location, 'the binding source does not exist'))
            return None
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

    def release_scope(self, node):
        reached, pending = set(), [node]
        while pending:
            current = pending.pop()
            for consumer, bindings in self.bindings.items():
                if consumer in reached or not any(binding[0][0] == 'node' and binding[0][1] == current
                                                   for binding in bindings.values()):
                    continue
                reached.add(consumer)
                if consumer not in self.gates:
                    pending.append(consumer)
        return {key: self.destination_views[key] for key in sorted(reached) if key in self.destination_views}

    def released_lineage(self, identity):
        pending, seen, boundary = [identity], set(), False
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            if current[0] == 'constant':
                continue
            if current[0] != 'node':
                continue
            node = current[1]
            if node in self.gates:
                boundary = True
                continue
            bindings = self.bindings[node]
            pending.extend(binding[0] for binding in bindings.values())
        return boundary

    def ancestor_warnings(self):
        issues = []
        origins = [(binding[0], binding[2], node in self.gates) for node, bindings in self.bindings.items()
                   for name, binding in bindings.items() if (node in self.gates and name == 'value')
                   or (name == 'scan' and binding[0] in self.restricted)
                   or (node not in self.gates and binding[0] in self.restricted)]
        origins.extend((binding[0], binding[2], False) for binding in self.final.values() if binding[0] in self.restricted)
        # Safety errors must not be hidden behind the bounded ordinary warning list.
        origins.sort(key=lambda row: not row[2])
        for identity, location, releasing in origins:
            frontier, seen = [identity], set()
            while frontier:
                source_id = frontier.pop()
                if source_id in seen or source_id[0] != 'node':
                    continue
                seen.add(source_id)
                node = source_id[1]
                if node in self.gates:
                    continue
                if (node not in self.protected_nodes and self.blocks[node].step.kind in ('model', 'tool')
                        and not (releasing and self.released_lineage(source_id))):
                    warning = {**issue('unrestricted_ancestor', location, f'upstream node {node} can dispatch before this restriction applies'),
                               'node': node}
                    issues.append({**warning, 'severity': 'error' if releasing else 'warning'})
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
            if node in self.gates:
                opened = self.assessments[node]
                if opened is None:
                    continue
                block = opened.parsed.block
            step = block.step
            if step.kind == 'transform':
                continue
            location = f'nodes.{node}'
            role = None
            destination = None
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
                    destination = {'kind': snapshot.kind, 'url': snapshot.endpoint.base_url, 'model': snapshot.model,
                                   'provider': snapshot.provider or ''}
                    view = {'kind': snapshot.kind, 'url': snapshot.endpoint.base_url, 'model': snapshot.model,
                            'provider': snapshot.provider, 'role': step.role,
                            'task_text': jsonio.encode_exact(effective.task.request_shape())}
                    private[node] = workshop.credential_identity(key)
                else:
                    if step.server not in mcp_servers:
                        mcp_servers[step.server] = state.mcp_snapshot_for_block(step.server)
                    snapshot = mcp_servers[step.server]
                    observed = state.mcp_client.observation(snapshot.identity, step.tool)
                    destination = {'kind': 'mcp', 'url': snapshot.url, 'server': step.server, 'tool': step.tool}
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
                restricted_gate = next((binding for binding in self.bindings[node].values()
                                        if binding[0] in self.restricted and binding[0][0] == 'node'
                                        and binding[0][1] in self.gates), None)
                if isinstance(exc, outbound_policy.Refused) and restricted_gate:
                    origin = self.origins.get(restricted_gate[0], restricted_gate[2])
                    found = issue(code, origin, 'this local-only port restricts the released value; the destination cannot receive it')
                else:
                    found = issue(code, location, 'the reviewed destination is unavailable or cannot receive these values')
                fact = outbound_policy.fact_view(state, destination)
                if isinstance(exc, RoleUnresolved):
                    fact, action = {'available': False, 'reason': exc.reason}, outbound_policy.remedy('role_unresolved', target=step.role)
                elif isinstance(exc, NoSuchServer):
                    fact, action = {'available': False, 'reason': 'server_missing'}, outbound_policy.remedy('server_missing', target=step.server)
                elif isinstance(exc, outbound_policy.Refused):
                    action = outbound_policy.remedy(code, fact, upstream_only=not restricted_gate and self.upstream_only(node))
                else:
                    action = None
                issues.append({**found, 'facts': fact, 'remedy': action})
        if required > self.definition.get('budget', {}).get('max_model_attempts', 32):
            issues.append(issue('budget_exceeded', 'budget.max_model_attempts', 'budget is below the required initial model calls'))
        self.destination_views = destinations
        return destinations, issues, private

    def gate_findings(self):
        issues = []
        for node in self.gates:
            binding = self.bindings[node].get('value')
            if binding and binding[0] not in self.restricted:
                issues.append({**issue('release_unneeded', f'nodes.{node}', 'the candidate is already unrestricted'), 'severity': 'warning'})
            scan = self.bindings[node].get('scan')
            if scan and scan[0] not in self.restricted:
                issues.append(issue('scan_unrestricted', f'nodes.{node}.scan', 'identifier mapping must be local-only'))
        return issues

    def preflight(self, inputs):
        issues = list(self.issues) + self.gate_findings()
        try:
            self.initial_values(inputs)
        except WorkshopError as exc:
            issues.extend(exc.issues)
        return {'executable_digest': self.digest, 'order': self.order, 'issues': issues[:100],
                'protected_nodes': sorted(self.protected_nodes),
                'restricted_sources': [{'source': list(identity), 'origin': self.origins.get(identity, 'saved example')}
                                       for identity in sorted(self.restricted)]}


    def labelled(self, identity):
        """Whether any port receiving `identity` is Local only (a consumer label)."""
        for node, bindings in self.bindings.items():
            ports = {port.name: port for port in self.blocks[node].inputs}
            if any(binding[0] == identity and ports[name].local_only for name, binding in bindings.items()):
                return True
        return any(binding[0] == identity and self.definition['outputs'][port]['local_only']
                   for port, binding in self.final.items())

    def upstream_only(self, node):
        """Whether `node` is protected solely because an upstream protected node produced its inputs, so a
        human-reviewed release could legitimately expose them (core v3 §3.4 `add_release_gate`)."""
        if node not in self.protected_nodes or node in self.gates or self.blocks[node].context_local_only:
            return False
        restricted = [binding[0] for binding in self.bindings[node].values() if binding[0] in self.restricted]
        return bool(restricted) and all(
            identity[0] == 'node' and identity[1] not in self.gates and identity[1] in self.protected_nodes
            and not self.sources[identity].local_only and not self.labelled(identity) for identity in restricted)

    # --- explanation projection (core v3 §4.2): describes bind/propagate, never decides protection ----
    def explain(self, example=None):
        """Why each value is restricted and why each node is protected, for the base basis or one example.
        Selecting a basis replaces the previous one; restrictions from different examples are never merged."""
        basis = ('base' if example is None or 'id' not in example
                 else {'example_id': example['id'], 'version': example['version']})
        if not self.available:
            return {'basis': basis, 'available': False}
        self.select_example(example or {})
        restrictions = set((example or {}).get('restrictions', {})) if basis != 'base' else set()
        consumers = {}
        for node, bindings in self.bindings.items():
            ports = {port.name: port for port in self.blocks[node].inputs}
            for name, (identity, _, location) in bindings.items():
                consumers.setdefault(identity, []).append((location, ports[name].local_only, node))
        for port, (identity, _, location) in self.final.items():
            consumers.setdefault(identity, []).append(
                (location, self.definition['outputs'][port]['local_only'], None))
        values = {}
        for identity in sorted(self.sources):
            restricted = identity in self.restricted
            causes = self.value_causes(identity, consumers.get(identity, []), restrictions, basis) if restricted else []
            if not restricted and identity[0] == 'node' and identity[1] in self.gates:
                causes = [{'code': 'released', 'gate': identity[1]}]
            values[value_id(identity)] = bounded({'source': list(identity), 'restricted': restricted}, causes)
        nodes = {}
        for node in self.order:
            protected = node in self.protected_nodes
            causes = []
            if protected:
                if node in self.gates:
                    causes.append({'code': 'release_assessment', 'node': node})
                for name, (identity, _, _) in sorted(self.bindings[node].items()):
                    if identity in self.restricted:
                        causes.append({'code': 'restricted_input', 'input': name, 'value': value_id(identity)})
                if self.blocks[node].context_local_only:
                    capture = self.captures.get(node)
                    inherited = capture is not None and capture.metadata.get('protection_origin') == 'inherited'
                    causes.append({'code': 'context_inherited' if inherited else 'context_manual', 'node': node})
            nodes[node] = bounded({'protected': protected}, causes)
        return {'basis': basis, 'available': True, 'values': values, 'nodes': nodes}

    def value_causes(self, identity, consumers, restrictions, basis):
        causes = []
        kind = identity[0]
        if kind in ('input', 'constant') and self.sources[identity]['local_only']:
            causes.append({'code': 'workflow_io', 'port': identity[1], 'side': 'inputs' if kind == 'input' else 'constants'})
        if kind == 'input' and identity[1] in restrictions:
            causes.append({'code': 'example_restriction', 'example_id': basis['example_id'], 'input': identity[1]})
        if kind == 'node' and self.sources[identity].local_only:
            causes.append({'code': 'declared_label', 'port': f'nodes.{identity[1]}.outputs.{identity[2]}'})
        labelled = [row for row in consumers if row[1]]
        # A label on a value that other, unlabelled ports also receive restricts them too (fan-out).
        fan_out = len(labelled) < len(consumers)
        for location, _, node in labelled:
            if node is None:
                causes.append({'code': 'workflow_io', 'port': location.split('.', 1)[1], 'side': 'outputs'})
            elif fan_out:
                causes.append({'code': 'consumer_label', 'consumer': location})
            else:
                causes.append({'code': 'declared_label', 'port': location})
        if kind == 'node' and identity[1] in self.protected_nodes and identity[1] not in self.gates:
            causes.append({'code': 'upstream', **self.upstream_chain(identity)})
        return causes

    def upstream_chain(self, identity):
        """One restricted predecessor per hop, walked through the bindings, at most 8 hops."""
        chain, current = [], identity
        for _ in range(8):
            node = current[1]
            previous = next((binding[0] for _, binding in sorted(self.bindings[node].items())
                             if binding[0] in self.restricted), None)
            if previous is None:
                chain.append(['context', node])
                return {'chain': chain, 'truncated': False}
            chain.append(list(previous))
            own_label = (previous[0] != 'node' or previous[1] in self.gates
                         or previous[1] not in self.protected_nodes or self.sources[previous].local_only)
            if own_label:
                return {'chain': chain, 'truncated': False}
            current = previous
        return {'chain': chain, 'truncated': True}


def value_id(identity):
    return '.'.join(identity)


def bounded(item, causes, limit=10):
    item['causes'] = causes[:limit]
    if len(causes) > limit:
        item['more'] = len(causes) - limit
    return item


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
        self.ident = secrets.token_hex(16)
        self.phase = 'running'
        self.release_condition = threading.Condition(self.lock)
        self.pending_release = None
        self.release_choice = None
        self.release_deadline = 0
        self.release_decisions = {}
        self.budget_epoch = 0
        self.budget_paused = False
        self.deadline = float('inf')

    def view(self):
        with self.lock:
            if self.result is None:
                return {'outcome': 'running', 'phase': self.phase, 'example_index': self.case,
                        'nodes': dict(self.live_rows), 'release': copy.deepcopy(self.pending_release)}
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
            self.release_condition.notify_all()
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

    def _budget_expired(self, epoch):
        with self.lock:
            if epoch != self.budget_epoch or self.budget_paused or self.result is not None:
                return
            if self.reason is None:
                self.reason = runs.BUDGET_EXCEEDED
            child = self.child
            self.release_condition.notify_all()
        if child is not None:
            try:
                self.state.store.stop_run(child, runs.BUDGET_EXCEEDED)
            except runs.NoSuchRunInMemory:
                pass

    def _start_budget(self, seconds):
        with self.lock:
            self.budget_epoch += 1
            epoch = self.budget_epoch
            self.budget_paused = False
            self.deadline = time.monotonic() + seconds
        self.budget_timer = runner.make_timer(max(0, seconds), lambda: self._budget_expired(epoch))

    def _pause_budget(self):
        with self.lock:
            remaining = max(0, self.deadline - time.monotonic())
            self.budget_epoch += 1
            self.budget_paused = True
            if remaining <= 0 and self.reason is None:
                self.reason = runs.BUDGET_EXCEEDED
        self.budget_timer.cancel()
        return remaining

    def decide_release(self, body):
        from .workflow_definition import record
        record(body, ('token', 'payload_digest', 'executable_digest', 'scope_digest', 'checks_digest', 'decision'),
               ('acknowledge_identifiers', 'acknowledge_assessment'), 'release')
        for key in ('acknowledge_identifiers', 'acknowledge_assessment'):
            if key in body and type(body[key]) is not bool:
                problem('invalid', 'release', 'acknowledgements must be booleans', 400)
        if body['decision'] not in ('release', 'reject') or not isinstance(body['token'], str):
            problem('invalid', 'release', 'expected release or reject and a decision token', 400)
        request_digest = digest(body)
        with self.lock:
            previous = self.release_decisions.get(body['token'])
            if previous is not None:
                if previous[0] == request_digest:
                    return previous[1]
                problem('release_stale', 'release', 'this gate already has a different decision')
            current = self.pending_release
            if (not current or self.phase != 'awaiting_release' or self.reason is not None
                    or current['token'] != body['token'] or time.monotonic() >= self.release_deadline
                    or any(body[key] != current[key] for key in
                           ('payload_digest', 'executable_digest', 'scope_digest', 'checks_digest'))):
                problem('release_stale', 'release', 'this release review is no longer current')
            if body['decision'] == 'release':
                if current['identifier_check']['hits'] and not body.get('acknowledge_identifiers', False):
                    problem('release_acknowledgement_required', 'release', 'acknowledge the identifier matches')
                assessment = current['assessment']
                if (assessment['status'] == 'unavailable' or assessment.get('findings')) and not body.get('acknowledge_assessment', False):
                    problem('release_acknowledgement_required', 'release', 'acknowledge the assessment concerns or unavailability')
            self.release_choice = {key: value for key, value in body.items() if key != 'token'}
            self.phase = 'deciding_release'
            response = {'status': 'accepted', 'decision': body['decision']}
            self.release_decisions[body['token']] = request_digest, response
            self.release_condition.notify_all()
            return response

    def execute_gate(self, graph, node, values):
        from datetime import datetime, timezone
        block, config = graph.blocks[node], graph.gates[node]
        value, _ = bound_value(values, graph.bindings[node]['value'], block.contracts['inputs']['value'].parsed)
        text = release_gate.representation(value, block.contracts['outputs']['released'].parsed)
        scan_binding = graph.bindings[node].get('scan')
        mapping = select(values[scan_binding[0]], scan_binding[1], scan_binding[2]) if scan_binding else []
        scan = release_gate.identifier_check(value, mapping, configured=scan_binding is not None)
        assessment = {'status': 'not_configured', 'findings': []}
        assessment_trace = None
        opened = graph.assessments[node]
        if opened is not None:
            with self.lock:
                self.phase = 'assessing_release'
                self.live_rows[node] = {'outcome': 'assessing_release'}
            if self.evidence:
                self.evidence.pending(node)
            snapshot, role, guard = graph.runtime[node]
            frozen = runner.FrozenSource(snapshot, False, {'workflow_node': node, 'release_assessment': True},
                                         owner=opened, capture=role, protected=True, dispatch=guard,
                                         attempt_gate=self.allow_attempt)
            assessment_trace = runner.run_block(self.state, load_capture(opened, opened.parsed.block.name),
                {'candidate': text, 'mapping': jsonio.encode_exact(mapping)}, self, frozen=frozen).result
            with self.lock:
                self.child = None
            if self.reason is not None:
                return {'outcome': 'stopped' if self.reason == runs.USER_STOP else 'budget_exceeded', 'outputs': {}}
            if self.evidence and self.evidence.error:
                raise EvidenceError('assessment evidence could not be written')
            if assessment_trace['outcome'] == 'complete':
                findings = jsonio.decode_exact(assessment_trace['outputs']['assessment']['text'])['findings']
                assessment = {'status': 'complete', 'findings': findings}
            else:
                failures = [step.get('failure') for step in assessment_trace.get('steps', [])]
                failures.extend((step.get('operation') or {}).get('reason') for step in assessment_trace.get('steps', []))
                failures.extend(item.get('message') for item in assessment_trace.get('issues', []))
                if assessment_trace['outcome'] in ('budget_exceeded', 'stopped', 'unknown') or any(str(failure).startswith('destination_') for failure in failures):
                    return assessment_trace
                assessment = {'status': 'unavailable', 'reason': assessment_trace['outcome'], 'findings': []}
        scope = graph.release_scope(node)
        wait = release_gate.review_wait(config)
        review = {'run_id': self.ident, 'example_index': self.case, 'gate': node, 'title': config['title'],
                  'format': block.contracts['outputs']['released'].parsed.format, 'payload': text,
                  'payload_digest': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                  'executable_digest': graph.digest, 'destinations': scope, 'scope_digest': digest(scope),
                  'identifier_check': scan, 'assessment': assessment,
                  'checks_digest': digest([scan, assessment])}
        if self.evidence:
            self.evidence.release_record(node, 'review', {**review, 'max_release_wait_seconds': wait,
                                                         'assessment_trace': assessment_trace})
        remaining = self._pause_budget()
        if self.reason is not None:
            return {'outcome': 'stopped' if self.reason == runs.USER_STOP else 'budget_exceeded', 'outputs': {}}
        review['expires_at'] = time.time() + wait
        with self.lock:
            self.release_choice = None
            self.release_deadline = time.monotonic() + wait
            self.pending_release = {**review, 'token': secrets.token_hex(32)}
            self.phase = 'awaiting_release'
            self.live_rows[node] = {'outcome': 'awaiting_release'}
            while self.release_choice is None and self.reason is None:
                left = self.release_deadline - time.monotonic()
                if left <= 0:
                    break
                self.release_condition.wait(left)
            choice = self.release_choice
            stopped = self.reason
            self.pending_release = None
            self.phase = 'running'
        outcome = ('stopped' if stopped == runs.USER_STOP else 'budget_exceeded') if stopped else (
            'release_expired' if choice is None else 'release_rejected' if choice['decision'] == 'reject' else 'complete')
        self._start_budget(remaining)
        decision = {key: review[key] for key in ('run_id', 'example_index', 'gate', 'payload_digest',
                    'executable_digest', 'destinations', 'scope_digest', 'checks_digest')}
        decision.update(decision=choice['decision'] if choice else outcome,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        identifier_hits=[{'start': hit['start'], 'end': hit['end'], 'identifier': hit['identifier']}
                                         for hit in scan['hits']],
                        assessment_status=assessment['status'],
                        acknowledge_identifiers=bool(choice and choice.get('acknowledge_identifiers')),
                        acknowledge_assessment=bool(choice and choice.get('acknowledge_assessment')))
        if self.evidence:
            self.evidence.release_record(node, 'decision', decision)
        return {'outcome': outcome, 'release_decision': decision, 'release_review': review,
                'assessment_trace': assessment_trace,
                'outputs': {'released': {'format': review['format'], 'text': text}} if outcome == 'complete' else {}}

    def execute_node(self, graph, node, values):
        if node in graph.gates:
            return self.execute_gate(graph, node, values)
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
        self._start_budget(timeout)
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
                        issues.append({**issue('destination_changed', f'nodes.{node}', 'the destination is no longer authorised'),
                                       'remedy': outbound_policy.remedy('destination_changed')})
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
            self.budget_timer.cancel()
            with self.lock:
                self.budget_epoch += 1
                self.pending_release = None
                self.phase = 'running'
                if self.reason is not None or (not self.budget_paused and time.monotonic() >= self.deadline):
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
        terminal = {'unknown', 'stopped', 'release_rejected', 'release_expired'}
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
            graph.destination_views = graph.example_destinations[index]
            row = self.execute(graph, example['inputs'])
            with self.lock:
                if self.reason == runs.USER_STOP and row['outcome'] != 'unknown':
                    row = {**row, 'outcome': 'stopped', 'checks_passed': False, 'outputs': {}}
            correctness = judgement(row['outputs'], example.get('expectation', {'kind': 'review'}),
                                    example.get('verified', False))
            end_batch = next((item['code'] for item in row['issues'] if item['code'] in fatal), None)
            if not end_batch and row['outcome'] in terminal:
                end_batch = ('call_unknown' if row['outcome'] == 'unknown' else
                             'user_stop' if row['outcome'] == 'stopped' else row['outcome'])
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


