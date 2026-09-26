"""Write an ``ae.model.Level`` as an AE ``.lvl`` (SVAL text), in the same layout
the AE editor saves: game-mode, version, lighting, tiles, units, scripts,
prefabs."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from ..sval import Writer
from .model import Level, Param, Script, Unit


def _state_value(w: Writer, name: str, tag: str, value) -> None:
    if tag == "bool":
        w.bool(bool(value), name)
    elif tag == "int":
        w.int(int(value), name)
    elif tag == "float":
        w.float(float(value), name)
    elif tag == "vec2":
        w.vec2(value[0], value[1], name)
    else:
        w.string(str(value), name)


def _write_unit(w: Writer, u: Unit) -> None:
    if not u.state:
        w.vec2(u.x, u.y)
        w.int(u.id)
        return
    w.open("array")
    w.vec2(u.x, u.y)
    w.int(u.id)
    w.open("dict")
    for name, (tag, value) in u.state.items():
        _state_value(w, name, tag, value)
    w.close("dict")
    w.close("array")


def _write_param(w: Writer, p: Param) -> None:
    if p.kind == "ids":
        w.open("array", p.name)
        for i in p.value:  # type: ignore[union-attr]
            w.int(i)
        w.close("array")
    elif p.kind == "bool":
        w.bool(bool(p.value), p.name)
    elif p.kind == "int":
        w.int(int(p.value), p.name)  # type: ignore[arg-type]
    elif p.kind == "float":
        w.float(float(p.value), p.name)  # type: ignore[arg-type]
    else:
        w.string(str(p.value), p.name)


def _write_script(w: Writer, s: Script) -> None:
    w.open("array")
    w.string(s.cls)
    w.int(s.id)
    w.vec3(s.x, s.y, 0)
    w.bool(s.enabled)
    w.int(s.trigger_times)
    w.bool(s.execute_on_start)
    if s.label:
        w.string(s.label)
    if s.params:
        w.open("dict")
        for p in s.params:
            _write_param(w, p)
        w.close("dict")
    if s.connections:
        w.open("array")
        for target, delay in s.connections:
            w.int(target)
            w.int(delay)
        w.close("array")
    w.close("array")


def render(level: Level) -> str:
    w = Writer()
    w.open("dict")

    w.open("dict", "game-mode")
    w.string(level.game_mode, "Class")
    w.close("dict")
    w.int(1, "version")
    if level.environment:
        w.open("dict", "lighting")
        w.string(level.environment, "environment")
        w.close("dict")

    w.open("array", "tiles")
    for (cx, cy), datasets in sorted(level.tile_cells.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        w.open("dict")
        w.vec2(cx, cy, "pos")
        w.open("array", "datasets")
        for tileset, rle in datasets:
            w.open("dict")
            w.string(tileset, "tileset")
            w.bytes(rle, "data-rle")
            w.close("dict")
        w.close("array")
        w.close("dict")
    w.close("array")

    groups: dict[str, list[Unit]] = defaultdict(list)
    for u in level.units:
        groups[u.path].append(u)
    w.open("dict", "units")
    for path in sorted(groups):
        w.open("array", path)
        for u in groups[path]:
            _write_unit(w, u)
        w.close("array")
    w.close("dict")

    w.open("array", "scripts")
    for s in level.scripts:
        _write_script(w, s)
    w.close("array")

    w.open("array", "prefabs")
    for p in level.prefabs:
        w.string(p.path)
        w.vec3(p.x, p.y, 0)
    w.close("array")

    w.close("dict")
    return w.text()


def write(level: Level, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(level), encoding="utf-8", newline="\n")
