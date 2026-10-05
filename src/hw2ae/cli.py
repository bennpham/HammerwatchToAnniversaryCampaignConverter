"""Command line: ``python -m hw2ae <command>``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, config
from .ae.assets import AssetIndex


def _paths(args) -> tuple[Path, Path]:
    root = config.find_ae_root(args.ae_root)
    assets = config.find_ae_assets(root, args.ae_assets)
    if root is None or assets is None:
        sys.exit("error: could not find Hammerwatch Anniversary Edition and its unpacked_assets_* folder; "
                 "pass --ae-root (and --ae-assets if the assets live elsewhere)")
    return root, assets


def cmd_convert(args) -> int:
    from .ae.scenario import slug
    from .convert import Options, convert
    from .hw1.campaign import load as load_campaign
    from .validate import validate

    root, assets = _paths(args)
    src = Path(args.source)
    if not src.is_dir():
        sys.exit(f"error: {src} is not a folder")
    name_id = args.id or slug(args.name or load_campaign(src).name)
    out = Path(args.out) if args.out else root / "editor" / name_id
    print(f"Source : {src}")
    print(f"Output : {out}")
    hw1_assets = config.find_hw1_assets(args.hw1_assets)
    report = convert(Options(source=src, out=out, ae_assets=assets, name=args.name, name_id=name_id,
                             lighting=args.lighting, hw1_assets=hw1_assets, max_players=args.max_players))

    print()
    print(f"Converted {report.levels} level(s): {report.units} units, {report.scripts} scripts.")
    if report.unmapped:
        print(f"\nNot converted ({sum(report.unmapped.values())} objects with no AE equivalent yet):")
        for t, n in report.unmapped.most_common():
            print(f"  {n:6d}  {t}")
    if report.unmapped_tilesets:
        print("\nTilesets with no AE equivalent yet (floor left empty):")
        for t, n in report.unmapped_tilesets.most_common():
            print(f"  {n:6d} tiles  {t}")
    if report.ported:
        print(f"\nPorted from HW1 art (no AE equivalent to map to; files under hw1/{name_id}/):")
        for t, n in report.ported.most_common():
            print(f"  {n:6d}  {t}")
    if report.dropped:
        print("\nLeft out on purpose:")
        for t, n in report.dropped.most_common():
            print(f"  {n:6d}  {t}")
    if report.players:
        print("\nClasses and lives (HW1 tweak/ and info.xml -> AE players/ and scripts/ in the scenario):")
        for line in report.players:
            print(f"  {line}")
    for w in report.warnings:
        print(f"warning: {w}")

    problems, _ = validate(out, AssetIndex(assets))
    if problems:
        print(f"\nValidation found {len(problems.items)} problem(s):")
        for p in problems.items[:50]:
            print(f"  {p}")
        return 1
    print("\nValidation passed: every unit, tileset, prefab and level exit resolves in AE.")
    print(f"Edit the levels with {root / 'EDITOR.exe'} from {out / 'levels'}.")
    if args.no_pack:
        print("Skipped packing (--no-pack); run `hw2ae pack` when you want to play it.")
        return 0
    return _pack(root, out)


def _pack(root: Path, folder: Path) -> int:
    from .hw1port import normalize_materials, pad_textures
    from .pack import pack
    # Units from any tool (older HW2A000FF, the original C# one, hand-copied)
    # point at HoH's system/hammerwatch.mats, which AE doesn't ship.
    fixed = normalize_materials(folder)
    if fixed:
        print(f"Pointed {fixed} unit/tileset file(s) at AE's system/default.mats.")
    padded = pad_textures(folder)
    if padded:
        print(f"Padded {padded} ported texture(s) to power-of-two sizes for AE.")
    try:
        h1c = pack(root, folder)
    except RuntimeError as e:
        print(f"error: {e}")
        return 1
    print(f"\nPacked {h1c}\nStart Hammerwatch Anniversary Edition: it is in the scenario list.")
    return 0


def cmd_preview(args) -> int:
    from .preview import Renderer
    _, assets = _paths(args)
    src = Path(args.level)
    out = Path(args.out) if args.out else src.with_suffix(".png")
    Renderer(assets).render(src, out, scale=args.scale)
    print(f"Wrote {out}")
    return 0


def cmd_pack(args) -> int:
    root, _ = _paths(args)
    return _pack(root, Path(args.folder))


def cmd_validate(args) -> int:
    from .validate import validate
    _, assets = _paths(args)
    problems, n = validate(Path(args.folder), AssetIndex(assets))
    for p in problems.items:
        print(p)
    print(f"{n} level(s) checked, {len(problems.items)} problem(s).")
    return 1 if problems else 0


def cmd_learn(args) -> int:
    from .mapping import learn
    root, _ = _paths(args)
    hw1 = Path(args.hw1_levels)
    ae = Path(args.ae_levels) if args.ae_levels else root / "scenarios" / "castle_hammerwatch" / "levels"
    result = learn.learn(hw1, ae)
    learn.write(result, Path(args.out))
    print(f"Wrote {args.out} ({len(result['types'])} HW1 types).")
    return 0


def cmd_doctor(args) -> int:
    root = config.find_ae_root(args.ae_root)
    assets = config.find_ae_assets(root, args.ae_assets) if root else None
    print(f"hw2ae {__version__}")
    print(f"AE install : {root or 'NOT FOUND'}")
    print(f"AE assets  : {assets or 'NOT FOUND'}")
    if assets:
        print(f"             {len(AssetIndex(assets))} files indexed")
    from .hw1port import AVAILABLE, INSTALL_HINT
    hw1 = config.find_hw1_assets(args.hw1_assets)
    print(f"HW1 assets : {hw1 or 'NOT FOUND (stock HW1 art AE lacks is not ported; run HW1 ResourceExtractor.exe)'}")
    print(f"HW2A000FF  : {'installed' if AVAILABLE else 'NOT INSTALLED (' + INSTALL_HINT + ')'}")
    return 0 if root and assets else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hw2ae", description="Convert Hammerwatch 1 missions and campaigns into "
                                 "Hammerwatch Anniversary Edition scenarios.")
    ap.add_argument("--version", action="version", version=f"hw2ae {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--ae-root", help="Hammerwatch Anniversary Edition install folder (auto-detected)")
        p.add_argument("--ae-assets", help="AE unpacked_assets_* folder (auto-detected)")

    p = sub.add_parser("convert", help="convert an HW1 mission/campaign folder and pack it for play")
    p.add_argument("source", help="HW1 mission folder (the one holding levels.xml and levels/)")
    p.add_argument("--out", help="scenario folder to write (default: <AE>/editor/<id>)")
    p.add_argument("--name", help="scenario title (default: <name> from the mission's info.xml)")
    p.add_argument("--id", help="scenario id: folder, .h1c and level-path name (default: slug of the name)")
    p.add_argument("--lighting", choices=("hw1", "theme"), default="hw1",
                   help="hw1: keep the mission's brightness (default); theme: AE's darker theme lighting")
    p.add_argument("--max-players", type=int, metavar="N",
                   help="players the scenario allows (default: keep the existing info.xml's, else 7 as AE's own scenarios)")
    p.add_argument("--no-pack", action="store_true", help="only write the folder; don't build scenarios/<id>.h1c")
    p.add_argument("--hw1-assets", help="HW1 stock assets as loose files, for porting art AE lacks "
                   "(default: <Hammerwatch>/editor/assetsExtract)")
    common(p)
    p.set_defaults(fn=cmd_convert)

    p = sub.add_parser("preview", help="render a PNG of an AE level with AE's sprites (needs Pillow)")
    p.add_argument("level", help="an AE .lvl file")
    p.add_argument("--out", help="PNG to write (default: next to the level)")
    p.add_argument("--scale", type=float, default=1.0)
    common(p)
    p.set_defaults(fn=cmd_preview)

    p = sub.add_parser("validate", help="check a scenario folder against AE's assets")
    p.add_argument("folder")
    common(p)
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("pack", help="build scenarios/<name>.h1c from a scenario folder with PACKAGER.exe")
    p.add_argument("folder")
    common(p)
    p.set_defaults(fn=cmd_pack)

    p = sub.add_parser("learn", help="relearn HW1->AE unit mappings from a campaign that exists in both games")
    p.add_argument("hw1_levels", help="HW1 levels folder, e.g. Hammerwatch/editor/campaign/levels")
    p.add_argument("--ae-levels", help="matching AE levels folder (default: castle_hammerwatch)")
    p.add_argument("--out", default="learned.json")
    common(p)
    p.set_defaults(fn=cmd_learn)

    p = sub.add_parser("doctor", help="show which installs were found")
    p.add_argument("--hw1-assets", help="HW1 stock assets as loose files")
    common(p)
    p.set_defaults(fn=cmd_doctor)

    args = ap.parse_args(argv)
    return args.fn(args)
