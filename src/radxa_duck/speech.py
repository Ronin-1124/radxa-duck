"""One continuous microphone session, controlled independently of HTTP requests."""

from __future__ import annotations

from collections import deque
from contextlib import ExitStack
from pathlib import Path
import threading
import time
import uuid

from .asr import AsrError, ZipformerAsr
from .audio_capture import CaptureError, open_pcm_stream
from .vad import SileroVad, VadError


class SpeechError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SpeechService:
    """A single worker owns the models and sound card; state contains no audio.

    A connection owns its session. Disconnecting stops that session, and a new
    connection never resumes recording automatically. Keep only 20 final texts
    and clear them five minutes after stopping or when starting a new session.
    """

    def __init__(self, model_dir: Path, vad_model: Path):
        self._model_dir, self._vad_model = Path(model_dir), Path(vad_model)
        self._condition = threading.Condition()
        self._state = "loading"
        self._ready = False
        self._closing = False
        self._owner: str | None = None
        self._session_id: str | None = None
        self._cancelled: threading.Event | None = None
        self._partial = ""
        self._segments: deque[dict] = deque(maxlen=20)
        self._segment_id = 0
        self._audio_seconds = 0.0
        self._error: dict | None = None
        self._finished_at: float | None = None
        self._revision = 0
        self._thread = threading.Thread(target=self._run, name="radxa-speech", daemon=True)
        self._thread.start()

    def _changed(self) -> None:
        self._revision += 1
        self._condition.notify_all()

    def _expire_text(self) -> None:
        if self._finished_at is not None and time.monotonic() - self._finished_at >= 300:
            self._segments.clear()
            self._partial = ""
            self._finished_at = None
            self._changed()

    def snapshot(self, owner: str | None = None) -> dict:
        with self._condition:
            self._expire_text()
            return {
                "type": "state", "schema_version": 1, "revision": self._revision,
                "state": self._state, "ready": self._ready,
                "session_id": self._session_id,
                "owned": owner is not None and self._owner == owner,
                "partial_text": self._partial,
                "segments": [dict(segment) for segment in self._segments],
                "audio_seconds": self._audio_seconds,
                "error": dict(self._error) if self._error else None,
                "backend": "sherpa-onnx/zipformer", "provider": "cpu",
            }

    def start(self, owner: str) -> None:
        if not isinstance(owner, str) or not owner:
            raise SpeechError("invalid_request", "A connection owner is required")
        with self._condition:
            if self._closing:
                raise SpeechError("closed", "Speech service is closing")
            if not self._ready:
                raise SpeechError("not_ready", "Speech models are not ready")
            if self._owner is not None:
                if self._owner == owner and self._state in ("starting", "listening"):
                    return
                raise SpeechError("busy", "A microphone session is already active")
            self._owner = owner
            self._session_id = uuid.uuid4().hex
            self._cancelled = threading.Event()
            self._partial = ""
            self._segments.clear()
            self._segment_id = 0
            self._audio_seconds = 0.0
            self._finished_at = None
            self._error = None
            self._state = "starting"
            self._changed()

    def stop(self, owner: str) -> None:
        with self._condition:
            if self._owner is None:
                return
            if self._owner != owner:
                raise SpeechError("not_owner", "Another connection controls the microphone")
            self._cancelled.set()
            self._partial = ""
            self._state = "stopping"
            self._changed()

    def _recording(self, session_id: str) -> None:
        with self._condition:
            if (self._session_id == session_id and self._cancelled is not None
                    and not self._cancelled.is_set()):
                self._state = "listening"
                self._changed()

    def _listen(self, asr: ZipformerAsr, vad: SileroVad,
                session_id: str, cancelled: threading.Event) -> None:
        failure = None
        try:
            with asr.open_stream(vad, cancelled=cancelled) as decoder:
                with open_pcm_stream(cancelled, on_recording=lambda: self._recording(session_id)) as chunks:
                    for pcm in chunks:
                        update = decoder.accept_pcm(pcm)
                        with self._condition:
                            if cancelled.is_set() or self._session_id != session_id:
                                break
                            self._partial = update["partial_text"]
                            self._audio_seconds = update["audio_seconds"]
                            for segment in update["finalized"]:
                                self._segment_id += 1
                                self._segments.append({"id": self._segment_id, **segment})
                            self._changed()
        except (CaptureError, AsrError, VadError) as exc:
            if exc.code != "cancelled":
                failure = {"code": exc.code, "message": str(exc)}
        except Exception:
            failure = {"code": "internal_error", "message": "Speech processing failed"}
        finally:
            with self._condition:
                if self._session_id == session_id:
                    self._owner = None
                    self._cancelled = None
                    self._partial = ""
                    self._error = failure
                    self._finished_at = time.monotonic()
                    self._state = "error" if failure else "idle"
                    self._changed()

    def _run(self) -> None:
        try:
            with ExitStack() as stack:
                asr = stack.enter_context(ZipformerAsr(self._model_dir))
                with self._condition:
                    if self._closing:
                        return
                vad = stack.enter_context(SileroVad(self._vad_model))
                with self._condition:
                    if self._closing:
                        return
                    self._ready = True
                    self._state = "idle"
                    self._changed()
                while True:
                    with self._condition:
                        while not self._closing and self._owner is None:
                            self._expire_text()
                            self._condition.wait(timeout=1)
                        if self._closing:
                            break
                        session_id, cancelled = self._session_id, self._cancelled
                    self._listen(asr, vad, session_id, cancelled)
        except Exception as exc:
            with self._condition:
                self._error = {
                    "code": getattr(exc, "code", "model_unavailable"),
                    "message": str(exc) if isinstance(exc, (AsrError, VadError)) else "Cannot initialize speech models",
                }
                self._state = "error"
                self._changed()
        finally:
            with self._condition:
                self._ready = False
                if self._closing:
                    self._state = "closed"
                    self._partial = ""
                    self._segments.clear()
                self._changed()

    def close(self, timeout: float = 30) -> None:
        with self._condition:
            self._closing = True
            if self._cancelled is not None:
                self._cancelled.set()
                self._state = "stopping"
            self._changed()
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise SpeechError("shutdown_timeout", "Speech worker did not finish shutdown")
