"""AE player skill files (``players/<class>/skills/*.sval``), read and written
as a tree so a converted copy differs from the stock file only where a value
changed.

A skill's levels are its ``skill`` dict followed by each ``upgrades`` entry;
a level owns the ``binds`` it sets plus plain params such as ``mana-cost``.
``modifier-skills`` hold the same structure one level down. The shop entry of
a level is its ``name``/``description``/``category``/``cost``; the first
entry's live on the skill (or modifier) itself.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

SHOP_KEYS = ("name", "description", "category", "cost")
_BARE_AMP = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#)")


def parse(text: str) -> ET.Element:
    # Descriptions carry query strings ("...?lvl=1&max-health=120") with bare '&'.
    # The first element is the skill: priest_mana_shield.sval repeats itself.
    return ET.fromstring(f"<_>{_BARE_AMP.sub('&amp;', text)}</_>")[0]


def serialize(el: ET.Element) -> str:
    out: list[str] = []
    _write(el, 0, out)
    return "\n".join(out) + "\n"


def _write(el: ET.Element, depth: int, out: list[str]) -> None:
    pad = "\t" * depth
    attrs = "".join(f' {k}="{v}"' for k, v in el.attrib.items())
    if len(el):
        out.append(f"{pad}<{el.tag}{attrs}>")
        for c in el:
            _write(c, depth + 1, out)
        out.append(f"{pad}</{el.tag}>")
    else:  # never self-closing: AE's own files don't use it
        out.append(f"{pad}<{el.tag}{attrs}>{(el.text or '').strip()}</{el.tag}>")


# -- navigation --------------------------------------------------------------

def child(d: ET.Element, name: str) -> ET.Element | None:
    for c in d:
        if c.get("name") == name:
            return c
    return None


def levels(entry: ET.Element) -> list[ET.Element]:
    """[skill dict, *upgrades] of a skill file's root or of a modifier skill."""
    base = child(entry, "skill")
    ups = child(entry, "upgrades")
    return ([base] if base is not None else []) + (list(ups) if ups is not None else [])


def mods(entry: ET.Element) -> list[ET.Element]:
    m = child(entry, "modifier-skills")
    return list(m) if m is not None else []


def find_mod(root: ET.Element, key: str | int) -> ET.Element | None:
    ms = mods(root)
    if isinstance(key, int):
        return ms[key] if key < len(ms) else None
    for m in ms:
        idn = child(m, "id")
        if idn is not None and (idn.text or "").strip() == key:
            return m
    return None


def scalar(d: ET.Element, name: str) -> str | None:
    c = child(d, name)
    return (c.text or "").strip() if c is not None else None


def set_scalar(d: ET.Element, name: str, value: str, tag: str = "int", after: str | None = None) -> None:
    c = child(d, name)
    if c is None:
        c = ET.Element(tag, {"name": name})
        idx = len(d)
        if after is not None:
            a = child(d, after)
            if a is not None:
                idx = list(d).index(a) + 1
        d.insert(idx, c)
    c.text = value


def remove(d: ET.Element, name: str) -> None:
    c = child(d, name)
    if c is not None:
        d.remove(c)


# -- level values ------------------------------------------------------------

def _holder(level: ET.Element, key: str, create: bool = False) -> tuple[ET.Element | None, str]:
    """The dict holding ``key`` ("bind:x" lives in the level's binds)."""
    if key.startswith("bind:"):
        b = child(level, "binds")
        if b is None and create:
            b = ET.SubElement(level, "dict", {"name": "binds"})
        return b, key[5:]
    return level, key


def get(level: ET.Element, key: str) -> ET.Element | None:
    h, n = _holder(level, key)
    return child(h, n) if h is not None else None


def value_at(entry: ET.Element, key: str, n: int) -> ET.Element | None:
    """The element setting ``key`` once the first ``n`` levels apply."""
    found = None
    for lv in levels(entry)[:n]:
        e = get(lv, key)
        if e is not None:
            found = e
    return found


def tag_of(entry: ET.Element, key: str, parent: ET.Element | None = None) -> str | None:
    for e in [entry] + ([parent] if parent is not None else []):
        for lv in levels(e):
            el = get(lv, key)
            if el is not None:
                return el.tag
    return None


def put(level: ET.Element, key: str, text: str, tag: str) -> None:
    h, n = _holder(level, key, create=True)
    set_scalar(h, n, text, tag)


def fmt(v: float, tag: str) -> str:
    if tag == "int":
        return str(int(round(v)))
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


# -- shop entries --------------------------------------------------------------

def shop_slots(entry: ET.Element) -> list[ET.Element]:
    """Where the shop reads tier t's entry (t = 1..len) in the stock layout."""
    lv = levels(entry)
    return [entry] + lv[1:]


def shop_info(slot: ET.Element) -> dict[str, ET.Element]:
    return {k: c for k in SHOP_KEYS if (c := child(slot, k)) is not None}


def set_shop_info(slot: ET.Element, info: dict[str, ET.Element], cost: int | None = None) -> None:
    """Make ``slot`` the shop entry ``info`` describes; an existing key keeps
    its place, a new one goes after the keys before it (or after ``id``)."""
    after = "id"
    for k in SHOP_KEYS:
        src = info.get(k)
        if src is None:
            remove(slot, k)
            continue
        text = str(cost) if k == "cost" and cost is not None else (src.text or "").strip()
        if child(slot, k) is None and child(slot, after) is None:
            after = None
        set_scalar(slot, k, text, src.tag, after=after)
        after = k
