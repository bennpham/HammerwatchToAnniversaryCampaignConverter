"""HW1 door pieces → AE doors.

HW1 builds a door from overlapping pieces of mixed length (``h`` is 32 px,
``h_v2`` 16 px, caps 8 px; vertical pieces are 40 px tall and overlap). AE's
doors are runs of 16-px pieces, ``cap_l, mid.., cap_r`` (``cap_u, mid..,
cap_d``), and only open through a ``DoorController`` that names the key. So
the pieces of each HW1 door are grouped and the door is laid out again.

The layout rules come from pairing the Castle Hammerwatch levels of both
games:

* horizontal: the run covers exactly the span of the HW1 pieces' collision
  boxes; an AE piece's origin is its left edge; the row sits at
  ``(floor(y) + 1) * 16``;
* vertical: the run fills the tile rows between the HW1 walls above and below
  the door, one piece per row with its origin at the row's bottom edge plus one
  row (AE's pieces reach into the wall below); the column is centred on HW1's.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..hw1.level import Obj
from ..mapping.resolver import DoorPiece
from .model import Level, Param, Script, Unit

TILE = 16

# HW1 collision extent along the door, in px from the piece's position.
H_EXTENT = {"": (-16, 16), "v2": (-8, 8), "cap_l": (-8, 0), "cap_r": (0, 8)}
V_EXTENT = {"": (-32, 8), "v2": (-16, 8), "cap_up": (-24, 8), "cap_dn": (-8, 24)}
# Castle Hammerwatch: an AE vertical run starts 24 px below HW1's collision top.
V_SHIFT = 24
# Pieces further apart than this (tiles, along the door) are separate doors.
MAX_GAP = 2.1
# Walls this far (tiles) past a vertical door's ends still bound it.
WALL_REACH = 3


@dataclass
class HW1Door:
    kind: DoorPiece                  # orientation/metal/theme shared by the pieces
    pieces: list[tuple[Obj, DoorPiece]] = field(default_factory=list)

    @property
    def objs(self) -> list[Obj]:
        return [o for o, _ in self.pieces]


def group(pieces: list[tuple[Obj, DoorPiece]]) -> list[HW1Door]:
    """Adjacent pieces of the same orientation, metal and theme form one door."""
    buckets: dict[tuple, list[tuple[Obj, DoorPiece]]] = {}
    for o, d in pieces:
        line = round(o.y * 4) if d.orient == "h" else round(o.x * 4)
        buckets.setdefault((d.orient, d.metal, d.theme, line), []).append((o, d))
    doors: list[HW1Door] = []
    for (orient, *_), items in sorted(buckets.items()):
        along = (lambda p: p[0].x) if orient == "h" else (lambda p: p[0].y)
        items.sort(key=along)
        cur: HW1Door | None = None
        for p in items:
            if cur is None or along(p) - along(cur.pieces[-1]) > MAX_GAP:
                cur = HW1Door(p[1])
                doors.append(cur)
            cur.pieces.append(p)
    return doors


def _is_wall(o: Obj) -> bool:
    name = o.type.rsplit("/", 1)[-1]
    return o.type.startswith("doodads/theme_") and not name.startswith("color")


def layout(door: HW1Door, walls: list[Obj]) -> list[tuple[str, float, float]]:
    """AE pieces ``(part, x, y)`` for one HW1 door."""
    if door.kind.orient == "h":
        x0 = min(o.x * TILE + H_EXTENT[d.piece][0] for o, d in door.pieces)
        x1 = max(o.x * TILE + H_EXTENT[d.piece][1] for o, d in door.pieces)
        y = (math.floor(door.pieces[0][0].y) + 1) * TILE
        n = max(1, round((x1 - x0) / TILE))
        xs = [x0 + i * TILE for i in range(n)]
        return [(_part(i, n, "cap_l", "cap_r"), x, y) for i, x in enumerate(xs)]

    col = door.pieces[0][0].x
    x = col * TILE - TILE / 2
    top = min(o.y for o in door.objs)
    bot = max(o.y for o in door.objs)
    column = [w for w in walls if abs(w.x - col) <= 0.5]
    above = [w.y for w in column if top - WALL_REACH <= w.y < top]
    below = [w.y for w in column if bot < w.y <= bot + WALL_REACH]
    if above and below:
        u, d = max(above), min(below)
        ys = [(row + 1) * TILE for row in range(int(math.floor(u)) + 1, int(math.floor(d)) + 1)]
    else:
        y0 = min(o.y * TILE + V_EXTENT[p.piece][0] for o, p in door.pieces) + V_SHIFT
        y1 = max(o.y * TILE + V_EXTENT[p.piece][1] for o, p in door.pieces) + V_SHIFT
        n = max(1, round((y1 - y0) / TILE))
        ys = [y0 + (i + 1) * TILE for i in range(n)]
    n = len(ys)
    return [(_part(i, n, "cap_u", "cap_d"), x, y) for i, y in enumerate(ys)]


def _part(i: int, n: int, first: str, last: str) -> str:
    if n == 1:
        return "mid"
    return first if i == 0 else last if i == n - 1 else "mid"


def convert(pieces: list[tuple[Obj, DoorPiece]], hw1_objects: list[Obj], level: Level,
            id_map: dict[int, int]) -> int:
    """Add every door and its ``DoorController`` to ``level``; returns the count."""
    walls = [o for o in hw1_objects if _is_wall(o)]
    doors = group(pieces)
    for door in doors:
        ids = []
        for part, x, y in layout(door, walls):
            uid = level.new_id()
            level.units.append(Unit(door.kind.ae_path(part), x, y, uid))
            ids.append(uid)
        for o in door.objs:
            id_map[o.id] = ids[0]
        cx = sum(u.x for u in level.units[-len(ids):]) / len(ids)
        cy = min(u.y for u in level.units[-len(ids):]) - 2 * TILE
        level.scripts.append(Script("DoorController", level.new_id(), cx, cy,
                                    label=f"key_{door.kind.metal}", params=[
                                        Param("ids", "Doors", ids),
                                        Param("string", "Collectable", f"key_{door.kind.metal}"),
                                    ]))
    return len(doors)
