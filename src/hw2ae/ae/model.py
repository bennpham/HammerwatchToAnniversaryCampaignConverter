"""In-memory AE level, filled by the converter and written by ``level_writer``."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Param:
    """One named value in a script's parameter dict. ``kind`` is an SVAL tag:
    string, int, float, bool, ``ids`` for an array of unit/script ids, or
    ``target`` for a ``(script id, event)`` feed such as ``#PlayerTarget``."""
    kind: str
    name: str
    value: object


@dataclass
class Unit:
    path: str
    x: float
    y: float
    id: int
    # Per-instance settings written as the unit's state dict (layer, start
    # scene, physics size...). Values are (sval tag, value) pairs.
    state: dict[str, tuple[str, object]] = field(default_factory=dict)


@dataclass
class Script:
    cls: str
    id: int
    x: float
    y: float
    enabled: bool = True
    trigger_times: int = -1
    execute_on_start: bool = False
    label: str | None = None
    params: list[Param] = field(default_factory=list)
    connections: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class PrefabPlacement:
    path: str
    x: float
    y: float


@dataclass
class Level:
    game_mode: str
    environment: str | None
    tile_cells: dict[tuple[int, int], list[tuple[str, str]]] = field(default_factory=dict)
    units: list[Unit] = field(default_factory=list)
    scripts: list[Script] = field(default_factory=list)
    prefabs: list[PrefabPlacement] = field(default_factory=list)
    _next_id: int = 1

    def new_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i
