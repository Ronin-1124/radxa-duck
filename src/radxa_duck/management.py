"""Board management adapters; background mutations outlive browser connections."""

from __future__ import annotations

import asyncio
from functools import partial
import json
from pathlib import Path
import subprocess

from starlette.responses import JSONResponse
from starlette.routing import Route

from .audio_devices import AudioDeviceError, AudioDevices
from .cloud_config import CloudConfigError, CloudConfigStore
from .device import DeviceStatus
from .network import NetworkError, NetworkService
from .tasks import TaskFailure, TaskManager, TaskManagerError


HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
KINDS = {"network.scan", "network.connect", "cloud.test", "power.reboot", "power.shutdown", "update.check"}


class ManagementError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class UpdateStatus:
    """Expose deployment prerequisites without inventing a release channel."""

    def snapshot(self):
        version = None
        try:
            value = subprocess.run(
                ["dpkg-query", "-W", "-f=${db:Status-Status}\\t${Version}", "radxa-duck"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=2, check=False, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
            if value.returncode == 0 and len(value.stdout) < 512:
                status, separator, candidate = value.stdout.decode("ascii").partition("\t")
                if status == "installed" and separator:
                    version = candidate.strip()
        except (OSError, subprocess.SubprocessError, UnicodeError):
            pass
        return {"schema_version": 1, "type": "update_status", "package": "radxa-duck",
                "installed": version is not None, "installed_version": version,
                "latest_version": None, "available": False,
                "reason": "release_source_unconfigured" if version else "package_not_installed",
                "capabilities": {"check": False, "install": False, "rollback": False}}

    def close(self):
        pass


class ManagementServices:
    def __init__(self, state_dir: Path, speech, vision):
        self._hardware = (speech, vision)
        self.device = DeviceStatus()
        self.audio = AudioDevices()
        self.network = NetworkService()
        self.updates = UpdateStatus()
        self.cloud = self.tasks = None
        try:
            self.cloud = CloudConfigStore(state_dir)
        except CloudConfigError:
            pass
        try:
            self.tasks = TaskManager(state_dir)
        except TaskManagerError:
            pass
        from .power import PowerService
        self.power = PowerService((speech, vision))

    def attach_application(self, apps):
        from .power import PowerService
        self.power = PowerService((apps, *self._hardware))

    def attach_runtime(self, brain, apps, observer):
        from .power import PowerService
        self.power = PowerService((brain, apps, observer, *self._hardware))

    def close(self):
        # Signal checkpoint rollback before waiting for task workers.
        self.network.close()
        if self.tasks is not None:
            self.tasks.close()
        if self.cloud is not None:
            self.cloud.close()
        self.device.close()


def create_management_routes(allowed_origins):
    def response(value, status=200, *, location=None):
        headers = dict(HEADERS)
        if location is not None:
            headers["Location"] = location
        return JSONResponse(value, status_code=status, headers=headers)

    def fail(code, message, status):
        return response({"schema_version": 1, "error": {"code": code, "message": message}}, status)

    async def body(request, allowed, required=()):
        if (request.headers.get("origin") not in allowed_origins
                or request.headers.get("x-radxa-duck-request") != "1"):
            raise ManagementError("forbidden", "An allowed browser origin and request header are required")
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise ManagementError("invalid_request", "Expected application/json")
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 8192:
                raise ManagementError("invalid_request", "Management request exceeds its size limit")
            raw.extend(chunk)
        try:
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate")
                    result[key] = value
                return result
            value = json.loads(raw, object_pairs_hook=unique,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))
        except (ValueError, UnicodeError, RecursionError):
            raise ManagementError("invalid_request", "Expected a JSON object") from None
        if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
            raise ManagementError("invalid_request", "Unexpected or missing management fields")
        return value

    def manager(request):
        value = getattr(request.app.state, "management", None)
        if value is None:
            raise ManagementError("management_unavailable", "Board management is not configured")
        return value

    def need(value, name):
        service = getattr(value, name)
        if service is None:
            raise ManagementError(name + "_unavailable", "Private management storage is unavailable")
        return service

    def guarded(handler):
        async def wrapped(request):
            try:
                return await handler(request)
            except (ManagementError, CloudConfigError, TaskManagerError, AudioDeviceError, NetworkError) as exc:
                code = exc.code
                status = (403 if code in ("forbidden", "permission_denied") else
                          404 if code == "not_found" else
                          409 if code in ("conflict", "busy", "device_busy", "confirmation_unavailable", "update_unavailable") else
                          400 if code in ("invalid_request", "unsupported_security") else 503)
                return fail(code, str(exc), status)
        return wrapped

    async def device(request):
        return response(await asyncio.to_thread(manager(request).device.snapshot))

    async def network(request):
        return response(await asyncio.to_thread(manager(request).network.snapshot))

    def network_operation(operation, *args, **kwargs):
        def run(context):
            try:
                if kwargs.pop("with_task_id", False):
                    return operation(context, *args, task_id=context.task_id, **kwargs)
                return operation(context, *args, **kwargs)
            except NetworkError as exc:
                raise TaskFailure(exc.code, str(exc), result={"rollback": exc.rollback}) from None
        return run

    async def submit(request, kind, operation, *, fields, key):
        tasks = need(manager(request), "tasks")
        task = await asyncio.to_thread(tasks.submit, kind, operation, request_id=fields["request_id"], key=key)
        return response(task, 202, location="/api/tasks/" + task["task_id"])

    async def scan(request):
        fields = await body(request, {"request_id"}, {"request_id"})
        service = manager(request).network
        return await submit(request, "network.scan", network_operation(service.scan), fields=fields, key="network")

    async def connect(request):
        fields = await body(request, {"request_id", "ssid", "password", "security"}, {"request_id", "ssid"})
        service = manager(request).network
        arguments = {key: value for key, value in fields.items() if key != "request_id"}
        service.validate_connection(**arguments)
        operation = network_operation(service.connect, with_task_id=True, **arguments)
        return await submit(request, "network.connect", operation, fields=fields, key="network")

    async def confirm(request):
        fields = await body(request, {"task_id"}, {"task_id"})
        services = manager(request)
        task = await asyncio.to_thread(need(services, "tasks").get, fields["task_id"])
        if task["kind"] != "network.connect":
            raise ManagementError("invalid_request", "Expected a Wi-Fi connection task")
        if task["state"] == "succeeded" and (task.get("result") or {}).get("confirmed") is True:
            return response({"task_id": task["task_id"], "confirmed": True})
        return response(await asyncio.to_thread(services.network.confirm, fields["task_id"]))

    async def audio(request):
        service = manager(request).audio
        if request.method == "GET":
            return response(await asyncio.to_thread(service.snapshot))
        fields = await body(request, {"volume_percent", "muted"})
        if not fields:
            raise ManagementError("invalid_request", "An output control is required")
        return response(await asyncio.to_thread(service.set_output, **fields))

    async def cloud(request):
        service = need(manager(request), "cloud")
        if request.method == "GET":
            return response(await asyncio.to_thread(service.snapshot))
        fields = await body(request, {"expected_revision", "provider", "base_url", "model", "timeout_seconds", "api_key"},
                            {"expected_revision"})
        revision = fields.pop("expected_revision")
        return response(await asyncio.to_thread(service.save, fields, expected_revision=revision))

    async def test_cloud(request):
        fields = await body(request, {"request_id", "expected_revision"}, {"request_id", "expected_revision"})
        service = need(manager(request), "cloud")
        # Reject malformed revisions before creating a background task.
        if type(fields["expected_revision"]) is not int or fields["expected_revision"] < 0:
            raise ManagementError("invalid_request", "Expected a nonnegative configuration revision")
        def run(context):
            context.check_cancelled()
            context.update("testing")
            try:
                result = service.test_connection(expected_revision=fields["expected_revision"])
            except CloudConfigError as exc:
                raise TaskFailure(exc.code, str(exc)) from None
            if result["state"] == "failed":
                raise TaskFailure(result["error"]["code"], result["error"]["message"], result=result)
            return result
        return await submit(request, "cloud.test", run, fields=fields, key="cloud")

    async def tasks(request):
        service = need(manager(request), "tasks")
        if "task_id" in request.path_params:
            return response(await asyncio.to_thread(service.get, request.path_params["task_id"]))
        if set(request.query_params) - {"kind", "active"}:
            raise ManagementError("invalid_request", "Unknown task filter")
        kind, active = request.query_params.get("kind"), request.query_params.get("active")
        if (kind is not None and kind not in KINDS) or active not in (None, "true", "false"):
            raise ManagementError("invalid_request", "Invalid task filter")
        items = await asyncio.to_thread(service.list)
        if kind:
            items = [item for item in items if item["kind"] == kind]
        if active is not None:
            items = [item for item in items if (item["state"] in ("queued", "running")) == (active == "true")]
        return response({"schema_version": 1, "tasks": items})

    async def power_status(request):
        return response(await asyncio.to_thread(manager(request).power.snapshot))

    async def power(request):
        fields = await body(request, {"request_id", "action"}, {"request_id", "action"})
        if fields["action"] not in ("reboot", "shutdown"):
            raise ManagementError("invalid_request", "Power action must be reboot or shutdown")
        service = manager(request).power
        return await submit(request, "power." + fields["action"],
                            partial(service.execute, action=fields["action"]), fields=fields, key="maintenance")

    async def update_status(request):
        return response(await asyncio.to_thread(manager(request).updates.snapshot))

    async def update_check(request):
        fields = await body(request, {"request_id"}, {"request_id"})
        service = manager(request).updates
        def run(context):
            context.check_cancelled()
            context.update("checking")
            status = service.snapshot()
            raise TaskFailure("update_unavailable", "A packaged installation and release source are required", result=status)
        return await submit(request, "update.check", run, fields=fields, key="maintenance")

    async def update_install(request):
        await body(request, {"request_id", "version"}, {"request_id", "version"})
        raise ManagementError("update_unavailable", "A packaged installation and release source are required")

    definitions = [
        ("/api/device/status", device, ["GET"]),
        ("/api/network/status", network, ["GET"]),
        ("/api/network/scan", scan, ["POST"]),
        ("/api/network/connect", connect, ["POST"]),
        ("/api/network/confirm", confirm, ["POST"]),
        ("/api/audio", audio, ["GET", "PATCH"]),
        ("/api/cloud/config", cloud, ["GET", "PUT"]),
        ("/api/cloud/test", test_cloud, ["POST"]),
        ("/api/tasks", tasks, ["GET"]),
        ("/api/tasks/{task_id}", tasks, ["GET"]),
        ("/api/power/status", power_status, ["GET"]),
        ("/api/power", power, ["POST"]),
        ("/api/update/status", update_status, ["GET"]),
        ("/api/update/check", update_check, ["POST"]),
        ("/api/update/install", update_install, ["POST"]),
    ]
    return [Route(path, guarded(handler), methods=methods) for path, handler, methods in definitions]
