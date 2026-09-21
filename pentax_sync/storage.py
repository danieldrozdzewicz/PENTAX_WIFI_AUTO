from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path

from .camera import RemotePhoto

LOG = logging.getLogger(__name__)


def _safe_filename(filename: str) -> str:
    name = Path(filename).name
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    if not name or name in {".", ".."}:
        raise ValueError("unsafe empty camera filename")
    return name


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_atomic(camera, photo: RemotePhoto, root: Path) -> tuple[Path, int, str]:
    root.mkdir(parents=True, exist_ok=True)
    basename = _safe_filename(photo.filename)
    destination = root / basename
    if destination.exists():
        stem, suffix = destination.stem, destination.suffix
        number = 2
        while destination.exists():
            destination = root / f"{stem}_{number}{suffix}"
            number += 1
    partial = destination.with_name(destination.name + ".part")
    written = 0
    try:
        with partial.open("wb") as output:
            written, content_type = camera.download(photo, output)
            output.flush()
            os.fsync(output.fileno())
        actual = partial.stat().st_size
        if actual == 0:
            raise IOError("camera returned an empty file")
        media_type = (content_type or "").split(";", 1)[0].strip().lower()
        if media_type in {"application/json", "text/plain", "text/html", "application/xml"}:
            raise IOError(f"camera returned an error document ({media_type}), not a photo")
        if written != actual:
            raise IOError(f"transfer length mismatch: received {written}, stored {actual}")
        if photo.size is not None and actual != photo.size:
            raise IOError(f"size mismatch: expected {photo.size}, received {actual}")
        with partial.open("rb") as check:
            header = check.read(8)
        suffix = Path(photo.filename).suffix.lower()
        if suffix in {".jpg", ".jpeg"} and not header.startswith(b"\xff\xd8\xff"):
            raise IOError("camera response is not a JPEG file")
        if suffix in {".dng", ".pef"} and header[:4] not in {b"II*\x00", b"MM\x00*"}:
            raise IOError("camera response is not a TIFF-based DNG/PEF file")
        digest = _hash_file(partial)
        os.replace(partial, destination)
        try:
            dirfd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        except OSError:
            pass
        return destination, actual, digest
    except Exception:
        LOG.warning("download failed for %s; partial file retained at %s", photo.filename, partial)
        raise
