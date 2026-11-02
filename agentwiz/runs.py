"""Runs, attempts, the cancellation token and the in-memory attempt store (techspec 0.1.3, 8.1-8.3, 8.5,
8.6, 8.8, 8.9, 9.1, 10.5, 14; R4-9, R4-10, R4-15, R4-16, R4-21, R4-23, R4-27, R4-36, R4-37, R5-10).

A *run* is one model request with one step; the first send and every repair are numbered *attempts* of
that step. Each attempt passes two fixed points, the end point and the commit, after which its record
never changes. The store decides, under one lock, every race between Stop, the end of a stream and a
repair admission.

Lock order (Section 14): the state lock, then this store's lock, then a token's lock. A token's lock is
never held while another lock is taken. No stop action runs while any lock is held. The store's lock is
held only for bookkeeping: never during a model call, a page write, a check, a history write or a folder
removal. Knows nothing of HTTP.
"""
from __future__ import annotations

import copy
import datetime
import re
import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import modelspell

from . import jsonio

RECORD_LIMIT = 100
RECORD_BYTES = 268_435_456
SUMMARY_LIMIT = 1000
STEP_ID = "request"
ATTEMPT_ID_RE = re.compile(r"[0-9a-f]{12}-[1-9][0-9]{0,5}\Z")
RUN_ID_RE = re.compile(r"[0-9a-f]{12}\Z")

HISTORY_INTACT, HISTORY_DELETING = "intact", "deleting"
HISTORY_DELETED, HISTORY_INCOMPLETE = "deleted", "history_incomplete"


def utc_now() -> str:
    """UTC time, ISO 8601 with milliseconds and a Z."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def attempt_id(run_id: str, number: int) -> str:
    return f"{run_id}-{number}"


def valid_attempt_id(value: object) -> bool:
    return isinstance(value, str) and ATTEMPT_ID_RE.match(value) is not None


def valid_run_id(value: object) -> bool:
    return isinstance(value, str) and RUN_ID_RE.match(value) is not None


# --- the cancellation token (8.3) -------------------------------------------------------------------
class CancelToken:
    """One per run. `cancel()` is idempotent and thread-safe. Stop actions run exactly once, outside every
    lock, in registration order; an action may itself call `cancel()`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled = False
        self._at: str | None = None
        self._actions: list[Callable[[], None]] = []

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    @property
    def cancelled_at(self) -> str | None:
        with self._lock:
            return self._at

    def cancel(self) -> bool:
        """True for the call that cancelled the token; False if it was already cancelled."""
        actions = self._publish()
        if actions is None:
            return False
        self._dispatch(actions)
        return True

    def on_cancel(self, action: Callable[[], None]) -> None:
        """Run `action` when the token is cancelled, or now (in this thread, after the lock is released)
        if it already is. The caller must hold no outer lock (state or store)."""
        with self._lock:
            if not self._cancelled:
                self._actions.append(action)
                return
        self._run_action(action)

    def _publish(self) -> list[Callable[[], None]] | None:
        """Mark the token cancelled and take its actions, without running them. None if already cancelled."""
        with self._lock:
            if self._cancelled:
                return None
            self._cancelled = True
            self._at = utc_now()
            actions, self._actions = self._actions, []
            return actions

    def _dispatch(self, actions: list[Callable[[], None]]) -> None:
        for action in actions:
            self._run_action(action)

    @staticmethod
    def _run_action(action: Callable[[], None]) -> None:
        try:
            action()
        except Exception as exc:  # noqa: BLE001 - the others must still run; class name only is reported
            from .ui.server import report_internal
            report_internal("stop action", exc)


# --- runs and attempts --------------------------------------------------------------------------------
@dataclass
class Run:
    """Run metadata (8.9): small, and never holding a record object. Mutable fields are guarded by the
    store's lock."""
    run_id: str
    source: str
    save_runs: bool
    created_at: str
    inputs: dict                   # {"system", "prompt"}
    contract: dict | None          # {"name", "revision"} or None
    token: CancelToken
    handle: object                 # modelspell.CancelHandle
    folder: Path | None = None     # set by the history writer when run.json was written
    history_problem: str | None = None   # why the run folder could not be created
    # --- guarded by the store's lock
    next_attempt: int = 1
    latest: int = 0                # the highest attempt number started
    running: int | None = None     # the attempt number reserved or running (at most one)
    pending: set = field(default_factory=set)   # attempt numbers with a save pending
    held: set = field(default_factory=set)      # attempt numbers whose full records are held
    history_state: str = HISTORY_INTACT
    prior_history_state: str | None = None      # captured at a deletion's reservation (R5-10)


