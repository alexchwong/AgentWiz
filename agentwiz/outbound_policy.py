"""Workspace grants and dispatch checks for protected block data."""
from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from urllib.request import getproxies, proxy_bypass

from . import blocks, jsonio
from .workshop import WorkshopError
from .workspace import write_atomic_bytes

FILE = 'destination_trust.json'
LIMIT = 1 << 20
MODEL_KINDS = ('lmstudio', 'openai_compat')


class Refused(Exception):
    pass


def canonical_url(url):
    try:
        parts = urlsplit(url)
        host = ipaddress.ip_address(parts.hostname or '')
        if (not host.is_loopback or parts.scheme not in ('http', 'https')
                or parts.username is not None or parts.password is not None
                or '?' in url or '#' in url or '%' in parts.netloc):
            raise ValueError
        address = f'[{host}]' if host.version == 6 else str(host)
        if parts.port is not None:
            address += f':{parts.port}'
        return urlunsplit((parts.scheme, address, parts.path, '', ''))
    except (ValueError, TypeError, AttributeError):
        raise Refused('destination_ineligible') from None


def identity(destination):
    if not isinstance(destination, dict):
        raise Refused('destination_ineligible')
    kind = destination.get('kind')
    fields = ('server', 'tool') if kind == 'mcp' else ('model', 'provider')
    if (kind not in (*MODEL_KINDS, 'mcp') or set(destination) != {'kind', 'url', *fields}
            or any(not isinstance(destination[key], str) or len(destination[key]) > 4096 for key in fields)
            or not destination[fields[0]] or (kind == 'mcp' and not destination['tool'])):
        raise Refused('destination_ineligible')
    return {**destination, 'url': canonical_url(destination['url'])}


def model_identity(snapshot):
    return identity({'kind': snapshot.kind, 'url': snapshot.endpoint.base_url,
                     'model': snapshot.model, 'provider': snapshot.provider})


def tool_identity(snapshot, tool):
    return identity({'kind': 'mcp', 'url': snapshot.url, 'server': snapshot.server, 'tool': tool})


def decide(destination, grants):
    if destination not in grants:
        raise Refused('destination_untrusted')
    if destination['kind'] != 'mcp':
        parts = urlsplit(destination['url'])
        if getproxies().get(parts.scheme) and not proxy_bypass(parts.netloc):
            raise Refused('destination_proxied')


def read(workspace):
    result = blocks.read_file(workspace, FILE, LIMIT)
    if result.fingerprint == ('missing',):
        return None, []
    if result.data is not None:
        try:
            obj = jsonio.decode_file_exact(result.data)
            grants = obj['grants']
            if (set(obj) != {'format_version', 'grants'} or type(obj['format_version']) is not int
                    or obj['format_version'] != 1 or not isinstance(grants, list) or len(grants) > 128
                    or any(identity(row) != row for row in grants)):
                raise ValueError
            return hashlib.sha256(result.data).hexdigest(), grants
        except (ValueError, KeyError, TypeError, Refused):
            pass
    raise Refused('destination_trust_unavailable')


@dataclass(frozen=True)
class Dispatch:
    state: object
    destination: dict | None
    revision: str | None
    unchanged: object

    @classmethod
    def prepare(cls, state, destination, unchanged):
        revision, grants = read(state.workspace.path)
        decide(destination, grants)
        return cls(state, destination, revision, unchanged)

    def check(self):
        if not self.unchanged():
            raise Refused('destination_changed')
        if self.destination is None:
            return
        revision, grants = read(self.state.workspace.path)
        if revision != self.revision:
            raise Refused('destination_changed')
        decide(self.destination, grants)


def view(state):
    try:
        revision, grants = read(state.workspace.path)
    except Refused as exc:
        raise WorkshopError(str(exc), 409) from None
    return {'format_version': 1, 'revision': revision, 'grants': grants}


def locality(url):
    """`local` for a loopback IP or `localhost`, else `remote`. No DNS or network access."""
    try:
        host = urlsplit(url).hostname or ''
    except ValueError:
        return 'remote'
    if host == 'localhost':
        return 'local'
    try:
        return 'local' if ipaddress.ip_address(host).is_loopback else 'remote'
    except ValueError:
        return 'remote'


