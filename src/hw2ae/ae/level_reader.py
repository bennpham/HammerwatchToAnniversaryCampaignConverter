"""Read AE ``.lvl`` files (SVAL text) into a light model.

Used to learn mappings from AE's own remade campaign and to validate our output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import sval


@dataclass
class Unit:
    type: str
    x: float
    y: float
    id: int
    layer: int | None = None


@dataclass
class Script:
    cls: str
    id: int
    x: float
    y: float
    enabled: bool
    trigger_times: int
    label: str | None
    params: sval.Node | None
    connections: list[tuple[int, int]]


@dataclass
class TileDataset:
    tileset: str
    encoding: str  # "data" or "data-rle"
    hexdata: str


@dataclass
class TileCell:
    x: float
    y: float
    datasets: list[TileDataset]


@dataclass
class Prefab:
    path: str
    x: float
    y: float


@dataclass
class AELevel:
    game_mode: str | None
    environment: str | None
    units: list[Unit] = field(default_factory=list)
    scripts: list[Script] = field(default_factory=list)
    tiles: list[TileCell] = field(default_factory=list)
    prefabs: list[Prefab] = field(default_factory=list)


def _units(node: sval.Node | None) -> list[Unit]:
    out: list[Unit] = []
    if node is None:
        return out
    for arr in node:
        t = arr.name or ""
        kids = arr.children
        i = 0
        while i < len(kids):
            k = kids[i]
            if k.tag in ("vec2", "vec3"):
                # flat pair: <vec2/><int/>
                x, y = k.value[0], k.value[1]
                uid = kids[i + 1].value if i + 1 < len(kids) else -1
                out.append(Unit(t, x, y, uid))
                i += 2
            elif k.tag == "array":
                # wrapped: <array><vec2/><int/>[...]<dict>{layer}</dict></array>
                p = k.children[0].value
                uid = k.children[1].value
                layer = None
                for c in k.children[2:]:
                    if c.tag == "dict" and c.get("layer") is not None:
                        layer = c["layer"].value
                out.append(Unit(t, p[0], p[1], uid, layer))
                i += 1
            else:
                i += 1
    return out


def _scripts(node: sval.Node | None) -> list[Script]:
    out: list[Script] = []
    if node is None:
        return out
    for arr in node:
        k = arr.children
        if len(k) < 6:
            continue
        cls, sid, pos, en, tt = k[0].value, k[1].value, k[2].value, k[3].value, k[4].value
        label = None
        params = None
        conns: list[tuple[int, int]] = []
        for c in k[6:]:
            if c.tag == "string":
                label = c.value
            elif c.tag == "dict":
                params = c
            elif c.tag == "array":
                vals = [v.value for v in c]
                conns = [(vals[j], vals[j + 1]) for j in range(0, len(vals) - 1, 2)]
        out.append(Script(cls, sid, pos[0], pos[1], en, tt, label, params, conns))
    return out


def _tiles(node: sval.Node | None) -> list[TileCell]:
    out: list[TileCell] = []
    if node is None:
        return out
    for d in node:
        pos = d["pos"].value
        dsets = []
        for ds in d.get("datasets") or []:
            ts = ds["tileset"].value
            b = ds.get("data-rle")
            enc = "data-rle"
            if b is None:
                b = ds.get("data")
                enc = "data"
            dsets.append(TileDataset(ts, enc, b.text.strip() if b is not None else ""))
        out.append(TileCell(pos[0], pos[1], dsets))
    return out


def _prefabs(node: sval.Node | None) -> list[Prefab]:
    out: list[Prefab] = []
    if node is None:
        return out
    k = node.children
    for i in range(0, len(k) - 1, 2):
        p = k[i + 1].value
        out.append(Prefab(k[i].value, p[0], p[1]))
    return out


def load(path: str | Path) -> AELevel:
    root = sval.parse_file(path)
    gm = root.get("game-mode")
    lighting = root.get("lighting")
    env = lighting.get("environment") if lighting is not None else None
    return AELevel(
        game_mode=gm["Class"].value if gm is not None and gm.get("Class") is not None else None,
        environment=env.value if env is not None else None,
        units=_units(root.get("units")),
        scripts=_scripts(root.get("scripts")),
        tiles=_tiles(root.get("tiles")),
        prefabs=_prefabs(root.get("prefabs")),
    )
