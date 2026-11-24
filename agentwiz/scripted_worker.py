"""In-process execution for a reviewed inactive team's scripted test worker."""
from contextlib import ExitStack
from dataclasses import dataclass
import secrets

from . import runs, workflow_evidence
from .workflow_definition import problem
from .workflow_runner import Job
from .workshop import WorkshopError


@dataclass(frozen=True)
class Artifact:
    scope: str
    producer: tuple
    value: dict


class Worker:
    SCOPES = frozenset({'agent', 'team'})

    def __init__(self, workshop, capture, agent_index, *, seconds=120, model_attempts=8, cloud=False):
        root = workshop.components.load(capture)
        if root['component']['kind'] != 'team' or type(agent_index) is not int or not 0 <= agent_index < len(root['children']):
            problem('invalid_worker', 'agent', 'choose an agent in the exact installed team')
        if type(seconds) is not int or not 1 <= seconds <= 1800 or type(model_attempts) is not int or not 0 <= model_attempts <= 64:
            problem('invalid_worker', 'budget', 'invalid test worker budget')
        self.workshop, self.capture, self.agent_index = workshop, capture, agent_index
        self.team_revision = root['revision']
        self.agent = root['children'][agent_index]
        self.cloud = cloud or self.agent['component']['definition']['mode'] == 'cloud'
        self.seconds, self.limit = seconds, model_attempts
        self.job = Job(workshop.state)
        self.artifacts = {}
        self.open = False
        self.children_started = 0

    def __enter__(self):
        state = self.workshop.state
        with ExitStack() as stack:
            if self.open or self.children_started:
                problem('invalid_worker', 'worker', 'create a fresh scripted worker')
            writer = state.register_writer('scripted workflow worker')
            stack.callback(writer.release)
            with self.workshop.lock:
                if self.workshop.active is not None:
                    problem('admission_busy', 'workflow', 'the Workshop slot is busy', 503)
                self.workshop.active = self.job.ident
            stack.callback(self._release)
            self.job.attempt_limit = self.limit
            self.job._start_budget(self.seconds)
            stack.callback(self.job.budget_timer.cancel)
            self.open = True
            self._cleanup = stack.pop_all()
        return self

    def _release(self):
        with self.workshop.lock:
            if self.workshop.active == self.job.ident:
                self.workshop.active = None

    def __exit__(self, *exc):
        self.stop()
        self.open = False
        self.artifacts.clear()
        self._cleanup.close()

    def stop(self):
        self.job.cancel()

    def plan(self, workflow_index, inputs):
        request = self.request(workflow_index, inputs)
        return self.workshop.workflows.plan('workflow_run', request)

    def request(self, workflow_index, inputs):
        if type(workflow_index) is not int or not 0 <= workflow_index < len(self.agent['children']):
            problem('invalid_worker', 'workflow', 'choose a workflow associated with the exact agent')
        name = self.agent['children'][workflow_index]['bundle']['definition']['name']
        return {'name': name, 'capture': self.capture, 'path': [self.agent_index, workflow_index], 'inputs': inputs}

    def execute(self, workflow_index, inputs, review, *, scope):
        if not self.open or scope not in self.SCOPES or self.children_started >= 2:
            problem('invalid_worker', 'worker', 'use the parent reservation for two scoped workflows')
        if self.job.reason is not None:
            return {'outcome': 'stopped' if self.job.reason == runs.USER_STOP else 'budget_exceeded', 'artifacts': {}}
        with ExitStack() as stack:
            graph, current = self.workshop.workflows.prepare_run(self.request(workflow_index, inputs), stack)
            self.workshop.check_plan(current, review)
            blocking = [row for row in current['descriptor']['issues'] if row['severity'] == 'error']
            if blocking:
                raise WorkshopError('preflight_refused', 409, blocking)
            if graph.gates or current['required_confirmations']:
                problem('invalid_worker', 'workflow', 'the scripted worker cannot initiate externally mediated steps')
            graph.select_example(graph.examples[0])
            graph.runtime = graph.example_runtime[0]
            graph.destination_views = graph.example_destinations[0]
            if not self.cloud and any(not item['protected'] for item in graph.destination_views.values()):
                problem('scope_refused', 'workflow', 'a Local only parent cannot authorise Cloud or tool effects')
            if self.cloud and any(binding[0] in graph.restricted for binding in graph.final.values()):
                problem('scope_refused', 'outputs', 'protected child results cannot enter a Cloud parent')
            self.children_started += 1
            self.job.attempt_limit = min(self.limit, self.job.attempts + graph.definition.get('budget', {}).get('max_model_attempts', 32))
            evidence_id = None
            producer = {'worker_run': self.job.ident, 'team_capture': self.capture, 'team_revision': self.team_revision,
                        'agent_revision': self.agent['revision'], 'workflow_index': workflow_index,
                        'workflow_revision': self.agent['children'][workflow_index]['revision'],
                        'executable_digest': graph.digest, 'scope': scope}
            try:
                if graph.saving:
                    evidence_id = secrets.token_hex(16)
                    self.job.evidence = workflow_evidence.Evidence(
                        self.workshop.state, evidence_id, graph.bundle, current['descriptor'], producer=producer)
                result = self.job.execute(graph, inputs, parent_budget=True)
                if self.job.evidence:
                    self.job.evidence.finish(result)
            except (OSError, ValueError, workflow_evidence.EvidenceError):
                problem('evidence_write_failed', 'workflow', 'required worker evidence could not be saved')
            finally:
                self.job.evidence = None
        handles = {}
        for name, value in result.pop('outputs', {}).items():
            if len(self.artifacts) >= 128 or sum(len(item.value['text']) for item in self.artifacts.values()) + len(value['text']) > 4 << 20:
                for key in handles.values():
                    del self.artifacts[key]
                return {**result, 'outcome': 'rejected', 'artifacts': {}, 'issues': [
                    {'code': 'artifact_limit', 'location': 'outputs', 'message': 'worker artifact limit exceeded'}]}
            handle = secrets.token_urlsafe(24)
            self.artifacts[handle] = Artifact(scope, (self.capture, self.agent_index, workflow_index, graph.digest, name), value)
            handles[name] = handle
        return {**result, 'artifacts': handles, **({'evidence_id': evidence_id} if evidence_id else {})}

    def read(self, handle, *, scope):
        artifact = self.artifacts.get(handle) if self.open and isinstance(handle, str) else None
        if artifact is None or artifact.scope != scope:
            problem('artifact_unavailable', 'artifact', 'the handle is stale, unknown or outside its scope')
        return {'producer': artifact.producer, **artifact.value}
