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
```

The Hammerwatch Anniversary Edition install and its `unpacked_assets_*` folder are found
automatically on any Steam library drive; override with `--ae-root` / `--ae-assets` or the
`HW2AE_AE_ROOT` / `HW2AE_AE_ASSETS` environment variables. `python -m hw2ae doctor` shows
what was found.

## Commands

| Command | What it does |
| --- | --- |
| `convert <mission>` | Convert, validate and pack. `--out`, `--name`, `--id`, `--lighting hw1\|theme`, `--no-pack`. |
| `validate <folder>` | Check a scenario folder: missing units/tilesets/prefabs/environments, broken script links, level exits to nowhere. |
| `pack <folder>` | Build `scenarios/<folder name>.h1c` from a scenario folder (e.g. after editing it in the editor). |
| `preview <level.lvl>` | Render a PNG of an AE level with AE's sprites, to eyeball alignment. |
| `learn <hw1 levels>` | Relearn HW1→AE unit correspondences from a campaign that exists in both games (see below). |

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
- **Doors** `door_<t>_<metal>_<h|v>…` → `doodads/doors/door_<h|v>_mid_<metal>_<theme>`.
- **Enemies** are matched on identical hit points (e.g. `skeleton_1_small` → `skeleton_warrior_weak`,
  `lich_3` → `skeleton_necromancer`, `tick_1_elite` → `tick_gold`).
- **Shops**: a `vendor_*` doodad becomes the matching AE shop prefab
  (`prefabs/shop_defense.pfb`, …), which carries its own shop script; HW1's `ShopArea` is
  dropped. The categories line up exactly (`def1-5`, `off1-5`, `combo1-5`, `misc1-5`).
- **Floors.** HW1 tilemaps map to AE tilesets (`a_default` → `prison_dirt` + `prison_tiles`,
  …) by measured overlap. AE picks tile variants and borders itself, so only "painted or not"
  carries over.
- **Scripts.** `LevelStart`, `AreaTrigger`, `AnnounceText`, `ToggleElement` → `ToggleScripts`,
  `ObjectEventTrigger(Destroyed)` → `UnitDestroyedTrigger`, `LevelExitArea` → `AreaTrigger` +
  `LevelExit`, `GameEnd` → `AnnounceText` + `ShowGameOver` (credits). Shapes become
  `:Physics_Rectangle` / `:Physics_Circle` areas. `RespawnPlayers` has no AE world script and
  is dropped; an unknown node is kept as a `ScriptLink` so its links still fire, and reported.
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
- Castle themes `e`–`g`, the desert themes `h`/`i`, HW1 light entries and prefabs are not
  mapped yet; they are reported, never guessed. `learn` against Temple of the Sun
  (`PACKAGER.exe -u scenarios/sun_temple.h1c -d <dir>` next to `Hammerwatch/editor/campaign2`)
  is the way to extend it.
- Verified by: the unit tests, `validate` (every path resolves in AE), and rendered previews.
  In-game behaviour (exits, shops, the win screen) still needs a playthrough.

## Tests

```
python -m pytest
```

The suite runs from small fixtures; the end-to-end test against the real AE install is
skipped when the game isn't installed.
