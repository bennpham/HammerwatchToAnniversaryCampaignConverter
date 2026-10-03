"""HW1 class tweaks (``tweak/*.xml``) -> AE player files overridden inside
the scenario (``players/...``, plus ``scripts/...`` for lives and the power
shop). AE loads a scenario's file in place of the stock one at the same path,
for that scenario only.

The rule for every tweaked stat:

* a value on one of HW1's stock upgrade tiers -> AE's own value for that tier
  (the skill starts owned at that level), since AE rebalanced the classes
  against its enemies;
* a value on no stock tier, i.e. the mission author's own number -> that
  number, converted to AE's units;
* a value the mission leaves at its stock default -> AE's stock skill.

The mission's ``upgrades`` list is what its shops sell: tiers it doesn't list
are taken off AE's shop.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from ..hw1 import tweak as hw1_tweak
from ..hw1.tweak import Tweak, Upgrade, Value
from . import skills

TABLE = Path(__file__).parent.parent / "mapping" / "data" / "players.json"

# Passive skill binds whose tier 0 is the class's own stat in classes.sval.
CLASS_FIELDS = {
    "bind:player-max-health-set": "base-health",
    "bind:player-max-mana-set": "base-mana",
    "bind:player-mana-regen-set": "base-mana-regen",
    "bind:player-health-regen-set": "base-health-regen",
}
SPEED_FILES = ("shared_speed", "shared_speed_melee")
AE_LIVES = 2


@dataclass
class Ladder:
    hw1: list[str]
    skill: str
    mod: str | int | None
    params: dict[str, tuple[str | None, str | None]]
    after: list[str] = field(default_factory=list)
    unlock: str | None = None

    @classmethod
    def from_json(cls, d: dict) -> "Ladder":
        return cls(d["hw1"], d["skill"], d.get("mod"), {k: tuple(v) for k, v in d["params"].items()},
                   d.get("after", []), d.get("unlock"))


@dataclass
class _Ctx:
    """One ladder being converted: its AE skill file and HW1 tier values."""
    label: str
    lad: Ladder
    root: ET.Element    # the skill file
    entry: ET.Element   # the skill itself, or its modifier skill
    tiers: list[dict[str, Value]]
    ae_cls: str | None

    @property
    def is_mod(self) -> bool:
        return self.lad.mod is not None

    @property
    def n_levels(self) -> int:
        return len(skills.levels(self.entry))

    @property
    def parent_level(self) -> int:
        """The level of the skill a modifier hangs off that the player has."""
        return max(1, int(skills.scalar(self.root, "starting-level") or 1))

    @property
    def class_owned(self) -> bool:
        """A passive whose tier 0 is the class's own stat in classes.sval."""
        keys = [k for k, _ in self.lad.params.values() if k]
        return bool(keys) and all(k in CLASS_FIELDS for k in keys)


@dataclass
class Result:
    files: dict[str, str] = field(default_factory=dict)  # scenario-relative path -> text
    lines: list[str] = field(default_factory=list)       # what each tweak became
    warnings: list[str] = field(default_factory=list)


def load_table(path: Path = TABLE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def ladder_key(upgrade_id: str) -> str:
    """``health-3`` / ``dmg3`` / ``whirldur`` -> the chain they belong to."""
    return re.sub(r"-?\d+$", "", upgrade_id)


def _eq(a: Value | None, b: Value | None) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b) if isinstance(a, bool) and isinstance(b, bool) else False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 1e-6
    if a is None or b is None:
        return a is b
    return str(a).replace("\\", "/") == str(b).replace("\\", "/")


def _num(v: Value | None) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except ValueError:
        return None


def _unlocked(cond: str | None, params: dict[str, Value]) -> bool:
    if cond is None:
        return True
    if cond.endswith(">0"):
        return (_num(params.get(cond[:-2])) or 0) > 0
    return bool(params.get(cond))


