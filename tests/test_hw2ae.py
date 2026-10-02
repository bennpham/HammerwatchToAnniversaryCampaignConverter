from __future__ import annotations

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
    assert start.execute_on_start and start.connections == [(music.id, 3521)]
    assert Param("int", "Music", 4) in music.params          # act4: chambers
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
    assert any("sets 0 lives" in w for w in report.warnings)
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
