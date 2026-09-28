"""AE tile layers.

A level's floor is a list of 512-px cells. A cell's ``pos`` is its *centre*,
so it covers ``pos - 256 .. pos + 256``, and is always a multiple of 512: AE
floors any other ``pos`` onto that grid, which shifts the whole cell. Each cell holds one dataset per
tileset, and each dataset is a presence grid of ``512 / size`` × ``512 / size``
tiles (``size`` comes from the tileset file). AE picks tile variants and
borders on its own, so the grid only says where the tileset is painted.

``data-rle`` stores that grid row by row as signed bytes: ``n > 0`` is a run of
``n`` painted tiles, ``n < 0`` a run of ``-n`` empty ones. Runs are capped at
126 either way (``0x7e`` / ``0x82``).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

CELL = 512
MAX_RUN = 126


def decode_rle(hexdata: str, side: int) -> list[bool]:
    out: list[bool] = []
    for b in bytes.fromhex(hexdata):
        n = b - 256 if b >= 128 else b
        out.extend([n > 0] * abs(n))
    total = side * side
    if len(out) < total:
        out.extend([False] * (total - len(out)))
    return out[:total]


def encode_rle(grid: list[bool]) -> str:
    out = bytearray()
    i = 0
    while i < len(grid):
        v = grid[i]
        j = i
        while j < len(grid) and grid[j] == v and j - i < MAX_RUN:
            j += 1
        n = j - i
        out.append(n if v else 256 - n)
        i = j
    return out.hex()


@lru_cache(maxsize=None)
def tileset_size(ae_assets: str, tileset: str) -> int:
    """Tile size in px, read from the ``size`` attribute of the tileset file."""
    p = Path(ae_assets) / tileset
    try:
        m = re.search(r'<tileset[^>]*\bsize="(\d+)"', p.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        m = None
    return int(m.group(1)) if m else 16


class TileLayers:
    """Collects painted tiles per tileset in AE pixel space and emits cells."""

    def __init__(self, ae_assets: Path, scenario_dir: Path | None = None):
        self.ae_assets = str(ae_assets)
        # Tilesets shipped inside the scenario (ported HW1 art) live here.
        self.scenario_dir = scenario_dir
        # tileset -> set of (tx, ty) in that tileset's own tile units
        self.layers: dict[str, set[tuple[int, int]]] = {}
        self.order: list[str] = []

    def size(self, tileset: str) -> int:
        if self.scenario_dir is not None and (self.scenario_dir / tileset).is_file():
            return tileset_size(str(self.scenario_dir), tileset)
        return tileset_size(self.ae_assets, tileset)

    def paint16(self, tileset: str, tiles16: set[tuple[int, int]],
                grow: tuple[int, int, int, int] | None = None) -> None:
        """Paint ``tileset`` over a set of 16-px tiles (HW1's grid, origin 0).

        Finer tilesets get every sub-tile; a coarser tile is painted when at
        least half of it is covered, so floors keep their shape at 32 px.

        ``grow`` = (left, up, right, down) widens the painted area by that many
        16-px tiles on each side and then paints every coarse tile it touches.
        AE paints a floor's bottom layer past the walkable floor, under the
        walls, so the jagged borders of the layers end up hidden by wall art."""
        if grow:
            left, up, right, down = grow
            tiles16 = {(x + dx, y + dy) for x, y in tiles16
                       for dx in range(-left, right + 1) for dy in range(-up, down + 1)}
        s = self.size(tileset)
        layer = self.layers.get(tileset)
        if layer is None:
            layer = self.layers[tileset] = set()
            self.order.append(tileset)
        if s <= 16:
            k = 16 // s
            for tx, ty in tiles16:
                for a in range(k):
                    for b in range(k):
                        layer.add((tx * k + a, ty * k + b))
        else:
            k = s // 16
            need = 1 if grow else (k * k + 1) // 2
            counts: dict[tuple[int, int], int] = {}
            for tx, ty in tiles16:
                key = (tx // k, ty // k)
                counts[key] = counts.get(key, 0) + 1
            for key, c in counts.items():
                if c >= need:
                    layer.add(key)

    def cells(self) -> dict[tuple[int, int], list[tuple[str, str]]]:
        """{(cell_centre_x, cell_centre_y): [(tileset, rle_hex), ...]} in paint order."""
        out: dict[tuple[int, int], list[tuple[str, str]]] = {}
        for ts in self.order:
            s = self.size(ts)
            side = CELL // s
            half = CELL // 2
            by_cell: dict[tuple[int, int], set[tuple[int, int]]] = {}
            for tx, ty in self.layers[ts]:
                # Cell (cx, cy) is centred on (cx*512, cy*512).
                cx, cy = (tx * s + half) // CELL, (ty * s + half) // CELL
                x0, y0 = (cx * CELL - half) // s, (cy * CELL - half) // s
                by_cell.setdefault((cx, cy), set()).add((tx - x0, ty - y0))
            for (cx, cy), local in sorted(by_cell.items()):
                grid = [False] * (side * side)
                for lx, ly in local:
                    grid[ly * side + lx] = True
                centre = (cx * CELL, cy * CELL)
                out.setdefault(centre, []).append((ts, encode_rle(grid)))
        return out


def decode_cell(pos: tuple[float, float], tileset: str, hexdata: str, size: int):
    """Yield the pixel rect (x, y, size) of every painted tile in an AE dataset."""
    side = CELL // size
    x0, y0 = pos[0] - CELL // 2, pos[1] - CELL // 2
    for i, v in enumerate(decode_rle(hexdata, side)):
        if v:
            yield x0 + (i % side) * size, y0 + (i // side) * size, size
