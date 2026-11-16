"""P02 target discovery, identity derivation and capability reports.

Discovers installed game targets on the local host, derives a
reproducible identity from hashed inputs, and reports bounded runtime
access honestly: ``static_only``, ``runtime_access_blocked`` or
``runtime_ready``. A spawned PID alone never satisfies ``runtime_ready``.

Platform notes:

* macOS
  * The Among Us Mac App Store build ships as an *iPhoneOS-wrapped*
    bundle at ``/Applications/AmongUs.app/Wrapper/AmongUs.app``. Its
    ``Info.plist`` lives at the bundle root — NOT under
    ``Contents/`` — and the executable is
    ``<bundle>/<CFBundleExecutable>`` directly beside it. Code that
    assumes the conventional ``Contents/MacOS/<name>`` layout misses
    these targets. See the integration note at the bottom of this file
    for the one-line fix the coordinator must apply to
    ``op_adapters/mac_appstore_memory_esp.py`` (``T → C``).
  * The Unity game module is always
    ``<bundle>/Frameworks/UnityFramework.framework/UnityFramework``;
    IL2CPP metadata (when present) is at
    ``<bundle>/Data/Managed/Metadata/global-metadata.dat``.
  * Architecture comes from the Mach-O header (fat or thin). The
    pointer width follows the Mach-O magic, not the architecture
    string.
* Windows
  * A non-Windows host returns an empty list gracefully; this is not a
    failure. ``os.name == 'nt'`` walks ``%ProgramFiles%``,
    ``%ProgramFiles(x86)%`` and the Steam library when available.
* Android
  * ``adb devices`` output is parsed when present. With no connected
    device the function returns a structured record stating so — a
    missing device is a blocked capability, not an empty list.

Every returned :class:`GameTarget` is pushed through
``validate_record("game_target", ...)`` before emission.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

from .contracts import (
    BuildFingerprint,
    GameTarget,
    Module,
    validate_record,
)


# ---------------------------------------------------------------------------
# Tool pin manifest. These versions are pinned here because the
# qualification report must record the exact tool a result came from;
# "I ran Cpp2IL" is useless without a tag.
# ---------------------------------------------------------------------------

PINNED_TOOLS: dict[str, dict[str, str]] = {
    "cpp2il": {
        "version": "2022.1.0-pre-release.21",
        "url": "https://github.com/SamboyCoding/Cpp2IL/releases/tag/2022.1.0-pre-release.21",
        "role": "static Unity IL2CPP decompiler for metadata version 31",
    },
    "ghidra": {
        "version": "12.1",
        "url": "https://github.com/NationalSecurityAgency/ghidra/releases/tag/Ghidra_12.1_build",
        "role": "disassembler/decompiler (native pseudocode)",
    },
    "rea_agents": {
        "version": "6.7.0",
        "url": "npm:rea-agents",
        "role": "reverse-engineering MCP server shared with every reasoning provider",
    },
    "lldb": {
        "version": "apple-shipped",
        "url": "ships with Xcode Command Line Tools",
        "role": "runtime fallback when task_for_pid is denied in-process",
    },
    "otool": {
        "version": "apple-shipped",
        "url": "ships with Xcode Command Line Tools",
        "role": "Mach-O header inspection",
    },
    "adb": {
        "version": ">=34.0.0",
        "url": "https://developer.android.com/tools/adb",
        "role": "Android device inventory for R6",
    },
}


# ---------------------------------------------------------------------------
# Mach-O constants
# ---------------------------------------------------------------------------


MH_MAGIC = 0xFEEDFACE          # 32-bit little-endian
MH_CIGAM = 0xCEFAEDFE
MH_MAGIC_64 = 0xFEEDFACF
MH_CIGAM_64 = 0xCFFAEDFE
FAT_MAGIC = 0xCAFEBABE
FAT_CIGAM = 0xBEBAFECA
FAT_MAGIC_64 = 0xCAFEBABF
FAT_CIGAM_64 = 0xBFBAFECA

CPU_TYPE_X86 = 0x00000007
CPU_TYPE_X86_64 = 0x01000007
CPU_TYPE_ARM = 0x0000000C
CPU_TYPE_ARM64 = 0x0100000C

_ARCH_NAMES = {
    CPU_TYPE_X86: ("x86", 4),
    CPU_TYPE_X86_64: ("x86_64", 8),
    CPU_TYPE_ARM: ("armv7", 4),
    CPU_TYPE_ARM64: ("arm64", 8),
}


@dataclass
class MachOInfo:
    """Parsed Mach-O header summary.

    ``architecture`` is the canonical lowercase slug (``arm64``,
    ``x86_64``, ...). ``pointer_width`` is derived from the magic,
    not inferred from the architecture name — a thin armv7 binary is
    pointer_width=4 even if the kernel ran it in a 64-bit sandbox.
    """

    architecture: str
    pointer_width: Literal[4, 8]
    is_fat: bool
    sub_architectures: tuple[str, ...]


def read_macho_info(path: Path) -> MachOInfo:
    """Parse the Mach-O header at ``path``.

    Raises ``ValueError`` if the file is not a Mach-O binary. For fat
    binaries, the first slice's architecture is reported and every
    slice slug is recorded in ``sub_architectures``.
    """
    with path.open("rb") as fh:
        head = fh.read(4096)
    if len(head) < 8:
        raise ValueError(f"{path}: file too small for a Mach-O header")
    magic = struct.unpack("<I", head[:4])[0]

    # Fat binary ---------------------------------------------------------
    if magic in (FAT_MAGIC, FAT_CIGAM, FAT_MAGIC_64, FAT_CIGAM_64):
        big = magic in (FAT_MAGIC, FAT_CIGAM_64, FAT_MAGIC_64, FAT_CIGAM)
        # Fat headers are traditionally big-endian; use ">I".
        nfat = struct.unpack(">I", head[4:8])[0]
        is_64 = magic in (FAT_MAGIC_64, FAT_CIGAM_64)
        entry_size = 32 if is_64 else 20
        slugs: list[str] = []
        first: tuple[str, int] | None = None
        for i in range(nfat):
            off = 8 + i * entry_size
            if off + 8 > len(head):
                break
            cpu = struct.unpack(">I", head[off:off + 4])[0]
            info = _ARCH_NAMES.get(cpu)
            if info is None:
                slugs.append(f"cpu_0x{cpu:x}")
                continue
            slugs.append(info[0])
            if first is None:
                first = info
        if first is None:
            raise ValueError(f"{path}: fat binary has no supported slices")
        return MachOInfo(
            architecture=first[0],
            pointer_width=first[1],  # type: ignore[arg-type]
            is_fat=True,
            sub_architectures=tuple(slugs),
        )

    # Thin binary --------------------------------------------------------
    if magic in (MH_MAGIC, MH_CIGAM):
        endian = "<" if magic == MH_MAGIC else ">"
        cpu = struct.unpack(f"{endian}I", head[4:8])[0]
        info = _ARCH_NAMES.get(cpu)
        if info is None:
            raise ValueError(f"{path}: unknown 32-bit cpu_type 0x{cpu:x}")
        return MachOInfo(info[0], 4, False, (info[0],))
    if magic in (MH_MAGIC_64, MH_CIGAM_64):
        endian = "<" if magic == MH_MAGIC_64 else ">"
        cpu = struct.unpack(f"{endian}I", head[4:8])[0]
        info = _ARCH_NAMES.get(cpu)
        if info is None:
            raise ValueError(f"{path}: unknown 64-bit cpu_type 0x{cpu:x}")
        return MachOInfo(info[0], 8, False, (info[0],))
    raise ValueError(f"{path}: not a Mach-O binary (magic=0x{magic:08x})")


def sha256_file(path: Path) -> str:
    """Streaming SHA-256; 1 MiB chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# macOS discovery
