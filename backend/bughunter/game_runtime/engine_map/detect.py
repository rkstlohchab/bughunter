"""Engine identification from loaded modules and on-disk layout.

Each rule records where its marker comes from. The IL2CPP rule was measured
on Combat Master 0.30.4 (macOS, 2026-11-17). The Mono and Unreal rules
follow those engines' documented packaging layouts and have not been
measured on a build on this machine.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

RULE_SOURCES = {
    "unity_il2cpp": "measured: Combat Master 0.30.4 ships GameAssembly.dylib + il2cpp_data/Metadata/global-metadata.dat",
    "unity_mono": "Unity Mono player layout (Managed/Assembly-CSharp.dll + MonoBleedingEdge/libmono*); not measured here",
    "unreal": "Unreal packaging layout (Content/Paks/*.pak) and Engine/Binaries; not measured here",
}


def _names(modules: Iterable) -> list[str]:
    out = []
    for row in modules or ():
        if isinstance(row, dict):
            value = row.get("path") or row.get("name") or ""
        else:
            value = row[0] if isinstance(row, (tuple, list)) else row
        name = str(value).rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if name:
            out.append(name)
    return out


def _bundle_root(path: Path) -> Path:
    for parent in [path, *path.parents]:
        if parent.suffix == ".app":
            return parent
    return path if path.is_dir() else path.parent


def detect_engine(modules: Iterable = (), bundle_path: str | None = None) -> dict:
    """Return ``{engine, evidence, files}``; engine is ``unknown`` without positive evidence."""
    names = _names(modules)
    lowered = [n.lower() for n in names]
    evidence: list[str] = []
    files: dict[str, str] = {}
    root = _bundle_root(Path(bundle_path)) if bundle_path else None

    def found(pattern: str) -> Path | None:
        if root is None or not root.exists():
            return None
        for hit in root.glob(pattern):
            return hit
        return None

    game_assembly = any(n.startswith("gameassembly") for n in lowered)
    metadata = found("**/il2cpp_data/Metadata/global-metadata.dat")
    if game_assembly:
        evidence.append("GameAssembly module loaded")
    if metadata is not None:
        files["metadata"] = str(metadata)
        evidence.append(f"metadata file {metadata.name}")
        if not game_assembly and found("**/GameAssembly.*") is not None:
            game_assembly = True
            evidence.append("GameAssembly file on disk")
    if game_assembly and (metadata is not None or root is None):
        return {"engine": "unity_il2cpp", "evidence": evidence, "files": files,
                "rule_source": RULE_SOURCES["unity_il2cpp"],
                "metadata_required": metadata is None}

    mono_module = any(n.startswith(("libmono", "mono-2.0", "monobdwgc")) for n in lowered)
    managed = found("**/Managed/Assembly-CSharp.dll")
    if mono_module or managed is not None:
        if mono_module:
            evidence.append("Mono runtime module loaded")
        if managed is not None:
            files["assembly_csharp"] = str(managed)
            evidence.append("Managed/Assembly-CSharp.dll present")
        return {"engine": "unity_mono", "evidence": evidence, "files": files,
                "rule_source": RULE_SOURCES["unity_mono"]}

    pak = found("**/Content/Paks/*.pak")
    shipping = [n for n in names if "-shipping" in n.lower()]
    if pak is not None or shipping:
        if pak is not None:
            files["pak"] = str(pak)
            evidence.append("Content/Paks/*.pak present")
        if shipping:
            evidence.append("Shipping binary " + shipping[0])
        return {"engine": "unreal", "evidence": evidence, "files": files,
                "rule_source": RULE_SOURCES["unreal"]}

    unity_player = any(n.startswith(("unityplayer", "unityframework")) for n in lowered)
    if unity_player:
        evidence.append("Unity player module loaded; scripting backend not identified")
    return {"engine": "unknown", "evidence": evidence, "files": files, "rule_source": None}
