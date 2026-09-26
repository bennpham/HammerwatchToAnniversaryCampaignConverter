"""Reader and writer for SVAL text, the XML-shaped format AE uses for levels,
scenario info and most data files.

A value is one element: ``dict``, ``array``, ``string``, ``int``, ``float``,
``bool``, ``vec2``, ``vec3``, ``vec4``, ``bytes``, ``color`` or ``null``.
Children of a ``dict`` carry a ``name`` attribute; children of an ``array``
don't.

Reading keeps the tag of every value (``Node``) because a level's unit and
script arrays are positional and mix types. Writing goes through ``Writer``,
which builds the text directly so the output matches the editor's own layout.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

SCALAR_TAGS = {"string", "int", "float", "bool", "vec2", "vec3", "vec4", "bytes", "color", "null"}


@dataclass
class Node:
    tag: str
    name: str | None = None
    text: str = ""
    children: list["Node"] = field(default_factory=list)

    # -- scalar access -------------------------------------------------
    @property
    def value(self):
        t = self.tag
        s = self.text.strip()
        if t == "int":
            return int(s)
        if t == "float":
            return float(s)
        if t == "bool":
            return s.lower() in ("t", "true", "1")
        if t in ("vec2", "vec3", "vec4", "color"):
            return tuple(float(x) for x in s.split())
        if t == "int-arr":
            return [int(x) for x in s.split()]
        if t == "null":
            return None
        return s

    # -- dict access ---------------------------------------------------
    def get(self, name: str) -> "Node | None":
        for c in self.children:
            if c.name == name:
                return c
        return None

    def __getitem__(self, name: str) -> "Node":
        n = self.get(name)
        if n is None:
            raise KeyError(name)
        return n

    def __iter__(self) -> Iterator["Node"]:
        return iter(self.children)


def _convert(el: ET.Element) -> Node:
    # HW1 level files use the same shape with ``dictionary`` and ``int-arr``,
    # so anything that isn't a known scalar is treated as a container.
    node = Node(tag=el.tag, name=el.get("name"), text=el.text or "")
    if el.tag not in SCALAR_TAGS and el.tag != "int-arr":
        node.children = [_convert(c) for c in el]
    return node


def parse_text(text: str) -> Node:
    if text.startswith("﻿"):
        text = text[1:]
    return _convert(ET.fromstring(text))


def parse_file(path: str | Path) -> Node:
    return parse_text(Path(path).read_text(encoding="utf-8-sig", errors="replace"))


# ---------------------------------------------------------------------------
# Writing


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def fmt_num(v: float) -> str:
    """Integers print without a decimal point, everything else with up to
    three decimals, which is what the AE editor writes."""
    if float(v).is_integer():
        return str(int(v))
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s == "-0" else s


class Writer:
    """Builds SVAL text with tab indentation.

    ``open``/``close`` wrap containers; the scalar helpers write one line each.
    Pass ``name`` inside a dict, leave it out inside an array.
    """

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._depth = 0

    def _attr(self, name: str | None) -> str:
        return f' name="{_esc(name)}"' if name is not None else ""

    def line(self, text: str) -> None:
        self._lines.append("\t" * self._depth + text)

    def open(self, tag: str, name: str | None = None) -> None:
        self.line(f"<{tag}{self._attr(name)}>")
        self._depth += 1

    def close(self, tag: str) -> None:
        self._depth -= 1
        self.line(f"</{tag}>")

    def scalar(self, tag: str, value: str, name: str | None = None) -> None:
        self.line(f"<{tag}{self._attr(name)}>{value}</{tag}>")

    def string(self, value: str, name: str | None = None) -> None:
        self.scalar("string", _esc(value), name)

    def int(self, value: int, name: str | None = None) -> None:
        self.scalar("int", str(int(value)), name)

    def float(self, value: float, name: str | None = None) -> None:
        self.scalar("float", fmt_num(value), name)

    def bool(self, value: bool, name: str | None = None) -> None:
        self.scalar("bool", "t" if value else "f", name)

    def vec2(self, x: float, y: float, name: str | None = None) -> None:
        self.scalar("vec2", f"{fmt_num(x)} {fmt_num(y)}", name)

    def vec3(self, x: float, y: float, z: float = 0, name: str | None = None) -> None:
        self.scalar("vec3", f"{fmt_num(x)} {fmt_num(y)} {fmt_num(z)}", name)

    def bytes(self, hexdata: str, name: str | None = None) -> None:
        self.scalar("bytes", hexdata, name)

    def null(self) -> None:
        self.line("<null/>")

    def text(self) -> str:
        return "\n".join(self._lines) + "\n"