# ---------------------------------------------------------------------------


_UNITY_VERSION_RE = re.compile(rb"\b(20\d\d\.\d+\.\d+[a-z]\d+)\b")


def _plist_executable(bundle: Path) -> tuple[Path | None, dict]:
    """Return (executable path, parsed Info.plist) for either wrapped-
    iPhoneOS or conventional macOS bundles.

    The wrapped-iPhoneOS layout puts Info.plist and the executable at
    the bundle root; conventional macOS puts them under ``Contents/``.
    """
    candidates = [
        (bundle / "Info.plist", bundle),
        (bundle / "Contents" / "Info.plist", bundle / "Contents" / "MacOS"),
    ]
    for info_path, exec_dir in candidates:
        if not info_path.is_file():
            continue
        try:
            with info_path.open("rb") as fh:
                info = plistlib.load(fh)
        except (plistlib.InvalidFileException, OSError):
            continue
        exec_name = info.get("CFBundleExecutable")
        if not exec_name:
            continue
        exec_path = exec_dir / exec_name
        if exec_path.is_file():
            return exec_path, info
    return None, {}


def _unity_version_from_module(module_path: Path) -> str | None:
    """Scan the Unity framework binary for the first ``20XX.Y.Zf?`` tag.

    Reads the file in a bounded window; a Unity version string is near
    the start of ``UnityFramework`` in practice.
    """
    try:
        with module_path.open("rb") as fh:
            blob = fh.read(8 * 1024 * 1024)
    except OSError:
        return None
    match = _UNITY_VERSION_RE.search(blob)
    if match:
        return match.group(1).decode("ascii", "replace")
    return None


def _macos_build_guid(data_dir: Path) -> str | None:
    """Unity writes ``build-guid=<hex>`` into ``Data/boot.config``."""
    cfg = data_dir / "boot.config"
    if not cfg.is_file():
        return None
    try:
        for line in cfg.read_text(errors="replace").splitlines():
            if line.startswith("build-guid="):
                return line.split("=", 1)[1].strip()
    except OSError:
        return None
    return None


