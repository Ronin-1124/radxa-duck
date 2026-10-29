"""Local speech and camera control over same-origin WebSockets."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
import uuid

from anyio import CancelScope

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from .speech import SpeechError, SpeechService
from .app_routes import create_app_routes
from .app_service import AppService
from .brain import BrainService, TurnContext
from .cloud_client import CloudClient
from .speech_output import SpeechOutput
from .duck_output import DuckOutput
from .management import ManagementServices, create_management_routes
from .identity import IdentityStore
from .console_routes import create_console_routes
from .vision import MODELS
from .vision_routes import create_vision_routes
from .vision_service import VisionService, VisionServiceError
from .vision_observer import VisionObserver, create_observer_routes


def create_app(model_dir: Path, vad_model: Path, *, origins: tuple[str, ...],
               web_root: Path | None = None, service_factory=SpeechService,
               vision_models: dict[str, Path] | None = None, camera_device: str = "/dev/video0",
               camera_rotation: int = 0, vision_service_factory=VisionService,
               management_state_dir: Path | None = None,
               management_factory=ManagementServices, tts_model_dir: Path | None = None,
               tts_volume: int = 45, tts_speaker_id: int = 0, apps_factory=AppService,
               duck_sound_dir: Path | None = None, duck_volume: int = 100,
               brain_camera_enabled: bool = False, brain_microphone_enabled: bool = False) -> Starlette:
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
        vision = None
        management = None
        apps = None
        brain = identity = observer = None
        app.state.management = None
        app.state.apps = None
        try:
            vision = vision_service_factory(vision_models, device=camera_device, rotation=camera_rotation)
            app.state.vision = vision
            if management_state_dir is not None:
                initialization = asyncio.create_task(asyncio.to_thread(
                    management_factory, management_state_dir, service, vision))
                cancelled = None
                while not initialization.done():
                    try:
                        await asyncio.shield(initialization)
                    except asyncio.CancelledError as exc:
                        # Cancelling the await cannot stop its constructor
                        # thread. Retain the result so finally closes any task
                        # workers and file locks it acquired during startup.
                        if cancelled is None:
                            cancelled = exc
                management = initialization.result()
                app.state.management = management
                if cancelled is not None:
                    raise cancelled
            identity = IdentityStore(management_state_dir)
            app.state.identity = identity
            context = TurnContext(identity)
            app.state.turn_context = context
            observer = VisionObserver(vision)
            app.state.vision_observer = observer
            cloud_store = getattr(management, "cloud", None)
            apps = apps_factory(service, vision,
                                cloud=CloudClient(cloud_store) if cloud_store is not None else None,
                                cloud_config=cloud_store,
                                output=(DuckOutput(duck_sound_dir, volume_percent=duck_volume) if duck_sound_dir is not None else
                                        SpeechOutput(tts_model_dir, volume_percent=tts_volume, speaker_id=tts_speaker_id)),
                                context_provider=context.for_turn, vision_observer=observer, visual_policy=context.allows_image)
            app.state.apps = apps
            attach = getattr(management, "attach_application", None)
            if callable(attach):
                attach(apps)
            brain = BrainService(apps, service, observer, identity, cloud_config=cloud_store,
                                 device=getattr(management, "device", None), context=context,
                                 camera_enabled=brain_camera_enabled, microphone_enabled=brain_microphone_enabled)
            app.state.brain = brain
            attach_runtime = getattr(management, "attach_runtime", None)
            if callable(attach_runtime):
                attach_runtime(brain, apps, observer)
            yield
        finally:
            # Signal both workers promptly and finish both cleanups even when
            # one fails. Neither camera nor microphone waits for the other.
            # Stop resident admission before closing workers or config stores.
            # Every cleanup still runs even if this first stage fails.
            async def close_services():
                results = []
                if brain is not None:
                    results.extend(await asyncio.gather(asyncio.to_thread(brain.close), return_exceptions=True))
                results.extend(await asyncio.gather(*(asyncio.to_thread(item.close) for item in services),
                                                    return_exceptions=True))
                if identity is not None:
                    results.extend(await asyncio.gather(asyncio.to_thread(identity.close), return_exceptions=True))
                return results

            services = [service] if vision is None else [service, vision]
            if management is not None:
                services.append(management)
            if apps is not None:
                services.append(apps)
            if observer is not None:
                services.append(observer)
            cleanup = asyncio.create_task(close_services())
            cancelled = None
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError as exc:
                    # A cancelled lifespan must still signal and await both
                    # hardware workers; their close methods have own deadlines.
                    cancelled = exc
            results = cleanup.result()
            failures = [result for result in results if isinstance(result, Exception)]
            if failures:
                raise ExceptionGroup("Device service shutdown failed", failures)
            if cancelled is not None:
                raise cancelled

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
            with CancelScope(shield=True):
                try:
                    service.stop(owner)
                except SpeechError:
                    pass
                connections.discard(owner)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    routes = [Route("/api/health", health), WebSocketRoute("/api/speech/live", live)]
    routes.extend(create_vision_routes(allowed_origins))
    routes.extend(create_management_routes(allowed_origins))
    routes.extend(create_app_routes(allowed_origins))
    routes.extend(create_console_routes(allowed_origins))
    routes.extend(create_observer_routes(allowed_origins))
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
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".local/state/radxa-duck",
                        help="private persistent board configuration and task state")
    parser.add_argument("--tts-model-dir", type=Path, help="optional CPU speech synthesis model directory")
    parser.add_argument("--tts-volume", type=int, default=45, choices=range(0, 101), metavar="0..100")
    parser.add_argument("--tts-speaker-id", type=int, default=0)
    parser.add_argument("--duck-sound-dir", type=Path, help="verified Microduck procedural sound bank")
    parser.add_argument("--duck-volume", type=int, default=100, choices=range(0, 101), metavar="0..100")
    parser.add_argument("--vision-model", action="append", default=[], metavar="NAME=PATH",
                        help=f"configured RKNN model ({', '.join(MODELS)}), repeatable; omitted means preview only")
    parser.add_argument("--camera-device", default="/dev/video0")
    parser.add_argument("--camera-rotation", type=int, choices=(0, 90, 180, 270), default=0)
    parser.add_argument("--brain-camera", action=argparse.BooleanOptionalAction, default=True,
                        help="resident camera/NPU perception, enabled by default")
    parser.add_argument("--microphone", action=argparse.BooleanOptionalAction, default=True,
                        help="resident CPU speech input, enabled by default")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    vision_models = {}
    for configured in args.vision_model:
        name, separator, path = configured.partition("=")
        if not separator or name not in MODELS or not path or name in vision_models:
            parser.error(f"--vision-model requires a unique NAME=PATH; names: {', '.join(MODELS)}")
        vision_models[name] = Path(path)
    origins = tuple(args.origin or (f"http://127.0.0.1:{args.port}", f"http://localhost:{args.port}"))
    try:
        app = create_app(args.model_dir, args.vad_model, origins=origins, web_root=args.web_root,
                         vision_models=vision_models, camera_device=args.camera_device,
                         camera_rotation=args.camera_rotation, management_state_dir=args.state_dir,
                         tts_model_dir=args.tts_model_dir, tts_volume=args.tts_volume,
                         tts_speaker_id=args.tts_speaker_id, brain_camera_enabled=args.brain_camera,
                         duck_sound_dir=args.duck_sound_dir, duck_volume=args.duck_volume,
                         brain_microphone_enabled=args.microphone)
    except (ValueError, VisionServiceError) as exc:
        parser.error(str(exc))
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, workers=1, proxy_headers=False,
                ws="websockets-sansio", ws_max_size=12 * 1024, ws_max_queue=4,
                ws_ping_interval=5, ws_ping_timeout=5,
                timeout_graceful_shutdown=35, access_log=False)


if __name__ == "__main__":
    main()
