"""Runner invariants for the library's model blocks (starter blocks addendum, 7.1 layer 2).

Plain Python functions, one per block: `check_<key>(inputs, outputs) -> list[str]`. `inputs` are the case's input
values as the block received them (text ports as strings, JSON ports as their JSON text); `outputs` are the
block's decoded outputs (text ports as strings, JSON ports as decoded values). Each function returns the
violations it found, as short reasons; an empty list means the run holds every invariant.

`HARD` names the blocks whose invariants must hold in every executed run (the bar in 7.1): classify_text label
membership, answer_from_source verbatim quotes, and deidentify_text map validity and consistency. For the other
blocks a violation makes the run `incorrect`, like a failed expectation.

There is no expression language here and no new runtime validator: these run only in scripts/library_eval.py
and the offline tests.
"""
from __future__ import annotations

import json
import copy
import re

HARD = frozenset({"classify_text", "answer_from_source", "deidentify_text"})
PLACEHOLDER = re.compile(r"\[[A-Z][A-Z0-9_]*\]")
NOT_FOUND = "Not found in the source."
WORD_LIMITS = {"short": 80, "medium": 200, "long": 400}


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def judgement_outputs(key, inputs, outputs, expectation):
    """Evaluation-only equivalence for a deadline's optional literal 'by ' prefix.

    Only minutes action_items[i].due is eligible, and only when the source contains
    that exact 'by <expected date>' phrase. Raw outputs and all other fields remain untouched.
    """
    if key != 'minutes_from_transcript' or expectation.get('kind') != 'fields':
        return outputs, []
    adjusted = copy.deepcopy(outputs)
    port = adjusted.get('action_items')
    if not isinstance(port, dict) or port.get('format') != 'json':
        return outputs, []
    items = json.loads(port['text'])
    changes = []
    for assertion in expectation.get('assertions', []):
        path = assertion.get('path', [])
        if (assertion.get('output') != 'action_items' or assertion.get('operation') != 'equals'
                or len(path) != 2 or type(path[0]) is not int or path[1] != 'due'):
            continue
        expected = json.loads(assertion['value_text'])
        index = path[0]
        if not isinstance(expected, str) or not 0 <= index < len(items):
            continue
        item = items[index]
        if (isinstance(item, dict) and item.get('due') == 'by ' + expected
                and re.search(r'(?<!\w)' + re.escape('by ' + expected) + r'(?!\w)',
                              inputs.get('transcript', ''))):
            item['due'] = expected
            changes.append({'output': 'action_items', 'path': path, 'rule': 'optional_by_deadline_prefix'})
    if changes:
        port['text'] = json.dumps(items)
    return adjusted, changes


def _words(text: str) -> int:
    return len(text.split())


def _verbatim(values, source: str, what: str) -> list[str]:
    return [f"{what} {i + 1} is not copied verbatim from the input"
            for i, v in enumerate(values) if isinstance(v, str) and v and v not in source]


def check_classify_text(inputs, outputs):
    labels = _json(inputs.get("labels"))
    label = outputs.get("label")
    if not isinstance(labels, list):
        return ["the case's labels are not a list"]
    return [] if label in labels else ["label is not one of the given labels"]


def check_answer_from_source(inputs, outputs):
    source, found = inputs.get("source", ""), outputs.get("found")
    quotes, answer = outputs.get("quotes") or [], outputs.get("answer", "")
    problems = _verbatim(quotes, source, "quote")
    if found is False:
        if answer.strip() != NOT_FOUND:
            problems.append("found is false but the answer is not the fixed not-found sentence")
        if quotes:
            problems.append("found is false but quotes were given")
    elif found is True and not quotes:
        problems.append("found is true but no quote supports the answer")
    return problems


