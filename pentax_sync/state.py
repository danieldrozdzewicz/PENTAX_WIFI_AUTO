from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class StateStore:
    def __init__(self, filename: Path):
        filename.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(filename, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS photos (
                camera_path TEXT NOT NULL,
                filename TEXT NOT NULL,
                size INTEGER,
                camera_timestamp TEXT,
                local_path TEXT,
                sha256 TEXT,
                downloaded_at REAL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                PRIMARY KEY (camera_path, filename)
            );
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def is_downloaded(self, photo) -> bool:
        row = self.db.execute(
            "SELECT size, camera_timestamp, local_path FROM photos WHERE camera_path=? AND filename=? AND status='DOWNLOADED'",
            (photo.camera_path, photo.filename),
        ).fetchone()
        if not row:
            return False
        old_size = row["size"]
        old_stamp = row["camera_timestamp"]
        if photo.size is not None and old_size is not None and int(photo.size) != int(old_size):
            return False
        if photo.timestamp and old_stamp and photo.timestamp != old_stamp:
            return False
        return bool(row["local_path"] and Path(row["local_path"]).is_file())

    def is_processed(self, photo) -> bool:
        row = self.db.execute(
            "SELECT size,camera_timestamp,local_path,status FROM photos WHERE camera_path=? AND filename=?",
            (photo.camera_path, photo.filename),
        ).fetchone()
        if not row or row["status"] not in {"DOWNLOADED", "BASELINED", "FILTERED"}:
            return False
        if photo.size is not None and row["size"] is not None and int(photo.size) != int(row["size"]):
            return False
        if photo.timestamp and row["camera_timestamp"] and photo.timestamp != row["camera_timestamp"]:
            return False
        if row["status"] in {"BASELINED", "FILTERED"}:
            return True
        return bool(row["local_path"] and Path(row["local_path"]).is_file())

    def mark(self, photo, status: str, *, local_path: str | None = None,
             size: int | None = None, sha256: str | None = None, error: str | None = None) -> None:
        self.db.execute(
            """INSERT INTO photos(camera_path,filename,size,camera_timestamp,local_path,sha256,downloaded_at,status,attempts,last_error)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(camera_path,filename) DO UPDATE SET
                 size=CASE WHEN excluded.status IN ('BASELINED','FILTERED') THEN excluded.size ELSE COALESCE(excluded.size,photos.size) END,
                 camera_timestamp=CASE WHEN excluded.status IN ('BASELINED','FILTERED') THEN excluded.camera_timestamp ELSE COALESCE(excluded.camera_timestamp,photos.camera_timestamp) END,
                 local_path=CASE WHEN excluded.status IN ('BASELINED','FILTERED') THEN NULL ELSE COALESCE(excluded.local_path,photos.local_path) END,
                 sha256=CASE WHEN excluded.status IN ('BASELINED','FILTERED') THEN NULL ELSE COALESCE(excluded.sha256,photos.sha256) END,
                 downloaded_at=CASE WHEN excluded.status IN ('BASELINED','FILTERED') THEN NULL ELSE COALESCE(excluded.downloaded_at,photos.downloaded_at) END,
                 status=excluded.status,
                 attempts=photos.attempts + CASE WHEN excluded.status='FAILED' THEN 1 ELSE 0 END,
                 last_error=excluded.last_error""",
            (photo.camera_path, photo.filename, size if size is not None else photo.size,
             photo.timestamp, local_path, sha256, time.time() if status == "DOWNLOADED" else None,
             status, 0, error),
        )
        self.db.commit()

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self.db.commit()

    def clear_filtered(self) -> None:
        self.db.execute("DELETE FROM photos WHERE status='FILTERED'")
        self.db.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def counts(self) -> tuple[int, int]:
        total = self.db.execute("SELECT count(*) FROM photos WHERE status='DOWNLOADED'").fetchone()[0]
        pending = self.db.execute("SELECT count(*) FROM photos WHERE status IN ('DISCOVERED','DOWNLOADING','FAILED')").fetchone()[0]
        return total, pending

    def last_file(self) -> str | None:
        row = self.db.execute("SELECT filename FROM photos WHERE status='DOWNLOADED' ORDER BY downloaded_at DESC LIMIT 1").fetchone()
        return row[0] if row else None
