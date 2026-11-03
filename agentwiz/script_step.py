"""Script steps: run one Python file as a block step, supervised by one owner (0.1.4, Section 11.4).

`[sys.executable, <absolute path>]` runs in a new session and process group, with an allow-listed environment,
the block's inputs as one JSON object on standard input, and the working folder set to a scratch folder. One
supervisor, in the step's own thread, owns every pipe: it is the only code that reads, writes or closes them until
a hand-over to a cleanup owner (`Retained`). Each pipe object stays the sole close owner of its descriptor; the
supervisor never closes a descriptor by number, never wraps or duplicates one, and closes an object exactly once.

Every deadline here is a `make_timer` timer whose callback only sets a flag or sends a signal, under the step lock
and only while the step still owns the process, so a late callback never signals a reaped or handed-over group.
Messages name rules, never output. Knows nothing of HTTP. POSIX only (process groups).
"""
from __future__ import annotations

import hashlib
import os
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from . import jsonio
from .errors import report_internal

KILL_GRACE_S = 2.0              # SIGTERM -> SIGKILL, and each bounded wait after it
POLL_S = 0.05                   # the supervisor's pass
RETAINED_POLL_S = 1.0           # a cleanup owner's round
OUTPUT_LIMIT = 32 * 1024 * 1024
STDERR_TAIL = 1024 * 1024
ENV_ALLOWED = ("PATH", "HOME", "LANG", "TMPDIR")


# --- hooks and boundaries tests replace ------------------------------------------------------------------------
def after_spawn(pgid: int) -> None:
    """Does nothing in shipped code; tests learn the script's process group here (or make it raise)."""


def spawn(argv: list[str], **kwargs: Any):
    """The boundary to the operating system: `subprocess.Popen` in shipped code."""
    return subprocess.Popen(argv, **kwargs)


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def send_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


