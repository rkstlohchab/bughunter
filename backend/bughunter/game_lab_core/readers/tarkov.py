"""Escape from Tarkov reader — Unity IL2CPP. BattlEye."""
from ..engines.unity_il2cpp import UnityIl2CppWalker


class TarkovWalker(UnityIl2CppWalker):
    GAME_ID = "tarkov"
    ANTICHEAT = "battleye"
    MODULE_NAME = "GameAssembly.dll"
