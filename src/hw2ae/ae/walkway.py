"""Walkways HW1 sized for its smaller player.

HW1's player collides as a circle of radius 3.5 (``actors/player/*_a.xml``:
``collision="3.5"``); AE's as 5.5 (``players/*/*.unit``). A piece whose two
collision rails leave a narrow walkway between them, the chambers bridge
planks (10-11 px apart), lets HW1's player through and stops AE's. Castle only
uses them as the collapsing bridge in the dragon fight, never walked on, but a
mission (Pirate Cove) lays them as planks to cross. The scenario gets a copy
of the AE piece with each rail moved back by the difference in radius, so the
walkway is as wide for AE's player as it was for HW1's.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

HW1_PLAYER_RADIUS = 3.5
AE_PLAYER_RADIUS = 5.5
# AE units that are a walkway between two collision rails.
WALKWAYS = re.compile(r"^doodads/walls/chambers/special_bridge_plank_p\d+\.unit$")

_POLYGON = re.compile(r"(<polygon\b[^>]*>)(.*?)(</polygon>)", re.S)
_POINT = re.compile(r"<point>([^<]*)</point>")


def _num(v: float) -> str:
    return str(int(v)) if v == int(v) else f"{v:g}"


def widen(unit_text: str, by: float = AE_PLAYER_RADIUS - HW1_PLAYER_RADIUS) -> str:
    """``unit_text`` with its two collision polygons moved ``by`` px apart
    each, across the walkway between them. Anything else is left as it is."""
    polys = list(_POLYGON.finditer(unit_text))
    if len(polys) != 2:
        return unit_text
    pts = [[tuple(float(v) for v in p.split()) for p in _POINT.findall(m.group(2))] for m in polys]
    if not all(pts):
        return unit_text
    centre = [(sum(x for x, _ in p) / len(p), sum(y for _, y in p) / len(p)) for p in pts]
    # The walkway runs between the rails: across it is the axis they differ most on.
    axis = 1 if abs(centre[0][1] - centre[1][1]) >= abs(centre[0][0] - centre[1][0]) else 0
    first = 0 if centre[0][axis] < centre[1][axis] else 1

    def moved(i: int, m: re.Match) -> str:
        d = -by if i == first else by

        def point(pm: re.Match) -> str:
            v = [float(s) for s in pm.group(1).split()]
            v[axis] += d
            return f"<point>{_num(v[0])} {_num(v[1])}</point>"

        return m.group(1) + _POINT.sub(point, m.group(2)) + m.group(3)

    out, pos = [], 0
    for i, m in enumerate(polys):
        out += [unit_text[pos:m.start()], moved(i, m)]
        pos = m.end()
    return "".join(out) + unit_text[pos:]


def walkway_unit(ae_path: str, ae_assets: Path, scenario_dir: Path, prefix: str) -> str | None:
    """The scenario's widened copy of an AE walkway piece (``prefix`` is
    ``hw1/<scenario id>``), or None when ``ae_path`` isn't one."""
    if not WALKWAYS.match(ae_path) or not (ae_assets / ae_path).is_file():
        return None
    rel = f"{prefix}/{ae_path}"
    dst = scenario_dir / rel
    if not dst.exists():
        text = (ae_assets / ae_path).read_text(encoding="utf-8")
        # "./x.png" means next to AE's piece; the copy lives elsewhere.
        text = text.replace('="./', f'="{PurePosixPath(ae_path).parent}/')
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(widen(text), encoding="utf-8", newline="\n")
    return rel
