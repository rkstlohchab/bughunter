"""Apex Legends reader — Source engine, Respawn modifications."""
from ..engines.apex_source import ApexWalker


class ApexReaderWalker(ApexWalker):
    GAME_ID = "apex"
    ANTICHEAT = "eac"
    MODULE_NAME = "r5apex.exe"
