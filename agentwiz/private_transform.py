"""Native private-summary operations. The model cannot supply source offsets."""
from __future__ import annotations

import hashlib
import re
import unicodedata

from . import identifier_pairs, jsonio, templates
from .workflow_definition import digest, problem

OPERATIONS = {'privacy_locate': {'source', 'discovery', 'corrections'},
              'privacy_redact': {'source', 'located', 'classification'},
              'privacy_review': {'record', 'assessment'},
              'privacy_restore': {'text', 'mapping', 'map_digest', 'manifest'}}
KINDS = ('person', 'organisation', 'address', 'postcode', 'place', 'date', 'email',
         'phone', 'record_id', 'other')
TOKEN = re.compile(r'\[\[AWZ1_[A-Z_]+_[1-9][0-9]*\]\]')
MARKER = re.compile(r'AWZ\s*1', re.I)
MAX_SOURCE = 40_000
MAX_CANDIDATE = 64_000
MAX_OCCURRENCES = 512
MAX_CONTEXTUAL = 300


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def word(char):
    category = unicodedata.category(char)
    return category[0] in 'LMN' or category == 'Pc'


def matches(text, literals):
    folded_parts = [c.casefold() for c in text]
    folded = ''.join(folded_parts)
    offsets = [i for i, part in enumerate(folded_parts) for _ in part]
    for literal in literals:
        position = folded.find(literal)
        while position != -1:
            stop = position + len(literal)
            start, end = offsets[position], offsets[stop - 1] + 1
            aligned = ((not position or offsets[position - 1] != start)
                       and (stop == len(offsets) or offsets[stop] != end - 1))
            if aligned and (not start or not word(text[start - 1])) and (end == len(text) or not word(text[end])):
                yield literal, start, end
            position = folded.find(literal, position + 1)


def scan(text, pairs):
    try:
        entries = identifier_pairs.parse(pairs, profile='scan')
    except identifier_pairs.PairsError as exc:
        problem('scan_invalid', 'gate.scan', exc.message)
    originals = {}
    for entry in entries:
        originals.setdefault(entry.original.casefold(), set()).add(entry.placeholder)
    if len(text) * max(1, len(originals)) > 16 << 20:
        problem('scan_limit', 'gate.scan', 'identifier check exceeds its work limit')
    hits = {}
    for literal, start, end in matches(text, sorted(originals)):
        hits.setdefault((start, end), set()).update(originals[literal])
        if len(hits) > 1000:
            problem('scan_limit', 'gate.scan', 'identifier check exceeds its result limit')
    return {'status': 'complete', 'hits': [
        {'path': [], 'part': 'value', 'start': start, 'end': end, 'identifier': labels[0], 'labels': labels}
        for (start, end), names in sorted(hits.items()) for labels in [sorted(names)]]}


def issue(code, start=None, end=None, space='source'):
    result = {'code': code}
    if start is not None:
        result['location'] = {'space': space, 'start': start, 'end': end}
    return result


def correction(source, start, end, kind):
    """Map UTF-16 selection offsets on an exact source to native code-point offsets."""
    if (not isinstance(source, str) or len(source) > MAX_SOURCE or type(start) is not int
            or type(end) is not int or not 0 <= start < end <= MAX_SOURCE * 2 or kind not in KINDS):
        raise ValueError('invalid correction selection')
    encoded = source.encode('utf-16-le')
    if end * 2 > len(encoded):
        raise ValueError('selection is beyond the source')
    first = len(encoded[:start * 2].decode('utf-16-le'))
    last = len(encoded[:end * 2].decode('utf-16-le'))
    text = source[first:last]
    if len(text) > 4096:
        raise ValueError('correction selection exceeds 4096 code points')
    return {'start': first, 'end': last, 'text': text, 'source_digest': sha(source), 'kind': kind}


