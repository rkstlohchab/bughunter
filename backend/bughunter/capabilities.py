"""Capability unlocks for the nine 'explicit non-capabilities' listed in AGENTS.md.

Each capability is operator-toggled via settings and consumed by patched existing
services. The gateway, scope and approval surfaces are NOT bypassed; the toggles
widen effect classifications, add new action classes, and expose Rust-side
isolated features (desktop control, memory bridge) to the Python backend so the
agent surface can see them when the operator wants.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import secrets
import socket
import ssl
import struct
import subprocess
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx
import keyring

from .store import Store, new_id, now
from .security import redact, redact_text


TOGGLES_KEY = "capability_toggles"
DEFAULT_TOGGLES = {
    "tor_direct_fallback": False,
    "nuclei_custom_templates": False,
    "provider_fallback_chain": [],
    "binary_replay": False,
    "websocket_replay": False,
    "cloud_transcription": False,
    "paid_fallback": False,
    "encrypted_backup": False,
    "bulk_course_downloader": False,
    "agent_can_confirm": False,
    "python_memory_bridge": False,
    "python_desktop_control": False,
}


def _toggles(store: Store) -> dict:
    saved = store.setting(TOGGLES_KEY, {}) or {}
    return {**DEFAULT_TOGGLES, **(saved if isinstance(saved, dict) else {})}


# ============================================================================
# 1. Direct-network fallback for Tor sources (public hosts only; .onion refused)
# ============================================================================

def direct_fetch(url: str, timeout: float = 15.0) -> dict:
    parsed = urlsplit(url)
    if (parsed.hostname or "").endswith(".onion"):
        raise ValueError("Direct fallback refuses .onion addresses; they require Tor.")
    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False,
                      headers={"User-Agent": "BugHunterResearch/0.1 (direct-fallback)"}) as client:
        response = client.get(url)
        content_type = response.headers.get("content-type", "").lower()
        if not (content_type.startswith("text/html") or content_type.startswith("text/plain")):
            raise ValueError(f"Direct fallback only accepts text/html or text/plain; got {content_type}")
        if len(response.content) > 1_000_000:
            raise ValueError("Direct fallback page exceeds 1 MB limit.")
        return {"status": response.status_code, "html": response.text,
                "type": content_type, "via": "direct-network"}


# ============================================================================
# 2. Custom Nuclei templates — operator-reviewed YAMLs loaded from a directory
# ============================================================================

NUCLEI_TEMPLATE_ROOT = "custom_nuclei_templates"
NUCLEI_ALLOWED_SEVERITY = {"info", "low", "medium", "high", "critical"}
NUCLEI_REJECTED_SECTIONS = ("code", "javascript", "network", "dns", "ssl",
                             "headless", "websocket", "whois")


def list_custom_templates(store: Store) -> list[dict]:
    base = store.root / NUCLEI_TEMPLATE_ROOT
    if not base.exists():
        return []
    out = []
    for path in sorted(base.glob("*.yaml")) + sorted(base.glob("*.yml")):
        if path.is_symlink() or not path.is_file():
            continue
        out.append({"id": path.stem, "path": str(path), "bytes": path.stat().st_size,
                     "mtime": path.stat().st_mtime})
    return out


def register_custom_template(store: Store, name: str, body: str) -> dict:
    if not isinstance(name, str) or not name.strip() or not name.replace("_", "").replace("-", "").isalnum():
        raise ValueError("Template name must be alphanumeric (with -/_ allowed).")
    if not isinstance(body, str) or len(body) > 200_000:
        raise ValueError("Template body must be a string under 200 KB.")
    lowered = body.lower()
    for section in NUCLEI_REJECTED_SECTIONS:
        if f"\n{section}:" in lowered or lowered.startswith(f"{section}:"):
            raise ValueError(f"Custom templates refuse the `{section}` section; HTTP checks only.")
    base = store.root / NUCLEI_TEMPLATE_ROOT
    base.mkdir(mode=0o700, exist_ok=True)
    target = base / f"{name}.yaml"
    target.write_text(body, encoding="utf-8")
    target.chmod(0o600)
    return {"id": name, "path": str(target), "bytes": target.stat().st_size,
             "note": "Operator-authored template registered. Nuclei will still run through Burp + gateway."}


def delete_custom_template(store: Store, name: str) -> dict:
    target = store.root / NUCLEI_TEMPLATE_ROOT / f"{name}.yaml"
    if target.exists() and target.is_file() and not target.is_symlink():
        target.unlink()
        return {"deleted": name}
    raise ValueError("Template not found.")


# ============================================================================
# 3. Silent provider fallback chain
# ============================================================================

FALLBACK_TRIGGERS = {"rate_limit", "auth", "transport", "timeout", "server_error"}


def classify_provider_error(response_or_exc) -> str:
    if isinstance(response_or_exc, httpx.HTTPStatusError):
        code = response_or_exc.response.status_code
        if code == 429: return "rate_limit"
        if code in (401, 403): return "auth"
        if 500 <= code < 600: return "server_error"
        return "transport"
    if isinstance(response_or_exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(response_or_exc, httpx.TransportError):
        return "transport"
    return "transport"


def resolve_fallback_provider(store: Store, failing_provider: str, error_class: str) -> str | None:
    toggles = _toggles(store)
    chain = toggles.get("provider_fallback_chain", []) or []
    if not isinstance(chain, list) or not chain:
        return None
    seen = False
    for item in chain:
        if item == failing_provider:
            seen = True
            continue
        if seen:
            if error_class in FALLBACK_TRIGGERS:
                return item
    return None


# ============================================================================
# 4. Binary & WebSocket replay — queue state-changing requests with bodies > 2 MiB
# ============================================================================

BINARY_MAX = 32 * 1024 * 1024
WEBSOCKET_MAX_FRAMES = 50


def queue_binary_action(app, project_id: str, request: dict, session_id: str,
                        run_id: str | None = None) -> dict:
    body_b64 = request.get("body_base64", "")
    if not isinstance(body_b64, str):
        raise ValueError("binary body must be base64-encoded as `body_base64`.")
    try:
        raw = base64.b64decode(body_b64, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("binary body base64 did not decode.") from exc
    if len(raw) > BINARY_MAX:
        raise ValueError(f"Binary body exceeds {BINARY_MAX // (1024*1024)} MiB cap.")
    normalized = {"method": request.get("method", "POST").upper(),
                   "url": request["url"],
                   "headers": request.get("headers", {}) or {},
                   "body_sha256": hashlib.sha256(raw).hexdigest(),
                   "body_bytes": len(raw),
                   "body_preview": raw[:256].hex()}
    stored_path = app.store.root / "binary_payloads" / f"{new_id('bin')}.bin"
    stored_path.parent.mkdir(mode=0o700, exist_ok=True)
    stored_path.write_bytes(raw)
    stored_path.chmod(0o600)
    action = app.store.put("action", {
        "project_id": project_id, "request": normalized, "session_id": session_id,
        "run_id": run_id, "status": "pending_approval", "effect": "state_changing",
        "reason": "Binary body replay (operator capability).",
        "request_hash": hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest(),
        "scope_hash": "", "approval": None, "evidence_ids": [],
        "binary_payload": str(stored_path),
    })
    app.store.event("action.queued", {"id": action["id"], "status": action["status"], "binary": True})
    return action


def queue_websocket_replay(app, project_id: str, url: str, frames: list,
                           session_id: str = "websocket", run_id: str | None = None) -> dict:
    if not isinstance(frames, list) or not 1 <= len(frames) <= WEBSOCKET_MAX_FRAMES:
        raise ValueError(f"Provide 1–{WEBSOCKET_MAX_FRAMES} frames.")
    cleaned = []
    for frame in frames:
        if not isinstance(frame, dict) or frame.get("direction") not in {"send", "receive"}:
            raise ValueError("Each frame needs direction: send|receive")
        payload = frame.get("payload", "")
        if not isinstance(payload, str) or len(payload) > 1_000_000:
            raise ValueError("Each frame payload must be a string under 1 MB.")
        cleaned.append({"direction": frame["direction"], "payload": payload,
                         "opcode": frame.get("opcode", "text")})
    normalized = {"method": "WEBSOCKET", "url": url, "frames": cleaned}
    action = app.store.put("action", {
        "project_id": project_id, "request": normalized, "session_id": session_id,
        "run_id": run_id, "status": "pending_approval", "effect": "state_changing",
        "reason": f"WebSocket replay with {len(cleaned)} frames.",
        "request_hash": hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest(),
        "scope_hash": "", "approval": None, "evidence_ids": [], "transport": "websocket",
    })
    app.store.event("action.queued", {"id": action["id"], "status": action["status"], "websocket": True})
    return action


# ============================================================================
# 5. Encrypted backup + Keychain export (AES-GCM with scrypt-derived key)
# ============================================================================

KEYCHAIN_PROVIDERS = ("claude_code", "anthropic", "openai", "openai_compatible",
                      "crucible", "remote_ollama", "deepseek")
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    if not isinstance(passphrase, str) or len(passphrase) < 12:
        raise ValueError("Use a passphrase of at least 12 characters.")
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt,
                           n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)


def _aes_gcm_encrypt(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes) -> tuple[bytes, bytes]:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise ValueError("Install `cryptography` for encrypted backups: pip install cryptography") from exc
    cipher = AESGCM(key)
    blob = cipher.encrypt(nonce, plaintext, aad)
    return blob[:-16], blob[-16:]


def _aes_gcm_decrypt(key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes, aad: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise ValueError("Install `cryptography` for encrypted backups: pip install cryptography") from exc
    return AESGCM(key).decrypt(nonce, ciphertext + tag, aad)


def export_keychain_credentials() -> dict:
    out = {}
    for provider in KEYCHAIN_PROVIDERS:
        try:
            secret = keyring.get_password("BugHunterClaudex", provider)
        except Exception:
            secret = None
        if secret:
            out[provider] = secret
    return out


def create_encrypted_backup(store: Store, destination: str, passphrase: str,
                            include_keychain: bool = True) -> dict:
    from .backup import create as plain_create
    workspace = store.root.parent / ".bughunter-backup-staging"
    workspace.mkdir(mode=0o700, exist_ok=True)
    plain = workspace / f"inner-{new_id('bk')}.zip"
    try:
        plain_result = plain_create(store.root, plain)
        inner_bytes = plain.read_bytes()
    finally:
        plain.unlink(missing_ok=True)
    keychain = export_keychain_credentials() if include_keychain else {}
    package = {"inner_zip_base64": base64.b64encode(inner_bytes).decode(),
                "keychain": keychain,
                "note": "Encrypted BugHunterClaudex backup. Decrypt with the same tool.",
                "schema": 1, "created_at": now()}
    plaintext = json.dumps(package).encode()
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    key = _derive_key(passphrase, salt)
    aad = b"bughunterclaudex-backup-v1"
    ciphertext, tag = _aes_gcm_encrypt(key, nonce, plaintext, aad)
    manifest = {"scheme": "AES-GCM/scrypt", "salt": salt.hex(), "nonce": nonce.hex(),
                 "tag": tag.hex(), "aad": aad.decode(), "N": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
                 "keychain_items": sorted(keychain.keys()), "inner_bytes": plain_result["uncompressed_bytes"]}
    dest = Path(destination).expanduser().absolute()
    if dest.exists():
        raise FileExistsError("Backup already exists; choose a new filename.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        archive.writestr("payload.bin", ciphertext)
    dest.chmod(0o600)
    return {"path": str(dest), "keychain_items": len(keychain),
            "inner_bytes": plain_result["uncompressed_bytes"]}


def decrypt_backup(archive_path: str, passphrase: str) -> dict:
    archive_path = Path(archive_path).expanduser().absolute()
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        ciphertext = archive.read("payload.bin")
    salt = bytes.fromhex(manifest["salt"])
    nonce = bytes.fromhex(manifest["nonce"])
    tag = bytes.fromhex(manifest["tag"])
    aad = manifest["aad"].encode()
    key = _derive_key(passphrase, salt)
    plaintext = _aes_gcm_decrypt(key, nonce, ciphertext, tag, aad)
    package = json.loads(plaintext)
    return {"keychain": package.get("keychain", {}),
            "inner_zip_bytes": base64.b64decode(package["inner_zip_base64"]),
            "created_at": package.get("created_at")}


# ============================================================================
# 6. Cloud transcription (hosted Whisper endpoint — operator supplies key + URL)
# ============================================================================

def cloud_transcribe(store: Store, audio_path: str) -> dict:
    config = store.setting("cloud_transcription", {}) or {}
    endpoint = config.get("endpoint", "").strip()
    api_key = keyring.get_password("BugHunterClaudex", "cloud_transcription") or config.get("api_key_plain", "")
    if not endpoint or not api_key:
        raise ValueError("Configure a cloud transcription endpoint and key first.")
    audio = Path(audio_path).expanduser().absolute()
    if not audio.is_file() or audio.stat().st_size > 100 * 1024 * 1024:
        raise ValueError("Audio file must exist and be under 100 MB.")
    with httpx.Client(timeout=600, follow_redirects=False) as client:
        files = {"file": (audio.name, audio.read_bytes(), "audio/mpeg")}
        data = {"model": config.get("model", "whisper-1")}
        response = client.post(endpoint, headers={"Authorization": f"Bearer {api_key}"},
                                files=files, data=data)
        response.raise_for_status()
        return {"text": response.json().get("text", ""),
                 "via": endpoint, "model": data["model"]}


# ============================================================================
# 7. Bulk course downloader (YouTube channel/playlist with optional cookie file)
# ============================================================================

def bulk_course_download(store: Store, source_url: str, cookie_file: str | None = None,
                         max_items: int = 100) -> dict:
    if not isinstance(source_url, str) or not source_url.startswith(("http://", "https://")):
        raise ValueError("Provide a valid course source URL.")
    if not 1 <= int(max_items) <= 1000:
        raise ValueError("max_items must be 1–1000.")
    try:
        import yt_dlp
    except ImportError as exc:
        raise ValueError("yt-dlp is unavailable in this backend environment.") from exc
    workspace = store.root / "bulk_downloads" / new_id("bulk")
    workspace.mkdir(parents=True, mode=0o700, exist_ok=True)
    options = {
        "outtmpl": str(workspace / "%(playlist_index)s-%(title).80s-%(id)s.%(ext)s"),
        "format": "bestaudio/best",
        "playlistend": max_items,
        "quiet": True, "no_warnings": True,
        "ignoreerrors": True,
    }
    if cookie_file:
        resolved = Path(cookie_file).expanduser().absolute()
        if not resolved.is_file():
            raise ValueError("Cookie file not found.")
        options["cookiefile"] = str(resolved)
    downloaded = []
    with yt_dlp.YoutubeDL(options) as yd:
        info = yd.extract_info(source_url, download=True)
    entries = info.get("entries", [info]) if isinstance(info, dict) else []
    for entry in entries:
        if not entry:
            continue
        downloaded.append({"id": entry.get("id"), "title": entry.get("title"),
                            "duration_s": entry.get("duration"),
                            "filepath": entry.get("requested_downloads", [{}])[0].get("filepath")})
    return {"workspace": str(workspace), "items": downloaded,
            "note": "Bulk-downloaded media is unreviewed. Ingest files individually to verify terms-of-service."}


# ============================================================================
# 8. Python-side memory bridge — thin stdio client over mac_memory_mcp.py
#    Previously isolated to the Rust shell; now callable from backend.
# ============================================================================

class MacMemoryBridgeClient:
    def __init__(self, store: Store, server_script: str | None = None):
        self.store = store
        self.lock = threading.Lock()
        self.process = None
        self.sequence = 0
        self.pending = {}
        self.reader = None
        self.server_script = server_script or str(
            Path(__file__).resolve().parents[2] / "src-tauri" / "resources" / "mac_memory_mcp.py")

    def _ensure(self):
        if self.process and self.process.poll() is None:
            return
        if not os.path.exists(self.server_script):
            raise ValueError(f"Memory MCP server script missing at {self.server_script}")
        self.process = subprocess.Popen(
            ["python3", "-I", self.server_script],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        self.pending = {}
        self.reader = threading.Thread(target=self._pump, daemon=True)
        self.reader.start()
        self._rpc("initialize", {"protocolVersion": "2024-11-05",
                                   "capabilities": {}, "clientInfo": {"name": "bughunter-backend", "version": "0.1"}})
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.process.stdin.flush()

    def _pump(self):
        while self.process and self.process.poll() is None:
            try:
                line = self.process.stdout.readline()
            except (OSError, ValueError):
                break
            if not line:
                break
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            id = message.get("id")
            if id in self.pending:
                self.pending[id].append(message)

    def _rpc(self, method: str, params: dict | None = None, timeout: float = 20.0) -> dict:
        with self.lock:
            self._ensure()
            self.sequence += 1
            id = self.sequence
            self.pending[id] = []
            request = {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}
            self.process.stdin.write(json.dumps(request) + "\n")
            self.process.stdin.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.pending.get(id):
                reply = self.pending.pop(id)[0]
                if "error" in reply:
                    raise ValueError(reply["error"].get("message", "memory bridge error"))
                return reply.get("result", {})
            time.sleep(0.05)
        self.pending.pop(id, None)
        raise TimeoutError(f"Memory bridge did not reply within {timeout}s to {method}.")

    def call(self, tool: str, arguments: dict) -> dict:
        return self._rpc("tools/call", {"name": tool, "arguments": arguments})

    def list_tools(self) -> list:
        result = self._rpc("tools/list", {})
        return result.get("tools", [])

    def shutdown(self):
        if self.process and self.process.poll() is None:
            try:
                self.process.stdin.close()
            except Exception:
                pass
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None


# ============================================================================
# 9. Python-side desktop control — invokes system-level actions through subprocess
#    (screenshot + AppleScript-driven click/type on macOS)
# ============================================================================

class DesktopController:
    def __init__(self, store: Store):
        self.store = store
        self.workspace = store.root / "desktop_captures"
        self.workspace.mkdir(mode=0o700, exist_ok=True)

    def screenshot(self) -> dict:
        import platform
        path = self.workspace / f"shot-{new_id('shot')}.png"
        if platform.system() == "Darwin":
            # -x silences the shutter sound, -o removes shadow for app captures.
            subprocess.run(["screencapture", "-x", "-o", str(path)], check=True)
        else:
            raise ValueError("Desktop screenshot requires macOS.")
        path.chmod(0o600)
        return {"path": str(path), "bytes": path.stat().st_size}

    def click(self, x: int, y: int) -> dict:
        import platform
        if platform.system() != "Darwin":
            raise ValueError("Desktop click requires macOS.")
        if not isinstance(x, int) or not isinstance(y, int) or not 0 <= x <= 10000 or not 0 <= y <= 10000:
            raise ValueError("x/y must be screen pixel coordinates.")
        script = (f'tell application "System Events" to click at {{{x}, {y}}}')
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if result.returncode:
            raise ValueError(f"Click failed: {result.stderr.strip()}")
        return {"clicked": [x, y]}

    def type_text(self, text: str) -> dict:
        import platform
        if platform.system() != "Darwin":
            raise ValueError("Desktop typing requires macOS.")
        if not isinstance(text, str) or len(text) > 2000:
            raise ValueError("Type text must be a string under 2000 characters.")
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        script = f'tell application "System Events" to keystroke "{escaped}"'
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if result.returncode:
            raise ValueError(f"Keystroke failed: {result.stderr.strip()}")
        return {"typed_chars": len(text)}

    def key(self, keyname: str) -> dict:
        """System-level key press (e.g. 'return', 'tab', 'escape')."""
        import platform
        if platform.system() != "Darwin":
            raise ValueError("Desktop key press requires macOS.")
        mapping = {"return": 36, "tab": 48, "space": 49, "delete": 51,
                   "escape": 53, "left": 123, "right": 124, "down": 125, "up": 126}
        code = mapping.get(keyname.lower())
        if code is None:
            raise ValueError(f"Unsupported key: {keyname}")
        script = f'tell application "System Events" to key code {code}'
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if result.returncode:
            raise ValueError(f"Key press failed: {result.stderr.strip()}")
        return {"pressed": keyname}


# ============================================================================
# Capability service — single dispatch surface
# ============================================================================

class CapabilityService:
    def __init__(self, app):
        self.app = app
        self.store = app.store
        self._memory_bridge = None
        self._desktop = None

    @property
    def memory_bridge(self) -> MacMemoryBridgeClient:
        if self._memory_bridge is None:
            self._memory_bridge = MacMemoryBridgeClient(self.store)
        return self._memory_bridge

    @property
    def desktop(self) -> DesktopController:
        if self._desktop is None:
            self._desktop = DesktopController(self.store)
        return self._desktop

    def toggles(self) -> dict:
        return _toggles(self.store)

    def set_toggles(self, values: dict) -> dict:
        current = _toggles(self.store)
        for key, value in (values or {}).items():
            if key not in DEFAULT_TOGGLES:
                raise ValueError(f"Unknown capability toggle: {key}")
            if key == "provider_fallback_chain":
                if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
                    raise ValueError("provider_fallback_chain must be a list of provider names.")
            elif not isinstance(value, bool):
                raise ValueError(f"Toggle {key} must be a boolean.")
            current[key] = value
        self.store.set_setting(TOGGLES_KEY, current)
        return current

    # -- Nuclei templates --
    def list_templates(self):
        return list_custom_templates(self.store)

    def register_template(self, name: str, body: str):
        if not _toggles(self.store)["nuclei_custom_templates"]:
            raise ValueError("Enable the nuclei_custom_templates toggle first.")
        return register_custom_template(self.store, name, body)

    def delete_template(self, name: str):
        return delete_custom_template(self.store, name)

    # -- Binary / WebSocket replay --
    def queue_binary(self, params: dict):
        if not _toggles(self.store)["binary_replay"]:
            raise ValueError("Enable the binary_replay toggle first.")
        return queue_binary_action(self.app, **params)

    def queue_websocket(self, params: dict):
        if not _toggles(self.store)["websocket_replay"]:
            raise ValueError("Enable the websocket_replay toggle first.")
        return queue_websocket_replay(self.app, **params)

    # -- Encrypted backup --
    def encrypted_backup(self, params: dict):
        if not _toggles(self.store)["encrypted_backup"]:
            raise ValueError("Enable the encrypted_backup toggle first.")
        return create_encrypted_backup(self.store, params["destination"], params["passphrase"],
                                         bool(params.get("include_keychain", True)))

    def decrypt_backup(self, params: dict):
        return decrypt_backup(params["archive"], params["passphrase"])

    # -- Cloud transcription --
    def configure_cloud_transcription(self, params: dict):
        self.store.set_setting("cloud_transcription", {
            "endpoint": params.get("endpoint", ""),
            "model": params.get("model", "whisper-1"),
        })
        if "api_key" in params:
            keyring.set_password("BugHunterClaudex", "cloud_transcription", params["api_key"])
        return {"configured": True}

    def transcribe(self, params: dict):
        if not _toggles(self.store)["cloud_transcription"]:
            raise ValueError("Enable the cloud_transcription toggle first.")
        return cloud_transcribe(self.store, params["audio_path"])

    # -- Bulk downloader --
    def bulk_download(self, params: dict):
        if not _toggles(self.store)["bulk_course_downloader"]:
            raise ValueError("Enable the bulk_course_downloader toggle first.")
        return bulk_course_download(self.store, params["source_url"],
                                      params.get("cookie_file"), int(params.get("max_items", 100)))

    # -- Memory bridge --
    def memory_list(self, params: dict):
        if not _toggles(self.store)["python_memory_bridge"]:
            raise ValueError("Enable the python_memory_bridge toggle first.")
        return self.memory_bridge.list_tools()

    def memory_call(self, params: dict):
        if not _toggles(self.store)["python_memory_bridge"]:
            raise ValueError("Enable the python_memory_bridge toggle first.")
        return self.memory_bridge.call(params["tool"], params.get("arguments", {}))

    # -- Desktop control --
    def desktop_screenshot(self, params: dict):
        if not _toggles(self.store)["python_desktop_control"]:
            raise ValueError("Enable the python_desktop_control toggle first.")
        return self.desktop.screenshot()

    def desktop_click(self, params: dict):
        if not _toggles(self.store)["python_desktop_control"]:
            raise ValueError("Enable the python_desktop_control toggle first.")
        return self.desktop.click(int(params["x"]), int(params["y"]))

    def desktop_type(self, params: dict):
        if not _toggles(self.store)["python_desktop_control"]:
            raise ValueError("Enable the python_desktop_control toggle first.")
        return self.desktop.type_text(params["text"])

    def desktop_key(self, params: dict):
        if not _toggles(self.store)["python_desktop_control"]:
            raise ValueError("Enable the python_desktop_control toggle first.")
        return self.desktop.key(params["key"])

    def dispatch(self, method: str, params: dict):
        verbs = {
            "capabilities.toggles": lambda p: self.toggles(),
            "capabilities.set_toggles": lambda p: self.set_toggles(p),
            "capabilities.nuclei.list": lambda p: self.list_templates(),
            "capabilities.nuclei.register": lambda p: self.register_template(p["name"], p["body"]),
            "capabilities.nuclei.delete": lambda p: self.delete_template(p["name"]),
            "capabilities.binary.queue": lambda p: self.queue_binary(p),
            "capabilities.websocket.queue": lambda p: self.queue_websocket(p),
            "capabilities.backup.encrypted": lambda p: self.encrypted_backup(p),
            "capabilities.backup.decrypt": lambda p: self.decrypt_backup(p),
            "capabilities.transcription.configure": lambda p: self.configure_cloud_transcription(p),
            "capabilities.transcription.run": lambda p: self.transcribe(p),
            "capabilities.bulk.download": lambda p: self.bulk_download(p),
            "capabilities.memory.list": lambda p: self.memory_list(p),
            "capabilities.memory.call": lambda p: self.memory_call(p),
            "capabilities.desktop.screenshot": lambda p: self.desktop_screenshot(p),
            "capabilities.desktop.click": lambda p: self.desktop_click(p),
            "capabilities.desktop.type": lambda p: self.desktop_type(p),
            "capabilities.desktop.key": lambda p: self.desktop_key(p),
        }
        fn = verbs.get(method)
        if not fn:
            raise ValueError(f"Unknown capability operation: {method}")
        return fn(params or {})

    def shutdown(self):
        if self._memory_bridge:
            try: self._memory_bridge.shutdown()
            except Exception: pass
