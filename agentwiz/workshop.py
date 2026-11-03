"""Bounded Workshop operation identities. No HTTP, filesystem or dispatch ownership.

The lock is C6, a leaf. Callers prepare and check an action before claiming its
unused ticket. A duplicate must be resolved before repeating those checks.
"""
from __future__ import annotations

import copy
import hashlib
import re
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from . import jsonio

TICKET_LIMIT = 128
TICKET_TTL = 600
OPERATION_LIMIT = 100
OPERATION_TTL = 600
TOKEN = re.compile(r"[0-9a-f]{32}\Z")


class WorkshopError(Exception):
    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass
class Operation:
    payload_digest: str
    status: dict
    completed_at: float | None = None


class Operations:
    """Process-local single-use tickets, with bounded completed-result retention.

    Active operations are never evicted. Their separate admission limits bound
    their count. Unknown IDs always refuse dispatch, including after eviction.
    """
    def __init__(self):
        self.lock = threading.Lock()
        self.unused: dict[str, float] = {}
        self.operations: OrderedDict[str, Operation] = OrderedDict()
        self.closed = False

    def _expire(self, now: float) -> None:
        self.unused = {key: end for key, end in self.unused.items() if end > now}
        expired = [key for key, op in self.operations.items()
                   if op.completed_at is not None and now - op.completed_at >= OPERATION_TTL]
        for key in expired:
            del self.operations[key]
        completed = [key for key, op in self.operations.items() if op.completed_at is not None]
        for key in completed[:-OPERATION_LIMIT]:
            del self.operations[key]

    def issue(self) -> dict:
        now = time.monotonic()
        ticket = secrets.token_hex(16)
        with self.lock:
            self._expire(now)
            if self.closed:
                raise WorkshopError("busy", 503)
            if len(self.unused) >= TICKET_LIMIT:
                raise WorkshopError("busy", 503)
            self.unused[ticket] = now + TICKET_TTL
        return {"format_version": 1, "ticket": ticket, "expires_in": TICKET_TTL}

    @staticmethod
    def digest(payload: dict) -> str:
        def ordered(value):
            if isinstance(value, dict):
                return {k: ordered(value[k]) for k in sorted(value)}
            if isinstance(value, list):
                return [ordered(v) for v in value]
            return value
        return hashlib.sha256(jsonio.encode_exact(ordered(payload)).encode("utf-8")).hexdigest()

    @staticmethod
    def validate(ticket: object) -> str:
        if not isinstance(ticket, str) or TOKEN.fullmatch(ticket) is None:
            raise WorkshopError("invalid", 400)
        return ticket

    def lookup(self, ticket: object, payload: dict | None = None) -> dict:
        key = self.validate(ticket)
        digest = self.digest(payload) if payload is not None else None
        with self.lock:
            self._expire(time.monotonic())
            operation = self.operations.get(key)
            if operation is not None:
                if digest is not None and digest != operation.payload_digest:
                    raise WorkshopError("ticket_conflict", 409)
                result = copy.deepcopy(operation.status)
            elif key in self.unused:
                result = {"state": "unused"}
            else:
                raise WorkshopError("ticket_expired", 410)
        return {"format_version": 1, "ticket": key, **result}

    def claim(self, ticket: object, payload: dict, operation_id: str) -> tuple[dict, bool]:
        """After action preflight, atomically reserve dispatch. False means duplicate.

        The caller must publish a terminal failure if dispatch/admission fails.
        No callback, state method or I/O is invoked while C6 is held.
        """
        key = self.validate(ticket)
        digest = self.digest(payload)
        with self.lock:
            self._expire(time.monotonic())
            previous = self.operations.get(key)
            if previous is not None:
                if digest != previous.payload_digest:
                    raise WorkshopError("ticket_conflict", 409)
                return copy.deepcopy(previous.status), False
            if key not in self.unused:
                raise WorkshopError("ticket_expired", 410)
            if self.closed:
                raise WorkshopError("busy", 503)
            del self.unused[key]
            status = {"state": "accepted", "operation_id": operation_id}
            self.operations[key] = Operation(digest, status)
            return dict(status), True

    def finish(self, ticket: str, status: dict) -> None:
        retained = copy.deepcopy(status)
        with self.lock:
            operation = self.operations.get(ticket)
            if operation is None or operation.completed_at is not None:
                return
            operation.status = {**retained, "state": "terminal",
                                "operation_id": operation.status["operation_id"]}
            operation.completed_at = time.monotonic()
            self._expire(operation.completed_at)

    def close(self) -> None:
        with self.lock:
            self.closed = True
