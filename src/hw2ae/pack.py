"""Build ``scenarios/<id>.h1c`` with AE's own PACKAGER.exe.

PACKAGER only packs scenarios it can find by name, i.e. folders directly under
``<AE>/scenarios``. So the folder is staged there, packed with
``PACKAGER.exe -l <id>``, and the staged copy is removed again; otherwise the
game would list the scenario twice (once as a folder, once as the .h1c).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path


def pack(ae_root: Path, folder: Path, timeout: int = 600) -> Path:
    exe = ae_root / "PACKAGER.exe"
    if not exe.exists():
        raise RuntimeError(f"{exe} not found")
    folder = folder.resolve()
    name = folder.name
    scenarios = ae_root / "scenarios"
    staged = scenarios / name
    h1c = scenarios / f"{name}.h1c"

    staged_here = folder != staged.resolve()
    if staged_here:
        if staged.exists():
            raise RuntimeError(f"{staged} already exists; move it away (or pack that folder directly) "
                               "so it is not overwritten")
        shutil.copytree(folder, staged)

    started = time.time()
    with tempfile.TemporaryFile() as out:
        try:
            subprocess.run([str(exe), "-l", name], cwd=ae_root, stdout=out, stderr=subprocess.STDOUT,
                           timeout=timeout, check=False)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"PACKAGER.exe did not finish within {timeout}s") from e
        finally:
            if staged_here:
                shutil.rmtree(staged, ignore_errors=True)
        out.seek(0)
        log = out.read().decode("utf-8", errors="replace")

    if not h1c.exists() or h1c.stat().st_mtime < started - 1:
        tail = "\n".join(log.strip().splitlines()[-15:])
        raise RuntimeError(f"PACKAGER.exe did not produce {h1c}. Its last output:\n{tail}")
    return h1c
