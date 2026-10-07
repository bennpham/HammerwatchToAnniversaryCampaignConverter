"""Read HW1's stock assets straight from ``assets.bin``.

HW1 ships every stock file packed in ``assets.bin``, next to Hammerwatch.exe.
The archive is uncompressed: the magic ``HWRAD`` and three header bytes, then
for each file a 7-bit-varint name length, the UTF-8 name, a little-endian
uint32 size and the bytes. Its files are the ones HW1's ``ResourceExtractor.exe``
writes to ``editor/assetsExtract``, byte for byte, so unpacking it once into a
cache gives the same loose-file folder without running the extractor.
"""

from __future__ import annotations

import os
import shutil
import struct
from pathlib import Path

MAGIC = b"HWRAD"
_HEADER = len(MAGIC) + 3
_COMPLETE = ".complete"


def read_index(path: Path) -> dict[str, tuple[int, int]]:
    """Name -> (offset, size) of every file in the archive."""
    data = Path(path).read_bytes()
    return _index(data, path)


def _index(data: bytes, path: Path) -> dict[str, tuple[int, int]]:
    if not data.startswith(MAGIC):
        raise ValueError(f"{path} is not a Hammerwatch assets.bin (no {MAGIC.decode()} header)")
    out: dict[str, tuple[int, int]] = {}
    p = _HEADER
    try:
        while p < len(data):
            n, shift = 0, 0
            while True:
                b = data[p]
                p += 1
                n |= (b & 0x7F) << shift
                shift += 7
                if not b & 0x80:
                    break
            name = data[p:p + n].decode("utf-8")
            p += n
            (size,) = struct.unpack_from("<I", data, p)
            p += 4
            if p + size > len(data):
                raise ValueError(f"{path}: {name} runs past the end of the file")
            out[name.replace("\\", "/")] = (p, size)
            p += size
    except (IndexError, struct.error, UnicodeDecodeError) as e:
        raise ValueError(f"{path}: damaged archive at byte {p}: {e}") from None
    return out


def cache_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / ".cache") / "hw2ae" / "hw1_assets"


def unpack_cached(bin_path: Path, cache: Path | None = None) -> Path:
    """The archive's files as a loose folder, unpacked once per version of
    ``assets.bin`` (its size and modification time) and reused after."""
    bin_path = Path(bin_path)
    st = bin_path.stat()
    dest = (cache or cache_root()) / f"{st.st_size}-{st.st_mtime_ns}"
    if (dest / _COMPLETE).is_file():
        return dest
    if dest.exists():  # an earlier unpack was interrupted
        shutil.rmtree(dest)
    data = bin_path.read_bytes()
    for name, (offset, size) in _index(data, bin_path).items():
        f = dest / name
        if dest.resolve() not in f.resolve().parents:
            raise ValueError(f"{bin_path}: {name} points outside the archive")
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data[offset:offset + size])
    (dest / _COMPLETE).write_text(str(bin_path), encoding="utf-8")
    return dest
