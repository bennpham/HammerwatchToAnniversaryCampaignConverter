"""A mission's own items and actors, kept with their HW1 look.

A custom HW1 item or actor is a stock behaviour with new art and numbers: a
``food`` item that heals 10, a skeleton spawner with another rate, a skeleton
captain that whirls and buffs like the stock miniboss. AE needs a unit with
AE's behaviour, so each one becomes a copy of its AE *twin*, the AE unit that
behaves the same way, with the twin's sprites swapped for the mission's and the
author's hit points kept. The copy lives in the scenario under
``hw1/<scenario id>/``, like ported doodads.

Twins:
* items and spawners: the stock HW1 object with the same behaviour and values
  (heal amount, spawned enemies), through ``units.json``;
* a custom ``collectable``: AE's hidden walk-over collectable (``sphere``);
* actors: the AE unit that fights the same way (movement, skills, buffs),
  preferring the same kind of creature (``skeleton`` in both names).
"""

from __future__ import annotations

import math
import re
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Callable

from .. import scale

PORTED_ROOT = "hw1"
COLLECTABLE_TWIN = "items/pickups/collectable_10_sphere.unit"
# The stock spawner whose AE unit carries a mission spawner's own spawns.
GENERIC_SPAWNER = "actors/spawners/skeleton_1.xml"
# AE units that are not enemies to copy: projectiles, bombs, effects, NPCs.
_NOT_ENEMIES = ("projectile", "_bomb", "bomb_", "effect", "/npcs/", "_spawn", "particle")
# AE's 8-direction scenes: index 0 faces east, then clockwise (AE's y points
# down); AnimString.GetSceneName rounds the facing angle to these.
AE_DIRS = ("east", "southeast", "south", "southwest", "west", "northwest", "north", "northeast")
# AE skill/feature words, matched in a unit and the files it includes.
_FEATURES = ("whirlwind", "bloodlust", "nova", "summon", "blink", "charge", "projectile", "melee", "heal", "teleport")


@dataclass
class Made:
    path: str           # the scenario's unit
    twin: str           # the AE unit it copies
    note: str           # what was kept from HW1


