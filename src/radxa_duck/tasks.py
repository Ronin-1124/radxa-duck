"""Bounded background operations with private, restartable status history.

Only task metadata and explicitly returned JSON results are journaled. Callables,
their arguments, exclusive keys and cancellation events remain in memory.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import queue
import re
import secrets
import stat
import threading
import time
import uuid


MAX_RESULT_BYTES = 16 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_HISTORY = 256
_ACTIVE = {"queued", "running"}
_TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z", re.ASCII)
_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{8,64}\Z", re.ASCII)
_TASK_ID = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_FIELDS = {"schema_version", "task_id", "kind", "request_id", "state", "phase", "progress",
           "created_utc", "updated_utc", "result", "error"}
_FAILED_MESSAGE = "The operation failed"


class TaskManagerError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class TaskFailure(RuntimeError):
    """An operation's deliberately public error and optionally redacted result."""

    def __init__(self, code: str, safe_message: str, result=None):
        if not _identifier(code) or not _message(safe_message):
            code, safe_message = "operation_failed", _FAILED_MESSAGE
        super().__init__(safe_message)
        self.code, self.result = code, result


class TaskCancelled(RuntimeError):
    def __init__(self):
        super().__init__("The operation was cancelled")


def _identifier(value):
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


def _message(value):
    return isinstance(value, str) and 1 <= len(value) <= 256 and not any(
        ord(char) < 32 or ord(char) == 127 for char in value)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _result(value):
    """Copy finite JSON without constructing an unbounded serialized result."""
    try:
        pending, visited = [(value, 0)], 0
        while pending:
            item, depth = pending.pop()
            visited += 1
            if depth > 32 or visited > MAX_RESULT_BYTES:
                raise ValueError()
            if type(item) is dict:
                if len(item) > MAX_RESULT_BYTES or any(type(key) is not str or len(key) > MAX_RESULT_BYTES for key in item):
                    raise ValueError()
                pending.extend((child, depth + 1) for child in item.values())
            elif type(item) is list:
                if len(item) > MAX_RESULT_BYTES:
                    raise ValueError()
                pending.extend((child, depth + 1) for child in item)
            elif type(item) is str:
                if len(item) > MAX_RESULT_BYTES:
                    raise ValueError()
            elif item is not None and type(item) not in (bool, int, float):
                raise ValueError()
        encoder = json.JSONEncoder(ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        chunks, length = [], 0
        for chunk in encoder.iterencode(value):
            length += len(chunk)
            if length > MAX_RESULT_BYTES:
                raise ValueError()
            chunks.append(chunk)
        return json.loads("".join(chunks))
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise TaskFailure("invalid_result", "The operation returned invalid or oversized task data") from None


class TaskContext:
    def __init__(self, manager, task_id):
        self.task_id = task_id
        self.cancelled = threading.Event()
        self._manager = manager

    def update(self, phase: str, progress: int | None = None):
        self._manager._update(self.task_id, phase, progress)

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise TaskCancelled()


class TaskManager:
    def __init__(self, state_dir: Path, max_workers=2, history_limit=32):
        if type(max_workers) is not int or not 1 <= max_workers <= 16:
            raise TaskManagerError("invalid_request", "max_workers must be an integer from 1 to 16")
        if type(history_limit) is not int or not 1 <= history_limit <= MAX_HISTORY:
            raise TaskManagerError("invalid_request", "history_limit must be an integer from 1 to 256")
        self._lock = threading.RLock()
        self._queue = queue.Queue()
        self._records, self._active, self._keys = {}, {}, {}
        self._history_limit = history_limit
        self._closing = self._closed = self._fault = False
        self._workers = []
        self._alive = 0
        self._last_content = None
        self._dir_fd = self._lock_fd = None
        try:
            directory = Path(state_dir)
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            self._private(os.fstat(self._dir_fd), directory=True)
            self._lock_fd = os.open("tasks.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                                    0o600, dir_fd=self._dir_fd)
            self._private(os.fstat(self._lock_fd))
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._last_content = self._read()
            self._records = self._load(self._last_content)
            changed = False
            for record in self._records.values():
                if record["state"] in _ACTIVE:
                    record.update(state="interrupted", updated_utc=_now(), result=None,
                                  error={"code": "interrupted", "message": "The service stopped before the operation finished"})
                    changed = True
            changed |= self._prune(self._records, self._history_limit)
            if changed:
                self._persist(self._records)
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError, TaskManagerError, TaskFailure):
            self._release()
            raise TaskManagerError("tasks_unavailable", "Task history is unavailable or already in use") from None
        # Daemon workers allow a timed close even if an operation ignores its
        # cancellation event. Its lifetime file lock remains held until exit.
        try:
            for index in range(max_workers):
                worker = threading.Thread(target=self._worker, name=f"radxa-task-{index}", daemon=True)
                self._alive += 1
                try:
                    worker.start()
                except BaseException:
                    self._alive -= 1
                    raise
                self._workers.append(worker)
        except BaseException:
            self.close(timeout=0)
            raise TaskManagerError("tasks_unavailable", "Task workers could not be started") from None

    @staticmethod
    def _private(info, *, directory=False):
        valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if (not valid_type or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077
                or not directory and info.st_nlink != 1):
            raise TaskManagerError("tasks_unavailable", "Task history requires private user-owned storage")

    def _read(self):
        try:
            fd = os.open("tasks.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=self._dir_fd)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            self._private(info)
            if info.st_size > MAX_FILE_BYTES:
                raise ValueError()
            content = stream.read(MAX_FILE_BYTES + 1)
            if len(content) > MAX_FILE_BYTES:
                raise ValueError()
            return content

    @staticmethod
    def _load(content):
        if content is None:
            return {}
        saved = json.loads(content)
        if (not isinstance(saved, dict) or set(saved) != {"schema_version", "tasks"}
                or type(saved["schema_version"]) is not int or saved["schema_version"] != 1
                or not isinstance(saved["tasks"], list) or len(saved["tasks"]) > MAX_HISTORY):
            raise ValueError()
        result, requests = {}, set()
        for row in saved["tasks"]:
            if (not isinstance(row, dict) or set(row) != _FIELDS or type(row["schema_version"]) is not int
                    or row["schema_version"] != 1 or not isinstance(row["task_id"], str)
                    or _TASK_ID.fullmatch(row["task_id"]) is None or row["task_id"] in result
                    or not _identifier(row["kind"]) or not isinstance(row["request_id"], str)
                    or _REQUEST_ID.fullmatch(row["request_id"]) is None or row["request_id"] in requests
                    or row["state"] not in _ACTIVE | _TERMINAL or not _identifier(row["phase"])
                    or row["progress"] is not None and (type(row["progress"]) is not int
                                                       or not 0 <= row["progress"] <= 100)):
                raise ValueError()
            created, updated = datetime.fromisoformat(row["created_utc"]), datetime.fromisoformat(row["updated_utc"])
            if created.tzinfo != timezone.utc or updated.tzinfo != timezone.utc or updated < created:
                raise ValueError()
            error = row["error"]
            if error is not None and (not isinstance(error, dict) or set(error) != {"code", "message"}
                                      or not _identifier(error["code"]) or not _message(error["message"])):
                raise ValueError()
            if ((row["state"] in _ACTIVE | {"succeeded"} and error is not None)
                    or row["state"] in {"failed", "cancelled", "interrupted"} and error is None
                    or row["state"] in _ACTIVE and row["result"] is not None):
                raise ValueError()
            _result(row["result"])
            result[row["task_id"]] = row
            requests.add(row["request_id"])
        return result

    @staticmethod
    def _prune(records, limit):
        changed = False
        for task_id in list(records):
            if len(records) <= limit:
                break
            if records[task_id]["state"] in _TERMINAL:
                del records[task_id]
                changed = True
        return changed

    def _persist(self, records):
        temporary = None
        try:
            if self._fault or self._read() != self._last_content:
                raise ValueError()
            content = (json.dumps({"schema_version": 1, "tasks": list(records.values())}, ensure_ascii=True,
                                  allow_nan=False, separators=(",", ":")) + "\n").encode("ascii")
            if len(content) > MAX_FILE_BYTES:
                raise ValueError()
            for _ in range(20):
                candidate = ".tasks-" + secrets.token_hex(8)
                try:
                    fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 0o600, dir_fd=self._dir_fd)
                    temporary = candidate
                    break
                except FileExistsError:
                    continue
            else:
                raise OSError()
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, "tasks.json", src_dir_fd=self._dir_fd, dst_dir_fd=self._dir_fd)
            temporary = None
            os.fsync(self._dir_fd)
            self._last_content = content
        except (OSError, ValueError, TypeError, RecursionError, TaskManagerError):
            self._fault = True
            for context in self._active.values():
                context.cancelled.set()
            raise TaskManagerError("tasks_unavailable", "Task history could not be saved") from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary, dir_fd=self._dir_fd)
                except OSError:
                    pass

    def _open(self):
        if self._closing or self._closed:
            raise TaskManagerError("closed", "Task manager is closed")

    def submit(self, kind, operation, *, request_id: str, key: str):
        if (not _identifier(kind) or not callable(operation) or not isinstance(request_id, str)
                or _REQUEST_ID.fullmatch(request_id) is None or not isinstance(key, str)
                or not 1 <= len(key) <= 128 or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in key)):
            raise TaskManagerError("invalid_request", "Invalid task kind, operation, request_id or exclusive key")
        with self._lock:
            self._open()
            if self._fault:
                raise TaskManagerError("tasks_unavailable", "Task history is unavailable")
            for record in self._records.values():
                if record["request_id"] == request_id:
                    if record["kind"] != kind:
                        raise TaskManagerError("conflict", "The request_id already belongs to another operation")
                    return deepcopy(record)
            if key in self._keys:
                raise TaskManagerError("conflict", "Another operation is using this device resource")
            records = deepcopy(self._records)
            self._prune(records, self._history_limit - 1)
            if len(records) >= self._history_limit:
                raise TaskManagerError("conflict", "Task capacity is occupied by active operations")
            task_id, timestamp = uuid.uuid4().hex, _now()
            record = {"schema_version": 1, "task_id": task_id, "kind": kind, "request_id": request_id,
                      "state": "queued", "phase": "queued", "progress": None, "created_utc": timestamp,
                      "updated_utc": timestamp, "result": None, "error": None}
            records[task_id] = record
            self._persist(records)
            self._records = records
            context = TaskContext(self, task_id)
            self._active[task_id], self._keys[key] = context, task_id
            self._queue.put((task_id, key, operation, context))
            return deepcopy(record)

    def get(self, task_id):
        if not isinstance(task_id, str) or _TASK_ID.fullmatch(task_id) is None:
            raise TaskManagerError("invalid_request", "task_id must be a UUID hexadecimal string")
        with self._lock:
            self._open()
            if task_id not in self._records:
                raise TaskManagerError("not_found", "The task is no longer available")
            return deepcopy(self._records[task_id])

    def list(self):
        with self._lock:
            self._open()
            return deepcopy(list(reversed(self._records.values())))

    def _update(self, task_id, phase, progress):
        if not _identifier(phase) or progress is not None and (type(progress) is not int or not 0 <= progress <= 100):
            raise TaskManagerError("invalid_request", "Task phase or progress is invalid")
        with self._lock:
            if self._closed or task_id not in self._active:
                raise TaskCancelled()
            if self._fault:
                raise TaskManagerError("tasks_unavailable", "Task history is unavailable")
            records = deepcopy(self._records)
            records[task_id].update(phase=phase, progress=progress, updated_utc=_now())
            self._persist(records)
            self._records = records

    def _finish(self, task_id, key, state, result=None, error=None):
        with self._lock:
            if task_id not in self._active:
                return
            records = deepcopy(self._records)
            row = records[task_id]
            row.update(state=state, result=result, error=error, updated_utc=_now())
            if state == "succeeded":
                row["progress"] = 100
            if not self._closed:
                try:
                    self._persist(records)
                except TaskManagerError:
                    row.update(state="failed", result=None, error={"code": "tasks_unavailable",
                                                                  "message": "Task completion could not be saved"})
                self._records = records
            self._active.pop(task_id, None)
            if self._keys.get(key) == task_id:
                del self._keys[key]

    def _worker(self):
        try:
            while True:
                item = self._queue.get()
                if item is None:
                    return
                task_id, key, operation, context = item
                state, result, error = "succeeded", None, None
                try:
                    context.check_cancelled()
                    with self._lock:
                        if task_id not in self._active or self._closed:
                            raise TaskCancelled()
                        records = deepcopy(self._records)
                        records[task_id].update(state="running", phase="running", updated_utc=_now())
                        self._persist(records)
                        self._records = records
                    result = _result(operation(context))
                except TaskCancelled:
                    state, error = "cancelled", {"code": "cancelled", "message": "The operation was cancelled"}
                    result = None
                except TaskFailure as failure:
                    # Validate again at the boundary: exception attributes can
                    # have been changed since construction by operation code.
                    message = failure.args[0] if len(failure.args) == 1 else None
                    failure_code = getattr(failure, "code", None)
                    error = ({"code": failure_code, "message": message}
                             if _identifier(failure_code) and _message(message) else
                             {"code": "operation_failed", "message": _FAILED_MESSAGE})
                    state = "cancelled" if error["code"] == "cancelled" else "failed"
                    try:
                        result = _result(getattr(failure, "result", None))
                    except TaskFailure:
                        state, result = "failed", None
                        error = {"code": "invalid_result", "message": "The operation returned invalid or oversized task data"}
                except BaseException:
                    # Never serialize exception repr, traceback, arguments or
                    # transport response bodies into persistent task history.
                    state, result = "failed", None
                    error = {"code": "operation_failed", "message": _FAILED_MESSAGE}
                self._finish(task_id, key, state, result, error)
                # Release the callable and any captured credentials while idle.
                del item, operation, context
        finally:
            with self._lock:
                self._alive -= 1
                if self._closing and not self._alive:
                    self._release()

    def close(self, timeout=10):
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout < 0):
            raise TaskManagerError("invalid_request", "close timeout must be a finite nonnegative number")
        with self._lock:
            if self._closed or self._closing:
                return
            self._closing = True
            records = deepcopy(self._records)
            changed = False
            for task_id, context in self._active.items():
                context.cancelled.set()
                if records[task_id]["state"] == "queued":
                    records[task_id].update(state="cancelled", updated_utc=_now(),
                                           error={"code": "cancelled", "message": "The operation was cancelled"})
                    changed = True
            if changed:
                try:
                    self._persist(records)
                except TaskManagerError:
                    pass
                self._records = records
            for _ in self._workers:
                self._queue.put(None)
        deadline = time.monotonic() + timeout
        for worker in self._workers:
            if worker is not threading.current_thread():
                worker.join(max(0, deadline - time.monotonic()))
        with self._lock:
            records, changed = deepcopy(self._records), False
            for row in records.values():
                if row["state"] in _ACTIVE:
                    row.update(state="interrupted", updated_utc=_now(), result=None,
                               error={"code": "interrupted", "message": "The operation did not stop before service shutdown"})
                    changed = True
            if changed:
                try:
                    self._persist(records)
                except TaskManagerError:
                    pass
                self._records = records
            self._closed = True
            if not self._alive:
                self._release()

    def _release(self):
        for name in ("_lock_fd", "_dir_fd"):
            fd = getattr(self, name, None)
            if fd is not None:
                os.close(fd)
                setattr(self, name, None)
