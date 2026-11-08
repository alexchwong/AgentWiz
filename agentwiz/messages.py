"""Scoped explanation catalogue for privacy refusals, readiness issues and restriction causes (core v3 §4.3).

`explain(item)` accepts the item shapes producers already emit, unchanged: Workshop restriction sources and
refusal details (`input`, `example`, `revision_number`, no `location`), workflow issues (`location`), the
workflow explanation projection's value and node causes, and remedy objects (`action`). It never fails: any
code outside `CODES` gets a generic explanation. Templates name fields with `{field}`; a missing field renders
as an empty mention. The page fetches `catalogue()` and fills the same templates with `textContent`.
Message text is not part of any contract and is not asserted by tests.
"""
from __future__ import annotations

import string

# code: (title, body, remedy label)
CATALOGUE = {
    # restriction and explanation causes (§4.1, §4.2)
    'declared_label': ('Local-only label', '{where} is labelled Local only, so this value must stay on this computer.', ''),
    'example_restriction': ('Example restriction', 'The example {example} marks input {input} as private.', ''),
    'retained_label': ('Earlier revision label', 'Revision {revision_number} labelled {input} Local only; review retained labels to retire it.', ''),
    'context_manual': ('Design protection', 'Design protection is on for {node}: everything it sends stays local.', ''),
    'context_inherited': ('Inherited protection', 'Design protection on {node} is inherited from protected assistance.', ''),
    'context_clear_pending': ('Protection change pending', 'Design protection was cleared but not yet saved.', ''),
    'workflow_io': ('Workflow port label', 'The workflow {side} port {port} is labelled Local only.', ''),
    'consumer_label': ('Shared value labelled elsewhere', 'Another port receiving this value, {consumer}, is Local only, so every receiver of it is restricted.', ''),
    'upstream': ('Produced from private data', 'This value comes from a protected step; the chain leads back to {chain_end}.', ''),
    'released': ('Released by review', 'Release Gate {gate} exposes this value after a human decision.', ''),
    'restricted_input': ('Receives a restricted value', 'Input {input} receives a restricted value, so this step may only send to trusted local destinations.', ''),
    'release_assessment': ('Local release assessment', 'The Release Gate assessment always runs as a protected local step.', ''),
    # refusal and readiness codes (§3.4 paths)
    'destination_untrusted': ('Destination not trusted', 'This local destination has not been granted trust for private data.', ''),
    'destination_ineligible': ('Destination cannot receive private data', 'Only trusted servers on this computer, addressed by a loopback IP, can receive private data.', ''),
    'destination_proxied': ('Destination goes through a proxy', 'A configured proxy would carry this request, so it cannot receive private data.', ''),
    'destination_trust_unavailable': ('Trust settings unreadable', 'The trusted destinations file cannot be read, so nothing can receive private data.', ''),
    'destination_changed': ('Destination changed', 'The destination or its trust changed after review.', ''),
    'destination_unavailable': ('Destination unavailable', 'The step at {where} has no usable destination.', ''),
    'protected_host_script': ('Protected script', 'A protected step cannot be a script, because scripts run with ordinary host access.', ''),
    'missing_credential': ('Missing credential', 'The configured model at {where} needs a credential.', ''),
    'tool_not_observed': ('Tool not offered', 'The latest tool list for {where} does not offer this tool.', ''),
    'tool_unverified': ('Tool not verified', 'Refresh tools to verify that {where} is still offered.', ''),
    'budget_exceeded': ('Budget too small', 'The model-call budget is below the calls this workflow needs.', ''),
    'binding_missing': ('Not connected', '{where} has no source.', ''),
    'contract_mismatch': ('Contracts do not fit', '{where}: the source and receiver contracts do not fit.', ''),
    'path_missing': ('Selected field missing', '{where}: the source lacks the selected field or index.', ''),
    'path_invalid': ('Invalid field path', '{where}: the field path is invalid.', ''),
    'workflow_cycle': ('Cycle', 'The workflow has a cycle, so it cannot be planned.', ''),
    'role_unresolved': ('Role not configured', 'Role {target} does not resolve to a usable profile.', ''),
    'server_missing': ('Server not configured', 'MCP server {target} is not configured.', ''),
    'example_incompatible': ('Example does not fit', '{where}: the example does not fit the current ports.', ''),
    # Release Gate planning (§1)
    'release_unneeded': ('Release not needed', 'The candidate at {where} is already unrestricted, so this Release Gate releases nothing.', ''),
    'scan_unrestricted': ('Identifier mapping not private', 'The identifier mapping at {where} must be Local only.', ''),
    'unrestricted_ancestor': ('Data sent before labels apply', '{node} sends data before any local-only label applies. Labels further on don’t protect that earlier call. Label the input, or add a Release Gate if a reviewed value should leave this computer.', ''),
    'scan_invalid': ('Identifier mapping invalid', 'The identifier mapping cannot be scanned.', ''),
    'scan_limit': ('Identifier check limit', 'The identifier check exceeded its limits.', ''),
    # restore transform (§2)
    'transform_error': ('Transform failed', '{where}: {message}', ''),
    'output_failed': ('Output check failed', 'The step output failed its contract.', ''),
    # remedy actions (§3.4)
    'grant': ('Grant trust', 'Trust this exact local destination to receive private data.', 'Grant'),
    'fix_address': ('Use a loopback IP', 'Change the server URL to a literal loopback IP such as 127.0.0.1.', 'Edit server URL'),
    'fix_proxy': ('Bypass the proxy', 'Exclude this local address from the proxy (for example with NO_PROXY).', 'Proxy help'),
    'choose_local_role': ('Use a local role', 'Choose a role whose profile is a trusted local server.', 'Choose role'),
    'configure_role': ('Configure the role', 'Assign role {target} to a profile with a model.', 'Open roles'),
    'configure_server': ('Configure the server', 'Add MCP server {target}.', 'Open MCP servers'),
    'refresh_readiness': ('Check again', 'Review readiness again before running.', 'Check readiness'),
    'repair_trust_settings': ('Repair trust settings', 'Repair or remove the trusted destinations file.', 'Open trusted destinations'),
    'add_release_gate': ('Add a Release Gate', 'This value is private only because it came from a protected step. Add a Release Gate so a person can review it before it leaves this computer.', 'Add Release Gate'),
    'explain': ('Not supported', 'This protected path is not supported.', 'Learn more'),
}
CODES = frozenset(CATALOGUE)
FALLBACK = ('Refused', 'This action was refused ({code}).', '')
HELP = ('A workflow without labels is treated according to its labels: it is not checked for private content, '
        'and the absence of warnings does not certify that data is non-private.')


