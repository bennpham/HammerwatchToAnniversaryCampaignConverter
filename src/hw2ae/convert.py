"""Convert an HW1 campaign/mission folder into an AE scenario folder."""

from __future__ import annotations

import collections
import dataclasses
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .ae import doors, level_writer, scenario
from .ae.assets import AssetIndex
from .ae.model import Level, PrefabPlacement, Unit
from .ae.scripts import ScriptContext, convert_scripts
from .ae.tiles import TileLayers
from .hw1 import campaign as hw1_campaign
from .hw1 import level as hw1_level
from .mapping.resolver import Dropped, Placement, Resolver

TILE = 16
DEFAULT_ENV = "effects/lighting/prison_1.env"
# AE's theme environments are dark and rely on dozens of light units. A HW1
# level lit by a bright ambient instead gets AE's neutral, fully lit setup.
BRIGHT_ENV = "effects/lighting/bonus.env"
BRIGHT_AMBIENT = 128
EXIT_WIDTH = 64
# AE hard-codes the party's starting lives (PartyRecord.as); only the
# NO_LIVES / DOUBLE_LIVES / INFINITE_LIVES modifiers change them.
AE_LIVES = 2


@dataclass
class Report:
    levels: int = 0
    units: int = 0
    scripts: int = 0
    unmapped: collections.Counter = field(default_factory=collections.Counter)
    unmapped_tilesets: collections.Counter = field(default_factory=collections.Counter)
    dropped: collections.Counter = field(default_factory=collections.Counter)
    warnings: list[str] = field(default_factory=list)

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)


@dataclass
class Options:
    source: Path
    out: Path
    ae_assets: Path
    name: str | None = None
    name_id: str | None = None
    lighting: str = "hw1"  # "hw1": keep HW1's brightness; "theme": AE's theme environments
    log: object = print


def _level_ae_path(name_id: str, res: str) -> str:
    """HW1 ``levels/level0.xml`` -> AE ``levels/<scenario>/level0.lvl``.

    Level paths share one namespace with every other scenario and the base
    game, so each scenario gets its own folder (Temple of the Sun does the same).
    """
    stem = PurePosixPath(res.replace("\\", "/")).stem
    return f"levels/{name_id}/{stem}.lvl"


def _dominant_theme(lv: hw1_level.Level, resolver: Resolver) -> str | None:
    counts: collections.Counter[str] = collections.Counter()
    for o in lv.doodads:
        t = resolver.theme_of(o.type)
        if t:
            counts[t] += 1
    return counts.most_common(1)[0][0] if counts else None


def _environment(lv: hw1_level.Level, theme: str | None, resolver: Resolver, lighting: str) -> str:
    theme_env = resolver.themes.get(theme, {}).get("env", DEFAULT_ENV) if theme else DEFAULT_ENV
    if lighting == "theme":
        return theme_env
    amb = lv.ambient
    if amb is not None and sum(amb[:3]) / 3 >= BRIGHT_AMBIENT and not lv.lights:
        return BRIGHT_ENV
    return theme_env


def convert_level(lv: hw1_level.Level, resolver: Resolver, ae_assets: Path, report: Report,
                  level_path_for, lighting: str = "hw1") -> Level:
    theme = _dominant_theme(lv, resolver)
    out = Level(game_mode="DungeonGameMode", environment=_environment(lv, theme, resolver, lighting))
    if lv.lights:
        report.warn(f"{lv.path.name}: {len(lv.lights)} HW1 light(s) not converted yet; "
                    "the level uses AE's theme lighting instead")

    # -- tiles -------------------------------------------------------------
    tl = TileLayers(ae_assets)
    for hw1_ts, layer in lv.layers.items():
        targets = resolver.tileset(hw1_ts)
        if not targets:
            report.unmapped_tilesets[hw1_ts] += len(layer.tiles)
            continue
        tiles16 = set(layer.tiles)
        for ts in targets:
            tl.paint16(ts, tiles16)
    out.tile_cells = tl.cells()

    # -- units and prefabs -------------------------------------------------
    id_map: dict[int, int] = {}
    exits: list[Unit] = []
    door_pieces = []
    for o in lv.all_objects():
        r = resolver.resolve(o.type)
        if r is None:
            report.unmapped[o.type] += 1
            continue
        if isinstance(r, Dropped):
            report.dropped[f"{o.type} ({r.reason})"] += 1
            continue
        assert isinstance(r, Placement)
        if r.door is not None:
            door_pieces.append((o, r.door))
            continue
        x, y = o.x * TILE + r.dx, o.y * TILE + r.dy
        if r.kind == "prefab":
            out.prefabs.append(PrefabPlacement(r.path, x, y))
            continue
        uid = out.new_id()
        id_map[o.id] = uid
        state = {k: ("string", v) for k, v in r.params.items()}
        u = Unit(r.path, x, y, uid, state)
        out.units.append(u)
        if r.is_exit:
            exits.append(u)

    _clear_exit_spans(out, exits)
    doors.convert(_level_themed(door_pieces, theme, resolver), lv.doodads, out, id_map)

    # -- scripts -----------------------------------------------------------
    ctx = ScriptContext(level=out, id_map=id_map, level_path_for=level_path_for, warn=report.warn)
    convert_scripts(lv.scripts, ctx)

    report.units += len(out.units)
    report.scripts += len(out.scripts)
    return out


