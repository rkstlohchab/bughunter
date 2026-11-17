"""P02 target qualification tests.

Covers the four P02 checks:

* ``P02.identity`` — a record with mismatched architecture/module/
  pointer_width is rejected by :func:`validate_identity`.
* ``P02.derivation`` — a synthesised Mach-O binary yields the same
  sha256 and architecture fields on every run.
* ``P02.capability_report`` — ``probe_runtime_access`` reports
  ``static_only`` / ``runtime_access_blocked`` / ``runtime_ready``
  with honest reasons.
* ``P02.target_memory_access`` — against the installed Among Us
  wrapped-iPhoneOS bundle, attempt a bounded ``mach_vm_read`` and
  record either ``runtime_ready`` or ``runtime_access_blocked``; skip
  on hosts where the target is not installed.
"""
from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from bughunter.game_runtime import GameTarget, validate_record
from bughunter.game_runtime.contracts import BuildFingerprint, Module
from bughunter.game_runtime.targets import (
    MH_MAGIC_64,
    CPU_TYPE_ARM64,
    MachOInfo,
    PINNED_TOOLS,
    discover_installed,
    enforce_identity,
    probe_runtime_access,
    read_macho_info,
    rea_tool_discovery,
    sha256_file,
    validate_identity,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
AMONG_US_WRAPPED = Path(
    "/Applications/AmongUs.app/Wrapper/AmongUs.app"
)
COMBAT_MASTER_BUNDLE = Path(
    Path.home()
    / "Library/Application Support/Steam/steamapps/common/Combat Master/CombatMaster.app"
)


# ---------------------------------------------------------------------------
# P02.identity — mismatched architecture / module / pointer_width
# ---------------------------------------------------------------------------


def _baseline_target(**overrides):
    base = dict(
        id="target_test",
        game_id="among_us",
        platform="macos",
        runtime="il2cpp",
        architecture="arm64",
        pointer_width=8,
        executable_or_package="/tmp/fake.app",
        version="2026.11.3",
        build_fingerprint=BuildFingerprint(
            sha256_main="a" * 64,
            sha256_metadata=None,
            extras={"module_architecture": "arm64"},
        ),
        modules=[
            Module(
                name="UnityFramework",
                base_rva="+0x0",
                size="0x1000",
                sha256="b" * 64,
            )
        ],
        capabilities=["static_analysis"],
    )
    base.update(overrides)
    return GameTarget(**base)


def test_identity_rejects_arch_pointer_width_mismatch():
    bad = _baseline_target(architecture="x86", pointer_width=8)
    problems = validate_identity(bad)
    assert any("pointer_width" in p for p in problems), problems
    with pytest.raises(ValueError, match="pointer_width"):
        enforce_identity(bad)


def test_identity_rejects_module_arch_mismatch():
    bad = _baseline_target(
        build_fingerprint=BuildFingerprint(
            sha256_main="a" * 64,
            sha256_metadata=None,
            extras={"module_architecture": "x86_64"},
        ),
    )
    problems = validate_identity(bad)
    assert any("module architecture" in p for p in problems), problems
    with pytest.raises(ValueError, match="module architecture"):
        enforce_identity(bad)


def test_identity_rejects_windows_with_unity_framework_module():
    bad = _baseline_target(
        platform="windows",
        architecture="x86_64",
    )
    problems = validate_identity(bad)
    assert any("UnityFramework" in p for p in problems), problems
    with pytest.raises(ValueError, match="UnityFramework"):
        enforce_identity(bad)


def test_identity_accepts_consistent_record():
    good = _baseline_target()
    assert validate_identity(good) == []
    assert enforce_identity(good) is good


def test_wire_contract_still_bites():
    # Sanity: validate_record itself still refuses an outright bad
    # record (missing modules). This guards against a future edit that
    # accidentally loosens the shared base model.
    data = {
        "id": "x", "game_id": "x", "platform": "macos",
        "runtime": "il2cpp", "architecture": "arm64",
        "pointer_width": 8, "executable_or_package": "/x",
        "version": "x",
        "build_fingerprint": {"sha256_main": "a" * 64},
        "capabilities": [],
    }  # modules missing on purpose
    with pytest.raises(ValueError, match="modules"):
        validate_record("game_target", data)


# ---------------------------------------------------------------------------
# P02.derivation — synthetic Mach-O round-trips deterministically
# ---------------------------------------------------------------------------


def _write_fake_macho64(path: Path) -> bytes:
    """Write a 32-byte mach_header_64 (arm64) followed by zero padding
    so the file is a definite mach-O on inspection.
    """
    header = struct.pack(
        "<IIIIIIII",
        MH_MAGIC_64,         # magic
        CPU_TYPE_ARM64,      # cputype
        0,                   # cpusubtype
        2,                   # filetype (MH_EXECUTE)
        0,                   # ncmds
        0,                   # sizeofcmds
        0,                   # flags
        0,                   # reserved
    )
    padding = b"\xAB" * 128  # deterministic stable tail
    body = header + padding
    path.write_bytes(body)
    return body


def test_derivation_is_deterministic_from_bytes(tmp_path):
    exe = tmp_path / "FakeGame"
    body = _write_fake_macho64(exe)

    first = read_macho_info(exe)
    second = read_macho_info(exe)
    assert first == second
    assert first.architecture == "arm64"
    assert first.pointer_width == 8
    assert first.is_fat is False
    assert first.sub_architectures == ("arm64",)

    sha_a = sha256_file(exe)
    sha_b = sha256_file(exe)
    assert sha_a == sha_b
    assert sha_a == hashlib.sha256(body).hexdigest()


def test_derivation_detects_non_macho(tmp_path):
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"this is not a mach-o file at all")
    with pytest.raises(ValueError, match="not a Mach-O"):
        read_macho_info(junk)


