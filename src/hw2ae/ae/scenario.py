"""AE scenario metadata: ``info.xml``, which both the game (for an unpacked
scenario folder) and PACKAGER.exe read. Shipped ``.h1c`` packages contain only
``info.xml``, ``logos.png`` and the levels; PACKAGER derives the rest.

The modifier list is the one both shipped campaigns use."""

from __future__ import annotations

import re
from pathlib import Path
from xml.sax.saxutils import escape

# (name, id, locked_by) ; None marks a separator.
MODIFIERS: list[tuple[str, str, str] | None] = [
    (".l.lineofsight", "LINE_OF_SIGHT", ""),
    None,
    (".l.nolives", "NO_LIVES", "INFINITE_LIVES"),
    (".l.inflives", "INFINITE_LIVES", "NO_LIVES"),
    None,
    (".l.1hp", "ONE_HP", "REVERSE_HP_REGEN"),
    (".l.sharehp", "SHARED_HP", ""),
    (".l.hpregen", "HP_REGEN", "ONE_HP,REVERSE_HP_REGEN"),
    None,
    (".l.doubledmg", "DOUBLE_DAMAGE", ""),
    (".l.doublelife", "DOUBLE_LIVES", "NO_LIVES,INFINITE_LIVES"),
    None,
    (".l.nohppup", "NO_HEALTH_PICKUP", ""),
    None,
    (".l.nomanaregen", "NO_MANA_REGEN", "5x_MANA_REGEN"),
    (".l.quickmana", "5x_MANA_REGEN", "NO_MANA_REGEN"),
    (".l.revhpregen", "REVERSE_HP_REGEN", "ONE_HP,HP_REGEN"),
    (".l.legacy", "LEGACY", ""),
]


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return s or "scenario"


def info_xml(name: str, description: str, start_lvl: str, players_max: int = 7) -> str:
    lines = [
        "<info>",
        f"\t<name>{escape(name)}</name>",
        "\t<tag>Hammerwatch</tag>",
        f"\t<description>{escape(description)}</description>",
        "",
        f'\t<players min="1" max="{players_max}" />',
        "\t<saving>all</saving>",
        "",
        "\t<start>",
        f'\t\t<level name="Start level" lvl="{escape(start_lvl)}" />',
        "\t</start>",
        "",
        "\t<modifiers>",
    ]
    for m in MODIFIERS:
        if m is None:
            lines.append("\t\t<separator />")
            continue
        n, i, lock = m
        extra = f' locked-by="{lock}"' if lock else ""
        lines.append(f'\t\t<modifier name="{n}" id="{i}"{extra} />')
    lines += ["\t</modifiers>", "</info>", ""]
    return "\n".join(lines)


# AE's own scenarios (Castle Hammerwatch, Temple of the Sun) allow 7. HW1
# missions have no player-limit setting, so there is nothing to carry over;
# --max-players sets another.
DEFAULT_MAX_PLAYERS = 7


def write(out: Path, name: str, description: str, start_lvl: str, max_players: int | None = None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    players = max_players or DEFAULT_MAX_PLAYERS
    (out / "info.xml").write_text(info_xml(name, description, start_lvl, players), encoding="utf-8", newline="\n")
    stale = out / "info.sval"  # written by earlier versions; PACKAGER would pack it as a plain file
    if stale.exists():
        stale.unlink()
