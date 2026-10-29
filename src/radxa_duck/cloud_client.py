"""Bounded, cancellable OpenAI-compatible text and single-image streaming.

The adapter deliberately exposes only answer text and a fixed completion record.
Provider metadata, reasoning, tool requests and response bodies are never public
errors. Configuration credentials and image data live only for this operation.
"""

from __future__ import annotations

import base64
from copy import deepcopy
import json
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request

from .cloud_config import CloudConfigError, _NoRedirect


MAX_PROMPT_CHARACTERS = 4000
MAX_HISTORY_MESSAGES = 12
MAX_HISTORY_CHARACTERS = 12000
MAX_OUTPUT_CHARACTERS = 8000
MAX_IMAGE_BYTES = 512 * 1024
MAX_LINE_BYTES = 64 * 1024
MAX_EVENT_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_REQUEST_BYTES = 1024 * 1024
MAX_TOKENS = 1024
MAX_OPEN_TIMEOUT = 5.0
_SYSTEM = ("你运行在 Radxa Duck 机器人上，角色身份以本轮配置为准，使用自然简洁的语言回答用户。只回答文字，"
           "不要调用工具，不要声称已经完成任何身体动作或设备操作。")
_MESSAGES = {
    "invalid_request": "Invalid cloud application input",
    "not_configured": "Save cloud configuration before using cloud applications",
    "config_unavailable": "Cloud configuration is unavailable",
    "conflict": "Cloud configuration changed; refresh before using it",
    "closed": "The cloud client is closed",
    "busy": "Cloud request capacity is occupied",
    "cancelled": "The cloud operation was cancelled",
    "authentication_failed": "The cloud service rejected the API key",
    "permission_denied": "The cloud service denied access",
    "request_rejected": "The cloud service rejected this application request",
    "endpoint_unavailable": "The cloud chat endpoint is unavailable",
    "rate_limited": "The cloud service is rate limited",
    "cloud_unavailable": "The cloud service is unavailable",
    "timeout": "The cloud operation timed out",
    "tls_failed": "The cloud TLS connection failed",
    "network_error": "The cloud service could not be reached",
    "invalid_response": "The cloud service returned an invalid answer stream",
    "incomplete_response": "The cloud answer stream ended before completion",
    "response_too_large": "The cloud answer stream exceeds its size limit",
    "output_limit": "The cloud answer exceeds its text limit",
    "response_refused": "The cloud service could not provide an answer",
    "unsupported_response": "The cloud answer requested unsupported tool operations",
    "empty_response": "The cloud service returned no answer text",
}


class CloudClientError(RuntimeError):
    def __init__(self, code: str):
        self.code = code if code in _MESSAGES else "cloud_unavailable"
        super().__init__(_MESSAGES[self.code])


