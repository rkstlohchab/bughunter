"""PUBG PC reader — Unreal Engine 4 with Krafton modifications. BE."""
from ..engines.unreal import UnrealWalker


class PubgPcWalker(UnrealWalker):
    GAME_ID = "pubg"
    ANTICHEAT = "battleye"
    MODULE_NAME = "TslGame.exe"
