"""Ephemeral application sessions over a board-owned, origin-checked socket.

Opening status or the socket never starts capture. A connection owns any session
it explicitly starts; disconnects signal cancellation before awaiting transport
cleanup. Replies and transcripts are sent only to their owning connection.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import uuid

from anyio import CancelScope

from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .app_service import AppServiceError


MAX_COMMAND_BYTES = 12 * 1024
MAX_TEXT_CHARACTERS = 2000
SCENES = frozenset(("voice-chat", "image-qa", "motion", "companion"))
REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{8,64}\Z", re.ASCII)
ACTION_ID = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z", re.ASCII)
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON field")
        value[key] = item
    return value


def _constant(_value):
    raise ValueError("Non-finite JSON number")


def _command(raw):
    try:
        value = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise AppServiceError("invalid_request", "Expected a JSON command object") from None
    if not isinstance(value, dict):
        raise AppServiceError("invalid_request", "Expected a JSON command object")
    kind = value.get("type")
    if kind == "start":
        if set(value) - {"type", "scene", "listen", "speak"} or "scene" not in value:
            raise AppServiceError("invalid_request", "Expected a scene and optional session controls")
        if not isinstance(value["scene"], str) or value["scene"] not in SCENES:
            raise AppServiceError("invalid_request", "Unknown application scene")
        if any(type(value[key]) is not bool for key in ("listen", "speak") if key in value):
            raise AppServiceError("invalid_request", "Session controls must be booleans")
    elif kind == "submit":
        if set(value) - {"type", "request_id", "text", "capture_image", "action_id"} or "request_id" not in value:
            raise AppServiceError("invalid_request", "Expected an application request")
        request_id = value["request_id"]
        if not isinstance(request_id, str) or REQUEST_ID.fullmatch(request_id) is None:
            raise AppServiceError("invalid_request", "request_id must contain 8 to 64 letters, digits, underscores or dashes")
        if "text" in value and (not isinstance(value["text"], str) or len(value["text"]) > MAX_TEXT_CHARACTERS):
            raise AppServiceError("invalid_request", "Application text exceeds its limit or is not a string")
        if "capture_image" in value and type(value["capture_image"]) is not bool:
            raise AppServiceError("invalid_request", "capture_image must be a boolean")
        action_id = value.get("action_id")
        if action_id is not None and (not isinstance(action_id, str) or ACTION_ID.fullmatch(action_id) is None):
            raise AppServiceError("invalid_request", "Invalid action identifier")
    elif kind in ("listen_start", "listen_stop", "cancel", "stop"):
        if set(value) != {"type"}:
            raise AppServiceError("invalid_request", "Unexpected application control fields")
    else:
        raise AppServiceError("invalid_request", "Unknown application command")
    return value


def create_app_routes(allowed_origins: set[str]) -> list:
    origins = frozenset(allowed_origins)
    connections: set[str] = set()

    async def read(request, name):
        service = getattr(request.app.state, "apps", None)
        if service is None:
            return JSONResponse({"schema_version": 1, "error": {
                "code": "apps_unavailable", "message": "Application services are not configured"}},
                status_code=503, headers=HEADERS)
        try:
            result = await asyncio.to_thread(getattr(service, name))
        except AppServiceError as exc:
            return JSONResponse({"schema_version": 1, "error": {"code": exc.code, "message": str(exc)}},
                                status_code=503, headers=HEADERS)
        except Exception:
            return JSONResponse({"schema_version": 1, "error": {
                "code": "apps_unavailable", "message": "Application status is unavailable"}},
                status_code=503, headers=HEADERS)
        return JSONResponse(result, headers=HEADERS)

    async def status(request):
        return await read(request, "capabilities")

    async def actions(request):
        return await read(request, "actions_snapshot")

    async def live(socket: WebSocket):
        if socket.headers.get("origin") not in origins or len(connections) >= 4:
            await socket.close(code=1008)
            return
        service = getattr(socket.app.state, "apps", None)
        if service is None:
            await socket.close(code=1013, reason="apps_unavailable")
            return
        owner = uuid.uuid4().hex
        connections.add(owner)
        sending = asyncio.Lock()
        connection_closing = threading.Event()
        inbox: asyncio.Queue[str] = asyncio.Queue(maxsize=10)
        starting = None

        async def send_json(message):
            async with sending:
                await asyncio.wait_for(socket.send_json(message), timeout=3)

        async def send_state():
            async with sending:
                state = service.snapshot(owner)
                await asyncio.wait_for(socket.send_json(state), timeout=3)

        async def publish():
            revision, sent_at = -1, 0.0
            session_id, sequence = None, 0
            while True:
                # Read under the sending lock: a command cannot publish a newer
                # snapshot and then be followed by an older cached snapshot.
                async with sending:
                    state = service.snapshot(owner)
                    if state["session_id"] != session_id:
                        session_id, sequence = state["session_id"], 0
                    if state["revision"] != revision or time.monotonic() - sent_at >= 5:
                        await asyncio.wait_for(socket.send_json(state), timeout=3)
                        revision, sent_at = state["revision"], time.monotonic()
                    for event in service.events(owner, sequence):
                        if event["seq"] <= sequence:
                            continue
                        current = service.snapshot(owner)
                        if not current["owned"] or current["session_id"] != event["session_id"]:
                            break
                        if (current.get("turn_id") is not None and event.get("turn_id") is not None
                                and current["turn_id"] != event["turn_id"]):
                            sequence = event["seq"]
                            continue
                        await asyncio.wait_for(socket.send_json(event), timeout=3)
                        sequence = event["seq"]
                await asyncio.sleep(0.05)

        async def receive_commands():
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
                try:
                    size = len(raw.encode("utf-8"))
                except UnicodeError:
                    await socket.close(code=1007)
                    return
                if size > MAX_COMMAND_BYTES or count > 10:
                    await socket.close(code=1008)
                    return
                try:
                    inbox.put_nowait(raw)
                except asyncio.QueueFull:
                    await socket.close(code=1008)
                    return

        async def commands():
            nonlocal starting
            while True:
                raw = await inbox.get()
                try:
                    command = _command(raw)
                    kind = command["type"]
                    if kind == "start":
                        # Configuration checks can wait on disk locks. Keep
                        # receiving disconnects while the worker checks them;
                        # cancellation is checked before it can claim an owner.
                        starting = asyncio.create_task(asyncio.to_thread(
                            service.start, owner, scene=command["scene"],
                            listen=command.get("listen", False), speak=command.get("speak", False),
                            cancelled=connection_closing))
                        await asyncio.shield(starting)
                    elif kind == "submit":
                        service.submit(owner, request_id=command["request_id"], text=command.get("text", ""),
                                       capture_image=command.get("capture_image", False),
                                       action_id=command.get("action_id"))
                    elif kind in ("listen_start", "listen_stop"):
                        service.set_listening(owner, enabled=kind == "listen_start")
                    elif kind == "cancel":
                        service.cancel(owner)
                    elif kind == "stop":
                        service.stop(owner)
                    await send_state()
                except AppServiceError as exc:
                    await send_json({"schema_version": 1, "type": "error", "error": {
                        "code": exc.code, "message": str(exc)}})

        tasks = []
        try:
            await socket.accept()
            tasks = [asyncio.create_task(publish()), asyncio.create_task(receive_commands()),
                     asyncio.create_task(commands())]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (WebSocketDisconnect, asyncio.TimeoutError, OSError):
            pass
        except Exception:
            try:
                await send_json({"schema_version": 1, "type": "error", "error": {
                    "code": "internal_error", "message": "Application control failed"}})
            except (RuntimeError, WebSocketDisconnect, asyncio.TimeoutError, OSError):
                pass
        finally:
            with CancelScope(shield=True):
                # Signal hardware before awaiting transport or startup cleanup.
                connection_closing.set()
                try:
                    service.stop(owner)
                except Exception:
                    pass
                connections.discard(owner)
                for task in tasks:
                    task.cancel()
                try:
                    if starting is not None:
                        await asyncio.shield(starting)
                except Exception:
                    pass
                finally:
                    try:
                        service.stop(owner)
                    except Exception:
                        pass
                await asyncio.gather(*tasks, return_exceptions=True)
                try:
                    await asyncio.wait_for(socket.close(), timeout=1)
                except (RuntimeError, WebSocketDisconnect, asyncio.TimeoutError, OSError):
                    pass

    return [Route("/api/apps/status", status), Route("/api/apps/actions", actions),
            WebSocketRoute("/api/apps/live", live)]
