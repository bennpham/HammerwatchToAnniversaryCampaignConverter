"""Locate the Hammerwatch and Anniversary Edition installs.

Order: explicit argument, environment variable, then the usual Steam library
folders on every drive letter."""

from __future__ import annotations

import os
import re
import string
from pathlib import Path

from .hw1.assets_bin import unpack_cached

AE_DIR = "Hammerwatch Anniversary Edition"
HW1_DIR = "Hammerwatch"
# Where HW1's ResourceExtractor.exe unpacks the stock assets (assets.bin).
HW1_EXTRACTED = r"editor\assetsExtract"
# HW1's stock assets, packed; always there when HW1 is installed.
HW1_ARCHIVE = "assets.bin"
STEAM_SUBDIRS = [
    r"Program Files (x86)\Steam\steamapps\common",
    r"Program Files\Steam\steamapps\common",
    r"SteamLibrary\steamapps\common",
    r"Steam\steamapps\common",
]


def _candidates(folder: str) -> list[Path]:
    out = []
    for drive in string.ascii_uppercase:
        for sub in STEAM_SUBDIRS:
            out.append(Path(f"{drive}:\\") / sub / folder)
    home = Path.home()
    out.append(home / ".steam/steam/steamapps/common" / folder)
    out.append(home / ".local/share/Steam/steamapps/common" / folder)
    return out


def find_ae_root(explicit: str | None = None) -> Path | None:
    for p in [explicit, os.environ.get("HW2AE_AE_ROOT")]:
        if p and Path(p).is_dir():
            return Path(p)
    for c in _candidates(AE_DIR):
        if c.is_dir():
            return c
    return None


def find_hw1_root(explicit: str | None = None) -> Path | None:
    """The Hammerwatch (HW1) install: the folder holding ``assets.bin``."""
    for p in [explicit, os.environ.get("HW2AE_HW1_ROOT")]:
        if p and Path(p).is_dir():
            return Path(p)
    for c in _candidates(HW1_DIR):
        if (c / HW1_ARCHIVE).is_file():
            return c
    return None


def find_hw1_assets(explicit: str | None = None) -> Path | None:
    """HW1's stock assets as loose files, for porting art AE never remade.

    Order: ``explicit``, ``HW2AE_HW1_ASSETS`` (a folder, or an ``assets.bin``),
    the folder HW1's ``ResourceExtractor.exe`` writes (``editor/assetsExtract``),
    then the install's ``assets.bin`` unpacked once into a per-user cache."""
    for p in [explicit, os.environ.get("HW2AE_HW1_ASSETS")]:
        if not p:
            continue
        if Path(p).is_dir():
            return Path(p)
        if Path(p).is_file():
            return unpack_cached(Path(p))
    root = find_hw1_root()
    if root is None:
        return None
    if (root / HW1_EXTRACTED).is_dir():
        return root / HW1_EXTRACTED
    if (root / HW1_ARCHIVE).is_file():
        return unpack_cached(root / HW1_ARCHIVE)
    return None


def find_ae_assets(ae_root: Path | None, explicit: str | None = None) -> Path | None:
    """The highest-numbered ``unpacked_assets_*`` folder in the AE install."""
    if explicit and Path(explicit).is_dir():
        return Path(explicit)
    env = os.environ.get("HW2AE_AE_ASSETS")
    if env and Path(env).is_dir():
        return Path(env)
    if ae_root is None:
        return None
    best: tuple[int, Path] | None = None
    for d in ae_root.glob("unpacked_assets*"):
        if d.is_dir():
            m = re.search(r"(\d+)$", d.name)
            n = int(m.group(1)) if m else 0
            if best is None or n > best[0]:
                best = (n, d)
    return best[1] if best else None