def ineligibility(destination):
    """Why `destination` cannot receive restricted values, or None when it can (trust aside):
    `provider` (a kind restricted dispatch never uses), `hostname` (a name such as localhost instead of a
    literal loopback IP), `remote`, `address` (another malformed or unsupported URL) or `proxied`."""
    if not isinstance(destination, dict) or destination.get('kind') not in (*MODEL_KINDS, 'mcp'):
        return 'provider'
    try:
        parts = urlsplit(destination.get('url') or '')
        host = parts.hostname or ''
    except (ValueError, TypeError):
        return 'address'
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is None:
        return 'hostname' if host == 'localhost' or host.endswith('.localhost') else 'remote'
    if not literal.is_loopback:
        return 'remote'
    try:
        exact = identity(destination)
        decide(exact, [exact])
    except Refused as exc:
        return 'proxied' if str(exc) == 'destination_proxied' else 'address'
    return None


def facts(state, destination, grants=None):
    """Locality, eligibility (with reason) and trust of one destination, reported separately. `grants`, when
    given, saves rereading the trust file for each destination."""
    reason = ineligibility(destination)
    try:
        exact = identity(destination)
        trusted = exact in (read(state.workspace.path)[1] if grants is None else grants)
    except Refused:
        trusted = False
    return {'locality': locality(destination.get('url') or ''), 'eligible': reason is None,
            'reason': reason, 'trusted': trusted}


def fact_view(state, destination, grants=None):
    """The §3.4 `facts` shape: available with the destination, or unavailable with a reason."""
    if destination is None:
        return {'available': False, 'reason': 'unresolved'}
    return {'available': True, 'destination': destination, **facts(state, destination, grants)}


REASON_ACTIONS = {'hostname': 'fix_address', 'address': 'fix_address', 'proxied': 'fix_proxy',
                  'remote': 'choose_local_role', 'provider': 'choose_local_role'}
CODE_ACTIONS = {'destination_changed': 'refresh_readiness', 'destination_trust_unavailable': 'repair_trust_settings',
                'protected_host_script': 'explain', 'role_unresolved': 'configure_role',
                'server_missing': 'configure_server'}


def remedy(code, fact=None, *, target=None, upstream_only=False):
    """The action that would resolve a protected refusal or readiness issue (core v3 §3.4), or None.
    `upstream_only` (workflow readiness only) means the node is protected solely by upstream restriction, so a
    human release could legitimately expose its value: a remote destination is then offered a Release Gate."""
    if code in CODE_ACTIONS:
        action = CODE_ACTIONS[code]
        return {'action': action, **({'target': target} if target is not None else {})}
    if fact is None or not fact.get('available'):
        return None
    if fact['eligible'] and not fact['trusted']:
        return {'action': 'grant', 'target': fact['destination']}
    action = REASON_ACTIONS.get(fact['reason'])
    if action == 'choose_local_role' and upstream_only:
        action = 'add_release_gate'
    if action is None:
        return None
    return {'action': action, **({'target': target} if target is not None else {})}


def config_destination(config, step):
    """The destination a block step would use under configuration only (no credentials), or None with the
    reason it cannot be resolved: `role_unresolved` or `server_missing`."""
    if step.kind == 'model':
        role = config['roles'].get(step.role)
        if role is None or 'unresolved' in role:
            return None, 'role_unresolved'
        return dict(role), None
    server = config['servers'].get(step.server)
    if server is None:
        return None, 'server_missing'
    return {'kind': 'mcp', 'url': server['url'], 'server': step.server, 'tool': step.tool}, None


USED_BY_LIMIT = 200
WORKFLOW_LIMIT = 50
BLOCK_LIMIT = 256


