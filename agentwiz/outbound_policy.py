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


def facts(state, destination):
    """Locality, eligibility (with reason) and trust of one destination, reported separately."""
    reason = ineligibility(destination)
    try:
        exact = identity(destination)
        trusted = exact in read(state.workspace.path)[1]
    except Refused:
        trusted = False
    return {'locality': locality(destination.get('url') or ''), 'eligible': reason is None,
            'reason': reason, 'trusted': trusted}


def candidates(state):
    """Every destination reachable from current configuration (core_02 §3.2, reduced): one row per
    resolved model role and per observed MCP tool, with locality, eligibility and trust. Uses are the
    roles and servers that reach it; the page adds the open block or workflow."""
    from .state import NoSuchServer, RoleUnresolved
    current = view(state)
    revision, grants = current['revision'], current['grants']
    rows = {}

    def add(destination, use):
        reason = ineligibility(destination)
        try:
            exact = identity(destination) if reason in (None, 'proxied') else None
        except Refused:
            exact = None
        key = jsonio.encode_exact(exact if exact is not None else destination)
        row = rows.setdefault(key, {'destination': exact or destination, 'exact': exact is not None,
                                    'locality': locality(destination.get('url') or ''),
                                    'eligible': reason is None, 'reason': reason,
                                    'trusted': exact is not None and exact in grants, 'used_by': []})
        if use not in row['used_by']:
            row['used_by'].append(use)

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
                {'kind': 'server', 'name': server})
    unused = [grant for grant in grants if jsonio.encode_exact(grant) not in rows]
    return {'format_version': 1, 'revision': revision, 'candidates': list(rows.values()), 'unused_grants': unused}


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
