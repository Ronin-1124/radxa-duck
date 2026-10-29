"""Resident brain and identity HTTP/WS boundary; no browser-owned lifecycle."""

from __future__ import annotations

import asyncio
import time
import uuid

from anyio import CancelScope

from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from .brain import BrainError
from .identity import IdentityError, parse_json


HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def create_console_routes(allowed_origins):
    connections = set()

    def response(value, status=200):
        return JSONResponse(value, status_code=status, headers=HEADERS)

    async def body(request, limit):
        if (request.headers.get("origin") not in allowed_origins
                or request.headers.get("x-radxa-duck-request") != "1"):
            raise BrainError("forbidden")
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise BrainError("invalid_request")
        raw = bytearray()
        async with asyncio.timeout(3):
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > limit:
                    raise BrainError("invalid_request")
        try:
            value = parse_json(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise BrainError("invalid_request") from None
        if not isinstance(value, dict):
            raise BrainError("invalid_request")
        return value

    def guarded(handler):
        async def wrapped(request):
            try:
                return await handler(request)
            except (BrainError, IdentityError) as exc:
                code = exc.code if exc.code in {"forbidden", "conflict", "invalid_request", "config_unavailable", "unavailable"} else "unavailable"
                status = 403 if code == "forbidden" else 409 if code == "conflict" else 400 if code == "invalid_request" else 503
                return response({"error": {"code": code}}, status)
            except (TimeoutError, OSError):
                return response({"error": {"code": "unavailable"}}, 503)
        return wrapped

    async def status(request):
        return response(await asyncio.to_thread(request.app.state.brain.snapshot))

    async def context(request):
        return response(await asyncio.to_thread(request.app.state.turn_context.snapshot))

    async def control(request):
        value = await body(request, 4096)
        # Cancelling the HTTP wait does not cancel an admitted operation.
        # Its request ID remains usable to discover the exact original result.
        result = await asyncio.wait_for(asyncio.to_thread(request.app.state.brain.control, value), 4.5)
        return response(result)

    async def identity(request):
        service = request.app.state.identity
        if request.method == "GET":
            return response(await asyncio.to_thread(service.snapshot))
        value = await body(request, 32 * 1024)
        if set(value) != {"expected_revision", "profile"}:
            raise IdentityError("invalid_request")
        return response(await asyncio.to_thread(service.save, **value))

    async def events(socket):
        if socket.headers.get("origin") not in allowed_origins or len(connections) >= 4:
            await socket.close(code=1008)
            return
        observer_id = uuid.uuid4().hex
        connections.add(observer_id)
        service = socket.app.state.brain

        async def publish():
            revision, sent_at = -1, 0.0
            while True:
                value = await asyncio.to_thread(service.snapshot)
                if value["revision"] != revision or time.monotonic() - sent_at >= 5:
                    await asyncio.wait_for(socket.send_json(value), 3)
                    revision, sent_at = value["revision"], time.monotonic()
                await asyncio.sleep(0.1)

        async def receive():
            message = await socket.receive()
            if message["type"] != "websocket.disconnect":
                await socket.close(code=1008, reason="read_only_subscription")

        tasks = []
        try:
            await socket.accept()
            tasks = [asyncio.create_task(publish()), asyncio.create_task(receive())]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (WebSocketDisconnect, asyncio.TimeoutError, OSError):
            pass
        finally:
            with CancelScope(shield=True):
                connections.discard(observer_id)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                try:
                    await asyncio.wait_for(socket.close(), 1)
                except (RuntimeError, WebSocketDisconnect, asyncio.TimeoutError, OSError):
                    pass

    return [Route("/api/brain/status", guarded(status)),
            Route("/api/brain/context", guarded(context)),
            Route("/api/brain/control", guarded(control), methods=["POST"]),
            WebSocketRoute("/api/brain/events", events),
            Route("/api/identity", guarded(identity), methods=["GET", "PUT"])]