# --- shared registry (kept by ProcessState) ----------------------------------------------------------------------
class Registry:
    """The cleanup owners and the running script steps of one process state, found by shutdown (10.10).
    One leaf lock guards both sets; nothing is acquired while it is held."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cleanups: set = set()
        self._active: set = set()

    def add_cleanup(self, owner: "Retained") -> None:
        with self._lock:
            self._cleanups.add(owner)

    def remove_cleanup(self, owner: "Retained") -> None:
        with self._lock:
            self._cleanups.discard(owner)

    def cleanups(self) -> list:
        with self._lock:
            return list(self._cleanups)

    def add_active(self, step: Any) -> None:
        with self._lock:
            self._active.add(step)

    def remove_active(self, step: Any) -> None:
        with self._lock:
            self._active.discard(step)

    def kill_all(self) -> None:
        """SIGKILL every script group still owned, by a step or a cleanup owner (10.10, step 4)."""
        with self._lock:
            targets = list(self._active) + list(self._cleanups)
        for target in targets:
            try:
                target.kill_now()
            except Exception as exc:  # noqa: BLE001 - the others must still be killed
                report_internal("script kill", exc)

    def service_idle(self) -> None:
        """One round for every cleanup owner whose own thread is not alive (10.10, step 5)."""
        for owner in self.cleanups():
            if not owner.thread_alive():
                try:
                    owner.service()
                except Exception as exc:  # noqa: BLE001
                    report_internal("script cleanup", exc)


@dataclass
class Env:
    """What a script step needs from its run and its process state."""
    make_timer: Callable[[float, Callable[[], None]], Any]
    registry: Registry
    writer: Any                                # the run's workspace-writer registration (for inherit)
    release_slot: Callable[[], None]           # releases the script slot, once
    protect: Callable[[], None]                # protects the run's folder from deletion
    unprotect: Callable[[], None]
    on_started: Callable[[], None] | None = None   # told once the process exists (the observer's `operation` event)


class Handle:
    """Lets the runner see, even after an exception, whether the slot moved to a cleanup owner."""

    def __init__(self) -> None:
        self.retained = False
        self.owner: "Retained | None" = None


# --- the cleanup owner -------------------------------------------------------------------------------------------
class Retained:
    """Owns a script's process, pipes, group, slot, scratch folder, folder protection and writer registration
    after the step could not confirm that everything ended (11.4, step 7)."""

    def __init__(self, proc: Any, pipes: Mapping[str, Any], pgid: int, env: Env, writer: Any,
                 scratch_temp: Path | None, label: str):
        self.proc = proc
        self.pipes = {role: obj for role, obj in pipes.items() if obj is not None and not obj.closed}
        self.pgid = pgid
        self.env = env
        self.writer = writer
        self.scratch_temp = scratch_temp
        self.label = label
        self._lock = threading.Lock()
        self.finished = False
        self._thread: threading.Thread | None = None
        self.start_failed = False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="script-cleanup")
        self._thread.start()

    def thread_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def kill_now(self) -> None:
        if not self.finished:
            send_group(self.pgid, signal.SIGKILL)

    def _loop(self) -> None:
        while True:
            try:
                if self.service():
                    return
            except Exception as exc:  # noqa: BLE001 - reported, and the next round runs
                report_internal("script cleanup", exc)
            time.sleep(RETAINED_POLL_S)

    def service(self) -> bool:
        """One bounded round; True once everything has ended and been released."""
        if not self._lock.acquire(blocking=False):
            return False
        try:
            if self.finished:
                return True
            returncode = self.proc.poll()
            if group_alive(self.pgid):
                send_group(self.pgid, signal.SIGKILL)
            stdin = self.pipes.get("stdin")
            if stdin is not None:
                self._close(stdin, "stdin")
            for role in ("stdout", "stderr"):
                obj = self.pipes.get(role)
                if obj is not None:
                    self._drain(obj, role)
            if returncode is None or group_alive(self.pgid):
                return False
            for role in list(self.pipes):
                self._close(self.pipes[role], role)
            if self.scratch_temp is not None:
                remove_scratch(self.scratch_temp)
            self.finished = True
            self.env.registry.remove_cleanup(self)
            self.env.unprotect()
            self.writer.release()
            self.env.release_slot()
            return True
        finally:
            self._lock.release()

    def _close(self, obj: Any, role: str) -> None:
        try:
            obj.close()
        except Exception as exc:  # noqa: BLE001 - the object counts as closed
            report_internal("script cleanup pipe", exc)
        self.pipes.pop(role, None)

    def _drain(self, obj: Any, role: str) -> None:
        try:
            fd = obj.fileno()
            os.set_blocking(fd, False)
            while True:
                try:
                    data = os.read(fd, 65536)
                except BlockingIOError:
                    return
                if not data:
                    self._close(obj, role)
                    return
        except OSError as exc:
            report_internal("script cleanup pipe", exc)
            self._close(obj, role)


def remove_scratch(folder: Path) -> None:
    try:
        shutil.rmtree(folder)
    except FileNotFoundError:
        pass
    except OSError as exc:
        report_internal("scratch folder", exc)


# --- the step -----------------------------------------------------------------------------------------------------
def _environment(run_id: str, block_name: str) -> dict:
    env = {name: os.environ[name] for name in ENV_ALLOWED if name in os.environ}
    env.update({name: value for name, value in os.environ.items() if name.startswith("LC_")})
    env["AGENTWIZ_RUN_ID"] = run_id
    env["AGENTWIZ_BLOCK"] = block_name
    return env


def _now() -> str:
    from .runs import utc_now
    return utc_now()


class _Step:
    def __init__(self, path: Path, values: Mapping, *, step_id: str, run_folder: Path | None, timeout_s: int,
                 token: Any, run_id: str, block_name: str, sha256: str, outputs: tuple[str, ...], file: str,
                 env: Env, handle: Handle):
        self.path, self.values, self.step_id, self.run_folder = path, values, step_id, run_folder
        self.timeout_s, self.token, self.run_id, self.block_name = timeout_s, token, run_id, block_name
        self.sha256, self.outputs, self.file, self.env, self.handle = sha256, outputs, file, env, handle
        self.lock = threading.Lock()                 # the step lock
        self.owned = False
        self.handed_over = False
        self.never = False                           # cancellation won the dispatch gate
        self.stopped = False                         # the stop action terminated a running script
        self.proc: Any = None
        self.pgid: int | None = None
        self.timers: list = []
        self.sel: selectors.BaseSelector | None = None
        self.pipes: dict[str, Any] = {}
        self.term_sent = False
        self.kill_sent = False
        self.flag_timeout = False
        self.flag_handover = False
        self.flag_drain = False
        self.timed_out = False
        self.oversize = False
        self.group_cleaned = False
        self.pipes_held = False
        self.cleanup = "complete"
        self.cleanup_reason: str | None = None
        self.scratch: Path | None = None
        self.scratch_temp: Path | None = None
        self.stdout = bytearray()
        self.stderr = bytearray()
        self.stderr_truncated = False
        self.unsent = b""
        self.started_at = _now()
        self.error: BaseException | None = None

    # --- timers, signals (callbacks act only while the step owns the process) ---
    def _timer(self, seconds: float, callback: Callable[[], None]) -> None:
        self.timers.append(self.env.make_timer(seconds, callback))

    def _cancel_timers(self) -> None:
        timers, self.timers = self.timers, []
        for timer in timers:
            try:
                timer.cancel()
            except Exception as exc:  # noqa: BLE001
                report_internal("script timer", exc)

    def _terminate_locked(self) -> None:
        """SIGTERM to the group, once, and arm the SIGKILL escalation. Caller holds the step lock and owns."""
        if self.term_sent:
            return
        self.term_sent = True
        send_group(self.pgid, signal.SIGTERM)
        self._timer(KILL_GRACE_S, self._on_kill)

    def _on_kill(self) -> None:
        with self.lock:
            if not self.owned or self.kill_sent:
                return
            self.kill_sent = True
            send_group(self.pgid, signal.SIGKILL)
            self._timer(KILL_GRACE_S, self._on_handover)

    def _on_handover(self) -> None:
        with self.lock:
            if self.owned:
                self.flag_handover = True

    def _on_timeout(self) -> None:
        with self.lock:
            if not self.owned or self.proc.poll() is not None:
                return
            self.timed_out = True
            self._terminate_locked()

    def _on_drain(self) -> None:
        with self.lock:
            if self.owned:
                self.flag_drain = True

    def _stop_action(self) -> None:
        """The run's stop action: decide "never launch" or terminate the running script, under the step lock."""
        with self.lock:
            if self.proc is None:
                self.never = True
                return
            if not self.owned or self.proc.poll() is not None:
                return                                # it exited on its own: an accepted result survives
            self.stopped = True
            self._terminate_locked()

    def kill_now(self) -> None:
        with self.lock:
            if self.owned and self.pgid is not None:
                self.term_sent = self.kill_sent = True
                send_group(self.pgid, signal.SIGKILL)

    # --- the whole step ---
    def run(self) -> dict:
        self.token.on_cancel(self._stop_action)
        if not hasattr(os, "killpg"):
            return self._rejected("script steps need an operating system with process groups")
        digest = _file_sha256(self.path)
        if digest != self.sha256:
            return self._rejected("the script file changed after the run was admitted")
        try:
            self._make_scratch()
        except OSError as exc:
            return self._rejected(f"could not create the scratch folder ({exc.strerror or type(exc).__name__})")
        raised: BaseException | None = None
        try:
            with self.lock:
                if self.never:
                    self._remove_temp_scratch()
                    return self._finish(None, never=True)
                try:
                    self.proc = spawn([sys.executable, str(self.path)], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
                                      cwd=str(self.scratch), env=_environment(self.run_id, self.block_name),
                                      start_new_session=True)
                except OSError as exc:
                    self._remove_temp_scratch()
                    return self._rejected(f"could not start the script ({exc.strerror or type(exc).__name__})",
                                          scratch_done=True)
                self.pgid = self.proc.pid
                self.owned = True
                self.pipes = {"stdin": self.proc.stdin, "stdout": self.proc.stdout, "stderr": self.proc.stderr}
            self.env.registry.add_active(self)
            try:
                if self.env.on_started is not None:
                    try:
                        self.env.on_started()
                    except Exception as exc:  # noqa: BLE001 - delivery never affects the step
                        report_internal("script step", exc)
                after_spawn(self.pgid)
                self._supervise()
            except BaseException as exc:  # noqa: BLE001 - teardown runs, then the error propagates
                raised = exc
                self.error = exc
        finally:
            if self.proc is not None:
                self._teardown()
                self.env.registry.remove_active(self)
        if raised is not None:
            raise raised
        return self._finish(self._exit_info())

    # --- scratch ---
    def _make_scratch(self) -> None:
        if self.run_folder is not None:
            parent = self.run_folder / "scratch"
            try:
                os.mkdir(parent, 0o700)
            except FileExistsError:
                pass
            folder = parent / self.step_id
            try:
                os.mkdir(folder, 0o700)
            except FileExistsError:
                pass
            self.scratch = folder
        else:
            self.scratch = Path(tempfile.mkdtemp(prefix="agentwiz-scratch-"))
            self.scratch_temp = self.scratch

    def _remove_temp_scratch(self) -> None:
        if self.scratch_temp is not None:
            remove_scratch(self.scratch_temp)
            self.scratch_temp = None

    # --- supervision (the step's own thread; the only code touching the pipes) ---
    def _close_pipe(self, role: str) -> None:
        obj = self.pipes.get(role)
        if obj is None:
            return
        if self.sel is not None:
            try:
                self.sel.unregister(obj.fileno())
            except (KeyError, ValueError, OSError):
                pass
        try:
            obj.close()
        except Exception as exc:  # noqa: BLE001 - the object counts as closed
            report_internal("script pipe", exc)
        self.pipes[role] = None

    def _open_roles(self) -> list[str]:
        return [role for role, obj in self.pipes.items() if obj is not None and not obj.closed]

    def _supervise(self) -> None:
        payload = jsonio.encode_exact(dict(self.values)).encode("utf-8")
        self.unsent = payload
        self.sel = selectors.DefaultSelector()
        for role in ("stdin", "stdout", "stderr"):
            os.set_blocking(self.pipes[role].fileno(), False)
        if self.unsent:
            self.sel.register(self.pipes["stdin"].fileno(), selectors.EVENT_WRITE, "stdin")
        else:
            self._close_pipe("stdin")
        self.sel.register(self.pipes["stdout"].fileno(), selectors.EVENT_READ, "stdout")
        self.sel.register(self.pipes["stderr"].fileno(), selectors.EVENT_READ, "stderr")
        with self.lock:
            self._timer(self.timeout_s, self._on_timeout)
        drain_armed = False
        while True:
            if self.sel.get_map():
                ready = self.sel.select(POLL_S)
            else:
                time.sleep(POLL_S)
                ready = []
            for key, _mask in ready:
                self._service(key.data)
            if self.oversize:
                with self.lock:
                    if self.owned:
                        self._terminate_locked()
            if self.proc.poll() is None:
                if self._handover_due():
                    return self._handover("the process did not exit after SIGKILL")
                continue
            if group_alive(self.pgid):
                with self.lock:
                    if self.owned and not self.term_sent:
                        self.group_cleaned = True
                        self._terminate_locked()
                if self._handover_due():
                    return self._handover("the process group did not end after SIGKILL")
                continue
            if not drain_armed:
                drain_armed = True
                with self.lock:
                    self._timer(KILL_GRACE_S, self._on_drain)
            if not self._open_roles() or not self.sel.get_map():
                return
            if self.flag_drain:
                self.pipes_held = True
                return

    def _handover_due(self) -> bool:
        with self.lock:
            return self.flag_handover

    def _service(self, role: str) -> None:
        obj = self.pipes.get(role)
        if obj is None or obj.closed:
            return
        fd = obj.fileno()
        if role == "stdin":
            try:
                written = os.write(fd, self.unsent[:65536])
            except BlockingIOError:
                return
            except (BrokenPipeError, OSError):
                self._close_pipe("stdin")
                return
            self.unsent = self.unsent[written:]
            if not self.unsent:
                self._close_pipe("stdin")
            return
        try:
            data = os.read(fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            self._close_pipe(role)
            return
        if not data:
            self._close_pipe(role)
            return
        if role == "stdout":
            if len(self.stdout) <= OUTPUT_LIMIT:
                self.stdout.extend(data[:OUTPUT_LIMIT + 1 - len(self.stdout)])
            if len(self.stdout) > OUTPUT_LIMIT:
                self.oversize = True
        else:
            self.stderr.extend(data)
            if len(self.stderr) > STDERR_TAIL:
                del self.stderr[:len(self.stderr) - STDERR_TAIL]
                self.stderr_truncated = True

    # --- hand-over (step 7) ---
    def _handover(self, reason: str) -> None:
        self.env.protect()
        with self.lock:
            if self.handed_over:
                return
            label = f"cleanup owner of run {self.run_id}, process group {self.pgid}"
            if self.sel is not None:
                try:
                    self.sel.close()
                except Exception as exc:  # noqa: BLE001
                    report_internal("script selector", exc)
                self.sel = None
            owner = Retained(self.proc, self.pipes, self.pgid, self.env, self.env.writer.inherit(label),
                             self.scratch_temp, label)
            self.env.registry.add_cleanup(owner)
            self.handed_over = True
            self.owned = False
            self.cleanup, self.cleanup_reason = "retained", reason
            self.handle.retained, self.handle.owner = True, owner
            self._cancel_timers()
        print(f"[AgentWiz] run {self.run_id}: script cleanup could not be confirmed; process group {self.pgid} "
              "is held by a cleanup owner", file=sys.stderr, flush=True)
        try:
            owner.start()
        except BaseException as exc:  # noqa: BLE001 - shutdown services an owner that never started
            owner.start_failed = True
            report_internal("script cleanup start", exc)

    # --- teardown: two exclusive terminal paths (step 6) ---
    def _teardown(self) -> None:
        with self.lock:
            handed = self.handed_over
        if handed:
            return                                     # (a) the owner has everything
        confirmed = self.proc.poll() is not None and not group_alive(self.pgid)
        if not confirmed:                              # (c) an error while the process or its group may live
            with self.lock:
                if self.owned:
                    self._terminate_locked()
            while True:
                if self.proc.poll() is not None and not group_alive(self.pgid):
                    break
                if self._handover_due():
                    self._handover("the process or its group did not end during teardown")
                    return
                time.sleep(POLL_S)
        # (b) ended and confirmed
        self._cancel_timers()
        for role in list(self.pipes):
            self._close_pipe(role)
        if self.sel is not None:
            try:
                self.sel.close()
            except Exception as exc:  # noqa: BLE001
                report_internal("script selector", exc)
            self.sel = None
        self._remove_temp_scratch()
        with self.lock:
            self.owned = False
            self.cleanup = "complete"

    # --- results ---
    def _exit_info(self) -> dict:
        return {"returncode": getattr(self.proc, "returncode", None)}

    def _operation(self, outcome: str, reason: str | None, **extra: Any) -> dict:
        record = {"file": self.file, "sha256": self.sha256, "outcome": outcome, "reason": reason, "exit_code": None,
                  "signal": None, "group_cleaned": self.group_cleaned, "cleanup": self.cleanup,
                  "pipes_held": self.pipes_held,
                  "stderr_tail": bytes(self.stderr).decode("utf-8", errors="replace"),
                  "stderr_truncated": self.stderr_truncated, "started_at": self.started_at, "ended_at": _now()}
        if self.cleanup_reason:
            record["cleanup_reason"] = self.cleanup_reason
        record.update(extra)
        return record

    def _rejected(self, reason: str, scratch_done: bool = False) -> dict:
        if not scratch_done:
            self._remove_temp_scratch()
        return {"operation": self._operation("rejected", reason), "values": None, "stopped": False, "never": False}

    def _finish(self, info: dict | None, never: bool = False) -> dict:
        if never:
            return {"operation": self._operation("stopped", "cancelled before the script was started"),
                    "values": None, "stopped": True, "never": True}
        code = info["returncode"] if info else None
        exit_code = code if code is not None and code >= 0 else None
        sig = -code if code is not None and code < 0 else None
        values = None
        if self.stopped:
            outcome, reason = "stopped", "terminated by the stop action"
        elif self.timed_out:
            outcome, reason = "timeout", f"the script ran longer than {self.timeout_s} seconds"
        elif self.oversize:
            outcome, reason = "script_error", "the output is larger than 32 MiB"
        elif self.cleanup == "retained":
            outcome, reason = "script_error", "cleanup could not be confirmed"
        elif sig is not None:
            outcome, reason = "script_error", f"the script was ended by signal {sig}"
        elif exit_code != 0:
            outcome, reason = "script_error", f"the script exited with status {exit_code}"
        else:
            outcome, reason, values = self._read_output()
        record = self._operation(outcome, reason, exit_code=exit_code, signal=sig)
        return {"operation": record, "values": values if outcome == "ok" else None,
                "stopped": self.stopped, "never": False}

    def _read_output(self) -> tuple[str, str | None, dict | None]:
        try:
            text = bytes(self.stdout).decode("utf-8")
        except UnicodeDecodeError:
            return "script_error", "the output is not valid UTF-8", None
        try:
            value = jsonio.decode_exact(text)
        except ValueError:
            return "script_error", "the output is not one JSON value", None
        if not isinstance(value, dict):
            return "script_error", "the output is not a JSON object", None
        missing = [name for name in self.outputs if name not in value]
        extra = [name for name in value if name not in self.outputs]
        if missing:
            return "script_error", "the output lacks a declared output: " + ", ".join(missing), None
        if extra:
            return "script_error", "the output holds a key that is not a declared output", None
        return "ok", None, value


def _file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def run(path: Path, values: Mapping, *, step_id: str, run_folder: Path | None, timeout_s: int, token: Any,
        run_id: str, block_name: str, sha256: str, outputs: tuple[str, ...], file: str, env: Env,
        handle: Handle) -> dict:
    """Run one script step. Returns `{"operation", "values", "stopped", "never"}`: the operation record (11.4),
    the parsed output object when `ok`, whether the stop action ended it, and whether cancellation won the
    dispatch gate. Raises what went wrong in the supervisor after teardown (the runner commits `internal_error`).
    `handle.retained` says whether the slot moved to a cleanup owner."""
    return _Step(Path(path), values, step_id=step_id, run_folder=run_folder, timeout_s=timeout_s, token=token,
                 run_id=run_id, block_name=block_name, sha256=sha256, outputs=outputs, file=file, env=env,
                 handle=handle).run()