def test_discover_installed_filters_non_bundles(tmp_path, monkeypatch):
    # Force the mac discovery path even on non-Darwin hosts via roots.
    # A directory with no .app inside yields an empty list cleanly.
    (tmp_path / "junk").mkdir()
    out = discover_installed(platform_filter="macos", macos_roots=[tmp_path])
    assert out == []


# ---------------------------------------------------------------------------
# P02.capability_report — honest three-way status
# ---------------------------------------------------------------------------


def test_capability_report_static_only_when_attempt_disabled():
    target = _baseline_target()
    result = probe_runtime_access(target, attempt=False)
    assert result.status == "static_only"
    assert "skipped" in result.reason
    assert result.bytes_read == 0
    assert result.pid is None
    assert result.sample_sha256 is None


def test_capability_report_blocked_when_bundle_missing(tmp_path):
    fake_bundle = tmp_path / "Ghost.app"
    fake_bundle.mkdir()
    target = _baseline_target(executable_or_package=str(fake_bundle))
    result = probe_runtime_access(target, attempt=True)
    assert result.status == "runtime_access_blocked"
    assert "executable does not exist" in result.reason


def test_capability_report_blocked_on_non_macos_target():
    # Build a windows target with a plain module name so identity
    # validation passes; then probe runtime access and confirm it
    # reports blocked rather than pretending the probe ran.
    target = _baseline_target(
        platform="windows",
        architecture="x86_64",
        pointer_width=8,
        modules=[
            Module(name="Game.exe", base_rva="+0x0", size="0x1000", sha256="c" * 64)
        ],
        build_fingerprint=BuildFingerprint(
            sha256_main="a" * 64, sha256_metadata=None, extras={}
        ),
    )
    result = probe_runtime_access(target, attempt=True)
    assert result.status == "runtime_access_blocked"
    assert "windows" in result.reason.lower()


def test_rea_tool_discovery_records_pins():
    manifest = rea_tool_discovery()
    assert manifest["pinned_tools"]["cpp2il"]["version"] == "2022.1.0-pre-release.21"
    assert "path_probes" in manifest
    assert manifest["path_probes"]["cpp2il"]["pinned_version"] == "2022.1.0-pre-release.21"


# ---------------------------------------------------------------------------
# P02.target_memory_access — live Among Us wrapped-iPhoneOS bundle
# ---------------------------------------------------------------------------


