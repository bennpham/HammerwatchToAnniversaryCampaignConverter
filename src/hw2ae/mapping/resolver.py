"""HW1 resource path → AE placement.

Three rule families cover what a level mostly consists of; everything else goes
through the curated ``data/units.json``:

* theme walls  ``doodads/theme_<t>/<t>_<piece>.xml``
* cover        ``doodads/special/color_theme_<t>_<N>.xml``
* doors        ``items/door_<t>_<metal>_<h|v>[_...].xml``

Pixel offsets keep the art where HW1 drew it: ``AE origin - HW1 origin``. The
wall numbers below were checked against every paired Castle Hammerwatch level
(HW1 ``h`` walls anchor both games, so they have zero offset).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..ae.assets import AssetIndex

DATA = Path(__file__).parent / "data"

# HW1 wall piece suffix -> AE candidates (name, dx, dy), first that exists wins.
# Prison/armory name caps cap_h_*/cap_v_* and long runs h_32/v_32; archives,
# chambers and battlements use cap_n/s/e/w and h/v. The AE bonus themes kept
# the HW1 names, which the final "same name" fallback catches.
WALL_PIECES: dict[str, list[tuple[str, int, int]]] = {
    "h_8": [("h_16", 0, 0)],
    "h_16": [("h_32", 0, 0), ("h", 0, 0)],
    "v_8": [("v_16", 0, 0)],
    "v_16": [("v_32", 0, -16), ("v", 0, -16)],
    "crn_l_up": [("crn_nw", 0, 16)],
    "crn_r_up": [("crn_ne", 0, 16)],
    "crn_l_dn": [("crn_sw", 0, 0)],
    "crn_r_dn": [("crn_se", 0, 0)],
    "x_t_l": [("x_w", 0, 16)],
    "x_t_r": [("x_e", 0, 16)],
    "x_t_up": [("x_n", 0, 16)],
    "x_t_dn": [("x_s", 0, 0)],
    "x_x": [("x_x", 0, 16)],
    "h_cap_l": [("cap_h_w", 0, 0), ("cap_w", 0, 0)],
    "h_cap_r": [("cap_h_e", 0, 0), ("cap_e", 0, 0)],
    "v_cap_up": [("cap_v_n", 0, 16), ("cap_n", 0, 16)],
    "v_cap_dn": [("cap_v_s", 0, 0), ("cap_s", 0, 0)],
    # AE exits are 64 px wide against HW1's 32; centre them on the HW1 opening.
    "exit_h_up": [("exit_up", -16, 0)],
    "exit_h_dn": [("exit_dn", -16, 0)],
}

WALL_RE = re.compile(r"^doodads/theme_(\w+?)/\1_(.+)\.xml$")
COVER_RE = re.compile(r"^doodads/special/color_theme_(\w+?)_(\d+)\.xml$")
BLACK_RE = re.compile(r"^doodads/special/color_black_(\d+)\.xml$")
DOOR_RE = re.compile(r"^items/door_(\w)_(bronze|silver|gold)_(h|v)(.*)\.xml$")


@dataclass
class Placement:
    kind: str  # "unit" or "prefab"
    path: str
    dx: float = 0.0
    dy: float = 0.0
    params: dict = field(default_factory=dict)
    is_exit: bool = False
    door: DoorPiece | None = None
    key: str | None = None  # a single-unit door: the collectable its DoorController takes


@dataclass(frozen=True)
class DoorPiece:
    """A HW1 door piece. AE doors are rebuilt per door, not per piece, so the
    converter needs what the piece belongs to rather than an AE unit for it."""
    orient: str   # "h" or "v"
    metal: str    # bronze, silver, gold
    theme: str    # AE door theme (prison, armory, ...)
    piece: str    # HW1 piece: "", "v2", "cap_l", "cap_r", "cap_up", "cap_dn"

    def ae_path(self, part: str) -> str:
        return f"doodads/doors/door_{self.orient}_{part}_{self.metal}_{self.theme}.unit"


@dataclass
class Dropped:
    reason: str


class Resolver:
    def __init__(self, assets: AssetIndex, data_dir: Path = DATA):
        self.assets = assets
        self.themes: dict = {k: v for k, v in json.loads((data_dir / "themes.json").read_text()).items()
                             if not k.startswith("_")}
        self.units: dict = {k: v for k, v in json.loads((data_dir / "units.json").read_text()).items()
                            if not k.startswith("_")}
        tilesets = json.loads((data_dir / "tilesets.json").read_text())
        self.tilesets: dict = {k: v for k, v in tilesets.items() if not k.startswith("_")}
        self.overlay_tilesets: set[str] = set(tilesets.get("_overlays", []))
        self._cache: dict[str, Placement | Dropped | None] = {}

    # -- objects ---------------------------------------------------------
    def resolve(self, hw1_path: str) -> Placement | Dropped | None:
        """``None`` means unmapped: the caller reports it and leaves it out."""
        key = hw1_path.replace("\\", "/")
        if key not in self._cache:
            self._cache[key] = self._resolve(key)
        return self._cache[key]

    def _resolve(self, p: str) -> Placement | Dropped | None:
        entry = self.units.get(p)
        if entry is not None:
            if "drop" in entry:
                return Dropped(entry["drop"])
            if "prefab" in entry:
                return self._checked(Placement("prefab", entry["prefab"], entry.get("dx", 0), entry.get("dy", 0)))
            return self._checked(Placement("unit", entry["ae"], entry.get("dx", 0), entry.get("dy", 0),
                                           dict(entry.get("params", {})), key=entry.get("key")))

        m = WALL_RE.match(p)
        if m:
            return self._wall(m.group(1), m.group(2))
        m = COVER_RE.match(p)
        if m:
            theme = self.themes.get(m.group(1))
            if theme is None:
                return None
            n = int(m.group(2))
            return self._checked(Placement("unit", f"doodads/walls/{theme['walls']}/__color_{n}.unit", 0, n))
        m = BLACK_RE.match(p)
        if m:
            return self._checked(Placement("unit", f"doodads/ledges/__black_{m.group(1)}.unit", 0, 0))
        m = DOOR_RE.match(p)
        if m:
            return self._door(*m.groups())
        return None

    def _checked(self, pl: Placement) -> Placement | None:
        return pl if self.assets.exists(pl.path) else None

    def _wall(self, letter: str, piece: str) -> Placement | None:
        theme = self.themes.get(letter)
        if theme is None:
            return None
        folder = f"doodads/walls/{theme['walls']}"
        # A theme's own pieces where AE built it differently (Temple of the
        # Sun's caves use h/v where the castle uses h_32/v_32), as paired.
        own = [tuple(theme["pieces"][piece])] if piece in theme.get("pieces", {}) else []
        cands = own + WALL_PIECES.get(piece, []) + [(piece, 0, 0)]
        for name, dx, dy in cands:
            path = f"{folder}/{name}.unit"
            if self.assets.exists(path):
                if theme.get("hw1_origins"):
                    # The bonus themes kept HW1's pieces and origins (AE's own
                    # bonus levels stack them exactly as HW1 does), so the
                    # prison-measured offsets would tear their columns apart.
                    dx = dy = 0
                return Placement("unit", path, dx, dy, is_exit=name.startswith("exit_"))
        return None

    def _door(self, letter: str, metal: str, orient: str, rest: str) -> Placement | None:
        theme = self.themes.get(letter)
        if theme is None:
            return None
        # ``door_a_bronze_v2`` and ``door_a_bronze_h_v2`` are both the short piece.
        piece = next((p for p in ("cap_l", "cap_r", "cap_up", "cap_dn") if p in rest),
                     "v2" if rest.endswith("2") else "")
        door = DoorPiece(orient, metal, theme["doors"], piece)
        # The mid piece stands for the whole door when checking it exists.
        pl = self._checked(Placement("unit", door.ae_path("mid")))
        if pl is not None:
            pl.door = door
        return pl

    # -- tiles -----------------------------------------------------------
    def tileset(self, hw1_tileset: str) -> list[str] | None:
        out = self.tilesets.get(hw1_tileset.replace("\\", "/"))
        if out is None:
            return None
        return [t for t in out if self.assets.exists(t)] or None

    def theme_of(self, hw1_path: str) -> str | None:
        for rx in (WALL_RE, COVER_RE):
            m = rx.match(hw1_path)
            if m and m.group(1) in self.themes:
                return m.group(1)
        return None
