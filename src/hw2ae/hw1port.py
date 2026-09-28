"""Port HW1 art that has no AE counterpart, using HW2A000FF.

Some HW1 objects map to nothing AE ships: a mission's own doodads (custom walls,
retextured lamps) and stock HW1 art AE never remade (water, the castle and
desert floors, many ledges and props). Those are converted from their HW1 XML
and PNG into AE ``.unit`` / ``.tileset`` files by the HW2A000FF remake
(https://github.com/bennpham/HW2A000FF-AllPlatform-Remake) and shipped inside
the scenario, which PACKAGER packs like any other file.

Only visuals are ported: doodads and tilesets. Items and actors carry gameplay
behaviour whose parameters differ between the engines, so those stay on AE's
own units (``units.json``) or are reported as unmapped.

Ported files live under ``hw1/<scenario id>/``: asset paths share one namespace
with the base game and every other scenario. A ported unit keeps HW1's sprite
origin, so it is placed at HW1's ``tile * 16`` with no offset.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Callable

# The only materials file AE ships; it has every material name HW2A000FF uses.
AE_MATERIALS = "system/default.mats"
PORTED_ROOT = "hw1"
INSTALL_HINT = "pip install -e <path to HW2A000FF-AllPlatform-Remake>  (or: pip install hw2ae[port])"

try:
    from hw2a000ff.context import ConversionContext
    from hw2a000ff.converters import tileset as _tileset
    from hw2a000ff.converters import unit as _unit
    from hw2a000ff.errors import ConversionError
    from hw2a000ff.settings import Settings
    AVAILABLE = True
except ImportError:  # optional dependency
    AVAILABLE = False

# Heroes of Hammerwatch's materials file, as HW2A000FF (any version, with any
# output prefix) and the original C# tool write it. AE doesn't ship it.
_HWR_MATERIALS = re.compile(r'(?<=["\'>])[^"\'<>]*?system/hammerwatch\.mats(?=:)')
_ASSET_SUFFIXES = (".unit", ".tileset")


def hwr_materials_refs(text: str) -> int:
    return len(_HWR_MATERIALS.findall(text))


def normalize_materials(scenario_dir: Path) -> int:
    """Point every ``…system/hammerwatch.mats:<name>`` reference in the
    scenario's units and tilesets at AE's ``system/default.mats:<name>``.

    Covers files from any tool, not just ours: older HW2A000FF builds and the
    original C# tool always write hammerwatch.mats. Returns the files changed."""
    changed = 0
    for f in scenario_dir.rglob("*"):
        if f.suffix.lower() not in _ASSET_SUFFIXES or not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        new = _HWR_MATERIALS.sub(AE_MATERIALS, text)
        if new != text:
            f.write_text(new, encoding="utf-8", newline="")
            changed += 1
    return changed


class Porter:
    """Converts HW1 doodads and tilesets on demand; one per scenario."""

    def __init__(self, mission_root: Path, hw1_assets: Path | None, scenario_dir: Path,
                 name_id: str, warn: Callable[[str], None]):
        self.prefix = f"{PORTED_ROOT}/{name_id}/"
        self.scenario_dir = scenario_dir
        self.out_dir = scenario_dir / PORTED_ROOT / name_id
        self._warn = warn
        self._done: dict[str, str | None] = {}
        self.ported: dict[str, str] = {}  # HW1 path -> AE path, for the report
        settings = Settings(source_path=mission_root, source_fallback_path=hw1_assets,
                            output_path=self.out_dir, output_prefix=self.prefix)
        if "materials_path" in Settings.__dataclass_fields__:
            settings.materials_path = AE_MATERIALS  # older builds: normalize_materials fixes it
        self._ctx = ConversionContext(settings, on_warning=lambda m: warn(f"porting HW1 art: {m}"))

    def port_unit(self, hw1_path: str) -> str | None:
        """AE path of the ported doodad, or ``None`` if it isn't a portable doodad."""
        return self._port(hw1_path, "doodad", "unit")

    def port_tileset(self, hw1_path: str) -> str | None:
        return self._port(hw1_path, "tileset", "tileset")

    def _port(self, hw1_path: str, root_name: str, ext: str) -> str | None:
        key = hw1_path.replace("\\", "/")
        if key in self._done:
            return self._done[key]
        self._done[key] = None
        source = self._ctx.resolve_source(key)
        if source is None:
            return None
        try:
            xml = self._ctx.load_xml(source)
            root = xml.document_element
        except (ConversionError, ValueError) as e:
            self._warn(f"porting HW1 art: {key}: {e}")
            return None
        if root.name != root_name:
            return None
        target = str(PurePosixPath(key).with_suffix(f".{ext}"))
        try:
            with self._ctx.open_output(target) as writer:
                if ext == "unit":
                    _unit.convert(self._ctx, xml, writer, "doodad", PurePosixPath(target).stem, target)
                else:
                    _tileset.convert(self._ctx, xml, writer)
        except ConversionError as e:
            self._warn(f"porting HW1 art: {key}: {e}")
            return None
        ae_path = self.prefix + target
        self._done[key] = self.ported[key] = ae_path
        return ae_path


def make_porter(mission_root: Path, hw1_assets: Path | None, scenario_dir: Path, name_id: str,
                warn: Callable[[str], None]) -> Porter | None:
    if not AVAILABLE:
        warn(f"HW1 art with no AE counterpart is left out: HW2A000FF isn't installed. {INSTALL_HINT}")
        return None
    return Porter(mission_root, hw1_assets, scenario_dir, name_id, warn)