def locate(source, discovery, corrections):
    from .agent_tools import scan as pattern_scan

    if len(source) > MAX_SOURCE or MARKER.search(source):
        raise ValueError('source exceeds the private-summary limit or contains a reserved marker')
    issues = []
    kinds = {}
    for row in discovery['surfaces']:
        literal = row['surface'].casefold()
        kinds.setdefault(literal, set()).add(row['kind'])
    if len(source) * len(kinds) > 16 << 20:
        raise ValueError('source and discovered surfaces exceed the matching work limit')
    rows, seen = {}, set()
    for literal, start, end in matches(source, sorted(kinds)):
        seen.add(literal)
        kind = sorted(kinds[literal])[0] if len(kinds[literal]) == 1 else 'other'
        rows[start, end] = (kind, 'model')
    if seen != kinds.keys():
        issues.append(issue('unresolved_span'))
    if discovery.get('incomplete'):
        issues.append(issue('discovery_incomplete'))
    for start, end, kind in pattern_scan(source, ('email', 'phone')):
        rows[start, end] = (kind, 'pattern')
    for row in corrections:
        start, end = row['start'], row['end']
        if row['source_digest'] != sha(source) or not 0 <= start < end <= len(source) or source[start:end] != row['text']:
            raise ValueError('reviewed correction does not match the retained source')
        rows[start, end] = (row['kind'], 'user')
    if len(rows) > MAX_OCCURRENCES:
        raise ValueError('too many candidate occurrences')

    occurrences, queue = [], []
    for ident, ((start, end), (kind, origin)) in enumerate(sorted(rows.items()), 1):
        options = [[start, end]]
        compound = False
        if origin == 'model':
            left, right = start, end
            while left and (word(source[left - 1]) or source[left - 1] in "-'’"):
                left -= 1
            while right < len(source) and (word(source[right]) or source[right] in "-'’"):
                right += 1
            if source[end:right] not in ("'s", "’s") and [left, right] != [start, end]:
                compound = True
                options = [[left, right]]
                suffix = re.match(r' +[^\W\d_][^\W_]*', source[right:])
                if suffix and suffix.group().strip()[0].isupper():
                    options.append([left, right + len(suffix.group())])
        occurrences.append({'id': ident, 'kind': kind, 'origin': origin, 'options': options})
        if origin == 'model':
            queue.append({'candidate_id': ident, 'kind': kind,
                          'context': source[max(0, start - 96):end + 96],
                          'options': [{'span_id': i, 'text': source[a:b]} for i, (a, b) in enumerate(options)],
                          'compound': compound})
    if len(queue) > MAX_CONTEXTUAL:
        raise ValueError('too many contextual occurrences')
    aliases = set()
    surfaces = discovery['surfaces']
    for link in discovery['alias_links']:
        a, b, evidence = link['left'], link['right'], link['evidence']
        if (0 <= a < len(surfaces) and 0 <= b < len(surfaces) and a != b and evidence in source
                and all(surfaces[i]['surface'].casefold() in evidence.casefold() for i in (a, b))):
            aliases.add(tuple(sorted((surfaces[a]['surface'].casefold(), surfaces[b]['surface'].casefold()))))
        else:
            issues.append(issue('uncertain_identity'))
    return {'occurrences': occurrences, 'queue': queue, 'issues': issues, 'aliases': sorted(aliases)}


