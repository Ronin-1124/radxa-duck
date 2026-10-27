"""Local speech control and live transcripts over a same-origin WebSocket."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
import uuid

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from .speech import SpeechError, SpeechService


def create_app(model_dir: Path, vad_model: Path, *, origins: tuple[str, ...],
               web_root: Path | None = None, service_factory=SpeechService) -> Starlette:
    allowed_origins = set()
    hosts = {"127.0.0.1", "localhost", "testserver"}
    for origin in origins:
        parsed = urlsplit(origin)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
                or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError("Origins must be HTTP(S) scheme and authority only")
        allowed_origins.add(origin.rstrip("/"))
        hosts.add(parsed.hostname)
    if not allowed_origins:
        raise ValueError("At least one browser origin is required")
    connections: set[str] = set()

    @asynccontextmanager
    async def lifespan(app):
        service = service_factory(model_dir, vad_model)
        app.state.speech = service
        try:
            yield
        finally:
            await asyncio.to_thread(service.close)

    async def health(request):
        state = request.app.state.speech.snapshot()
        return JSONResponse({"schema_version": 1, "state": state["state"],
                             "ready": state["ready"], "provider": "cpu"},
                            headers={"Cache-Control": "no-store"})

    async def live(socket: WebSocket):
        # Browser origins are required even on the LAN. No wildcard CORS or
        # browser microphone upload: the server always uses the board's HAT.
        if socket.headers.get("origin") not in allowed_origins or len(connections) >= 4:
            await socket.close(code=1008)
            return
        owner = uuid.uuid4().hex
        connections.add(owner)
        service = socket.app.state.speech
        sending = asyncio.Lock()

        async def send(data):
            async with sending:
                await asyncio.wait_for(socket.send_json(data), timeout=3)

        async def publish():
            revision = -1
            sent_at = 0.0
            while True:
                state = service.snapshot(owner)
                if state["revision"] != revision or time.monotonic() - sent_at >= 5:
                    await send(state)
                    revision = state["revision"]
                    sent_at = time.monotonic()
                await asyncio.sleep(0.1)

        async def commands():
            window_started = time.monotonic()
            count = 0
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
                if len(raw) > 1024 or count > 10:
                    await socket.close(code=1008)
                    return
                try:
                    command = json.loads(raw)
                    if not isinstance(command, dict) or set(command) != {"type"}:
                        raise SpeechError("invalid_request", "Expected a start or stop command")
                    if command["type"] == "start":
                        service.start(owner)
                    elif command["type"] == "stop":
                        service.stop(owner)
                    else:
                        raise SpeechError("invalid_request", "Unknown speech command")
                    await send(service.snapshot(owner))
                except json.JSONDecodeError:
                    await send({"type": "error", "error": {"code": "invalid_request", "message": "Expected JSON"}})
                except SpeechError as exc:
                    await send({"type": "error", "error": {"code": exc.code, "message": str(exc)}})

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
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                service.stop(owner)
            except SpeechError:
                pass
            connections.discard(owner)

    routes = [Route("/api/health", health), WebSocketRoute("/api/speech/live", live)]
    if web_root is not None:
        index = Path(web_root) / "index.html"
        if not index.is_file():
            raise ValueError("web_root must contain a built index.html")

        async def page(request):
            return FileResponse(index, headers={"Cache-Control": "no-cache"})

        routes.extend(Route(f"/{name}", page) for name in ("overview", "voice", "vision", "apps", "device", "settings"))
        routes.append(Mount("/", StaticFiles(directory=web_root, html=True), name="web"))
    return Starlette(routes=routes, lifespan=lifespan,
                     middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=sorted(hosts))])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--vad-model", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--origin", action="append", help="allowed browser origin, repeatable")
    parser.add_argument("--web-root", type=Path, help="built web assets to serve from the same process")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    origins = tuple(args.origin or (f"http://127.0.0.1:{args.port}", f"http://localhost:{args.port}"))
    app = create_app(args.model_dir, args.vad_model, origins=origins, web_root=args.web_root)
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, workers=1, proxy_headers=False,
                ws="websockets-sansio", ws_max_size=2048, ws_max_queue=4,
                ws_ping_interval=5, ws_ping_timeout=5,
                timeout_graceful_shutdown=35, access_log=False)


if __name__ == "__main__":
    main()