def _hw1_xml(path: Path) -> ET.Element | None:
    try:
        return ET.fromstring(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (ET.ParseError, OSError):
        return None


def _entries(root: ET.Element) -> dict[str, str]:
    """HW1 behaviour values: ``<entry name=x><int>..`` and ``<int name=x>``."""
    out: dict[str, str] = {}
    beh = root.find("behavior")
    d = beh.find("dictionary") if beh is not None else None
    for c in (d if d is not None else []):
        if c.tag == "entry" and len(c):
            out[c.get("name", "")] = (c[0].text or "").strip()
        elif c.get("name") and not len(c):
            out[c.get("name")] = (c.text or "").strip()
    return out


def _spawns(root: ET.Element) -> list[str]:
    arr = next((a for a in root.iter("array") if a.get("name") == "spawns"), None)
    return sorted((s.text or "").strip() for s in arr.iter("string")) if arr is not None else []


def _hw1_features(root: ET.Element) -> tuple[str, set[str]]:
    move = next((s.text for d in root.iter("dictionary") if d.get("name") == "movement"
                 for s in d if s.get("name") == "type"), "") or ""
    feats: set[str] = set()
    for d in root.iter("dictionary"):
        kind = next((s.text for s in d if s.get("name") == "type"), None)
        if kind is None or d.get("name") == "movement":
            continue
        kind = kind.lower()
        if "whirl" in kind:
            feats.add("whirlwind")
        if "nova" in kind:
            feats.add("nova")
        if kind in ("hit", "melee"):
            feats.add("melee")
        if kind in ("ranged", "shoot", "spray"):
            feats.add("projectile")
        for f in ("summon", "blink", "charge", "heal", "teleport"):
            if f in kind:
                feats.add(f)
        buff = next((s.text for s in d if s.get("name") == "buff"), "") or ""
        if "bloodlust" in buff:
            feats.add("bloodlust")
    return move.lower(), feats


def _first(sprites: dict[str, ET.Element], *names: str) -> ET.Element | None:
    """The first of ``names`` that has a sprite (an Element's truth value is
    whether it has children, so ``a or b`` won't do)."""
    return next((sprites[n] for n in names if n in sprites), None)


def _numbers(root: ET.Element) -> dict[str, float]:
    out = {}
    for k, v in _entries(root).items():
        try:
            out[k] = float(v)
        except ValueError:
            pass
    return out


def _closing(text: str, pos: int, tag: str) -> int | None:
    """Index just past the ``</tag>`` closing an element opened before ``pos``."""
    depth = 1
    for m in re.finditer(rf"<{tag}\b[^>]*?(/?)>|</{tag}>", text[pos:]):
        if m.group(0) == f"</{tag}>":
            depth -= 1
        elif not m.group(1):
            depth += 1
        if depth == 0:
            return pos + m.end()
    return None


def _cut_array(text: str, name: str) -> str:
    """``text`` with the named array emptied (nested arrays included)."""
    start = text.find(f'<array name="{name}">')
    if start < 0:
        return text
    i, depth = start + 1, 1
    for m in re.finditer(r"<array\b[^>]*?(/?)>|</array>", text[start + 1:]):
        if m.group(0) == "</array>":
            depth -= 1
        elif not m.group(1):
            depth += 1
        if depth == 0:
            end = start + 1 + m.end()
            return text[:start] + f'<array name="{name}"></array>' + text[end:]
    return text


def _sprites(root: ET.Element) -> dict[str, ET.Element]:
    out: dict[str, ET.Element] = {}
    for s in root.iter("sprite"):
        out.setdefault(s.get("name", "default"), s)
    return out


class CustomUnits:
    def __init__(self, mission_root: Path, hw1_assets: Path | None, ae_assets: Path, scenario_dir: Path,
                 name_id: str, units: dict, warn: Callable[[str], None]) -> None:
        self.mission = mission_root
        self.hw1 = hw1_assets
        self.ae = ae_assets
        self.out = scenario_dir
        self.prefix = f"{PORTED_ROOT}/{name_id}"
        self.units = units
        self.warn = warn
        self.made: dict[str, Made | None] = {}
        self._painted: dict[str, list | None] = {}

    # -- entry point -----------------------------------------------------------
    def unit(self, hw1_type: str) -> str | None:
        """The scenario's AE unit for a mission-made item/actor, or None."""
        key = hw1_type.replace("\\", "/")
        if key not in self.made:
            self.made[key] = self._make(key)
        m = self.made[key]
        return m.path if m else None

    def player_unit(self, hw1_player: str, ae_unit: str) -> str | None:
        """AE's player unit with a mission's player look (``actors/player/
        <class>_a.xml``): the same class, the mission's sprites. HW1's _b/_c/_d
        are the other players' colours; AE tints one sprite set instead."""
        root = _hw1_xml(self.mission / hw1_player)
        if root is None or not (self.ae / ae_unit).is_file():
            return None
        text = (self.ae / ae_unit).read_text(encoding="utf-8")
        text = text.replace('="./', f'="{PurePosixPath(ae_unit).parent}/')
        return self._actor_scenes(text, _sprites(root))

    def painted(self, hw1_tileset: str) -> list | None:
        """Sprite pieces for a painted-map tileset (see ``painted.py``)."""
        from .painted import painted_map
        if hw1_tileset not in self._painted:
            sources = [b for b in (self.mission, self.hw1) if b is not None]
            self._painted[hw1_tileset] = painted_map(hw1_tileset, sources, self.out, self.prefix)
        return self._painted[hw1_tileset]

    def boss_title(self, hw1_type: str) -> str | None:
        """A boss bar title for a mission actor flagged ``boss-hp``."""
        src = self.mission / hw1_type
        root = _hw1_xml(src) if src.is_file() else None
        if root is None or _entries(root).get("boss-hp", "").lower() != "true":
            return None
        words = [w for w in PurePosixPath(hw1_type).stem.split("_") if len(w) > 3]
        return " ".join(w.capitalize() for w in words) or "Boss"

    # -- twins -----------------------------------------------------------------
    def _make(self, key: str) -> Made | None:
        src = self.mission / key
        if not src.is_file():
            return None
        root = _hw1_xml(src)
        if root is None or root.tag not in ("item", "actor"):
            return None
        behavior = root.get("behavior", "")
        if root.tag == "item":
            twin = self._item_twin(root, behavior)
        elif behavior == "spawner":
            # Any AE spawner carries the mission's own spawns (_write_spawner).
            twin = self._spawner_twin(root) or self.units.get(GENERIC_SPAWNER, {}).get("ae")
        elif behavior == "composite":
            twin = self._actor_twin(key, root)
        else:
            # HW1's simple behaviours (melee, ranged...): the stock actor with
            # the same behaviour and the nearest numbers.
            twin = self._simple_twin(root, behavior) or self._actor_twin(key, root)
        if twin is None:
            self.warn(f"custom {root.tag} '{key}' ({behavior}): no AE unit behaves like it; not converted")
            return None
        return self._write(key, root, twin)

    def _stock_twin(self, kind: str, match: Callable[[ET.Element], bool]) -> str | None:
        if self.hw1 is None:
            return None
        for f in sorted((self.hw1 / kind).rglob("*.xml")):
            rel = f.relative_to(self.hw1).as_posix()
            entry = self.units.get(rel)
            if not isinstance(entry, dict) or "ae" not in entry:
                continue
            r = _hw1_xml(f)
            if r is not None and match(r):
                return entry["ae"]
        return None

    def _item_twin(self, root: ET.Element, behavior: str) -> str | None:
        if behavior == "collectable":
            return COLLECTABLE_TWIN
        mine = {k: v for k, v in _entries(root).items() if re.fullmatch(r"-?[\d.]+", v)}
        return self._stock_twin("items", lambda r: r.get("behavior") == behavior and
                                {k: v for k, v in _entries(r).items() if re.fullmatch(r"-?[\d.]+", v)} == mine)

    def _spawner_twin(self, root: ET.Element) -> str | None:
        mine = _spawns(root)
        if not mine:
            return None
        return self._stock_twin("actors", lambda r: r.get("behavior") == "spawner" and _spawns(r) == mine)

    def _simple_twin(self, root: ET.Element, behavior: str) -> str | None:
        if self.hw1 is None:
            return None
        mine = _numbers(root)
        best, best_d = None, None
        for f in sorted((self.hw1 / "actors").rglob("*.xml")):
            rel = f.relative_to(self.hw1).as_posix()
            entry = self.units.get(rel)
            if not isinstance(entry, dict) or "ae" not in entry:
                continue
            r = _hw1_xml(f)
            if r is None or r.get("behavior") != behavior:
                continue
            theirs = _numbers(r)
            shared = [k for k in ("hp", "speed", "dmg", "range") if k in mine and k in theirs]
            d = sum(abs(math.log(max(mine[k], 0.01) / max(theirs[k], 0.01))) for k in shared) + 4 - len(shared)
            if best_d is None or d < best_d:
                best, best_d = entry["ae"], d
        return best

    def _resolve(self, hw1_type: str) -> str | None:
        """AE unit for a HW1 object a custom unit refers to (a spawn)."""
        if (self.mission / hw1_type).is_file():
            return self.unit(hw1_type)
        entry = self.units.get(hw1_type)
        return entry["ae"] if isinstance(entry, dict) and "ae" in entry else None

    def _actor_twin(self, key: str, root: ET.Element) -> str | None:
        move, feats = _hw1_features(root)
        hp = float(_entries(root).get("hp", "0") or 0)
        words = {w for w in re.split(r"[_\-/]", PurePosixPath(key).stem.lower()) if len(w) > 3}
        best, best_score = None, None
        for path, (amove, afeats, ahp) in _ae_actors(self.ae).items():
            score = 3 * len(feats & afeats) - len(feats ^ afeats)
            score += 2 if move and move in amove else 0
            score += 3 if any(w in path for w in words) else 0
            score += 1 if "/hammerwatch/" in path else 0
            key_ = (score, -abs(ahp - hp))
            if best_score is None or key_ > best_score:
                best, best_score = path, key_
        return best

    # -- writing -----------------------------------------------------------------
    def _write(self, key: str, root: ET.Element, twin: str) -> Made:
        text = (self.ae / twin).read_text(encoding="utf-8")
        # "./x.png" means next to the twin; the copy lives elsewhere.
        text = text.replace('="./', f'="{PurePosixPath(twin).parent}/')
        sprites = _sprites(root)
        notes = []
        if root.tag == "actor":
            text = self._actor_scenes(text, sprites)
            collision = self._hw1_collision(root)
            if collision is not None:  # a static object: HW1's own shape
                text = re.sub(r"<collision\b[^>]*>.*?</collision>", lambda m: collision, text, flags=re.S)
            if root.get("behavior") == "spawner":
                text = self._write_spawner(key, root, text, notes)
            hp = _entries(root).get("hp")
            if hp and re.search(r'<int name="hp">\d+</int>', text):
                text = re.sub(r'<int name="hp">\d+</int>', f'<int name="hp">{int(float(hp))}</int>', text, count=1)
                notes.append(f"hp {int(float(hp))}")
        else:
            text = self._item_scenes(text, sprites)
            if twin == COLLECTABLE_TWIN:
                text = self._collectable_text(text, _entries(root).get("pickup-text", ""))
        out_rel =f"{self.prefix}/{PurePosixPath(key).with_suffix('.unit')}"
        dst = self.out / out_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8", newline="\n")
        return Made(out_rel, twin, ", ".join(["HW1 sprites"] + notes))

    def _write_spawner(self, key: str, root: ET.Element, text: str, notes: list[str]) -> str:
        """An AE spawner fires a projectile whose SpawnUnit picks the unit
        (weights out of 1000, as HW1's). The copy fires its own projectile
        with the mission's spawns; with none, it is only a destructible."""
        spawns = []
        arr = next((a for a in root.iter("array") if a.get("name") == "spawns"), None)
        items = list(arr) if arr is not None else []
        for w, s in zip(items[0::2], items[1::2]):
            unit = self._resolve((s.text or "").strip())
            if unit is None:
                self.warn(f"custom spawner '{key}': spawn '{(s.text or '').strip()}' has no AE unit; left out")
                continue
            spawns.append((int(float(w.text or 0)), unit))
        if not spawns:
            notes.append("spawns nothing (as in HW1)")
            return _cut_array(text, "skills")
        total = sum(w for w, _ in spawns) or 1
        weights = [max(1, round(w * 1000 / total)) for w, _ in spawns]
        listing = "".join(f"\n\t\t\t\t\t<int>{w}</int><string>{u}</string>" for w, (_, u) in zip(weights, spawns))

        def projectile(m: re.Match) -> str:
            src = m.group(1)
            if not (self.ae / src).is_file():
                return m.group(0)
            proj = (self.ae / src).read_text(encoding="utf-8").replace('="./', f'="{PurePosixPath(src).parent}/')
            proj = re.sub(r'(<array name="units">).*?(</array>)', lambda a: a.group(1) + listing + "\n\t\t\t\t" + a.group(2),
                          proj, count=1, flags=re.S)
            rel = f"{self.prefix}/{PurePosixPath(key).with_suffix('')}_spawn.unit"
            (self.out / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.out / rel).write_text(proj, encoding="utf-8", newline="\n")
            return f'<string name="projectile">{rel}</string>'

        text = re.sub(r'<string name="projectile">([^<]*spawn[^<]*)</string>', projectile, text)
        notes.append("spawns " + ", ".join(PurePosixPath(u).stem for _, u in spawns))
        return text

    def _hw1_collision(self, root: ET.Element) -> str | None:
        """A static HW1 actor's collision shape (spawners, towers), scaled."""
        col = root.find("collision")
        if col is None or col.get("static") != "true":
            return None
        k = scale.factor(next(iter(_sprites(root).values())).get("scale") if _sprites(root) else None)
        shapes = []
        for c in col:
            if c.tag == "circle":
                shapes.append(f'<circle offset="{scale.scale_pair(c.get("offset", "0 0"), k)}" '
                              f'radius="{scale.scale_pair(c.get("radius", "8"), k)}" />')
            elif c.tag == "polygon":
                pts = "".join(f"<point>{scale.scale_pair(p.text or '0 0', k)}</point>" for p in c.findall("point"))
                shapes.append(f"<polygon>{pts}</polygon>")
        return f'<collision static="true">{"".join(shapes)}</collision>' if shapes else None

    @staticmethod
    def _collectable_text(text: str, pickup_text: str) -> str:
        """The sphere twin announces itself (its tutorial line, its icon);
        the mission's item says its own pickup text instead."""
        from xml.sax.saxutils import escape
        text = re.sub(r'(<string name="class">ShowFloatingText</string>\s*<string name="text">)[^<]*',
                      lambda m: m.group(1) + escape(pickup_text), text, count=1)
        for cls in ("AnnouncePickup",):
            text = re.sub(rf'\s*<dict>\s*<string name="class">{cls}</string>.*?</dict>', "", text, count=1, flags=re.S)
        return re.sub(r'\s*<dict>\s*<array name="graphic-world">.*?</dict>', "", text, count=1, flags=re.S)

    def _texture(self, hw1_tex: str) -> str | None:
        """Copy a HW1 texture into the scenario; AE path, or None if missing."""
        rel = hw1_tex.replace("\\", "/")
        for base in (self.mission, self.hw1):
            if base is not None and (base / rel).is_file():
                dst = self.out / self.prefix / rel
                if not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(base / rel, dst)
                return f"{self.prefix}/{rel}"
        self.warn(f"HW1 texture '{rel}' not found; the AE sprite is kept")
        return None

    def _ae_sprite(self, s: ET.Element, material: str, indent: str) -> str | None:
        tex = s.findtext("texture")
        path = self._texture(tex.strip()) if tex else None
        if path is None:
            return None
        origin = (s.findtext("origin") or "0 0").strip()
        k = scale.factor(s.get("scale"))  # HW1 draws the sprite at 16/scale
        if k != 1.0:
            scaled = scale.scaled_texture(self.out / path, k)
            if scaled is not None:
                path = scaled.relative_to(self.out).as_posix()
                origin = scale.scale_pair(origin, k, whole=True)
            else:
                k = 1.0
        frames = []
        for f in s.findall("frame"):
            time = f.get("time", "100")
            rect = (f.text or "").strip()
            if k != 1.0 and len(rect.split()) == 4:
                rect = scale.scale_rect(rect, k)
            frames.append(f'{indent}\t<frame time="{time}">{rect}</frame>')
        return (f'{indent}<sprite origin="{origin}" looping="true" texture="{path}" material="{material}">\n'
                + "\n".join(frames) + f"\n{indent}</sprite>")

    def _material(self, text: str) -> str:
        mats = re.findall(r'<sprite[^>]*material="([^"]+)"', text)
        return max(set(mats), key=mats.count) if mats else "system/default.mats:enemy"

    def _item_scenes(self, text: str, sprites: dict[str, ET.Element]) -> str:
        hw1 = _first(sprites, "default", *sprites)
        if hw1 is None:
            return text
        mat = self._material(text)
        new = self._ae_sprite(hw1, mat, "\t\t\t")
        if new is None:
            return text
        return re.sub(r"[ \t]*<sprite\b[^>]*>.*?</sprite>", lambda m: new, text, count=1, flags=re.S)

    def _actor_scenes(self, text: str, sprites: dict[str, ET.Element]) -> str:
        """Each AE scene (``idle-3``, ``walk-0``, ``smash-5``, ``whirlwind``)
        gets the HW1 sprite of the same facing and action: idle = the plain
        compass name, walk = ``<dir>-walk``, any other 8-direction action =
        ``<dir>-attack``, a single scene = the HW1 sprite of that name, else
        HW1's ``channeling`` or south-facing idle, so no AE art shows through."""
        mat = self._material(text)
        has_shared = '<scene name="shared">' in text

        def hw1_for(name: str) -> ET.Element | None:
            m = re.fullmatch(r"(.+)-(\d)", name)
            if m is None:
                return _first(sprites, name, "channeling", "south")
            action, k = m.group(1), int(m.group(2))
            d = AE_DIRS[k]
            if action == "idle":
                return sprites.get(d)
            return _first(sprites, f"{d}-walk" if action == "walk" else f"{d}-attack", d)

        def scene(name: str, full: str, inner: str) -> str:
            if name == "shared":
                return full
            s = hw1_for(name)
            sprite = self._ae_sprite(s, mat, "\t\t\t") if s is not None else None
            if sprite is None:
                return full
            # Only the art changes: collision, minimap and shadow stay. A
            # %block line was an AE sprite macro (with the shared scene in it).
            body = re.sub(r"[ \t]*<sprite\b[^>]*>.*?</sprite>\s*", "", inner, flags=re.S)
            body, macros = re.subn(r"[ \t]*%block\b[^\n]*\n?", "", body)
            shared = '\t\t\t<scene src="shared"/>\n' if has_shared and macros and 'src="shared"' not in body else ""
            # editor-bounds and the like described AE's sprite, not HW1's.
            return f'<scene name="{name}">{body.rstrip()}\n{shared}{sprite}\n\t\t</scene>'

        # Scenes can hold whole scenes (not just <scene src=.../>), so each
        # is matched to its own closing tag.
        out, pos = [], 0
        for m in re.finditer(r'<scene name="([^"]+)"[^>]*?(?<!/)>', text):
            if m.start() < pos:
                continue  # inside a scene already handled
            end = _closing(text, m.end(), "scene")
            if end is None:
                break
            out.append(text[pos:m.start()])
            out.append(scene(m.group(1), text[m.start():end], text[m.end():end - len("</scene>")]))
            pos = end
        out.append(text[pos:])
        return "".join(out)


@lru_cache(maxsize=4)
def _ae_actors(ae_assets: Path) -> dict[str, tuple[str, set[str], float]]:
    """AE enemy units: path -> (movement classes, feature words, hp)."""
    out: dict[str, tuple[str, set[str], float]] = {}
    root = ae_assets / "actors"
    for f in root.rglob("*.unit"):
        rel = f.relative_to(ae_assets).as_posix()
        if "/bosses/" in rel or "/players/" in rel or any(w in rel for w in _NOT_ENEMIES):
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        # Enemies; AE's minibosses (skeleton_guard...) are StateActorBehavior.
        if "CompositeActorBehavior" not in text and "StateActorBehavior" not in text:
            continue
        full = text + "".join(_include(ae_assets, inc) for inc in re.findall(r'%include "([^"]+)"', text))
        low = full.lower()
        move = " ".join(sorted(set(re.findall(r"(\w+)movement", low))))
        feats = {w for w in _FEATURES if w in low}
        hp = re.search(r'name="hp">(\d+)', text)
        out[rel] = (move, feats, float(hp.group(1)) if hp else 0.0)
    return out


@lru_cache(maxsize=256)
def _include(ae_assets: Path, rel: str) -> str:
    p = ae_assets / rel
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
