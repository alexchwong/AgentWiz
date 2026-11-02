"""MCP operations: worker threads, gates, Stop, deadlines, capacity and maintenance work (techspec 0.1.2,
Section 9.7; R2-5, R2-8, R2-12, R2-13, R2-25, R2-35, R2-36).

Each discovery or tool call runs in its own worker thread. The worker hands progress and its final
result to the request handler through a queue holding at most the latest progress event and the final
event. An operation's outcome is committed exactly once, under the operation's lock, by whichever
comes first: the worker queuing its final result, or abandonment (Stop, the deadline, or the page
going away). Abandonment reserves a legacy cancellation notice's session borrow before it shuts down
every socket the operation has registered, so a session's DELETE can never overtake the notice.

At most WORKER_LIMIT workers exist, abandoned ones included until they exit; nothing is queued.
Cancellation notices and session DELETEs run as maintenance work with their own budget and a network
limit enforced by a gate after connecting. Nothing here ever resends a tool call. No HTTP server code.
"""
from __future__ import annotations

import collections
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from .mcp_client import (Abandoned, MCPClient, MCPError, OpContext, Session, Snapshot, classify, reason_text)

WORKER_LIMIT = 8
MAINTENANCE_LIMIT = 4
MAINTENANCE_NETWORK_S = 4.5
CANCEL_ANSWER_S = 5.0
RETAIN_COUNT = 200
RETAIN_S = 600.0


def before_final_write() -> None:
    """Called by a call's handler after the outcome is committed and before the final event is written.
    A no-op in shipped code; tests make it block to put a Stop between the commit and the write."""


class Busy(Exception):
    def __init__(self):
        super().__init__(f"{WORKER_LIMIT} tool operations are still running or stopping; try again shortly")


class NoSuchOperation(Exception):
    pass


class Operation:
    def __init__(self, ops: "Ops", kind: str, snap: Snapshot, deadline_s: float):
        self.ops = ops
        self.id = secrets.token_urlsafe(12)
        self.kind = kind                               # "discovery" | "call"
        self.snapshot = snap
        self.ctx = OpContext(time.monotonic() + deadline_s, self._progress)
        self.lock = self.ctx.lock                      # the operation's lock
        self.cond = threading.Condition(self.lock)
        self.progress: tuple | None = None             # the latest progress event, not yet taken
        self.committed: dict | None = None             # {"outcome", "reason", "value"|"error"}
        self.cancel_status: str | None = None
        self.cancel_reason: str | None = None
        self.notice_done = threading.Event()
        self.notice_done.set()
        self.ended_at: float | None = None

    # --- worker side --------------------------------------------------------------------------
    def _progress(self, progress: str, total: str | None, message: str | None) -> None:
        with self.cond:
            if self.committed is None and self.ctx.abandoned is None:
                self.progress = (progress, total, message)
                self.cond.notify_all()

    def _commit_locked(self, committed: dict) -> bool:
        if self.committed is not None:
            return False
        self.committed = committed
        self.cond.notify_all()
        return True

    # --- handler side -------------------------------------------------------------------------
    def wait(self, timeout: float) -> tuple[str, Any]:
        """("final", committed) | ("progress", (p, t, m)) | ("timeout", None)."""
        end = time.monotonic() + max(0.0, timeout)
        with self.cond:
            while True:
                if self.committed is not None:
                    return "final", self.committed
                if self.progress is not None:
                    p, self.progress = self.progress, None
                    return "progress", p
                left = end - time.monotonic()
                if left <= 0:
                    return "timeout", None
                self.cond.wait(left)

    def time_left(self) -> float:
        return self.ctx.deadline - time.monotonic()

    def abandon(self, reason: str) -> str:
        """Stop, the deadline or the page going away. Returns "repeat", "late" or "abandoned"."""
        notice = None
        with self.cond:
            if self.ctx.abandoned is not None:
                return "repeat"
            if self.committed is not None:
                if reason == "stopped":
                    self.cancel_status = self.cancel_status or "not_sent"
                    self.cancel_reason = self.cancel_reason or "the call had already finished"
                return "late"
            ctx = self.ctx
            ctx.abandoned = reason
            passed = ctx.call_gate_passed
            c = classify(self.kind if self.kind == "call" else "discovery", gate_passed=passed, abandoned=reason)
            self._commit_locked({"outcome": c.outcome, "reason": c.reason,
                                 "error": MCPError(c.outcome, c.reason, f"{reason_text(c.reason)} "
                                                   f"(server {self.snapshot.server})")})
            if self.kind == "call":
                if not passed:
                    self.cancel_status, self.cancel_reason = "not_sent", "nothing had been sent"
                elif ctx.call_session is not None:
                    self.ops.client.reserve_notice(ctx.call_session)   # before any socket is shut down
                    notice = (ctx.call_session, ctx.call_request_id)
                    self.cancel_status = "unconfirmed"
                    self.notice_done.clear()
                else:
                    self.cancel_status = "stream_closed"
            ctx.shutdown_sockets_locked()
        if notice is not None:
            if not self.ops.schedule_notice(self, *notice):
                with self.cond:
                    self.cancel_status, self.cancel_reason = "not_sent", "too many cancellations still in progress"
                self.ops.client.release(notice[0])
                self.notice_done.set()
        with self.cond:
            self.cond.notify_all()
        return "abandoned"

    def status(self) -> dict:
        with self.lock:
            out = {"status": self.cancel_status or "not_sent"}
            if self.cancel_reason:
                out["reason"] = self.cancel_reason
            return out


