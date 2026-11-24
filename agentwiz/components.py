"""Reviewed inert plugin imports and immutable native workflow captures."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import fcntl
import copy
import os
from pathlib import Path
import re

from . import blocks, jsonio, private_templates, private_transform, workshop_assets as assets
from .workflow_definition import digest, names, problem, record
from .workflow_runner import Graph
from .workspace import write_atomic_bytes

KINDS = ('team', 'agent', 'workflow', 'block')
CATEGORIES = ('research', 'extraction', 'analysis', 'writing', 'verification', 'privacy', 'general')
ID = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}/[a-z0-9][a-z0-9_-]{0,63}\Z')
REVISION = re.compile('[0-9a-f]{64}\Z')
LIMIT = 2 << 20
CAPTURE_LIMIT = 32 << 20


def require(condition, location, message):
    if not condition:
        problem('invalid_component', location, message, 400)


def depth(value, remaining=64):
    require(remaining >= 0, 'package', 'package nesting exceeds its limit')
    if isinstance(value, dict):
        for child in value.values():
            depth(child, remaining - 1)
    elif isinstance(value, list):
        for child in value:
            depth(child, remaining - 1)


def decode(text):
    require(isinstance(text, str), 'package', 'provide a UTF-8 JSON package')
    try:
        require(len(text.encode('utf-8')) <= LIMIT, 'package', 'package exceeds 2 MiB')
        package = jsonio.decode_exact(text)
        depth(package)
        return package
    except (UnicodeError, ValueError, RecursionError):
        problem('invalid_component', 'package', 'package is not bounded exact JSON', 400)


def declaration(value, kind=None):
    record(value, ('kind', 'id', 'title', 'visibility', 'category', 'definition'), ('description', 'tags'), 'component')
    require(value['kind'] in KINDS and (kind is None or value['kind'] == kind), 'kind', 'unexpected component kind')
    ident = value['id']
    require(isinstance(ident, str) and ID.fullmatch(ident) and not ident.startswith('agentwiz/'),
            'id', 'expected a non-reserved namespace and slug')
    require(value['visibility'] in ('public', 'private'), 'visibility', 'choose public or private')
    require(value['category'] in CATEGORIES, 'category', 'unknown primary category')
    require(isinstance(value['title'], str) and 0 < len(value['title']) <= 256, 'title', 'expected a short title')
    require(isinstance(value.get('description', ''), str) and len(value.get('description', '')) <= 4096,
            'description', 'expected a bounded description')
    tags = value.get('tags', [])
    require(isinstance(tags, list) and len(tags) <= 12 and all(isinstance(t, str) and 0 < len(t) <= 64 for t in tags),
            'tags', 'expected at most twelve short tags')
    require(isinstance(value['definition'], dict), 'definition', 'expected an object')
    return value


def revision(value):
    def normalize(part):
        if isinstance(part, list):
            return [normalize(item) for item in part]
        if not isinstance(part, dict):
            return part
        if set(part) == {'inline'}:
            return {'inline': {k: normalize(part['inline'][k]) for k in ('kind', 'id', 'definition')}}
        return {k: normalize(v) for k, v in part.items()}
    definition = dict(value['definition'])
    if value['kind'] == 'workflow':
        for key, default in (('layout', {}), ('examples', [])):
            definition.setdefault(key, default)
    elif value['kind'] == 'block':
        definition.setdefault('files', {})
    return digest({'kind': value['kind'], 'id': value['id'], 'definition': normalize(definition)})


def identity(value):
    return (value['kind'], value['id'], revision(value))


class Compiler:
    def __init__(self, package, pool):
        record(package, ('format', 'format_version', 'root'), ('dependencies',), 'package')
        require(package['format'] == 'agentwiz.component' and type(package['format_version']) is int
                and package['format_version'] == 1, 'format', 'expected agentwiz.component version 1')
        self.dependencies, self.used = {}, set()
        self.pool = pool
        dependencies = package.get('dependencies', [])
        require(isinstance(dependencies, list) and len(dependencies) <= 88, 'dependencies', 'too many definitions')
        for row in dependencies:
            item = declaration(row)
            ident = identity(item)
            require(item['visibility'] == 'public' and ident not in self.dependencies,
                    'dependencies', 'expected unique public definitions')
            self.dependencies[ident] = item
        self.counts = {k: set() for k in KINDS}
        self.root = declaration(package['root'])

    def resolve(self, expr, kind):
        if isinstance(expr, dict) and set(expr) == {'inline'}:
            return declaration(expr['inline'], kind)
        record(expr, ('ref',), (), 'child')
        ref = record(expr['ref'], ('id', 'kind', 'revision'), (), 'ref')
        require(ref['kind'] == kind and isinstance(ref['id'], str) and ID.fullmatch(ref['id'])
                and isinstance(ref['revision'], str) and REVISION.fullmatch(ref['revision']), 'ref', 'invalid exact reference')
        ident = (kind, ref['id'], ref['revision'])
        item = self.dependencies.get(ident) or self.pool.get(ident)
        if item is None and kind == 'block' and ref['id'].startswith('agentwiz/'):
            from . import library_install
            key = ref['id'].split('/')[1]
            if key in library_install.index():
                entry = library_install.entry(key)
                step = entry.definition.get('steps', [{}])[0]
                if entry.definition.get('format_version') == 4 and step.get('kind') in ('model', 'tool', 'transform'):
                    builtin = {'kind': 'block', 'id': ref['id'], 'title': key, 'visibility': 'public',
                               'category': 'general', 'definition': {'block': entry.definition, 'files': entry.files}}
                    if revision(builtin) == ref['revision']:
                        item = builtin
        if item is None:
            problem('missing_dependency', ref['id'], 'supply the exact referenced definition or review a replacement')
        self.used.add(ident)
        return item

    def compile(self, value):
        kind, definition = value['kind'], value['definition']
        self.counts[kind].add(identity(value))
        limit = {'team': 1, 'agent': 8, 'workflow': 16, 'block': 64}[kind]
        require(len(self.counts[kind]) <= limit, kind, 'too many component revisions')
        result = {'component': value, 'revision': revision(value)}
        if kind == 'team':
            record(definition, ('purpose', 'agents'), ('lead_agent_id',), 'team')
            require(isinstance(definition['purpose'], str), 'team.purpose', 'expected text')
            children = definition['agents']
            require(isinstance(children, list) and 1 <= len(children) <= 8, 'agents', 'expected one to eight agents')
            result['children'] = [self.compile(self.resolve(row, 'agent')) for row in children]
            ids = [child['component']['id'] for child in result['children']]
            require(len(set(ids)) == len(ids) and definition.get('lead_agent_id', ids[0]) in ids,
                    'agents', 'duplicate agents or invalid lead')
        elif kind == 'agent':
            record(definition, ('instructions', 'mode', 'inputs', 'outputs', 'capability_requests', 'artifact_requests', 'workflows'),
                   ('role',), 'agent')
            require(isinstance(definition['instructions'], str) and 0 < len(definition['instructions']) <= 128 << 10
                    and definition['mode'] in ('cloud', 'local_only'), 'agent', 'invalid instructions or mode')
            for side in ('inputs', 'outputs'):
                for port in names(definition[side], 0, 50, side).values():
                    from .checks import parse_contract
                    record(port, ('contract', 'local_only'), ('description',), side)
                    require(type(port['local_only']) is bool, side, 'expected explicit privacy mark')
                    parse_contract(port['contract'])
            for key in ('capability_requests', 'artifact_requests'):
                require(isinstance(definition[key], list) and len(definition[key]) <= 64,
                        key, 'expected a bounded request list')
            children = definition['workflows']
            require(isinstance(children, list) and 1 <= len(children) <= 16, 'workflows', 'expected one to sixteen workflows')
            result['children'] = [self.compile(self.resolve(row, 'workflow')) for row in children]
        elif kind == 'workflow':
            result['bundle'], result['children'] = self.workflow(definition)
        else:
            result['snapshot'] = self.block(definition)
        return result

    def workflow(self, definition):
        record(definition, ('workflow', 'blocks'), ('examples', 'layout'), 'workflow')
        workflow = dict(definition['workflow'])
        aliases = names(definition['blocks'], 0, 64, 'blocks')
        nodes, snapshots, children, used = {}, {}, [], set()
        for key, value in names(workflow.get('nodes'), 0, 64, 'nodes').items():
            record(value, ('block', 'bindings'), (), 'node')
            alias = value['block']
            require(isinstance(alias, str) and alias in aliases, f'nodes.{key}', 'unknown block alias')
            child = self.compile(self.resolve(aliases[alias], 'block'))
            capture = child['snapshot']
            ident = digest(capture)
            snapshots[ident] = capture
            nodes[key] = {'snapshot_id': ident, 'bindings': value['bindings']}
            if alias not in used:
                children.append(child)
            used.add(alias)
        require(used == set(aliases), 'blocks', 'unreferenced block alias')
        examples = [{**row, 'verified': False, 'origin': 'user'} for row in definition.get('examples', [])]
        bundle = {'format_version': 1, 'definition': {**workflow, 'nodes': nodes},
                  'snapshots': snapshots, 'layout': definition.get('layout', {}), 'examples': examples}
        with ExitStack() as stack:
            graph = Graph(bundle, workflow.get('name'), stack)
            if graph.issues:
                problem(graph.issues[0]['code'], graph.issues[0]['location'], graph.issues[0]['message'])
        return bundle, children

    def block(self, definition):
        record(definition, ('block',), ('files',), 'block')
        block = definition['block']
        require(isinstance(block, dict) and block.get('format_version') == 4, 'block', 'expected a v4 block')
        steps = block.get('steps')
        require(isinstance(steps, list) and len(steps) == 1 and isinstance(steps[0], dict), 'steps', 'expected one step')
        step = steps[0]
        allowed = (step.get('kind') == 'model' or
                   (step.get('kind') == 'transform' and step.get('operation') not in private_transform.OPERATIONS) or
                   (step.get('kind') == 'tool' and 'builtin' in step))
        require(allowed, 'steps', 'portable blocks are limited to model, portable transforms and built-in tools')
        require(isinstance(block.get('name'), str) and blocks.IDENT.fullmatch(block['name']), 'block.name', 'invalid block name')
        files = definition.get('files', {})
        require(isinstance(files, dict) and all(isinstance(text, str) for text in files.values()),
                'files', 'bundled prompt assets must be literal text')
        return private_templates.snapshot(block, assets.decode_files(files))

    def run(self):
        compiled = self.compile(self.root)
        require(set(self.dependencies) <= self.used, 'dependencies', 'unreferenced bundled definition')
        return compiled


@contextmanager
def locked(folder):
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if folder.is_symlink():
        problem('unsafe_entry', 'components', 'a symbolic link blocks publication')
    fd = os.open(folder / '.lock', os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'rb') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def tree(capture):
    yield capture
    for child in capture.get('children', []):
        yield from tree(child)


def shareable(capture):
    for row in tree(capture):
        definition = row['component']['definition']
        if definition.get('block', {}).get('context_local_only') or row.get('protected'):
            problem('protected_component', row['component']['id'], 'the definition is protected')


class Components:
    def __init__(self, workshop):
        self.workshop = workshop

    @property
    def root(self):
        root = self.workshop.state.workspace.path / 'components'
        if root.is_symlink():
            problem('unsafe_entry', 'components', 'a symbolic link blocks workspace component access')
        return root

    @property
    def pool(self):
        return getattr(self.workshop.state, 'component_pool', Path('./component_pool'))

    @staticmethod
    def records(folder):
        if folder.is_symlink():
            problem('unsafe_entry', 'components', 'a symbolic link blocks reading')
        if not folder.is_dir():
            return []
        return [jsonio.decode_file_exact(entry.read_bytes()) for entry in sorted(folder.glob('*.json'))[:512]
                if not entry.is_symlink() and entry.is_file() and entry.stat().st_size <= CAPTURE_LIMIT]

    def pool_index(self):
        result = {}
        for row in self.records(self.pool):
            value = row['component']
            if value['visibility'] == 'public':
                result[identity(value)] = value
            for dependency in row.get('dependencies', []):
                result[identity(dependency)] = dependency
        return result

    def listing(self):
        def view(row):
            value = row['component']
            return {k: value[k] for k in ('kind', 'id', 'title', 'category', 'visibility')} | {
                'revision': row['revision'], 'capture': row.get('capture'), 'workflow_paths': [
                    {'path': path, 'title': item['component']['title']}
                    for path, item in self.workflows(row) if 'bundle' in item]}
        return {'workspace': [view(row) for kind in KINDS for row in self.records(self.root / kind)],
                'pool': [view(row) for row in self.records(self.pool)]}

    def workflows(self, row, path=()):
        yield list(path), row
        for i, child in enumerate(row.get('children', [])):
            yield from self.workflows(child, (*path, i))

    def load(self, ident):
        require(isinstance(ident, str) and REVISION.fullmatch(ident), 'capture', 'invalid installation identity')
        for kind in KINDS:
            folder = self.root / kind
            file = folder / (ident + '.json')
            if not folder.is_symlink() and not file.is_symlink() and file.is_file() and file.stat().st_size <= CAPTURE_LIMIT:
                row = jsonio.decode_file_exact(file.read_bytes())
                if digest({k: v for k, v in row.items() if k != 'capture'}) == ident:
                    return row
        problem('missing', 'capture', 'installation is unavailable', 404)

    def plan(self, request):
        record(request, ('source',), (), 'import')
        package = decode(request['source'])
        self.workshop.reject_credentials(package)
        compiler = Compiler(package, self.pool_index())
        row = compiler.run()
        for _, item in self.workflows(row):
            if 'bundle' not in item:
                continue
            bundle = item['bundle']
            with ExitStack() as stack:
                graph = Graph(bundle, bundle['definition']['name'], stack)
                destinations, issues, _ = graph.destinations(self.workshop.state, self.workshop)
                item['setup'] = issues
                item['destinations'] = destinations
                self.workshop.workflows.update_examples(graph, bundle['examples'], [])
        row['capture'] = digest(row)
        require(len(jsonio.encode_file_exact(row)) <= CAPTURE_LIMIT, 'capture', 'capture exceeds 32 MiB')
        view = {'id': row['component']['id'], 'kind': row['component']['kind'], 'revision': row['revision'],
                'capture': row['capture'], 'active': False,
                'components': [{'id': part['component']['id'], 'revision': part['revision'],
                                'setup': part.get('setup', []), 'destinations': part.get('destinations', {})}
                               for part in tree(row)]}
        return self.workshop.workflows.plan_view('component_install', request, view, 'component_install'), row

    def install(self, body):
        with self.workshop.state.register_writer('component import'):
            if self.workshop.state.workspace.read_only:
                problem('read_only', 'workspace', 'the workspace is read-only')
            plan, row = self.plan(body['request'])
            self.workshop.check_plan(plan, body)
            folder = self.root / row['component']['kind']
            if self.root.is_symlink():
                problem('unsafe_entry', 'components', 'a symbolic link blocks publication')
            with locked(folder):
                name = row['capture'] + '.json'
                file = folder / name
                if file.is_symlink():
                    problem('unsafe_entry', 'components', 'a symbolic link blocks publication')
                if not file.exists():
                    write_atomic_bytes(folder, name, jsonio.encode_file_exact(row))
                    assets.sync_directory(folder)
        return plan['descriptor']

    def fork_block(self, body):
        record(body, ('request',), (), 'fork')
        request = record(body['request'], ('capture', 'target'), (), 'request')
        row = self.load(request['capture'])
        require(row['component']['kind'] == 'block', 'capture', 'choose an installed standalone block')
        definition = row['component']['definition']
        target = assets.key_name(request['target'])
        require(self.workshop.assets.identity(target) is None, 'target', 'choose an unused block name')
        draft = {'key': target, 'definition_text': jsonio.encode_exact({**definition['block'], 'name': target}),
                 'files': definition.get('files', {}), 'metadata': {'format_version': 1, 'origin': 'manual'}}
        if definition['block'].get('context_local_only'):
            draft['metadata']['protection_origin'] = 'inherited'
        imported = self.workshop.import_draft({'draft': draft})['draft']
        reviewed = {'draft': imported, 'expected_identity': None}
        plan = self.workshop.plan('save', reviewed)
        return {'source': row['component']['id'], 'revision': row['revision'], 'target': target,
                'visibility': 'private', 'save_request': reviewed, 'save_plan': plan}

    def bundle(self, ident, path):
        require(isinstance(path, list) and len(path) <= 3 and all(type(i) is int and i >= 0 for i in path),
                'path', 'invalid child path')
        row = self.load(ident)
        for i in path:
            children = row.get('children', [])
            if i >= len(children):
                problem('missing', 'path', 'child workflow is unavailable', 404)
            row = children[i]
        if 'bundle' not in row:
            problem('invalid_component', 'path', 'choose a workflow')
        return row['bundle']

    def share(self, body):
        record(body, ('capture', 'action'), ('expected_plan', 'confirmations', 'include_examples'), 'share')
        require(body['action'] in ('promote', 'export'), 'action', 'choose promotion or export')
        row = self.load(body['capture'])
        shareable(row)
        value = row['component']
        if body['action'] == 'promote':
            require(value['visibility'] == 'public', 'visibility', 'only public entries can enter the pool')
        all_parts = {identity(part['component']): part['component'] for part in tree(row)}
        dependencies, omissions = {}, []
        selected = body.get('include_examples', [])
        require(isinstance(selected, list) and len(selected) <= 20
                and all(isinstance(key, str) for key in selected) and len(set(selected)) == len(selected),
                'include_examples', 'provide exact component/example IDs')
        used_examples = set()

        def portable(original):
            item = copy.deepcopy(original)
            definition = item['definition']
            if item['id'].startswith('agentwiz/'):
                item['id'] = 'agentwiz-bundled/' + item['id'].split('/')[1]
            if item['kind'] == 'workflow':
                kept = []
                for example in definition.get('examples', []):
                    key = original['id'] + '/' + example['id']
                    if key not in selected:
                        omissions.append(key)
                        continue
                    if example.get('restrictions') or example.get('held_out'):
                        problem('protected_example', key, 'omit the restricted or held-out example')
                    used_examples.add(key)
                    kept.append(example)
                definition['examples'] = kept
            collection = {'team': 'agents', 'agent': 'workflows', 'workflow': 'blocks'}.get(item['kind'])
            if collection:
                children = definition[collection]
                for key in children.keys() if isinstance(children, dict) else range(len(children)):
                    expr = children[key]
                    if 'inline' in expr:
                        expr['inline'] = portable(expr['inline'])
                        continue
                    ref = expr['ref']
                    ident = (ref['kind'], ref['id'], ref['revision'])
                    if ident not in all_parts:
                        problem('missing_dependency', ref['id'], 'a pinned dependency is unavailable')
                    compatible = portable(all_parts[ident])
                    dependencies[identity(compatible)] = compatible
                    ref.update(id=compatible['id'], revision=revision(compatible))
            return item

        packaged_root = portable(value)
        require(used_examples == set(selected), 'include_examples', 'selected example was not found')
        package = {'format': 'agentwiz.component', 'format_version': 1, 'root': packaged_root,
                   'dependencies': [dependencies[key] for key in sorted(dependencies)]}
        Compiler(package, {}).run()
        self.workshop.reject_credentials(package)
        view = {'package': package, 'capture': row['capture'], 'omitted_examples': sorted(set(omissions))}
        plan = self.workshop.workflows.plan_view('component_' + body['action'],
                {'capture': body['capture'], 'include_examples': selected}, view, 'component_' + body['action'])
        if 'expected_plan' not in body:
            return plan
        self.workshop.check_plan(plan, body)
        if body['action'] == 'promote':
            with self.workshop.state.register_writer('component promotion'), locked(self.pool):
                published = Compiler(package, {}).run()
                published['dependencies'] = package['dependencies']
                name = digest(published) + '.json'
                data = jsonio.encode_file_exact(published)
                target = self.pool / name
                if target.is_symlink() or target.exists() and target.read_bytes() != data:
                    problem('component_collision', packaged_root['id'], 'publication identity is already in use')
                if not target.exists():
                    write_atomic_bytes(self.pool, name, data)
                assets.sync_directory(self.pool)
        return view