def saved_uses(state):
    """Configured and saved uses of every destination (core v3 §3.3), from configuration only: roles, blocks'
    current default revisions and saved workflows, with `requires_trust` when a saved workflow would dispatch
    to it from a protected node under its base restrictions or any one saved example's. No traffic, no
    credentials; each workflow's captures are released before the next is opened."""
    from contextlib import ExitStack
    config = state.destination_config()
    rows, scan = {}, {'complete': True, 'workflows': 0, 'workflows_skipped': 0, 'reasons': []}

    def add(destination, use, requires):
        key = row_key(destination)
        row = rows.setdefault(key, {'destination': destination, 'used_by': [], 'requires_trust': False})
        row['requires_trust'] = row['requires_trust'] or requires
        if use not in row['used_by']:
            row['used_by'].append(use)

    for role, destination in config['roles'].items():
        if 'unresolved' not in destination:
            add(dict(destination), {'kind': 'role', 'name': role}, False)
    listing = state.block_listing()
    if listing['total'] > len(listing['blocks']):
        scan['complete'] = False
        scan['reasons'].append({'kind': 'blocks', 'reason': 'limit'})
    for row in listing['blocks'][:BLOCK_LIMIT]:
        loaded = state.block_loaded(row.get('name'))
        block = loaded.block if loaded is not None else None
        if block is None or block.step.kind not in ('model', 'tool'):
            continue
        destination, _ = config_destination(config, block.step)
        if destination is not None:
            add(destination, {'kind': 'block', 'name': loaded.name}, False)
    workflows = state.workshop.workflows
    try:
        names = workflows.listing()['workflows']
    except WorkshopError as exc:
        names = []
        scan['complete'] = False
        scan['reasons'].append({'kind': 'workflows', 'reason': exc.code})
    if len(names) > WORKFLOW_LIMIT:
        scan['complete'] = False
        scan['reasons'].extend({'kind': 'workflow', 'name': name, 'reason': 'limit'} for name in names[WORKFLOW_LIMIT:])
    for name in names[:WORKFLOW_LIMIT]:
        try:
            with ExitStack() as stack:
                graph = workflows.open_saved(name, stack).graph
                protected = set()
                for example in [None, *graph.bundle['examples'][:20]]:
                    graph.select_example(example or {})
                    protected |= graph.protected_nodes
                for node in graph.order:
                    block, is_protected = graph.blocks[node], node in protected
                    if node in graph.gates:
                        opened = graph.assessments[node]
                        if opened is None:
                            continue
                        block, is_protected = opened.parsed.block, True
                    if block.step.kind not in ('model', 'tool'):
                        continue
                    destination, _ = config_destination(config, block.step)
                    if destination is not None:
                        add(destination, {'kind': 'workflow', 'name': name}, is_protected)
            scan['workflows'] += 1
        except WorkshopError as exc:
            scan['complete'] = False
            scan['workflows_skipped'] += 1
            scan['reasons'].append({'kind': 'workflow', 'name': name, 'reason': exc.code})
    return {'rows': list(rows.values()), 'scan': scan}


def row_key(destination):
    reason = ineligibility(destination)
    try:
        exact = identity(destination) if reason in (None, 'proxied') else None
    except Refused:
        exact = None
    return jsonio.encode_exact(exact if exact is not None else destination)


def candidates(state):
    """Every destination reachable from current configuration and saved uses (core_02 §3.2; core v3 §3.3): one
    row per resolved model role, per observed MCP tool and per destination a block or saved workflow binds, with
    locality, eligibility, trust, uses and `requires_trust`. `trust_gaps` are eligible, untrusted destinations a
    saved protected workflow requires; `scan` says whether the saved-use inventory is complete."""
    from .state import NoSuchServer, RoleUnresolved
    current = view(state)
    revision, grants = current['revision'], current['grants']
    rows = {}

    def add(destination, use, *, observed=None, requires=False):
        reason = ineligibility(destination)
        try:
            exact = identity(destination) if reason in (None, 'proxied') else None
        except Refused:
            exact = None
        key = jsonio.encode_exact(exact if exact is not None else destination)
        row = rows.setdefault(key, {'destination': exact or destination, 'exact': exact is not None,
                                    'locality': locality(destination.get('url') or ''),
                                    'eligible': reason is None, 'reason': reason,
                                    'trusted': exact is not None and exact in grants, 'used_by': [],
                                    'requires_trust': False})
        if destination.get('kind') == 'mcp':
            # An authenticated observation wins over the credential-free inventory's `unknown`.
            if observed is True or 'observed' not in row:
                row['observed'] = observed if observed is not None else 'unknown'
        row['requires_trust'] = row['requires_trust'] or requires
        if use not in row['used_by']:
            row['used_by'].append(use)
        return row

    snapshot_view = state.view()
    for role in snapshot_view['roles']['map']:
        try:
            snapshot = state.role_capture(role).snapshot
        except RoleUnresolved:
            continue
        add({'kind': snapshot.kind, 'url': snapshot.endpoint.base_url, 'model': snapshot.model,
             'provider': snapshot.provider or ''}, {'kind': 'role', 'name': role})
    for server in snapshot_view['mcp']['servers']:
        try:
            snapshot = state.mcp_snapshot_for_block(server)
        except NoSuchServer:
            continue
        for tool in state.mcp_client.observed_tools(snapshot.identity):
            add({'kind': 'mcp', 'url': snapshot.url, 'server': server, 'tool': tool},
                {'kind': 'server', 'name': server}, observed=True)
    inventory = saved_uses(state)
    for saved in inventory['rows']:
        row = None
        for use in saved['used_by']:
            row = add(saved['destination'], use, requires=saved['requires_trust'])
        if row is not None:
            row['requires_trust'] = row['requires_trust'] or saved['requires_trust']
    for row in rows.values():
        if len(row['used_by']) > USED_BY_LIMIT:
            row['used_by'] = row['used_by'][:USED_BY_LIMIT]
            row['used_by_truncated'] = True
        fact = {'available': True, 'destination': row['destination'], 'locality': row['locality'],
                'eligible': row['eligible'], 'reason': row['reason'], 'trusted': row['trusted']}
        action = remedy(None, fact) if not row['trusted'] else None
        # Choosing a local role is only advice for a destination a protected use needs.
        if action is not None and action['action'] == 'choose_local_role' and not row['requires_trust']:
            action = None
        row['remedy'] = action
    gaps = [row for row in rows.values() if row['eligible'] and not row['trusted'] and row['requires_trust']]
    unused = [grant for grant in grants if jsonio.encode_exact(grant) not in rows]
    return {'format_version': 1, 'revision': revision, 'candidates': list(rows.values()), 'unused_grants': unused,
            'trust_gaps': gaps, 'scan': inventory['scan']}


