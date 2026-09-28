"""Check a scenario folder against AE's assets before opening it in the game.

Catches exactly what the AE editor logs as "Missing unit/tileset ... skipping",
plus broken script wiring and level exits that lead nowhere."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .ae import level_reader
from .ae.assets import AssetIndex
from .ae.tiles import CELL


@dataclass
class Problems:
    items: list[str] = field(default_factory=list)

    def add(self, msg: str) -> None:
        self.items.append(msg)

    def __bool__(self) -> bool:
        return bool(self.items)


def validate(scenario_dir: Path, assets: AssetIndex) -> tuple[Problems, int]:
    problems = Problems()
    levels = sorted(scenario_dir.rglob("*.lvl"))
    # Paths inside the scenario resolve against the scenario root too.
    own = {p.relative_to(scenario_dir).as_posix().lower() for p in scenario_dir.rglob("*") if p.is_file()}

    def exists(path: str) -> bool:
        return assets.exists(path) or path.lower() in own

    if not (scenario_dir / "info.xml").exists():
        problems.add("info.xml is missing")

    loaded = {f.relative_to(scenario_dir).as_posix().lower(): level_reader.load(f) for f in levels}
    start_ids = {rel: {_start_id(s) for s in lv.scripts if s.cls == "LevelStart"}
                 for rel, lv in loaded.items()}

    for f in levels:
        rel = f.relative_to(scenario_dir).as_posix()
        lv = loaded[rel.lower()]
        missing: dict[str, int] = {}
        if lv.environment and not exists(lv.environment):
            problems.add(f"{rel}: missing environment {lv.environment}")
        off_grid = sum(1 for c in lv.tiles if c.x % CELL or c.y % CELL)
        if off_grid:
            problems.add(f"{rel}: {off_grid} tile cell(s) not on the {CELL}-px grid; AE would shift their floor")
        for c in lv.tiles:
            for d in c.datasets:
                if not exists(d.tileset):
                    missing[d.tileset] = missing.get(d.tileset, 0) + 1
        unit_ids = set()
        for u in lv.units:
            unit_ids.add(u.id)
            if not exists(u.type):
                missing[u.type] = missing.get(u.type, 0) + 1
        for p in lv.prefabs:
            if not exists(p.path):
                missing[p.path] = missing.get(p.path, 0) + 1
        for path, n in sorted(missing.items()):
            problems.add(f"{rel}: missing {path} (x{n})")

        script_ids = {s.id for s in lv.scripts}
        for s in lv.scripts:
            for t, _ in s.connections:
                if t not in script_ids:
                    problems.add(f"{rel}: {s.cls} #{s.id} links to missing script #{t}")
            if s.params is not None:
                for arr in s.params:
                    if arr.tag == "array" and arr.name in ("Areas", "Units", "Scripts", "Doors"):
                        for c in arr:
                            if c.tag == "int" and c.value not in unit_ids | script_ids:
                                problems.add(f"{rel}: {s.cls} #{s.id} {arr.name} refers to missing #{c.value}")
            if s.cls == "LevelExit" and s.params is not None:
                target = s.params.get("Level")
                if target is not None and target.value.lower() not in own:
                    problems.add(f"{rel}: LevelExit #{s.id} leads to {target.value}, which is not in the scenario")
                elif target is not None and target.value.lower() in start_ids:
                    sid = _start_id(s)
                    if sid not in start_ids[target.value.lower()]:
                        problems.add(f"{rel}: LevelExit #{s.id} wants start '{sid}' in {target.value}, "
                                     "which has no such LevelStart")
        if not any(s.cls == "LevelStart" for s in lv.scripts):
            problems.add(f"{rel}: no LevelStart, players have nowhere to spawn")
        # A door opens only through a DoorController naming its key.
        controlled = {c.value for s in lv.scripts if s.cls == "DoorController" and s.params is not None
                      and s.params.get("Doors") is not None for c in s.params["Doors"]}
        orphans = sum(1 for u in lv.units
                      if u.type.startswith("doodads/doors/door_") and u.id not in controlled)
        if orphans:
            problems.add(f"{rel}: {orphans} door piece(s) without a DoorController; no key can open them")

    # A new game spawns at the start level's LevelStart without a StartID.
    for start in _start_levels(scenario_dir):
        if start.lower() in start_ids and "" not in start_ids[start.lower()]:
            problems.add(f"{start}: start level has no default LevelStart (one without a StartID); "
                         "a new game spawns players at 0,0")
    return problems, len(levels)


def _start_levels(scenario_dir: Path) -> list[str]:
    info = scenario_dir / "info.xml"
    if not info.exists():
        return []
    try:
        root = ET.fromstring(info.read_text(encoding="utf-8-sig", errors="replace"))
    except ET.ParseError:
        return []
    return [lv.get("lvl", "") for lv in root.iter("level") if lv.get("lvl")]


def _start_id(s: level_reader.Script) -> str:
    v = s.params.get("StartID") if s.params is not None else None
    return v.value if v is not None else ""
