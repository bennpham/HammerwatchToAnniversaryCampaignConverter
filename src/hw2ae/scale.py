"""HW1 sprite ``scale``: how many texture pixels make 16 world pixels.

Stock HW1 art is drawn at ``scale="16"``, one texture pixel per world pixel.
A mission can draw its art bigger or smaller (starcraft_campaign: 25, 30,
32 ...), and HW1 scales the whole doodad by 16/scale: its sprite frames,
origin and collision shape alike. Its invisible blockers (``inv_carre``,
scale 32) are half the size of their texture, and ``inv_carre_small``
(scale 90) is about 30 px across, as its name says. AE has no sprite scale,
so the art is resized and every coordinate multiplied by 16/scale.
"""

from __future__ import annotations

import re
from pathlib import Path

STOCK = 16.0


def factor(scale: float | str | None) -> float:
    try:
        s = float(scale) if scale not in (None, "") else STOCK
    except ValueError:
        return 1.0
    return STOCK / s if s > 0 else 1.0


def hw1_scale(xml_text: str) -> float:
    """The factor of a HW1 file's first sprite (16/scale; 1.0 at stock)."""
    m = re.search(r'<sprite\b[^>]*\bscale="([\d.]+)"', xml_text)
    return factor(m.group(1)) if m else 1.0


def _n(v: float) -> str:
    r = round(v, 2)
    return str(int(r)) if r == int(r) else str(r)


def scaled_texture(src: Path, f: float) -> Path | None:
    """``name@x0.5.png`` next to ``src``: the texture resized by ``f``."""
    if f == 1.0:
        return src
    try:
        from PIL import Image
    except ImportError:
        return None
    dst = src.with_name(f"{src.stem}@x{_n(f)}{src.suffix}")
    if not dst.exists():
        with Image.open(src) as im:
            w, h = im.size
            im.convert("RGBA").resize((max(1, round(w * f)), max(1, round(h * f))), Image.LANCZOS).save(dst)
    return dst


def scale_rect(text: str, f: float) -> str:
    """``x y w h`` scaled so neighbouring frames still meet."""
    x, y, w, h = (float(v) for v in text.split())
    x0, y0 = round(x * f), round(y * f)
    return f"{x0} {y0} {max(1, round((x + w) * f) - x0)} {max(1, round((y + h) * f) - y0)}"


def scale_pair(text: str, f: float, whole: bool = False) -> str:
    """Coordinates times ``f``; ``whole`` rounds them, as sprite origins are
    always whole pixels in AE's own units."""
    return " ".join(str(round(float(v) * f)) if whole else _n(float(v) * f) for v in text.split())


def scale_unit(text: str, f: float, texture: callable) -> str:
    """A unit (HW2A000FF's or our own) with every sprite and collision
    coordinate multiplied by ``f``; ``texture(path)`` gives the scaled
    texture's path for a sprite's ``texture``."""
    if f == 1.0:
        return text
    text = re.sub(r"<point>([^<]+)</point>", lambda m: f"<point>{scale_pair(m.group(1), f)}</point>", text)
    text = re.sub(r'\b(origin|offset)="([^"]+)"',
                  lambda m: f'{m.group(1)}="{scale_pair(m.group(2), f, whole=m.group(1) == "origin")}"', text)
    text = re.sub(r'\bradius="([\d.]+)"', lambda m: f'radius="{_n(float(m.group(1)) * f)}"', text)
    text = re.sub(r"(<frame\b[^>]*>)([^<]+)(</frame>)", lambda m: m.group(1) + scale_rect(m.group(2), f) + m.group(3),
                  text)
    return re.sub(r'\btexture="([^"]+)"', lambda m: f'texture="{texture(m.group(1))}"', text)
