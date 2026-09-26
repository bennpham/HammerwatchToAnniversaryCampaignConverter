"""HW1 campaign/mission metadata: ``levels.xml`` and ``info.xml``."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class LevelEntry:
    id: str
    res: str       # relative to the campaign root, e.g. levels/level0.xml
    name: str
    act: str | None


@dataclass
class Campaign:
    root: Path
    name: str
    description: str
    lives: int | None
    start: str
    levels: list[LevelEntry] = field(default_factory=list)

    def by_id(self, level_id: str) -> LevelEntry | None:
        for lv in self.levels:
            if lv.id == level_id:
                return lv
        return None


def _read_xml(path: Path) -> ET.Element:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return ET.fromstring(text)


def load(root: str | Path) -> Campaign:
    root = Path(root)
    name, desc, lives = root.name, "", None
    info = root / "info.xml"
    if info.exists():
        el = _read_xml(info)
        name = (el.findtext("name") or name).strip()
        desc = (el.findtext("description") or "").strip()
        lt = el.findtext("lives")
        lives = int(lt) if lt and lt.strip().lstrip("-").isdigit() else None

    levels: list[LevelEntry] = []
    start = ""
    lx = root / "levels.xml"
    if lx.exists():
        el = _read_xml(lx)
        start = el.get("start", "")
        for act in [el] + list(el.iter("act")):
            act_name = act.get("name") if act.tag == "act" else None
            for lvl in act.findall("level"):
                levels.append(LevelEntry(lvl.get("id", ""), lvl.get("res", ""), lvl.get("name", ""), act_name))
    else:
        # No levels.xml: every XML under levels/ is a level, in name order.
        for i, f in enumerate(sorted((root / "levels").glob("*.xml"))):
            levels.append(LevelEntry(str(i), f"levels/{f.name}", f.stem, None))
        start = levels[0].id if levels else ""
    if not start and levels:
        start = levels[0].id
    return Campaign(root, name, desc, lives, start, levels)
