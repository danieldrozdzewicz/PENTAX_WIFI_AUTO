from __future__ import annotations

import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from .camera import CameraError, PentaxCamera
from .config import Config
from .jellyfin import JellyfinClient
from .network import WifiController
from .state import StateStore
from .raw_preview import extract_jpeg_preview
from .storage import download_atomic

LOG = logging.getLogger(__name__)
BACKOFF = (1, 2, 5, 10, 30)


def _iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class SyncDaemon:
    def __init__(self, config: Config):
        self.config = config
        self.config.photo_root.mkdir(parents=True, exist_ok=True)
        self.wifi = WifiController(config)
        self.camera = PentaxCamera(
            config.camera_base_url,
            timeout=config.camera_request_timeout,
            download_timeout=config.camera_download_timeout,
        )
        self.jellyfin = JellyfinClient(config.jellyfin_url, config.jellyfin_api_key)
        self.state = StateStore(config.state_dir / "state.db")
        configured_date = config.photo_date_from.isoformat() if config.photo_date_from else ""
        if self.state.get_meta("photo_date_from") != configured_date:
            self.state.clear_filtered()
            self.state.set_meta("photo_date_from", configured_date)
        self.status = {
            "state": "WAIT_CONFIG" if not config.camera_ssid else "WAIT_WIFI",
            "camera_online": False,
            "wifi_ssid": None,
            "camera_ip": config.camera_base_url.removeprefix("http://").split("/")[0],
            "last_sync": None,
            "last_file": self.state.last_file(),
            "pending": 0,
            "downloaded_total": self.state.counts()[0],
            "jellyfin": "UNKNOWN",
            "updated_at": _iso_now(),
        }
        self.last_new_photo: float | None = None
        self.last_full_scan = 0.0
        self.last_latest_key: str | None = None
        self.next_connect = 0.0
        self.next_probe = 0.0
        self.connect_failures = 0
        self.probe_failures = 0
        self.last_refresh = time.monotonic()
        self.last_health_check = 0.0
        self.last_list_fallback = 0.0
        self.running = True

    def close(self) -> None:
        self.state.close()

    def _write_status(self) -> None:
        total, pending = self.state.counts()
        self.status.update(
            pending=pending,
            downloaded_total=total,
            last_file=self.state.last_file(),
            updated_at=_iso_now(),
        )
        if time.monotonic() - self.last_health_check >= 60:
            self.status["jellyfin"] = self.jellyfin.health()
            self.last_health_check = time.monotonic()
        target = self.config.status_file
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)

    def _scan_and_download(self) -> int:
        photos = self.camera.list_photos()
        self.last_full_scan = time.monotonic()
        if self.config.initial_sync == "baseline" and self.state.get_meta("initial_baseline_done") is None:
            for photo in photos:
                self.state.mark(photo, "BASELINED")
            self.state.set_meta("initial_baseline_done", "1")
            self.status["last_sync"] = _iso_now()
            self.state.set_meta("last_sync", self.status["last_sync"])
            LOG.info("initial camera baseline recorded: %d existing files; they were not imported", len(photos))
            return 0
        jpeg_stems = {
            Path(photo.filename).stem.casefold()
            for photo in photos
            if Path(photo.filename).suffix.lower() in {".jpg", ".jpeg"}
        }
        downloaded = 0
        for photo in photos:
            if self.state.is_processed(photo):
                continue
            self.state.mark(photo, "DISCOVERED")
            started = time.monotonic()
            try:
                if self.config.photo_date_from is not None:
                    if not photo.timestamp:
                        photo = self.camera.photo_info(photo)
                    captured_at = datetime.fromisoformat(photo.timestamp)
                    if captured_at.tzinfo is not None:
                        captured_at = captured_at.astimezone().replace(tzinfo=None)
                    if captured_at < self.config.photo_date_from:
                        self.state.mark(photo, "FILTERED")
                        LOG.info("skipped %s captured before PHOTO_DATE_FROM (%s)", photo.filename, photo.timestamp)
                        continue
                self.state.mark(photo, "DOWNLOADING")
                is_jpeg = Path(photo.filename).suffix.lower() in {".jpg", ".jpeg"}
                jpeg_root = (
                    self.config.photo_root / self.config.jpeg_subdir
                    if self.config.jpeg_subdir else self.config.photo_root
                )
                download_root = jpeg_root if is_jpeg else self.config.photo_root
                destination, size, digest = download_atomic(self.camera, photo, download_root)
                self.state.mark(photo, "DOWNLOADED", local_path=str(destination), size=size, sha256=digest)
                LOG.info("downloaded %s (%d bytes, %.1f s)", photo.filename, size, time.monotonic() - started)
                if (destination.suffix.lower() in {".dng", ".pef"}
                        and Path(photo.filename).stem.casefold() not in jpeg_stems):
                    preview = extract_jpeg_preview(
                        destination,
                        max_side=self.config.raw_preview_max_side,
                        quality=self.config.raw_preview_quality,
                        destination_dir=jpeg_root,
                    )
                    if preview:
                        LOG.info("created Jellyfin JPEG preview %s", preview.name)
                self.last_new_photo = time.monotonic()
                self.status["last_file"] = photo.filename
                downloaded += 1
            except CameraError as exc:
                self.state.mark(photo, "FAILED", error=str(exc)[:500])
                LOG.warning("camera interrupted while checking %s; scan will resume: %s", photo.filename, exc)
                raise
            except Exception as exc:
                self.state.mark(photo, "FAILED", error=str(exc)[:500])
                LOG.warning("will retry %s: %s", photo.filename, exc)
            finally:
                # A large camera inventory can take many minutes; keep Jellyfin
                # and the visible status current while each RAW is transferred.
                self._refresh_if_due()
                self._write_status()
        if downloaded:
            self.status["last_sync"] = _iso_now()
            self.state.set_meta("last_sync", self.status["last_sync"])
        return downloaded

    def _refresh_if_due(self) -> None:
        now = time.monotonic()
        new_photos_ready = (
            self.last_new_photo is not None
            and now - self.last_new_photo >= self.config.jellyfin_refresh_debounce
        )
        periodic_refresh_due = now - self.last_refresh >= self.config.jellyfin_refresh_interval
        if not new_photos_ready and not periodic_refresh_due:
            return
        self.jellyfin.refresh()
        self.last_refresh = time.monotonic()
        self.last_new_photo = None

    def _retry_delay(self, failures: int) -> float:
        delay = BACKOFF[min(failures, len(BACKOFF) - 1)]
        return min(delay, self.config.connect_retry_interval)

    def run(self) -> None:
        if self.config.camera_ssid:
            LOG.info("Pentax sync daemon started; target %s", self.config.photo_root)
        else:
            LOG.warning("camera Wi-Fi is not configured yet; service will wait")
        self._write_status()
        try:
            while self.running:
                now = time.monotonic()
                if not self.config.camera_ssid:
                    self.status.update(state="WAIT_CONFIG", camera_online=False, wifi_ssid=None)
                    self._write_status()
                    time.sleep(30)
                    continue

                if now >= self.next_connect and not self.wifi.is_associated():
                    self.status.update(state="CONNECTING", camera_online=False, wifi_ssid=None)
                    try:
                        if self.wifi.connect():
                            LOG.info("connected to camera Wi-Fi %s", self.config.camera_ssid)
                            self.status.update(state="CAMERA_CHECK", wifi_ssid=self.wifi.connected_ssid())
                            self.connect_failures = 0
                            self.next_connect = time.monotonic() + 3
                        else:
                            delay = self._retry_delay(self.connect_failures)
                            self.connect_failures += 1
                            jittered = delay + random.random()
                            self.next_connect = time.monotonic() + jittered
                            LOG.debug("camera Wi-Fi unavailable; retry in %.0f s", jittered)
                    except Exception as exc:
                        delay = self._retry_delay(self.connect_failures)
                        self.connect_failures += 1
                        self.next_connect = time.monotonic() + delay + random.random()
                        LOG.warning("Wi-Fi connect attempt failed: %s", exc)

                if self.wifi.is_associated() and now >= self.next_probe:
                    try:
                        self.camera.probe()
                        if not self.status.get("camera_online"):
                            LOG.info("camera API is online at %s", self.config.camera_base_url)
                            self.last_full_scan = 0.0
                        self.status.update(state="SYNC", camera_online=True, wifi_ssid=self.wifi.connected_ssid())
                        self.probe_failures = 0
                        self.next_probe = now + self.config.poll_interval
                        latest_supported = True
                        try:
                            latest = self.camera.latest()
                            key = latest.remote_path + ":" + str(latest.size) + ":" + str(latest.timestamp) if latest else None
                        except CameraError as exc:
                            latest_supported = False
                            key = None
                            if now - self.last_list_fallback >= 60:
                                LOG.info("latest-info unavailable; using periodic photo listing (%s)", exc)
                                self.last_list_fallback = now
                        changed = latest_supported and key is not None and key != self.last_latest_key
                        if changed:
                            self.last_latest_key = key
                        scan_due = now - self.last_full_scan >= (self.config.full_scan_interval if latest_supported else 10)
                        if changed or scan_due:
                            self._scan_and_download()
                        self.status["last_sync"] = self.state.get_meta("last_sync")
                    except CameraError as exc:
                        if self.status.get("camera_online"):
                            LOG.warning("camera API disconnected: %s", exc)
                        self.status.update(state="WAIT_WIFI", camera_online=False)
                        delay = self._retry_delay(self.probe_failures)
                        self.probe_failures += 1
                        self.next_probe = time.monotonic() + delay
                        self.last_full_scan = 0.0
                    except Exception:
                        LOG.exception("unexpected synchronization error")
                        self.next_probe = time.monotonic() + 5

                if self.wifi.default_route_uses_wifi():
                    LOG.error("default route unexpectedly uses Wi-Fi; refusing further network changes")
                    self.status["state"] = "ROUTE_GUARD"
                self._refresh_if_due()
                self._write_status()
                time.sleep(self.config.poll_interval)
        finally:
            self.close()


def configure_logging(level: str) -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=numeric, format="%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
