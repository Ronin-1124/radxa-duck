#!/usr/bin/env python3
"""Build an offline Debian 12 ARM64 application from verified release inputs.

Inputs are a pinned wheelhouse, model directory and an already built web tree.
No development environment, credentials, user configuration or recordings enter
the package. Hardware drivers and RKNN bindings remain supplied by Radxa OS.
"""

from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy_checked(source, target, size, checksum):
    if source.is_symlink() or not source.is_file() or source.stat().st_size != size or digest(source) != checksum:
        raise ValueError("Unverified release asset: " + str(source))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(0o644)


def models(source, target):
    from radxa_duck.asr import MODEL_FILES, MODEL_ID
    from radxa_duck.vad import MODEL_BYTES, MODEL_SHA256
    from radxa_duck.vision import MODELS
    entries = []
    def add(relative, size, checksum, origin):
        copy_checked(source / relative, target / relative, size, checksum)
        entries.append({"path": relative, "bytes": size, "sha256": checksum, "source": origin})
    for name, size, checksum in MODEL_FILES:
        model, revision = MODEL_ID.split('/')
        add("asr/" + name, size, checksum, f"https://huggingface.co/csukuangfj/{model}/tree/{revision}")
    add("vad.onnx", MODEL_BYTES, MODEL_SHA256,
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx")
    for name in ("yolov8n", "yolo26n", "yolo26s"):
        size, checksum, _ = MODELS[name]
        add("vision/" + name + ".rknn", size, checksum,
            "https://github.com/Ronin-1124/radxa-duck/blob/main/src/radxa_duck/vision.py")
    notices = source / "notices"
    if not notices.is_dir() or not any(notices.iterdir()):
        raise ValueError("Model provenance and license notices are required")
    safe_tree(notices, target / "notices")
    return entries


def safe_tree(source, target):
    for path in sorted(source.rglob("*")):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError("Unsafe release input: " + str(path))
        if path.is_file():
            dest = target / path.relative_to(source)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
            dest.chmod(0o644)


def wheels(source, target):
    requirements = {}
    for line in (ROOT / "packaging/runtime-requirements.txt").read_text().splitlines():
        if line and not line.startswith("#"):
            name, version = line.split("==")
            requirements[re.sub(r"[-_.]+", "-", name).lower()] = version
    found, records, occupied = {}, [], set()
    locked = {row['filename']: row for row in json.loads((ROOT / 'packaging/runtime-wheels.json').read_text())}
    for wheel in sorted(source.glob("*.whl")):
        if wheel.name not in locked or digest(wheel) != locked[wheel.name]['sha256']:
            raise ValueError("Wheel checksum mismatch: " + wheel.name)
        with zipfile.ZipFile(wheel) as archive:
            metadata = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            if len(metadata) != 1:
                raise ValueError("Invalid wheel metadata")
            info = BytesParser().parsebytes(archive.read(metadata[0]))
            name, version = re.sub(r"[-_.]+", "-", info["Name"]).lower(), info["Version"]
            if name not in requirements or version != requirements[name] or name in found:
                raise ValueError("Unpinned or duplicate wheel: " + wheel.name)
            if not (wheel.name.endswith("-py3-none-any.whl") or "aarch64" in wheel.name
                    and ("-cp311-" in wheel.name or "-abi3-" in wheel.name or "-py3-none-" in wheel.name)):
                raise ValueError("Wheel does not target Debian 12 ARM64: " + wheel.name)
            found[name] = version
            records.append({"name": name, "version": version, "wheel": wheel.name, "sha256": digest(wheel)})
            for member in archive.infolist():
                path = PurePosixPath(member.filename)
                if path.is_absolute() or ".." in path.parts or member.file_size > 256 * 1024 * 1024:
                    raise ValueError("Unsafe wheel member")
                if member.is_dir():
                    continue
                if any(part.endswith(".data") for part in path.parts):
                    continue  # Executable entry points are not used by the service.
                if member.filename in occupied:
                    raise ValueError("Overlapping wheel files")
                occupied.add(member.filename)
                destination = target.joinpath(*path.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as handle, destination.open("wb") as output:
                    shutil.copyfileobj(handle, output)
                destination.chmod(0o644)
    if found != requirements:
        raise ValueError("Incomplete pinned wheelhouse: " + str(set(requirements) - found.keys()))
    return records


def build(args):
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Use a three-part release version")
    if not (args.web_dist / "index.html").is_file():
        raise ValueError("Built web assets are required")
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="radxa-duck-package-") as temporary:
        stage = Path(temporary)
        stage.chmod(0o755)
        lib, share, control = stage / "usr/lib/radxa-duck", stage / "usr/share/radxa-duck", stage / "DEBIAN"
        lib.mkdir(parents=True)
        control.mkdir()
        code = lib / "radxa_duck"
        code.mkdir()
        for path in (ROOT / "src/radxa_duck").glob("*.py"):
            shutil.copyfile(path, code / path.name)
        shutil.copyfile(ROOT / "packaging/entrypoint.py", lib / "entrypoint.py")
        manifest = {"schema_version": 1, "version": version, "architecture": "arm64", "python": "3.11",
                    "models": models(args.models_dir, share / "models"),
                    "runtime": wheels(args.wheelhouse, lib / "vendor")}
        manifest['application'] = [{"path": str(path.relative_to(lib)), "sha256": digest(path)}
                                   for path in sorted(code.glob('*.py'))]
        manifest['web'] = [{"path": str(path.relative_to(args.web_dist)), "sha256": digest(path)}
                           for path in sorted(args.web_dist.rglob('*')) if path.is_file()]
        try:
            manifest['source_revision'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
            manifest['source_dirty'] = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True))
        except subprocess.CalledProcessError:
            manifest['source_revision'], manifest['source_dirty'] = None, None
        metadata = lib / f"radxa_duck-{version}.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text(f"Metadata-Version: 2.1\nName: radxa-duck\nVersion: {version}\n")
        safe_tree(args.web_dist, share / "web")
        from radxa_duck.interaction import SOUNDS
        sound_root = ROOT / "assets/microduck"
        sound_manifest = json.loads((sound_root / "manifest.json").read_bytes())
        if set(sound_manifest["clips"]) != set(SOUNDS) or not (sound_root / "LICENSE").is_file():
            raise ValueError("Incomplete sound bank or license")
        for name, record in sound_manifest["clips"].items():
            copy_checked(sound_root / (name + ".wav"), share / "sounds/microduck" / (name + ".wav"),
                         record["bytes"], record["sha256"])
        safe_tree(sound_root, share / "sounds/microduck")
        manifest["sounds"] = [{"path": str(path.relative_to(ROOT / "assets/microduck")), "sha256": digest(path)}
                              for path in sorted((ROOT / "assets/microduck").rglob("*")) if path.is_file()]
        doc = stage / "usr/share/doc/radxa-duck"
        doc.mkdir(parents=True)
        shutil.copyfile(ROOT / "README.md", doc / "README.md")
        for pattern in ("LICENSE*", "COPYING*"):
            for license in (ROOT / "web/node_modules").rglob(pattern):
                if license.is_file() and not license.is_symlink():
                    dest = doc / "third-party/web" / license.relative_to(ROOT / "web/node_modules")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(license, dest)
        (share / "build-manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        for name, destination in (("config.json", "etc/radxa-duck/config.json"),
                                  ("radxa-duck.service", "lib/systemd/system/radxa-duck.service"),
                                  ("50-radxa-duck.rules", "usr/share/polkit-1/rules.d/50-radxa-duck.rules")):
            path = stage / destination
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "packaging" / name, path)
        for name in ("postinst", "prerm", "postrm"):
            shutil.copyfile(ROOT / "packaging" / name, control / name)
            (control / name).chmod(0o755)
        installed_size = sum(path.stat().st_size for path in stage.rglob("*") if path.is_file()) // 1024
        (control / "control").write_text(
            f"Package: radxa-duck\nVersion: {version}\nArchitecture: arm64\nSection: misc\nPriority: optional\n"
            f"Maintainer: Radxa Duck contributors\nInstalled-Size: {installed_size}\n"
            "Depends: python3 (>= 3.11), python3 (<< 3.12), python3-gi, python3-rknnlite2 (>= 2.3.0), python3-rknnlite2 (<< 2.4), "
            "rknpu2-rk356x, gir1.2-gstreamer-1.0, gir1.2-gst-plugins-base-1.0, gstreamer1.0-plugins-base, "
            "gstreamer1.0-plugins-good, libsamplerate0, libgomp1, alsa-utils, adduser, init-system-helpers, "
            "network-manager, policykit-1\nHomepage: https://github.com/Ronin-1124/radxa-duck\n"
            "Description: Resident voice and NPU vision interactions for Radxa ZERO 3W\n"
            " Offline application, web console and verified default model assets.\n")
        (control / "conffiles").write_text("/etc/radxa-duck/config.json\n")
        sums = []
        for path in sorted(stage.rglob("*")):
            if path.is_file() and control not in path.parents:
                sums.append(hashlib.md5(path.read_bytes()).hexdigest() + "  " + str(path.relative_to(stage)))
                path.chmod(0o644)
        (control / "md5sums").write_text("\n".join(sums) + "\n")
        epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
        for path in stage.rglob("*"):
            os.utime(path, (epoch, epoch))
        os.utime(stage, (epoch, epoch))
        output = args.output / f"radxa-duck_{version}_arm64.deb"
        subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "-z3", "--build", str(stage), str(output)], check=True,
                       env={**os.environ, 'SOURCE_DATE_EPOCH': str(epoch)})
        (args.output / "build-manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        (args.output / "SHA256SUMS").write_text(digest(output) + "  " + output.name + "\n")
        print(json.dumps({"package": str(output), "bytes": output.stat().st_size, "sha256": digest(output)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--web-dist", type=Path, default=ROOT / "web/dist")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    build(parser.parse_args())


if __name__ == "__main__":
    main()
