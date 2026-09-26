"""Index of AE's unpacked assets, for existence checks.

Paths are stored the way levels reference them: forward slashes, relative to
``unpacked_assets_*``, lower-cased for lookup (Windows is case-insensitive and
AE's own levels are not always consistent about case).
"""

from __future__ import annotations

import os
from pathlib import Path

# Units the engine provides without a file.
BUILTIN_UNITS = {":physics_rectangle", ":physics_circle", ":physics_polygon"}


class AssetIndex:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._paths: set[str] = set()
        for dirpath, _dirs, files in os.walk(self.root):
            rel = os.path.relpath(dirpath, self.root).replace("\\", "/")
            prefix = "" if rel == "." else rel + "/"
            for f in files:
                self._paths.add((prefix + f).lower())

    def exists(self, path: str) -> bool:
        p = path.split(":", 1)[0] if not path.startswith(":") else path
        p = p.lower()
        return p in BUILTIN_UNITS or p in self._paths

    def __len__(self) -> int:
        return len(self._paths)
