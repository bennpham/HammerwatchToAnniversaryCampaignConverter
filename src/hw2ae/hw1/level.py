"""Hammerwatch 1 level XML → plain Python model.

Two dialects exist and both are accepted:

* the HW1 editor writes ``<vec2 name="pos">x y</vec2>`` and ``connection-delays``;
* the random dungeon generator writes ``<float name="x">`` / ``<float name="y">``
  and ``delays``.

Positions stay in HW1 tile units here. Converting to AE pixels is the
converter's job, because the offset depends on which AE unit the object maps to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import sval

CHUNK = 20  # a tiledata block is 20×20 tiles


@dataclass
class TileLayer:
    tileset: str
    # (tile_x, tile_y) -> variant (1-based; 0 never stored)
    tiles: dict[tuple[int, int], int] = field(default_factory=dict)


@dataclass
class Obj:
    id: int
    type: str
    x: float
    y: float
    layer: int | None = None
    extra: sval.Node | None = None  # the raw dictionary, for anything unusual


@dataclass
class ScriptNode:
    id: int
    type: str
    x: float
    y: float
    enabled: bool
    trigger_times: int
    params: sval.Node | None
    connections: list[int]
    delays: list[int]


@dataclass
class PrefabRef:
    path: str
    x: float
    y: float


@dataclass
class Level:
    path: Path
    layers: dict[str, TileLayer]
    doodads: list[Obj]
    actors: list[Obj]
    items: list[Obj]
    scripts: list[ScriptNode]
    prefabs: list[PrefabRef]
    lights: list[sval.Node]
    ambient: tuple[int, int, int, int] | None

    def all_objects(self):
        yield from self.doodads
        yield from self.actors
        yield from self.items


def _pos(d: sval.Node) -> tuple[float, float]:
    p = d.get("pos")
    if p is not None:
        v = p.value
        return v[0], v[1]
    x, y = d.get("x"), d.get("y")
    return (x.value if x is not None else 0.0), (y.value if y is not None else 0.0)


def _objects(section: sval.Node | None) -> list[Obj]:
    out: list[Obj] = []
    if section is None:
        return out
    for arr in section:
        if arr.tag != "array":
            continue
        for d in arr:
            if d.tag == "dictionary":
                # <array name="doodads"><dictionary>{id,type,pos|x/y,layer}</dictionary>
                t = d.get("type")
                if t is None:
                    continue
                x, y = _pos(d)
                layer = d.get("layer")
                out.append(Obj(id=d["id"].value, type=t.value, x=x, y=y,
                               layer=layer.value if layer is not None else None, extra=d))
            elif d.tag == "array" and arr.name:
                # <array name="actors/x.xml"><array><int>id</int><vec2>x y</vec2>[<dictionary>..]</array>
                uid = None
                pos = None
                extra = None
                for c in d:
                    if c.tag == "int" and uid is None:
                        uid = c.value
                    elif c.tag == "vec2" and pos is None:
                        pos = c.value
                    elif c.tag == "dictionary":
                        extra = c
                if uid is None or pos is None:
                    continue
                layer = extra.get("layer") if extra is not None else None
                out.append(Obj(id=uid, type=arr.name, x=pos[0], y=pos[1],
                               layer=layer.value if layer is not None else None, extra=extra))
    return out


def _tiles(section: sval.Node | None) -> dict[str, TileLayer]:
    layers: dict[str, TileLayer] = {}
    if section is None:
        return layers
    data = section.get("tiledata")
    if data is None:
        return layers
    for block in data:
        bx, by = block["x"].value, block["y"].value
        datasets = block.get("datasets")
        if datasets is None:
            continue
        for ds in datasets:
            ts = ds["tileset"].value
            values = ds["data-t"].value
            layer = layers.setdefault(ts, TileLayer(ts))
            # Cell i of a block declared at (X, Y) sits at (X-10+i%20, Y-10+i/20).
            for i, v in enumerate(values[: CHUNK * CHUNK]):
                if v:
                    layer.tiles[(bx - 10 + i % CHUNK, by - 10 + i // CHUNK)] = v
    return layers


def _scripts(section: sval.Node | None) -> list[ScriptNode]:
    out: list[ScriptNode] = []
    if section is None:
        return out
    nodes = section.get("nodes")
    if nodes is None:
        return out
    for d in nodes:
        x, y = _pos(d)
        conns = d.get("connections")
        # HW1 times links by "connection-delays". The random dungeon generator
        # also writes "delays" as a copy of the connection ids, not times; it
        # writes real delays under both names (HammerwatchRogueLikeDungeon-
        # GeneratorRemake: ScriptNode.connectTo), so "delays" alone is no delay.
        delays = d.get("connection-delays")
        enabled = d.get("enabled")
        tt = d.get("trigger-times")
        c = conns.value if conns is not None else []
        dl = delays.value if delays is not None else []
        dl = (dl + [0] * len(c))[: len(c)]
        out.append(ScriptNode(
            id=d["id"].value, type=d["type"].value, x=x, y=y,
            enabled=enabled.value if enabled is not None else True,
            trigger_times=tt.value if tt is not None else -1,
            params=d.get("parameters"), connections=c, delays=dl))
    return out


def _prefabs(section: sval.Node | None) -> list[PrefabRef]:
    out: list[PrefabRef] = []
    if section is None:
        return out
    for arr in section:
        if arr.tag != "array":
            continue
        for d in arr:
            if d.tag == "dictionary" and d.get("type") is not None:
                x, y = _pos(d)
                out.append(PrefabRef(d["type"].value, x, y))
            elif d.tag == "vec2":
                # <array name="prefabs/x.xml"><vec2>..</vec2>...</array>
                x, y = d.value
                out.append(PrefabRef(arr.name or "", x, y))
    return out


def load(path: str | Path) -> Level:
    path = Path(path)
    root = sval.parse_file(path)
    lighting = root.get("lighting")
    lights: list[sval.Node] = []
    ambient = None
    if lighting is not None:
        la = lighting.get("lights")
        if la is not None:
            lights = list(la)
        amb = lighting.get("ambient-color")
        if amb is not None:
            try:
                vals = [c.value for c in amb] if amb.children else amb.value
                ambient = tuple(int(v) for v in vals)[:4]  # type: ignore[assignment]
            except (TypeError, ValueError):
                ambient = None

    def sec(name: str) -> sval.Node | None:
        return root.get(name)

    return Level(
        path=path,
        layers=_tiles(sec("tilemap")),
        doodads=_objects(sec("doodads")),
        actors=_objects(sec("actors")),
        items=_objects(sec("items")),
        scripts=_scripts(sec("scripting")),
        prefabs=_prefabs(sec("prefabs")),
        lights=lights,
        ambient=ambient,
    )
