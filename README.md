# Hammerwatch → Anniversary Edition campaign converter (`hw2ae`)

Turns a Hammerwatch 1 mission or campaign folder (for example one made by the
[random dungeon generator](https://github.com/bennpham/HammerwatchRogueLikeDungeonGeneratorRemake))
into a **playable Hammerwatch Anniversary Edition scenario** that uses AE's own
remade art, enemies, pickups and scripts.

```
python -m hw2ae convert "D:\Program Files (x86)\Steam\steamapps\common\Hammerwatch\editor\dungeon2100092937"
```

That one command:

1. converts every level listed in the mission's `levels.xml`;
2. writes an editable scenario folder to `<AE>\editor\<id>\` (open its levels in `EDITOR.exe`);
3. checks every reference against AE's assets;
4. packs `<AE>\scenarios\<id>.h1c` with AE's `PACKAGER.exe`, so the scenario is in the
   game's scenario list straight away.

## Why not `hw2a000ff`?

[`hw2a000ff`](https://github.com/Crackshell/hw2a000ff) ports Hammerwatch 1's *own* asset
files into the newer engine's format, under their old names (`doodads/theme_a/a_x_x.unit`,
`tilemaps/a_default.tileset`). The Anniversary Edition doesn't ship those files; it ships
remakes under new names (`doodads/walls/prison/x_x.unit`, `tilesets/prison_tiles.tileset`).
So a level converted that way opens in the AE editor as an empty map. This tool rewrites
every reference to AE's remade asset instead.

## Install

Python 3.10+, no required dependencies. Pillow is optional (level previews and the
scenario thumbnail).

```
pip install -e .[preview,test]     # or just run it with  python -m hw2ae  from src/
pip install -e .[port]             # optional: port HW1 art AE has no counterpart for (below)
```

The Hammerwatch Anniversary Edition install and its `unpacked_assets_*` folder are found
automatically on any Steam library drive; override with `--ae-root` / `--ae-assets` or the
`HW2AE_AE_ROOT` / `HW2AE_AE_ASSETS` environment variables. `python -m hw2ae doctor` shows
what was found.

## Commands

| Command | What it does |
| --- | --- |
| `convert <mission>` | Convert, validate and pack. `--out`, `--name`, `--id`, `--lighting hw1\|theme`, `--max-players N`, `--no-pack`. |
| `validate <folder>` | Check a scenario folder: missing units/tilesets/prefabs/environments, broken script links, level exits to nowhere. |
| `pack <folder>` | Build `scenarios/<folder name>.h1c` from a scenario folder (e.g. after editing it in the editor). |
| `preview <level.lvl>` | Render a PNG of an AE level with AE's sprites, to eyeball alignment. |
| `learn <hw1 levels>` | Relearn HW1→AE unit correspondences from a campaign that exists in both games (see below). |
`--max-players N` sets how many players the scenario allows (`<players max>` in its
`info.xml`); without it a scenario allows 7, as AE's own do. HW1 missions have no such setting.

## How it maps things

Everything was measured, not guessed. Castle Hammerwatch exists in both games: as HW1 editor
XML (`Hammerwatch/editor/campaign/levels`) and as AE's remake
(`scenarios/castle_hammerwatch/levels`). Pairing the two versions of each level gives the
correspondences and pixel offsets.

- **Coordinates.** HW1 positions are in 16-px tiles; AE's are pixels. A unit lands at
  `tile * 16 + offset`, where the offset is `AE sprite origin - HW1 sprite origin`, so the art
  stays exactly where HW1 drew it.
- **Walls** (`doodads/theme_<t>/<t>_<piece>`) map by piece onto `doodads/walls/<theme>/`.
  Themes are `a` prison, `b` armory, `c` archives, `d` chambers, plus `bonus1`–`bonus5`.
  Each theme folder names its pieces a little differently (`cap_h_w` vs `cap_w`, `h_32` vs
  `h`), so every piece has a candidate list. AE exits are 64 px wide where HW1's were 32, so
  the wall pieces and torches under an exit are removed.
- **Cover** `color_theme_<t>_<N>` → `walls/<theme>/__color_<N>`.
- **Doors** `door_<t>_<metal>_<h|v>…` are grouped per door and laid out again as an AE run
  (`cap_l, mid…, cap_r` / `cap_u, mid…, cap_d`) of `doodads/doors/door_*_<metal>_<theme>`, in
  the level's theme, with a `DoorController` taking `key_<metal>`: AE doors don't open without
  one. Horizontal runs cover the HW1 pieces' collision span; vertical runs fill the rows between
  the walls above and below.
- **Enemies** are matched on identical hit points (e.g. `skeleton_1_small` → `skeleton_warrior_weak`,
  `lich_3` → `skeleton_necromancer`, `tick_1_elite` → `tick_gold`).
  Pickups are matched on their values, not their names: HW1's `health_3` heals 75 like AE's
  `health_4`. Temple of the Sun's enemies were paired by how many of each stand on each of its
  15 levels in both games, plus shared buffs (`mummy_1` → `mummy_soldier`, `lich_desert_1`'s frost
  → `mummy_lich_ice`, the tracking towers' fire/ice/drain beams → `tower_laser_*`).
- **Bosses.** All seven HW1 bosses (queen, dragon, knight, lich, krilith, worm, anubis) map to the
  same-named AE boss and fight without level scripts. Each kind of boss in a level gets its
  own AE boss bar, titled as AE's campaigns title it. "Boss N%" / "Boss Died" become health and
  death triggers on the bosses, and the generator's multi-boss countdown (`Variable` /
  `ChangeVariable` / `CheckVariable`) becomes AE's `Variable` / `ChangeVariables` /
  `CheckVariables`.
- **Shops**: a `vendor_*` doodad becomes the matching AE shop unit
  (`doodads/generic/shop_defense.unit`, …), and HW1's `ShopArea` a `UseTrigger` on its area
  feeding an AE `ShopArea`, as Castle Hammerwatch wires them. The categories line up exactly
  (`def1-5`, `off1-5`, `combo1-5`, `misc1-5`). AE's shop prefabs aren't used: they pick their
  stock from progression flags a converted scenario never sets.
- **Floors.** HW1 tilemaps map to AE tilesets (`a_default` → `prison_dirt` + `prison_tiles`,
  …) by measured overlap. AE picks tile variants and borders itself, so only "painted or not"
  carries over.
- **Scripts.** `LevelStart`, `AreaTrigger`, `AnnounceText`, `ToggleElement` → `ToggleScripts`,
  `ObjectEventTrigger(Destroyed)` → `UnitDestroyedTrigger`, `LevelExitArea` → `AreaTrigger` +
  `LevelExit`, `GameEnd` → `AnnounceText` + `ShowGameOver` (credits). Shapes become
  `:Physics_Rectangle` / `:Physics_Circle` areas (HW1 circles give a diameter in tiles). HW1
  counts an actor dying inside an area as leaving it; AE doesn't, so an exit trigger for actors
  also watches its own `AllInside` for deaths. HW1's `Counter` counts down and `IncrementCounter`
  adds to it, which in AE is a `ModifyCounter` decrement. `RespawnPlayers` has no AE world script and
  is dropped; an unknown node is kept as a `ScriptLink` so its links still fire, and reported.
  `AllPlayersAreaTrigger` (teleporter pads) fires its targets through AE's `OnAllEntered`;
  a `LevelExitArea` with no shape becomes a lone `LevelExit` the pad executes; `PlaySound`
  takes the AE FMOD event listed in `sounds.json`. `PlayMusic` picks AE's music for the Castle
  area where HW1 plays that track (HW1 plays `act4` in the archives and `act3` in the chambers).
  `ObjectEventTrigger(Hit)` → `UnitDamagedTrigger`; AE only reports damage to units with a
  damage-taking behaviour, so a ported doodad it watches (Pirate Cove's dig spots) gets a copy
  with AE's `Breakable` behaviour and health no hit uses up.
- **Ported HW1 art.** Doodads and tilesets that map to nothing AE ships (a mission's own
  custom doodads, water, the castle/desert themes, many props) are converted from their HW1
  XML/PNG with [HW2A000FF](https://github.com/bennpham/HW2A000FF-AllPlatform-Remake) and
  shipped inside the scenario under `hw1/<id>/`, keeping the HW1 look. A placed piece's own HW1
  `layer` carries over on the scale HW2A000FF gives `defaultlayer` (HW1 20 = AE 0), so ported
  art keeps HW1's draw order (a boat's rower stays on top of the boat). AE's player is wider
  than HW1's (collision radius 5.5 vs 3.5), so the chambers bridge planks, whose rails leave a
  10-11 px walkway, get a scenario copy with each rail moved back 2 px. Custom files come from
  the mission folder, stock ones from HW1's extracted assets (`Hammerwatch/editor/assetsExtract`,
  written by HW1's `ResourceExtractor.exe`; override with `--hw1-assets`). Items and actors are
  never ported: their gameplay parameters differ between the engines. Any unit or tileset in
  the scenario pointing at Heroes of Hammerwatch's `system/hammerwatch.mats` (from any tool) is
  rewritten to AE's `system/default.mats` when packing, and `validate` flags leftovers.
- **Lighting.** AE's themed environments are dark and expect many light sources. A HW1 level
  with a bright ambient (the generator's are fully lit) gets AE's neutral lighting;
  `--lighting theme` uses the moody theme environments instead.

The tables live in [`src/hw2ae/mapping/data/`](src/hw2ae/mapping/data) and the rules in
[`resolver.py`](src/hw2ae/mapping/resolver.py). Add a line to `units.json` to map something
new; the converter lists everything it couldn't map with counts.

### AE file-format notes

- `.lvl` is SVAL text: `game-mode`, `version`, `lighting`, `tiles`, `units`, `scripts`, `prefabs`.
- A tile cell's `pos` is its **centre**; it covers `pos ± 256` px and is always a multiple of
  512 (AE floors any other `pos` onto that grid, shifting the floor by up to 511 px).
- A new game spawns at the `LevelStart` with no `StartID` param (AE's "default spawn"); HW1's
  start id `0` maps to that, and a `LevelExit` without `StartID` leads to it.
- Starting lives are fixed at 2 in AE; only the `NO_LIVES` / `DOUBLE_LIVES` / `INFINITE_LIVES`
  modifiers change them, so a HW1 `<lives>` value is reported, not converted.
- `data-rle` is a row-major presence grid of `(512 / tileset size)²` tiles stored as **signed
  byte runs**: `n > 0` painted, `n < 0` empty, capped at ±126.
- A script entry is `[class, id, vec3 pos, enabled, trigger-times, execute-on-start,
  label?, params?, [target, delay, …]?]`.
- `PACKAGER.exe -l <name>` packs `scenarios/<name>/`; `PACKAGER.exe -u <file.h1c> -d <dir>` unpacks.
  Level paths share one namespace across all scenarios, so levels go to `levels/<id>/`.

## Coverage and known gaps

- Complete for the random dungeon generator's classic themes (`a`–`d`): every asset it
  places, all its script nodes, shops and exits.
- Themes `e`–`i` are Temple of the Sun's. Pairing HW1's `campaign2` with AE's `sun_temple`
  (`PACKAGER.exe -u scenarios/sun_temple.h1c -d <dir>`, then `learn campaign2/levels --ae-levels
  <dir>/levels/sun_temple`) maps `e` (desert cave), `f` (crystal cave), `g` (pyramid) and `i`
  (fancy pyramid) walls, doors and floors to AE's remakes. `f`'s walls follow `e`'s rules (the
  same AE piece set; too few paired walls to confirm). The outdoor desert `h` keeps its floor
  mapping but its cliff walls stay ported: AE rebuilt them from different pieces.
- Every stock HW1 item and actor maps to AE, is dropped with a reason (player looks, a test
  item), or is a known gap with no AE counterpart, listed in the tests (`KNOWN_GAPS`): thrown
  bombs, furniture, the bonus letters, `lich_desert_2`, `floater_fire`, a few scripted boss props.
  Props whose HW1 states AE renamed (traps, boss locks) stay ported, so level scripts that switch
  them keep working. HW1 light entries and prefabs are not converted yet; they are reported,
  never guessed.
- Verified by: the unit tests, `validate` (every path resolves in AE), and rendered previews.
  In-game behaviour (exits, shops, the win screen) still needs a playthrough.

## Tests

```
python -m pytest
```

The suite runs from small fixtures; the end-to-end test against the real AE install is
skipped when the game isn't installed.
