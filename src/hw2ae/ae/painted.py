"""HW1 "painted map" tilesets -> AE sprite units.

A HW1 tileset maps its texture across the world: a painted cell shows the
part of the image under it, scaled by the sprite's ``scale`` (16 = one texture
pixel per world pixel). Most tilesets are a small repeating floor pattern,
which the HW1 art porter turns into an AE tileset. Some missions instead paint
a whole level as one big image (starcraft_campaign's ``part_map*.png``, up to
6272x3232, drawn at half size with ``scale="32"``). AE tilesets only hold
16-px tiles and AE textures are at most 2048 per side, so such a map becomes
the image at its drawn size, cut into pieces of at most 2048x2048, each a
sprite unit placed where HW1 draws it. The whole image is drawn; HW1 only
showed it under painted cells, which for these maps is the level itself.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# AE's texture limit (its TextureLoader: "should not be bigger than 2048").
MAX_TEXTURE = 2048
# A tileset image this big in the world is a painted level, not a pattern.
PAINTED_MIN_PX = 512
# HW1 tileset level -> AE layer, as the HW1 art porter writes tilesets.
LAYER_OFFSET = -1100


@dataclass
class Piece:
    unit: str      # scenario-relative AE unit
    x: int         # world position of its top-left corner
    y: int
    layer: int


def _frame(root: ET.Element) -> tuple[str, tuple[int, int, int, int], float] | None:
    sprite = root.find("sprite")
    if sprite is None:
        return None
    tex = (sprite.findtext("texture") or "").strip()
    frame = (sprite.findtext("frame") or "").split()
    if not tex or len(frame) != 4:
        return None
    scale = float(sprite.get("scale", "16") or 16)
    return tex, tuple(int(float(v)) for v in frame), scale  # type: ignore[return-value]


def painted_map(hw1_tileset: str, sources: list[Path], scenario_dir: Path, prefix: str) -> list[Piece] | None:
    """The pieces for a painted-map tileset, or None for an ordinary one
    (or when Pillow, the tileset or its image is missing)."""
    src = next((b / hw1_tileset for b in sources if (b / hw1_tileset).is_file()), None)
    if src is None:
        return None
    try:
        root = ET.fromstring(src.read_text(encoding="utf-8-sig", errors="replace"))
    except ET.ParseError:
        return None
    info = _frame(root) if root.tag == "tileset" else None
    if info is None:
        return None
    tex, (fx, fy, fw, fh), scale = info
    world_w, world_h = round(fw * 16 / scale), round(fh * 16 / scale)
    if max(world_w, world_h) < PAINTED_MIN_PX:
        return None
    img_path = next((b / tex for b in sources if (b / tex).is_file()), None)
    if img_path is None:
        return None
    try:
        from PIL import Image
    except ImportError:
        return None

    with Image.open(img_path) as im:
        art = im.convert("RGBA").crop((fx, fy, fx + fw, fy + fh))
    if (world_w, world_h) != art.size:
        art = art.resize((world_w, world_h), Image.LANCZOS)
    layer = int(root.get("level", "0") or 0) + LAYER_OFFSET
    stem = PurePosixPath(hw1_tileset).stem
    pieces = []
    for py in range(0, world_h, MAX_TEXTURE):
        for px in range(0, world_w, MAX_TEXTURE):
            w, h = min(MAX_TEXTURE, world_w - px), min(MAX_TEXTURE, world_h - py)
            name = f"{prefix}/painted/{stem}_{px // MAX_TEXTURE}_{py // MAX_TEXTURE}"
            png = scenario_dir / f"{name}.png"
            png.parent.mkdir(parents=True, exist_ok=True)
            art.crop((px, py, px + w, py + h)).save(png)  # padded to powers of two with the rest
            (scenario_dir / f"{name}.unit").write_text(
                '<unit slot="doodad">\n\t<scenes start="default">\n\t\t<scene name="default">\n'
                f'\t\t\t<sprite origin="0 0" texture="{name}.png" material="system/default.mats:doodad">\n'
                f'\t\t\t\t<frame>0 0 {w} {h}</frame>\n\t\t\t</sprite>\n\t\t</scene>\n\t</scenes>\n</unit>\n',
                encoding="utf-8", newline="\n")
            pieces.append(Piece(f"{name}.unit", px, py, layer))
    return pieces