class _Fields(dict):
    def __missing__(self, key):
        return ''


def _fields(item):
    fields = _Fields({key: value for key, value in item.items() if isinstance(value, (str, int))})
    fields['where'] = item.get('location') or item.get('port') or item.get('input') or item.get('consumer') or ''
    fields.setdefault('example', item.get('example') or item.get('example_id') or '')
    chain = item.get('chain')
    if isinstance(chain, list) and chain and isinstance(chain[-1], list):
        fields['chain_end'] = '.'.join(str(part) for part in chain[-1])
    target = item.get('target')
    if isinstance(target, dict):
        fields['target'] = target.get('model') or target.get('tool') or target.get('url') or ''
    return fields


def _format(template, fields):
    return string.Formatter().vformat(template, (), fields)


def explain(item):
    """{title, body, remedy_label} for one item; never raises."""
    try:
        code = item.get('code') or item.get('action')
        title, body, label = CATALOGUE.get(code, FALLBACK)
        fields = _fields(item)
        fields['code'] = str(code)
        remedy = item.get('remedy')
        if isinstance(remedy, dict) and remedy.get('action') in CATALOGUE:
            label = CATALOGUE[remedy['action']][2]
        return {'title': title, 'body': _format(body, fields), 'remedy_label': label}
    except Exception:  # noqa: BLE001 - the catalogue must never turn a refusal into an error
        return {'title': FALLBACK[0], 'body': 'This action was refused.', 'remedy_label': ''}


def catalogue():
    return {'format_version': 1, 'help': HELP, 'fallback': dict(zip(('title', 'body', 'remedy_label'), FALLBACK)),
            'codes': {code: dict(zip(('title', 'body', 'remedy_label'), row)) for code, row in CATALOGUE.items()}}
