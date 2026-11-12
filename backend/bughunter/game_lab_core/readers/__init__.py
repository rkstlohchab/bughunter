"""Per-game reader modules.

Each module defines a walker subclass with the game's module name,
anticheat tag, and matrix convention. Offsets come from the game's
own offset JSON at runtime; the walker class only encodes layout +
engine semantics.
"""
from .cs2 import Cs2Walker
from .valorant import ValorantWalker
from .apex import ApexReaderWalker
from .rust import RustWalker
from .fortnite import FortniteWalker
from .r6_siege import R6SiegeWalker
from .tarkov import TarkovWalker
from .cod_mw import CodMwWalker
from .pubg_pc import PubgPcWalker
from .bgmi import BgmiWalker


WALKER_BY_GAME = {
    "cs2":        Cs2Walker,
    "valorant":   ValorantWalker,
    "apex":       ApexReaderWalker,
    "rust":       RustWalker,
    "fortnite":   FortniteWalker,
    "r6_siege":   R6SiegeWalker,
    "tarkov":     TarkovWalker,
    "cod_mw":     CodMwWalker,
    "pubg":       PubgPcWalker,
    "bgmi":       BgmiWalker,
    "cod_mobile": BgmiWalker,     # same Android bridge, different offsets
    "free_fire":  BgmiWalker,
}
