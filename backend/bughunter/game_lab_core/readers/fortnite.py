"""Fortnite reader — UE5. Both BattlEye and EAC builds exist; the walker
is identical, only AC tag differs on the Frame."""
from ..engines.unreal import UnrealWalker


class FortniteWalker(UnrealWalker):
    GAME_ID = "fortnite"
    ANTICHEAT = "eac"
    MODULE_NAME = "FortniteClient-Win64-Shipping.exe"