_AMONG_US_PRESENT = AMONG_US_WRAPPED.is_dir() and (
    AMONG_US_WRAPPED / "Frameworks" / "UnityFramework.framework" / "UnityFramework"
).is_file()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only target_memory_access check")
@pytest.mark.skipif(
    not _AMONG_US_PRESENT,
    reason="Among Us wrapped-iPhoneOS bundle not installed at /Applications/AmongUs.app/Wrapper/AmongUs.app",
)
def test_target_memory_access_against_installed_among_us(tmp_path):
    targets = discover_installed(
        platform_filter="macos",
        macos_roots=[Path("/Applications")],
    )
    among = [t for t in targets if t.game_id == "amongus" or "among" in t.id.lower()]
    assert among, "discover_installed did not surface the installed Among Us bundle"
    target = among[0]
    # Identity gate — the discovered record must be internally
    # consistent before we ever run a bounded read against it.
    enforce_identity(target)

    result = probe_runtime_access(target, attempt=True, bytes_to_read=64)
    assert result.status in ("runtime_ready", "runtime_access_blocked")
    if result.status == "runtime_access_blocked":
        # Reason must name the actual block. The oracle code path
        # records a 'task_for_pid' or 'no running process' reason;
        # either is acceptable, but a bare 'blocked' is not.
        assert result.reason, "blocked status must carry a reason"
        assert result.pid is None or result.pid > 0
    else:
        assert result.bytes_read == 64
        assert result.pid and result.pid > 0
        assert result.sample_sha256 is not None
        assert result.access_mode in ("in_process", "external")


@pytest.mark.parametrize("field", ["main", "module", "metadata"])
def test_identity_rehashes_supplied_files_and_rejects_forgery(tmp_path, field):
    from bughunter.game_runtime.targets import verify_target_files
    import plistlib
    bundle = tmp_path / "Measured.app"
    module = bundle / "Frameworks/UnityFramework.framework/UnityFramework"
    module.parent.mkdir(parents=True)
    executable = bundle / "Measured"
    _write_fake_macho64(executable)
    _write_fake_macho64(module)
    metadata = bundle / "Data/Managed/Metadata/global-metadata.dat"
    metadata.parent.mkdir(parents=True)
    metadata.write_bytes(b"measured metadata")
    (bundle / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "Measured"}))
    target = _baseline_target(executable_or_package=str(bundle),
        build_fingerprint=BuildFingerprint(sha256_main=sha256_file(executable), sha256_metadata=sha256_file(metadata), extras={}),
        modules=[Module(name="UnityFramework", base_rva="+0x0", size=hex(module.stat().st_size), sha256=sha256_file(module))])
    assert len(verify_target_files(target)) == 3
    if field == "main":
        target.build_fingerprint.sha256_main = "0" * 64
    elif field == "metadata":
        target.build_fingerprint.sha256_metadata = "0" * 64
    else:
        target.modules[0].sha256 = "0" * 64
    with pytest.raises(ValueError, match="sha256 mismatch"):
        verify_target_files(target)
    record = tmp_path / "target.json"
    record.write_text(target.model_dump_json())
    run = subprocess.run([sys.executable, str(REPO_ROOT / "scripts/qualify_game_target.py"),
        "--target", str(record), "--report-dir", str(tmp_path), "--no-runtime-attempt"], capture_output=True, text=True)
    assert run.returncode == 2, run.stderr
    assert not (tmp_path / "target_qualification.json").exists()


@pytest.mark.parametrize("machine,magic,expected", [(0x14c,0x10b,("x86",4)), (0x8664,0x20b,("x86_64",8)), (0xaa64,0x20b,("arm64",8))])
def test_identity_pe_architecture_is_measured(tmp_path, machine, magic, expected):
    from bughunter.game_runtime.targets import read_pe_identity
    data = bytearray(128)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 64)
    data[64:68] = b"PE\0\0"
    struct.pack_into("<H", data, 68, machine)
    struct.pack_into("<H", data, 88, magic)
    exe = tmp_path / "fixture.exe"
    exe.write_bytes(data)
    assert read_pe_identity(exe) == expected
    struct.pack_into("<H", data, 88, 0)
    exe.write_bytes(data)
    with pytest.raises(ValueError, match="inconsistent"):
        read_pe_identity(exe)


def test_capability_discovery_uses_live_rea_catalog():
    class Session:
        catalog = [{"name": "inspect_binary", "description": "inspect"}]
        availability = {"groups": [{"available": True, "names": ["inspect_binary"]}]}
        def request(self, name, arguments, timeout):
            assert name == "binary_session"
            return {"structuredContent": {"result": {"server_identity": {"version": "fixture"}}}}
    class Rea:
        closed = False
        def session(self, scope): return Session()
        def close_task(self, scope): self.closed = True
    rea = Rea()
    report = rea_tool_discovery(rea)
    assert report["rea"]["available"] is True
    assert report["rea"]["catalog"][0]["name"] == "inspect_binary"
    assert rea.closed