def _level_themed(pieces, theme: str | None, resolver: Resolver):
    """HW1 reuses the prison doors (``door_a_*``) in every theme; AE's remake
    gives each door its level's theme, so do the same when AE has that door."""
    door_theme = resolver.themes.get(theme, {}).get("doors") if theme else None
    if not door_theme:
        return pieces
    out = []
    for o, d in pieces:
        themed = dataclasses.replace(d, theme=door_theme)
        out.append((o, themed if resolver.assets.exists(themed.ae_path("mid")) else d))
    return out


def _clear_exit_spans(level: Level, exits: list[Unit]) -> None:
    """AE exits are 64 px wide where HW1's were 32, so they cover the wall
    piece on each side of the old opening (and the torches hung there)."""
    if not exits:
        return
    doomed: set[int] = set()
    for e in exits:
        folder = e.path.rsplit("/", 1)[0] + "/"
        for u in level.units:
            if u is e or u.id in doomed:
                continue
            if not (e.x <= u.x < e.x + EXIT_WIDTH):
                continue
            name = u.path.rsplit("/", 1)[-1]
            is_wall = u.path.startswith(folder) and not name.startswith(("__color", "exit_"))
            is_torch = u.path.startswith("doodads/generic/lamps/torch")
            if is_wall and abs(u.y - e.y) <= 8:
                doomed.add(u.id)
            elif is_torch and 0 <= u.y - e.y <= 32:
                doomed.add(u.id)
    level.units[:] = [u for u in level.units if u.id not in doomed]


def convert(opts: Options) -> Report:
    t0 = time.perf_counter()
    log = opts.log
    report = Report()
    camp = hw1_campaign.load(opts.source)
    name = opts.name or camp.name
    name_id = opts.name_id or scenario.slug(name)

    log(f"Indexing AE assets in {opts.ae_assets} ...")
    assets = AssetIndex(opts.ae_assets)
    resolver = Resolver(assets)

    paths = {lv.id: _level_ae_path(name_id, lv.res) for lv in camp.levels}

    def level_path_for(level_id: str) -> str | None:
        return paths.get(level_id)

    for entry in camp.levels:
        src = camp.root / entry.res
        if not src.exists():
            report.warn(f"level '{entry.id}' file is missing: {src}")
            continue
        lv = hw1_level.load(src)
        ae_level = convert_level(lv, resolver, opts.ae_assets, report, level_path_for, opts.lighting)
        dst = opts.out / paths[entry.id]
        level_writer.write(ae_level, dst)
        report.levels += 1
        log(f"  {entry.res} -> {paths[entry.id]}  ({len(ae_level.units)} units, "
            f"{len(ae_level.scripts)} scripts, {len(ae_level.tile_cells)} tile cells)")

    if camp.lives is not None and camp.lives != AE_LIVES:
        hint = " (tick the 'No lives' modifier for 0)" if camp.lives == 0 else ""
        report.warn(f"the HW1 mission sets {camp.lives} lives; AE always starts with {AE_LIVES}{hint}")

    start = paths.get(camp.start) or (next(iter(paths.values())) if paths else "")
    desc = camp.description or f"Converted from the Hammerwatch mission '{camp.name}'."
    scenario.write(opts.out, name, desc, start)
    _write_logo(camp.root, opts.out, start, opts.ae_assets, report)

    log(f"Done in {time.perf_counter() - t0:.1f}s")
    return report


def _write_logo(src: Path, out: Path, start_lvl: str, ae_assets: Path, report: Report) -> None:
    """``logos.png`` is the 128×128 picture in the scenario list: the HW1
    campaign's icon if it has one, otherwise a render of the start room."""
    icon = src / "icon.png"
    if icon.exists():
        shutil.copyfile(icon, out / "logos.png")
        return
    try:
        from .preview import Renderer
        from PIL import Image
    except ImportError:
        report.warn("no logos.png written (install Pillow for a thumbnail of the start room)")
        return
    lvl = out / start_lvl
    if not lvl.exists():
        return
    try:
        img, x0, y0 = Renderer(ae_assets).render_image(lvl)
        cx, cy = _start_point(lvl)
        px, py = int(cx - x0), int(cy - y0)
        box = (px - 128, py - 160, px + 128, py + 96)
        img.crop(box).resize((128, 128), Image.LANCZOS).save(out / "logos.png")
    except Exception as e:  # a missing thumbnail must never fail a conversion
        report.warn(f"could not render logos.png: {e}")


def _start_point(lvl: Path) -> tuple[float, float]:
    from .ae import level_reader
    lv = level_reader.load(lvl)
    for s in lv.scripts:
        if s.cls == "LevelStart":
            return s.x, s.y
    return (lv.units[0].x, lv.units[0].y) if lv.units else (0.0, 0.0)
