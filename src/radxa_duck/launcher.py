"""Validate installed configuration and launch one board service."""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import hashlib
import ipaddress
import json
from pathlib import Path
import socket
import struct
import sys

from .identity import parse_json


PATHS = {"model_dir", "vad_model", "tts_model_dir", "duck_sound_dir", "state_dir", "web_root"}
FIELDS = PATHS | {"schema_version", "host", "port", "origins", "local_origins", "vision_models",
                  "camera_device", "camera_rotation", "brain_camera_enabled", "microphone_enabled", "tts_volume", "tts_speaker_id", "duck_volume"}


def local_origins(port):
    """Explicitly trust this machine's names and assigned IPv4 addresses."""
    names = {"localhost", "127.0.0.1", "[::1]", socket.gethostname(), socket.gethostname() + ".local"}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        for _, name in socket.if_nameindex():
            try:
                raw = fcntl.ioctl(connection.fileno(), 0x8915, struct.pack("256s", name.encode()[:15]))
                names.add(socket.inet_ntoa(raw[20:24]))
            except OSError:
                continue
    return {f"http://{name}:{port}" for name in names}


def load_config(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(16 * 1024 + 1)
    if len(raw) > 16 * 1024:
        raise ValueError("invalid_config")
    value = parse_json(raw)
    if isinstance(value, dict):
        # Existing v1 installations retain their explicit quiet output policy.
        value.setdefault("duck_sound_dir", None)
        value.setdefault("duck_volume", 100)
    if not isinstance(value, dict) or set(value) != FIELDS or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("invalid_config")
    for key in PATHS:
        item = value[key]
        if key in {"tts_model_dir", "duck_sound_dir"} and item is None:
            continue
        if not isinstance(item, str) or not Path(item).is_absolute() or ".." in Path(item).parts:
            raise ValueError("invalid_path")
        value[key] = Path(item)
    for key in ("local_origins", "brain_camera_enabled", "microphone_enabled"):
        if type(value[key]) is not bool:
            raise ValueError("invalid_config")
    for key, lo, hi in (("port", 1, 65535), ("tts_volume", 0, 100), ("duck_volume", 0, 100), ("tts_speaker_id", 0, 1000)):
        if type(value[key]) is not int or not lo <= value[key] <= hi:
            raise ValueError("invalid_config")
    if (not isinstance(value["host"], str) or len(value["host"]) > 253
            or not isinstance(value["camera_device"], str) or not value["camera_device"].startswith("/dev/")
            or type(value["camera_rotation"]) is not int or value["camera_rotation"] not in (0, 90, 180, 270)
            or not isinstance(value["origins"], list) or len(value["origins"]) > 32
            or not all(isinstance(item, str) and len(item) <= 2048 for item in value["origins"])):
        raise ValueError("invalid_config")
    ipaddress.ip_address(value["host"])
    from .vision import MODELS
    models = value["vision_models"]
    if not isinstance(models, dict) or set(models) - MODELS.keys():
        raise ValueError("invalid_config")
    for key, item in models.items():
        if not isinstance(item, str) or not Path(item).is_absolute() or ".." in Path(item).parts:
            raise ValueError("invalid_path")
        models[key] = Path(item)
    origins = set(value.pop("origins"))
    if value.pop("local_origins"):
        origins.update(local_origins(value["port"]))
    if not origins:
        raise ValueError("origin_required")
    value["origins"] = tuple(sorted(origins))
    del value["schema_version"]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/etc/radxa-duck/config.json"))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        if args.check:
            from .asr import _verify_models
            from .vad import MODEL_BYTES, MODEL_SHA256
            _verify_models(config["model_dir"])
            if (config["vad_model"].stat().st_size != MODEL_BYTES
                    or hashlib.sha256(config["vad_model"].read_bytes()).hexdigest() != MODEL_SHA256):
                raise ValueError("invalid_model")
            missing = [name for name in ("gi", "rknnlite", "sherpa_onnx", "cv2") if importlib.util.find_spec(name) is None]
            print(json.dumps({"ready": not missing, "missing_dependencies": missing}))
            raise SystemExit(bool(missing))
        host, port = config.pop("host"), config.pop("port")
        config["management_state_dir"] = config.pop("state_dir")
        config["brain_microphone_enabled"] = config.pop("microphone_enabled")
        from .server import create_app
        import uvicorn
        uvicorn.run(create_app(**config), host=host, port=port, workers=1, proxy_headers=False,
                    ws="websockets-sansio", ws_max_size=12 * 1024, ws_max_queue=4,
                    ws_ping_interval=5, ws_ping_timeout=5, timeout_graceful_shutdown=35, access_log=False)
    except (OSError, ValueError, KeyError, UnicodeError, RuntimeError):
        print("radxa-duck: invalid or unavailable installation configuration", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
