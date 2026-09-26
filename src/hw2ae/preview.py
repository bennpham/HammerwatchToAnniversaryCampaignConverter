"""Render a rough PNG of an AE level using AE's own sprites (needs Pillow).

It is not the game's renderer: each unit is drawn with the first sprite of its
first scene, tiles use the tileset's first tile with no borders, and there is
no lighting. That is enough to check that walls, floors and objects line up.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path, PurePosixPath

from .ae import level_reader
from .ae.tiles import decode_cell, tileset_size

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover
    Image = None  # type: ignore[assignment]

# A few AE textures carry extra PNG channels Pillow can't decode; it logs about
# each one but still loads the image.
logging.getLogger("PIL").setLevel(logging.ERROR)

SPRITE_RE = re.compile(
    r'<sprite\b([^>]*)>\s*(?:<frame[^>]*>\s*([-\d. ]+)\s*</frame>)', re.S)
ATTR_RE = re.compile(r'(\w[\w-]*)="([^"]*)"')


class Renderer:
    def __init__(self, assets: Path):
        if Image is None:
            raise RuntimeError("preview needs Pillow: pip install pillow")
        self.assets = Path(assets)

    # -- source text with %include/%replace resolved -----------------------
    @lru_cache(maxsize=None)
    def _text(self, rel: str) -> str:
        p = self.assets / rel
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        out: list[str] = []
        replaces: list[tuple[str, str]] = []
        for line in raw.splitlines():
            s = line.strip()
            m = re.match(r'%include\s+"([^"]+)"', s)
            if m:
                out.append(self._text(m.group(1)))
                continue
            m = re.match(r"%replace\s+(\S+)\s+(\S+)", s)
            if m:
                replaces.append((m.group(1), m.group(2)))
                continue
            if s.startswith("%"):
                continue
            out.append(line)
        text = "\n".join(out)
        for a, b in replaces:
            text = text.replace(a, b)
        return text

    @lru_cache(maxsize=None)
    def _image(self, rel: str):
        try:
            return Image.open(self.assets / rel).convert("RGBA")
        except (OSError, ValueError):
            return None

    @lru_cache(maxsize=None)
    def unit_sprite(self, unit_path: str):
        """(image, ox, oy) for the unit's first real sprite, or None."""
        text = self._text(unit_path)
        base = PurePosixPath(unit_path).parent
        for m in SPRITE_RE.finditer(text):
            attrs = dict(ATTR_RE.findall(m.group(1)))
            tex = attrs.get("texture", "")
            if not tex or "minimap" in tex:
                continue
            frame = [int(float(v)) for v in m.group(2).split()]
            if len(frame) != 4 or frame[2] <= 0 or frame[3] <= 0:
                continue
            ox, oy = (int(float(v)) for v in attrs.get("origin", "0 0").split()[:2])
            if tex == "special/empty.png":
                img = Image.new("RGBA", (frame[2], frame[3]), (8, 8, 12, 255))
                return img, ox, oy
            path = str(base / tex) if tex.startswith(".") else tex
            path = str(PurePosixPath(*[p for p in PurePosixPath(path).parts]))
            path = _normalise(path)
            atlas = self._image(path)
            if atlas is None:
                continue
            x, y, w, h = frame
            return atlas.crop((x, y, x + w, y + h)), ox, oy
        return None

    @lru_cache(maxsize=None)
    def tile_image(self, tileset: str):
        text = self._text(tileset)
        m = re.search(r'<tileset[^>]*texture="([^"]+)"', text)
        t = re.search(r"<tile>\s*([\d ]+)\s*</tile>", text)
        if not m or not t:
            return None
        atlas = self._image(m.group(1))
        if atlas is None:
            return None
        x, y, w, h = (int(v) for v in t.group(1).split())
        return atlas.crop((x, y, x + w, y + h))

    # -- level -------------------------------------------------------------
    def render(self, lvl_path: Path, out_png: Path, scale: float = 1.0, markers: bool = True) -> Path:
        img, x0, y0 = self.render_image(lvl_path)
        if markers:
            self.draw_markers(lvl_path, img, x0, y0)
        if scale != 1.0:
            img = img.resize((int(img.width * scale), int(img.height * scale)), Image.NEAREST)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_png)
        return out_png

    def render_image(self, lvl_path: Path):
        """(image, x0, y0): level pixel (x, y) is image pixel (x - x0, y - y0)."""
        lv = level_reader.load(lvl_path)
        xs, ys = [], []
        for c in lv.tiles:
            xs += [c.x - 256, c.x + 256]
            ys += [c.y - 256, c.y + 256]
        for u in lv.units:
            xs.append(u.x)
            ys.append(u.y)
        if not xs:
            raise RuntimeError("empty level")
        x0, y0 = int(min(xs)) - 64, int(min(ys)) - 96
        x1, y1 = int(max(xs)) + 64, int(max(ys)) + 64
        img = Image.new("RGBA", (x1 - x0, y1 - y0), (40, 40, 48, 255))
        draw = ImageDraw.Draw(img)

        for c in lv.tiles:
            for d in c.datasets:
                s = tileset_size(str(self.assets), d.tileset)
                tile = self.tile_image(d.tileset)
                for tx, ty, sz in decode_cell((c.x, c.y), d.tileset, d.hexdata, s):
                    px, py = int(tx - x0), int(ty - y0)
                    if tile is not None:
                        img.alpha_composite(tile.resize((sz, sz)), (px, py))
                    else:
                        draw.rectangle([px, py, px + sz - 1, py + sz - 1], fill=(90, 80, 60, 255))

        for u in sorted(lv.units, key=lambda u: u.y):
            px, py = int(u.x - x0), int(u.y - y0)
            if u.type.startswith(":Physics"):
                continue
            spr = self.unit_sprite(u.type)
            if spr is None:
                color = (220, 60, 60, 255) if u.type.startswith("actors/") else (60, 200, 90, 255)
                draw.ellipse([px - 3, py - 3, px + 3, py + 3], fill=color)
                continue
            im, ox, oy = spr
            X, Y = px - ox, py - oy
            if 0 <= X < img.width and 0 <= Y < img.height:
                img.alpha_composite(im, (X, Y))
            else:
                img.paste(im, (X, Y), im)

        return img, x0, y0

    def draw_markers(self, lvl_path: Path, img, x0: int, y0: int) -> None:
        """Overlay physics areas (magenta), prefabs (cyan) and scripts (yellow)."""
        lv = level_reader.load(lvl_path)
        draw = ImageDraw.Draw(img)
        for u in lv.units:
            if u.type.startswith(":Physics"):
                px, py = int(u.x - x0), int(u.y - y0)
                draw.rectangle([px - 8, py - 8, px + 8, py + 8], outline=(255, 0, 255, 255))
        for p in lv.prefabs:
            px, py = int(p.x - x0), int(p.y - y0)
            draw.rectangle([px - 12, py - 12, px + 12, py + 12], outline=(0, 255, 255, 255), width=2)
        for s in lv.scripts:
            px, py = int(s.x - x0), int(s.y - y0)
            draw.rectangle([px - 3, py - 3, px + 3, py + 3], fill=(255, 255, 0, 255))


def _normalise(path: str) -> str:
    parts: list[str] = []
    for p in path.replace("\\", "/").split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            if parts:
                parts.pop()
            continue
        parts.append(p)
    return "/".join(parts)
