"""Rust reader — Unity IL2CPP."""
from ..engines.unity_il2cpp import UnityIl2CppWalker


class RustWalker(UnityIl2CppWalker):
    GAME_ID = "rust"
    ANTICHEAT = "eac"
    MODULE_NAME = "GameAssembly.dll"
