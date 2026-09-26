"""Learn HW1 → AE unit mappings from a campaign that exists in both games.

Castle Hammerwatch ships in HW1 as editor XML (``Hammerwatch/editor/campaign``)
and in AE as the remade ``scenarios/castle_hammerwatch``. The remake moved every
level by a fixed offset but kept the layout, so for each HW1 object we look at
the AE units near where it should land and count which AE type turns up, and
at what pixel delta. Walls and doodads agree almost perfectly; actors and
pickups much less, because AE re-rolls many spawns, so treat those as hints.

Per level, the global offset is the most common ``AE - HW1*16`` difference over
all nearby pairs. Horizontal wall runs dominate every level, so that peak
anchors each level the same way: HW1 ``h`` walls land with a zero delta.
"""

from __future__ import annotations

import collections
import json
import random
from dataclasses import dataclass
from pathlib import Path

from ..ae import level_reader as ae
from ..hw1 import level as hw1

TILE = 16
RADIUS = 40  # px searched around each HW1 object


@dataclass
class Candidate:
    ae_type: str
    dx: int
    dy: int
    hits: int


def _offset_votes(h: hw1.Level, a: ae.AELevel, rng: random.Random) -> list[tuple[int, int]]:
    objs = list(h.all_objects())
    if not objs or not a.units:
        return [(0, 0)]
    votes: collections.Counter[tuple[int, int]] = collections.Counter()
    sample = rng.sample(objs, min(400, len(objs)))
    for o in sample:
        ox, oy = o.x * TILE, o.y * TILE
        for u in a.units:
            votes[(round(u.x - ox), round(u.y - oy))] += 1
    return [v for v, _ in votes.most_common(40)]


def _score_offset(h: hw1.Level, a: ae.AELevel, gx: int, gy: int,
                  mapping: dict[str, tuple[str, int, int]]) -> int:
    """How many HW1 objects land exactly (±2 px) on the AE unit ``mapping``
    predicts for them, if the level is shifted by (gx, gy)."""
    have: dict[str, set[tuple[int, int]]] = collections.defaultdict(set)
    for u in a.units:
        have[u.type].add((round(u.x) // 4, round(u.y) // 4))
    score = 0
    for o in h.all_objects():
        m = mapping.get(o.type)
        if m is None:
            continue
        ut, dx, dy = m
        key = (round(o.x * TILE + gx + dx) // 4, round(o.y * TILE + gy + dy) // 4)
        if key in have.get(ut, ()):
            score += 1
    return score


def _strong(hits: dict[str, collections.Counter], totals: collections.Counter,
            min_count: int = 30, min_ratio: float = 0.2) -> dict[str, tuple[str, int, int]]:
    out = {}
    for t, n in totals.items():
        c = hits.get(t)
        if not c or n < min_count:
            continue
        (ut, dx, dy), k = c.most_common(1)[0]
        if k / n >= min_ratio:
            out[t] = (ut, dx, dy)
    return out


def pair_level(h: hw1.Level, a: ae.AELevel, gx: int, gy: int,
               into: dict[str, collections.Counter]) -> collections.Counter:
    grid: dict[tuple[int, int], list[ae.Unit]] = collections.defaultdict(list)
    for u in a.units:
        grid[(int(u.x // 64), int(u.y // 64))].append(u)
    counts: collections.Counter[str] = collections.Counter()
    for o in h.all_objects():
        counts[o.type] += 1
        px, py = o.x * TILE + gx, o.y * TILE + gy
        cx, cy = int(px // 64), int(py // 64)
        c = into.setdefault(o.type, collections.Counter())
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                for u in grid.get((cx + i, cy + j), ()):
                    dx, dy = u.x - px, u.y - py
                    if abs(dx) <= RADIUS and abs(dy) <= RADIUS:
                        c[(u.type, round(dx), round(dy))] += 1
    return counts


def learn(hw1_levels: Path, ae_levels: Path, log=print) -> dict:
    rng = random.Random(1)
    pairs = []
    for hf in sorted(hw1_levels.glob("*.xml")):
        af = ae_levels / (hf.stem + ".lvl")
        if af.exists():
            h, a = hw1.load(hf), ae.load(af)
            pairs.append((hf.stem, h, a, _offset_votes(h, a, rng)))

    # Pass 1: anchor each level on its raw vote peak and learn a rough mapping.
    # Pass 2+: re-anchor each level on the candidate offset that makes the most
    # objects land exactly on their mapped unit, then relearn.
    offsets = {name: votes[0] for name, _h, _a, votes in pairs}
    for _ in range(3):
        hits: dict[str, collections.Counter] = {}
        totals: collections.Counter[str] = collections.Counter()
        for name, h, a, _votes in pairs:
            gx, gy = offsets[name]
            totals.update(pair_level(h, a, gx, gy, hits))
        mapping = _strong(hits, totals)
        for name, h, a, votes in pairs:
            offsets[name] = max(votes, key=lambda g: _score_offset(h, a, g[0], g[1], mapping))

    levels_used = []
    for name, *_ in pairs:
        gx, gy = offsets[name]
        log(f"  {name}: offset ({gx}, {gy})")
        levels_used.append({"level": name, "offset": [gx, gy]})

    out: dict[str, dict] = {}
    for t, n in totals.most_common():
        c = hits.get(t, collections.Counter())
        # Collapse by AE type first to pick the type, then its best delta.
        by_type: collections.Counter[str] = collections.Counter()
        for (ut, _dx, _dy), k in c.items():
            by_type[ut] += k
        cands = []
        for ut, _ in by_type.most_common(4):
            deltas = collections.Counter({(dx, dy): k for (u2, dx, dy), k in c.items() if u2 == ut})
            (dx, dy), k = deltas.most_common(1)[0]
            cands.append({"ae": ut, "dx": dx, "dy": dy, "hits": k})
        out[t] = {"count": n, "candidates": cands}
    return {"levels": levels_used, "types": out}


def write(result: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1), encoding="utf-8")
