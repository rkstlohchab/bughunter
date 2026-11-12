"""Valorant reader — UE4. Vanguard blocks any software vector; DMA path only."""
from ..engines.unreal import UnrealWalker


class ValorantWalker(UnrealWalker):
    GAME_ID = "valorant"
    ANTICHEAT = "vanguard"
    MODULE_NAME = "VALORANT-Win64-Shipping.exe"
