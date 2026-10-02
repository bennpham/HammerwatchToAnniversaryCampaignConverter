"""HW1 script nodes → AE world scripts.

HW1 and AE both wire scripts as nodes with (target id, delay) connections, so
most nodes translate one to one. The differences handled here:

* HW1 shapes (``RectangleShape`` / ``CircleShape``) are AE physics units, not
  scripts; triggers point at them through their ``Areas`` array.
* ``LevelExitArea`` is two AE scripts: an ``AreaTrigger`` wired to a ``LevelExit``
  (just the ``LevelExit`` when it has no shape and only other scripts fire it).
* ``AllPlayersAreaTrigger`` fires through its ``OnAllEntered`` feed, not links.
* ``PlaySound`` takes an AE FMOD event, looked up in ``mapping/data/sounds.json``.
* ``PlayMusic`` sets AE's ``MusicMode`` (and ``AmbienceMode``) from the same file.
* ``DestroyObject`` is ``DestroyUnits``; ``ChangeDoodadState`` is ``SetUnitScene``.
* A boss's "Boss N%" / "Boss Died" global events become an ``ActorHealthTrigger``
  / ``UnitDestroyedTrigger`` on the boss, plus a ``CreateBossBar`` per kind of
  boss, as in AE's own dragon level.
* ``Variable`` / ``ChangeVariable`` / ``CheckVariable`` are AE's ``Variable`` /
  ``ChangeVariables`` / ``CheckVariables``: the generator counts boss deaths
  down to 0 with them to open a multi-boss room.
* ``SpawnObject``, ``TimerTrigger``, ``ToggleImmortality``, ``ProjectileSpewer``
  and ``DangerArea`` have AE scripts of the same purpose; ``Checkpoint`` is a
  ``LevelStart`` plus a ``SetRespawnPoint`` to it. The random dungeon generator
  packs single-value parameters (see ``_packed``).
* ``GameEnd`` is an ``AnnounceText`` followed by ``ShowGameOver`` (credits).
* ``ShopArea`` is a ``UseTrigger`` on its area feeding an AE ``ShopArea``, the
  way Castle Hammerwatch's shops are wired. (AE's shop prefabs pick their
  stock from progression flags a converted scenario never sets.)
* ``RespawnPlayers`` has no AE world script; AE revives through its own rules.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

from .. import sval
from ..hw1.level import ScriptNode
from .model import Level, Param, Script, Unit

TILE = 16

# UseTrigger's shop icon, as on Castle Hammerwatch's shops.
SHOP_ICON = 4
# WorldScript::ShopAreaType.Power
SHOP_TYPE_POWER = 1

# AE scripts that fire their targets through a named feed rather than links.
FEED_LINKS = {"AllPlayersAreaTrigger": "OnAllEntered"}

_SOUND_DATA = json.loads((Path(__file__).parent.parent / "mapping" / "data" / "sounds.json").read_text())
# HW1 sound (sound bank file:name) -> AE FMOD event, as AE's own levels use them.
SOUNDS: dict[str, str] = _SOUND_DATA["sounds"]
# HW1 music track (sound/music.xml:name) -> AE MusicMode / AmbienceMode values.
MUSIC: dict[str, int] = _SOUND_DATA["music"]
AMBIENCE: dict[str, int] = _SOUND_DATA["ambience"]
# Tracks AE plays as a one-shot stinger rather than a music mode.
MUSIC_STINGERS: dict[str, str] = _SOUND_DATA["music_stingers"]

# HW1 projectile -> AE projectile unit, for ProjectileSpewer.
PROJECTILES: dict[str, str] = {k: v for k, v in json.loads(
    (Path(__file__).parent.parent / "mapping" / "data" / "projectiles.json").read_text()).items()
    if not k.startswith("_")}
# HW1 spewer direction (0-3) -> AE degrees, from Crackshell's HW1 converter.
SPEWER_DIRECTIONS = {0: 270, 1: 90, 2: 180, 3: 0}
# FloatCompareFunc.Less, ActorHealthTrigger's default in AE's levels.
HEALTH_LESS = 2

# HW1 ChangeVariable ``mod`` -> AE ChangeFunc. 0 set (Temple of the Sun resets
# its counters to 0), 1 add (its worm spawns count up), 2 subtract (the
# generator's boss countdown, played through).
CHANGE_FUNCS = {0: 1, 1: 2, 2: 3}
# HW1 CheckVariable ``cmp-func`` -> AE CompareFunc. Only 0 (equals) is known:
# the generator's "all bosses dead" check, played through. Temple of the Sun's
# 2/3/4 cap its spawns but don't show whether they are strict or inclusive.
COMPARE_FUNCS = {0: 1}
# How long AE's Castle levels wait after a ChangeVariables before checking it.
CHECK_AFTER_CHANGE_MS = 50

# DangerArea's DamageFilter: Neutral 1 | Player 2 | Enemy 4 | Other 64 (its default).
DAMAGE_FILTER_ALL = 71

# HW1 AreaTrigger ``types`` bits -> AE AreaFilter bits.
_AREA_FILTER = {1: 2, 2: 4, 4: 8 | 16 | 32, 8: 64}


def _ids(params: sval.Node | None, name: str) -> list[int]:
    if params is None:
        return []
    d = params.get(name)
    if d is None:
        return []
    # The random dungeon generator writes the ids straight in, as
    # <int-arr name="static">; the HW1 editor nests them in a dictionary.
    if d.tag == "int-arr":
        return list(d.value)
    if d.tag == "int":
        return [d.value]
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
    warn: Callable[[str], None]
    dropped: set[int] = field(default_factory=set)
    # (AE id, boss bar title) of the level's boss actors, which fire HW1's "Boss N%" events.
    bosses: list[tuple[int, str]] = field(default_factory=list)
    # HW1 object type -> AE unit path, as placed objects resolve (SpawnObject).
    resolve_unit: Callable[[str], str | None] = lambda hw1_type: None
    # The level's HW1 script nodes by id, for nodes that read another's params.
    nodes: dict[int, ScriptNode] = field(default_factory=dict)

    @property
    def boss_ids(self) -> list[int]:
        return [uid for uid, _ in self.bosses]


def convert_scripts(nodes: list[ScriptNode], ctx: ScriptContext) -> None:
    lv = ctx.level
    by_id = {n.id: n for n in nodes}
    ctx.nodes = by_id

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
        else:
            ctx.id_map[n.id] = lv.new_id()

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
        # HW1's boss events fire once; AE's health trigger fires on every hit
        # below the threshold, so it is made one-shot as AE's own levels do.
        first.trigger_times = 1 if first.cls == "ActorHealthTrigger" else n.trigger_times
        # The last script of an expansion carries the node's outgoing links.
        last = made[-1]
        links: list[tuple[int, int]] = []
        for target, delay in zip(n.connections, n.delays):
            if target in ctx.dropped:
                continue
            t = ctx.id_map.get(target)
            if t is None or target not in by_id:
                continue
            links.append((t, int(delay)))
        links = _check_after_change(n, links, by_id, ctx)
        feed = FEED_LINKS.get(last.cls)
        if feed:
            last.params.append(Param("ids", feed, [t for t, _ in links]))
            if any(d for _, d in links):
                ctx.warn(f"{last.cls} #{last.id}: AE fires {feed} without delays; HW1's link delays are dropped")
        else:
            last.connections.extend(links)
        lv.scripts.extend(made)
    # AE draws a bar per actor, each titled with the Name it was created with,
    # so a room of mixed bosses gets one CreateBossBar per kind of boss.
    by_name: dict[str, list[int]] = {}
    for uid, name in ctx.bosses:
        by_name.setdefault(name, []).append(uid)
    for name, ids in by_name.items():
        lv.scripts.append(Script("CreateBossBar", lv.new_id(), 0, 0, execute_on_start=True, params=[
            Param("ids", "Actors", ids),
            Param("string", "Name", name),
        ]))
    for t, k in sorted(unsupported.items()):
        ctx.warn(f"unsupported script node '{t}' x{k}; kept as a plain ScriptLink so its links still fire")
    respawns = sum(1 for n in nodes if n.type == "RespawnPlayers")
    if respawns:
        ctx.warn(f"RespawnPlayers x{respawns} left out: AE has no world script that revives players "
                 "(they come back through AE's lives)")


def _check_after_change(n: ScriptNode, links: list[tuple[int, int]], by_id: dict[int, ScriptNode],
                        ctx: ScriptContext) -> list[tuple[int, int]]:
    """A HW1 node that fires a ChangeVariable and a CheckVariable together
    (the generator's boss countdown) relies on the change landing first. AE's
    own levels never fire both at once: the check is chained from the change or
    runs 50 ms after it (Castle level_2), so do the same."""
    aes = {ctx.id_map.get(i): by_id[i].type for i in n.connections if i in by_id}
    changes = [d for t, d in links if aes.get(t) == "ChangeVariable"]
    if not changes:
        return links
    after = max(changes) + CHECK_AFTER_CHANGE_MS
    return [(t, max(d, after)) if aes.get(t) == "CheckVariable" else (t, d) for t, d in links]


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
        target = str(_val(p, "level", ""))
        lvl = ctx.level_path_for(target)
        if lvl is None:
            ctx.warn(f"level exit points at unknown level id '{target}'")
            lvl = ""
        areas = _feed(ctx, _ids(p, "shape"))
        # Without a shape the exit is only ever fired by other scripts (a
        # teleporter pad, say), so it is a lone LevelExit they can execute.
        exit_ = Script("LevelExit", ctx.level.new_id() if areas else sid, x + 16, y, label=lvl,
                       params=[Param("string", "Level", lvl)])
        start_id = _start_id(_val(p, "start id", 0))
        if start_id is not None:
            exit_.params.append(Param("string", "StartID", start_id))
        if not areas:
            return [exit_]
        trig = Script("AreaTrigger", sid, x, y, params=[
            Param("int", "Event", 1),
            Param("ids", "Areas", areas),
            Param("int", "Filter", 2),
        ])
        trig.connections.append((exit_.id, 0))
        return [trig, exit_]

    if t == "AllPlayersAreaTrigger":
        # Fires when every (living) player stands in the area. Its links go in
        # OnAllEntered, filled by convert_scripts (FEED_LINKS).
        return [Script("AllPlayersAreaTrigger", sid, x, y, params=[
            Param("ids", "Areas", _feed(ctx, _ids(p, "shape"))),
        ])]

    if t == "PlaySound":
        hw1_sound = str(_val(p, "sound", ""))
        event = SOUNDS.get(hw1_sound)
        if event is None:
            ctx.warn(f"no AE sound for HW1 '{hw1_sound}' yet (add it to mapping/data/sounds.json); "
                     "its PlaySound is kept as a plain ScriptLink")
            return [Script("ScriptLink", sid, x, y)]
        params = [Param("string", "Sound", event)]
        if _val(p, "loop", False):
            params.append(Param("bool", "Looping", True))
        return [Script("PlaySound", sid, x, y, label=event.rsplit("/", 1)[-1], params=params)]

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
        # The HW1 editor writes {event: ...}; the random dungeon generator
        # writes the event name as the whole parameters value.
        event = str(_packed(p) if _packed(p) is not None else _val(p, "event", ""))
        if event in ("LevelLoaded", "0"):
            return [Script("ScriptLink", sid, x, y, execute_on_start=True)]
        return _boss_event(event, ctx, sid, x, y)

    if t == "SpawnObject":
        hw1_type = str(_packed(p) if _packed(p) is not None else _val(p, "type", ""))
        unit = ctx.resolve_unit(hw1_type)
        if unit is None:
            ctx.warn(f"SpawnObject of '{hw1_type}', which has no AE unit yet; kept as a plain ScriptLink")
            return [Script("ScriptLink", sid, x, y)]
        return [Script("SpawnUnit", sid, x, y, label=unit.rsplit("/", 1)[-1],
                       params=[Param("string", "UnitType", unit)])]

    if t == "TimerTrigger":
        freq = _packed(p) if _packed(p) is not None else _val(p, "freq", 1000)
        return [Script("TimerTrigger", sid, x, y, params=[Param("int", "Frequency", int(freq))])]

    if t == "ToggleImmortality":
        # HW1 0/1/2 = make immortal / mortal / toggle (the generator's dragon
        # is immortal through each 30-s wave countdown) -> AE Enable/Disable/Toggle.
        state = int(_val(p, "state", 0))
        return [Script("ToggleImmortality", sid, x, y, params=[
            Param("int", "State", min(state, 2) + 1),
            Param("ids", "Units", _feed(ctx, _ids(p, "element"))),
        ])]

    if t == "ProjectileSpewer":
        hw1_proj = str(_val(p, "projectile", ""))
        proj = PROJECTILES.get(hw1_proj)
        if proj is None:
            ctx.warn(f"no AE projectile for HW1 '{hw1_proj}' yet (add it to mapping/data/projectiles.json); "
                     "its ProjectileSpewer is kept as a plain ScriptLink")
            return [Script("ScriptLink", sid, x, y)]
        return [Script("ProjectileSpewer", sid, x, y, label=hw1_proj.rsplit("/", 1)[-1], params=[
            Param("string", "Projectile", proj),
            Param("int", "Direction", SPEWER_DIRECTIONS.get(int(_val(p, "direction", 0)), 0)),
            Param("float", "Spread", float(_val(p, "spread", 0.0))),
            Param("int", "Frequency", int(_val(p, "spawn-rate", 1000))),
        ])]

    if t == "DangerArea":
        shapes = _ids(p, "shape")
        params = [
            Param("ids", "Areas", _feed(ctx, shapes)),
            Param("int", "Damage", int(_val(p, "damage", 0))),
            Param("int", "Frequency", int(_val(p, "freq", 500))),
        ]
        # HW1 picks who a zone affects on its shape ("types"); AE on the zone.
        flt = _damage_filter(ctx, shapes)
        if flt is not None:
            params.append(Param("int", "Filter", flt))
        buff = str(_val(p, "buff", "") or "")
        if buff:
            # HW1 buffs/<name>.xml -> AE's buff table entry of the same name.
            params.append(Param("string", "Buff", f"actors/buffs.sval:{PurePosixPath(buff).stem}"))
        return [Script("DangerArea", sid, x, y, params=params)]

    if t == "Checkpoint":
        # AE respawns players at the LevelStart named by the current start id.
        spawn_id = f"hw1_checkpoint_{n.id}"
        ctx.level.scripts.append(Script("LevelStart", ctx.level.new_id(), x, y, label=spawn_id,
                                        params=[Param("string", "StartID", spawn_id)]))
        return [Script("SetRespawnPoint", sid, x, y, label=spawn_id,
                       params=[Param("string", "SpawnId", spawn_id)])]

    if t == "DestroyObject":
        units = _feed(ctx, _ids(p, "static") + _ids(p, "object"))
        return [Script("DestroyUnits", sid, x, y, params=[Param("ids", "Units", units)])]

    if t == "ChangeDoodadState":
        # Ported HW1 doodads keep HW1's state names as their scene names.
        state = str(_val(p, "state", ""))
        return [Script("SetUnitScene", sid, x, y, label=state, params=[
            Param("ids", "Units", _feed(ctx, _ids(p, "object"))),
            Param("string", "State", state),
        ])]

    if t == "PlayMusic":
        return _play_music(n, ctx, sid, x, y)

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
        cats = str(_val(p, "cats", ""))
        use = Script("UseTrigger", sid, x, y, label="Shop", params=[
            Param("int", "Icon", SHOP_ICON),
            Param("ids", "Areas", _feed(ctx, _ids(p, "shape"))),
        ])
        shop = Script("ShopArea", ctx.level.new_id(), x + 16, y, label=cats, params=[
            Param("string", "Categories", cats),
            Param("target", "#PlayerTarget", (sid, "User")),
        ])
        if "power" in cats.split():
            # The potion vendor: AE's power shop is its own menu (ShopAreaType
            # Power), as in prefabs/shop_potion.pfb; the default skill shop
            # has nothing under "power".
            shop.params.append(Param("int", "Type", SHOP_TYPE_POWER))
        use.connections.append((shop.id, 0))
        return [use, shop]

    if t == "Variable":
        # Both the HW1 editor and the generator pack the start value.
        value = _packed(p) if _packed(p) is not None else _val(p, "value", 0)
        return [Script("Variable", sid, x, y, label=str(value), params=[Param("int", "Value", int(value))])]

    if t == "ChangeVariable":
        mod = int(_val(p, "mod", 0))
        func = CHANGE_FUNCS.get(mod)
        if func is None:
            ctx.warn(f"ChangeVariable mod {mod} has no known AE function; kept as a plain ScriptLink")
            return [Script("ScriptLink", sid, x, y)]
        return [Script("ChangeVariables", sid, x, y, params=[
            Param("int", "Function", func),
            Param("int", "Value", int(_val(p, "value", 0))),
            Param("ids", "Variables", _feed(ctx, _ids(p, "vars"))),
        ])]

    if t == "CheckVariable":
        cmp = int(_val(p, "cmp-func", 0))
        func = COMPARE_FUNCS.get(cmp)
        if func is None:
            ctx.warn(f"CheckVariable cmp-func {cmp} has no known AE comparison; kept as a plain ScriptLink")
            return [Script("ScriptLink", sid, x, y)]
        # HW1 fires on-true / on-false instead of its links; AE does the same.
        return [Script("CheckVariables", sid, x, y, params=[
            Param("int", "Function", func),
            Param("int", "Value", int(_val(p, "cmp-val", 0))),
            Param("ids", "Variable", _feed(ctx, _ids(p, "vars"))[:1]),
            Param("ids", "OnTrue", _feed(ctx, _ids(p, "on-true"))),
            Param("ids", "OnFalse", _feed(ctx, _ids(p, "on-false"))),
        ])]

    return None


def _damage_filter(ctx: ScriptContext, shape_ids: list[int]) -> int | None:
    """AE DamageFilter for a zone whose HW1 shapes list ``types``; ``None``
    keeps AE's default (everything)."""
    types = 0
    for i in shape_ids:
        shape = ctx.nodes.get(i)
        if shape is None or _val(shape.params, "types") is None:
            return None  # a shape without types affects everything in HW1
        types |= int(_val(shape.params, "types"))
    flt = 0
    for bit, ae_bits in _AREA_FILTER.items():
        if types & bit:
            flt |= ae_bits
    flt &= DAMAGE_FILTER_ALL
    return flt if 0 < flt and flt != DAMAGE_FILTER_ALL & ~1 else None


def _packed(params: sval.Node | None):
    """The random dungeon generator writes single-value parameters packed:
    ``<string name="parameters">actors/bat_1.xml</string>``. Their value, or
    ``None`` for the usual parameters dictionary."""
    if params is None or params.tag in ("dictionary", "dict"):
        return None
    return params.value


_BOSS_EVENT = re.compile(r"^Boss (\d+)%$")


def _boss_event(event: str, ctx: ScriptContext, sid: int, x: float, y: float) -> list[Script] | None:
    """HW1 bosses broadcast "Boss 75%" ... "Boss Died". AE watches the boss
    itself, as its own dragon level does: an ActorHealthTrigger per threshold
    (health as a fraction) and a UnitDestroyedTrigger for the death."""
    if not ctx.boss_ids:
        return None
    m = _BOSS_EVENT.match(event)
    if m:
        return [Script("ActorHealthTrigger", sid, x, y, label=event, params=[
            Param("ids", "Units", list(ctx.boss_ids)),
            Param("int", "Function", HEALTH_LESS),
            Param("float", "Value", int(m.group(1)) / 100),
        ])]
    if event == "Boss Died":
        return [Script("UnitDestroyedTrigger", sid, x, y, label=event,
                       params=[Param("ids", "Units", list(ctx.boss_ids))])]
    return None


def _play_music(n: ScriptNode, ctx: ScriptContext, sid: int, x: float, y: float) -> list[Script]:
    """AE plays one music event and picks the track with the ``MusicMode``
    parameter (the main menu leaves it on Title). A HW1 act track becomes that
    mode plus the act's ambience, as AE's region table pairs them."""
    track = str(_val(n.params, "sound", ""))
    stinger = MUSIC_STINGERS.get(track)
    if stinger is not None:
        return [Script("PlaySound", sid, x, y, label=stinger.rsplit("/", 1)[-1],
                       params=[Param("string", "Sound", stinger)])]
    mode = MUSIC.get(track)
    if mode is None:
        ctx.warn(f"no AE music for HW1 '{track}' yet (add it to mapping/data/sounds.json); "
                 "its PlayMusic is kept as a plain ScriptLink")
        return [Script("ScriptLink", sid, x, y)]
    label = track.rsplit(":", 1)[-1]
    music = Script("PlayMusic", sid, x, y, label=label, params=[Param("int", "Music", mode)])
    ambience = AMBIENCE.get(track)
    if ambience is None:
        return [music]
    amb = Script("PlayMusic", ctx.level.new_id(), x + 16, y, label=f"{label} ambience", params=[
        Param("int", "Music", ambience),
        Param("bool", "Ambience", True),
    ])
    music.connections.append((amb.id, 0))
    return [music, amb]