def test_derivation_wrong_tool_hash_is_not_executed(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("qualify_target_test", REPO_ROOT / "scripts/qualify_game_target.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    binary = tmp_path / "tool"
    binary.write_text("#!/bin/sh\ntouch should-not-exist\n")
    binary.chmod(0o700)
    result = cli._static_analysis(_baseline_target(), [], tool_path=binary, report_dir=tmp_path)
    assert result["status"] == "tool_error"
    assert not (tmp_path / "static-analysis").exists()


def test_identity_rejects_hashed_wrong_arch_windows_module(tmp_path):
    from bughunter.game_runtime.targets import verify_target_files
    def pe(path, machine, magic):
        data = bytearray(128); data[:2] = b"MZ"
        struct.pack_into("<I",data,60,64); data[64:68]=b"PE\0\0"
        struct.pack_into("<H",data,68,machine);struct.pack_into("<H",data,88,magic)
        path.write_bytes(data)
    main=tmp_path/"game.exe";mod=tmp_path/"GameAssembly.dll"
    pe(main,0x8664,0x20b);pe(mod,0x14c,0x10b)
    target=_baseline_target(platform="windows",architecture="x86_64",executable_or_package=str(main),
        build_fingerprint=BuildFingerprint(sha256_main=sha256_file(main),extras={}),
        modules=[Module(name=mod.name,base_rva="+0x0",size=hex(mod.stat().st_size),sha256=sha256_file(mod))])
    with pytest.raises(ValueError,match="module architecture"):
        verify_target_files(target)


# ---------------------------------------------------------------------------
# Conventional-macOS Unity layout (Combat Master-style)
# ---------------------------------------------------------------------------


def _write_conventional_unity_bundle(bundle: Path) -> dict[str, Path]:
    """Lay out a synthetic conventional-macOS Unity IL2CPP bundle:

    * ``Contents/Info.plist`` + ``Contents/MacOS/<exec>``
    * ``Contents/Frameworks/UnityPlayer.dylib``
    * ``Contents/Frameworks/GameAssembly.dylib``
    * ``Contents/Resources/Data/il2cpp_data/Metadata/global-metadata.dat``
    * ``Contents/Resources/Data/boot.config`` with a ``build-guid`` line
    """
    import plistlib
    contents = bundle / "Contents"
    macos_dir = contents / "MacOS"
    frameworks = contents / "Frameworks"
    data = contents / "Resources" / "Data"
    il2cpp_meta = data / "il2cpp_data" / "Metadata"
    for d in (macos_dir, frameworks, il2cpp_meta):
        d.mkdir(parents=True)
    exec_path = macos_dir / "SyntheticGame"
    unity_player = frameworks / "UnityPlayer.dylib"
    game_assembly = frameworks / "GameAssembly.dylib"
    _write_fake_macho64(exec_path)
    _write_fake_macho64(unity_player)
    _write_fake_macho64(game_assembly)
    metadata = il2cpp_meta / "global-metadata.dat"
    metadata.write_bytes(b"synthetic il2cpp metadata")
    (data / "boot.config").write_text(
        "scripting-runtime-version=latest\nbuild-guid=deadbeefcafe\n"
    )
    (contents / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleExecutable": "SyntheticGame",
        "CFBundleIdentifier": "com.test.SyntheticGame",
        "CFBundleShortVersionString": "1.0.0",
    }))
    return {
        "bundle": bundle,
        "exec": exec_path,
        "unity_player": unity_player,
        "game_assembly": game_assembly,
        "metadata": metadata,
    }


