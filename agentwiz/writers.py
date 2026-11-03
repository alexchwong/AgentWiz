"""The workspace-writer barrier (0.1.4, 10.10; review v3 R3).

One mechanism covers every application-managed writer: a count and a `closing` flag, both protected by the lock of
one condition variable (a leaf lock: nothing else is acquired while it is held, and it is never held across disk I/O
or while waiting for the drain, because `Condition.wait` releases it).

`register` is one decision under that lock: if `closing` is set it refuses; otherwise it counts the writer and returns
the registration, so shutdown can never observe a count of zero while a writer has been accepted but not yet counted.
`Writer.release` decrements once. `Writer.inherit` is for a holder that still holds its own registration (a script
step handing its process to a cleanup owner): it counts even when `closing` is set, which is safe because the count
cannot be zero while the inheriting holder is registered. Knows nothing of HTTP.
"""
from __future__ import annotations

import threading
import time

from .errors import ShuttingDown


class WritersClosed(ShuttingDown):
    """The barrier is closing: a writer that has not been counted is refused."""


class Writer:
    def __init__(self, barrier: "WriterBarrier", key: int, label: str):
        self._barrier = barrier
        self._key = key
        self.label = label
        self._released = False          # guarded by the barrier's lock

    def release(self) -> None:
        self._barrier._release(self)

    def inherit(self, label: str) -> "Writer":
        return self._barrier._add(label, force=True)

    def __enter__(self) -> "Writer":
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


class WriterBarrier:
    def __init__(self) -> None:
        self._cond = threading.Condition(threading.Lock())
        self.closing = False
        self._labels: dict[int, str] = {}
        self._next = 0

    def register(self, label: str) -> Writer:
        return self._add(label, force=False)

    def _add(self, label: str, force: bool) -> Writer:
        with self._cond:
            if self.closing and not force:
                raise WritersClosed()
            self._next += 1
            self._labels[self._next] = label
            return Writer(self, self._next, label)

    def _release(self, writer: Writer) -> None:
        with self._cond:
            if writer._released:
                return
            writer._released = True
            self._labels.pop(writer._key, None)
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self.closing = True

    def count(self) -> int:
        with self._cond:
            return len(self._labels)

    def outstanding(self) -> list[str]:
        with self._cond:
            return sorted(self._labels.values())

    def wait_drained(self, timeout: float) -> bool:
        """Wait, holding no lock between wake-ups, until the count is zero or `timeout` passes."""
        end = time.monotonic() + timeout
        with self._cond:
            while self._labels:
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self._cond.wait(left)
            return True
