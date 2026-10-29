"""Resident interaction admission and device state, independent of web clients.

One AppService executes turns; the brain retains its own owner across browser
disconnects. CPU speech and NPU vision workers are shared, never duplicated.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import re
import threading
import time
import uuid

from .app_service import AppServiceError


_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
ACTIONS = frozenset(("pause", "resume", "enable_microphone", "disable_microphone", "cancel_task"))
MAX_REVISION = 2 ** 53 - 1


class BrainError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class TurnContext:
    """Copied facts for prompt assembly; no dependency back into brain locks."""

    def __init__(self, identity):
        self._identity = identity
        self._lock = threading.Lock()
        self._facts = {}

    def update(self, facts):
        with self._lock:
            self._facts = deepcopy(facts)

    def for_turn(self):
        profile = self._identity.for_turn()
        with self._lock:
            facts = deepcopy(self._facts)
        return ("以下 JSON 是本轮统一的角色身份。按此身份表达，遵守真实设备能力与执行约束。\n"
                + json.dumps(profile, ensure_ascii=False, allow_nan=False)
                + "\n以下 JSON 是板端采样的设备事实；缺失信息表示未知，不得推测动作已完成。\n"
                + json.dumps(facts, ensure_ascii=False, allow_nan=False))


class BrainService:
    def __init__(self, apps, speech, vision_observer, identity, *, cloud_config=None,
                 device=None, context=None, camera_enabled=False):
        self._apps, self._speech, self._vision = apps, speech, vision_observer
        self._identity, self._cloud_config, self._device, self._context = identity, cloud_config, device, context
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._instance_id = "brain-" + uuid.uuid4().hex
        self._owner = self._instance_id
        self._paused = self._microphone_enabled = self._closing = self._suspended = self._fault = False
        self._camera_enabled = camera_enabled
        self._initialized = False
        self._camera_lease = False
        self._camera_failed = False
        self._task = None
        self._task_finished_at = None
        self._seen = 0
        self._session = None
        self._revision = 0
        self._last = None
        self._requests = {}
        self._cloud = {"configured": False, "model": None, "health": "unknown"}
        self._cloud_revision = self._health_revision = None
        self._facts = {}
        self._thread = threading.Thread(target=self._run, name="radxa-brain", daemon=True)
        self._thread.start()

    def _collect_events(self, state):
        if not state.get("owned"):
            self._session = None
            self._seen = 0
            return
        if self._session != state.get("session_id"):
            self._session, self._seen = state.get("session_id"), 0
        for event in self._apps.events(self._owner, self._seen):
            self._seen = max(self._seen, event["seq"])
            if event["type"] == "turn_start":
                self._task = {"id": event["turn_id"], "kind": "voice", "state": "running", "summary": "语音交互"}
                self._task_finished_at = None
            elif event["type"] == "answer_done":
                self._health_revision = event.get("revision")
                self._cloud["health"] = "ready" if self._health_revision == self._cloud_revision else "unknown"
            elif event["type"] == "turn_end" and self._task is not None and self._task["id"] == event["turn_id"]:
                self._task["state"] = event["state"]
                self._task_finished_at = time.monotonic()
                if event["state"] == "failed":
                    code = (event.get("error") or {}).get("code")
                    self._task["summary"] = "语音交互失败"
                    self._paused = self._fault = True
                    if code in {"authentication_failed", "permission_denied", "rate_limited", "cloud_unavailable",
                                "network_error", "timeout", "tls_failed", "endpoint_unavailable", "model_unavailable"}:
                        self._cloud["health"] = "error"
                elif event["state"] == "cancelled":
                    self._task["summary"] = "语音交互已取消"
                else:
                    self._task["summary"] = "语音交互完成"

    def _snapshot(self):
        engine = self._apps.snapshot(self._owner)
        self._collect_events(engine)
        microphone = self._speech.snapshot()
        camera = self._vision.snapshot(self._owner)
        actions = self._apps.actions_snapshot()
        owned = engine.get("owned") is True
        busy = engine.get("session_id") is not None and not owned
        mic_available = microphone.get("ready") is True and not microphone.get("maintenance", False)
        mic_active = microphone.get("active", microphone.get("state") == "listening")
        task_active = self._task is not None and self._task["state"] in ("running", "cancelling")
        state = ("stopped" if self._closing else "starting" if not self._initialized else "paused" if self._suspended else
                 "error" if self._fault else "paused" if self._paused else "running")
        activity = "idle"
        if task_active and owned:
            phase = engine["state"]
            activity = "speaking" if phase == "playing" else "acting" if phase == "executing" else "thinking"
        elif state == "running" and self._microphone_enabled and owned and mic_active:
            activity = "listening"
        usable = self._initialized and not self._closing and not self._suspended
        capture_busy = microphone.get("state") in ("starting", "listening", "stopping") and not owned
        releasing = owned and engine["state"] == "stopping" and not task_active
        controls = {"pause": usable and not self._paused and not self._fault,
                    "resume": usable and (self._paused or self._fault) and not busy
                              and not releasing and (not self._microphone_enabled or mic_available),
                    "enable_microphone": usable and not self._microphone_enabled and mic_available
                                         and not busy and not capture_busy and not releasing,
                    "disable_microphone": usable and self._microphone_enabled,
                    "cancel_task": usable and task_active and self._task["state"] == "running" and owned}
        value = {"schema_version": 1, "type": "brain_status", "instance_id": self._instance_id,
                 "state": state, "activity": activity, "events_available": True,
                 "identity": self._identity.summary(),
                 "microphone": {"available": mic_available, "enabled": self._microphone_enabled, "active": bool(mic_active)},
                 "camera": {"available": camera["available"] and not self._camera_failed, "enabled": self._camera_enabled,
                            "active": camera["camera_active"]},
                 "cloud": deepcopy(self._cloud),
                 "motion": {"available": actions["available"], "state": "ready" if actions["available"] else "unavailable"},
                 "task": deepcopy(self._task), "controls": controls}
        if value != self._last:
            self._revision += 1
            self._last = deepcopy(value)
        return {**value, "revision": self._revision, "sampled_at": datetime.now(timezone.utc).isoformat()}

    def snapshot(self):
        with self._lock:
            return self._snapshot()

    @staticmethod
    def validate_control(value):
        if (not isinstance(value, dict) or set(value) - {"request_id", "action", "expected_instance_id", "expected_revision", "task_id"}
                or not {"request_id", "action", "expected_instance_id", "expected_revision"} <= set(value)
                or not isinstance(value["request_id"], str) or not _ID.fullmatch(value["request_id"])
                or not isinstance(value["expected_instance_id"], str) or not _ID.fullmatch(value["expected_instance_id"])
                or not isinstance(value["action"], str) or value["action"] not in ACTIONS
                or type(value["expected_revision"]) is not int or not 0 <= value["expected_revision"] <= MAX_REVISION):
            raise BrainError("invalid_request")
        if value["action"] == "cancel_task":
            if not isinstance(value.get("task_id"), str) or not _ID.fullmatch(value["task_id"]):
                raise BrainError("invalid_request")
        elif "task_id" in value:
            raise BrainError("invalid_request")

    def _ensure_session(self):
        state = self._apps.snapshot(self._owner)
        if state.get("owned"):
            return
        speak = self._apps.capabilities()["capabilities"]["voice_reply"]
        self._apps.start(self._owner, scene="voice-chat", listen=False, speak=speak, allow_unconfigured=True)
        self._session = None

    def control(self, value):
        self.validate_control(value)
        with self._lock:
            request_id, action = value["request_id"], value["action"]
            if request_id in self._requests:
                original, result = self._requests[request_id]
                if original != value:
                    raise BrainError("conflict")
                return deepcopy(result)
            current = self._snapshot()
            if (current["instance_id"] != value["expected_instance_id"] or current["revision"] != value["expected_revision"]
                    or not current["controls"][action]):
                raise BrainError("conflict")
            try:
                if action == "pause":
                    if self._apps.snapshot(self._owner).get("owned"):
                        self._apps.set_listening(self._owner, False)
                    self._paused = True
                elif action == "resume":
                    if self._microphone_enabled:
                        self._ensure_session()
                        self._apps.set_listening(self._owner, True)
                    self._paused = self._fault = False
                    self._camera_failed = False
                elif action == "enable_microphone":
                    if not self._paused and not self._fault:
                        self._ensure_session()
                        self._apps.set_listening(self._owner, True)
                    self._microphone_enabled = True
                elif action == "disable_microphone":
                    if self._apps.snapshot(self._owner).get("owned"):
                        self._apps.set_listening(self._owner, False)
                    self._microphone_enabled = False
                else:
                    if self._task is None or self._task["id"] != value["task_id"]:
                        raise BrainError("conflict")
                    self._apps.cancel(self._owner, stop_listening=False, expected_turn_id=value["task_id"])
                    self._task["state"] = "cancelling"
            except AppServiceError as exc:
                raise BrainError("conflict" if exc.code in ("not_owner", "busy", "conflict") else "unavailable") from None
            result = {"schema_version": 1, "request_id": request_id, "status": self._snapshot()}
            self._requests[request_id] = (deepcopy(value), deepcopy(result))
            # Old commands also carry their original revision; even outside
            # this reply cache they cannot be executed against a newer state.
            while len(self._requests) > 256:
                del self._requests[next(iter(self._requests))]
            self._wake.set()
            return result

    def _sample_configuration(self):
        configured = {"configured": False, "model": None, "revision": None}
        try:
            if self._cloud_config is not None:
                configured = self._cloud_config.snapshot()
        except Exception:
            pass
        device = {}
        try:
            if self._device is not None:
                sample = self._device.snapshot()
                device = {key: sample.get(key) for key in ("model", "app_version", "cpu", "memory", "temperature")}
        except Exception:
            pass
        with self._lock:
            if configured.get("revision") != self._cloud_revision:
                self._cloud["health"] = "unknown"
                self._cloud_revision = configured.get("revision")
            self._cloud.update(configured=configured.get("configured") is True, model=configured.get("model"))
            self._facts = device
            self._initialized = True

    def _tick(self):
        with self._lock:
            status = self._snapshot()
            engine = self._apps.snapshot(self._owner)
            want_capture = not (self._paused or self._fault or self._suspended or self._closing)
            if self._camera_enabled and want_capture and not self._camera_lease and not self._camera_failed:
                try:
                    self._vision.acquire(self._owner, detection=True)
                    self._camera_lease = True
                except Exception:
                    self._camera_failed = True
            elif self._camera_lease and (not want_capture or not self._camera_enabled):
                self._vision.release(self._owner)
                self._camera_lease = False
            if self._camera_lease and self._vision.snapshot(self._owner)["state"] == "error":
                self._camera_failed = True
                self._vision.release(self._owner)
                self._camera_lease = False
            if engine.get("owned"):
                busy = self._task is not None and self._task["state"] in ("running", "cancelling")
                if engine["state"] == "error":
                    self._paused = self._fault = True
                if not want_capture and engine.get("listening"):
                    self._apps.set_listening(self._owner, False)
                if not busy and (not self._microphone_enabled or not want_capture):
                    self._apps.stop(self._owner)
            if self._task_finished_at is not None and time.monotonic() - self._task_finished_at >= 300:
                if engine.get("owned"):
                    self._apps.forget_context(self._owner)
                self._task = self._task_finished_at = None
            if self._context is not None:
                perception = None
                if self._camera_enabled and self._camera_lease:
                    frame = self._vision.frame()
                    if frame is not None:
                        result = frame.header.get("result") or {}
                        perception = {"frame_id": frame.header.get("frame_id"),
                                      "detections": [{key: item.get(key) for key in ("label", "score", "box_xyxy")}
                                                     for item in result.get("detections", [])[:20]]}
                self._context.update({"sampled_at": status["sampled_at"], "brain_state": status["state"],
                                      "microphone": status["microphone"], "camera": status["camera"],
                                      "cloud": status["cloud"], "motion": status["motion"], "device": self._facts,
                                      "visual_observation": perception})

    def _run(self):
        sampled_at = 0.0
        while True:
            with self._lock:
                if self._closing:
                    return
            if time.monotonic() - sampled_at >= 2:
                self._sample_configuration()
                sampled_at = time.monotonic()
            try:
                self._tick()
            except Exception:
                with self._lock:
                    self._paused = self._fault = True
            self._wake.wait(0.1)
            self._wake.clear()

    def suspend(self, timeout=15):
        with self._lock:
            self._suspended = self._paused = True
            self._microphone_enabled = False
            if self._apps.snapshot(self._owner).get("owned"):
                self._apps.stop(self._owner)
            self._vision.release(self._owner)
            self._camera_lease = False
            self._wake.set()

    def resume(self):
        # Maintenance rollback makes controls available; explicit user action
        # is required to resume interaction or enable capture again.
        with self._lock:
            self._suspended = False
            self._wake.set()

    def close(self, timeout=10):
        with self._lock:
            self._closing = True
            self.suspend()
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise BrainError("shutdown_timeout")
