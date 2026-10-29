"""Vision control and paired JPEG/detection frames over one WebSocket.

GET /api/vision/status never opens hardware. WS /api/vision/live accepts start
(optional settings), stop, and ack(session_id, frame_id). Each frame is a JSON
header immediately followed by one binary JPEG. The owner acknowledges that
complete frame after decoding/displaying it; only then can a newer frame follow.
Stopping or leaving the page must also discard pending browser image decodes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import time
import uuid

from anyio import CancelScope

from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .vision_service import VisionServiceError


MAX_JPEG_BYTES = 256 * 1024


@dataclass
class _PendingFrame:
    session_id: str
    frame_id: int
    deadline: float | None = None


def create_vision_routes(allowed_origins: set[str], *, ack_timeout: float = 3.0) -> list:
    connections: set[str] = set()

    async def status(request):
        return JSONResponse(request.app.state.vision.snapshot(),
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    async def live(socket: WebSocket):
        if socket.headers.get("origin") not in allowed_origins or len(connections) >= 4:
            await socket.close(code=1008)
            return
        owner = uuid.uuid4().hex
        connections.add(owner)
        service = socket.app.state.vision
        sending = asyncio.Lock()
        pending: _PendingFrame | None = None

        async def send_json(message):
            async with sending:
                await asyncio.wait_for(socket.send_json(message), timeout=3)

        def discard_obsolete_credit(state):
            nonlocal pending
            if pending is not None and (not state["owned"] or state["state"] != "streaming"
                                        or pending.session_id != state["session_id"]):
                pending = None

        async def publish():
            nonlocal pending
            revision = -1
            state_sent_at = 0.0
            session_id = None
            last_frame_id = -1
            while True:
                state = service.snapshot(owner)
                discard_obsolete_credit(state)
                if state["session_id"] != session_id:
                    session_id, last_frame_id = state["session_id"], -1
                if state["revision"] != revision or time.monotonic() - state_sent_at >= 5:
                    await send_json(state)
                    revision, state_sent_at = state["revision"], time.monotonic()
                if pending is not None and pending.deadline is not None and time.monotonic() >= pending.deadline:
                    # This polling deadline is tied to the exact in-flight pair;
                    # stop and a new session cannot inherit an old timeout task.
                    async with sending:
                        discard_obsolete_credit(service.snapshot(owner))
                        if pending is not None and pending.deadline is not None and time.monotonic() >= pending.deadline:
                            await asyncio.wait_for(socket.close(code=1013, reason="preview_ack_timeout"), 3)
                            return
                if pending is None and state["owned"] and state["state"] == "streaming":
                    async with sending:
                        current = service.snapshot(owner)
                        if (pending is not None or not current["owned"] or current["state"] != "streaming"
                                or current["session_id"] != session_id):
                            continue
                        # Waiting for another response may take longer than the
                        # frame lifetime. Fetch the newest frame under this lock.
                        frame = service.latest_frame(owner, session_id, last_frame_id)
                        if frame is not None:
                            header = frame.header
                            if not 0 < len(frame.jpeg) <= MAX_JPEG_BYTES or header["image_bytes"] != len(frame.jpeg):
                                await asyncio.wait_for(socket.close(code=1011, reason="invalid_preview_frame"), 3)
                                return
                            credit = _PendingFrame(header["session_id"], header["frame_id"])
                            pending = credit
                            # Once the header is sent, finish its binary message
                            # or close the connection. Never interleave a state.
                            await asyncio.wait_for(socket.send_json(header), 3)
                            await asyncio.wait_for(socket.send_bytes(frame.jpeg), 3)
                            last_frame_id = header["frame_id"]
                            if pending is credit:
                                credit.deadline = time.monotonic() + ack_timeout
                await asyncio.sleep(0.02 if state["owned"] and state["state"] == "streaming" else 0.1)

        async def commands():
            nonlocal pending
            window_started, count = time.monotonic(), 0
            while True:
                message = await socket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                raw = message.get("text")
                if raw is None:
                    await socket.close(code=1003)
                    return
                if time.monotonic() - window_started >= 1:
                    window_started, count = time.monotonic(), 0
                count += 1
                if len(raw) > 1024 or count > 40:
                    await socket.close(code=1008)
                    return
                try:
                    command = json.loads(raw)
                    if not isinstance(command, dict):
                        raise VisionServiceError("invalid_request", "Expected a vision command object")
                    kind = command.get("type")
                    if kind == "ack" and set(command) == {"type", "session_id", "frame_id"}:
                        session, frame_id = command["session_id"], command["frame_id"]
                        if (not isinstance(session, str) or not 0 < len(session) <= 64
                                or type(frame_id) is not int or not 0 <= frame_id < 2 ** 53):
                            raise VisionServiceError("invalid_request", "Invalid frame acknowledgement")
                        if pending is not None and (session, frame_id) == (pending.session_id, pending.frame_id):
                            pending = None
                        # Old, repeated or unmatched ACKs cannot grant credit to
                        # a different frame or session, and need no response.
                        continue
                    if kind == "start" and set(command) in ({"type"}, {"type", "settings"}):
                        settings = command.get("settings")
                        if "settings" in command and not isinstance(settings, dict):
                            raise VisionServiceError("invalid_request", "settings must be an object")
                        service.start(owner, settings)
                    elif kind == "stop" and set(command) == {"type"}:
                        service.stop(owner)
                    else:
                        raise VisionServiceError("invalid_request", "Expected start, stop or ack")
                    state = service.snapshot(owner)
                    discard_obsolete_credit(state)
                    await send_json(state)
                except json.JSONDecodeError:
                    await send_json({"type": "error", "error": {"code": "invalid_request", "message": "Expected JSON"}})
                except VisionServiceError as exc:
                    await send_json({"type": "error", "error": {"code": exc.code, "message": str(exc)}})

        tasks = []
        try:
            await socket.accept()
            tasks = [asyncio.create_task(publish()), asyncio.create_task(commands())]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (WebSocketDisconnect, asyncio.TimeoutError, OSError):
            pass
        finally:
            with CancelScope(shield=True):
                pending = None
                # Cancelling capture precedes waiting for network task cleanup.
                try:
                    service.stop(owner)
                except VisionServiceError:
                    pass
                # ASGI cancellation can interrupt any following await. Release the
                # admission slot synchronously, just like camera cancellation.
                connections.discard(owner)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                try:
                    await asyncio.wait_for(socket.close(), 1)
                except (RuntimeError, WebSocketDisconnect, asyncio.TimeoutError, OSError):
                    pass

    return [Route("/api/vision/status", status), WebSocketRoute("/api/vision/live", live)]
