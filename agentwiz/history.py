"""Run history: run folders, redaction, listing, reading and deleting saved runs (techspec 0.1.3, 10;
R5-2 to R5-10).

When a run is created with the switch on, `Writer.start_run` writes `runs/<UTC time>-<run_id>/run.json`;
each committed attempt is then written once, redacted, as `request-<n>.json`. Files are never rewritten
and the writer never recreates a folder. Reading is always from disk. Deleting a run is a reservation in
the attempt store, a removal with no lock held through `remove_tree`, and a settlement (Section 10.5).

Nothing here takes the state lock or the store's lock while it touches the disk. Knows nothing of HTTP.
"""
from __future__ import annotations

import datetime
import os
import re
from pathlib import Path
from typing import Any, Callable

import modelspell

from . import __version__, jsonio, runs
from .workspace import write_atomic_bytes

DIRECTORY = "runs"
LIST_LIMIT = 200
FOLDER_RE = re.compile(r"[0-9]{8}-[0-9]{6}-([0-9a-f]{12})\Z")
ATTEMPT_FILE_RE = re.compile(r"request-([1-9][0-9]{0,5})\.json\Z")
MIN_CREDENTIAL = 8
RECORD_VERSION = 1


class RemoveError(Exception):
    """remove_tree failed. `changed` is False only when it knows nothing was removed by this call."""

    def __init__(self, message: str, changed: bool = True):
        super().__init__(message)
        self.changed = changed


class NoSuchRun(Exception):
    pass


class NotDeletable(Exception):
    pass


class DeleteFailed(Exception):
    """AgentWiz's text plus the system's error text."""


class HistoryReadOnly(Exception):
    pass


def before_write() -> None:
    """A hook that does nothing in shipped code. Tests make it block or remove the folder, to force an
    ordering between a commit and its attempt file (techspec 18.1)."""


def _strerror(exc: OSError) -> str:
    return exc.strerror or type(exc).__name__


