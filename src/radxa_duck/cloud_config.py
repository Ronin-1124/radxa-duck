"""Private, revisioned cloud configuration and bounded OpenAI-compatible checks.

Only redacted snapshots leave the store. A connection check uses the saved URL
and never follows redirects or includes response bodies in public errors.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


MAX_FILE_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
MAX_REVISION = 2 ** 53 - 1
_FIELDS = {"provider", "base_url", "model", "timeout_seconds", "api_key"}
_MESSAGES = {
    "authentication_failed": "The cloud service rejected the API key",
    "permission_denied": "The cloud service denied access",
    "rate_limited": "The cloud service is rate limited",
    "endpoint_unavailable": "The cloud models endpoint is unavailable",
    "cloud_unavailable": "The cloud service is unavailable",
    "timeout": "The cloud connection timed out",
    "tls_failed": "The cloud TLS connection failed",
    "network_error": "The cloud service could not be reached",
    "invalid_response": "The cloud service returned an invalid models response",
    "response_too_large": "The cloud models response exceeds its size limit",
    "model_unavailable": "The configured model was not listed by the cloud service",
}


class CloudConfigError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class CloudConfigStore:
    def __init__(self, state_dir: Path, *, allow_local_http: bool = False, opener=None):
        self._allow_local_http = allow_local_http
        self._thread_lock = threading.RLock()
        self._opener = opener if opener is not None else urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect())
        self._directory = Path(state_dir)
        try:
            self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._dir_fd = os.open(self._directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            self._check_private(os.fstat(self._dir_fd), directory=True)
        except (OSError, CloudConfigError):
            if hasattr(self, "_dir_fd"):
                os.close(self._dir_fd)
                del self._dir_fd
            raise CloudConfigError("config_unavailable", "Cloud configuration directory is unavailable") from None
        # Validate an existing file before accepting writes. A broken private
        # configuration is never silently replaced by defaults.
        try:
            with self._locked():
                self._load()
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _check_private(info, *, directory=False):
        right_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not right_type or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise CloudConfigError("config_unavailable", "Cloud configuration requires private user-owned storage")
        if not directory and info.st_nlink != 1:
            raise CloudConfigError("config_unavailable", "Cloud configuration file has unsafe links")

    @contextmanager
    def _locked(self):
        with self._thread_lock:
            if not hasattr(self, "_dir_fd"):
                raise CloudConfigError("closed", "Cloud configuration store is closed")
            try:
                fd = os.open("cloud-config.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=self._dir_fd)
                self._check_private(os.fstat(fd))
            except (OSError, CloudConfigError):
                if "fd" in locals():
                    os.close(fd)
                raise CloudConfigError("config_unavailable", "Cloud configuration lock is unavailable") from None
            try:
                expires = None
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        now = time.monotonic()
                        if expires is None:
                            expires = now + 2
                        elif now >= expires:
                            raise CloudConfigError("config_unavailable", "Cloud configuration is busy") from None
                        time.sleep(0.02)
                    except OSError:
                        raise CloudConfigError("config_unavailable", "Cloud configuration lock is unavailable") from None
                yield
            finally:
                # Closing the independently opened descriptor releases flock,
                # including when loading or writing the file fails.
                os.close(fd)

    @staticmethod
    def _defaults():
        return {"schema_version": 1, "revision": 0, "provider": "openai-compatible", "base_url": None,
                "model": None, "timeout_seconds": 8, "api_key": None, "updated_utc": None}

    def _base_url(self, value):
        if not isinstance(value, str) or not 1 <= len(value) <= 2048:
            raise CloudConfigError("invalid_request", "base_url must contain 1 to 2048 characters")
        if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value) or any(
                char in value for char in ("?", "#", "\\")):
            raise CloudConfigError("invalid_request", "base_url must not contain whitespace, query or fragment")
        try:
            parsed = urllib.parse.urlsplit(value)
            hostname, port = parsed.hostname, parsed.port
            if (parsed.scheme not in ("https", "http") or not hostname or "@" in parsed.netloc
                    or parsed.query or parsed.fragment or port is not None and not 1 <= port <= 65535):
                raise ValueError()
            try:
                address = ipaddress.ip_address(hostname)
                local = address.is_loopback
                host = f"[{address}]" if address.version == 6 else str(address)
            except ValueError:
                host = hostname.encode("idna").decode("ascii").lower()
                if not host or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-." for char in host):
                    raise ValueError()
                local = host in ("localhost", "localhost.")
            if parsed.scheme == "http" and not (self._allow_local_http and local):
                raise CloudConfigError("invalid_request", "HTTPS is required for the cloud service")
            authority = host + (f":{port}" if port is not None else "")
            return urllib.parse.urlunsplit((parsed.scheme, authority, parsed.path.rstrip("/"), "", ""))
        except CloudConfigError:
            raise
        except (ValueError, UnicodeError):
            raise CloudConfigError("invalid_request", "base_url must be an HTTP(S) URL without credentials") from None

    def _normalize(self, config, previous):
        if not isinstance(config, dict) or set(config) - _FIELDS:
            raise CloudConfigError("invalid_request", "Unknown cloud configuration fields")
        result = {**previous, **config}
        if result["provider"] != "openai-compatible":
            raise CloudConfigError("invalid_request", "Unsupported cloud provider protocol")
        result["base_url"] = self._base_url(result["base_url"])
        name = result["model"]
        if (not isinstance(name, str) or not 1 <= len(name) <= 128
                or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in name)):
            raise CloudConfigError("invalid_request", "model must be 1 to 128 characters without whitespace")
        if type(result["timeout_seconds"]) is not int or not 1 <= result["timeout_seconds"] <= 120:
            raise CloudConfigError("invalid_request", "timeout_seconds must be an integer from 1 to 120")
        key = result["api_key"]
        if key is not None and (not isinstance(key, str) or not 1 <= len(key) <= 4096
                                or any(not 33 <= ord(char) <= 126 for char in key)):
            raise CloudConfigError("invalid_request", "api_key must be a nonempty ASCII token or null")
        return result

    def _load(self):
        try:
            fd = os.open("cloud-config.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=self._dir_fd)
        except FileNotFoundError:
            return self._defaults()
        except OSError:
            raise CloudConfigError("config_unavailable", "Cloud configuration cannot be read") from None
        try:
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                self._check_private(info)
                if info.st_size > MAX_FILE_BYTES:
                    raise ValueError()
                content = stream.read(MAX_FILE_BYTES + 1)
                if len(content) > MAX_FILE_BYTES:
                    raise ValueError()
            saved = json.loads(content)
            if (not isinstance(saved, dict) or set(saved) != set(self._defaults())
                    or type(saved["schema_version"]) is not int or saved["schema_version"] != 1
                    or type(saved["revision"]) is not int or not 1 <= saved["revision"] <= MAX_REVISION
                    or not isinstance(saved["updated_utc"], str) or len(saved["updated_utc"]) > 64):
                raise ValueError()
            timestamp = datetime.fromisoformat(saved["updated_utc"])
            if timestamp.tzinfo != timezone.utc:
                raise ValueError()
            result = self._normalize({key: saved[key] for key in _FIELDS}, saved)
            if result != saved:
                raise ValueError()
            return saved
        except (OSError, ValueError, UnicodeError, RecursionError, CloudConfigError):
            raise CloudConfigError("config_unavailable", "Cloud configuration is invalid or not private") from None

    @staticmethod
    def _public(saved):
        return {"schema_version": 1, "revision": saved["revision"], "configured": saved["revision"] > 0,
                "provider": saved["provider"], "base_url": saved["base_url"], "model": saved["model"],
                "timeout_seconds": saved["timeout_seconds"], "has_api_key": saved["api_key"] is not None,
                "updated_utc": saved["updated_utc"]}

    def snapshot(self):
        with self._locked():
            return self._public(self._load())

    def save(self, config: dict, *, expected_revision: int):
        if type(expected_revision) is not int or not 0 <= expected_revision < MAX_REVISION:
            raise CloudConfigError("invalid_request", "expected_revision must be a nonnegative integer")
        with self._locked():
            previous = self._load()
            if previous["revision"] != expected_revision:
                raise CloudConfigError("conflict", "Cloud configuration changed; refresh before saving")
            saved = self._normalize(config, previous)
            saved["revision"] = expected_revision + 1
            saved["updated_utc"] = datetime.now(timezone.utc).isoformat()
            content = (json.dumps(saved, ensure_ascii=True) + "\n").encode("utf-8")
            if len(content) > MAX_FILE_BYTES:
                raise CloudConfigError("invalid_request", "Cloud configuration exceeds its encoded size limit")
            temporary = None
            try:
                # Create within the verified directory using its fd, so a
                # renamed parent cannot redirect a write to a different path.
                for _ in range(20):
                    candidate = ".cloud-config-" + secrets.token_hex(8)
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
                os.replace(temporary, "cloud-config.json", src_dir_fd=self._dir_fd, dst_dir_fd=self._dir_fd)
                temporary = None
                os.fsync(self._dir_fd)
            except OSError:
                raise CloudConfigError("config_unavailable", "Cloud configuration could not be saved") from None
            finally:
                if temporary is not None:
                    try:
                        os.unlink(temporary, dir_fd=self._dir_fd)
                    except OSError:
                        pass
            return self._public(saved)

    def test_connection(self, *, expected_revision: int | None = None):
        with self._locked():
            saved = self._load()
            if not saved["revision"]:
                raise CloudConfigError("not_configured", "Save cloud configuration before testing")
            if expected_revision is not None and (type(expected_revision) is not int
                                                   or expected_revision != saved["revision"]):
                raise CloudConfigError("conflict", "Cloud configuration changed; refresh before testing")
            # No lock is held while doing network I/O. Report the tested
            # revision even if a different browser saves a new configuration.
            saved = deepcopy(saved)
        started = time.monotonic()
        code, available = None, None
        headers = {"Accept": "application/json", "User-Agent": "radxa-duck"}
        if saved["api_key"] is not None:
            headers["Authorization"] = "Bearer " + saved["api_key"]
        request = urllib.request.Request(saved["base_url"] + "/models", headers=headers, method="GET")
        try:
            with self._opener.open(request, timeout=saved["timeout_seconds"]) as response:
                status = response.status
                if not 200 <= status < 300:
                    code = self._status_error(status)
                else:
                    chunks, size = [], 0
                    read = getattr(response, "read1", response.read)
                    while size <= MAX_RESPONSE_BYTES:
                        if time.monotonic() - started >= saved["timeout_seconds"]:
                            raise TimeoutError()
                        chunk = read(min(4096, MAX_RESPONSE_BYTES + 1 - size))
                        if time.monotonic() - started >= saved["timeout_seconds"]:
                            raise TimeoutError()
                        if not chunk:
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                    raw = b"".join(chunks)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        code = "response_too_large"
                    else:
                        payload = json.loads(raw)
                        if (not isinstance(payload, dict) or not isinstance(payload.get("data"), list)
                                or not all(isinstance(row, dict) and isinstance(row.get("id"), str)
                                           for row in payload["data"])):
                            code = "invalid_response"
                        else:
                            available = any(row["id"] == saved["model"] for row in payload["data"])
                            if not available:
                                code = "model_unavailable"
        except urllib.error.HTTPError as error:
            code = self._status_error(error.code)
            error.close()
        except (TimeoutError, socket.timeout):
            code = "timeout"
        except ssl.SSLError:
            code = "tls_failed"
        except urllib.error.URLError as error:
            code = ("timeout" if isinstance(error.reason, (TimeoutError, socket.timeout)) else
                    "tls_failed" if isinstance(error.reason, ssl.SSLError) else "network_error")
        except (json.JSONDecodeError, UnicodeError, ValueError, RecursionError):
            code = "invalid_response"
        except OSError:
            code = "network_error"
        except Exception:
            # Custom transports and native errors must not leak credentials or
            # provider responses through an exception traceback to the caller.
            code = "network_error"
        return {"schema_version": 1, "revision": saved["revision"], "state": "failed" if code else "succeeded",
                "elapsed_ms": (time.monotonic() - started) * 1000,
                "error": {"code": code, "message": _MESSAGES[code]} if code else None,
                "model_available": available}

    @staticmethod
    def _status_error(status):
        return ({401: "authentication_failed", 403: "permission_denied", 404: "endpoint_unavailable",
                 429: "rate_limited"}.get(status, "cloud_unavailable"))

    def close(self):
        with self._thread_lock:
            if hasattr(self, "_dir_fd"):
                os.close(self._dir_fd)
                del self._dir_fd
