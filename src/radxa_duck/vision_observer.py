"""Shared camera leases and bounded, read-only JPEG subscriptions."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import math
import re
import threading
import time
import uuid

from anyio import CancelScope

from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from .identity import parse_json
from .vision_service import MAX_JPEG_BYTES, VisionServiceError


_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


class VisionObserver:
    """All shared consumers use one worker/model; browsers never own the brain.

    Legacy exclusive capture is observable but cannot be taken over by a lease.
    The owner is retained by VisionService until native cleanup completes.
    """

    def __init__(self, vision):
        self._vision = vision
        self._owner = "shared-vision-" + uuid.uuid4().hex
        self._lock = threading.RLock()
        self._leases = set()
        self._closed = self._suspended = False

    def snapshot(self, consumer=None):
        with self._lock:
            raw = self._vision.snapshot(self._owner)
            available = (raw.get("ready") is True and not self._closed and not self._suspended
                         and raw.get("capabilities", {}).get("camera_present", True))
            can_join = (raw.get("session_id") is None and not self._leases
                        or raw.get("owned") and raw["state"] in ("starting", "streaming"))
            return {"available": bool(available), "camera_active": raw.get("active", raw["state"] == "streaming"),
                    "preview_owned": consumer is not None and consumer in self._leases,
                    "can_start_preview": bool(available and can_join and consumer not in self._leases),
                    "session_id": raw.get("session_id"), "state": raw["state"]}

    def acquire(self, consumer, *, detection=None):
        with self._lock:
            if consumer in self._leases:
                return
            if len(self._leases) >= 8:
                raise VisionServiceError("busy", "Camera consumer limit reached")
            if not self.snapshot(consumer)["can_start_preview"]:
                raise VisionServiceError("busy", "Shared camera capture is unavailable")
            if not self._leases:
                settings = {"max_fps": 5}
                if detection is not None:
                    settings["detection_enabled"] = detection
                self._vision.start(self._owner, settings)
            elif detection is True and self._vision.snapshot().get("settings", {}).get("detection_enabled") is False:
                raise VisionServiceError("busy", "The active camera does not provide detections")
            self._leases.add(consumer)

    def release(self, consumer):
        with self._lock:
            self._leases.discard(consumer)
            if not self._leases and self._vision.snapshot(self._owner).get("owned"):
                self._vision.stop(self._owner)

    def frame(self):
        observer = getattr(self._vision, "observe_frame", None)
        if callable(observer):
            return observer()
        # Adapter doubles and older workers may only provide owner reads.
        raw = self._vision.snapshot(self._owner)
        if raw.get("owned"):
            return self._vision.latest_frame(self._owner, raw.get("session_id"), -1)
        return None

    def suspend(self, timeout=15):
        with self._lock:
            self._suspended = True
            self._leases.clear()
            self.release(None)

    def resume(self):
        with self._lock:
            self._suspended = False

    def close(self):
        with self._lock:
            self._closed = True
            self.suspend()


def _frame_header(frame, subscription_id, frame_id):
    """Check the producer boundary too; malformed detection data never reaches UI."""
    header = frame.header
    width, height, jpeg = header.get("width"), header.get("height"), frame.jpeg
    if (type(width) is not int or type(height) is not int or not 1 <= width <= 4096
            or not 1 <= height <= 4096 or width * height > 8388608 or not isinstance(jpeg, bytes)
            or not 4 <= len(jpeg) <= MAX_JPEG_BYTES or header.get("image_bytes") != len(jpeg)
            or not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9")):
        raise VisionServiceError("invalid_response", "Invalid camera frame")
    result = header.get("result")
    if result is not None and not isinstance(result, dict):
        raise VisionServiceError("invalid_response", "Invalid detections")
    detections = (result or {}).get("detections", [])
    if not isinstance(detections, list) or len(detections) > 256:
        raise VisionServiceError("invalid_response", "Invalid detections")
    clean = []
    for detection in detections:
        if not isinstance(detection, dict):
            raise VisionServiceError("invalid_response", "Invalid detection")
        label, score, box = detection.get("label"), detection.get("score"), detection.get("box_xyxy")
        if (not isinstance(label, str) or any(0xD800 <= ord(c) <= 0xDFFF for c in label)
                or len(label.encode("utf-16-le")) // 2 > 128
                or type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1
                or not isinstance(box, (tuple, list)) or len(box) != 4
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
                or not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)):
            raise VisionServiceError("invalid_response", "Invalid detection")
        clean.append({"label": label, "score": score, "box_xyxy": list(box)})
    return {"schema_version": 1, "type": "frame", "subscription_id": subscription_id,
            "frame_id": frame_id, "width": width, "height": height, "jpeg_bytes": len(jpeg), "detections": clean}


def create_observer_routes(allowed_origins, *, ack_timeout=10.0):
    connections = set()

    async def info(request):
        service = request.app.state.vision_observer
        return JSONResponse({"schema_version": 1, "type": "vision_observer_info",
                             "available": service.snapshot()["available"], "max_jpeg_bytes": MAX_JPEG_BYTES},
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    async def observe(socket):
        if socket.headers.get("origin") not in allowed_origins or len(connections) >= 4:
            await socket.close(code=1008)
            return
        subscription_id = uuid.uuid4().hex
        connections.add(subscription_id)
        service = socket.app.state.vision_observer
        sending = asyncio.Lock()
        pending = None
        sequence = revision = 0
        last_state = last_source = None
        requests = {}

        def status(request_id=None):
            nonlocal revision, last_state, pending
            current = service.snapshot(subscription_id)
            body = {field: current[field] for field in ("camera_active", "preview_owned", "can_start_preview")}
            if body != last_state:
                revision += 1
                last_state = body
            if not current["camera_active"] or pending is not None and pending["source_session"] != current["session_id"]:
                pending = None
            packet = {"schema_version": 1, "type": "observer_status", "subscription_id": subscription_id,
                      "revision": revision, **body}
            if request_id is not None:
                packet["request_id"] = request_id
            return packet

        async def publish():
            nonlocal pending, sequence, last_source
            sent_revision, sent_at = -1, 0.0
            while True:
                async with sending:
                    current = status()
                    now = time.monotonic()
                    if current["revision"] != sent_revision or now - sent_at >= 5:
                        await asyncio.wait_for(socket.send_json(current), 3)
                        sent_revision, sent_at = current["revision"], now
                    if pending is not None and now >= pending["deadline"]:
                        await asyncio.wait_for(socket.close(code=1013, reason="observer_ack_timeout"), 3)
                        return
                    if pending is None and current["camera_active"]:
                        frame = service.frame()
                        if frame is not None:
                            source = (frame.header.get("session_id"), frame.header.get("frame_id"))
                            if source != last_source:
                                sequence += 1
                                header = _frame_header(frame, subscription_id, sequence)
                                if len(json.dumps(header, ensure_ascii=False).encode("utf-8")) > 64 * 1024:
                                    raise VisionServiceError("invalid_response", "Frame metadata exceeds its limit")
                                # One lock covers both messages. Never queue frames
                                # behind a slow browser; the worker keeps one latest.
                                pending = {"id": sequence, "deadline": float("inf"), "source_session": source[0]}
                                credit = pending
                                await asyncio.wait_for(socket.send_json(header), 3)
                                await asyncio.wait_for(socket.send_bytes(frame.jpeg), 3)
                                last_source = source
                                if pending is credit:
                                    pending["deadline"] = time.monotonic() + ack_timeout
                await asyncio.sleep(0.04 if current["camera_active"] else 0.1)

        async def commands():
            nonlocal pending
            began, count = time.monotonic(), 0
            while True:
                message = await socket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                raw = message.get("text")
                if raw is None:
                    await socket.close(code=1003)
                    return
                if time.monotonic() - began >= 1:
                    began, count = time.monotonic(), 0
                count += 1
                if len(raw) > 1024 or count > 40:
                    await socket.close(code=1008)
                    return
                request_id = None
                try:
                    value = parse_json(raw)
                    if not isinstance(value, dict):
                        raise ValueError()
                    kind = value.get("type")
                    if kind == "ack":
                        if (set(value) != {"type", "subscription_id", "frame_id", "displayed"}
                                or value["subscription_id"] != subscription_id or type(value["frame_id"]) is not int
                                or not 0 <= value["frame_id"] < 2 ** 53 or type(value["displayed"]) is not bool):
                            raise ValueError()
                        if pending is not None and pending["id"] == value["frame_id"]:
                            pending = None
                        continue
                    if (set(value) != {"type", "request_id"} or kind not in ("preview.start", "preview.stop")
                            or not isinstance(value["request_id"], str) or not _ID.fullmatch(value["request_id"])):
                        raise ValueError()
                    request_id = value["request_id"]
                    async with sending:
                        if request_id in requests:
                            previous_kind, previous = requests[request_id]
                            if previous_kind != kind:
                                raise VisionServiceError("conflict", "Request ID already used")
                            await asyncio.wait_for(socket.send_json(previous), 3)
                            continue
                        # Bound admission instead of forgetting old IDs and
                        # accidentally replaying them later in the connection.
                        if len(requests) >= 128:
                            raise VisionServiceError("busy", "Reconnect to issue further preview operations")
                        try:
                            if kind == "preview.start":
                                service.acquire(subscription_id)
                            else:
                                service.release(subscription_id)
                            reply = status(request_id)
                        except VisionServiceError as exc:
                            reply = {"type": "error", "code": exc.code, "request_id": request_id}
                        requests[request_id] = (kind, deepcopy(reply))
                        await asyncio.wait_for(socket.send_json(reply), 3)
                except (ValueError, UnicodeError, RecursionError, VisionServiceError) as exc:
                    reply = {"type": "error", "code": getattr(exc, "code", "invalid_request")}
                    if request_id is not None:
                        reply["request_id"] = request_id
                    async with sending:
                        await asyncio.wait_for(socket.send_json(reply), 3)

        tasks = []
        try:
            await socket.accept()
            tasks = [asyncio.create_task(publish()), asyncio.create_task(commands())]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (WebSocketDisconnect, asyncio.TimeoutError, OSError, VisionServiceError):
            pass
        finally:
            with CancelScope(shield=True):
                pending = None
                try:
                    service.release(subscription_id)
                except VisionServiceError:
                    pass
                connections.discard(subscription_id)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                try:
                    await asyncio.wait_for(socket.close(), 1)
                except (RuntimeError, WebSocketDisconnect, asyncio.TimeoutError, OSError):
                    pass

    return [Route("/api/vision/observer/status", info), WebSocketRoute("/api/vision/observe", observe)]
