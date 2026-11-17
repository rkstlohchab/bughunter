"""Engine mapping probes against synthetic memory and the metadata reader against synthetic files."""
from __future__ import annotations

import struct
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fake_memory import il2cpp_image, unreal_image  # noqa: E402

from bughunter.game_runtime.engine_map.detect import detect_engine  # noqa: E402
from bughunter.game_runtime.engine_map.il2cpp_metadata import (  # noqa: E402
    MetadataError, read_metadata, search_identifiers,
)
from bughunter.game_runtime.engine_map.il2cpp_runtime import (  # noqa: E402
    Il2CppClassProbe, Il2CppFieldProbe, Il2CppInstanceProbe,
)
from bughunter.game_runtime.engine_map.memview import Sweep  # noqa: E402
from bughunter.game_runtime.engine_map.unreal_runtime import (  # noqa: E402
    NameLayout, UnrealNamesProbe, UnrealObjectsProbe, solve_header,
)
from bughunter.game_runtime.scans import ScanBudgetExhausted  # noqa: E402
from bughunter.memory_engine.discovery import _BoundedAdapter  # noqa: E402


def _regions(mem):
    return mem.regions(writable_only=False), mem.regions_writable()


def _run(probe, mem):
    probe.advance(mem)
    assert probe.done
    return probe.result


# --------------------------------------------------------------- metadata


