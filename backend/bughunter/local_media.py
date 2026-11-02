"""Mac-local video analysis orchestration. Never uploads media to Kaggle.

Installation is opt-in, bounded and allowlisted. Work is queued in background
threads. The existing knowledge service handles source persistence and indexing.
"""
from __future__ import annotations

import base64
import json
import os
import platform
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .store import Store, now

OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_VISION = "qwen3-vl:2b"
WHISPER_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin"
INSTALL_TARGETS = {"ollama", "ffmpeg", "whisper", "vision", "speech"}
# Recognize but do not trust arbitrary custom binary paths or remote Ollama URLs.
BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/Applications/Ollama.app/Contents/Resources", "/usr/bin")


def binary(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for folder in BIN_DIRS:
        path = Path(folder) / name
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


class LocalMediaRuntime:
    def __init__(self, store: Store):
        self.store = store
        self._lock = threading.RLock()
        self._install_thread: threading.Thread | None = None
        self._server: subprocess.Popen | None = None
        self._job = {"state": "idle", "detail": "", "target": None}

    def dispatch(self, method: str, p: dict):
        if method == "media.status":
            return self.status()
        if method == "media.configure":
            return self.configure(p)
        if method == "media.setup":
            return self.setup(p)
        if method == "media.start":
            return self.start()
        if method == "media.stop":
            return self.stop()
        if method == "media.timeline":
            return self.timeline(p["source_id"])
        if method == "media.control":
            return self.control(p["source_id"], p["operation"])
        raise ValueError(f"Unknown media operation: {method}")

    def config(self):
        saved = self.store.setting("local_media", {}) or {}
        return {"enabled": True, "vision_model": DEFAULT_VISION, "frame_interval": 12,
                "max_frames": 0, "adaptive_frames": True, "scene_threshold": 0.30, "vision_enabled": True, "speech_enabled": True,
                **saved}

    def configure(self, p):
        config = self.config()
        for key in ("enabled", "vision_enabled", "speech_enabled", "adaptive_frames"):
            if key in p:
                if not isinstance(p[key], bool):
                    raise ValueError(f"{key} must be true or false")
                config[key] = p[key]
        if "frame_interval" in p:
            value = float(p["frame_interval"])
            if not 2 <= value <= 120:
                raise ValueError("Frame interval must be 2–120 seconds")
            config["frame_interval"] = value
        if "max_frames" in p:
            value = int(p["max_frames"])
            if not 0 <= value <= 1000000:
                raise ValueError("Maximum frames must be 0 (no cap), or 1–1,000,000")
            config["max_frames"] = value
        if "scene_threshold" in p:
            value = float(p["scene_threshold"])
            if not 0.05 <= value <= 0.95:
                raise ValueError("Scene sensitivity must be between 0.05 and 0.95")
            config["scene_threshold"] = value
        if "vision_model" in p:
            value = str(p["vision_model"])
            if value not in {"qwen3-vl:2b", "qwen3-vl:4b", "qwen3-vl:8b"}:
                raise ValueError("Choose a supported local Qwen3-VL model")
            config["vision_model"] = value
        self.store.set_setting("local_media", config)
        return config

    def _ollama_tags(self):
        try:
            with httpx.Client(timeout=httpx.Timeout(3, connect=1), trust_env=False) as client:
                response = client.get(f"{OLLAMA_HOST}/api/tags")
                response.raise_for_status()
                return [m.get("name", "") for m in response.json().get("models", [])]
        except (httpx.HTTPError, ValueError, KeyError):
            return None

    def status(self):
        config = self.config()
        models = self._ollama_tags()
        speech_path = self.whisper_path()
        with self._lock:
            job = dict(self._job)
        return {"platform": platform.system(), "machine": platform.machine(),
                "config": config, "ollama_bin": bool(binary("ollama")),
                "ollama_running": models is not None, "installed_models": models or [],
                "vision_ready": models is not None and (config["vision_model"] in models or config["vision_model"] + ":latest" in models),
                "ffmpeg": bool(binary("ffmpeg") and binary("ffprobe")),
                "whisper": bool(binary("whisper-cli")), "speech_model": speech_path.is_file(),
                "speech_model_path": str(speech_path) if speech_path.is_file() else None,
                "setup": job}

    def whisper_path(self):
        # No arbitrary path from a website or video description is executed.
        custom = (self.store.setting("settings", {}) or {}).get("whisper_model_path")
        if custom and Path(custom).expanduser().is_file():
            return Path(custom).expanduser()
        return self.store.root / "models" / "ggml-base.bin"

    def start(self):
        if self._ollama_tags() is not None:
            return {"state": "running", "managed": False}
        exe = binary("ollama")
        if not exe:
            raise ValueError("Ollama is not installed. Use Setup in the Video AI panel.")
        with self._lock:
            if not self._server or self._server.poll() is not None:
                env = {**os.environ, "OLLAMA_HOST": "127.0.0.1:11434"}
                self._server = subprocess.Popen([exe, "serve"], env=env, stdin=subprocess.DEVNULL,
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                start_new_session=True)
        for _ in range(30):
            if self._ollama_tags() is not None:
                return {"state": "running", "managed": True}
            time.sleep(.2)
        raise ValueError("Ollama did not respond on local loopback; inspect macOS permissions or a conflicting server")

    def stop(self):
        # Only stop the child created by this app; do not kill the user's Ollama app.
        with self._lock:
            if self._server and self._server.poll() is None:
                self._server.terminate()
                try:
                    self._server.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    self._server.kill()
            self._server = None
        return {"state": "stopped", "note": "An independently running Ollama application is not terminated."}

    def setup(self, p):
        target = p.get("target")
        if target not in INSTALL_TARGETS or p.get("approved") is not True:
            raise ValueError("Choose a supported component and explicitly approve installing it")
        if platform.system() != "Darwin":
            raise ValueError("Automatic component installation is supported on macOS only")
        with self._lock:
            if self._install_thread and self._install_thread.is_alive():
                raise ValueError("Another component is currently being installed")
            self._job = {"state": "running", "target": target, "detail": "Starting"}
            self._install_thread = threading.Thread(target=self._setup_job, args=(target,), daemon=True)
            self._install_thread.start()
        return {"state": "started", "target": target}

    def _set_job(self, **values):
        with self._lock:
            self._job.update(values)

    def _setup_job(self, target):
        try:
            if target in {"ollama", "ffmpeg", "whisper"}:
                brew = binary("brew")
                if not brew:
                    raise RuntimeError("Homebrew is not installed. Install Homebrew once, then use this app for component setup.")
                formula = {"ollama": "ollama", "ffmpeg": "ffmpeg", "whisper": "whisper.cpp"}[target]
                self._set_job(detail=f"Installing {formula} with Homebrew")
                result = subprocess.run([brew, "install", formula], capture_output=True, text=True,
                                        stdin=subprocess.DEVNULL, timeout=3600, check=False)
                if result.returncode:
                    raise RuntimeError((result.stderr or result.stdout)[-350:])
            elif target == "vision":
                self._set_job(detail="Starting local model server")
                self.start()
                exe = binary("ollama")
                if not exe:
                    raise RuntimeError("Install Ollama first")
                model = self.config()["vision_model"]
                self._set_job(detail=f"Downloading {model} to this Mac (may be several GB)")
                result = subprocess.run([exe, "pull", model], capture_output=True, text=True,
                                        stdin=subprocess.DEVNULL, timeout=7200, check=False)
                if result.returncode:
                    raise RuntimeError((result.stderr or result.stdout)[-350:])
            elif target == "speech":
                target_path = self.store.root / "models" / "ggml-base.bin"
                target_path.parent.mkdir(parents=True, exist_ok=True)
                partial = target_path.with_suffix(".part")
                self._set_job(detail="Downloading the Whisper base model to the app data folder")
                request = urllib.request.Request(WHISPER_URL, headers={"User-Agent": "BugHunterClaudex/1"})
                try:
                    with urllib.request.urlopen(request, timeout=45) as src, partial.open("wb") as dest:
                        if urlparse(src.url).scheme != "https" or urlparse(src.url).hostname not in {"huggingface.co", "cdn-lfs.hf.co", "cas-bridge.xethub.hf.co", "cas-server.xethub.hf.co"}:
                            raise RuntimeError("Unexpected model download redirect")
                        total = 0
                        while block := src.read(1024 * 1024):
                            total += len(block)
                            if total > 600_000_000:
                                raise RuntimeError("Whisper model exceeded size limit")
                            dest.write(block)
                    if not 40_000_000 <= partial.stat().st_size <= 600_000_000:
                        raise RuntimeError("Whisper download is incomplete")
                    partial.replace(target_path)
                finally:
                    partial.unlink(missing_ok=True)
            self._set_job(state="ready", detail=f"{target} setup complete")
        except Exception as exc:
            self._set_job(state="error", detail=str(exc)[:350])

    def vision(self, image: Path, timestamp: float):
        self.start()
        prompt = ("Analyze this exact screenshot from a recorded software tutorial. "
                  "Describe the application/window, visible buttons, menu labels, fields, mouse cursor, "
                  "selected options, displayed text, and what action is actually observable. "
                  "State exactly what is visibly present; do not invent mouse clicks, keypresses, or intermediate actions. "
                  "Use headings 'Visible controls', 'Observed screen state', and 'Uncertain actions'. "
                  "If code is readable, quote only visible lines; never fill in code that is off-screen. "
                  "Separate observations from hypotheses. Use concise technical language. "
                  f"Video timestamp: {timestamp:.2f} seconds.")
        payload = {"model": self.config()["vision_model"], "stream": False, "keep_alive": "3m",
                   "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(image.read_bytes()).decode("ascii")]}],
                   "options": {"temperature": 0, "num_predict": 420}}
        with httpx.Client(timeout=httpx.Timeout(240, connect=5), trust_env=False) as client:
            response = client.post(OLLAMA_HOST + "/api/chat", json=payload)
            response.raise_for_status()
            return (response.json().get("message") or {}).get("content", "").strip()[:4000]

    def timeline(self, source_id):
        source = self.store.get("source", source_id)
        if not source:
            raise ValueError("Source not found")
        path = self.store.root / "artifacts" / source_id / "media_timeline.json"
        if not path.exists():
            return {"source_id": source_id, "items": [], "status": source.get("status", "queued")}
        return {"source_id": source_id, "items": json.loads(path.read_text()), "status": source.get("status", "queued")}

    def control(self, source_id, operation):
        if operation not in {"pause", "resume", "cancel"}:
            raise ValueError("Unsupported media operation")
        source = self.store.get("source", source_id)
        if not source:
            raise ValueError("Source not found")
        if source.get("status") not in {"processing", "queued"}:
            raise ValueError("Source is not currently being processed")
        self.store.put("source", {**source, "media_control": operation})
        return {"operation": operation, "source_id": source_id}

    def checkpoint(self, source_id, service):
        # Called between operations; never leave a thread hanging indefinitely on pause.
        while True:
            if service._stop.is_set():
                raise RuntimeError("Application shutting down")
            source = self.store.get("source", source_id)
            if source is None:
                raise RuntimeError("Source deleted")
            control = source.get("media_control")
            if control == "cancel":
                raise RuntimeError("Video processing cancelled by user")
            if control != "pause":
                return
            time.sleep(.25)

    def shutdown(self):
        self.stop()
