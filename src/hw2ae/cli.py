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


def _git_commit(folder: Path) -> str | None:
    """Short commit of the checkout holding ``folder``, with ``+changes`` if it
    has uncommitted edits; ``None`` outside a git checkout."""
    import subprocess
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=folder, capture_output=True,
                              text=True, timeout=10)
        if head.returncode != 0:
            return None
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=folder,
                               capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return head.stdout.strip() + ("+changes" if dirty.stdout.strip() else "")


def _hw2ae_desc() -> str:
    commit = _git_commit(Path(__file__).parent)
    return f"hw2ae {__version__}" + (f" ({commit})" if commit else "")


def _hw2a000ff_desc() -> str | None:
    """HW2A000FF's version and the commit it was installed from, or ``None``
    if it isn't installed."""
    import json
    from importlib import metadata
    from .hw1port import AVAILABLE
    if not AVAILABLE:
        return None
    try:
        dist = metadata.distribution("hw2a000ff")
    except metadata.PackageNotFoundError:
        return "HW2A000FF (version unknown)"
    commit = None
    try:
        info = json.loads(dist.read_text("direct_url.json") or "{}")
    except ValueError:
        info = {}
    if "vcs_info" in info:
        commit = info["vcs_info"].get("commit_id", "")[:7] or None
    elif info.get("dir_info", {}).get("editable") and info.get("url", "").startswith("file:"):
        from urllib.parse import urlparse
        from urllib.request import url2pathname
        commit = _git_commit(Path(url2pathname(urlparse(info["url"]).path)))
    return f"HW2A000FF {dist.version}" + (f" ({commit})" if commit else "")


def _hw1_source(hw1_assets: Path | None) -> str:
    from .hw1.assets_bin import cache_root
    if hw1_assets is None:
        return "NOT FOUND"
    try:
        hw1_assets.relative_to(cache_root())
        return f"{hw1_assets} (unpacked from HW1's assets.bin)"
    except ValueError:
        return str(hw1_assets)


def _missing_art_setup(hw1_assets: Path | None) -> list[str]:
    """What this machine lacks to port HW1 art AE has no counterpart for."""
    from .hw1port import AVAILABLE
    missing = []
    if not AVAILABLE:
        missing.append("HW2A000FF isn't installed: run  pip install -e .  in this repository "
                       "(it installs the pinned HW2A000FF and Pillow)")
    if hw1_assets is None:
        missing.append("Hammerwatch (HW1) wasn't found: install it from Steam, or point at it with "
                       "--hw1-assets <Hammerwatch>/assets.bin or the HW2AE_HW1_ROOT environment variable")
    try:
        import PIL  # noqa: F401
    except ImportError:
        missing.append("Pillow isn't installed (AE needs ported textures padded to power-of-two "
                       "sizes): run  pip install -e .  in this repository")
    return missing


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
    hw1_assets = config.find_hw1_assets(args.hw1_assets)
    missing = _missing_art_setup(hw1_assets)
    if missing and not args.allow_missing_art:
        print("error: this machine can't port the HW1 art AE has no counterpart for (floor buttons, "
              "pillars, ledges, exits...), so the scenario would be missing pieces:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        print("Fix the above (python -m hw2ae doctor checks it), or pass --allow-missing-art to "
              "convert without that art.", file=sys.stderr)
        return 2
    print(f"Using  : {_hw2ae_desc()}, {_hw2a000ff_desc() or 'no HW2A000FF'}")
    print(f"HW1 art: {_hw1_source(hw1_assets)}")
    print(f"Source : {src}")
    print(f"Output : {out}")
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
    print(_hw2ae_desc())
    print(f"AE install : {root or 'NOT FOUND'}")
    print(f"AE assets  : {assets or 'NOT FOUND'}")
    if assets:
        print(f"             {len(AssetIndex(assets))} files indexed")
    hw1_root = config.find_hw1_root()
    print(f"HW1 install: {hw1_root or 'NOT FOUND'}")
    hw1 = config.find_hw1_assets(args.hw1_assets)
    print(f"HW1 assets : {_hw1_source(hw1)}")
    print(f"HW2A000FF  : {_hw2a000ff_desc() or 'NOT INSTALLED'}")
    missing = _missing_art_setup(hw1)
    for m in missing:
        print(f"problem    : {m}")
    if not missing and root and assets:
        print("Ready: conversions on this machine port every piece of HW1 art AE lacks.")
    return 0 if root and assets and not missing else 1


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
                   help="players the scenario allows (default: 7, as AE's own scenarios)")
    p.add_argument("--no-pack", action="store_true", help="only write the folder; don't build scenarios/<id>.h1c")
    p.add_argument("--hw1-assets", help="HW1 stock assets, for porting art AE lacks: a folder of loose "
                   "files or HW1's assets.bin (default: <Hammerwatch>/editor/assetsExtract, else "
                   "<Hammerwatch>/assets.bin)")
    p.add_argument("--allow-missing-art", action="store_true",
                   help="convert even if this machine can't port HW1 art (HW2A000FF, Pillow or HW1 missing)")
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
    p.add_argument("--hw1-assets", help="HW1 stock assets: a folder of loose files or HW1's assets.bin")
    common(p)
    p.set_defaults(fn=cmd_doctor)

    args = ap.parse_args(argv)
    return args.fn(args)