class RepairRefused(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class NoSuchAttempt(Exception):
    pass


class NotHeld(Exception):
    pass


class RunActive(Exception):
    pass


class NoSuchRunInMemory(Exception):
    pass


@dataclass
class _Attempt:
    id: str
    run: Run
    number: int
    record: dict | None            # live while running; frozen after the commit; None once dropped
    state: str = "running"         # "running" | "committed"
    ended: bool = False
    stop_requested_at: str | None = None
    save_status: str | None = None
    save_detail: dict = field(default_factory=dict)   # {"file"} or {"reason"}
    summary: dict | None = None


@dataclass(frozen=True)
class EndPoint:
    cancelled: bool
    stop_requested_at: str | None


def _summarise(att: _Attempt, record: dict) -> dict:
    check = record.get("check")
    return {"id": att.id, "run_id": att.run.run_id, "step_id": record["step_id"], "attempt": att.number,
            "parent_id": record["parent_id"], "outcome": record["outcome"],
            "cancel_reason": record["cancel_reason"], "stop_requested_at": record["stop_requested_at"],
            "check": check["overall"] if isinstance(check, dict) else None, "ended_at": record["ended_at"]}


class AttemptStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, Run] = {}
        self._attempts: dict[str, _Attempt] = {}
        self._records: OrderedDict[str, int] = OrderedDict()   # committed, held records -> encoded size
        self._record_bytes = 0
        self._settled: OrderedDict[str, None] = OrderedDict()  # settled committed attempts, oldest first

    # --- views for tests and the state body -------------------------------------------------------------
    def running_count(self) -> int:
        with self._lock:
            return sum(1 for a in self._attempts.values() if a.state == "running")

    def stop_all(self) -> None:
        """Cancel active attempts when a disposable tutorial instance closes."""
        with self._lock:
            ids = [a.id for a in self._attempts.values() if a.state == "running"]
        for id_ in ids:
            self.stop(id_)

    def run_view(self, run_id: str) -> dict | None:
        """The run's metadata as a plain dictionary (what the store keeps, never a record), or None."""
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            return {"run_id": run.run_id, "next_attempt": run.next_attempt, "latest": run.latest,
                    "running": run.running, "pending": sorted(run.pending), "held": sorted(run.held),
                    "history_state": run.history_state, "prior_history_state": run.prior_history_state,
                    "attempts": sorted(a.number for a in self._attempts.values() if a.run is run)}

    # --- creating runs and attempts ---------------------------------------------------------------------
    def new_run(self, *, source: str, save_runs: bool, system: str, prompt: str, contract: dict | None) -> Run:
        """A new run with attempt 1 reserved. The caller must start it (`run_attempt`) or `abandon_run` it."""
        run = Run(run_id=secrets.token_hex(6), source=source, save_runs=save_runs, created_at=utc_now(),
                  inputs={"system": system, "prompt": prompt}, contract=contract, token=CancelToken(),
                  handle=modelspell.CancelHandle())
        run.token.on_cancel(run.handle.cancel)
        with self._lock:
            run.next_attempt, run.running = 2, 1
            self._runs[run.run_id] = run
        return run

    def abandon_run(self, run: Run, number: int) -> None:
        """Give back a reservation that was never started (a refusal after reserving)."""
        with self._lock:
            if run.running == number and attempt_id(run.run_id, number) not in self._attempts:
                run.running = None
                if run.next_attempt == number + 1:
                    run.next_attempt = number
                self._prune_run_locked(run)

    def admit_repair(self, attempt_id_: str, reserve: bool = True) -> tuple[Run, dict, int]:
        """Section 9.1: every check, and the reservation of the next attempt number, in one step under the
        store's lock. Returns (run, the parent's committed record, the reserved number); with reserve=False
        (the preview) nothing is reserved and the number is the one the next repair would take. Raises
        NoSuchAttempt (404), RepairRefused."""
        with self._lock:
            att = self._attempts.get(attempt_id_)
            if att is None:
                raise NoSuchAttempt()
            run = att.run
            if att.state != "committed":
                raise RepairRefused(409, "not_latest", "this attempt is still running")
            if att.record is None or run.run_id not in self._runs:
                raise RepairRefused(410, "not_held",
                                    "This attempt is no longer held in memory; send the request again.")
            if run.token.cancelled:
                raise RepairRefused(409, "run_stopped", "This run was stopped; send the request again.")
            if run.history_state == HISTORY_DELETING:
                raise RepairRefused(409, "run_active", "This run's history is being deleted; try again.")
            if run.running is not None or att.number != run.latest:
                raise RepairRefused(409, "not_latest", "a later attempt of this step exists or is running")
            record = att.record
            check = record.get("check")
            if (record.get("outcome") not in ("complete", "truncated") or not isinstance(check, dict)
                    or check.get("overall") != "fail"):
                raise RepairRefused(409, "not_repairable", "only a failed check on a completed answer can be repaired")
            number = run.next_attempt
            if reserve:
                run.next_attempt += 1
                run.running = number
            return run, record, number

    def start(self, run: Run, number: int, record: dict) -> str:
        """Register the attempt (its number was reserved) with its record, in the running state."""
        aid = attempt_id(run.run_id, number)
        with self._lock:
            self._attempts[aid] = _Attempt(aid, run, number, record)
            run.latest = max(run.latest, number)
            run.running = number
            self._runs.setdefault(run.run_id, run)
        return aid

    # --- Stop (8.6) ---------------------------------------------------------------------------------------
    def stop(self, attempt_id_: str) -> str:
        """Accept a Stop for one attempt. 'stopping' or 'already_finished'; NoSuchAttempt if unknown. Acceptance,
        the timestamp and the token's cancellation are one decision under the store's lock; the stop actions
        are dispatched after every lock is released."""
        with self._lock:
            att = self._attempts.get(attempt_id_)
            if att is None:
                raise NoSuchAttempt()
            if att.ended or att.state != "running":
                return "already_finished"
            if att.stop_requested_at is None:
                att.stop_requested_at = utc_now()
            actions = att.run.token._publish()
            token = att.run.token
        if actions:
            token._dispatch(actions)
        return "stopping"

    # --- end point and commit (8.5) ------------------------------------------------------------------------
    def end_point(self, attempt_id_: str) -> EndPoint:
        with self._lock:
            att = self._attempts[attempt_id_]
            att.ended = True
            return EndPoint(att.run.token.cancelled, att.stop_requested_at)

    def commit(self, attempt_id_: str, record: dict, saving: bool) -> str:
        """Freeze the record, create its summary, apply retention and set the save status: 'pending' when the
        run is being saved, else 'not_requested'. Returns the status."""
        size = len(jsonio.encode(record))
        with self._lock:
            att = self._attempts[attempt_id_]
            run = att.run
            att.record = record
            att.state = "committed"
            att.summary = _summarise(att, record)
            att.save_status = "pending" if saving else "not_requested"
            run.running = None
            if saving:
                run.pending.add(att.number)
            if size <= RECORD_BYTES:
                self._records[att.id] = size
                self._record_bytes += size
                run.held.add(att.number)
            else:
                att.record = None            # larger than the whole budget: never kept
            self._evict_records_locked()
            if not saving:
                self._settle_locked(att)
            self._prune_run_locked(run)
            return att.save_status

    def _evict_records_locked(self) -> None:
        while self._records and (len(self._records) > RECORD_LIMIT or self._record_bytes > RECORD_BYTES):
            oldest, size = self._records.popitem(last=False)
            self._record_bytes -= size
            att = self._attempts.get(oldest)
            if att is not None:
                att.record = None
                att.run.held.discard(att.number)
                self._prune_run_locked(att.run)

    def _settle_locked(self, att: _Attempt) -> None:
        """The attempt's save status is final: it joins the settled summaries (the latest SUMMARY_LIMIT)."""
        self._settled[att.id] = None
        while len(self._settled) > SUMMARY_LIMIT:
            oldest, _ = self._settled.popitem(last=False)
            gone = self._attempts.pop(oldest, None)
            if gone is not None:
                size = self._records.pop(oldest, None)
                if size is not None:
                    self._record_bytes -= size
                gone.run.held.discard(gone.number)
                self._prune_run_locked(gone.run)

    def settle_save(self, attempt_id_: str, status: str, **detail) -> None:
        """pending -> saved or failed, exactly once."""
        with self._lock:
            att = self._attempts.get(attempt_id_)
            if att is None or att.save_status != "pending":
                return
            att.save_status, att.save_detail = status, dict(detail)
            att.run.pending.discard(att.number)
            self._settle_locked(att)
            self._prune_run_locked(att.run)

    def _prune_run_locked(self, run: Run) -> None:
        """Release the run's metadata once nothing needs it (8.9)."""
        if (run.running is None and not run.pending and not run.held
                and run.history_state != HISTORY_DELETING and self._runs.get(run.run_id) is run):
            del self._runs[run.run_id]

    def release_run(self, run: Run) -> None:
        with self._lock:
            self._prune_run_locked(run)

    # --- polling (8.8) --------------------------------------------------------------------------------------
    def envelope(self, attempt_id_: str) -> dict:
        """The polling envelope. Raises NotHeld (410) when neither a record nor a summary is held."""
        with self._lock:
            att = self._attempts.get(attempt_id_)
            if att is None:
                raise NotHeld()
            if att.state == "running":
                return {"state": "running", "stop_requested_at": att.stop_requested_at, "save_status": None,
                        "record_held": False}
            out = {"state": "committed", "stop_requested_at": att.record["stop_requested_at"] if att.record
                   else att.summary["stop_requested_at"],
                   "save_status": att.save_status, "record_held": att.record is not None}
            out.update(att.save_detail)
            if att.record is not None:
                out["record"] = att.record
            else:
                out["summary"] = {**att.summary, "record_held": False, "save_status": att.save_status}
            return out

    def record_of(self, attempt_id_: str) -> dict | None:
        with self._lock:
            att = self._attempts.get(attempt_id_)
            return att.record if att is not None and att.state == "committed" else None

    def save_detail(self, attempt_id_: str) -> tuple[str | None, dict]:
        with self._lock:
            att = self._attempts.get(attempt_id_)
            return (att.save_status, dict(att.save_detail)) if att is not None else (None, {})

    # --- history state and deletion (10.5) --------------------------------------------------------------------
    def history_state(self, run: Run) -> str:
        with self._lock:
            return run.history_state

    def reserve_delete(self, run_id: str) -> Run | None:
        """Reserve the deletion of a run's history. Returns the in-memory Run (now 'deleting'), or None when
        the run is not in memory. Raises RunActive when an attempt is running, a save is pending or a deletion
        is already reserved."""
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            if run.running is not None or run.pending or run.history_state == HISTORY_DELETING:
                raise RunActive()
            run.prior_history_state = run.history_state
            run.history_state = HISTORY_DELETING
            return run

    def settle_delete(self, run: Run | None, *, ok: bool, changed: bool) -> None:
        """Clear 'deleting' on every path (R5-10): success -> deleted; failure -> the state captured at the
        reservation unless the tree changed, in which case history_incomplete."""
        if run is None:
            return
        with self._lock:
            prior = run.prior_history_state or HISTORY_INTACT
            if ok:
                run.history_state = HISTORY_DELETED
            elif changed:
                run.history_state = HISTORY_INCOMPLETE
            else:
                run.history_state = prior
            run.prior_history_state = None
            self._prune_run_locked(run)

    # --- debugging aid for tests ---------------------------------------------------------------------------
    def snapshot_record(self, attempt_id_: str) -> dict | None:
        rec = self.record_of(attempt_id_)
        return copy.deepcopy(rec) if rec is not None else None
