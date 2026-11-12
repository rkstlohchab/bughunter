"""CS2 reader — Source 2, offsets auto-refresh from a2x/cs2-dumper."""
from ..engines.source2 import Source2Walker


class Cs2Walker(Source2Walker):
    GAME_ID = "cs2"
    ANTICHEAT = "vac-trust"
    MODULE_NAME = "client.dll"
