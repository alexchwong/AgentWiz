"""Workspace transcripts for AgentWiz Chat. Private turns are never written here."""
from __future__ import annotations

import math
import re
from pathlib import Path

from . import blocks, history, jsonio, model_call, runs
from .workspace import write_atomic_bytes

PREFIX = "chat-history-"
LIMIT = 8 << 20
MAX_CHATS = 50
MAX_TURNS = 20
ID = re.compile(r"conv_[a-f0-9]{8}")


class HistoryError(Exception):
    pass


def model_choice(value):
    """Validate a source and model ID. A choice conveys no connection settings or authority."""
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {"kind", "model"}
            or value["kind"] not in model_call.KINDS or not isinstance(value["model"], str)
            or not 0 < len(value["model"]) <= 4096 or value["model"] != value["model"].strip()
            or not value["model"].isprintable()):
        raise ValueError("invalid model choice")
    if value["kind"] == "openrouter":
        parts = value["model"].split("/")
        if (len(parts) != 2
                or any(part in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.:-]+", part) for part in parts)):
            raise ValueError("invalid OpenRouter model ID")
    return dict(value)


USAGE_FIELDS = ("input_tokens", "output_tokens", "cost")


def usage_summary(result):
    """An attempted call contributes known values and explicitly marks missing values."""
    result = result or {}
    usage = result.get("usage") or {}
    values = {key: usage.get(key) for key in USAGE_FIELDS[:2]}
    values["cost"] = result.get("cost")
    for key, value in values.items():
        if not _usage_value(key, value):
            values[key] = None
    return {**values, "partial": [key for key in USAGE_FIELDS if values[key] is None]}


def _usage_value(key, value):
    if key == "cost":
        return type(value) in (int, float) and 0 <= value <= 1e100 and math.isfinite(value)
    return type(value) is int and value >= 0


def merge_usage(current, change):
    if current is None:
        return change
    if change is None:
        return current
    totals = {key: None if current[key] is None and change[key] is None else
              (current[key] or 0) + (change[key] or 0) for key in USAGE_FIELDS}
    return {**totals, "partial": [key for key in USAGE_FIELDS
                                   if key in current["partial"] or key in change["partial"]]}


def _usage_valid(value):
    return (isinstance(value, dict) and set(value) == {*USAGE_FIELDS, "partial"}
            and isinstance(value["partial"], list)
            and all(key in USAGE_FIELDS for key in value["partial"])
            and all((value[key] is None and key in value["partial"]) or _usage_value(key, value[key])
                    for key in USAGE_FIELDS))


def _route_valid(value):
    return (isinstance(value, dict) and set(value) == {"kind", "model", "provider"}
            and value["kind"] in model_call.KINDS and isinstance(value["model"], str)
            and (value["provider"] is None or isinstance(value["provider"], str)))


def turn_snapshot(view):
    """Keep displayable turns without staged-file handles, raw tool payloads or route credentials."""
    return {
        "run_id": view["run_id"], "message": view["message"], "answer": view["answer"],
        "partial": view["partial"], "stop_reason": view["stop_reason"],
        "trace": [{"capability": action["capability"], "outcome": action["outcome"]}
                  for action in view["trace"]],
        "sources": view["sources"],
        **{key: view[key] for key in ("accounting", "served_route") if key in view},
    }


def _turn_valid(turn):
    return (isinstance(turn, dict)
            and set(turn) - {"accounting", "served_route"} ==
            {"run_id", "message", "answer", "partial", "stop_reason", "trace", "sources"}
            and (turn.get("accounting") is None or _usage_valid(turn["accounting"]))
            and ("served_route" not in turn or _route_valid(turn["served_route"]))
            and runs.valid_run_id(turn["run_id"]) and isinstance(turn["message"], str)
            and all(turn[field] is None or isinstance(turn[field], str)
                    for field in ("answer", "partial", "stop_reason"))
            and isinstance(turn["trace"], list)
            and all(isinstance(action, dict) and set(action) == {"capability", "outcome"}
                    and all(isinstance(value, str) for value in action.values()) for action in turn["trace"])
            and isinstance(turn["sources"], list) and all(isinstance(url, str) for url in turn["sources"]))


ENTRY_ID = re.compile(r"ce_[a-f0-9]{16}")
JOB_ID = re.compile(r"[a-f0-9]{32}")
MAX_COMPANY = 120
MAX_COMPANY_RUNS = 20
ENTRY_KEYS = {
    "user": {"id", "type", "at", "text", "intake_id", "error"},
    "representative": {"id", "type", "at", "kind", "text", "usage", "model", "commentary_on"},
    "handoff": {"id", "type", "at", "intake_id", "origin", "summary", "brief", "brief_sha256", "state", "run_id"},
    "run": {"id", "type", "at", "handoff_id", "policy", "model", "class", "state", "outcome", "reason", "pathway",
            "preset_id", "updates", "operations", "summary", "usage", "open_conditions", "question", "report"},
}


def _optional_str(value):
    return value is None or isinstance(value, str)


def entry_valid(entry):
    """Strict shape check for one saved company entry. Values are displayed, never executed."""
    if not isinstance(entry, dict) or entry.get("type") not in ENTRY_KEYS:
        return False
    kind = entry["type"]
    keys = ENTRY_KEYS[kind] | ({"report_withheld"} if kind == "run" else set())
    if not ENTRY_KEYS[kind] <= set(entry) <= keys or not isinstance(entry["at"], str):
        return False
    if kind == "run":
        report = entry["report"]
        return (bool(JOB_ID.fullmatch(entry["id"])) and entry["state"] in ("running", "finished", "interrupted")
                and all(_optional_str(entry[key]) for key in ("outcome", "reason", "pathway", "preset_id", "question",
                                                              "model", "class", "handoff_id"))
                and isinstance(entry["updates"], list) and isinstance(entry["operations"], list)
                and all(isinstance(row, dict) and isinstance(row.get("text"), str) for row in entry["updates"])
                and all(isinstance(row, dict) and isinstance(row.get("id"), str) for row in entry["operations"])
                and isinstance(entry["summary"], dict) and isinstance(entry["open_conditions"], list)
                and (entry["usage"] is None or _usage_valid(entry["usage"]))
                and (report is None or (isinstance(report, dict) and set(report) == {"markdown", "sha256", "size", "status"}
                                        and isinstance(report["markdown"], str) and isinstance(report["sha256"], str)
                                        and type(report["size"]) is int)))
    if not ENTRY_ID.fullmatch(entry["id"]):
        return False
    if kind == "user":
        return isinstance(entry["text"], str) and _optional_str(entry["error"]) and isinstance(entry["intake_id"], str)
    if kind == "representative":
        return (entry["kind"] in ("simple_answer", "clarification", "brief_proposal") and isinstance(entry["text"], str)
                and (entry["usage"] is None or _usage_valid(entry["usage"])) and _optional_str(entry["model"])
                and _optional_str(entry["commentary_on"]))
    summary = entry["summary"]
    return (entry["origin"] in ("representative", "direct", "edited") and entry["state"] in ("draft", "sent", "superseded")
            and isinstance(entry["brief"], str) and isinstance(entry["brief_sha256"], str)
            and _optional_str(entry["run_id"])
            and (summary is None or (isinstance(summary, dict) and set(summary) == {"objective", "constraints", "deliverable"}
                                     and isinstance(summary["constraints"], list))))


def interrupt(entry):
    """A run saved while running cannot have continued: it is shown as interrupted, never as complete."""
    if entry["type"] != "run" or entry["state"] != "running":
        return entry
    summary = {**entry["summary"], "state": "interrupted", "current": None}
    return {**entry, "state": "interrupted", "outcome": None, "reason": "process_ended", "summary": summary}


class History:
    """The manager serializes writes with conversation changes and executions."""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    @staticmethod
    def _name(ident):
        if not isinstance(ident, str) or not ID.fullmatch(ident):
            raise HistoryError("no_such_chat")
        return f"{PREFIX}{ident}.json"

    def contains(self, ident):
        path = self.workspace / self._name(ident)
        return path.exists() or path.is_symlink()

    def _ids(self):
        return [ident for path in self.workspace.glob(f"{PREFIX}*.json")
                if ID.fullmatch(ident := path.stem.removeprefix(PREFIX))]

    def _read(self, ident):
        result = blocks.read_file(self.workspace, self._name(ident), LIMIT)
        if result.fingerprint == ("missing",):
            return None
        if result.data is not None:
            try:
                row = jsonio.decode_file(result.data)
                version = row.get("format_version")
                keys = {"format_version", "id", "private", "ready", "updated", "title", "model", "turns"}
                if type(version) is int and version >= 2:
                    keys |= {"totals"}
                if type(version) is int and version == 3:
                    keys |= {"company", "timeline"}
                if (type(version) is int and version in (1, 2, 3) and set(row) == keys
                        and (row.get("totals") is None or _usage_valid(row["totals"]))
                        and row["id"] == ident and row["private"] is False and type(row["ready"]) is bool
                        and isinstance(row["updated"], str) and isinstance(row["title"], str)
                        and isinstance(row["turns"], list) and len(row["turns"]) <= MAX_TURNS
                        and all(_turn_valid(turn) for turn in row["turns"])
                        and len({turn["run_id"] for turn in row["turns"]}) == len(row["turns"])
                        and self._company_valid(row)):
                    model_choice(row["model"])
                    row.setdefault("company", [])
                    row.setdefault("timeline", [["chat", turn["run_id"]] for turn in row["turns"]])
                    return row
            except (ValueError, AttributeError, RecursionError):
                pass
        raise HistoryError("chat_history_unavailable")

    @staticmethod
    def _company_valid(row):
        if row["format_version"] != 3:
            return len(row["turns"]) > 0
        company, timeline = row["company"], row["timeline"]
        if (not isinstance(company, list) or not isinstance(timeline, list) or len(company) > MAX_COMPANY
                or not row["turns"] and not company or not all(entry_valid(entry) for entry in company)):
            return False
        ids = {"chat": {turn["run_id"] for turn in row["turns"]}, "company": {entry["id"] for entry in company}}
        if len(ids["company"]) != len(company):
            return False
        return all(isinstance(item, list) and len(item) == 2 and item[0] in ids and item[1] in ids[item[0]]
                   for item in timeline)

    def listing(self):
        rows = []
        for ident in self._ids()[:MAX_CHATS]:
            try:
                row = self._read(ident)
            except HistoryError:
                rows.append({"id": ident, "title": "Unavailable chat", "ready": False, "updated": ""})
                continue
            if row is not None:
                rows.append({key: row[key] for key in ("id", "title", "ready", "updated")})
        return sorted(rows, key=lambda row: row["updated"], reverse=True)

    def reopen(self, ident):
        row = self._read(ident)
        if row is None:
            raise HistoryError("no_such_chat")
        if not row["ready"]:
            raise HistoryError("chat_history_interrupted")
        return row

    def _write(self, row):
        data = jsonio.encode(row) + b"\n"
        while len(data) > LIMIT and len(row.get("timeline") or row["turns"]) > 1:
            if "timeline" in row:
                kind, ident = row["timeline"].pop(0)
                if kind == "chat":
                    row["turns"] = [turn for turn in row["turns"] if turn["run_id"] != ident]
                else:
                    row["company"] = [entry for entry in row["company"] if entry["id"] != ident]
            else:
                del row["turns"][0]
            data = jsonio.encode(row) + b"\n"
        if len(data) > LIMIT:
            raise HistoryError("chat_history_size")
        try:
            write_atomic_bytes(self.workspace, self._name(row["id"]), data)
        except OSError:
            raise HistoryError("chat_history_unavailable") from None

    def suspend(self, ident):
        row = self._read(ident)
        if row and row["ready"]:
            if row["format_version"] != 3:
                row = {key: value for key, value in row.items() if key not in ("company", "timeline")}
            self._write({**row, "ready": False})

    def save(self, ident, turns, model, totals, credentials, company=None, timeline=None):
        company = list(company or [])
        if not turns and not company:
            return
        before = self._read(ident)
        if before is None and len(self._ids()) >= MAX_CHATS:
            raise HistoryError("chat_history_full")
        turns = turns[-MAX_TURNS:]
        kept = {"chat": {turn["run_id"] for turn in turns}, "company": {entry["id"] for entry in company}}
        timeline = [[kind, item] for kind, item in (timeline or [["chat", t["run_id"]] for t in turns])
                    if item in kept.get(kind, ())]
        first = timeline[0] if timeline else None
        by_id = {**{turn["run_id"]: turn["message"] for turn in turns},
                 **{entry["id"]: entry.get("text", "") for entry in company if entry["type"] == "user"}}
        source = by_id.get(first[1], "") if first else (turns[0]["message"] if turns else "")
        title = " ".join(source.split())[:72] or "Chat"
        safe_company = []
        for entry in company:
            redacted, changed = history.redact(entry, credentials)
            if entry["type"] == "run" and entry.get("report") and redacted.get("report") != entry["report"]:
                redacted = {**redacted, "report": None, "report_withheld": "credential"}
            if changed:
                redacted["id"] = entry["id"]
            safe_company.append(redacted)
        content, _ = history.redact({"title": before["title"] if before else title, "model": model,
                                     "turns": turns}, credentials)
        try:
            model_choice(content["model"])
        except ValueError:
            content["model"] = None
        # Keep schema fields intact while redacting backend-supplied identity strings.
        for safe, turn in zip(content["turns"], turns):
            safe["run_id"] = turn["run_id"]
            if "accounting" in turn:
                safe["accounting"] = turn["accounting"]
            if "served_route" in turn:
                safe["served_route"]["kind"] = turn["served_route"]["kind"]
        row = {**content, "format_version": 2, "id": ident, "private": False, "ready": True, "totals": totals,
               "updated": runs.utc_now()}
        if safe_company:
            row.update(format_version=3, company=safe_company, timeline=timeline)
        self._write(row)

    def rename(self, ident, title, credentials):
        if not isinstance(title, str) or not 0 < len(title) <= 120 or not title.isprintable() or not title.strip():
            raise HistoryError("invalid_chat_title")
        row = self._read(ident)
        if row is None:
            raise HistoryError("no_such_chat")
        safe, _ = history.redact({"title": title.strip()}, credentials)
        if row["format_version"] != 3:
            row = {key: value for key, value in row.items() if key not in ("company", "timeline")}
        self._write({**row, "title": safe["title"]})

    def delete(self, ident):
        try:
            (self.workspace / self._name(ident)).unlink(missing_ok=True)
        except OSError:
            raise HistoryError("chat_history_unavailable") from None
