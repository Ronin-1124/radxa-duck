"""Cloud adapters with temporary private stores, fake streams and loopback HTTP."""

from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error

from radxa_duck.cloud_client import CloudClient, CloudClientError, MAX_IMAGE_BYTES, MAX_TOKENS
from radxa_duck.cloud_config import CloudConfigStore


KEY = "private-test-key-never-real"
CONFIG = {"base_url": "https://cloud.example/v1", "model": "example-model", "api_key": KEY,
          "timeout_seconds": 8}
JPEG = b"\xff\xd8private-frame\xff\xd9"


def event(content=None, *, reason=None, delta=None):
    values = {} if delta is None else dict(delta)
    if content is not None:
        values["content"] = content
    data = {"choices": [{"index": 0, "delta": values, "finish_reason": reason}]}
    return b"data: " + json.dumps(data, ensure_ascii=False).encode() + b"\n\n"


def done(content="你好", reason="stop"):
    return event(content) + event(reason=reason) + b"data: [DONE]\n\n"


class Response(io.BytesIO):
    def __init__(self, value=None, *, status=200, content_type="text/event-stream"):
        super().__init__(done() if value is None else value)
        self.status = status
        self.headers = {"Content-Type": content_type}


class SplitResponse(Response):
    def __init__(self, value, size=1):
        super().__init__()
        self._value, self._offset, self._size = value, 0, size

    def read1(self, size):
        result = self._value[self._offset:self._offset + min(size, self._size)]
        self._offset += len(result)
        return result


class BlockingResponse(Response):
    def __init__(self):
        super().__init__(b"")
        self.entered = threading.Event()
        self.release = threading.Event()

    def read1(self, size):
        self.entered.set()
        if not self.release.wait(2):
            raise AssertionError("Unreleased test response")
        return b""

    def close(self):
        self.release.set()
        super().close()


