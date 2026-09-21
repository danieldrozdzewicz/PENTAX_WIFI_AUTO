from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

LOG = logging.getLogger(__name__)


def extract_jpeg_preview(
    raw_path: Path, max_side: int = 1920, quality: int = 85, *,
    destination_dir: Path | None = None, replace_existing: bool = False
) -> Path | None:
    """Extract the camera's embedded JPEG so Jellyfin can make a normal photo thumbnail."""
    if raw_path.suffix.lower() not in {".dng", ".pef"}:
        return None
    tool = shutil.which("simple_dcraw")
    ffmpeg = shutil.which("ffmpeg")
    if not tool:
        LOG.warning("simple_dcraw is missing; cannot make a Jellyfin preview for %s", raw_path.name)
        return None
    if not ffmpeg:
        LOG.warning("ffmpeg is missing; cannot resize the Jellyfin preview for %s", raw_path.name)
        return None

    preview = Path(str(raw_path) + ".thumb.jpg")
    destination_dir = destination_dir or raw_path.parent
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / raw_path.with_suffix(".jpg").name
    if destination.exists() and not replace_existing:
        return None
    encoded = destination.with_name(destination.name + ".part")
    qscale = max(2, min(31, round(31 - (quality - 1) * 29 / 99)))

    try:
        subprocess.run(
            [tool, "-e", str(raw_path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        with preview.open("rb") as source:
            if source.read(3) != b"\xff\xd8\xff":
                raise IOError("LibRaw did not produce a valid JPEG preview")
        subprocess.run(
            [
                ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(preview), "-map_metadata", "0",
                "-vf", f"scale={max_side}:{max_side}:force_original_aspect_ratio=decrease:force_divisible_by=2",
                "-frames:v", "1", "-c:v", "mjpeg", "-q:v", str(qscale),
                "-f", "image2", str(encoded),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        with encoded.open("rb") as source:
            if source.read(3) != b"\xff\xd8\xff":
                raise IOError("ffmpeg did not produce a valid resized JPEG preview")
        if destination.exists() and not replace_existing:
            return None
        os.replace(encoded, destination)
        return destination
    except (OSError, subprocess.SubprocessError) as exc:
        LOG.warning("cannot extract JPEG preview for %s: %s", raw_path.name, exc)
        return None
    finally:
        preview.unlink(missing_ok=True)
        encoded.unlink(missing_ok=True)