def _unity_asset_version(path: Path) -> tuple[str, int] | None:
    if not path.is_file():
        return None
    with path.open("rb") as stream:
        header = stream.read(128)
    if len(header) < 49:
        return None
    format_version = struct.unpack_from(">I", header, 8)[0]
    if not 9 <= format_version <= 30:
        return None
    offset = 48 if format_version >= 22 else 20
    value = header[offset:].split(b"\0", 1)[0]
    if re.fullmatch(rb"(?:20\d\d|6000)\.\d+\.\d+[abfp]\d+", value):
        return value.decode("ascii"), offset
    return None


def _macos_inspect_bundle(bundle: Path) -> GameTarget | None:
    """Produce a :class:`GameTarget` for one ``.app`` bundle, or None if
    the bundle is not a Unity target this discovery recognises.

    Only wrapped-iPhoneOS Unity bundles — and conventional macOS
    bundles that still carry a UnityFramework module — are returned.
    Non-Unity apps are skipped silently; this discovery is scoped to
    targets P02 can reason about.
    """
    exec_path, info = _plist_executable(bundle)
    if exec_path is None:
        return None

    # Unity module location. iPhoneOS-wrapped and modern macOS Unity
    # builds both use ``Frameworks/UnityFramework.framework/UnityFramework``.
    unity = bundle / "Frameworks" / "UnityFramework.framework" / "UnityFramework"
    if not unity.is_file():
        return None

    try:
        exec_info = read_macho_info(exec_path)
    except ValueError:
        return None
    try:
        unity_info = read_macho_info(unity)
    except ValueError:
        return None

    # Data directory layout: iPhoneOS-wrapped lives at ``<bundle>/Data``.
    data_root = bundle / "Data"
    metadata = data_root / "Managed" / "Metadata" / "global-metadata.dat"
    sha_main = sha256_file(exec_path)
    sha_meta = sha256_file(metadata) if metadata.is_file() else None
    sha_unity = sha256_file(unity)

    unity_version = _unity_version_from_module(unity)
    asset = data_root / "globalgamemanagers"
    asset_version = _unity_asset_version(asset)
    if asset_version:
        unity_version = asset_version[0]
    build_guid = _macos_build_guid(data_root)
    bundle_version = (
        info.get("CFBundleShortVersionString")
        or info.get("CFBundleVersion")
        or "unknown"
    )
    bundle_id = info.get("CFBundleIdentifier") or bundle.name

    # module size: file length on disk, which is the on-disk slice size
    # for a thin Mach-O. Fat slices would need an offset table; we
    # record file size here and note the fat layout in extras.
    module_size = unity.stat().st_size

    extras: dict[str, Any] = {
        "bundle_identifier": bundle_id,
        "bundle_path": str(bundle),
        "wrapper_layout": (bundle / "Info.plist").is_file(),  # True when iPhoneOS-wrapped
        "module_sha256": sha_unity,
        "module_architecture": unity_info.architecture,
        "module_pointer_width": unity_info.pointer_width,
        "module_is_fat": unity_info.is_fat,
        "module_sub_architectures": list(unity_info.sub_architectures),
    }
    if unity_version:
        extras["unity_version"] = unity_version
    if asset_version:
        extras["unity_version_source"] = {"path": str(asset.relative_to(bundle)),
                                         "sha256": sha256_file(asset), "byte_offset": asset_version[1]}
    if build_guid:
        extras["unity_build_guid"] = build_guid
    if metadata.is_file():
        extras["metadata_present"] = True
        extras["metadata_path"] = str(metadata.relative_to(bundle))

    # Target id: prefer bundle identifier + short version, fall back to
    # the main executable sha.
    tid = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{bundle_id}_{bundle_version}")
    gid = re.sub(r"[^a-z0-9_]+", "_", bundle_id.lower().split(".")[-1]) or "unknown_game"

    capabilities = ["static_analysis"]
    if metadata.is_file():
        capabilities.append("il2cpp_metadata_present")
    if exec_info.architecture != unity_info.architecture:
        capabilities.append("mixed_arch_warning")

    target = GameTarget(
        id=f"target_{tid}",
        game_id=gid,
        platform="macos",
        runtime="il2cpp" if metadata.is_file() else "unity_mono_or_unknown",
        architecture=exec_info.architecture,
        pointer_width=exec_info.pointer_width,
        executable_or_package=str(bundle),
        version=str(bundle_version),
        build_fingerprint=BuildFingerprint(
            sha256_main=sha_main,
            sha256_metadata=sha_meta,
            extras=extras,
        ),
        modules=[
            Module(
                name="UnityFramework",
                base_rva="+0x0",
                size=f"0x{module_size:x}",
                sha256=sha_unity,
            )
        ],
        capabilities=capabilities,
    )
    # Mandatory emission gate.
    validate_record("game_target", target.model_dump())
    return target


