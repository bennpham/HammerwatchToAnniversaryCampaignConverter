from __future__ import annotations

import re
from pathlib import Path

import pytest

from hw2ae import sval
from hw2ae.ae import doors, level_reader, level_writer, tiles
from hw2ae.ae.model import Level, Param, Script, Unit
from hw2ae.ae.scripts import ScriptContext, convert_scripts
from hw2ae.config import find_ae_assets, find_ae_root
from hw2ae.convert import Options, convert
from hw2ae.hw1 import campaign, level as hw1_level
from hw2ae.mapping.resolver import Dropped, Placement, Resolver

FIX = Path(__file__).parent / "fixtures" / "mission"


class AllExist:
    """Stands in for the AE asset index: every path exists."""

    def exists(self, path: str) -> bool:
        return True


# -- SVAL ------------------------------------------------------------------

def test_sval_roundtrip():
    w = sval.Writer()
    w.open("dict")
    w.string("a & <b>", "s")
    w.int(-3, "i")
    w.vec2(1.5, -2, "v")
    w.open("array", "arr")
    w.int(7)
    w.bool(True)
    w.close("array")
    w.close("dict")
    root = sval.parse_text(w.text())
    assert root["s"].value == "a & <b>"
    assert root["i"].value == -3
    assert root["v"].value == (1.5, -2.0)
    assert [c.value for c in root["arr"]] == [7, True]


# -- tile RLE --------------------------------------------------------------

def test_rle_known_samples():
    # A full 16-px cell (32x32) and an empty one, as AE's own levels store them.
    assert tiles.decode_rle("7e7e7e7e7e7e7e7e10", 32) == [True] * 1024
    assert tiles.decode_rle("8282828282828282f0", 32) == [False] * 1024
    assert tiles.encode_rle([True] * 1024) == "7e7e7e7e7e7e7e7e10"
    assert tiles.encode_rle([False] * 1024) == "8282828282828282f0"