class Opener:
    def __init__(self, response=None, error=None):
        self.response = response if response is not None else Response()
        self.error = error
        self.calls = []

    def open(self, request, *, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        return self.response


class CloudClientTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = CloudConfigStore(self.root)
        self.addCleanup(self.store.close)
        self.store.save(CONFIG, expected_revision=0)
        self.cancelled = threading.Event()

    def client(self, **kwargs):
        value = CloudClient(self.store, **kwargs)
        self.addCleanup(value.close)
        return value

    def collect(self, opener=None, **kwargs):
        client = self.client(opener=opener if opener is not None else Opener())
        return list(client.stream("你好", cancelled=self.cancelled, **kwargs))

    def test_json_decisions_use_explicit_json_mode_without_enabling_tools(self):
        opener = Opener(Response(done('{"reply":"你好","sound":"greet","look":false}')))
        result = self.collect(opener, json_mode=True)
        payload = json.loads(opener.calls[0][0].data)
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        self.assertNotIn('tools', payload)
        self.assertEqual(result[-1]['type'], 'done')
        self.error('invalid_request', lambda: self.collect(json_mode='true'))

    def test_invalid_application_answer_does_not_remain_verified(self):
        client = self.client(opener=Opener())
        list(client.stream('你好', cancelled=self.cancelled))
        self.assertEqual(client.feedback()['state'], 'succeeded')
        client.reject_answer('invalid_response', revision=0)
        self.assertEqual(client.feedback()['state'], 'succeeded')
        client.reject_answer('invalid_response', revision=1)
        self.assertEqual(client.feedback()['state'], 'failed')
        self.assertEqual(client.feedback()['error_code'], 'invalid_response')

    def error(self, code, operation):
        with self.assertRaises(CloudClientError) as raised:
            operation()
        self.assertEqual(raised.exception.code, code)
        self.assertNotIn(KEY, repr(raised.exception))
        self.assertNotIn("cloud.example", repr(raised.exception))
        return raised.exception

    def test_text_stream_uses_saved_credentials_bounded_request_and_redacted_events(self):
        response = Response(event(delta={"role": "assistant", "reasoning_content": "private reasoning"})
                            + event("你好") + event("，朋友") + event(reason="stop")
                            + b'data: {"choices":[],"usage":{"completion_tokens":10}}\n\n'
                            + b"data: [DONE]\n\n")
        opener = Opener(response)
        result = self.collect(opener)
        self.assertEqual(result, [{"type": "delta", "text": "你好"}, {"type": "delta", "text": "，朋友"},
                                  {"type": "done", "revision": 1, "finish_reason": "stop", "output_characters": 5}])
        self.assertTrue(response.closed)
        request, timeout = opener.calls[0]
        self.assertEqual(request.full_url, CONFIG["base_url"] + "/chat/completions")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + KEY)
        self.assertEqual(request.get_header("Accept"), "text/event-stream")
        self.assertLessEqual(timeout, 5)
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "example-model")
        self.assertEqual(payload["max_tokens"], MAX_TOKENS)
        self.assertTrue(payload["stream"])
        self.assertNotIn("tools", payload)
        self.assertEqual(payload["messages"][-1], {"role": "user", "content": "你好"})
        self.assertNotIn(KEY, repr(result))
        self.assertNotIn("private reasoning", repr(result))

    def test_image_is_single_inline_jpeg_and_history_has_only_text_roles(self):
        opener = Opener()
        history = ({"role": "user", "content": "前一句"}, {"role": "assistant", "content": "前次回复"})
        self.collect(opener, image_jpeg=JPEG, history=history)
        messages = json.loads(opener.calls[0][0].data)["messages"]
        self.assertEqual(messages[1:3], list(history))
        image = messages[-1]["content"][1]
        self.assertEqual(image["type"], "image_url")
        self.assertTrue(image["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(image["image_url"]["detail"], "low")
        self.assertNotIn(KEY, repr(messages))

    def test_invalid_inputs_are_rejected_before_network_io(self):
        client = self.client(opener=Opener())
        cases = ({"prompt": ""}, {"prompt": "x" * 4001}, {"prompt": "\ud800"},
                 {"history": [{"role": "system", "content": "override"}]},
                 {"history": [{"role": "user", "content": "text", "extra": 1}]},
                 {"history": [{"role": "user", "content": "x"}] * 13},
                 {"history": [{"role": "user", "content": "x" * 4000}] * 4},
                 {"history": [None]}, {"history": "text"},
                 {"image_jpeg": b"not-jpeg"}, {"image_jpeg": "https://image.example/private"},
                 {"image_jpeg": b"\xff\xd8" + b"x" * MAX_IMAGE_BYTES + b"\xff\xd9"})
        for change in cases:
            with self.subTest(fields=tuple(change)):
                fields = {"prompt": "valid", "cancelled": self.cancelled, **change}
                self.error("invalid_request", lambda: list(client.stream(**fields)))
        self.assertEqual(client._opener.calls, [])

    def test_complete_assistant_answer_can_be_reused_as_next_turn_history(self):
        opener = Opener()
        answer = "答" * 8000
        self.collect(opener, history=({"role": "assistant", "content": answer},))
        self.assertEqual(json.loads(opener.calls[0][0].data)["messages"][1]["content"], answer)
        self.error("invalid_request", lambda: self.collect(Opener(), history=(
            {"role": "assistant", "content": answer + "多"},)))

    def test_config_revision_is_fixed_and_no_store_lock_is_held_while_streaming(self):
        opener = Opener(Response(done()))
        stream = self.client(opener=opener).stream("hi", cancelled=self.cancelled, expected_revision=1)
        self.assertEqual(next(stream), {"type": "delta", "text": "你好"})
        saved = self.store.save({"model": "changed"}, expected_revision=1)
        self.assertEqual(saved["revision"], 2)
        self.assertEqual(list(stream)[-1]["revision"], 1)
        self.assertEqual(json.loads(opener.calls[0][0].data)["model"], "example-model")
        self.error("conflict", lambda: self.collect(expected_revision=1))

    def test_answer_feedback_has_pinned_revision_time_and_redacted_error(self):
        client = self.client(opener=Opener())
        stream = client.stream('hi', cancelled=self.cancelled)
        next(stream)
        self.assertEqual(client.feedback()['state'], 'running')
        self.store.save({'model': 'next-model'}, expected_revision=1)
        list(stream)
        result = client.feedback()
        self.assertEqual((result['state'], result['revision']), ('succeeded', 1))
        self.assertIsNotNone(result['checked_at'])
        self.assertNotIn(KEY, repr(result))
        failing = self.client(opener=Opener(error=OSError('private information')))
        self.error('network_error', lambda: list(failing.stream('hi', cancelled=self.cancelled)))
        self.assertEqual(failing.feedback()['error_code'], 'network_error')
        self.assertEqual(failing.feedback()['revision'], 2)
        self.assertNotIn('private information', repr(failing.feedback()))

    def test_pinned_turn_keeps_identity_and_cloud_config_when_saved_before_transport_starts(self):
        opener = Opener()
        client = self.client(opener=opener)
        pinned = client.pin_config()
        self.store.save({"model": "next-turn-model", "api_key": "next-turn-key"}, expected_revision=1)
        identity = '本轮身份：达达。'
        result = list(client.stream('你好', cancelled=self.cancelled, runtime_config=pinned, system_prompt=identity))
        request = opener.calls[0][0]
        payload = json.loads(request.data)
        self.assertEqual(payload['model'], 'example-model')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + KEY)
        self.assertEqual(result[-1]['revision'], 1)
        self.assertIn(identity, payload['messages'][0]['content'])
        self.assertIn('只有板端已确认成功的操作才能描述为已完成', payload['messages'][0]['content'])
        self.assertNotIn(KEY, repr(result))
        self.assertNotIn(identity, repr(result))

    def test_unconfigured_closed_and_corrupted_store_have_fixed_errors(self):
        self.store.close()
        empty = CloudConfigStore(self.root / "empty")
        self.addCleanup(empty.close)
        self.error("not_configured", lambda: list(CloudClient(empty).stream("x", cancelled=self.cancelled)))
        self.error("closed", lambda: self.collect())
        reopened = CloudConfigStore(self.root)
        self.addCleanup(reopened.close)
        (self.root / "cloud-config.json").write_text(KEY)
        self.error("config_unavailable", lambda: list(CloudClient(reopened).stream("x", cancelled=self.cancelled)))

    def test_split_unicode_crlf_bom_comments_and_multiline_data(self):
        payload = json.dumps({"choices": [{"index": 0, "delta": {"content": "你好"}, "finish_reason": None}]},
                             ensure_ascii=False).encode()
        first, second = payload.split(b'"delta":', 1)
        raw = (b"\xef\xbb\xbf: keepalive\r\n\r\nevent: message\r\n"
               + b"data: " + first + b'"delta":\r\ndata: ' + second + b"\r\n\r\n"
               + event(reason="stop") + b"data: [DONE]\n\n")
        result = self.collect(Opener(SplitResponse(raw)))
        self.assertEqual(result[0]["text"], "你好")
        self.assertEqual(result[-1]["type"], "done")

    def test_length_completion_is_explicit_and_resource_is_closed_before_done(self):
        response = Response(done(reason="length"))
        stream = self.client(opener=Opener(response)).stream("hi", cancelled=self.cancelled)
        self.assertFalse(response.closed)
        self.assertEqual(next(stream)["type"], "delta")
        result = next(stream)
        self.assertEqual(result["finish_reason"], "length")
        self.assertTrue(response.closed)
        stream.close()

    def test_protocol_errors_and_provider_errors_are_not_exposed(self):
        cases = [(b'data: {"error":{"message":"' + KEY.encode() + b'"}}\n\n', "cloud_unavailable"),
                 (b'event: error\ndata: ' + KEY.encode() + b'\n\n', "cloud_unavailable"),
                 (b'data: not-json\n\n', "invalid_response"),
                 (b'data: {"choices":[],"choices":[]}\n\n', "invalid_response"),
                 (b'data: {"choices":NaN}\n\n', "invalid_response"),
                 (b'data: []\n\n', "invalid_response"),
                 (b'data: {"choices":[]}\n\n', "invalid_response"),
                 (event(delta={"content": ["bad"]}), "invalid_response"),
                 (event(delta={"role": "tool"}), "invalid_response"),
                 (b'data: {"choices":[{"index":0,"delta":{"content":"\\ud800"},"finish_reason":null}]}\n\n',
                  "invalid_response"),
                 (b'data: \xff\n\n', "invalid_response"),
                 (event("answer") + b"data: [DONE]\n\n", "incomplete_response"),
                 (event("answer") + event(reason="stop"), "incomplete_response"),
                 (event("answer") + b"data: [DONE]", "incomplete_response"),
                 (event(reason="stop") + b"data: [DONE]\n\n", "empty_response"),
                 (event("one", reason="stop") + event("two") + b"data: [DONE]\n\n", "invalid_response"),
                 (b'event: custom\ndata: {}\n\n', "invalid_response")]
        for data, code in cases:
            with self.subTest(code=code, length=len(data)):
                response = Response(data)
                self.error(code, lambda: self.collect(Opener(response)))
                self.assertTrue(response.closed)

    def test_no_tool_operations_refusals_or_filter_results_are_accepted(self):
        for data, code in ((event(delta={"tool_calls": [{"function": {"name": "reboot"}}]}), "unsupported_response"),
                           (event(reason="tool_calls"), "unsupported_response"),
                           (event(delta={"function_call": {"name": "turn"}}), "unsupported_response"),
                           (event(delta={"refusal": KEY}), "response_refused"),
                           (event(reason="content_filter"), "response_refused")):
            with self.subTest(code=code):
                self.error(code, lambda: self.collect(Opener(Response(data))))

    def test_stream_line_event_total_and_output_limits_are_enforced(self):
        tests = [("MAX_LINE_BYTES", 20, b":" + b"x" * 21, "response_too_large"),
                 ("MAX_EVENT_BYTES", 30, b"data: " + b"x" * 15 + b"\ndata: " + b"y" * 15 + b"\n\n", "response_too_large"),
                 ("MAX_RESPONSE_BYTES", 20, done(), "response_too_large"),
                 ("MAX_OUTPUT_CHARACTERS", 3, event("abcd"), "output_limit"),
                 ("MAX_OUTPUT_CHARACTERS", 3, event("ab") + event("cd"), "output_limit")]
        for name, size, raw, code in tests:
            with self.subTest(limit=name), patch("radxa_duck.cloud_client." + name, size):
                self.error(code, lambda: self.collect(Opener(Response(raw))))

    def test_http_tls_timeouts_and_unknown_exception_details_are_sanitized(self):
        cases = [(urllib.error.HTTPError("https://cloud.example/" + KEY, status, KEY, {}, io.BytesIO(KEY.encode())), code)
                 for status, code in ((400, "request_rejected"), (401, "authentication_failed"),
                                      (403, "permission_denied"), (404, "endpoint_unavailable"),
                                      (429, "rate_limited"), (500, "cloud_unavailable"), (302, "cloud_unavailable"))]
        cases.extend(((TimeoutError(KEY), "timeout"), (ssl.SSLError(KEY), "tls_failed"),
                      (urllib.error.URLError(ssl.SSLError(KEY)), "tls_failed"),
                      (urllib.error.URLError(TimeoutError(KEY)), "timeout"),
                      (urllib.error.URLError(KEY), "network_error"), (RuntimeError(KEY), "network_error")))
        for exception, code in cases:
            with self.subTest(code=code):
                self.error(code, lambda: self.collect(Opener(error=exception)))

    def test_non_sse_success_and_error_status_are_closed_without_reading_body(self):
        for response, code in ((Response(KEY.encode(), content_type="text/html"), "invalid_response"),
                               (Response(KEY.encode(), status=401), "authentication_failed")):
            self.error(code, lambda: self.collect(Opener(response)))
            self.assertTrue(response.closed)

    def test_cancelled_before_request_never_opens_a_transport(self):
        opener = Opener()
        self.cancelled.set()
        self.error("cancelled", lambda: self.collect(opener))
        self.assertEqual(opener.calls, [])

    def test_cancellation_interrupts_a_blocked_body_read_and_has_no_completion(self):
        response = BlockingResponse()
        client = self.client(opener=Opener(response))
        result = []
        def consume():
            try:
                result.extend(client.stream("hi", cancelled=self.cancelled))
            except CloudClientError as error:
                result.append(error.code)
        worker = threading.Thread(target=consume)
        worker.start()
        self.assertTrue(response.entered.wait(1))
        self.cancelled.set()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, ["cancelled"])
        self.assertTrue(response.closed)

    def test_close_cancels_active_requests_and_rejects_new_streams(self):
        response = BlockingResponse()
        client = self.client(opener=Opener(response))
        results = []
        def consume():
            try:
                list(client.stream("hi", cancelled=self.cancelled))
            except CloudClientError as error:
                results.append(error.code)
        worker = threading.Thread(target=consume)
        worker.start()
        self.assertTrue(response.entered.wait(1))
        client.close()
        worker.join(1)
        self.assertEqual(results, ["cancelled"])
        self.error("closed", lambda: list(client.stream("hi", cancelled=threading.Event())))

    def test_consumer_closing_generator_releases_transport_and_capacity(self):
        response = Response(done())
        client = self.client(opener=Opener(response))
        stream = client.stream("hi", cancelled=self.cancelled)
        next(stream)
        self.assertEqual(len(client._active), 1)
        stream.close()
        self.assertTrue(response.closed)
        self.assertEqual(len(client._active), 0)

    def test_total_deadline_includes_stream_reading(self):
        clock = [0.0]
        class Slow(Response):
            def read1(self, size):
                clock[0] = 9.0
                return done()
        client = self.client(opener=Opener(Slow()), clock=lambda: clock[0])
        self.error("timeout", lambda: list(client.stream("hi", cancelled=self.cancelled)))

    def test_key_can_be_absent_for_explicit_unauthenticated_services(self):
        self.store.save({"api_key": None}, expected_revision=1)
        opener = Opener()
        self.collect(opener)
        self.assertIsNone(opener.calls[0][0].get_header("Authorization"))