def hw1_tiers(stock: Tweak, lad: Ladder) -> tuple[list[dict[str, Value]], list[Upgrade]]:
    """The ladder's HW1 values at each tier (0 = before its first upgrade, with
    the skill it hangs off unlocked) and the stock upgrade that reaches each."""
    vals = dict(stock.params)
    for u in stock.upgrades:
        if ladder_key(u.id) in lad.after:
            vals.update(u.values)
    tiers, ups = [dict(vals)], []
    for u in stock.upgrades:
        if ladder_key(u.id) in lad.hw1:
            vals.update(u.values)
            tiers.append(dict(vals))
            ups.append(u)
    return tiers, ups


def _convert(conv: str, v: float, pairs: list[tuple[float, float]]) -> float:
    if conv == "id":
        return v
    if conv == "pct":
        return v / 100
    if conv == "ms":
        return v * 1000
    if conv == "x16":
        return v * 16
    if conv == "inv":
        return 1 / v if v else 0
    if conv == "inv1000":
        return 1000 / v if v else 0
    if conv.startswith("lin "):
        a, b = (float(x) for x in conv.split()[1:])
        return a * v + b
    if conv == "ratio" and pairs and pairs[0][0]:
        return v * pairs[0][1] / pairs[0][0]
    if conv == "interp" and len(pairs) >= 2:
        pts = sorted(pairs)
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if v <= x1 or (x1, y1) == pts[-1]:
                return y0 + (v - x0) * (y1 - y0) / (x1 - x0) if x1 != x0 else y0
    return v


class _Files:
    """Stock AE skill files, parsed once and written only if they changed."""

    def __init__(self, ae_assets: Path) -> None:
        self.ae = ae_assets
        self.trees: dict[str, ET.Element] = {}
        self.stock: dict[str, str] = {}

    def get(self, rel: str, src: str | None = None) -> ET.Element:
        if rel not in self.trees:
            text = (self.ae / (src or rel)).read_text(encoding="utf-8")
            self.trees[rel] = skills.parse(text)
            self.stock[rel] = skills.serialize(skills.parse(text))
        return self.trees[rel]

    def changed(self) -> dict[str, str]:
        out = {}
        for rel, root in self.trees.items():
            text = skills.serialize(root)
            if text != self.stock[rel]:
                out[rel] = text
        return out


class _Classes:
    """Edits to ``players/classes.sval``, kept as text: it carries ``%``
    preprocessor lines and a loader that a tree round-trip would mangle."""

    def __init__(self, ae_assets: Path) -> None:
        self.text = (ae_assets / "players" / "classes.sval").read_text(encoding="utf-8")
        self.stock = self.text

    def _span(self, cls: str) -> tuple[int, int]:
        i = self.text.index(f'<string name="id">{cls}</string>')
        start = self.text.rindex("<dict>", 0, i)
        return start, self.text.index("\n\t</dict>", i)

    def get(self, cls: str, name: str) -> str | None:
        a, b = self._span(cls)
        m = re.search(rf'<(\w+) name="{re.escape(name)}">([^<]*)</\1>', self.text[a:b])
        return m.group(2).strip() if m else None

    def set(self, cls: str, name: str, value: str) -> None:
        a, b = self._span(cls)
        block, n = re.subn(rf'(<(\w+) name="{re.escape(name)}">)[^<]*(</\2>)', rf"\g<1>{value}\g<3>",
                           self.text[a:b], count=1)
        if n:
            self.text = self.text[:a] + block + self.text[b:]

    def skill_paths(self, cls: str) -> list[str]:
        a, b = self._span(cls)
        return re.findall(r"<string>(players/[^<]+\.sval)</string>", self.text[a:b])

    def replace_skill(self, cls: str, old: str, new: str) -> None:
        a, b = self._span(cls)
        self.text = self.text[:a] + self.text[a:b].replace(f"<string>{old}</string>", f"<string>{new}</string>") + self.text[b:]