def check_deidentify_text(inputs, outputs):
    source, text = inputs.get("text", ""), outputs.get("text", "")
    entries = outputs.get("identifier_map") or []
    problems, by_placeholder, by_original = [], {}, {}
    pre_existing = set(PLACEHOLDER.findall(source))
    for i, entry in enumerate(entries):
        placeholder, original = entry.get("placeholder", ""), entry.get("original", "")
        if not PLACEHOLDER.fullmatch(placeholder):
            problems.append(f"map entry {i + 1}: placeholder does not match bracket_upper")
        if not original or original not in source:
            problems.append(f"map entry {i + 1}: original does not occur in the input")
        if placeholder not in text:
            problems.append(f"map entry {i + 1}: placeholder does not occur in the output text")
        if placeholder in pre_existing:
            problems.append(f"map entry {i + 1}: placeholder collides with a token already in the input")
        if by_placeholder.setdefault(placeholder, original) != original:
            problems.append(f"map entry {i + 1}: two originals share one placeholder")
        if by_original.setdefault(original, placeholder) != placeholder:
            problems.append(f"map entry {i + 1}: one original has two placeholders")
    for original in by_original:
        if original and original in text:
            problems.append("a mapped original still occurs in the output text")
            break
    return problems


def check_summarise_document(inputs, outputs):
    length = _json(inputs.get("length"))
    limit = WORD_LIMITS.get(length)
    summary = outputs.get("summary", "")
    if limit is not None and _words(summary) > limit:
        return [f"summary has {_words(summary)} words, over the {length} limit of {limit}"]
    return []


def check_critique_against_checklist(inputs, outputs):
    checklist = _json(inputs.get("checklist"))
    results = outputs.get("results") or []
    if not isinstance(checklist, list):
        return ["the case's checklist is not a list"]
    problems = []
    if [r.get("item") for r in results] != checklist:
        problems.append("results do not echo the checklist items exactly and in order")
    text = inputs.get("text", "")
    for i, r in enumerate(results):
        if r.get("met") in ("yes", "no") and r.get("evidence") and r["evidence"] not in text:
            problems.append(f"result {i + 1}: evidence is not copied verbatim from the text")
    return problems


def check_table_from_text(inputs, outputs):
    columns = _json(inputs.get("columns"))
    rows = outputs.get("rows") or []
    if not isinstance(columns, list):
        return ["the case's columns are not a list"]
    return [f"row {i + 1} has {len(row)} values for {len(columns)} columns"
            for i, row in enumerate(rows) if len(row) != len(columns)]


def check_extract_entities(inputs, outputs):
    text, problems = inputs.get("text", ""), []
    for port in ("people", "organisations", "dates", "places"):
        values = outputs.get(port) or []
        problems += _verbatim(values, text, f"{port} value")
        if len(set(values)) != len(values):
            problems.append(f"{port} lists a value more than once")
        positions = [text.find(v) for v in values if v in text]
        if positions != sorted(positions):
            problems.append(f"{port} is not in order of first appearance")
    return problems


def check_proofread(inputs, outputs):
    text, corrected = inputs.get("text", ""), outputs.get("corrected", "")
    problems = []
    for i, change in enumerate(outputs.get("changes") or []):
        if change.get("before") and change["before"] not in text:
            problems.append(f"change {i + 1}: before is not copied from the original")
        if change.get("after") and change["after"] not in corrected:
            problems.append(f"change {i + 1}: after is not copied from the corrected text")
    return problems


def _owned_items(items, text):
    return [f"item {i + 1}: owner is not named in the text"
            for i, item in enumerate(items) if item.get("owner") and item["owner"] not in text]


def check_extract_action_items(inputs, outputs):
    return _owned_items(outputs.get("items") or [], inputs.get("text", ""))


def check_minutes_from_transcript(inputs, outputs):
    return _owned_items(outputs.get("action_items") or [], inputs.get("transcript", ""))


def check_compare_documents_meaning(inputs, outputs):
    before, after, problems = inputs.get("before", ""), inputs.get("after", ""), []
    if before == after and outputs.get("differences"):
        problems.append("identical documents but differences were reported")
    return problems


def check_rewrite_for_audience(inputs, outputs):
    return [] if outputs.get("text", "").strip() or not inputs.get("text", "").strip() else ["empty rewrite"]


def check_translate_text(inputs, outputs):
    return [] if outputs.get("text", "").strip() or not inputs.get("text", "").strip() else ["empty translation"]


def check_draft_reply(inputs, outputs):
    return [] if outputs.get("reply", "").strip() else ["empty reply"]


CHECKS = {name[len("check_"):]: fn for name, fn in dict(globals()).items() if name.startswith("check_")}


def check(key: str, inputs: dict, outputs: dict) -> list[str]:
    """The violations of one run; a block without a function has no extra invariants."""
    fn = CHECKS.get(key)
    return [] if fn is None else fn(inputs, outputs)