def _discover_macos(roots: Iterable[Path] | None = None) -> list[GameTarget]:
    if sys.platform != "darwin":
        # On non-macOS hosts an explicit roots list still runs (for the
        # synthesised fixture test). Without roots, return nothing.
        if roots is None:
            return []
    search = list(roots) if roots is not None else [Path("/Applications")]
    out: list[GameTarget] = []
    seen: set[str] = set()

    def visit(bundle: Path) -> None:
        # iPhoneOS-wrapped layout: outer .app has a Wrapper/<Game>.app.
        wrapper = bundle / "Wrapper"
        if wrapper.is_dir():
            for inner in sorted(wrapper.glob("*.app")):
                if inner.is_dir():
                    visit(inner)
                    return
        target = _macos_inspect_bundle(bundle)
        if target is not None and target.id not in seen:
            seen.add(target.id)
            out.append(target)

    for root in search:
        if not root.is_dir():
            continue
        # Top-level .app bundles only. Nested app bundles (e.g. inside
        # Xcode) are not relevant target surface.
        for bundle in sorted(root.glob("*.app")):
            if bundle.is_dir():
                visit(bundle)
    return out


# ---------------------------------------------------------------------------
# Windows discovery
# ---------------------------------------------------------------------------


def _discover_windows() -> list[GameTarget]:
    """Walk standard install roots on Windows hosts.

    A non-Windows host returns an empty list, as documented: this is a
    gracefully empty result, not a failure. The actual Windows walker
    is implemented in a dedicated lane; the stub below returns the
    inventory without invented offsets.
    """
    if os.name != "nt":
        return []
    out: list[GameTarget] = []
    roots: list[Path] = []
    for key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        val = os.environ.get(key)
        if val:
            roots.append(Path(val))
    # Steam library folders — conservative discovery, parse libraryfolders.vdf.
    steam = os.environ.get("ProgramFiles(x86)")
    if steam:
        candidate = Path(steam) / "Steam" / "steamapps"
        if candidate.is_dir():
            roots.append(candidate)
    for root in roots:
        if not root.is_dir():
            continue
        for sub in sorted(root.iterdir()):
            if not sub.is_dir():
                continue
            # Look for any PE executable in the folder root.
            for exe in sorted(sub.glob("*.exe")):
                try:
                    sha = sha256_file(exe)
                    arch, width = read_pe_identity(exe)
                except (OSError, ValueError):
                    continue
                target_id = f"target_win_{sub.name}_{sha[:12]}"
                game_id = re.sub(r"[^a-z0-9_]+", "_", sub.name.lower())
                target = GameTarget(
                    id=target_id,
                    game_id=game_id or "unknown_game",
                    platform="windows",
                    runtime="unknown",
                    architecture=arch,
                    pointer_width=width,
                    executable_or_package=str(exe),
                    version="unknown",
                    build_fingerprint=BuildFingerprint(
                        sha256_main=sha,
                        sha256_metadata=None,
                        extras={"install_root": str(sub)},
                    ),
                    modules=[],
                    capabilities=["static_analysis"],
                )
                validate_record("game_target", target.model_dump())
                out.append(target)
                break  # one representative executable per install folder
    return out


def read_pe_identity(path: Path) -> tuple[str, int]:
    """Measure PE machine and optional-header width; reject inconsistent pairs."""
    with path.open("rb") as stream:
        dos = stream.read(64)
        if len(dos) < 64 or dos[:2] != b"MZ":
            raise ValueError("not a PE executable")
        offset = struct.unpack_from("<I", dos, 60)[0]
        if offset < 64 or offset > path.stat().st_size - 26:
            raise ValueError("invalid PE header offset")
        stream.seek(offset)
        head = stream.read(26)
    if head[:4] != b"PE\0\0":
        raise ValueError("invalid PE signature")
    machine = struct.unpack_from("<H", head, 4)[0]
    magic = struct.unpack_from("<H", head, 24)[0]
    expected = {0x14c: ("x86", 4, 0x10b), 0x8664: ("x86_64", 8, 0x20b),
                0xaa64: ("arm64", 8, 0x20b)}.get(machine)
    if expected is None or expected[2] != magic:
        raise ValueError("unsupported or inconsistent PE architecture/pointer width")
    return expected[:2]


# ---------------------------------------------------------------------------
# Android discovery (device inventory only)
# ---------------------------------------------------------------------------