# --- redaction (10.4) -----------------------------------------------------------------------------------
def redact(value: Any, credentials: list[str]) -> tuple[Any, bool]:
    """A copy of `value` with every credential of at least 8 characters replaced by ***, longest first, in
    every string value. Returns (copy, whether anything was replaced)."""
    secrets_ = sorted({c for c in credentials if isinstance(c, str) and len(c) >= MIN_CREDENTIAL},
                      key=lambda c: (-len(c), c))
    if not secrets_:
        return value, False
    changed = False

    def walk(item: Any) -> Any:
        nonlocal changed
        if isinstance(item, str):
            out = item
            for secret in secrets_:
                if secret in out:
                    out = out.replace(secret, "***")
            if out != item:
                changed = True
            return out
        if isinstance(item, dict):
            return {k: walk(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [walk(v) for v in item]
        return item

    return walk(value), changed


# --- the writer ------------------------------------------------------------------------------------------
def folder_name(run: runs.Run) -> str:
    created = datetime.datetime.fromisoformat(run.created_at.replace("Z", "+00:00"))
    return f"{created.astimezone(datetime.timezone.utc):%Y%m%d-%H%M%S}-{run.run_id}"


class Writer:
    """Writes run folders and attempt files. `credentials()` returns every credential AgentWiz has held."""

    def __init__(self, workspace_path: Path, store: runs.AttemptStore, credentials: Callable[[], list[str]]):
        self.path = Path(workspace_path) / DIRECTORY
        self.workspace_path = Path(workspace_path)
        self.store = store
        self.credentials = credentials

    def _mkdir(self, path: Path) -> None:
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            return
        os.chmod(path, 0o700)

    def start_run(self, run: runs.Run) -> None:
        """Create the run's folder and write run.json (10.2). On failure the run goes on: `history_problem`
        says why, and every attempt's save reports it."""
        try:
            self._mkdir(self.path)
            folder = self.path / folder_name(run)
            os.mkdir(folder, 0o700)
            os.chmod(folder, 0o700)
            payload = {"record_version": RECORD_VERSION, "run_id": run.run_id, "source": run.source,
                       "created_at": run.created_at, "steps": [runs.STEP_ID], "inputs": run.inputs,
                       "contract": run.contract, "agentwiz_version": __version__,
                       "modelspell_version": modelspell.__version__}
            redacted, changed = redact(payload, self.credentials())
            redacted["redacted"] = changed
            write_atomic_bytes(folder, "run.json", jsonio.encode_record_file(redacted))
        except OSError as exc:
            run.history_problem = f"could not create the run folder ({_strerror(exc)})"
            return
        run.folder = folder

    def write_attempt(self, run: runs.Run, record: dict) -> dict:
        """Write one committed attempt, once. Returns {"save_status": "saved", "file": <path relative to the
        workspace>} or {"save_status": "failed", "reason": <text>}. Exactly one write is attempted."""
        state = self.store.history_state(run)
        if state == runs.HISTORY_DELETED:
            return {"save_status": "failed", "reason": "the run's history was deleted"}
        if state == runs.HISTORY_INCOMPLETE:
            return {"save_status": "failed",
                    "reason": "the run's history is incomplete after a failed deletion"}
        if run.history_problem is not None or run.folder is None:
            return {"save_status": "failed", "reason": run.history_problem or "the run folder was not created"}
        before_write()
        folder = run.folder
        if not folder.is_dir():
            return {"save_status": "failed", "reason": "the run's folder no longer exists"}
        name = f"{record['step_id']}-{record['attempt']}.json"
        try:
            payload, changed = redact(record, self.credentials())
            payload = dict(payload)
            payload["redacted"] = changed
            data = jsonio.encode_record_file(payload)
            write_atomic_bytes(folder, name, data)
        except OSError as exc:
            return {"save_status": "failed", "reason": f"could not write {name} ({_strerror(exc)})"}
        except (TypeError, ValueError) as exc:  # a record AgentWiz could not encode
            from .ui.server import report_internal
            report_internal("history", exc)
            return {"save_status": "failed", "reason": f"could not write {name} (the record could not be encoded)"}
        return {"save_status": "saved", "file": f"{DIRECTORY}/{folder.name}/{name}"}

    # --- reading (10.5) -----------------------------------------------------------------------------------
    def _entries(self) -> list[tuple[str, str, bool]]:
        """(folder name, run id, is_symlink) of every entry named like a run folder, newest first."""
        found = []
        try:
            with os.scandir(self.path) as listing:
                for item in listing:
                    match = FOLDER_RE.match(item.name)
                    if match is None:
                        continue
                    try:
                        link = item.is_symlink()
                        if not link and not item.is_dir(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    found.append((item.name, match.group(1), link))
        except OSError:
            return []
        found.sort(reverse=True)
        return found

    @staticmethod
    def _problem(file: str, exc: BaseException) -> dict:
        if isinstance(exc, OSError):
            return {"file": file, "code": "unreadable", "message": f"the file cannot be read ({_strerror(exc)})"}
        return {"file": file, "code": "invalid", "message": str(exc)}

    def _read(self, path: Path) -> dict:
        return jsonio.decode_file(path.read_bytes())

    def list_runs(self) -> dict:
        entries = [e for e in self._entries() if not e[2]]
        shown = []
        for name, run_id, _ in entries[:LIST_LIMIT]:
            folder = self.path / name
            row: dict = {"run_id": run_id, "folder": name, "created_at": None, "source": None, "attempts": 0,
                         "outcome": None, "check": None, "problem": None}
            try:
                meta = self._read(folder / "run.json")
                row["created_at"], row["source"] = meta.get("created_at"), meta.get("source")
            except (OSError, ValueError) as exc:
                row["problem"] = self._problem(str(folder / "run.json"), exc)
            numbers = self._attempt_numbers(folder)
            row["attempts"] = len(numbers)
            if numbers:
                try:
                    latest = self._read(folder / f"{runs.STEP_ID}-{numbers[-1]}.json")
                    row["outcome"] = latest.get("outcome")
                    check = latest.get("check")
                    row["check"] = check.get("overall") if isinstance(check, dict) else None
                except (OSError, ValueError):
                    pass
            shown.append(row)
        return {"runs": shown, "total": len(entries), "shown": len(shown)}

    @staticmethod
    def _attempt_numbers(folder: Path) -> list[int]:
        try:
            names = os.listdir(folder)
        except OSError:
            return []
        return sorted(int(m.group(1)) for n in names if (m := ATTEMPT_FILE_RE.match(n)))

    def read_run(self, run_id: str) -> dict:
        for name, found_id, link in self._entries():
            if found_id == run_id and not link:
                break
        else:
            raise NoSuchRun()
        folder = self.path / name
        problems, run = [], None
        try:
            run = self._read(folder / "run.json")
        except (OSError, ValueError) as exc:
            problems.append(self._problem(str(folder / "run.json"), exc))
        attempts = []
        for number in self._attempt_numbers(folder):
            file = folder / f"{runs.STEP_ID}-{number}.json"
            try:
                attempts.append(self._read(file))
            except (OSError, ValueError) as exc:
                problems.append(self._problem(str(file), exc))
        return {"run": run, "attempts": attempts, "problems": problems}

    # --- deleting (10.5) ----------------------------------------------------------------------------------
    def delete_run(self, run_id: str, *, read_only: bool) -> None:
        """Reservation, removal and settlement. Raises HistoryReadOnly, NoSuchRun, NotDeletable,
        runs.RunActive, DeleteFailed."""
        if read_only:
            raise HistoryReadOnly()
        for name, found_id, link in self._entries():
            if found_id == run_id:
                break
        else:
            raise NoSuchRun()
        folder = self.path / name
        if link or folder.parent != self.path or not FOLDER_RE.match(name):
            raise NotDeletable()
        run = self.store.reserve_delete(run_id)        # RunActive
        ok, changed, text = False, True, ""
        try:
            try:
                remove_tree(folder)
                ok = True
            except RemoveError as exc:
                changed, text = exc.changed, str(exc)
            except Exception as exc:  # noqa: BLE001 - unchanged is never assumed
                from .ui.server import report_internal
                report_internal("history delete", exc)
                changed, text = True, type(exc).__name__
        finally:
            self.store.settle_delete(run, ok=ok, changed=changed)
        if not ok:
            raise DeleteFailed(f"could not delete the run's folder ({text})")


def remove_tree(folder: Path) -> None:
    """Remove a run folder and its contents without following symbolic links. Returns nothing on success.
    Raises RemoveError; `changed` is False only when it failed before removing anything."""
    changed = False
    try:
        if folder.is_symlink() or not folder.is_dir():
            raise RemoveError("the folder is not a plain directory", changed=False)
        for current, dirs, files in os.walk(folder, topdown=False, followlinks=False):
            for name in files:
                os.unlink(os.path.join(current, name))
                changed = True
            for name in dirs:
                path = os.path.join(current, name)
                if os.path.islink(path):
                    os.unlink(path)
                else:
                    os.rmdir(path)
                changed = True
        os.rmdir(folder)
    except RemoveError:
        raise
    except OSError as exc:
        raise RemoveError(_strerror(exc), changed=changed) from None