class Converter:
    def __init__(self, mission: dict[str, Tweak], ae_assets: Path, stock: dict[str, Tweak] | None = None,
                 table: dict | None = None) -> None:
        self.mission = mission
        self.stock = stock if stock is not None else hw1_tweak.load_stock()
        self.table = table if table is not None else load_table()
        self.files = _Files(ae_assets)
        self.classes = _Classes(ae_assets)
        self.ae = ae_assets
        self.res = Result()

    # -- helpers ---------------------------------------------------------------
    def _line(self, s: str) -> None:
        if s not in self.res.lines:  # the two shared speed files report alike
            self.res.lines.append(s)

    def _warn(self, s: str) -> None:
        if s not in self.res.warnings:
            self.res.warnings.append(s)

    def _skill_rel(self, folder: str, skill: str) -> str:
        return f"players/{folder}/skills/{skill}.sval"

    # -- entry points ----------------------------------------------------------
    def run(self) -> Result:
        shared_mission = self.mission.get("shared")
        if shared_mission is not None:
            for f in SPEED_FILES + ("shared_combo",):
                self._apply_file("shared", self._skill_rel("shared", f), shared_mission, only_speed=f in SPEED_FILES,
                                 skip_speed=f not in SPEED_FILES)
            self._base("shared", shared_mission)
            self._unknown("shared", shared_mission)
        for hw1_cls in hw1_tweak.CLASSES:
            m = self.mission.get(hw1_cls)
            if m is None:
                continue
            spec = self.table[hw1_cls]
            for lad in (Ladder.from_json(d) for d in spec["ladders"]):
                self._ladder(hw1_cls, spec["ae"], lad, m, self.stock[hw1_cls])
            self._base(hw1_cls, m)
            self._class_speed(hw1_cls, spec["ae"], m)
            self._unknown(hw1_cls, m)

        self.res.files.update(self.files.changed())
        combo = self.files.trees.get(self._skill_rel("shared", "shared_combo"))
        if combo is not None and skills.scalar(combo, "starting-level"):
            scripts = combo_scripts(self.ae)
            if len(scripts) < len(COMBO_SCRIPTS):
                self._warn("AE's combo scripts changed; a combo owned from the start may not charge")
            self.res.files.update(scripts)
        if self.classes.text != self.classes.stock:
            self.res.files["players/classes.sval"] = self.classes.text
        return self.res

    def _apply_file(self, hw1_cls: str, rel: str, m: Tweak, only_speed=False, skip_speed=False) -> None:
        for lad in (Ladder.from_json(d) for d in self.table[hw1_cls]["ladders"]):
            is_speed = lad.skill == "speed"
            if (only_speed and not is_speed) or (skip_speed and is_speed):
                continue
            self._ladder(hw1_cls, "shared", lad, m, self.stock[hw1_cls], rel=rel)

    # -- one ladder ------------------------------------------------------------
    def _ladder(self, hw1_cls: str, folder: str, lad: Ladder, m: Tweak, stock: Tweak, rel: str | None = None) -> None:
        rel = rel or self._skill_rel(folder, lad.skill)
        root = self.files.get(rel)
        entry = root if lad.mod is None else skills.find_mod(root, lad.mod)
        if entry is None:
            self._warn(f"{rel}: modifier skill {lad.mod!r} not found; HW1 '{lad.hw1[0]}' upgrades not converted")
            return
        tiers, tier_ups = hw1_tiers(stock, lad)
        label = f"{hw1_cls} {'/'.join(lad.hw1)}"
        if lad.skill == "speed":
            label += " (melee)" if rel.endswith("_melee.sval") else " (ranged)"
        ctx = _Ctx(label, lad, root, entry, tiers,
                   self.table[hw1_cls]["ae"] if hw1_cls != "shared" else None)
        mp = {**stock.params, **m.params}

        # The skill this ladder hangs off must be owned, or none of it applies.
        gate = lad.unlock
        if lad.after:
            parent = next((p for p in (Ladder.from_json(d) for d in self.table[hw1_cls]["ladders"])
                           if p.hw1[0] == lad.after[0]), None)
            gate = parent.unlock if parent else None
        unlocked = _unlocked(gate, mp)

        level = 0
        if unlocked and not lad.params:
            level = 1  # an unlock with nothing else to set
            self._line(f"{ctx.label}: owned from the start")
        elif unlocked:
            t = self._match(tiers, mp, lad.params)
            if t is not None and t <= ctx.n_levels:
                level = max(t, 1 if lad.unlock else 0)
                if level:
                    self._line(f"{ctx.label}: HW1 tier {t} -> AE level {level}")
            else:
                level = self._write_literal(ctx, {p: mp[p] for p in lad.params if p in mp})
                if lad.unlock:
                    level = max(level, 1)
        if level:
            skills.set_scalar(entry, "starting-level", str(level), "int", after="id")

        self._shop(ctx, level, m, tier_ups)

    def _match(self, tiers: list[dict[str, Value]], mp: dict[str, Value], params) -> int | None:
        for t in range(len(tiers) - 1, -1, -1):
            if all(_eq(mp.get(p), tiers[t].get(p)) for p in params if p in mp and p in tiers[t]):
                return t
        return None

    def _ae_value(self, ctx: "_Ctx", key: str, tier: int) -> str | None:
        """AE's value of ``key`` at a ladder tier, as text."""
        if tier == 0:
            if ctx.is_mod:
                e = skills.value_at(ctx.root, key, ctx.parent_level)
                return (e.text or "").strip() if e is not None else None
            if key in CLASS_FIELDS and ctx.ae_cls:
                return self.classes.get(ctx.ae_cls, CLASS_FIELDS[key])
            return None
        e = skills.value_at(ctx.entry, key, tier)
        if e is None and ctx.is_mod:
            e = skills.value_at(ctx.root, key, ctx.parent_level)
        return (e.text or "").strip() if e is not None else None

    def _literal(self, ctx: "_Ctx", p: str, v: Value, where: str = "") -> str | None:
        """The AE text for an author's own HW1 value, or None (warned) when AE
        can only take one of its tiers and none fits."""
        key, conv = ctx.lad.params[p]
        if key is None:
            if not any(_eq(tv.get(p), v) for tv in ctx.tiers):
                self._warn(f"{ctx.label}: {where}{p} = {v} has no AE equivalent; AE's own value is kept")
            return None
        nv = _num(v)
        if conv == "snap" or nv is None:
            exact = [t for t, tv in enumerate(ctx.tiers[:ctx.n_levels + 1]) if _eq(tv.get(p), v)]
            if exact:
                return self._ae_value(ctx, key, exact[-1])
            self._warn(f"{ctx.label}: {where}{p} = {v} sits on no HW1 tier and AE keeps this one in its own "
                       f"buff/effect data; AE's value is kept")
            return None
        pairs = []
        for t, tv in enumerate(ctx.tiers[:ctx.n_levels + 1]):
            a, b = _num(tv.get(p)), _num(self._ae_value(ctx, key, t))
            if a is not None and b is not None:
                pairs.append((a, b))
        out = _convert(conv, nv, pairs)
        return skills.fmt(out, self._tag(ctx, key, out))

    def _tag(self, ctx: "_Ctx", key: str, v: float) -> str:
        tag = skills.tag_of(ctx.entry, key, ctx.root if ctx.is_mod else None)
        if tag is None and key in CLASS_FIELDS:
            tag = "float" if "regen" in key else "int"
        return tag or ("int" if float(v).is_integer() else "float")

    def _put(self, level: ET.Element, key: str, text: str, tag: str) -> None:
        """Set a value; a changed radius also resizes the effect drawn with it
        ("...effect?radius=N" next to it), keeping AE's offset between them."""
        old = skills.get(level, key)
        old_v = _num(old.text) if old is not None else None
        skills.put(level, key, text, tag)
        if "radius" not in key or old_v is None:
            return
        holder = skills.child(level, "binds") if key.startswith("bind:") else level
        for c in (holder if holder is not None else []):
            m = re.search(r"radius=(-?[\d.]+)", c.text or "")
            if c.tag == "string" and m:
                fx = float(m.group(1)) - old_v + float(text)
                c.text = c.text[:m.start(1)] + skills.fmt(fx, "int") + c.text[m.end(1):]

    def _write_literal(self, ctx: "_Ctx", values: dict[str, Value]) -> int:
        """The author's own numbers, written into the level the player starts
        with. Returns the level to start the ladder's skill at."""
        class_owned = not ctx.is_mod and ctx.class_owned
        if ctx.is_mod:
            start, target = 0, skills.levels(ctx.root)[ctx.parent_level - 1]
        elif class_owned:
            start, target = 0, None
        else:
            start, target = 1, skills.levels(ctx.entry)[0]
        for p, v in values.items():
            text = self._literal(ctx, p, v)
            if text is None:
                continue
            key = ctx.lad.params[p][0]
            if class_owned:
                self.classes.set(ctx.ae_cls, CLASS_FIELDS[key], text)
            else:
                self._put(target, key, text, self._tag(ctx, key, float(_num(text) or 0)))
            self._line(f"{ctx.label}: {p} {v} -> {key.removeprefix('bind:')} {text}")
        return start

    # -- shop ------------------------------------------------------------------
    def _shop(self, ctx: "_Ctx", level: int, m: Tweak, tier_ups: list[Upgrade]) -> None:
        """Sell the tiers above ``level`` that the mission's shop lists."""
        entry, n_levels = ctx.entry, ctx.n_levels
        info = {t + 1: skills.shop_info(s) for t, s in enumerate(skills.shop_slots(entry))}
        sell: list[tuple[int, Upgrade]] = []
        for t in range(level + 1, n_levels + 1):
            if t - 1 >= len(tier_ups):
                break
            mu = m.upgrade(tier_ups[t - 1].id)
            if mu is None:
                break
            sell.append((t, mu))
        stock_sold = [t for t in range(1, n_levels + 1) if t - 1 < len(tier_ups)]
        if level == 0 and [t for t, _ in sell] == stock_sold and all(
                mu.cost == tier_ups[t - 1].cost and mu.values == tier_ups[t - 1].values for t, mu in sell):
            return

        # Upgrades whose numbers the mission changed.
        lv = skills.levels(entry)
        for t, mu in sell:
            su = tier_ups[t - 1]
            for p, v in mu.values.items():
                if p in ctx.lad.params and not _eq(v, su.values.get(p)):
                    text = self._literal(ctx, p, v, f"shop tier {t} ")
                    if text is None:
                        continue
                    key = ctx.lad.params[p][0]
                    self._put(lv[t - 1], key, text, self._tag(ctx, key, float(_num(text) or 0)))
                    self._line(f"{ctx.label}: shop tier {t} {p} {v} -> {key.removeprefix('bind:')} {text}")

        for s in [entry] + lv:
            skills.remove(s, "category")
        # AE's shop shows level[picked] as the next purchase, without adding
        # starting-level, so each sold tier's entry moves down by ``level``.
        for t, mu in sell:
            if level == 0:
                slot = entry if t == 1 else lv[t - 1]
            elif ctx.is_mod:
                slot = entry if t == level + 1 else lv[t - level - 1]
            else:
                slot = lv[t - level - 1]
            ae_cost = None if mu.cost == tier_ups[t - 1].cost else mu.cost
            skills.set_shop_info(slot, info.get(t, {}), ae_cost)
        if level and not ctx.is_mod and sell:
            # An owned skill only lists upgrades while its own category is on sale.
            cat = info.get(sell[0][0], {}).get("category")
            if cat is not None:
                skills.set_scalar(entry, "category", (cat.text or "").strip(), "string", after="id")
        sold = [t for t, _ in sell]
        if sold:
            self._line(f"{ctx.label}: shop sells tier(s) {', '.join(map(str, sold))}")
        elif level < n_levels:
            self._line(f"{ctx.label}: not sold in shops")

    # -- other params ----------------------------------------------------------
    def _base(self, hw1_cls: str, m: Tweak) -> None:
        stock = self.stock[hw1_cls]
        folder = self.table[hw1_cls]["ae"]
        for p, spec in self.table[hw1_cls].get("base", {}).items():
            if p not in m.params or _eq(m.params[p], stock.params.get(p)):
                continue
            v = m.params[p]
            if spec is None:
                self._warn(f"{hw1_cls} {p} = {v} has no AE equivalent; AE's own value is kept")
                continue
            skill, key, conv = spec
            root = self.files.get(self._skill_rel(folder, skill))
            nv = _num(v)
            if nv is None:
                self._warn(f"{hw1_cls} {p} = {v} not converted")
                continue
            tag = skills.tag_of(root, key) or ("float" if not float(nv).is_integer() else "int")
            text = skills.fmt(_convert(conv, nv, []), tag)
            skills.put(skills.levels(root)[0], key, text, tag)
            self._line(f"{hw1_cls} {p}: {v} -> {skill} {key.removeprefix('bind:')} {text} (literal)")

    def _unknown(self, hw1_cls: str, m: Tweak) -> None:
        """Params and upgrades HW1 itself doesn't define (a mission can list
        anything; HW1 ignores what its classes don't read)."""
        stock = self.stock[hw1_cls]
        known = set(self.table[hw1_cls].get("base", {})) | {"move-speed"}
        for d in self.table[hw1_cls]["ladders"]:
            known |= set(d["params"])
            if d.get("unlock"):
                known.add(d["unlock"].removesuffix(">0"))
        params = [p for p, v in m.params.items() if p not in known and not _eq(v, stock.params.get(p))]
        if params:
            self._warn(f"{hw1_cls}: params HW1 doesn't define, not converted: {', '.join(params)}")
        ladders = {k for d in self.table[hw1_cls]["ladders"] for k in d["hw1"]}
        power = set(self.table[hw1_cls].get("power", {})) - {"_doc"}
        ups = [u.id for u in m.upgrades
               if ladder_key(u.id) not in ladders and u.id not in power and stock.upgrade(u.id) is None]
        if ups:
            self._warn(f"{hw1_cls}: upgrades HW1 doesn't define, not converted: {', '.join(ups)}")

    def _class_speed(self, hw1_cls: str, ae_cls: str, m: Tweak) -> None:
        """HW1 lets a class file set its own move-speed; AE keeps speed in a
        file every class of its kind shares, so the class gets its own copy."""
        shared = self.mission.get("shared") or self.stock["shared"]
        if "move-speed" not in m.params or _eq(m.params["move-speed"], shared.params.get("move-speed")):
            return
        paths = [p for p in self.classes.skill_paths(ae_cls) if p.rsplit("/", 1)[-1][:-5] in SPEED_FILES]
        if not paths:
            return
        src = paths[0]
        rel = f"players/{ae_cls}/skills/hw1_{src.rsplit('/', 1)[-1]}"
        speed = Tweak({**shared.params, "move-speed": m.params["move-speed"]}, shared.upgrades)
        self.files.get(rel, src)  # speed is all the file holds, so start from stock
        before = len(self.res.lines)
        self._apply_file("shared", rel, speed, only_speed=True)
        if self.files.changed().get(rel) is not None:
            self.classes.replace_skill(ae_cls, src, rel)
        for i in range(before, len(self.res.lines)):
            self.res.lines[i] = self.res.lines[i].replace("shared speed", f"{hw1_cls} speed")


