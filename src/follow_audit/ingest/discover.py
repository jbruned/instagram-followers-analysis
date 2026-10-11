"""Find exports (zips and unzipped folders) inside the data directory."""

from __future__ import annotations

import os
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .parse import is_relevant

PERSONAL_INFO_NAME = "personal_information.json"

# Follower lists are text and small. These stop a hostile or broken zip from filling memory.
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_TOTAL_BYTES = 400 * 1024 * 1024
MAX_FILES = 5000


class TooBig(ValueError):
    """The export is larger than any real followers export."""


@dataclass
class Export:
    """One export on disk. ``read_files`` yields (path inside export, bytes)."""

    path: Path
    is_zip: bool

    @property
    def name(self) -> str:
        return self.path.name

    def mtime(self) -> float:
        return self.path.stat().st_mtime

    def top_level_names(self) -> list[str]:
        """Names that may carry the ``instagram-<user>-<date>-<hash>`` pattern."""
        names = [self.path.stem if self.is_zip else self.path.name]
        if self.is_zip:
            with zipfile.ZipFile(self.path) as zf:
                names += sorted({n.split("/", 1)[0] for n in zf.namelist() if "/" in n})
        else:
            names += sorted(p.name for p in self.path.iterdir() if p.is_dir())
        return names

    def read_files(self) -> Iterator[tuple[str, bytes]]:
        if self.is_zip:
            with zipfile.ZipFile(self.path) as zf:
                total = 0
                for count, info in enumerate(zf.infolist(), 1):
                    if count > MAX_FILES * 20:
                        raise TooBig("the zip holds too many files")
                    if info.is_dir():
                        continue
                    base = info.filename.rsplit("/", 1)[-1]
                    if is_relevant(info.filename) or base == PERSONAL_INFO_NAME:
                        total = _check_size(info.filename, info.file_size, total)
                        yield info.filename, zf.read(info)
        else:
            total = 0
            for dirpath, _, files in os.walk(self.path):
                for f in sorted(files):
                    full = Path(dirpath) / f
                    rel = full.relative_to(self.path).as_posix()
                    if is_relevant(rel) or f == PERSONAL_INFO_NAME:
                        total = _check_size(rel, full.stat().st_size, total)
                        yield rel, full.read_bytes()


def _check_size(name: str, size: int, total: int) -> int:
    """Refuse a file or running total beyond the limits; returns the new total."""
    if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
        raise TooBig(f"{name} is too large to be part of a followers export")
    return total + size


def discover(data_dir: Path) -> list[Export]:
    data_dir = data_dir.resolve()
    if not data_dir.is_dir():
        return []
    exports: dict[Path, Export] = {}
    for dirpath, dirnames, files in os.walk(data_dir):
        here = Path(dirpath)
        for f in files:
            if f.lower().endswith(".zip"):
                exports[here / f] = Export(here / f, is_zip=True)
            elif f.lower() == "connections.json":
                root = here
                exports.setdefault(root, Export(root, is_zip=False))
        for d in list(dirnames):
            if d == "followers_and_following":
                root = here.parent if here.name == "connections" else here
                exports.setdefault(root, Export(root, is_zip=False))
    # Drop folders nested inside another discovered folder export.
    roots = [p for p, e in exports.items() if not e.is_zip]
    for p in roots:
        if any(o != p and o in p.parents for o in roots):
            del exports[p]
    return sorted(exports.values(), key=lambda e: str(e.path))
