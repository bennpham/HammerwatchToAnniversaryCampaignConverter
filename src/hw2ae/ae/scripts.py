"""HW1 script nodes → AE world scripts.

HW1 and AE both wire scripts as nodes with (target id, delay) connections, so
most nodes translate one to one. The differences handled here:

* HW1 shapes (``RectangleShape`` / ``CircleShape``) are AE physics units, not
  scripts; triggers point at them through their ``Areas`` array.
* ``LevelExitArea`` is two AE scripts: an ``AreaTrigger`` wired to a ``LevelExit``.
* ``GameEnd`` is an ``AnnounceText`` followed by ``ShowGameOver`` (credits).
* ``ShopArea`` is dropped when a vendor was converted into an AE shop prefab,
  because the prefab carries its own shop script.
* ``RespawnPlayers`` has no AE world script; AE revives through its own rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .. import sval
from ..hw1.level import ScriptNode
from .model import Level, Param, PrefabPlacement, Script, Unit

TILE = 16

SHOP_PREFABS = {
    "def": "prefabs/shop_defense.pfb",
    "off": "prefabs/shop_offense.pfb",
    "combo": "prefabs/shop_combo.pfb",
    "misc": "prefabs/shop_health.pfb",
    "power": "prefabs/shop_potion.pfb",
}

# HW1 AreaTrigger ``types`` bits -> AE AreaFilter bits.
_AREA_FILTER = {1: 2, 2: 4, 4: 8 | 16 | 32, 8: 64}


def _ids(params: sval.Node | None, name: str) -> list[int]:
    if params is None:
        return []
    d = params.get(name)
    if d is None:
        return []
    out: list[int] = []
    for c in d:
        if c.tag == "int-arr":
            out.extend(c.value)
        elif c.tag == "int":
            out.append(c.value)
    return out


def _val(params: sval.Node | None, name: str, default=None):
    if params is None:
        return default
    n = params.get(name)
    return default if n is None or n.children else n.value


def _start_id(v) -> str | None:
    """HW1 start id -> AE ``StartID``; ``None`` for HW1's default start (0).

    A new AE game spawns at the ``LevelStart`` whose ``StartID`` is empty, and
    AE's own levels leave the param out for that one. HW1's default is id 0."""
    s = str(v if v is not None else "").strip()
    return None if s in ("", "0") else s


@dataclass
class ScriptContext:
    level: Level
    id_map: dict[int, int]                      # HW1 id -> AE id (units and scripts)
    level_path_for: Callable[[str], str | None]  # HW1 level id -> AE .lvl path
    shop_categories_placed: set[str]            # category prefixes covered by shop prefabs
    warn: Callable[[str], None]
    dropped: set[int] = field(default_factory=set)


def convert_scripts(nodes: list[ScriptNode], ctx: ScriptContext) -> None:
    lv = ctx.level
    by_id = {n.id: n for n in nodes}

    # Pass 1: shapes become physics units; everything else reserves an AE id.
    for n in nodes:
        px, py = n.x * TILE, n.y * TILE
        if n.type == "RectangleShape":
            uid = lv.new_id()
            w = float(_val(n.params, "w", 1.0)) * TILE
            h = float(_val(n.params, "h", 1.0)) * TILE
            lv.units.append(Unit(":Physics_Rectangle", px, py, uid, _physics_state({"size": ("vec2", (w, h))})))
            ctx.id_map[n.id] = uid
        elif n.type == "CircleShape":
            uid = lv.new_id()
            r = float(_val(n.params, "radius", _val(n.params, "r", 1.0))) * TILE
            lv.units.append(Unit(":Physics_Circle", px, py, uid, _physics_state({"radius": ("float", r)})))
            ctx.id_map[n.id] = uid
        elif n.type == "RespawnPlayers":
            ctx.dropped.add(n.id)
        elif n.type == "ShopArea" and _shop_is_covered(n, ctx):
            ctx.dropped.add(n.id)
            for sid in _ids(n.params, "shape"):
                ctx.dropped.add(sid)
        else:
            ctx.id_map[n.id] = lv.new_id()

    # Shapes that only served a dropped ShopArea should go too.
    lv.units[:] = [u for u in lv.units
                   if not (u.path.startswith(":Physics") and
                           any(ctx.id_map.get(s) == u.id for s in ctx.dropped))]

    unsupported: dict[str, int] = {}
    for n in nodes:
        if n.id in ctx.dropped or n.type in ("RectangleShape", "CircleShape"):
            continue
        made = _convert_node(n, ctx)
        if made is None:
            unsupported[n.type] = unsupported.get(n.type, 0) + 1
            made = [Script("ScriptLink", ctx.id_map[n.id], n.x * TILE, n.y * TILE)]
        first = made[0]
        first.enabled = n.enabled
        first.trigger_times = n.trigger_times
        # The last script of an expansion carries the node's outgoing links.
        last = made[-1]
        for target, delay in zip(n.connections, n.delays):
            if target in ctx.dropped:
                continue
            t = ctx.id_map.get(target)
            if t is None or target not in by_id:
                continue
            last.connections.append((t, int(delay)))
        lv.scripts.extend(made)
    for t, k in sorted(unsupported.items()):
        ctx.warn(f"unsupported script node '{t}' x{k}; kept as a plain ScriptLink so its links still fire")


def _physics_state(extra: dict) -> dict:
    s = {
        "active": ("bool", True),
        "jam-through": ("bool", True),
        "sensor": ("bool", True),
        "shot-through": ("bool", True),
        "aim-through": ("bool", True),
    }
    s.update(extra)
    return s


def _shop_is_covered(n: ScriptNode, ctx: ScriptContext) -> bool:
    cats = str(_val(n.params, "cats", "") or "").split()
    prefixes = {c.rstrip("0123456789") for c in cats}
    if prefixes and prefixes <= ctx.shop_categories_placed:
        return True
    # No vendor in this level: place the matching shop prefab on the shop area.
    for p in prefixes:
        path = SHOP_PREFABS.get(p)
        if path:
            ctx.level.prefabs.append(PrefabPlacement(path, n.x * TILE, n.y * TILE))
            ctx.shop_categories_placed.add(p)
            return True
    return False


def _feed(ctx: ScriptContext, hw1_ids: list[int]) -> list[int]:
    return [ctx.id_map[i] for i in hw1_ids if i in ctx.id_map and i not in ctx.dropped]


def _convert_node(n: ScriptNode, ctx: ScriptContext) -> list[Script] | None:
    sid = ctx.id_map[n.id]
    x, y = n.x * TILE, n.y * TILE
    p = n.params
    t = n.type

    if t == "LevelStart":
        start_id = _start_id(_val(p, "id", 0))
        if start_id is None:
            return [Script("LevelStart", sid, x, y, label="default spawn")]
        return [Script("LevelStart", sid, x, y, params=[Param("string", "StartID", start_id)])]

    if t == "LevelExitArea":
        trig = Script("AreaTrigger", sid, x, y, params=[
            Param("int", "Event", 1),
            Param("ids", "Areas", _feed(ctx, _ids(p, "shape"))),
            Param("int", "Filter", 2),
        ])
        target = str(_val(p, "level", ""))
        lvl = ctx.level_path_for(target)
        if lvl is None:
            ctx.warn(f"level exit points at unknown level id '{target}'")
            lvl = ""
        exit_ = Script("LevelExit", ctx.level.new_id(), x + 16, y, label=lvl,
                       params=[Param("string", "Level", lvl)])
        start_id = _start_id(_val(p, "start id", 0))
        if start_id is not None:
            exit_.params.append(Param("string", "StartID", start_id))
        trig.connections.append((exit_.id, 0))
        return [trig, exit_]

    if t == "AreaTrigger":
        types = int(_val(p, "types", 1))
        flt = 0
        for bit, ae_bits in _AREA_FILTER.items():
            if types & bit:
                flt |= ae_bits
        return [Script("AreaTrigger", sid, x, y, params=[
            Param("int", "Event", int(_val(p, "event", 0)) + 1),
            Param("ids", "Areas", _feed(ctx, _ids(p, "shape"))),
            Param("int", "Filter", flt or 2),
        ])]

    if t == "AnnounceText":
        params = [Param("string", "Text", str(_val(p, "text", "")))]
        time = _val(p, "time")
        if time is not None:
            params.append(Param("int", "TimeOverride", int(time)))
        return [Script("AnnounceText", sid, x, y, label=str(_val(p, "text", "")), params=params)]

    if t == "ToggleElement":
        state = int(_val(p, "state", 1))
        return [Script("ToggleScripts", sid, x, y, params=[
            Param("int", "State", min(state, 2) + 1),
            Param("ids", "Scripts", _feed(ctx, _ids(p, "element"))),
        ])]

    if t == "ScriptLink":
        return [Script("ScriptLink", sid, x, y)]

    if t == "GlobalEventTrigger":
        if str(_val(p, "event", "")) in ("LevelLoaded", "0"):
            return [Script("ScriptLink", sid, x, y, execute_on_start=True)]
        return None

    if t == "ObjectEventTrigger":
        ev = str(_val(p, "event", ""))
        if ev in ("Destroyed", "0"):
            return [Script("UnitDestroyedTrigger", sid, x, y,
                           params=[Param("ids", "Units", _feed(ctx, _ids(p, "object")))])]
        return None

    if t == "GameEnd":
        text = str(_val(p, "text", ""))
        ann = Script("AnnounceText", sid, x, y, label=text, params=[Param("string", "Text", text)])
        end = Script("ShowGameOver", ctx.level.new_id(), x + 16, y, params=[
            Param("bool", "ShowCredits", True),
            Param("string", "Filename", ""),
        ])
        ann.connections.append((end.id, 3000))
        return [ann, end]

    if t == "ShopArea":
        # Only reached when no shop prefab could stand in for it.
        return [Script("ShopArea", sid, x, y, params=[
            Param("string", "Categories", str(_val(p, "cats", ""))),
        ])]

    return None