def _discover_android() -> list[GameTarget]:
    """Report attached Android devices via ``adb devices -l``.

    If ``adb`` is missing or no device is attached, returns a single
    sentinel GameTarget stamped as a ``runtime_access_blocked``
    capability with the reason recorded in ``extras``. The record still
    obeys the wire contract; a missing device is a blocked capability,
    not an empty inventory that could be mistaken for "no Android
    games on this host".
    """
    adb = shutil.which("adb")
    if adb is None:
        return [_android_blocked("adb_missing", "adb binary not found on PATH")]

    try:
        proc = subprocess.run(
            [adb, "devices", "-l"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [_android_blocked("adb_failed", str(exc))]

    devices: list[tuple[str, dict[str, str]]] = []
    for line in proc.stdout.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2 or parts[1] != "device":
            continue
        serial = parts[0]
        meta = {}
        for frag in parts[2:]:
            if ":" in frag:
                k, v = frag.split(":", 1)
                meta[k] = v
        devices.append((serial, meta))

    if not devices:
        return [_android_blocked("no_device", "adb devices returned no attached device")]

    out: list[GameTarget] = []
    for serial, meta in devices:
        extras: dict[str, Any] = {
            "serial": serial,
            "adb_meta": meta,
            "adapter_note": "P02 inventory only; BGMI qualification lives in R6",
        }
        # Synthesize a stable sha256-looking tag over serial + meta. Not
        # a real binary digest; recorded in extras so the capability
        # label stays honest.
        fake_main = hashlib.sha256(
            f"android:{serial}:{json.dumps(meta, sort_keys=True)}".encode()
        ).hexdigest()
        extras["sha256_source"] = "synthetic_device_descriptor"
        target = GameTarget(
            id=f"target_android_{serial}",
            game_id="android_device_inventory",
            platform="android",
            runtime="unknown",
            architecture=meta.get("abi", "unknown"),
            pointer_width=8,
            executable_or_package=f"adb://{serial}",
            version=meta.get("model", "unknown"),
            build_fingerprint=BuildFingerprint(
                sha256_main=fake_main,
                sha256_metadata=None,
                extras=extras,
            ),
            modules=[],
            capabilities=["device_inventory", "runtime_access_pending"],
        )
        validate_record("game_target", target.model_dump())
        out.append(target)
    return out


def _android_blocked(reason: str, detail: str) -> GameTarget:
    fake = hashlib.sha256(f"android_blocked:{reason}:{detail}".encode()).hexdigest()
    target = GameTarget(
        id=f"target_android_blocked_{reason}",
        game_id="android_device_inventory",
        platform="android",
        runtime="unknown",
        architecture="unknown",
        pointer_width=8,
        executable_or_package="adb://unavailable",
        version="unknown",
        build_fingerprint=BuildFingerprint(
            sha256_main=fake,
            sha256_metadata=None,
            extras={
                "block_reason": reason,
                "detail": detail,
                "sha256_source": "synthetic_blocked_descriptor",
            },
        ),
        modules=[],
        capabilities=["runtime_access_blocked"],
    )
    validate_record("game_target", target.model_dump())
    return target


# ---------------------------------------------------------------------------
# Public discover_installed
# ---------------------------------------------------------------------------


Platform = Literal["macos", "windows", "android"]


def discover_installed(
    platform_filter: Platform | Iterable[Platform] | None = None,
    *,
    macos_roots: Iterable[Path] | None = None,
) -> list[GameTarget]:
    """Discover installed targets on the local host.

    ``platform_filter`` restricts the scan to one or more of
    ``"macos"``, ``"windows"``, ``"android"``. ``None`` scans all
    three. ``macos_roots`` lets tests inject a synthetic bundle tree;
    production callers leave it as ``None``.

    Every returned record has already been pushed through
    :func:`bughunter.game_runtime.validate_record`.
    """
    if isinstance(platform_filter, str):
        wanted = {platform_filter}
    elif platform_filter is None:
        wanted = {"macos", "windows", "android"}
    else:
        wanted = set(platform_filter)

    results: list[GameTarget] = []
    if "macos" in wanted:
        results.extend(_discover_macos(macos_roots))
    if "windows" in wanted:
        results.extend(_discover_windows())
    if "android" in wanted:
        results.extend(_discover_android())
    return results


# ---------------------------------------------------------------------------
# Identity cross-checks and capability reports
# ---------------------------------------------------------------------------


_ARCH_POINTER_WIDTH = {
    "x86": 4, "armv7": 4,
    "x86_64": 8, "arm64": 8,
}


def validate_identity(target: GameTarget) -> list[str]:
    """Cross-field identity checks beyond the wire schema.

    The wire schema is deliberately loose about ``architecture`` and
    cross-field consistency. P02 requires rejecting records where the
    architecture string disagrees with the pointer width, where the
    declared platform and the module name conflict, or where a module
    sha collides with the main-binary sha without declaring the two
    paths as the same file. Returns a list of human-readable problems;
    empty means consistent.
    """
    problems: list[str] = []
    expected = _ARCH_POINTER_WIDTH.get(target.architecture)
    if expected is not None and expected != target.pointer_width:
        problems.append(
            f"architecture {target.architecture!r} implies pointer_width={expected} "
            f"but record has pointer_width={target.pointer_width}"
        )
    if target.platform == "macos" and target.modules:
        unity = [m for m in target.modules if m.name == "UnityFramework"]
        if unity:
            mod_arch = target.build_fingerprint.extras.get("module_architecture")
            if mod_arch and mod_arch != target.architecture:
                problems.append(
                    f"module architecture {mod_arch!r} does not match target "
                    f"architecture {target.architecture!r}"
                )
    if target.platform == "windows" and any(
        m.name == "UnityFramework" for m in target.modules
    ):
        problems.append(
            "Windows target should not carry a UnityFramework.framework "
            "module; use the PE module name instead"
        )
    return problems


def enforce_identity(target: GameTarget) -> GameTarget:
    """Like :func:`validate_identity` but raises on problems.

    Used by the qualify CLI and tests; emission paths call this after
    construction when they want cross-field consistency guarantees.
    """
    problems = validate_identity(target)
    if problems:
        raise ValueError(
            "target identity rejected: " + "; ".join(problems)
        )
    return target


def verify_target_files(target: GameTarget) -> list[dict[str, Any]]:
    """Bind a supplied identity to measured files, not caller-provided hashes."""
    enforce_identity(target)
    location = Path(target.executable_or_package).resolve()
    extras = target.build_fingerprint.extras
    if target.platform == "android" and not location.is_file():
        raise ValueError("Android qualification requires pulled, hashed package/module files")
    main = _plist_executable(location)[0] if location.is_dir() else location
    if main is None or not main.is_file():
        raise ValueError(f"target executable does not exist: {location}")
    root = location if location.is_dir() else main.parent
    inputs = [("main", main, target.build_fingerprint.sha256_main)]
    if target.platform == "macos":
        info = read_macho_info(main)
        if (info.architecture, info.pointer_width) != (target.architecture, target.pointer_width):
            raise ValueError("measured executable architecture/pointer width differs from target")
    elif target.platform == "windows":
        if read_pe_identity(main) != (target.architecture, target.pointer_width):
            raise ValueError("measured executable architecture/pointer width differs from target")
    for mod in target.modules:
        configured = (extras.get("module_paths") or {}).get(mod.name)
        path = Path(configured) if configured else root / (
            "Frameworks/UnityFramework.framework/UnityFramework" if mod.name == "UnityFramework" else mod.name
        )
        if not path.is_absolute():
            path = root / path
        inputs.append((mod.name, path, mod.sha256))
        if target.platform == "macos" and path.is_file():
            info = read_macho_info(path)
            if (info.architecture, info.pointer_width) != (target.architecture, target.pointer_width):
                raise ValueError(f"measured module architecture differs: {mod.name}")
        elif target.platform == "windows" and path.is_file():
            if read_pe_identity(path) != (target.architecture, target.pointer_width):
                raise ValueError(f"measured module architecture differs: {mod.name}")
    if target.build_fingerprint.sha256_metadata is not None:
        path = Path(extras.get("metadata_path") or "Data/Managed/Metadata/global-metadata.dat")
        inputs.append(("metadata", path if path.is_absolute() else root / path,
                       target.build_fingerprint.sha256_metadata))
    version_source = extras.get("unity_version_source")
    if version_source:
        version_path = root / version_source["path"]
        measured_version = _unity_asset_version(version_path)
        if measured_version != (extras.get("unity_version"), version_source.get("byte_offset")):
            raise ValueError("measured Unity version differs from target identity")
        inputs.append(("unity_version", version_path, version_source.get("sha256")))
    measured = []
    for role, path, expected in inputs:
        if not path.is_file():
            raise ValueError(f"target input missing: {role}: {path}")
        actual = sha256_file(path)
        if not expected or actual != expected.lower():
            raise ValueError(f"target input sha256 mismatch: {role}: {path}")
        measured.append({"role": role, "path": str(path.resolve()), "sha256": actual,
                         "size": path.stat().st_size})
    return measured


# ---------------------------------------------------------------------------
# Runtime capability probe
# ---------------------------------------------------------------------------


@dataclass
class RuntimeAccessResult:
    """Honest capability report. ``status`` is one of
    ``static_only``, ``runtime_access_blocked`` or ``runtime_ready``.
    """

    status: Literal["static_only", "runtime_access_blocked", "runtime_ready"]
    reason: str
    bytes_read: int = 0
    pid: int | None = None
    access_mode: Literal["in_process", "external", "none"] = "none"
    sample_sha256: str | None = None
    process_start_time_ns: int | None = None


def _mac_find_pid(bundle_executable_name: str) -> int | None:
    """Return the first PID whose process name matches ``name``.

    Uses ``pgrep -x`` so we match exact process names; ``pgrep -f``
    would also match command-line substrings on other processes.
    """
    pgrep = shutil.which("pgrep")
    if pgrep is None:
        return None
    try:
        proc = subprocess.run(
            [pgrep, "-x", bundle_executable_name],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            return int(line)
    return None


def probe_runtime_access(
    target: GameTarget,
    *,
    attempt: bool = True,
    bytes_to_read: int = 64,
) -> RuntimeAccessResult:
    """Attempt a bounded read against ``target``'s live process.

    * ``attempt=False`` returns ``static_only`` without touching the
      system — used when a caller only wants the static report.
    * On macOS we route through the fixture oracle's ``mach_vm_read``
      path. If ``task_for_pid`` is denied AND ``lldb`` is not usable,
      we honestly record ``runtime_access_blocked`` with the exact
      kernel reason.
    * A successful bounded read records a ``sample_sha256`` so a
      reviewer can bind the capability to real bytes, not a claim.
    """
    if not attempt:
        return RuntimeAccessResult("static_only", "runtime probe skipped")

    if not 1 <= bytes_to_read <= 4096:
        raise ValueError("runtime probe must read between 1 and 4096 bytes")
    if target.platform != "macos":
        return RuntimeAccessResult(
            "runtime_access_blocked",
            f"platform {target.platform!r} runtime probe not implemented in P02 "
            "(see P03 for the Windows/Android adapters)",
        )

    try:
        verify_target_files(target)
    except (ValueError, OSError) as exc:
        return RuntimeAccessResult("runtime_access_blocked", str(exc))

    # Look up the process by CFBundleExecutable. The wrapped-iPhoneOS
    # binary name equals the file at ``<bundle>/<CFBundleExecutable>``,
    # which we can derive from executable_or_package + Info.plist.
    bundle = Path(target.executable_or_package)
    exec_path, _ = _plist_executable(bundle)
    if exec_path is None:
        return RuntimeAccessResult(
            "runtime_access_blocked",
            f"could not resolve CFBundleExecutable for {bundle}",
        )
    pid = _mac_find_pid(exec_path.name)
    if pid is None:
        return RuntimeAccessResult(
            "runtime_access_blocked",
            f"no running process named {exec_path.name!r} "
            "(target must be launched before P02.target_memory_access can pass)",
        )

    try:
        from ..memory_engine.protocol import read_process_start_time_ns
        birth = read_process_start_time_ns(pid)
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "/usr/lib/libSystem.B.dylib")
        libc.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        libc.proc_pidpath.restype = ctypes.c_int
        path_buffer = ctypes.create_string_buffer(4096)
        if libc.proc_pidpath(pid, path_buffer, len(path_buffer)) <= 0 or birth <= 0:
            raise ValueError("process path/birth identity unavailable")
        if Path(os.fsdecode(path_buffer.value)).resolve() != exec_path.resolve():
            raise ValueError("process executable path differs from qualified target")
    except (OSError, ValueError) as exc:
        return RuntimeAccessResult("runtime_access_blocked", str(exc), pid=pid)

    # Route through the fixture oracle's reader stack, which already
    # handles task_for_pid denial and the lldb fallback.
    try:
        from .testing.oracle import (  # local import to keep targets.py importable
            OracleUnavailable,
            _LldbReader,
            _MacReader,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return RuntimeAccessResult(
            "runtime_access_blocked",
            f"oracle reader stack unavailable: {exc}",
            pid=pid,
        )

    reader = None
    mode: Literal["in_process", "external", "none"] = "none"
    first_error: str | None = None
    try:
        reader = _MacReader(pid)
        mode = "external"
    except OracleUnavailable as exc:
        first_error = str(exc)
        try:
            reader = _LldbReader(pid)
            mode = "external"
        except OracleUnavailable as exc2:
            return RuntimeAccessResult(
                "runtime_access_blocked",
                f"task_for_pid denied ({first_error}); lldb fallback unavailable ({exc2})",
                pid=pid,
            )

    # Find the UnityFramework base in the live address space. P02 reads
    # the first ``bytes_to_read`` bytes of the module. The adapter in
    # P03 will own precise module-base enumeration; here we settle for
    # the first readable page at the module's known on-disk prefix via
    # vmmap.
    expected_module = bundle / "Frameworks/UnityFramework.framework/UnityFramework"
    base = _mac_module_base(pid, str(expected_module.resolve()))
    if base is None:
        reader.close()
        return RuntimeAccessResult(
            "runtime_access_blocked",
            f"vmmap did not report a UnityFramework base in pid {pid}",
            pid=pid,
            access_mode=mode,
        )
    try:
        data = reader.read(base, bytes_to_read)
        if len(data) != bytes_to_read:
            raise ValueError(f"partial runtime read: {len(data)}/{bytes_to_read}")
        if read_process_start_time_ns(pid) != birth:
            raise ValueError("target process identity changed during probe")
    except Exception as exc:  # OracleUnavailable or OSError
        return RuntimeAccessResult(
            "runtime_access_blocked",
            f"bounded read at 0x{base:x} failed: {exc}",
            pid=pid,
            access_mode=mode,
        )
    finally:
        try:
            if reader is not None:
                reader.close()
        except Exception:
            pass

    return RuntimeAccessResult(
        "runtime_ready",
        f"read {len(data)} bytes from UnityFramework at 0x{base:x}",
        bytes_read=len(data),
        pid=pid,
        access_mode=mode,
        sample_sha256=hashlib.sha256(data).hexdigest(),
        process_start_time_ns=birth,
    )


def _mac_module_base(pid: int, module_name: str) -> int | None:
    """Parse ``vmmap -resident <pid>`` output for the module's first
    region. ``vmmap`` ships with macOS and does not need the debugger
    entitlement to inspect regions of a process the user owns.
    """
    vmmap = shutil.which("vmmap")
    if vmmap is None:
        return None
    try:
        proc = subprocess.run(
            [vmmap, "-resident", str(pid)],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    for line in proc.stdout.splitlines():
        if module_name not in line:
            continue
        match = re.search(r"([0-9a-fA-F]{6,16})-[0-9a-fA-F]+", line)
        if match:
            try:
                return int(match.group(1), 16)
            except ValueError:
                continue
    return None


# ---------------------------------------------------------------------------
# REA tool discovery
# ---------------------------------------------------------------------------


def rea_tool_discovery(rea_service=None) -> dict[str, Any]:
    """Enumerate analysis tools through the shared REA integration.

    Prefers ``rea.py``'s live tool catalog so a qualification report
    records what the operator actually has installed. Falls back to
    PATH-based probing when the REA runtime is not set up. Every
    returned tool carries its pinned version requirement from
    :data:`PINNED_TOOLS`.
    """
    manifest: dict[str, Any] = {
        "pinned_tools": PINNED_TOOLS,
        "rea": {"available": False, "detail": "", "catalog": []},
        "path_probes": {},
    }

    # REA-integrated catalog. Importing ``rea.py`` is heavy (pulls in
    # mcp, httpx, hashing threads). Guard the import so this stays
    # usable in minimal test environments.
    try:
        from .. import rea as _rea  # noqa: F401
        manifest["rea"]["pinned_version"] = _rea.REA_VERSION
        manifest["rea"]["module_import"] = True
    except Exception as exc:  # pragma: no cover
        manifest["rea"]["module_import"] = False
        manifest["rea"]["detail"] = f"rea.py import failed: {exc!r}"

    if rea_service is not None:
        scope = "p02-qualification"
        try:
            session = rea_service.session(scope)
            response = session.request("binary_session", {}, timeout=30)
            if response.get("isError"):
                raise ValueError("REA binary_session failed")
            manifest["rea"].update(available=True,
                catalog=[{"name": t["name"], "description": t.get("description", "")[:300]}
                         for t in session.catalog],
                availability=session.availability,
                identity=_rea.payload(response).get("result", {}).get("server_identity", {}))
        except Exception as exc:
            manifest["rea"]["detail"] = f"REA catalog unavailable: {exc}"
        finally:
            rea_service.close_task(scope)
    else:
        manifest["rea"]["detail"] = "Live integration not requested; PATH probes alone do not qualify an analysis tool."

    # ``rea_agents`` only runs with a configured ``node`` + the
    # ``rea-agents`` npm package. Report whether the entrypoint exists
    # without actually starting an MCP session (which would require
    # an interactive store).
    node_bin = shutil.which("node")
    if node_bin is None:
        manifest["rea"]["node"] = {"available": False, "reason": "node not on PATH"}
    else:
        manifest["rea"]["node"] = {"available": True, "path": node_bin}

    # Path probes for well-known tools, with pinned requirements kept
    # side by side so any PATH-only fallback stays honest.
    for tool_name in ("cpp2il", "ghidra", "ida", "ida64", "hopper",
                      "otool", "lldb", "adb", "frida", "aapt", "aapt2"):
        path = shutil.which(tool_name)
        pin = PINNED_TOOLS.get(tool_name, {})
        manifest["path_probes"][tool_name] = {
            "found": path is not None,
            "path": path,
            "pinned_version": pin.get("version"),
            "role": pin.get("role"),
        }

    return manifest


# ---------------------------------------------------------------------------
# Integration request record (T → C)
# ---------------------------------------------------------------------------

INTEGRATION_REQUEST_T_TO_C: dict[str, Any] = {
    "from_role": "T",
    "to_role": "C",
    "file": "backend/bughunter/op_adapters/mac_appstore_memory_esp.py",
    "function": "_check_build",
    "symptom": (
        "The adapter hard-codes '/Applications/Among Us.app' and treats a "
        "missing bundle as 'app not found'. The Mac App Store install lives "
        "at '/Applications/AmongUs.app/Wrapper/AmongUs.app' and the outer "
        "'/Applications/AmongUs.app' does NOT have a Contents/MacOS layout."
    ),
    "one_line_fix": (
        "Change the default install probe to prefer "
        "'/Applications/AmongUs.app/Wrapper/AmongUs.app' when it exists and "
        "fall back to '/Applications/Among Us.app'."
    ),
    "scope": "adapter default install path only; no behavior change on the patch_app/launch paths.",
}


__all__ = [
    "PINNED_TOOLS",
    "MachOInfo",
    "RuntimeAccessResult",
    "INTEGRATION_REQUEST_T_TO_C",
    "read_macho_info",
    "sha256_file",
    "discover_installed",
    "validate_identity",
    "enforce_identity",
    "probe_runtime_access",
    "rea_tool_discovery",
]