def preview(state, body):
    if set(body) != {'destination'}:
        raise WorkshopError('invalid', 400)
    grants_view = view(state)
    revision, grants = grants_view['revision'], grants_view['grants']
    destination = None
    reason = None
    try:
        destination = identity(body['destination'])
        decide(destination, [destination])
        if destination not in grants:
            reason = 'destination_untrusted'
    except Refused as exc:
        reason = str(exc)
    detail = ineligibility(body['destination']) if reason not in (None, 'destination_untrusted') else None
    return {'revision': revision, 'destination': destination, 'reason': reason, 'detail': detail,
            'locality': locality(body['destination'].get('url') or '') if isinstance(body['destination'], dict) else 'remote',
            'trusted': destination is not None and destination in grants}


def update(state, body):
    if (set(body) != {'expected_revision', 'destination', 'trusted'}
            or type(body['trusted']) is not bool):
        raise WorkshopError('invalid', 400)
    if state.workspace.read_only:
        raise WorkshopError('read_only', 409)
    writer = state.register_writer('destination trust')
    try:
        with state.workshop.assets.reserve('destination_trust', 'settings'):
            revision, grants = read(state.workspace.path)
            if revision != body['expected_revision']:
                raise WorkshopError('stale', 409)
            destination = identity(body['destination'])
            updated = [grant for grant in grants if grant != destination]
            if body['trusted']:
                updated.append(destination)
            if len(updated) > 128:
                raise WorkshopError('too_large', 400)
            if updated != grants:
                write_atomic_bytes(state.workspace.path, FILE,
                                   jsonio.encode_file_exact({'format_version': 1, 'grants': updated}))
            return view(state)
    except Refused as exc:
        raise WorkshopError(str(exc), 409) from None
    finally:
        writer.release()


def credential(endpoint):
    import os
    return ((endpoint.api_key or '').strip() or
            (os.environ.get(endpoint.api_key_env, '').strip() if endpoint.api_key_env else '') or None)


def block_dispatch(state, block, snapshot, *, protected=True, defaults=None):
    if block.step.kind == 'model':
        key = credential(snapshot.endpoint)

        def unchanged():
            from dataclasses import replace
            from .state import RoleUnresolved
            try:
                captured = state.role_capture(block.step.role)
                live = captured.snapshot
            except RoleUnresolved:
                return False
            return (replace(live, endpoint=replace(live.endpoint, api_key=None, api_key_env=None))
                    == replace(snapshot, endpoint=replace(snapshot.endpoint, api_key=None, api_key_env=None))
                    and credential(live.endpoint) == key
                    and (defaults is None or captured.defaults == defaults))

        destination = model_identity(snapshot) if protected else None
    else:
        def unchanged():
            from .state import NoSuchServer
            try:
                return state.mcp_snapshot_for_block(block.step.server) == snapshot
            except NoSuchServer:
                return False

        destination = tool_identity(snapshot, block.step.tool) if protected else None
    dispatch = Dispatch.prepare(state, destination, unchanged) if protected else Dispatch(state, None, None, unchanged)
    dispatch.check()
    return dispatch
