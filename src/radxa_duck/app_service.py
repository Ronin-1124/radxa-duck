"""Single-owner application sessions with bounded, memory-only conversations."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
import re
import threading
import time
import uuid

from .actions import ActionError, ActionService
from .cloud_client import CloudClientError
from .speech import SpeechError
from .speech_output import SpeechOutputError
from .vision_service import VisionServiceError


SCENES = ("voice-chat", "image-qa", "motion", "companion")
MAX_TEXT = 2000


class AppServiceError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _text(value):
    return (isinstance(value, str) and len(value) <= MAX_TEXT
            and not any(ord(c) < 32 and c not in "\n\t" or 0xD800 <= ord(c) <= 0xDFFF for c in value))


class AppService:
    def __init__(self, speech, vision, *, cloud=None, cloud_config=None, output=None, actions=None,
                 context_provider=None):
        self._speech, self._vision = speech, vision
        self._cloud, self._cloud_config, self._output = cloud, cloud_config, output
        self._actions = actions if actions is not None else ActionService()
        self._context_provider = context_provider
        self._condition = threading.Condition()
        self._owner = self._session_id = self._scene = None
        self._state = "idle"
        self._closing = self._suspended = self._stop_requested = False
        self._listen = self._speak = self._mic_active = False
        self._request = self._turn_id = self._cancelled = None
        self._reply = self._transcript = ""
        self._error = None
        self._revision = self._seq = 0
        self._history = []
        self._events = deque(maxlen=128)
        self._requests = {}
        self._asr_seen = 0
        self._asr_session_id = None
        self._hardware_owner = None
        self._continue_on_cancel = False
        self._thread = threading.Thread(target=self._run, name="radxa-apps", daemon=True)
        self._thread.start()

    def _changed(self):
        self._revision += 1
        self._condition.notify_all()

    def _event(self, kind, **fields):
        self._seq += 1
        self._events.append({"schema_version": 1, "type": kind, "seq": self._seq,
                             "session_id": self._session_id, "turn_id": self._turn_id, **fields})
        self._changed()

    def _owns(self, owner):
        if self._owner != owner or owner is None:
            raise AppServiceError("not_owner", "This connection does not own the application session")

    def _cloud_ready(self):
        try:
            return self._cloud is not None and self._cloud_config is not None and self._cloud_config.snapshot()["configured"]
        except Exception:
            return False

    def _output_ready(self):
        try:
            return self._output is not None and self._output.snapshot()["available"] is True
        except Exception:
            return False

    def actions_snapshot(self):
        return self._actions.snapshot()

    def capabilities(self):
        cloud = self._cloud_ready()
        speech = self._speech.snapshot().get("ready") is True
        vision = self._vision.snapshot().get("ready") is True
        output = self._output_ready()
        actions = self._actions.snapshot()["available"]
        with self._condition:
            blocked = self._closing or self._suspended
            scenes = []
            for scene in SCENES:
                ready = actions if scene == "motion" else cloud and (vision if scene == "image-qa" else True)
                available = ready and not blocked
                reason = (None if available else "maintenance" if blocked else
                          "controller_unavailable" if scene == "motion" else
                          "cloud_unconfigured" if not cloud else "camera_unavailable")
                scenes.append({"id": scene, "available": available, "reason": reason})
            return {"schema_version": 1, "type": "apps_status", "state": self._state,
                    "session_active": self._owner is not None, "scenes": scenes,
                    "capabilities": {"session": not blocked, "streaming": cloud,
                                     "voice_input": speech, "voice_reply": output,
                                     "image_input": {"available": cloud and vision, "verified": False,
                                                     "requires_capture_confirmation": True},
                                     "actions": actions, "companion": {"mode": "passive", "proactive": False}}}

    def snapshot(self, owner=None):
        with self._condition:
            owned = owner is not None and owner == self._owner
            return {"schema_version": 1, "type": "app_state", "revision": self._revision,
                    "event_sequence": self._seq, "state": self._state, "owned": owned,
                    "session_id": self._session_id, "scene": self._scene,
                    "turn_id": self._turn_id, "listening": self._listen,
                    "speak": self._speak, "maintenance": self._suspended,
                    "reply": self._reply if owned else "", "transcript": self._transcript if owned else "",
                    "error": deepcopy(self._error) if owned else None}

    def events(self, owner, after):
        with self._condition:
            return deepcopy([e for e in self._events if e["seq"] > after]) if self._owner == owner else []

    def start(self, owner, *, scene, listen=False, speak=False, cancelled=None, allow_unconfigured=False):
        if (not isinstance(owner, str) or not owner or scene not in SCENES
                or type(listen) is not bool or type(speak) is not bool or listen and scene != "voice-chat"):
            raise AppServiceError("invalid_request", "Invalid application session settings")
        if scene == "motion":
            if not self._actions.snapshot()["available"]:
                raise AppServiceError("controller_unavailable", "The motion controller is not connected")
        elif not allow_unconfigured and not self._cloud_ready():
            raise AppServiceError("cloud_unconfigured", "Save a cloud model configuration before starting a session")
        if speak and not self._output_ready():
            raise AppServiceError("tts_unavailable", "Speech output has not been configured")
        if scene == "image-qa" and not self._vision.snapshot().get("ready"):
            raise AppServiceError("camera_unavailable", "The camera service is not ready")
        if listen and not self._speech.snapshot().get("ready"):
            raise AppServiceError("not_ready", "Speech recognition is not ready")
        with self._condition:
            if cancelled is not None and cancelled.is_set():
                raise AppServiceError("cancelled", "The application connection closed during startup")
            if self._closing or self._suspended:
                raise AppServiceError("maintenance", "Application sessions are unavailable during maintenance")
            if self._owner is not None:
                raise AppServiceError("busy", "An application session is already active")
            self._owner, self._session_id, self._scene = owner, uuid.uuid4().hex, scene
            self._hardware_owner = "app-" + self._session_id
            self._listen, self._speak = listen, speak
            self._stop_requested = False
            self._history.clear()
            self._requests.clear()
            self._events.clear()
            self._reply = self._transcript = ""
            self._error = None
            self._turn_id = None
            self._state = "listening" if listen else "waiting"
            self._changed()

    def submit(self, owner, *, request_id, text="", capture_image=False, action_id=None):
        if (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", request_id)
                or not _text(text) or type(capture_image) is not bool
                or action_id is not None and not isinstance(action_id, str)):
            raise AppServiceError("invalid_request", "Invalid application request")
        with self._condition:
            self._owns(owner)
            if self._stop_requested or self._suspended:
                raise AppServiceError("busy", "The application session is stopping")
            if request_id in self._requests:
                return
            if self._request is not None or self._cancelled is not None:
                raise AppServiceError("busy", "An application response is already in progress")
            if self._scene == "image-qa" and not capture_image:
                raise AppServiceError("capture_required", "Explicitly request a current camera image for this question")
            if capture_image and self._scene != "image-qa":
                raise AppServiceError("invalid_request", "Camera capture is only available for image questions")
            if self._scene == "motion":
                if action_id is None or text.strip():
                    raise AppServiceError("invalid_request", "Choose an advertised action ID to execute")
            elif action_id is not None or not text.strip():
                raise AppServiceError("invalid_request", "A nonempty question is required")
            self._queue(request_id, text.strip(), capture_image, action_id)

    def _queue(self, request_id, text, capture_image=False, action_id=None):
        context, preparation_error = {}, None
        try:
            if self._context_provider is not None:
                context["system_prompt"] = self._context_provider()
            pin_config = getattr(self._cloud, "pin_config", None)
            if action_id is None and callable(pin_config):
                context["runtime_config"] = pin_config()
        except Exception as exc:
            # Keep credentials and configuration errors out of events. The
            # worker will report a finite public failure for this exact turn.
            preparation_error = getattr(exc, "code", "config_unavailable")
        self._turn_id = uuid.uuid4().hex
        self._request = {"text": text, "capture_image": capture_image, "action_id": action_id,
                         "context": context, "preparation_error": preparation_error}
        self._continue_on_cancel = False
        self._requests[request_id] = self._turn_id
        while len(self._requests) > 32:
            del self._requests[next(iter(self._requests))]
        self._reply, self._transcript, self._error = "", text, None
        self._cancelled = threading.Event()
        self._state = "queued"
        self._event("turn_start", request_id=request_id)

    def set_listening(self, owner, enabled):
        if type(enabled) is not bool:
            raise AppServiceError("invalid_request", "Listening must be a boolean")
        with self._condition:
            self._owns(owner)
            if self._scene != "voice-chat" or self._stop_requested or self._suspended:
                raise AppServiceError("invalid_request", "Listening is only available in an active voice chat")
            self._listen = enabled
            # During a reply this flag controls the next capture, not the
            # current generation/playback phase. Waiting implies the previous
            # microphone has actually been released by the worker.
            if self._request is None:
                self._state = "listening" if enabled else "stopping" if self._mic_active else "waiting"
            self._changed()

    def cancel(self, owner, *, stop_listening=True, expected_turn_id=None):
        with self._condition:
            self._owns(owner)
            if expected_turn_id is not None and (self._turn_id != expected_turn_id or self._request is None):
                raise AppServiceError("conflict", "The target interaction has already finished")
            if stop_listening:
                self._listen = False
            self._continue_on_cancel = not stop_listening
            if self._cancelled is not None:
                self._cancelled.set()
            self._state = "stopping" if self._cancelled is not None or self._mic_active else "waiting"
            self._changed()

    def forget_context(self, owner):
        """Expire idle resident context without restarting microphone capture."""
        with self._condition:
            self._owns(owner)
            if self._request is not None:
                return
            self._history.clear()
            self._events.clear()
            self._requests.clear()
            self._reply = self._transcript = ""
            self._turn_id = None
            self._changed()

    def stop(self, owner):
        with self._condition:
            if self._owner is None:
                return
            self._owns(owner)
            self._listen = False
            self._stop_requested = True
            if self._cancelled is not None:
                self._cancelled.set()
            self._state = "stopping"
            self._changed()

    @staticmethod
    def _check(cancelled):
        if cancelled.is_set():
            raise AppServiceError("cancelled", "The application request was cancelled")

    def _phase(self, phase):
        with self._condition:
            if self._stop_requested or self._closing or self._cancelled is not None and self._cancelled.is_set():
                return
            self._state = phase
            self._changed()

    def _stop_device(self, device):
        state = device.snapshot(self._hardware_owner)
        if not state.get("owned"):
            return
        session_id = state.get("session_id")
        device.stop(self._hardware_owner)
        deadline = time.monotonic() + 8
        while device.snapshot(self._hardware_owner).get("owned"):
            if time.monotonic() >= deadline:
                raise AppServiceError("release_timeout", "A capture device did not finish stopping")
            time.sleep(0.02)
        if device is self._speech and hasattr(device, "clear_transcript"):
            device.clear_transcript(session_id)

    def _stop_microphone(self):
        self._stop_device(self._speech)
        if self._asr_session_id is not None and hasattr(self._speech, "clear_transcript"):
            self._speech.clear_transcript(self._asr_session_id)
        with self._condition:
            self._mic_active = False
            self._asr_seen = 0
            self._asr_session_id = None

    def _capture(self, cancelled):
        self._phase("capturing")
        with self._condition:
            self._check(cancelled)
            self._vision.start(self._hardware_owner, {"detection_enabled": False, "max_fps": 1})
        try:
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                self._check(cancelled)
                state = self._vision.snapshot(self._hardware_owner)
                if state["state"] == "error":
                    raise AppServiceError("camera_unavailable", "The camera could not provide a current image")
                frame = self._vision.latest_frame(self._hardware_owner, state.get("session_id"), -1)
                if frame is not None:
                    return bytes(frame.jpeg)
                cancelled.wait(0.03)
            raise AppServiceError("camera_timeout", "The camera did not provide an image in time")
        finally:
            self._stop_device(self._vision)

    def _answer(self, request, cancelled):
        self._stop_microphone()
        self._check(cancelled)
        if request["preparation_error"] is not None:
            raise AppServiceError(request["preparation_error"], "The interaction configuration is unavailable")
        image = self._capture(cancelled) if request["capture_image"] else None
        if request["action_id"] is not None:
            self._phase("executing")
            result = self._actions.execute(request["action_id"], cancelled=cancelled)
            with self._condition:
                self._event("action_result", **result)
            return
        self._phase("generating")
        if self._cloud is None:
            raise AppServiceError("cloud_unconfigured", "Cloud understanding is not configured")
        completed = False
        try:
            for event in self._cloud.stream(request["text"], cancelled=cancelled,
                                            image_jpeg=image, history=tuple(self._history), **request["context"]):
                self._check(cancelled)
                with self._condition:
                    self._check(cancelled)
                    if event["type"] == "delta":
                        if not isinstance(event.get("text"), str) or len(self._reply) + len(event["text"]) > 8000:
                            raise AppServiceError("response_too_large", "The answer exceeded its size limit")
                        self._reply += event["text"]
                        self._event("answer_delta", text=event["text"])
                    elif event["type"] == "done":
                        completed = True
                        self._event("answer_done", revision=event["revision"], finish_reason=event["finish_reason"])
        finally:
            image = None
        if not completed or not self._reply.strip():
            raise AppServiceError("incomplete_response", "The cloud response did not finish")
        self._check(cancelled)
        with self._condition:
            self._check(cancelled)
            self._history.extend(({"role": "user", "content": request["text"]},
                                  {"role": "assistant", "content": self._reply}))
            while len(self._history) > 12 or sum(len(item["content"]) for item in self._history) > 10000:
                del self._history[:2]
        if self._speak:
            limits = self._output.snapshot()
            if len(self._reply) > limits.get("max_text_chars", 1200):
                raise AppServiceError("speech_text_limit", "The answer exceeds the configured speech output limit")
            result = self._output.speak(self._reply, cancelled=cancelled, on_phase=self._phase)
            self._check(cancelled)
            with self._condition:
                self._check(cancelled)
                self._event("playback_done", played=result.get("played", True),
                            audio_seconds=result.get("audio_seconds", 0))

    def _listen_step(self):
        with self._condition:
            if not self._listen or self._stop_requested or self._closing or self._suspended or self._request is not None:
                return
            if not self._mic_active:
                self._speech.start(self._hardware_owner, private=True)
                self._asr_session_id = self._speech.snapshot(self._hardware_owner).get("session_id")
                self._mic_active = True
                self._asr_seen = 0
                if self._error is not None and self._error["code"] == "device_unavailable":
                    self._error = None
                self._phase("listening")
        state = self._speech.snapshot(self._hardware_owner)
        if state.get("state") == "error":
            raise AppServiceError("speech_unavailable", "Speech recognition stopped unexpectedly")
        with self._condition:
            if not self._listen or self._stop_requested or self._request is not None:
                return
            partial = state.get("partial_text", "")
            if partial != self._transcript:
                self._transcript = partial
                self._event("transcript", text=partial, final=False)
            final = [segment for segment in state.get("segments", []) if segment["id"] > self._asr_seen]
            if final:
                self._asr_seen = final[-1]["id"]
                text = " ".join(segment["text"] for segment in final).strip()
                if text:
                    self._queue(uuid.uuid4().hex, text[:MAX_TEXT])
                    self._event("transcript", text=text[:MAX_TEXT], final=True)

    def _finish_session(self):
        self._stop_microphone()
        self._stop_device(self._vision)
        with self._condition:
            self._owner = self._session_id = self._scene = self._hardware_owner = None
            self._request = self._turn_id = self._cancelled = None
            self._reply = self._transcript = ""
            self._history.clear()
            self._events.clear()
            self._requests.clear()
            self._listen = self._speak = self._stop_requested = False
            self._state = "closed" if self._closing else "idle"
            self._changed()

    def _run(self):
        while True:
            with self._condition:
                if self._owner is None:
                    if self._closing:
                        return
                    self._condition.wait(timeout=0.1)
                    continue
                stop = self._stop_requested or self._closing
                request, cancelled = self._request, self._cancelled
                listen = self._listen
            try:
                if stop:
                    self._finish_session()
                    continue
                if request is not None:
                    outcome, failure = "succeeded", None
                    try:
                        self._answer(request, cancelled)
                        self._check(cancelled)
                    except Exception as exc:
                        code = getattr(exc, "code", "operation_failed")
                        outcome = "cancelled" if code == "cancelled" or cancelled.is_set() else "failed"
                        failure = {"code": code if isinstance(exc, (AppServiceError, CloudClientError, ActionError,
                                                                              SpeechError, SpeechOutputError, VisionServiceError)) else "operation_failed",
                                   "message": str(exc) if isinstance(exc, (AppServiceError, CloudClientError, ActionError, SpeechOutputError))
                                   else "The application request could not be completed"}
                    with self._condition:
                        self._error = failure
                        self._event("turn_end", state=outcome, error=failure)
                        self._request = self._cancelled = None
                        # Failure never automatically reopens the microphone.
                        if outcome != "succeeded" and not (outcome == "cancelled" and self._continue_on_cancel):
                            self._listen = False
                        if failure is not None and failure["code"] == "release_timeout":
                            self._stop_requested = True
                        self._state = "listening" if self._listen else "waiting"
                        self._changed()
                    request = cancelled = None
                elif listen:
                    self._listen_step()
                    with self._condition:
                        self._condition.wait(timeout=0.05)
                else:
                    if self._mic_active:
                        self._stop_microphone()
                    with self._condition:
                        if (not self._listen and self._request is None
                                and not self._stop_requested and not self._closing
                                and self._state == "stopping"):
                            self._state = "waiting"
                            self._changed()
                        self._condition.wait(timeout=0.05)
            except Exception:
                with self._condition:
                    self._listen = False
                    self._error = {"code": "device_unavailable", "message": "Application capture resources are unavailable"}
                    self._state = "error"
                    self._changed()
                    # Retain ownership on cleanup failure; do not permit a new
                    # session to race resources that may still be active.
                    self._condition.wait(timeout=0.1)

    def suspend(self, timeout=15):
        with self._condition:
            self._suspended = True
            self._changed()
            if self._owner is not None:
                self.stop(self._owner)
            if not self._condition.wait_for(lambda: self._owner is None, timeout=timeout):
                raise AppServiceError("shutdown_timeout", "Application work did not finish stopping")

    def resume(self):
        with self._condition:
            self._suspended = False
            self._changed()

    def close(self, timeout=30):
        with self._condition:
            self._closing = True
            if self._owner is not None:
                self.stop(self._owner)
            self._changed()
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise AppServiceError("shutdown_timeout", "Application worker did not finish stopping")
        if self._cloud is not None:
            self._cloud.close()
        if self._output is not None:
            self._output.close()