# -- scripts --------------------------------------------------------------------

# AE checks combo by what the player bought (pickedSkills), which leaves out a
# starting level, so a combo the class starts with would never charge.
COMBO_SCRIPTS = ("scripts/Behaviors/Actors/Player/Player.as", "scripts/Behaviors/Effects/GiveCombo.as")
_PICKED_COMBO = re.compile(r'int\(([\w.]+)\.pickedSkills\["shared_misc_combo"\]\)')


def combo_scripts(ae_assets: Path) -> dict[str, str]:
    """The scripts that test for combo, counting a starting level as owned."""
    out = {}
    for rel in COMBO_SCRIPTS:
        text = (ae_assets / rel).read_text(encoding="utf-8")
        new = _PICKED_COMBO.sub(r'\1.GetSkillLevel("shared_misc_combo")', text)
        if new != text:
            out[rel] = new
    return out


def lives_script(ae_assets: Path, lives: int) -> str | None:
    """``scripts/Modules/PartyRecord.as`` starting the party with ``lives``."""
    text = (ae_assets / "scripts" / "Modules" / "PartyRecord.as").read_text(encoding="utf-8")
    new, n = re.subn(r"(%else\s*\n\s*)m_lives = \d+;", rf"\g<1>m_lives = {int(lives)};", text, count=1)
    return new if n else None