class Ops:
    def __init__(self, client: MCPClient):
        self.client = client
        client.schedule_delete = self.schedule_delete
        self._lock = threading.Lock()
        self.workers = 0
        self.maintenance = 0
        self._ops: dict[str, Operation] = {}
        self._ended: collections.deque[Operation] = collections.deque()

    def view(self) -> dict:
        with self._lock:
            return {"workers": self.workers, "worker_limit": WORKER_LIMIT, "maintenance": self.maintenance,
                    "maintenance_limit": MAINTENANCE_LIMIT}

    # --- workers ----------------------------------------------------------------------------------
    def start(self, kind: str, snap: Snapshot, work: Callable[[OpContext], Any]) -> Operation:
        deadline = snap.call_deadline_s if kind == "call" else snap.discovery_deadline_s
        op = Operation(self, kind, snap, deadline)
        with self._lock:
            self._expire_locked()
            if self.workers >= WORKER_LIMIT:
                raise Busy()
            self.workers += 1
            self._ops[op.id] = op
        threading.Thread(target=self._run, args=(op, work), daemon=True, name=f"mcp-{kind}").start()
        return op

    def _run(self, op: Operation, work: Callable[[OpContext], Any]) -> None:
        try:
            try:
                value = work(op.ctx)
                committed = {"outcome": getattr(value, "outcome", "ok"), "reason": None, "value": value}
            except Abandoned as exc:
                op.abandon(str(exc))       # a deadline the worker noticed at a gate; otherwise a repeat
                committed = None
            except MCPError as exc:
                committed = {"outcome": exc.outcome, "reason": exc.reason, "error": exc}
            except Exception as exc:  # noqa: BLE001 - reported by class only; never resent
                from .ui.server import report_internal
                report_internal("mcp worker", exc)
                outcome = "error" if op.kind == "discovery" else ("unknown" if op.ctx.call_gate_passed else "rejected")
                committed = {"outcome": outcome, "reason": "malformed",
                             "error": MCPError(outcome, "malformed", f"InternalError: {type(exc).__name__}")}
            if committed is not None:
                with op.cond:
                    if op.ctx.abandoned is None:   # after abandonment the worker queues nothing more
                        op._commit_locked(committed)
        finally:
            self.client.release_all(op.ctx)
            with self._lock:
                self.workers -= 1
                op.ended_at = time.monotonic()
                self._ended.append(op)
                self._expire_locked()

    def _expire_locked(self) -> None:
        now = time.monotonic()
        while self._ended and (len(self._ended) > RETAIN_COUNT or now - self._ended[0].ended_at > RETAIN_S):
            old = self._ended.popleft()
            self._ops.pop(old.id, None)

    def get(self, op_id: object) -> Operation:
        with self._lock:
            self._expire_locked()
            op = self._ops.get(op_id) if isinstance(op_id, str) else None
        if op is None:
            raise NoSuchOperation()
        return op

    def cancel(self, op_id: object) -> dict:
        """POST /api/mcp/cancel: answers within CANCEL_ANSWER_S."""
        op = self.get(op_id)
        op.abandon("stopped")
        op.notice_done.wait(CANCEL_ANSWER_S)
        return op.status()

    # --- maintenance --------------------------------------------------------------------------
    def _take_slot(self) -> bool:
        with self._lock:
            if self.maintenance >= MAINTENANCE_LIMIT:
                return False
            self.maintenance += 1
            return True

    def _free_slot(self) -> None:
        with self._lock:
            self.maintenance -= 1

    def _maintenance_snapshot(self, snap: Snapshot) -> Snapshot:
        return replace(snap, idle_timeout_s=min(float(snap.idle_timeout_s), MAINTENANCE_NETWORK_S))

    def schedule_notice(self, op: Operation, session: Session, request_id: int) -> bool:
        if not self._take_slot():
            return False

        def run() -> None:
            status, reason = "failed", None
            try:
                mctx = OpContext(time.monotonic() + MAINTENANCE_NETWORK_S)
                if self.client.send_notice(mctx, self._maintenance_snapshot(op.snapshot), session, request_id):
                    status = "delivered"
                else:
                    reason = "the server did not acknowledge the cancellation"
            except Abandoned:
                reason = "the cancellation could not be sent within its time limit"
            except MCPError as exc:
                reason = exc.description
            except Exception as exc:  # noqa: BLE001
                reason = f"InternalError: {type(exc).__name__}"
            finally:
                with op.lock:
                    op.cancel_status, op.cancel_reason = status, reason
                self._free_slot()
                op.notice_done.set()
                self.client.release(session)

        threading.Thread(target=run, daemon=True, name="mcp-notice").start()
        return True

    def schedule_delete(self, session: Session) -> None:
        if not self._take_slot():
            self.client.skip_delete(session)
            return
        client = self.client

        class DeleteContext(OpContext):
            def gate(self, *, post: bool = False, **kw) -> None:
                super().gate(**kw)
                if post and not client.delete_gate(session):
                    raise Abandoned("not_eligible")

        def run() -> None:
            mctx = DeleteContext(time.monotonic() + MAINTENANCE_NETWORK_S)
            url, token = session.identity
            snap = Snapshot("(session end)", url, MAINTENANCE_NETWORK_S, MAINTENANCE_NETWORK_S,
                            MAINTENANCE_NETWORK_S, token)
            try:
                client.send_delete(mctx, snap, session)
            except (Abandoned, MCPError):
                pass
            except Exception:  # noqa: BLE001
                pass
            finally:
                if session.delete_sent:
                    client.forget(session)
                else:
                    with client.session_lock:
                        session.delete_scheduled = False
                self._free_slot()

        threading.Thread(target=run, daemon=True, name="mcp-delete").start()