def test_rle_vertical_line_from_editor():
    # Painted in the AE editor: a one-tile-wide vertical line, 6 tiles long.
    grid = tiles.decode_rle("828282da01e101e101e101e101e101828282bb", 32)
    painted = [i for i, v in enumerate(grid) if v]
    assert len(painted) == 6
    assert {i % 32 for i in painted} == {painted[0] % 32}
    assert [i // 32 for i in painted] == list(range(painted[0] // 32, painted[0] // 32 + 6))


def test_rle_roundtrip_random():
    import random
    rng = random.Random(3)
    for side in (16, 32, 128):
        grid = [rng.random() < 0.3 for _ in range(side * side)]
        assert tiles.decode_rle(tiles.encode_rle(grid), side) == grid


def test_cells_are_centred_on_the_512_grid():
    # AE centres cells on multiples of 512 (cell 0 covers -256..256) and
    # floors any other pos onto that grid, so an off-grid cell would shift.
    tl = tiles.TileLayers(Path("does-not-exist"))
    tl.paint16("tilesets/x.tileset", {(0, 0), (15, 15), (16, 0), (-17, -1)})
    cells = tl.cells()
    assert set(cells) == {(0, 0), (512, 0), (-512, 0)}
    rects = set()
    for pos, datasets in cells.items():
        assert pos[0] % 512 == 0 and pos[1] % 512 == 0
        for ts, rle in datasets:
            rects |= set(tiles.decode_cell(pos, ts, rle, 16))
    assert rects == {(0, 0, 16), (240, 240, 16), (256, 0, 16), (-272, -16, 16)}


def test_bottom_layer_grows_past_the_floor(monkeypatch):
    from hw2ae.convert import BOTTOM_LAYER_GROW
    tl = tiles.TileLayers(Path("does-not-exist"))
    tl.paint16("tilesets/fine.tileset", {(0, 0)}, grow=BOTTOM_LAYER_GROW)
    # 1 tile left and up, 2 right and down: HW1 floors stop 2 rows short of
    # bottom walls, and AE's right-hand wall art sits further out.
    assert tl.layers["tilesets/fine.tileset"] == {(x, y) for x in (-1, 0, 1, 2) for y in (-1, 0, 1, 2)}

    # A 32-px layer keeps a coarse tile only when half of it is floor, which
    # drops a lone 16-px tile; grown, it takes every coarse tile it touches.
    monkeypatch.setattr(tiles.TileLayers, "size", lambda self, ts: 32)
    coarse = tiles.TileLayers(Path("does-not-exist"))
    coarse.paint16("tilesets/coarse.tileset", {(1, 1)})
    assert coarse.layers["tilesets/coarse.tileset"] == set()
    coarse.paint16("tilesets/grown.tileset", {(1, 1)}, grow=(1, 1, 1, 1))
    assert coarse.layers["tilesets/grown.tileset"] == {(0, 0), (1, 0), (0, 1), (1, 1)}


# -- HW1 reading -----------------------------------------------------------

def test_reads_generator_dialect():
    lv = hw1_level.load(FIX / "levels" / "level0.xml")
    assert [o.type for o in lv.doodads][0] == "doodads/theme_a/a_x_t_l.xml"
    assert (lv.doodads[0].x, lv.doodads[0].y) == (3.0, 4.0)
    assert len(lv.actors) == 1 and len(lv.items) == 1
    assert {s.type for s in lv.scripts} == {"LevelStart", "RectangleShape", "LevelExitArea", "ShopArea"}
    assert lv.ambient == (255, 255, 255, 255)
    layer = lv.layers["tilemaps/a_default.xml"]
    assert len(layer.tiles) == 2
    # cell i of a block at (X, Y) sits at (X - 10 + i % 20, Y - 10 + i // 20)
    assert all(-10 <= x < 10 and -10 <= y < 10 for x, y in layer.tiles)


def test_reads_editor_dialect():
    lv = hw1_level.load(FIX / "levels" / "level1.xml")
    assert (lv.doodads[0].x, lv.doodads[0].y) == (12.0, -3.0)
    assert lv.actors[0].type == "actors/tick_1_small.xml" and lv.actors[0].id == 2
    assert lv.items[0].type == "items/some_unknown_item.xml"
    trig = next(s for s in lv.scripts if s.type == "ObjectEventTrigger")
    assert trig.connections == [6] and trig.delays == [250]
    assert lv.layers["tilemaps/c_default.xml"].tiles == {(10, -10): 3}
    assert lv.ambient == (90, 90, 90, 255)


def test_campaign():
    c = campaign.load(FIX)
    assert c.name == "Test Dungeon" and c.start == "0"
    assert [lv.res for lv in c.levels] == ["levels/level0.xml", "levels/level1.xml"]


# -- resolver --------------------------------------------------------------

def test_wall_mapping_and_offsets():
    r = Resolver(AllExist())  # type: ignore[arg-type]
    p = r.resolve("doodads/theme_a/a_x_t_l.xml")
    assert isinstance(p, Placement) and (p.path, p.dx, p.dy) == ("doodads/walls/prison/x_w.unit", 0, 16)
    p = r.resolve("doodads/theme_c/c_h_16.xml")
    assert p.path == "doodads/walls/archives/h_32.unit"  # AllExist picks the first candidate
    p = r.resolve("doodads/special/color_theme_d_64.xml")
    assert (p.path, p.dy) == ("doodads/walls/chambers/__color_64.unit", 64)
    p = r.resolve("items/door_a_silver_h_v2.xml")
    assert p.path == "doodads/doors/door_h_mid_silver_prison.unit"
    assert isinstance(r.resolve("doodads/generic/marker_exit.xml"), Dropped)
    assert r.resolve("items/some_unknown_item.xml") is None
    torch = r.resolve("doodads/generic/lamp_torch_off.xml")
    assert torch.params == {"start": "n-off"}


# -- scripts ---------------------------------------------------------------

def test_scripts_exit_shop_and_game_end():
    lv0 = hw1_level.load(FIX / "levels" / "level0.xml")
    out = Level("DungeonGameMode", None)
    ctx = ScriptContext(out, {}, lambda i: f"levels/test/level{i}.lvl", warn=lambda m: None)
    convert_scripts(lv0.scripts, ctx)
    classes = [s.cls for s in out.scripts]
    assert classes.count("LevelStart") == 1
    # A shop is a UseTrigger on the HW1 area feeding a ShopArea, as in Castle Hammerwatch.
    use = next(s for s in out.scripts if s.cls == "UseTrigger")
    shop = next(s for s in out.scripts if s.cls == "ShopArea")
    assert use.connections == [(shop.id, 0)]
    assert Param("string", "Categories", "def1 def2 def3 def4 def5") in shop.params
    assert Param("target", "#PlayerTarget", (use.id, "User")) in shop.params
    trig = next(s for s in out.scripts if s.cls == "AreaTrigger")
    exit_ = next(s for s in out.scripts if s.cls == "LevelExit")
    assert trig.connections == [(exit_.id, 0)]
    assert Param("string", "Level", "levels/test/level1.lvl") in exit_.params
    # HW1 start id 0 is AE's default spawn: no StartID at all, on both ends.
    start = next(s for s in out.scripts if s.cls == "LevelStart")
    assert start.params == [] and start.label == "default spawn"
    assert not any(p.name == "StartID" for p in exit_.params)
    rects = [u for u in out.units if u.path == ":Physics_Rectangle"]
    exit_rect = next(r for r in rects if r.state["size"] == ("vec2", (32.0, 16.0)))
    areas = next(p for p in trig.params if p.name == "Areas")
    assert areas.value == [exit_rect.id]

    lv1 = hw1_level.load(FIX / "levels" / "level1.xml")
    out1 = Level("DungeonGameMode", None)
    ctx1 = ScriptContext(out1, {2: 99}, lambda i: None, warn=lambda m: None)
    convert_scripts(lv1.scripts, ctx1)
    ud = next(s for s in out1.scripts if s.cls == "UnitDestroyedTrigger")
    assert ud.trigger_times == 1 and next(p for p in ud.params if p.name == "Units").value == [99]
    ann = next(s for s in out1.scripts if s.cls == "AnnounceText")
    end = next(s for s in out1.scripts if s.cls == "ShowGameOver")
    assert ud.connections == [(ann.id, 250)] and ann.connections == [(end.id, 3000)]


def test_teleporter_pad_fires_sound_and_exit():
    # dungeon1500735902's lobby: everyone on the pad -> sound + exit to level 0.
    lv = hw1_level.load(Path(__file__).parent / "fixtures" / "teleporter.xml")
    out = Level("DungeonGameMode", None)
    ctx = ScriptContext(out, {}, lambda i: f"levels/test/level{i}.lvl", warn=lambda m: None)
    convert_scripts(lv.scripts, ctx)
    by_cls = {s.cls: s for s in out.scripts}
    assert set(by_cls) == {"AllPlayersAreaTrigger", "LevelExit", "PlaySound"}  # no AreaTrigger for a shapeless exit
    pad, exit_, sound = by_cls["AllPlayersAreaTrigger"], by_cls["LevelExit"], by_cls["PlaySound"]
    rect, = [u for u in out.units if u.path == ":Physics_Rectangle"]
    assert Param("ids", "Areas", [rect.id]) in pad.params
    # AE fires these through OnAllEntered, not ordinary links.
    assert Param("ids", "OnAllEntered", [sound.id, exit_.id]) in pad.params and pad.connections == []
    assert Param("string", "Sound", "event:/SFX/Effects/other/teleport") in sound.params
    assert Param("string", "Level", "levels/test/level0.lvl") in exit_.params


def test_floor_button_and_level_music():
    # dungeon1500735902 level 7, in the random dungeon generator's dialect.
    lv = hw1_level.load(Path(__file__).parent / "fixtures" / "button_and_music.xml")
    out = Level("DungeonGameMode", None)
    walls = {67: 501, 68: 502, 69: 503}
    ctx = ScriptContext(out, {**walls, 74: 510}, lambda i: None, warn=lambda m: None)
    convert_scripts(lv.scripts, ctx)
    by_cls: dict[str, list] = {}
    for s in out.scripts:
        by_cls.setdefault(s.cls, []).append(s)

    # <string name="parameters">LevelLoaded</string> starts the level's music.
    start, = by_cls["ScriptLink"]
    music, ambience = by_cls["PlayMusic"]
    # Its <int-arr name="delays">3521</int-arr> is the target's id, not a time.
    assert start.execute_on_start and start.connections == [(music.id, 0)]
    # HW1's Castle plays act4 in the archives (levels 7-9): AE's archives region.
    assert Param("int", "Music", 5) in music.params
    assert Param("int", "Music", 3) in ambience.params
    assert Param("bool", "Ambience", True) in ambience.params and music.connections == [(ambience.id, 0)]

    # The button destroys the seal wall (ids in a bare int-arr) and shows as pressed.
    trig, = by_cls["AreaTrigger"]
    destroy, = by_cls["DestroyUnits"]
    scene, = by_cls["SetUnitScene"]
    assert Param("ids", "Units", [501, 502, 503]) in destroy.params
    assert Param("ids", "Units", [510]) in scene.params and Param("string", "State", "pressed") in scene.params
    assert {t for t, _ in trig.connections} == {destroy.id, scene.id}


def test_minibosses_are_mapped():
    r = Resolver(AllExist())  # type: ignore[arg-type]
    assert r.resolve("actors/tick_1_mb.xml").path == "actors/beasts/ticks/tick_giant.unit"
    assert r.resolve("actors/lich_1_mb.xml").path == "actors/undead/skeletons/hammerwatch/skeleton_wizard.unit"


def test_dragon_fight_events():
    # dungeon365496787's boss level, in the random dungeon generator's dialect.
    from hw2ae.convert import Report, convert_level
    lv = hw1_level.load(Path(__file__).parent / "fixtures" / "dragon.xml")
    r = Resolver(AllExist())  # type: ignore[arg-type]
    report = Report()
    out = convert_level(lv, r, Path("does-not-exist"), report, lambda i: None)
    dragon, = [u for u in out.units if u.path == "actors/bosses/boss_dragon/boss_dragon.unit"]
    assert (dragon.x, dragon.y) == (22 * 16, 3 * 16 - 16)
    by = {}
    for s in out.scripts:
        by.setdefault(s.cls, []).append(s)

    # "Boss 75%" -> a one-shot health trigger on the dragon, as AE's dragon level does.
    hp, = by["ActorHealthTrigger"]
    assert hp.trigger_times == 1
    assert Param("ids", "Units", [dragon.id]) in hp.params and Param("float", "Value", 0.75) in hp.params
    imm, = by["ToggleImmortality"]
    assert Param("int", "State", 1) in imm.params            # state 0: immortal through the countdown
    assert Param("ids", "Units", [dragon.id]) in imm.params
    timer, = by["TimerTrigger"]
    spawn, = by["SpawnUnit"]
    assert {t for t, _ in hp.connections} == {imm.id, timer.id}
    assert Param("int", "Frequency", 1500) in timer.params and not timer.enabled
    assert Param("string", "UnitType", "actors/beasts/bats/bat_black.unit") in spawn.params
    assert spawn.trigger_times == 6

    spewer, = by["ProjectileSpewer"]
    assert Param("string", "Projectile", "doodads/generic/trap_shooter_arrow_projectile_normal.unit") in spewer.params
    assert Param("int", "Direction", 90) in spewer.params and Param("int", "Frequency", 1000) in spewer.params
    danger, = by["DangerArea"]
    assert Param("string", "Buff", "actors/buffs.sval:bloodlust") in danger.params
    # The bloodlust zone's shape has types 2: enemies only, not the players.
    assert Param("int", "Filter", 4) in danger.params

    # Checkpoint: a LevelStart to respawn at, set by SetRespawnPoint.
    respawn, = by["SetRespawnPoint"]
    starts = [s for s in by["LevelStart"] if s.params]
    assert Param("string", "SpawnId", "hw1_checkpoint_816") in respawn.params
    assert [Param("string", "StartID", "hw1_checkpoint_816")] == starts[0].params

    # "Boss Died" opens the exit walls; the boss bar names the dragon.
    died, = by["UnitDestroyedTrigger"]
    destroy, = by["DestroyUnits"]
    assert Param("ids", "Units", [dragon.id]) in died.params and died.connections[0][0] == destroy.id
    bar, = by["CreateBossBar"]
    assert bar.execute_on_start and Param("ids", "Actors", [dragon.id]) in bar.params
    assert Param("string", "Name", ".ig.boss4") in bar.params


def test_bosses_and_upgrades_are_mapped():
    r = Resolver(AllExist())  # type: ignore[arg-type]
    for boss in ("knight", "lich", "krilith", "worm", "anubis"):
        assert r.resolve(f"actors/boss_{boss}/boss_{boss}.xml").path == f"actors/bosses/boss_{boss}/boss_{boss}.unit"
    for up in ("damage", "defense", "damage_2", "defense_2", "health_2", "mana_2"):
        assert r.resolve(f"items/upgrade_{up}.xml").path == f"items/pickups/upgrade_{up}.unit"
    assert r.resolve("actors/boss_queen/boss_queen.xml").path == "actors/bosses/boss_queen/boss_queen.unit"
    # By heal value, not name: HW1 health_3 heals 75 like AE's health_4, health_4 50 like AE's health_3.
    assert r.resolve("items/health_3.xml").path == "items/pickups/health_4.unit"
    assert r.resolve("items/health_4.xml").path == "items/pickups/health_3.unit"


def test_bonus_door_gets_its_key_controller():
    from hw2ae.convert import BOSS_BAR_NAMES, Report, convert_level
    r = Resolver(AllExist())  # type: ignore[arg-type]
    door = r.resolve("items/bonus_door_h_32.xml")
    assert door.path == "doodads/doors/door_h_32_bonus.unit" and door.key == "key_bonus"
    assert r.resolve("items/bonus_key.xml").path == "items/pickups/key_bonus.unit"
    assert BOSS_BAR_NAMES["actors/bosses/boss_queen/boss_queen.unit"] == ".ig.boss1"

    lv = hw1_level.Level(path=Path("l.xml"), layers={}, doodads=[], actors=[], scripts=[], prefabs=[], lights=[],
                         ambient=None, items=[hw1_level.Obj(1, "items/bonus_door_h_32.xml", 2, 3)])
    out = convert_level(lv, r, Path("."), Report(), lambda i: None)
    unit, = out.units
    ctrl, = (s for s in out.scripts if s.cls == "DoorController")
    assert Param("ids", "Doors", [unit.id]) in ctrl.params and Param("string", "Collectable", "key_bonus") in ctrl.params


def test_temple_of_the_sun_themes_and_enemies():
    r = Resolver(AllExist())  # type: ignore[arg-type]
    # Cave runs are AE's h/v; the pyramid keeps the castle's h_32; desert doors for every Temple theme.
    e = r.resolve("doodads/theme_e/e_h_16.xml")
    assert (e.path, e.dx, e.dy) == ("doodads/walls/cave_desert/h.unit", 0, 0)
    assert r.resolve("doodads/theme_g/g_h_16.xml").path == "doodads/walls/pyramid_inside/h_32.unit"
    i = r.resolve("doodads/theme_i/i_crn_l_up.xml")
    assert (i.path, i.dy) == ("doodads/walls/pyramid_fancy/v.unit", 16)
    assert r.resolve("items/door_g_gold_h.xml").door.theme == "desert"
    assert r.resolve("doodads/theme_h/h_h_16_up.xml") is None  # rebuilt by AE: stays ported
    m = "actors/undead/mummies/"
    for hw1, ae in (("guard_desert_1", m + "mummy_guard"), ("mummy_1", m + "mummy_soldier"),
                    ("lich_desert_1", m + "mummy_lich_ice"), ("spider_1", "actors/beasts/spiders/spider_poison"),
                    ("tower_tracking_2", "actors/towers/tower_laser_ice")):
        assert r.resolve(f"actors/{hw1}.xml").path == f"{ae}.unit"


def test_multi_boss_counter_opens_the_gate():
    # dungeon239628003's level0: two bosses count a Variable down to 0, which opens the exit.
    from hw2ae.convert import Report, convert_level
    lv = hw1_level.load(Path(__file__).parent / "fixtures" / "multiboss.xml")
    r = Resolver(AllExist())  # type: ignore[arg-type]
    out = convert_level(lv, r, Path("does-not-exist"), Report(), lambda i: None)
    anubis, = [u for u in out.units if u.path == "actors/bosses/boss_anubis/boss_anubis.unit"]
    lich, = [u for u in out.units if u.path == "actors/bosses/boss_lich/boss_lich.unit"]
    by = {}
    for s in out.scripts:
        by.setdefault(s.cls, []).append(s)

    var, = by["Variable"]
    assert Param("int", "Value", 2) in var.params
    died = {s.params[0].value[0]: s for s in by["UnitDestroyedTrigger"]}
    assert set(died) == {anubis.id, lich.id}
    destroy, = by["DestroyUnits"]
    for trig in died.values():
        # The generator's "delays" are its connection ids, not times; the check
        # runs 50 ms after the change, as AE's own counters do.
        (change, d1), (check, d2) = trig.connections
        assert (d1, d2) == (0, 50)
        change = next(s for s in by["ChangeVariables"] if s.id == change)
        check = next(s for s in by["CheckVariables"] if s.id == check)
        assert Param("int", "Function", 3) in change.params and Param("int", "Value", 1) in change.params
        assert Param("ids", "Variables", [var.id]) in change.params
        assert Param("int", "Function", 1) in check.params and Param("int", "Value", 0) in check.params
        assert Param("ids", "Variable", [var.id]) in check.params
        on_true = next(p.value for p in check.params if p.name == "OnTrue")
        assert destroy.id in on_true

    # One bar per kind of boss, each with its own title.
    bars = {next(p.value for p in b.params if p.name == "Name"): b for b in by["CreateBossBar"]}
    assert set(bars) == {".d.ig.boss_3", ".ig.boss3"}
    assert Param("ids", "Actors", [anubis.id]) in bars[".d.ig.boss_3"].params


def test_danger_zone_targets_follow_the_hw1_shape():
    from hw2ae import sval
    lv = hw1_level.load(Path(__file__).parent / "fixtures" / "dragon.xml")
    nodes = [n for n in lv.scripts if n.id in (689, 690)]
    shape = next(n for n in nodes if n.id == 689)

    def filter_for(types: str | None):
        extra = f'<int name="types">{types}</int>' if types is not None else ""
        shape.params = sval.parse_text(f'<dictionary><float name="w">4</float><float name="h">4</float>{extra}</dictionary>')
        out = Level("DungeonGameMode", None)
        convert_scripts(nodes, ScriptContext(out, {}, lambda i: None, warn=lambda m: None))
        zone = next(s for s in out.scripts if s.cls == "DangerArea")
        return next((p.value for p in zone.params if p.name == "Filter"), None)

    assert filter_for("1") == 2       # players only: the final level's damage zone
    assert filter_for("2") == 4       # enemies only: the boss room's bloodlust
    assert filter_for("15") is None   # everything: AE's default
    assert filter_for(None) is None


def test_win_orb_is_a_pickup():
    # The generator's last floor ends the game when this orb is picked up.
    r = Resolver(AllExist())  # type: ignore[arg-type]
    assert r.resolve("items/crystal_purple.xml").path == "items/pickups/collectable_10_sphere.unit"


def test_potion_vendor_opens_the_power_shop():
    from hw2ae import sval
    lv = hw1_level.load(FIX / "levels" / "level0.xml")
    node = next(n for n in lv.scripts if n.type == "ShopArea")
    node.params = sval.parse_text('<dictionary><string name="cats">power</string></dictionary>')
    out = Level("DungeonGameMode", None)
    convert_scripts([node], ScriptContext(out, {}, lambda i: None, warn=lambda m: None))
    shop = next(s for s in out.scripts if s.cls == "ShopArea")
    assert Param("int", "Type", 1) in shop.params


def test_bonus_walls_keep_hw1_offsets():
    # A v_16 column ending in v_cap_up left a 16-px walk-out gap in the bonus
    # lobby: bonus pieces keep HW1's origins, unlike the prison walls measured.
    r = Resolver(AllExist())  # type: ignore[arg-type]
    assert r.resolve("doodads/theme_a/a_v_16.xml").dy == -16
    for n in range(1, 6):
        v = r.resolve(f"doodads/theme_bonus{n}/bonus{n}_v_16.xml")
        cap = r.resolve(f"doodads/theme_bonus{n}/bonus{n}_v_cap_up.xml")
        assert (v.dx, v.dy) == (cap.dx, cap.dy) == (0, 0)


def test_blue_and_gold_ticks_stay_apart():
    # tick_1_elite (spiky blue) and tick_2 (gold) share 70 HP with AE's tick_gold.
    r = Resolver(AllExist())  # type: ignore[arg-type]
    assert r.resolve("actors/tick_1_elite.xml").path == "actors/beasts/ticks/tick_elite.unit"
    assert r.resolve("actors/tick_2.xml").path == "actors/beasts/ticks/tick_gold.unit"
    assert r.resolve("actors/tick_2_small.xml").path == "actors/beasts/ticks/tick_gold_small.unit"


# -- materials and ported HW1 art -----------------------------------------------

def test_normalize_materials_fixes_units_from_any_tool(tmp_path):
    from hw2ae.hw1port import hwr_materials_refs, normalize_materials
    unit = tmp_path / "hw1" / "x" / "a.unit"
    unit.parent.mkdir(parents=True)
    # The original C# tool (no prefix) and HW2A000FF with a prefix.
    unit.write_text('<sprite texture="a.png" material="system/hammerwatch.mats:wall">\n'
                    '<sprite texture="b.png" material="hw1/x/system/hammerwatch.mats:glow">\n')
    (tmp_path / "hw1" / "x" / "t.tileset").write_text('<tileset material="system/default.mats:floor"/>')
    assert normalize_materials(tmp_path) == 1
    text = unit.read_text()
    assert hwr_materials_refs(text) == 0
    assert 'material="system/default.mats:wall"' in text and 'material="system/default.mats:glow"' in text


def _png(path: Path) -> None:
    """A 1x1 RGBA PNG, without needing Pillow."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\xff")) + chunk(b"IEND", b""))


def test_porter_ships_custom_doodads_inside_the_scenario(tmp_path):
    from hw2ae import hw1port
    if not hw1port.AVAILABLE:
        pytest.skip("HW2A000FF is not installed")
    import shutil
    mission = tmp_path / "mission"
    shutil.copytree(Path(__file__).parent / "fixtures" / "custom_art", mission)
    _png(mission / "doodads" / "level1" / "c_blood.png")
    scenario = tmp_path / "scenario"
    warnings: list[str] = []
    porter = hw1port.Porter(mission, None, scenario, "my_dungeon", warnings.append)

    ae_path = porter.port_unit("doodads/level1/c_h_16.xml")
    assert ae_path == "hw1/my_dungeon/doodads/level1/c_h_16.unit"
    text = (scenario / ae_path).read_text()
    assert 'texture="hw1/my_dungeon/doodads/level1/c_blood.png"' in text
    assert "system/default.mats:" in text and "hammerwatch.mats" not in text
    assert (scenario / "hw1/my_dungeon/doodads/level1/c_blood.png").is_file()
    assert porter.port_unit("items/not_here.xml") is None


def test_plank_walkways_widen_by_the_player_size_difference():
    from hw2ae.ae.walkway import WALKWAYS, widen
    # AE's special_bridge_plank_p1 (= HW1's): rails above and below a 10-11 px walkway.
    p1 = ('<collision static="true"><polygon><point>0 5</point><point>0 -8</point><point>28 -8</point>'
          '<point>28 1</point></polygon><polygon><point>30 20</point><point>30 11</point><point>1 16</point>'
          '<point>1 20</point></polygon></collision>')
    assert widen(p1) == ('<collision static="true"><polygon><point>0 3</point><point>0 -10</point>'
                         '<point>28 -10</point><point>28 -1</point></polygon><polygon><point>30 22</point>'
                         '<point>30 13</point><point>1 18</point><point>1 22</point></polygon></collision>')
    # p5 runs vertically: its rails move left and right.
    p5 = ('<polygon><point>-8 27</point><point>2 1</point></polygon>'
          '<polygon><point>16 27</point><point>11 1</point></polygon>')
    assert widen(p5) == ('<polygon><point>-10 27</point><point>0 1</point></polygon>'
                         '<polygon><point>18 27</point><point>13 1</point></polygon>')
    assert widen("<polygon><point>0 0</point></polygon>") == "<polygon><point>0 0</point></polygon>"
    assert WALKWAYS.match("doodads/walls/chambers/special_bridge_plank_p4.unit")
    assert not WALKWAYS.match("doodads/walls/chambers/special_bridge_p1.unit")


def test_make_hittable_adds_breakable_and_health_per_scene():
    from hw2ae.hw1port import UNBREAKABLE_HEALTH, make_hittable
    ported = ('<unit slot="doodad">\n  <scenes start="hwport_def">\n    <scene name="hwport_shared">\n'
              '      <collision static="true"><circle offset="0 0" radius="5" /></collision>\n    </scene>\n'
              '    <scene name="closed">\n\t  <scene src="hwport_shared" />\n    </scene>\n'
              '    <scene name="hwport_def">\n\t  <scene src="hwport_shared" />\n    </scene>\n  </scenes>\n</unit>')
    text = make_hittable(ported)
    assert text.startswith('<unit slot="doodad">\n  <behavior class="Breakable">')
    health = f'<data name="health"><int>{UNBREAKABLE_HEALTH}</int></data>'
    # Every scene a unit can be in has health; the shared one is only included.
    assert text.count(health) == 2
    assert f'<scene name="hwport_shared">\n      <collision' in text
    assert f'<scene name="closed">\n      {health}' in text


# -- doors -----------------------------------------------------------------

def _door_obj(i, name, x, y):
    return hw1_level.Obj(i, f"items/{name}.xml", x, y)


def _wall(i, piece, x, y):
    return hw1_level.Obj(i, f"doodads/theme_a/a_{piece}.xml", x, y)


def _doors(objs, walls=()):
    r = Resolver(AllExist())  # type: ignore[arg-type]
    pieces = [(o, r.resolve(o.type).door) for o in objs]
    out = Level("DungeonGameMode", None)
    n = doors.convert(pieces, list(walls), out, {})
    return n, out


def test_door_generator_row_fills_the_wall_gap():
    # Generator: five 16-px h_v2 pieces between walls at x=21 and x=27.
    objs = [_door_obj(i, "door_a_silver_h_v2", 22.5 + i, 43.0) for i in range(5)]
    n, out = _doors(objs)
    assert n == 1
    got = [(u.path.rsplit("/", 1)[-1], u.x, u.y) for u in out.units]
    assert got == [("door_h_cap_l_silver_prison.unit", 352, 704),
                   ("door_h_mid_silver_prison.unit", 368, 704),
                   ("door_h_mid_silver_prison.unit", 384, 704),
                   ("door_h_mid_silver_prison.unit", 400, 704),
                   ("door_h_cap_r_silver_prison.unit", 416, 704)]
    dc, = out.scripts
    assert dc.cls == "DoorController"
    assert Param("string", "Collectable", "key_silver") in dc.params
    assert Param("ids", "Doors", [u.id for u in out.units]) in dc.params


def test_door_castle_row_matches_ae_remake():
    # Castle Hammerwatch level 2: cap_l, h_v2, h, cap_r -> AE cap_l, mid, mid, cap_r.
    objs = [_door_obj(1, "door_a_gold_h_cap_l", -23.5, -17.5), _door_obj(2, "door_a_gold_h_v2", -23.0, -17.5),
            _door_obj(3, "door_a_gold_h", -21.5, -17.5), _door_obj(4, "door_a_gold_h_cap_r", -20.5, -17.5)]
    _, out = _doors(objs)
    assert [(u.x, u.y) for u in out.units] == [(-384, -272), (-368, -272), (-352, -272), (-336, -272)]


def test_door_column_fills_rows_between_walls():
    # Generator: bronze v pieces at rows 27-30, walls end at row 26 and resume at row 31.
    objs = [_door_obj(i, "door_a_bronze_v", 50.5, 27.0 + i) for i in range(4)]
    walls = [_wall(10, "x_t_dn", 50, 26), _wall(11, "crn_r_dn", 51, 26), _wall(12, "crn_l_up", 50, 31)]
    _, out = _doors(objs, walls)
    parts = [u.path.rsplit("/", 1)[-1].split("_bronze")[0] for u in out.units]
    assert parts == ["door_v_cap_u", "door_v_mid", "door_v_mid", "door_v_mid", "door_v_cap_d"]
    assert [u.y for u in out.units] == [448, 464, 480, 496, 512]
    assert {u.x for u in out.units} == {800}


def test_separate_doors_get_separate_controllers():
    objs = [_door_obj(1, "door_a_bronze_h_v2", 10.5, 5.0), _door_obj(2, "door_a_bronze_h_v2", 11.5, 5.0),
            _door_obj(3, "door_a_bronze_h_v2", 30.5, 5.0), _door_obj(4, "door_a_gold_h_v2", 12.5, 5.0)]
    n, out = _doors(objs)
    assert n == 3 and [s.cls for s in out.scripts] == ["DoorController"] * 3


def test_level_writer_roundtrip(tmp_path):
    lv = Level("DungeonGameMode", "effects/lighting/bonus.env")
    lv.units.append(Unit("doodads/generic/lamps/torch.unit", 8, 16, 1, {"start": ("string", "n-off")}))
    lv.units.append(Unit("doodads/generic/lamps/torch.unit", 40, 16, 2))
    lv.scripts.append(Script("LevelStart", 3, 0, 0, params=[Param("string", "StartID", "north")]))
    lv.tile_cells = {(0, 0): [("tilesets/prison_dirt.tileset", "7e7e04")]}
    path = tmp_path / "x.lvl"
    level_writer.write(lv, path)
    back = level_reader.load(path)
    assert back.game_mode == "DungeonGameMode" and back.environment == "effects/lighting/bonus.env"
    assert sorted((u.x, u.y, u.id) for u in back.units) == [(8, 16, 1), (40, 16, 2)]
    assert back.scripts[0].cls == "LevelStart" and back.tiles[0].datasets[0].hexdata == "7e7e04"


# -- against a real AE install (skipped when it isn't there) ---------------

AE_ROOT = find_ae_root()
AE_ASSETS = find_ae_assets(AE_ROOT) if AE_ROOT else None
needs_ae = pytest.mark.skipif(AE_ASSETS is None, reason="Hammerwatch Anniversary Edition not installed")


@needs_ae
def test_convert_fixture_against_ae(tmp_path):
    from hw2ae.ae.assets import AssetIndex
    from hw2ae.validate import validate

    report = convert(Options(source=FIX, out=tmp_path / "test_dungeon", ae_assets=AE_ASSETS, log=lambda *a: None))
    assert report.levels == 2
    assert report.unmapped == {"items/some_unknown_item.xml": 1}
    lives = (tmp_path / "test_dungeon" / "scripts" / "Modules" / "PartyRecord.as").read_text()
    assert "m_lives = 0;" in lives and "m_lives = 2;" not in lives  # the mission's info.xml sets 0
    problems, n = validate(tmp_path / "test_dungeon", AssetIndex(AE_ASSETS))
    assert n == 2 and not problems, problems.items
    lvl0 = level_reader.load(tmp_path / "test_dungeon" / "levels" / "test_dungeon" / "level0.lvl")
    assert lvl0.environment == "effects/lighting/bonus.env"  # HW1 ambient 255 -> bright
    assert lvl0.prefabs == []
    assert any(u.type == "doodads/generic/shop_defense.unit" for u in lvl0.units)
    assert {"UseTrigger", "ShopArea"} <= {s.cls for s in lvl0.scripts}
    walls = [u for u in lvl0.units if u.type == "doodads/walls/prison/x_w.unit"]
    assert [(w.x, w.y) for w in walls] == [(48, 80)]
    lvl1 = level_reader.load(tmp_path / "test_dungeon" / "levels" / "test_dungeon" / "level1.lvl")
    assert lvl1.environment == "effects/lighting/archives_1.env"  # dim HW1 ambient keeps the theme
    assert any(u.type == "doodads/walls/archives/h.unit" for u in lvl1.units)


# -- HW1 class tweaks -> AE player files ------------------------------------

from hw2ae.ae import players, skills  # noqa: E402
from hw2ae.hw1 import tweak  # noqa: E402

TWEAK = Path(__file__).parent / "fixtures" / "tweak"


def test_parse_hw1_tweak():
    t = tweak.load_dir(TWEAK)["knight"]
    assert t.params["max-health"] == 125 and t.params["heal"] is True and t.params["whirl-range"] == 1.0
    assert [u.id for u in t.upgrades] == ["healeff2", "healeff3", "lightning"]
    assert t.upgrade("healeff3").cost == 0 and t.upgrade("healeff2").values == {"heal-amount": 7, "heal-mana-cost": 7}


def test_stock_tiers_follow_unlocks():
    stock = tweak.load_stock()["knight"]
    heal = players.Ladder(["heal", "healeff"], "pal_healing", None, {"heal-amount": ("bind:heal", "id")})
    tiers, ups = players.hw1_tiers(stock, heal)
    assert [t["heal-amount"] for t in tiers] == [-1, 5, 6, 7, 8]
    assert [u.id for u in ups] == ["heal", "healeff1", "healeff2", "healeff3"]
    # A ladder hanging off an unlock starts from the unlocked values.
    dur = players.Ladder(["whirldur"], "pal_whirlwind", "pal_whirlwind_duration", {"whirl-dur": ("x", "ms")},
                         after=["whirl"])
    assert [t["whirl-dur"] for t in players.hw1_tiers(stock, dur)[0]] == [4, 6, 8]
    assert players.ladder_key("whirldur") == players.ladder_key("whirldur1") == "whirldur"
    assert players.ladder_key("health-3") == "health"


def test_stock_json_matches_hw1_assets():
    from hw2ae.config import find_hw1_assets
    hw1 = find_hw1_assets()
    if hw1 is None or not (hw1 / "tweak").is_dir():
        pytest.skip("HW1 assets not extracted")
    live = tweak.load_dir(hw1 / "tweak")
    assert {k: tweak.to_json(v) for k, v in live.items()} == \
        {k: tweak.to_json(v) for k, v in tweak.load_stock().items()}


def test_unit_conversions():
    assert players._convert("inv1000", 600, []) == pytest.approx(1.6667, abs=1e-4)
    assert players._convert("lin 20 10", 4, []) == 90  # charge range: tiles -> AE units
    assert players._convert("ratio", 1.1, [(0.9, 1.2)]) == pytest.approx(1.4667, abs=1e-4)
    assert players._convert("interp", 4.25, [(3, 350), (4, 450), (5, 550)]) == 475
    assert players._convert("interp", 8, [(5, 550), (6.5, 650)]) == 750  # extrapolates


def test_skill_svals_keep_bare_ampersands():
    text = ('<dict>\n\t<string name="description">.x?a=1&b=2</string>\n\t<array name="upgrades">\n\t</array>\n'
            '</dict>\n<dict>\n\t<string name="name">duplicate</string>\n</dict>\n')
    root = skills.parse(text)
    assert skills.scalar(root, "description") == ".x?a=1&b=2"
    out = skills.serialize(root)
    assert "a=1&b=2" in out and "&amp;" not in out and "duplicate" not in out
    assert '<array name="upgrades"></array>' in out  # AE's files never self-close


@needs_ae
def test_tweaks_against_ae():
    res = players.Converter(tweak.load_dir(TWEAK), AE_ASSETS).run()

    # On a stock tier: AE's own value for that tier, nothing more to buy.
    sword = skills.parse(res.files["players/paladin/skills/pal_sword.sval"])
    dmg = skills.find_mod(sword, "pal_sword_dmg")
    assert skills.scalar(dmg, "starting-level") == "3"
    assert all(skills.child(s, "category") is None for s in [dmg] + skills.levels(dmg))

    # Owned at tier 2 while the shop still sells tiers 3 and 4: AE lists
    # level[picked] as the next buy, so the entries move down two levels.
    heal = skills.parse(res.files["players/paladin/skills/pal_healing.sval"])
    lv = skills.levels(heal)
    assert skills.scalar(heal, "starting-level") == "2"
    assert skills.scalar(heal, "category") == "def4"
    assert (skills.scalar(lv[0], "category"), skills.scalar(lv[0], "cost")) == ("def4", "2600")
    assert (skills.scalar(lv[1], "category"), skills.scalar(lv[1], "cost")) == ("def5", "0")  # mission's price
    assert skills.child(lv[2], "category") is None and skills.child(lv[3], "category") is None

    # Off every tier: the author's own number, in the class's base stats.
    classes = res.files["players/classes.sval"]
    paladin = classes[classes.index("<string name=\"id\">paladin</string>"):]
    assert '<int name="base-health">125</int>' in paladin[:paladin.index("</dict>")]

    assert any("whirl-range" in w for w in res.warnings)
    assert any("lightning" in w for w in res.warnings)
    assert not any(f.startswith("players/priest/") for f in res.files)  # no priest.xml: AE stock


@needs_ae
def test_stock_tweaks_change_nothing():
    stock = tweak.load_stock()
    assert players.Converter(dict(stock), AE_ASSETS, stock=stock).run().files == {}


@needs_ae
def test_power_shop_and_lives_scripts():
    stock = tweak.load_stock()["shared"]
    mission = tweak.Tweak(dict(stock.params), [u for u in stock.upgrades if u.id != "life"])
    text, notes = players.power_shop_script(AE_ASSETS, mission, stock, players.load_table())
    build = text[text.index("void BuildList()"):text.index("void ReloadList()")]
    assert '"power_life"' not in build and '"power_rejuv", ".rejuv-uname", ".rejuv-udesc", 175' in build
    assert '"potion_damage", ".pot-dmg-uname", ".pot-dmg-udesc", 1000' in build  # stock HW1 price -> AE's
    assert any("'life' not sold" in n for n in notes)
    assert players.power_shop_script(AE_ASSETS, stock, stock, players.load_table())[0] is None
    assert "m_lives = 0;" in players.lives_script(AE_ASSETS, 0)


@needs_ae
def test_combo_owned_from_start_charges():
    stock = tweak.load_stock()
    shared = tweak.Tweak({**stock["shared"].params, "combo": True}, [])
    res = players.Converter({"shared": shared}, AE_ASSETS, stock=stock).run()
    combo = skills.parse(res.files["players/shared/skills/shared_combo.sval"])
    assert skills.scalar(combo, "starting-level") == "1"
    # AE counts bought levels only; the overrides count the starting level too.
    for rel in players.COMBO_SCRIPTS:
        assert 'GetSkillLevel("shared_misc_combo")' in res.files[rel]
        assert 'pickedSkills["shared_misc_combo"]' not in res.files[rel]


def test_ported_textures_are_padded_to_powers_of_two(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    from hw2ae.hw1port import pad_textures
    f = tmp_path / "hw1" / "m" / "doodads" / "big.png"
    f.parent.mkdir(parents=True)
    Image.new("RGBA", (500, 655), (255, 0, 0, 255)).save(f)
    Image.new("RGBA", (64, 32)).save(f.with_name("ok.png"))
    assert pad_textures(tmp_path) == 1
    with Image.open(f) as im:
        assert im.size == (512, 1024)
        assert im.getpixel((499, 654)) == (255, 0, 0, 255) and im.getpixel((500, 0))[3] == 0


# -- pirate cove: a custom mission's script nodes ----------------------------

def test_piratecove_script_nodes():
    # Real nodes from Hammerwatch/editor/piratecove/levels/cove.xml.
    lv = hw1_level.load(Path(__file__).parent / "fixtures" / "piratecove_nodes.xml")
    out = Level("DungeonGameMode", None)
    out.units.append(Unit("doodads/x.unit", 40, 290, 999))  # next to the speech bubble
    placed = {110183: 900, 117465: 901, 117466: 902, 109980: 903, 109982: 904, 109989: 905, 109988: 906,
              110193: 910, 110194: 911, 118818: 912, 119534: 913, 118765: 914, 119235: 915,
              119210: 916, 119211: 917, 119212: 918}
    warnings: list[str] = []
    ctx = ScriptContext(out, dict(placed), lambda i: None, warn=warnings.append,
                        resolve_unit=lambda t: "actors/x.unit", dialog_prefix="cove")
    convert_scripts(lv.scripts, ctx)
    by_cls: dict[str, list] = {}
    for s in out.scripts:
        by_cls.setdefault(s.cls, []).append(s)
    sid = {n.id: ctx.id_map[n.id] for n in lv.scripts}

    # A hit on a placed object counts; Counter keeps HW1's count and target.
    hit, = by_cls["UnitDamagedTrigger"]
    assert Param("ids", "Units", [900]) in hit.params
    # A Counter counts its runs in a saved Variable (AE's Counter forgets on a level revisit).
    assert "Counter" not in by_cls
    add = next(s for s in by_cls["ChangeVariables"] if s.id == sid[110189])
    (check_id, _), = add.connections
    check = next(s for s in by_cls["CheckVariables"] if s.id == check_id)
    assert Param("int", "Value", 2) in check.params and Param("ids", "OnTrue", [911]) in check.params

    # "dynamic" = what a SpawnObject made: AE's #Units from that SpawnUnit.
    gone, = by_cls["UnitDestroyedTrigger"]
    assert Param("sources", "#Units", [(sid[153456], "AllSpawned")]) in gone.params

    # State 2 is toggle for both (HW1's editor lists Show/Enable, Hide/Disable, Toggle).
    hide, = by_cls["HideUnit"]
    assert Param("int", "State", 3) in hide.params and Param("ids", "Units", [901, 902]) in hide.params
    coll, = by_cls["ToggleCollision"]
    assert Param("int", "State", 3) in coll.params

    rnd, = by_cls["RandomCount"]
    assert Param("int", "NumToExecute", 1) in rnd.params and Param("ids", "ToExecute", [917, 916, 918]) in rnd.params
    shake, = by_cls["AddScreenShake"]
    assert Param("int", "Time", 200) in shake.params and Param("float", "Amount", 4.0) in shake.params

    # Set -> a flag for the whole run; toggle -> check, then set the opposite.
    flags = by_cls["SetFlag"]
    assert any(Param("int", "State", 2) in f.params and Param("string", "Flag", "quest_shovel") in f.params
               for f in flags)
    toggle, = by_cls["CheckFlag"]
    assert {i for p in toggle.params if p.kind == "ids" for i in p.value} == \
        {f.id for f in flags if Param("string", "Flag", "quest_text_shovel") in f.params}

    # One CheckPlayerCount per HW1 player count; 4 means 4 or more in AE.
    counts = sorted(by_cls["CheckPlayerCount"], key=lambda s: next(p.value for p in s.params if p.name == "Value"))
    assert [next(p.value for p in s.params if p.name == "OnTrue") for s in counts] == [[912], [913], [913], [913]]
    assert Param("int", "Function", 4) in counts[-1].params

    # A speech bubble is a dialog line, anchored to the nearest unit.
    dlg, = by_cls["StartDialog"]
    assert ctx.dialogs == [("cove_1", "Here we are!")]
    assert Param("string", "Dialog", "tweak/dialogs/dialog_castlehw.sval:cove_1") in dlg.params
    assert Param("ids", "Player", [999]) in dlg.params

    fx, = by_cls["SpawnEffect"]
    assert Param("string", "Effect", "effects/blink.effect") in fx.params
    # The level's own CheckVariable (the others count Counter runs).
    check, = (s for s in by_cls["CheckVariables"] if not str(s.label or "").startswith(">="))
    assert Param("int", "Function", 2) in check.params        # HW1 1 = Greater
    assert any("MoveAI" in w for w in warnings)


@needs_ae
def test_speech_bubbles_extend_ae_dialog_table():
    from hw2ae.ae.scripts import dialog_file
    text = dialog_file(AE_ASSETS, [("cove_1", "Here we are & ready")])
    import xml.etree.ElementTree as ET
    table = ET.fromstring("<_>" + skills._BARE_AMP.sub("&amp;", text) + "</_>").find("array")
    ids = [skills.scalar(d, "id") for d in table]
    assert "guide1" in ids and ids[-1] == "cove_1"  # AE's own lines stay
    assert "Here we are &amp; ready" in text


@needs_ae
def test_custom_items_and_actors_keep_hw1_looks(tmp_path):
    from hw2ae.ae.custom import CustomUnits
    from hw2ae.config import find_hw1_assets
    from hw2ae.mapping.resolver import Resolver
    from hw2ae.ae.assets import AssetIndex
    hw1 = find_hw1_assets()
    if hw1 is None:
        pytest.skip("HW1 assets not extracted")
    mission = tmp_path / "mission"
    (mission / "items").mkdir(parents=True)
    (mission / "actors").mkdir()
    Image = pytest.importorskip("PIL.Image")
    Image.new("RGBA", (64, 64)).save(mission / "items" / "pie.png")
    Image.new("RGBA", (256, 160)).save(mission / "actors" / "capt.png")
    (mission / "items" / "my_pie.xml").write_text(
        '<item behavior="food"><behavior><dictionary><entry name="hp"><int>10</int></entry></dictionary></behavior>'
        '<sprite scale="16"><texture>items/pie.png</texture><origin>8 8</origin><frame>16 0 16 16</frame></sprite></item>')
    (mission / "items" / "my_key.xml").write_text(
        '<item behavior="collectable"><behavior><dictionary><entry name="pickup-text"><string>Shovel!</string></entry>'
        '</dictionary></behavior><sprite scale="16"><texture>items/pie.png</texture><origin>0 0</origin>'
        '<frame>0 0 16 16</frame></sprite></item>')
    dirs = "east northeast north northwest west southwest south southeast".split()
    sprites = "".join(f'<sprite name="{d}{s}"><texture>actors/capt.png</texture><origin>16 24</origin>'
                      f'<frame time="100">{32 * i} {r * 32} 32 32</frame></sprite>'
                      for r, s in enumerate(("", "-walk", "-attack")) for i, d in enumerate(dirs))
    (mission / "actors" / "my_skeleton_capt.xml").write_text(
        '<actor behavior="composite"><behavior><dictionary><entry name="hp"><int>1000</int></entry>'
        '<entry name="boss-hp"><bool>true</bool></entry>'
        '<dictionary name="movement"><string name="type">melee</string></dictionary><array name="skills">'
        '<dictionary><string name="type">whirlwind</string></dictionary><dictionary><string name="type">hit</string>'
        '</dictionary><dictionary><string name="type">buff</string><string name="buff">buffs/bloodlust.xml</string>'
        '</dictionary></array></dictionary></behavior>' + sprites +
        '<sprite name="whirlwind"><texture>actors/capt.png</texture><origin>16 24</origin><frame>0 96 32 32</frame>'
        '</sprite></actor>')
    out = tmp_path / "scenario"
    cu = CustomUnits(mission, hw1, AE_ASSETS, out, "m", Resolver(AssetIndex(AE_ASSETS)).units, lambda w: None)

    # Same behaviour and values as stock health_1 -> AE's health pickup, HW1 art.
    pie = cu.unit("items/my_pie.xml")
    assert pie == "hw1/m/items/my_pie.unit" and cu.made["items/my_pie.xml"].twin == "items/pickups/health_1.unit"
    text = (out / pie).read_text()
    assert 'texture="hw1/m/items/pie.png"' in text and "<frame time=\"100\">16 0 16 16</frame>" in text
    assert (out / "hw1/m/items/pie.png").exists()

    key = (out / cu.unit("items/my_key.xml")).read_text()
    assert "Shovel!" in key and "AnnouncePickup" not in key and ".tut.i.sphere" not in key
    # Its own art, not the sphere's orb, and no combo sphere for picking it up.
    assert "combo_sphere" not in key and "GiveCollectable" not in key
    # AE won't pick up an item whose only effect is its floating text.
    assert re.search(r'<string name="class">SetFlag</string>\s*<string name="flag">hw1_picked_my_key</string>'
                     r'\s*<bool name="value">true</bool>', key)
    art = key.index('texture="hw1/m/items/pie.png" material="items/items.mats:item"')
    assert art < key.index("<light") and key.count("effects/lights/light_L.png") == 2

    # Melee + whirlwind + bloodlust on a skeleton -> Castle's skeleton guard,
    # with HW1's art per facing (AE index 0 = east, clockwise) and its hp.
    capt = cu.unit("actors/my_skeleton_capt.xml")
    assert "skeleton_guard" in cu.made["actors/my_skeleton_capt.xml"].twin
    unit = (out / capt).read_text()
    assert '<int name="hp">1000</int>' in unit
    assert '="./' not in unit  # the twin's own "./x.png" art now points at the twin's folder
    idle2 = unit[unit.index('<scene name="idle-2">'):]
    assert "<frame time=\"100\">192 0 32 32</frame>" in idle2[:idle2.index("</scene>")]  # south
    assert cu.boss_title("actors/my_skeleton_capt.xml") == "Skeleton Capt"


def test_hw1_sprite_scale_shrinks_art_and_collision(tmp_path):
    from hw2ae import scale
    # starcraft_campaign's invisible blocker inv_carre: drawn at scale 32 = half size.
    hw1 = ('<doodad><sprite scale="32"><texture>doodads/block.png</texture><origin>40 35</origin>'
           '<frame>67 74 87 74</frame></sprite></doodad>')
    f = scale.hw1_scale(hw1)
    assert f == 0.5 and scale.hw1_scale('<doodad><sprite scale="16"/></doodad>') == 1.0
    unit = ('<collision static="true"><polygon><point>-42 -34</point><point>49 35</point></polygon>'
            '<circle offset="4 -2" radius="6" /></collision>'
            '<sprite origin="40 35" texture="hw1/m/doodads/block.png"><frame>67 74 87 74</frame></sprite>')
    out = scale.scale_unit(unit, f, lambda p: p.replace(".png", "@x0.5.png"))
    assert "<point>-21 -17</point>" in out and "<point>24.5 17.5</point>" in out
    assert 'offset="2 -1"' in out and 'radius="3"' in out
    assert 'origin="20 18"' in out and "<frame>34 37 43 37</frame>" in out
    assert 'texture="hw1/m/doodads/block@x0.5.png"' in out

    Image = pytest.importorskip("PIL.Image")
    src = tmp_path / "block.png"
    Image.new("RGBA", (200, 100)).save(src)
    with Image.open(scale.scaled_texture(src, f)) as im:
        assert im.size == (100, 50)


@needs_ae
def test_custom_spawners_spawn_the_missions_units(tmp_path):
    import re
    from hw2ae.ae.custom import CustomUnits
    from hw2ae.ae.assets import AssetIndex
    from hw2ae.config import find_hw1_assets
    from hw2ae.mapping.resolver import Resolver
    hw1 = find_hw1_assets()
    if hw1 is None:
        pytest.skip("HW1 assets not extracted")
    Image = pytest.importorskip("PIL.Image")
    m = tmp_path / "m"
    (m / "actors" / "spawners").mkdir(parents=True)
    Image.new("RGBA", (96, 64)).save(m / "actors" / "art.png")
    sprite = '<sprite scale="32" name="{n}"><texture>actors/art.png</texture><origin>8 8</origin><frame>0 0 16 16</frame></sprite>'
    (m / "actors" / "ling.xml").write_text(
        '<actor behavior="melee"><behavior><dictionary><entry name="hp"><int>7</int></entry>'
        '<entry name="speed"><float>1.5</float></entry></dictionary></behavior>' + sprite.format(n="south") + "</actor>")
    spawner = ('<actor behavior="spawner"><behavior><dictionary><array name="spawns">{spawns}</array>'
               '<entry name="hp"><int>50</int></entry></dictionary></behavior>' + sprite.format(n="default") +
               '<collision static="true"><circle offset="0 0" radius="30" /></collision></actor>')
    (m / "actors" / "spawners" / "nest.xml").write_text(spawner.format(
        spawns="<int>750</int><string>actors/ling.xml</string><int>250</int><string>actors/skeleton_1.xml</string>"))
    (m / "actors" / "spawners" / "tower.xml").write_text(spawner.format(spawns=""))
    out = tmp_path / "out"
    cu = CustomUnits(m, hw1, AE_ASSETS, out, "m", Resolver(AssetIndex(AE_ASSETS)).units, lambda w: None)

    # A plain melee chaser is twinned with the nearest stock chaser, never a projectile.
    assert cu.unit("actors/ling.xml") is not None
    assert "projectile" not in cu.made["actors/ling.xml"].twin

    nest = (out / cu.unit("actors/spawners/nest.xml")).read_text()
    proj = re.search(r'<string name="projectile">([^<]+)</string>', nest).group(1)
    assert proj.startswith("hw1/m/") and proj.endswith("_spawn.unit")
    listing = (out / proj).read_text()
    assert "<int>750</int><string>hw1/m/actors/ling.unit</string>" in listing
    assert "<int>250</int><string>actors/undead/skeletons/hammerwatch/skeleton_warrior.unit</string>" in listing
    # Hittable: HW1's own shape (scale 32 -> radius 15), and the minimap marker stays.
    assert '<circle offset="0 0" radius="15" />' in nest and "minimap" in nest

    tower = (out / cu.unit("actors/spawners/tower.xml")).read_text()
    assert '<array name="skills"></array>' in tower and '<int name="hp">50</int>' in tower


def test_validator_flags_units_ae_would_refuse():
    from hw2ae.validate import _xml_error
    ok = ('%include "x.inc"\n<unit><behavior><string name="d">.a?x=1&y=2</string></behavior>\n'
          '%if DIFF_HARD\n<int name="hp">2</int>\n%endif\n</unit>')
    assert _xml_error(ok) is None
    assert _xml_error("<unit><array name=\"skills\"></array>\n</dict></array></unit>") is not None


@needs_ae
def test_scene_swap_handles_scenes_inside_scenes(tmp_path):
    import re
    from hw2ae.ae.custom import CustomUnits
    from hw2ae.validate import _xml_error
    Image = pytest.importorskip("PIL.Image")
    m = tmp_path / "m"
    (m / "actors").mkdir(parents=True)
    Image.new("RGBA", (64, 64)).save(m / "actors" / "a.png")
    (m / "actors" / "civ.xml").write_text('<actor><sprite name="south"><texture>actors/a.png</texture>'
                                          '<origin>4 4</origin><frame>0 0 8 8</frame></sprite></actor>')
    cu = CustomUnits(m, None, AE_ASSETS, tmp_path / "out", "m", {}, lambda w: None)
    # AE's black bat nests whole scenes inside scenes.
    from hw2ae.ae.custom import _sprites, _hw1_xml
    bat = (AE_ASSETS / "actors/beasts/bats/bat_black.unit").read_text(encoding="utf-8")
    unit = cu._actor_scenes(bat, _sprites(_hw1_xml(m / "actors" / "civ.xml")))
    assert _xml_error(unit) is None
    assert len(re.findall(r"<scene\b[^>]*?(?<!/)>", unit)) == unit.count("</scene>")


@needs_ae
def test_ported_doodads_keep_hw1_layers_and_take_hits(tmp_path):
    from hw2ae import hw1port
    from hw2ae.ae.assets import AssetIndex
    from hw2ae.convert import Report, convert_level
    if not hw1port.AVAILABLE:
        pytest.skip("HW2A000FF is not installed")
    import shutil
    mission = tmp_path / "mission"
    shutil.copytree(Path(__file__).parent / "fixtures" / "custom_art", mission)
    _png(mission / "doodads" / "level1" / "c_blood.png")
    piece = "doodads/level1/c_h_16.xml"
    lvl = mission / "levels" / "l.xml"
    lvl.parent.mkdir()
    # Pirate Cove's boat rower: layer 25 over a boat at HW1's default (20).
    lvl.write_text(
        '<dictionary><dictionary name="doodads"><array name="doodads">'
        f'<dictionary><int name="id">1</int><string name="type">{piece}</string><vec2 name="pos">0 0</vec2>'
        '<int name="layer">25</int></dictionary>'
        f'<dictionary><int name="id">2</int><string name="type">{piece}</string><vec2 name="pos">4 0</vec2>'
        '</dictionary>'
        f'<dictionary><int name="id">3</int><string name="type">{piece}</string><vec2 name="pos">8 0</vec2>'
        '</dictionary></array></dictionary>'
        '<dictionary name="scripting"><array name="nodes"><dictionary><int name="id">10</int>'
        '<string name="type">ObjectEventTrigger</string><bool name="enabled">True</bool>'
        '<int name="trigger-times">3</int><vec2 name="pos">8 2</vec2><dictionary name="parameters">'
        '<string name="event">Hit</string><dictionary name="object"><int-arr name="static">3</int-arr>'
        '</dictionary></dictionary></dictionary></array></dictionary></dictionary>')
    scenario = tmp_path / "scenario"
    porter = hw1port.Porter(mission, None, scenario, "m", lambda w: None)
    report = Report()
    out = convert_level(hw1_level.load(lvl), Resolver(AssetIndex(AE_ASSETS)), AE_ASSETS, report,
                        lambda i: None, porter=porter)

    a, b, c = sorted(out.units, key=lambda u: u.x)
    assert a.state == {"layer": ("int", 5)} and b.state == {}
    # Only the watched piece gets the hittable copy; the trigger watches it.
    assert a.path == b.path == "hw1/m/doodads/level1/c_h_16.unit"
    assert c.path == "hw1/m/doodads/level1/c_h_16_hit.unit"
    assert 'class="Breakable"' in (scenario / c.path).read_text()
    trig, = [s for s in out.scripts if s.cls == "UnitDamagedTrigger"]
    assert Param("ids", "Units", [c.id]) in trig.params


# Stock HW1 items/actors with no AE counterpart found, and why (README "Coverage and known gaps").
KNOWN_GAPS = {
    # bombs and shots enemies throw, not placed in levels
    "items/bomb_boss_krilith.xml", "items/bomb_drain.xml", "items/bomb_floater_fire.xml",
    "items/bomb_lich_desert_1.xml", "items/bomb_lich_desert_2.xml", "items/bomb_stalactite.xml",
    "items/bomb_stalactite_e.xml", "items/bomb_wisp_1.xml", "items/bomb_wisp_1_small.xml", "items/bomb_wisp_2.xml",
    "items/ranger_bomb.xml", "actors/boss_knight/lich_projectile.xml",
    "actors/boss_dragon/fireball_trap.xml", "actors/boss_dragon/firespray.xml",
    # no AE unit: Temple's archery reward, crystals, furniture, letters, extra lives, shovel
    "items/collectable_4.xml", "items/crystal_green.xml", "items/crystal_red.xml",
    *(f"items/furniture_{p}.xml" for p in ("chair_a", "chair_a_v2", "chair_a_v3", "chair_b", "chair_b_v2",
                                           "chair_b_v3", "table_a", "table_a_v2", "table_b", "table_b_v2",
                                           "table_b_v3")),
    *(f"items/letter_{c}.xml" for c in "acehmrtw"),
    "items/powerup_5up.xml", "items/powerup_7up.xml", "items/tool_shovel.xml",
    # no AE counterpart: lich_desert_2's confusion bolt, the fire floater, a desert NPC, scripted boss props
    "actors/lich_desert_2.xml", "actors/floater_fire.xml", "actors/npc_guard_desert_1.xml",
    "actors/boss_krilith/boss_krilith_static.xml", "actors/boss_krilith/collision_skeleton_1_mb.xml",
    "actors/slime_1_host_razed.xml", "actors/tower_battlement_archer_3_razed.xml",
    "actors/spawners/bonus/skeleton_1.xml",
}


def test_every_stock_item_and_actor_is_mapped_dropped_or_a_known_gap():
    from hw2ae.config import find_hw1_assets
    hw1 = find_hw1_assets()
    if hw1 is None:
        pytest.skip("HW1's extracted assets are not available")
    r = Resolver(AllExist())  # type: ignore[arg-type]
    missing = sorted(rel for kind in ("items", "actors") for f in (hw1 / kind).rglob("*.xml")
                     if (rel := f.relative_to(hw1).as_posix()) not in KNOWN_GAPS and r.resolve(rel) is None)
    assert not missing, missing


def test_increment_counter_takes_a_run_back(tmp_path):
    # Survival Colosseum: +1 per enemy entering the arena, a Counter run per one dying, fires at 0.
    # HW1's Counter counts down and IncrementCounter adds to it, so in AE it takes a run back.
    node = ('<dictionary><int name="id">{i}</int><string name="type">{t}</string><bool name="enabled">True</bool>'
            '<int name="trigger-times">-1</int><vec2 name="pos">0 0</vec2>{p}</dictionary>')
    xml = tmp_path / "l.xml"
    xml.write_text('<dictionary><dictionary name="scripting"><array name="nodes">'
                   + node.format(i=683, t="Counter", p='<dictionary name="parameters"><int name="count">0</int>'
                                 '<dictionary name="execute"><int-arr name="static">685</int-arr></dictionary>'
                                 '</dictionary>')
                   + node.format(i=684, t="IncrementCounter", p='<dictionary name="parameters"><dictionary '
                                 'name="counter"><int-arr name="static">683</int-arr></dictionary></dictionary>')
                   + node.format(i=685, t="ScriptLink", p="")
                   + "</array></dictionary></dictionary>")
    lv = hw1_level.load(xml)
    out = Level("DungeonGameMode", None)
    ctx = ScriptContext(out, {}, lambda i: None, warn=lambda m: None)
    convert_scripts(lv.scripts, ctx)
    # Never AE's Counter: it forgets its count when the player leaves and comes back.
    assert not any(s.cls in ("Counter", "ModifyCounter") for s in out.scripts)
    var, = (s for s in out.scripts if s.cls == "Variable")
    run = next(s for s in out.scripts if s.id == ctx.id_map[683])
    inc = next(s for s in out.scripts if s.id == ctx.id_map[684])
    check, = (s for s in out.scripts if s.cls == "CheckVariables")
    # A run adds 1 then checks >= count; IncrementCounter subtracts 1 from the same Variable.
    assert Param("int", "Function", 2) in run.params and Param("ids", "Variables", [var.id]) in run.params
    assert run.connections == [(check.id, 0)]
    assert Param("int", "Function", 4) in check.params and Param("int", "Value", 0) in check.params
    assert Param("ids", "OnTrue", [ctx.id_map[685]]) in check.params
    assert Param("int", "Function", 3) in inc.params and Param("ids", "Variables", [var.id]) in inc.params


def _nodes_level(tmp_path, nodes: str):
    xml = tmp_path / "l.xml"
    xml.write_text('<dictionary><dictionary name="scripting"><array name="nodes">' + nodes
                   + "</array></dictionary></dictionary>")
    return hw1_level.load(xml)


_NODE = ('<dictionary><int name="id">{i}</int><string name="type">{t}</string><bool name="enabled">True</bool>'
         '<int name="trigger-times">-1</int><vec2 name="pos">{x} {y}</vec2>{p}{c}</dictionary>')


def test_enemy_dying_inside_counts_as_leaving(tmp_path):
    # Survival Colosseum: "enemy left the arena" runs the counter; in HW1 a death inside is a leave.
    lv = _nodes_level(tmp_path, "".join([
        _NODE.format(i=677, t="CircleShape", x=0, y=0, c="",
                     p='<dictionary name="parameters"><float name="radius">5</float></dictionary>'),
        _NODE.format(i=680, t="AreaTrigger", x=0, y=0, c='<int-arr name="connections">683</int-arr>'
                     '<int-arr name="connection-delays">0</int-arr>',
                     p='<dictionary name="parameters"><int name="event">1</int><int name="types">2</int>'
                     '<dictionary name="shape"><int-arr name="static">677</int-arr></dictionary></dictionary>'),
        _NODE.format(i=683, t="ScriptLink", x=0, y=0, c="", p=""),
    ]))
    out = Level("DungeonGameMode", None)
    ctx = ScriptContext(out, {}, lambda i: None, warn=lambda m: None)
    convert_scripts(lv.scripts, ctx)
    area, = (s for s in out.scripts if s.cls == "AreaTrigger")
    died, = (s for s in out.scripts if s.cls == "UnitDestroyedTrigger")
    assert Param("int", "Event", 2) in area.params and Param("int", "Filter", 4) in area.params
    assert Param("sources", "#Units", [(area.id, "AllInside")]) in died.params
    (link, _), = area.connections
    assert died.connections == [(link, 0)]
    target = ctx.id_map[683]
    assert next(s for s in out.scripts if s.id == link).connections == [(target, 0)]


def test_spawned_object_lands_where_a_placed_one_would(tmp_path):
    # The arena's closing wall is spawned: a_v_16 is AE's v_32, placed 16 px up.
    lv = _nodes_level(tmp_path, _NODE.format(i=1, t="SpawnObject", x=11, y=2, c="",
                                             p='<string name="parameters">doodads/theme_a/a_v_16.xml</string>'))
    r = Resolver(AllExist())  # type: ignore[arg-type]
    out = Level("DungeonGameMode", None)
    ctx = ScriptContext(out, {}, lambda i: None, warn=lambda m: None,
                        resolve_unit=lambda t: r.resolve(t).path,
                        resolve_offset=lambda t: (r.resolve(t).dx, r.resolve(t).dy))
    convert_scripts(lv.scripts, ctx)
    spawn, = out.scripts
    assert (spawn.cls, spawn.x, spawn.y) == ("SpawnUnit", 176, 16)


def test_circle_shapes_take_hw1_diameter(tmp_path):
    # HW1's CircleShape stores a diameter in tiles; the colosseum's arena is 35 across.
    lv = _nodes_level(tmp_path, _NODE.format(i=677, t="CircleShape", x=-3.25, y=1, c="",
                                             p='<dictionary name="parameters"><float name="diameter">35</float>'
                                               '<int name="types">2</int></dictionary>'))
    out = Level("DungeonGameMode", None)
    convert_scripts(lv.scripts, ScriptContext(out, {}, lambda i: None, warn=lambda m: None))
    circle, = out.units
    assert circle.path == ":Physics_Circle" and circle.state["radius"] == ("float", 280.0)


def test_max_players_option_and_kept_on_reconvert(tmp_path):
    from hw2ae.ae import scenario
    scenario.write(tmp_path, "s", "d", "levels/s/a.lvl")
    assert scenario.existing_max_players(tmp_path) == 7          # a new scenario, as AE's own
    scenario.write(tmp_path, "s", "d", "levels/s/a.lvl", 3)
    assert scenario.existing_max_players(tmp_path) == 3
    scenario.write(tmp_path, "s", "d", "levels/s/a.lvl")         # reconvert without the option
    assert '<players min="1" max="3" />' in (tmp_path / "info.xml").read_text()
