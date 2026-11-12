"""BGMI / COD-Mobile / Free Fire reader — Android Zygisk bridge."""
from ..engines.android_native import AndroidNativeWalker


class BgmiWalker(AndroidNativeWalker):
    GAME_ID = "bgmi"
    ANTICHEAT = "bgmi-ace"
