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
* ``GameEnd`` is an ``AnnounceText`` followed by ``ShowGameOver`` (credits).
* ``ShopArea`` is a ``UseTrigger`` on its area feeding an AE ``ShopArea``, the
  way Castle Hammerwatch's shops are wired. (AE's shop prefabs pick their
  stock from progression flags a converted scenario never sets.)
* ``RespawnPlayers`` has no AE world script; AE revives through its own rules.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import sval
from ..hw1.level import ScriptNode
from .model import Level, Param, Script, Unit

TILE = 16

# UseTrigger's shop icon, as on Castle Hammerwatch's shops.
SHOP_ICON = 4

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
        first.trigger_times = n.trigger_times
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
        feed = FEED_LINKS.get(last.cls)
        if feed:
            last.params.append(Param("ids", feed, [t for t, _ in links]))
            if any(d for _, d in links):
                ctx.warn(f"{last.cls} #{last.id}: AE fires {feed} without delays; HW1's link delays are dropped")
        else:
            last.connections.extend(links)
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
        event = p.value if p is not None and p.tag == "string" else _val(p, "event", "")
        if str(event) in ("LevelLoaded", "0"):
            return [Script("ScriptLink", sid, x, y, execute_on_start=True)]
        return None

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
        use.connections.append((shop.id, 0))
        return [use, shop]

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
