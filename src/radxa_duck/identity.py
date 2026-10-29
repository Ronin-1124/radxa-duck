"""One private, revisioned robot identity, pinned at interaction boundaries."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat
import threading
import time
import unicodedata


MAX_REVISION = 2 ** 53 - 1
MAX_BYTES = 32 * 1024
LIMITS = {"name": 40, "description": 2000, "personality": 1000, "expression_style": 1000}
DEFAULT_PROFILE = {"name": "小鸭", "description": "我是 Radxa Duck，一个小鸭子机器人。",
                   "personality": "友善、好奇、诚实", "expression_style": "自然、简洁，使用用户的语言回答"}


class IdentityError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def parse_json(raw):
    return json.loads(raw, object_pairs_hook=unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))


def validate_profile(profile):
    if not isinstance(profile, dict) or set(profile) != set(LIMITS):
        raise IdentityError("invalid_request")
    result = {}
    for field, limit in LIMITS.items():
        value = profile[field]
        if (not isinstance(value, str)
                or any(unicodedata.category(c) in ("Cc", "Cs") and c != "\n" for c in value)):
            raise IdentityError("invalid_request")
        if len(value.encode("utf-16-le")) // 2 > limit:
            raise IdentityError("invalid_request")
        if field == "name" and not value.strip():
            raise IdentityError("invalid_request")
        result[field] = value
    return result


class IdentityStore:
    """No history files. active_revision changes only when a turn takes a copy.

    Without a state directory, defaults are readable but saving is unavailable.
    Corrupt/unsafe storage is reported, never overwritten with defaults.
    """

    def __init__(self, state_dir: Path | None = None):
        self._lock = threading.RLock()
        self._active_revision = 0
        self._saved = {"schema_version": 1, "revision": 0, "profile": deepcopy(DEFAULT_PROFILE)}
        self._dir_fd = None
        self._closed = False
        if state_dir is not None:
            try:
                Path(state_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
                self._dir_fd = os.open(state_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
                self._private(os.fstat(self._dir_fd), directory=True)
                with self._locked():
                    self._saved = self._load()
            except OSError:
                self.close()
                raise IdentityError("config_unavailable") from None
            except BaseException:
                self.close()
                raise

    @staticmethod
    def _private(info, *, directory=False):
        right_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if (not right_type
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077
                or not directory and info.st_nlink != 1):
            raise IdentityError("config_unavailable")

    @contextmanager
    def _locked(self):
        with self._lock:
            if self._closed or self._dir_fd is None:
                raise IdentityError("config_unavailable")
            fd = None
            try:
                fd = os.open("identity.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=self._dir_fd)
                self._private(os.fstat(fd))
                deadline = time.monotonic() + 2
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise IdentityError("config_unavailable") from None
                        time.sleep(0.02)
                yield
            except OSError:
                raise IdentityError("config_unavailable") from None
            finally:
                if fd is not None:
                    os.close(fd)

    def _load(self):
        try:
            fd = os.open("identity.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=self._dir_fd)
        except FileNotFoundError:
            return {"schema_version": 1, "revision": 0, "profile": deepcopy(DEFAULT_PROFILE)}
        try:
            with os.fdopen(fd, "rb") as stream:
                self._private(os.fstat(stream.fileno()))
                raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError()
            saved = parse_json(raw)
            if (not isinstance(saved, dict) or set(saved) != {"schema_version", "revision", "profile"}
                    or type(saved["schema_version"]) is not int or saved["schema_version"] != 1
                    or type(saved["revision"]) is not int or not 1 <= saved["revision"] <= MAX_REVISION):
                raise ValueError()
            validate_profile(saved["profile"])
            return saved
        except (ValueError, UnicodeError, RecursionError, IdentityError):
            raise IdentityError("config_unavailable") from None

    def _public(self):
        return {**deepcopy(self._saved), "type": "identity_config", "active_revision": self._active_revision}

    def snapshot(self):
        with self._lock:
            if self._closed:
                raise IdentityError("config_unavailable")
            if self._dir_fd is not None:
                with self._locked():
                    value = self._load()
                    if value['revision'] < self._active_revision:
                        raise IdentityError("config_unavailable")
                    self._saved = value
            return self._public()

    def summary(self):
        with self._lock:
            return {"name": self._saved["profile"]["name"], "revision": self._saved["revision"],
                    "active_revision": self._active_revision}

    def save(self, *, expected_revision, profile):
        if type(expected_revision) is not int or not 0 <= expected_revision < MAX_REVISION:
            raise IdentityError("invalid_request")
        normalized = validate_profile(profile)
        with self._locked():
            previous = self._load()
            if previous["revision"] != expected_revision:
                raise IdentityError("conflict")
            saved = {"schema_version": 1, "revision": expected_revision + 1, "profile": normalized}
            raw = (json.dumps(saved, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
            temporary = ".identity-" + secrets.token_hex(12)
            try:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=self._dir_fd)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, "identity.json", src_dir_fd=self._dir_fd, dst_dir_fd=self._dir_fd)
                os.fsync(self._dir_fd)
            finally:
                try:
                    os.unlink(temporary, dir_fd=self._dir_fd)
                except FileNotFoundError:
                    pass
            self._saved = saved
            return self._public()

    def for_turn(self):
        with self._lock:
            value = self.snapshot()
            self._active_revision = value["revision"]
            return deepcopy(value["profile"])

    def close(self):
        with self._lock:
            self._closed = True
            if self._dir_fd is not None:
                os.close(self._dir_fd)
                self._dir_fd = None