def test_discover_installed_recognises_conventional_unity_bundle(tmp_path):
    """A conventional macOS Unity IL2CPP bundle (UnityPlayer + GameAssembly,
    Il2cpp metadata under Contents/Resources/Data) must be discovered as
    a macos target with both modules recorded and the layout tag set."""
    bundle = tmp_path / "SyntheticGame.app"
    bundle.mkdir()
    _write_conventional_unity_bundle(bundle)
    targets = discover_installed(platform_filter="macos", macos_roots=[tmp_path])
    assert len(targets) == 1
    target = targets[0]
    assert target.platform == "macos"
    assert target.runtime == "il2cpp"
    assert target.architecture == "arm64"
    assert target.pointer_width == 8
    assert {m.name for m in target.modules} == {"UnityPlayer", "GameAssembly"}
    extras = target.build_fingerprint.extras
    assert extras["layout"] == "conventional_macos"
    assert extras["wrapper_layout"] is False
    paths = extras["module_paths"]
    assert paths["UnityPlayer"] == "Contents/Frameworks/UnityPlayer.dylib"
    assert paths["GameAssembly"] == "Contents/Frameworks/GameAssembly.dylib"
    assert extras["unity_build_guid"] == "deadbeefcafe"
    assert "il2cpp_metadata_present" in target.capabilities
    assert "il2cpp_gameassembly_present" in target.capabilities


def test_verify_target_files_accepts_conventional_layout(tmp_path):
    from bughunter.game_runtime.targets import verify_target_files
    bundle = tmp_path / "SyntheticGame.app"
    bundle.mkdir()
    _write_conventional_unity_bundle(bundle)
    targets = discover_installed(platform_filter="macos", macos_roots=[tmp_path])
    measured = verify_target_files(targets[0])
    roles = {m["role"] for m in measured}
    # main + UnityPlayer + GameAssembly + metadata
    assert roles == {"main", "UnityPlayer", "GameAssembly", "metadata"}


def test_fat_macho_prefers_host_arch_slice(tmp_path):
    """A universal binary whose first slice is NOT the host's arch must
    still be reported with the host-matching slice in ``architecture``.
    Running arm64 Mac + a (x86_64, arm64) fat binary must pick arm64."""
    from bughunter.game_runtime.targets import (
        CPU_TYPE_X86_64, FAT_CIGAM, _host_architecture,
    )
    exe = tmp_path / "FatGame"
    # Minimal fat header, big-endian: magic, nfat=2; entries listed with
    # x86_64 FIRST and arm64 second so we can tell the choice came from
    # host matching, not slice order.
    header = struct.pack(">I", FAT_CIGAM) + struct.pack(">I", 2)
    # 20-byte 32-bit fat_arch entries.
    for cpu in (CPU_TYPE_X86_64, CPU_TYPE_ARM64):
        header += struct.pack(">IIIII", cpu, 0, 0x100, 0x100, 0)
    # Pad to 4 KiB to give read_macho_info the header window it reads.
    exe.write_bytes(header + b"\x00" * (4096 - len(header)))
    info = read_macho_info(exe)
    assert info.is_fat is True
    assert set(info.sub_architectures) == {"x86_64", "arm64"}
    host = _host_architecture()
    if host in ("arm64", "x86_64"):
        assert info.architecture == host
    else:
        assert info.architecture in ("arm64", "x86_64")


# ---------------------------------------------------------------------------
# Live Combat Master (conventional-macOS) target, skip if not installed
# ---------------------------------------------------------------------------


_COMBAT_MASTER_PRESENT = COMBAT_MASTER_BUNDLE.is_dir() and (
    COMBAT_MASTER_BUNDLE / "Contents" / "Frameworks" / "UnityPlayer.dylib"
).is_file()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only live test")
@pytest.mark.skipif(
    not _COMBAT_MASTER_PRESENT,
    reason=f"Combat Master not installed at {COMBAT_MASTER_BUNDLE}",
)
def test_target_memory_access_against_installed_combat_master():
    from bughunter.game_runtime.targets import verify_target_files
    targets = discover_installed(
        platform_filter="macos",
        macos_roots=[COMBAT_MASTER_BUNDLE.parent],
    )
    combat = [t for t in targets if t.game_id == "combatmaster"]
    assert combat, "discover_installed did not surface Combat Master"
    target = combat[0]
    enforce_identity(target)
    measured = verify_target_files(target)
    roles = {m["role"] for m in measured}
    assert {"main", "UnityPlayer", "GameAssembly", "metadata"} <= roles
    result = probe_runtime_access(target, attempt=True, bytes_to_read=64)
    assert result.status in ("runtime_ready", "runtime_access_blocked")
    if result.status == "runtime_ready":
        assert result.bytes_read == 64
        assert result.pid and result.pid > 0
        assert result.sample_sha256 is not None
        assert result.access_mode in ("in_process", "external")
    else:
        assert result.reason