def _text(value, limit, *, nonempty=True):
    if (not isinstance(value, str) or len(value) > limit or nonempty and not value.strip()
            or any(ord(char) < 32 and char not in "\n\r\t" or ord(char) == 127
                   or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
        raise CloudClientError("invalid_request")
    return value


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise CloudClientError("invalid_response") from None


class _Lifetime:
    """Interrupt a blocked HTTP body without waiting for its read timeout."""

    def __init__(self, cancelled, timeout, clock):
        self.cancelled = cancelled
        self._local_cancelled = threading.Event()
        self.finished = threading.Event()
        self.clock = clock
        self.deadline = clock() + timeout
        self.response = None
        self._lock = threading.Lock()
        self.thread = threading.Thread(target=self._watch, name="duck-cloud-cancel", daemon=True)

    def check(self):
        if self.cancelled.is_set() or self._local_cancelled.is_set():
            raise CloudClientError("cancelled")
        if self.clock() >= self.deadline:
            raise CloudClientError("timeout")

    def cancel(self):
        self._local_cancelled.set()

    def bind(self, response):
        with self._lock:
            self.response = response
        self.check()

    def _interrupt(self):
        with self._lock:
            response = self.response
        if response is None:
            return
        # urllib's HTTPResponse.close alone may wait for a buffered reader's
        # lock. Shut down its socket first, which wakes the blocked read.
        try:
            raw = getattr(getattr(response, "fp", None), "raw", None)
            sock = getattr(raw, "_sock", None)
            if sock is not None:
                sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            response.close()
        except Exception:
            pass

    def _watch(self):
        while not self.finished.wait(0.025):
            try:
                self.check()
            except CloudClientError:
                self._interrupt()
                return

    def close(self):
        self.finished.set()
        self._interrupt()
        if self.thread.ident is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=0.5)
        with self._lock:
            self.response = None


def _events(response, lifetime):
    """Decode SSE with limits before buffering a whole line or event."""
    buffer = bytearray()
    total = event_size = 0
    data, event = [], ""
    first = True
    read = getattr(response, "read1", response.read)
    while True:
        lifetime.check()
        chunk = read(4096)
        lifetime.check()
        if not isinstance(chunk, bytes):
            raise CloudClientError("invalid_response")
        if not chunk:
            # SSE data not followed by an empty line is not a committed event.
            if buffer or data:
                raise CloudClientError("incomplete_response")
            return
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise CloudClientError("response_too_large")
        buffer.extend(chunk)
        while True:
            newline = buffer.find(b"\n")
            if newline < 0:
                if len(buffer) > MAX_LINE_BYTES:
                    raise CloudClientError("response_too_large")
                break
            if newline > MAX_LINE_BYTES:
                raise CloudClientError("response_too_large")
            raw = bytes(buffer[:newline])
            del buffer[:newline + 1]
            if raw.endswith(b"\r"):
                raw = raw[:-1]
            if first:
                raw = raw.removeprefix(b"\xef\xbb\xbf")
                first = False
            try:
                line = raw.decode("utf-8", errors="strict")
            except UnicodeError:
                raise CloudClientError("invalid_response") from None
            if not line:
                if data:
                    yield event, "\n".join(data)
                data, event, event_size = [], "", 0
                continue
            if line.startswith(":"):
                continue
            field, separator, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "data":
                event_size += len(raw) + 1
                if event_size > MAX_EVENT_BYTES:
                    raise CloudClientError("response_too_large")
                data.append(value if separator else "")
            elif field == "event":
                if len(value) > 64:
                    raise CloudClientError("invalid_response")
                event = value


class CloudClient:
    def __init__(self, store, *, opener=None, clock=time.monotonic):
        self._store = store
        self._opener = opener if opener is not None else urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect())
        self._clock = clock
        self._lock = threading.Lock()
        self._active = set()
        self._closed = False

    @staticmethod
    def _request(saved, prompt, image_jpeg, history, system_prompt=None):
        _text(prompt, MAX_PROMPT_CHARACTERS)
        if not isinstance(history, (tuple, list)) or len(history) > MAX_HISTORY_MESSAGES:
            raise CloudClientError("invalid_request")
        system = _SYSTEM
        if system_prompt is not None:
            system += "\n" + _text(system_prompt, 16000)
        messages = [{"role": "system", "content": system}]
        size = 0
        for item in history:
            if (not isinstance(item, dict) or set(item) != {"role", "content"}
                    or item["role"] not in ("user", "assistant")):
                raise CloudClientError("invalid_request")
            limit = MAX_OUTPUT_CHARACTERS if item["role"] == "assistant" else MAX_PROMPT_CHARACTERS
            content = _text(item["content"], limit)
            size += len(content)
            if size > MAX_HISTORY_CHARACTERS:
                raise CloudClientError("invalid_request")
            messages.append({"role": item["role"], "content": content})
        content = prompt
        if image_jpeg is not None:
            if (not isinstance(image_jpeg, bytes) or not 8 <= len(image_jpeg) <= MAX_IMAGE_BYTES
                    or not image_jpeg.startswith(b"\xff\xd8") or not image_jpeg.endswith(b"\xff\xd9")):
                raise CloudClientError("invalid_request")
            content = [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(image_jpeg).decode("ascii"), "detail": "low"}}]
        messages.append({"role": "user", "content": content})
        payload = json.dumps({"model": saved["model"], "messages": messages, "stream": True,
                              "max_tokens": MAX_TOKENS}, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(payload) > MAX_REQUEST_BYTES:
            raise CloudClientError("invalid_request")
        headers = {"Accept": "text/event-stream", "Content-Type": "application/json", "User-Agent": "radxa-duck"}
        if saved["api_key"] is not None:
            headers["Authorization"] = "Bearer " + saved["api_key"]
        return urllib.request.Request(saved["base_url"] + "/chat/completions", data=payload,
                                      headers=headers, method="POST")

    @staticmethod
    def _status(status):
        return {400: "request_rejected", 401: "authentication_failed", 403: "permission_denied",
                404: "endpoint_unavailable", 429: "rate_limited"}.get(status, "cloud_unavailable")

    def pin_config(self, *, expected_revision=None):
        """Private per-turn configuration. Never return this from a route."""
        try:
            return self._store.runtime_snapshot(expected_revision=expected_revision)
        except CloudConfigError as exc:
            raise CloudClientError(exc.code) from None

    def stream(self, prompt: str, *, cancelled: threading.Event, image_jpeg: bytes | None = None,
               history: tuple = (), expected_revision: int | None = None,
               system_prompt: str | None = None, runtime_config: dict | None = None):
        if not isinstance(cancelled, threading.Event):
            raise CloudClientError("invalid_request")
        if cancelled.is_set():
            raise CloudClientError("cancelled")
        saved = self.pin_config(expected_revision=expected_revision) if runtime_config is None else deepcopy(runtime_config)
        if expected_revision is not None and saved["revision"] != expected_revision:
            raise CloudClientError("conflict")
        request = self._request(saved, prompt, image_jpeg, history, system_prompt)
        lifetime = _Lifetime(cancelled, saved["timeout_seconds"], self._clock)
        with self._lock:
            if self._closed:
                raise CloudClientError("closed")
            if len(self._active) >= 2:
                raise CloudClientError("busy")
            self._active.add(lifetime)
        # Drop the extra configuration copy as soon as headers are constructed.
        revision = saved["revision"]
        open_timeout = min(MAX_OPEN_TIMEOUT, saved["timeout_seconds"])
        saved = None
        completed = None
        try:
            lifetime.check()
            lifetime.thread.start()
            response = self._opener.open(request, timeout=open_timeout)
            lifetime.bind(response)
            if not 200 <= response.status < 300:
                raise CloudClientError(self._status(response.status))
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "text/event-stream":
                raise CloudClientError("invalid_response")
            finish_reason, output_length = None, 0
            for event, raw in _events(response, lifetime):
                lifetime.check()
                if event == "error":
                    raise CloudClientError("cloud_unavailable")
                if event not in ("", "message"):
                    raise CloudClientError("invalid_response")
                if raw == "[DONE]":
                    if finish_reason is None:
                        raise CloudClientError("incomplete_response")
                    if not output_length:
                        raise CloudClientError("empty_response")
                    completed = {"type": "done", "revision": revision, "finish_reason": finish_reason,
                                 "output_characters": output_length}
                    break
                payload = _json(raw)
                if not isinstance(payload, dict):
                    raise CloudClientError("invalid_response")
                if "error" in payload:
                    raise CloudClientError("cloud_unavailable")
                choices = payload.get("choices")
                if choices == [] and isinstance(payload.get("usage"), dict):
                    continue
                if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                    raise CloudClientError("invalid_response")
                choice = choices[0]
                if type(choice.get("index")) is not int or choice["index"] != 0:
                    raise CloudClientError("invalid_response")
                delta = choice.get("delta")
                if not isinstance(delta, dict):
                    raise CloudClientError("invalid_response")
                if delta.get("role") not in (None, "assistant"):
                    raise CloudClientError("invalid_response")
                if delta.get("refusal"):
                    raise CloudClientError("response_refused")
                if delta.get("tool_calls") or delta.get("function_call"):
                    raise CloudClientError("unsupported_response")
                text = delta.get("content")
                if text is not None and not isinstance(text, str):
                    raise CloudClientError("invalid_response")
                reason = choice.get("finish_reason")
                if finish_reason is not None and (text or reason is not None):
                    raise CloudClientError("invalid_response")
                if reason is not None:
                    if reason == "content_filter":
                        raise CloudClientError("response_refused")
                    if reason in ("tool_calls", "function_call"):
                        raise CloudClientError("unsupported_response")
                    if reason not in ("stop", "length"):
                        raise CloudClientError("invalid_response")
                    finish_reason = reason
                if text:
                    if output_length + len(text) > MAX_OUTPUT_CHARACTERS:
                        raise CloudClientError("output_limit")
                    try:
                        _text(text, MAX_OUTPUT_CHARACTERS, nonempty=False)
                    except CloudClientError:
                        raise CloudClientError("invalid_response") from None
                    output_length += len(text)
                    lifetime.check()
                    yield {"type": "delta", "text": text}
            if completed is None:
                raise CloudClientError("incomplete_response")
        except urllib.error.HTTPError as exc:
            code = self._status(exc.code)
            try:
                exc.close()
            except Exception:
                pass
            lifetime.check()
            raise CloudClientError(code) from None
        except (TimeoutError, socket.timeout):
            lifetime.check()
            raise CloudClientError("timeout") from None
        except ssl.SSLError:
            lifetime.check()
            raise CloudClientError("tls_failed") from None
        except urllib.error.URLError as exc:
            lifetime.check()
            code = ("timeout" if isinstance(exc.reason, (TimeoutError, socket.timeout)) else
                    "tls_failed" if isinstance(exc.reason, ssl.SSLError) else "network_error")
            raise CloudClientError(code) from None
        except CloudClientError:
            raise
        except Exception:
            lifetime.check()
            raise CloudClientError("network_error") from None
        finally:
            lifetime.close()
            with self._lock:
                self._active.discard(lifetime)
            # A consumer may stop at the done record without advancing the
            # generator again. Do not retain credentials or the JPEG there.
            request = image_jpeg = response = None
            runtime_config = system_prompt = None
            prompt, history, raw, payload, text = "", (), "", None, None
        # Release the transport before a consumer observes completion.
        yield completed

    def close(self):
        with self._lock:
            self._closed = True
            for lifetime in self._active:
                lifetime.cancel()
