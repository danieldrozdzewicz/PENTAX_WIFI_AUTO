from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Any, BinaryIO

LOG = logging.getLogger(__name__)
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".dng", ".pef"}


class CameraError(RuntimeError):
    pass


@dataclass(frozen=True)
class RemotePhoto:
    camera_path: str
    filename: str
    size: int | None = None
    timestamp: str | None = None

    @property
    def remote_path(self) -> str:
        return "/".join(part for part in (self.camera_path.strip("/"), self.filename) if part)


def _key(mapping: dict[str, Any], names: tuple[str, ...]) -> Any:
    lowered = {str(key).lower(): value for key, value in mapping.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def _as_path(value: Any) -> str | None:
    if not isinstance(value, (str, int)):
        return None
    text = str(value).replace("\\", "/").strip("/")
    if not text or "\x00" in text:
        return None
    return text


def _photo_from_object(obj: dict[str, Any], prefix: str = "") -> RemotePhoto | None:
    name = _key(obj, ("filename", "fileName", "name", "file"))
    path_value = _key(obj, ("path", "fullPath", "filepath", "cameraPath"))
    directory_value = _key(obj, ("dir", "directory", "directoryName", "folder"))
    path = _as_path(path_value)
    directory = _as_path(directory_value)
    if isinstance(name, dict):
        name = _key(name, ("filename", "name", "file"))
    if not isinstance(name, str) and path and PurePosixPath(path).suffix.lower() in PHOTO_EXTENSIONS:
        name = PurePosixPath(path).name
    if not isinstance(name, str):
        return None
    name = name.replace("\\", "/").strip("/")
    if not name or PurePosixPath(name).suffix.lower() not in PHOTO_EXTENSIONS:
        return None
    if "/" in name:
        path = path or name
        name = PurePosixPath(name).name
    if path and PurePosixPath(path).suffix.lower() in PHOTO_EXTENSIONS:
        parent = PurePosixPath(path).parent.as_posix()
    else:
        parent = path or directory or prefix.strip("/")
    if parent == ".":
        parent = ""
    size_value = _key(obj, ("size", "filesize", "fileSize", "length", "bytes"))
    try:
        size = int(size_value) if size_value is not None else None
        if size is not None and size < 0:
            size = None
    except (TypeError, ValueError):
        size = None
    stamp = _key(obj, ("datetime", "dateTime", "timestamp", "date", "modified", "mtime"))
    return RemotePhoto(parent, name, size, str(stamp) if stamp is not None else None)


def extract_photos(payload: Any) -> list[RemotePhoto]:
    found: dict[str, RemotePhoto] = {}
    containers = {"photos", "files", "items", "images", "data", "list", "entries", "results", "content", "directories", "dirs"}

    def visit(node: Any, prefix: str = "") -> None:
        if isinstance(node, dict):
            photo = _photo_from_object(node, prefix)
            if photo:
                found[photo.remote_path.casefold()] = photo
                return
            directory_name = _key(node, ("name", "dir", "directory"))
            directory_files = _key(node, ("files",))
            if isinstance(directory_name, str) and isinstance(directory_files, list):
                next_prefix = "/".join(part for part in (prefix, directory_name.strip("/")) if part)
                visit(directory_files, next_prefix)
                return
            for key, value in node.items():
                if str(key).lower() in {"metadata", "status", "result", "error", "errors"}:
                    visit(value, prefix)
                    continue
                key_text = str(key).replace("\\", "/").strip("/")
                if PurePosixPath(key_text).suffix.lower() in PHOTO_EXTENSIONS:
                    leaf = PurePosixPath(key_text).name
                    parent = PurePosixPath(key_text).parent.as_posix()
                    if parent == ".":
                        parent = prefix
                    entry = dict(value) if isinstance(value, dict) else {}
                    entry.setdefault("name", leaf)
                    item = _photo_from_object(entry, parent)
                    if item:
                        found[item.remote_path.casefold()] = item
                    continue
                next_prefix = prefix if key_text.lower() in containers else "/".join(part for part in (prefix, key_text) if part)
                visit(value, next_prefix)
        elif isinstance(node, list):
            for item in node:
                visit(item, prefix)
        elif isinstance(node, str) and PurePosixPath(node).suffix.lower() in PHOTO_EXTENSIONS:
            item = _photo_from_object({"path": node if "/" in node else "", "name": node}, prefix)
            if item:
                found[item.remote_path.casefold()] = item

    visit(payload)
    return sorted(found.values(), key=lambda item: (item.camera_path.casefold(), item.filename.casefold()))


class PentaxCamera:
    def __init__(self, base_url: str, timeout: float = 5.0, download_timeout: float = 180.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.download_timeout = download_timeout

    def _open(self, url: str, timeout: float):
        request = urllib.request.Request(url, headers={"User-Agent": "pentax-sync/0.1"})
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CameraError(f"camera request failed: {exc}") from exc

    def get_json(self, endpoint: str) -> Any:
        with self._open(self.base_url + endpoint, self.timeout) as response:
            try:
                raw = response.read(4 * 1024 * 1024)
            except (TimeoutError, OSError) as exc:
                raise CameraError(f"camera response timed out at {endpoint}: {exc}") from exc
        try:
            return json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            snippet = raw[:160].decode("utf-8", "replace").replace("\n", " ")
            raise CameraError(f"camera returned non-JSON at {endpoint}: {snippet}") from exc

    def probe(self) -> dict[str, Any]:
        for endpoint in ("/v1/status", "/v1/ping", "/v1/apis"):
            try:
                value = self.get_json(endpoint)
                return {"endpoint": endpoint, "response": value}
            except CameraError:
                continue
        raise CameraError("camera API probe failed")

    def latest(self) -> RemotePhoto | None:
        payload = self.get_json("/v1/photos/latest/info")
        photos = extract_photos(payload)
        return max(photos, key=lambda photo: photo.timestamp or "") if photos else None

    def photo_info(self, photo: RemotePhoto) -> RemotePhoto:
        encoded = urllib.parse.quote(photo.remote_path, safe="/")
        payload = self.get_json("/v1/photos/" + encoded + "/info")
        photos = extract_photos(payload)
        match = next((item for item in photos if item.remote_path.casefold() == photo.remote_path.casefold()), None)
        if match is None or not match.timestamp:
            raise CameraError(f"camera did not provide a capture date for {photo.remote_path}")
        return replace(photo, timestamp=match.timestamp, size=match.size or photo.size)

    def list_photos(self) -> list[RemotePhoto]:
        payload = self.get_json("/v1/photos")
        photos = extract_photos(payload)
        if not photos:
            raise CameraError("/v1/photos responded, but no supported photo entries were parsed")
        return photos

    def download(self, photo: RemotePhoto, output: BinaryIO) -> tuple[int, str | None]:
        encoded = urllib.parse.quote(photo.remote_path, safe="/")
        url = self.base_url + "/v1/photos/" + encoded
        total = 0
        with self._open(url, self.download_timeout) as response:
            content_type = response.headers.get("Content-Type")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                total += len(chunk)
        return total, content_type


def filename_is_supported(name: str) -> bool:
    return PurePosixPath(name).suffix.lower() in PHOTO_EXTENSIONS