def power_shop_script(ae_assets: Path, mission: Tweak, stock: Tweak, table: dict) -> tuple[str | None, list[str]]:
    """``scripts/GUI/Shop/PowerShopMenuContent.as`` selling what the mission's
    ``shared.xml`` lists, at its prices (AE's price where HW1's was stock)."""
    power = {k: v for k, v in table["shared"]["power"].items() if not k.startswith("_")}
    stock_life = "350 + int(pow(PartyRecord::GetNumLives(), 2.6f))"
    notes: list[str] = []
    lines = []
    life_cost = stock_life
    for hid, (ae_id, name, desc, ae_cost) in power.items():
        mu, su = mission.upgrade(hid), stock.upgrade(hid)
        if mu is None:
            notes.append(f"power shop: '{hid}' not sold")
            continue
        cost = ae_cost if su is not None and mu.cost == su.cost else mu.cost
        if cost != ae_cost:
            notes.append(f"power shop: '{hid}' costs {cost}")
        if hid == "life":
            scale = mu.extra.get("life-cost-scale", "2.6")
            if su is None or scale != su.extra.get("life-cost-scale", "2.6"):
                notes.append(f"power shop: each life bought raises the price by lives^{scale}")
            else:
                scale = "2.6"
            life_cost = f"{cost} + int(pow(PartyRecord::GetNumLives(), {float(scale)}f))"
            lines += ["%if !MOD_INFINITE_LIVES | !MOD_NO_LIVES",
                      f'\t\tAddItemToShop("{ae_id}", "{name}", "{desc}", {life_cost});', "%endif"]
        else:
            lines.append(f'\t\tAddItemToShop("{ae_id}", "{name}", "{desc}", {cost});')
    if not notes:
        return None, []  # sells what AE sells, at AE's prices
    text = (ae_assets / "scripts" / "GUI" / "Shop" / "PowerShopMenuContent.as").read_text(encoding="utf-8")
    m = re.search(r"%if !MOD_INFINITE_LIVES \| !MOD_NO_LIVES\n.*?(?=\t\tAddItemToShop\(\"potion_speed\")", text, re.S)
    if m is None or stock_life not in text:
        return None, ["power shop: AE's PowerShopMenuContent.as changed; the mission's power shop is not converted"]
    new = text[:m.start()] + "\n".join(lines) + "\n" + text[m.end():]
    return new.replace(stock_life, life_cost), notes