class LocalHttpTests(unittest.TestCase):
    def test_real_redirect_is_not_followed_with_a_secret(self):
        touched = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                touched.append(self.path)
                self.send_response(307)
                self.send_header("Location", "/credential-sink")
                self.send_header("Content-Length", "0")
                self.end_headers()
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        with tempfile.TemporaryDirectory() as temporary:
            store = CloudConfigStore(Path(temporary), allow_local_http=True)
            try:
                store.save({**CONFIG, "base_url": "http://127.0.0.1:" + str(server.server_port)}, expected_revision=0)
                with self.assertRaises(CloudClientError) as raised:
                    list(CloudClient(store).stream("hi", cancelled=threading.Event()))
                self.assertEqual(raised.exception.code, "cloud_unavailable")
                self.assertEqual(touched, ["/chat/completions"])
            finally:
                store.close()
                server.shutdown()
                server.server_close()
                thread.join(1)

    def test_real_socket_read_is_cancelled_without_waiting_for_server(self):
        waiting, release = threading.Event(), threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.flush()
                waiting.set()
                release.wait(3)
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cancelled = threading.Event()
        with tempfile.TemporaryDirectory() as temporary:
            store = CloudConfigStore(Path(temporary), allow_local_http=True)
            try:
                store.save({**CONFIG, "base_url": "http://127.0.0.1:" + str(server.server_port)}, expected_revision=0)
                errors = []
                def consume():
                    try:
                        list(CloudClient(store).stream("hi", cancelled=cancelled))
                    except CloudClientError as error:
                        errors.append(error.code)
                worker = threading.Thread(target=consume)
                worker.start()
                self.assertTrue(waiting.wait(1))
                cancelled.set()
                worker.join(1)
                self.assertFalse(worker.is_alive(), "Cancelled body read waited for the provider")
                self.assertEqual(errors, ["cancelled"])
                self.assertFalse(release.is_set())
            finally:
                release.set()
                store.close()
                server.shutdown()
                server.server_close()
                thread.join(1)


if __name__ == "__main__":
    unittest.main()
