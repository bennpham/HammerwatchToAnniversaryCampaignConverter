"""Check a scenario folder against AE's assets before opening it in the game.

Catches exactly what the AE editor logs as "Missing unit/tileset ... skipping",
plus broken script wiring and level exits that lead nowhere."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .ae import level_reader
from .ae.assets import AssetIndex


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

    for f in levels:
        rel = f.relative_to(scenario_dir).as_posix()
        lv = level_reader.load(f)
        missing: dict[str, int] = {}
        if lv.environment and not exists(lv.environment):
            problems.add(f"{rel}: missing environment {lv.environment}")
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
                    if arr.tag == "array" and arr.name in ("Areas", "Units", "Scripts"):
                        for c in arr:
                            if c.tag == "int" and c.value not in unit_ids | script_ids:
                                problems.add(f"{rel}: {s.cls} #{s.id} {arr.name} refers to missing #{c.value}")
            if s.cls == "LevelExit" and s.params is not None:
                target = s.params.get("Level")
                if target is not None and target.value.lower() not in own:
                    problems.add(f"{rel}: LevelExit #{s.id} leads to {target.value}, which is not in the scenario")
        if not any(s.cls == "LevelStart" for s in lv.scripts):
            problems.add(f"{rel}: no LevelStart, players have nowhere to spawn")
    return problems, len(levels)