def redact(source, located, classification):
    occurrences = {row['id']: row for row in located['occurrences']}
    queued = {row['candidate_id'] for row in located['queue']}
    decisions, duplicate = {}, set()
    issues = list(located['issues'])
    for row in classification:
        ident = row['candidate_id']
        if ident not in queued:
            issues.append(issue('unexpected_classification'))
        elif ident in decisions:
            duplicate.add(ident)
        else:
            decisions[ident] = row
    selected = {}
    for ident, occurrence in occurrences.items():
        options = occurrence['options']
        decision = decisions.get(ident)
        if ident in queued:
            if decision and ident not in duplicate and decision['decision'] == 'keep':
                continue
            if (ident in duplicate or not decision or decision['decision'] != 'identifier'
                    or not 0 <= decision.get('span_id', -1) < len(options)):
                issues.append(issue('unresolved_span', *options[-1]))
                continue
        start, end = options[decision['span_id'] if decision else 0]
        selected[ident] = (start, end, occurrence['kind'], occurrence['origin'])
    parents = {ident: ident for ident in selected}

    def representative(ident):
        while parents[ident] != ident:
            ident = parents[ident]
        return ident

    aliases = {tuple(pair) for pair in located['aliases']}
    for ident, (start, end, kind, _) in selected.items():
        row = decisions.get(ident, {})
        link = row.get('same_entity_as')
        if not link:
            continue
        other = link['candidate_id']
        evidence = set(link['evidence_ids'])
        if other in selected and {ident, other} <= evidence <= queued and selected[other][2] == kind:
            a, b = selected[other][:2]
            pair = tuple(sorted((source[start:end].casefold(), source[a:b].casefold())))
            if pair[0] == pair[1] or pair in aliases:
                first, second = sorted((representative(ident), representative(other)))
                parents[second] = first
                continue
        issues.append(issue('uncertain_identity', start, end))

    spans = []
    for ident, (start, end, kind, origin) in sorted(selected.items(), key=lambda item: item[1]):
        if spans and spans[-1][1] > start:
            prior = spans.pop()
            spans.append((prior[0], max(end, prior[1]), 'other', 'overlap', ('overlap', prior[0])))
        else:
            spans.append((start, end, kind, origin, ('entity', representative(ident))))
    group_literals = {}
    for start, end, kind, origin, group in spans:
        group_literals.setdefault(group, set()).add(source[start:end])
    tokens, mapping, manifest, ledger = {}, {}, {}, []
    parts, source_offset, candidate_offset = [], 0, 0
    for start, end, kind, origin, group in spans:
        if group not in tokens:
            token = f'[[AWZ1_{kind.upper()}_{len(tokens) + 1}]]'
            tokens[group] = token
            variants = sorted(group_literals[group])
            mapping[token] = {'canonical': min(variants, key=lambda value: (-len(value), value)),
                              'variants': variants, 'kind': kind}
        token = tokens[group]
        head = source[source_offset:start]
        candidate_offset += len(head)
        parts.extend((head, token))
        ledger.append({'start': start, 'end': end, 'candidate_start': candidate_offset,
                       'candidate_end': candidate_offset + len(token), 'token': token,
                       'original': source[start:end], 'origin': origin})
        candidate_offset += len(token)
        source_offset = end
        manifest[token] = manifest.get(token, 0) + 1
    candidate = ''.join(parts) + source[source_offset:]
    reconstructed, cursor = [], 0
    for row in ledger:
        reconstructed.extend((candidate[cursor:row['candidate_start']], row['original']))
        cursor = row['candidate_end']
    reconstructed.append(candidate[cursor:])
    if len(candidate) > MAX_CANDIDATE or sha(''.join(reconstructed)) != sha(source):
        raise ValueError('candidate exceeds the limit or fails the exact inverse check')
    pairs = {(literal, token) for token, row in mapping.items() for literal in row['variants']}
    pairs.update((source[a:b], f'candidate_{ident}') for ident, (a, b, _, _) in selected.items()
                 if not any(a == row['start'] and b == row['end'] for row in ledger))
    scan_pairs = [{'original': original, 'placeholder': label} for original, label in sorted(pairs)]
    identifier_check = scan(candidate, scan_pairs)
    candidate_digest = sha(candidate)
    return {'candidate': candidate, 'source_digest': sha(source), 'candidate_digest': candidate_digest,
            'entity_map': mapping, 'map_digest': digest(mapping), 'token_manifest': manifest, 'ledger': ledger,
            'scan_pairs': scan_pairs, 'scan': identifier_check,
            'coverage': {'candidate_digest': candidate_digest, 'status': 'incomplete' if issues else 'complete',
                         'issues': issues[:MAX_OCCURRENCES]}}


def assess(candidate, response):
    severity = {'direct_identifier': 'high', 'identifying_combination': 'high',
                'potential_identifier': 'medium', 'linkable_context': 'medium', 'ambiguity': 'low'}
    findings, incomplete = [], False
    for row in response:
        quotes = row['quotes']
        located = [{'start': match.start(), 'end': match.end()} for quote in quotes
                   for match in re.finditer(re.escape(quote), candidate)]
        if (not quotes or any(not quote or quote not in candidate for quote in quotes)
                or (row['category'] == 'identifying_combination' and len(set(quotes)) < 2)):
            incomplete = True
            continue
        findings.append({**row, 'severity': severity[row['category']], 'locations': located[:64],
                         'concern': row['category'], 'location': quotes[0], 'explanation': row['reason']})
    findings.sort(key=lambda row: (('high', 'medium', 'low').index(row['severity']), row['locations'][0]['start']))
    return {'status': 'unavailable' if incomplete else 'complete', 'findings': findings,
            **({'reason': 'one or more findings lacked exact candidate evidence'} if incomplete else {})}


def restore(text, mapping, map_digest, manifest):
    if digest(mapping) != map_digest or mapping.keys() != manifest.keys():
        raise ValueError('entity map or token manifest does not match')
    if MARKER.search(TOKEN.sub('', text)) or any(
            found.group() not in manifest or text[found.start()-1:found.start()] == '['
            or text[found.end():found.end()+1] == ']' for found in TOKEN.finditer(text)):
        raise ValueError('invalid or unknown private-summary placeholder')
    return TOKEN.sub(lambda found: mapping[found.group()]['canonical'], text)


def execute(operation, inputs):
    try:
        if operation == 'privacy_locate':
            return locate(**inputs)
        if operation == 'privacy_redact':
            return redact(**inputs)
        if operation == 'privacy_review':
            record = inputs['record']
            return {'coverage': record['coverage'], 'scan': record['scan'],
                    'assessment': assess(record['candidate'], inputs['assessment'])}
        return restore(**inputs)
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        raise templates.FillError('inputs', 'invalid or oversized private-summary material') from exc