def _metadata(tmp_path, *, version=39, fields=3, tamper=False, magic=0xFAB11BAF):
    identifiers = b"mscorlib\0<Module>\0PlayerWeapon\0_chargedAmmoLeft\0get_AmmoKitPrice\0Weapon\0" * 4
    sections = [b"\x01\x02\x03\x04" * 8, identifiers, b"\x00\xff" * 20]
    header = 8 + len(sections) * 4 * fields
    body, records, cursor = b"", [], header
    for section in sections:
        records.append((cursor, len(section), len(section) // 4))
        body += section
        cursor += len(section)
    head = struct.pack("<Ii", magic, version)
    for offset, size, count in records:
        head += struct.pack("<iii", offset, size, count) if fields == 3 else struct.pack("<ii", offset, size)
    blob = head + body + (b"\0" if tamper else b"")
    path = tmp_path / "global-metadata.dat"
    path.write_bytes(blob)
    return path


@pytest.mark.parametrize("fields", [3, 2])
def test_metadata_tiling_layouts_and_identifier_section(tmp_path, fields):
    meta = read_metadata(_metadata(tmp_path, fields=fields))
    assert meta.version == 39 and meta.identifier_section == 1
    found = search_identifiers(meta, "ammo")
    assert found["results"][:2] == ["_chargedAmmoLeft", "get_AmmoKitPrice"]
    assert search_identifiers(meta, "PlayerWeapon")["results"][0] == "PlayerWeapon"


def test_metadata_rejects_untiled_or_foreign_files(tmp_path):
    with pytest.raises(MetadataError) as error:
        read_metadata(_metadata(tmp_path, tamper=True))
    assert error.value.code == "unsupported_header_layout"
    with pytest.raises(MetadataError) as error:
        read_metadata(_metadata(tmp_path, magic=0x12345678))
    assert error.value.code == "bad_magic"


def test_detect_engine_from_bundle_and_modules(tmp_path):
    app = tmp_path / "Game.app"
    (app / "Contents/Frameworks").mkdir(parents=True)
    (app / "Contents/Frameworks/GameAssembly.dylib").write_bytes(b"x")
    meta = app / "Contents/Resources/Data/il2cpp_data/Metadata"
    meta.mkdir(parents=True)
    (meta / "global-metadata.dat").write_bytes(b"x")
    assert detect_engine([], str(app / "Contents/MacOS/Game"))["engine"] == "unity_il2cpp"
    assert detect_engine([{"path": "/x/GameAssembly.dylib"}])["engine"] == "unity_il2cpp"
    assert detect_engine([{"path": "/x/Game-Win64-Shipping.exe"}])["engine"] == "unreal"
    assert detect_engine([{"path": "/x/libmonobdwgc-2.0.dylib"}])["engine"] == "unity_mono"
    assert detect_engine([{"path": "/x/UnityPlayer.dylib"}])["engine"] == "unknown"


# --------------------------------------------------------------- IL2CPP


def test_il2cpp_class_probe_derives_record_and_rejects_lone_pointer():
    mem, at = il2cpp_image()
    readable, writable = _regions(mem)
    result = _run(Il2CppClassProbe("PlayerWeapon", readable, writable), mem)
    best = result["candidates"][0]
    assert best["class_address"] == hex(at["weapon"])
    assert best["name_slot_offset"] == "0x10"
    assert best["namespace"] == "Game.Combat" and best["namespace_slot_delta"] == 8
    assert best["confidence"] == "candidate" and best["self_references"] == 1
    # The decoy pointer at heap+0x9000 has no referenced base and never becomes a candidate.
    assert all(int(c["name_pointer_slot"], 16) != 0x2000_9000 for c in result["candidates"])


def test_il2cpp_class_probe_namespace_filter_and_not_found():
    mem, _ = il2cpp_image()
    readable, writable = _regions(mem)
    assert _run(Il2CppClassProbe("PlayerWeapon", readable, writable, namespace="Other"), mem)["candidates"] == []
    assert _run(Il2CppClassProbe("NoSuchType", readable, writable), mem)["status"] == "not_found"


def test_il2cpp_instances_and_field_probe():
    mem, at = il2cpp_image()
    readable, writable = _regions(mem)
    instances = _run(Il2CppInstanceProbe(at["weapon"], writable), mem)
    assert set(instances["instances"]) >= {hex(a) for a in at["instances"]}
    assert hex(at["weapon"] + 0x40) not in instances["instances"]  # self reference excluded
    field = _run(Il2CppFieldProbe("_chargedAmmoLeft", at["weapon"], readable, writable), mem)
    candidate = field["candidates"][0]
    assert candidate["parent_slot_deltas"] == [0x10]
    assert {"slot_delta": 0x18, "value": "0x24"} in candidate["offset_candidates"]


def test_class_probe_resumes_across_byte_budgets():
    mem, at = il2cpp_image()
    readable, writable = _regions(mem)
    probe = Il2CppClassProbe("PlayerWeapon", readable, writable)
    calls = 0
    while not probe.done:
        calls += 1
        bounded = _BoundedAdapter(mem, threading.Event(), 5, 0x6000)
        try:
            probe.advance(bounded)
        except ScanBudgetExhausted:
            pass
        assert calls < 200
    assert calls > 3
    assert probe.result["candidates"][0]["class_address"] == hex(at["weapon"])


def test_sweep_records_unreadable_chunks_without_interpreting():
    mem, _ = il2cpp_image()
    sweep = Sweep([(0x1000_0000, 0x4000), (0x7000_0000, 0x1000)])
    seen = []
    assert sweep.advance(mem, lambda raw, at: seen.append(at), chunk=0x1000)
    assert seen == [0x1000_0000 + i * 0x1000 for i in range(4)]
    assert sweep.skipped_total == 1 and sweep.progress()["done"]


# --------------------------------------------------------------- Unreal


def test_solve_header_finds_length_field():
    solutions = solve_header(4 << 6, 12 << 6, 2)
    assert (6, 10) in solutions and all(((4 << 6) >> s) & ((1 << b) - 1) == 4 for s, b in solutions)


def test_unreal_names_probe_derives_layout_and_module_root():
    mem, at = unreal_image()
    readable, writable = _regions(mem)
    result = _run(UnrealNamesProbe(readable, writable, [("Game-Mac-Shipping", at["module"], 0x2000)]), mem)
    layout = result["layout"]
    assert layout["block_start"] == hex(at["block"]) and layout["header_bytes"] == 2
    assert layout["align"] == 2 and layout["decoded_names"][:3] == ["None", "ByteProperty", "IntProperty"]
    assert result["block_table_slots"][0]["root"] == "mod:Game-Mac-Shipping+0x800"
    assert "synthetic" in result["verified"]


def test_unreal_objects_probe_derives_layout_and_object_array():
    mem, at = unreal_image()
    readable, writable = _regions(mem)
    names = _run(UnrealNamesProbe(readable, writable, []), mem)["layout"]
    layout = NameLayout.from_wire({**names, "blocks_slot": hex(at["blocks_slot"])})
    result = _run(UnrealObjectsProbe(layout, writable, []), mem)
    assert result["class_object"] == hex(at["class_object"])
    obj = result["object_layout"]
    assert (obj["class_slot"], obj["name_slot"], obj["outer_slot"], obj["entry_stride"]) == ("0x10", "0x18", "0x20", 2)
    assert obj["index_block_shift"] is None and len(obj["index_block_shift_candidates"]) > 1
    assert any(row["entry"] == hex(at["array_entry"]) and row["item_stride"] == "0x18"
               for row in result["object_array_entries"])
