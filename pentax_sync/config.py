from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


@dataclass(frozen=True)
class Config:
    camera_ssid: str
    camera_wifi_password: str
    camera_base_url: str
    wifi_interface: str
    photo_root: Path
    state_dir: Path
    status_file: Path
    jellyfin_url: str
    jellyfin_api_key: str
    jellyfin_refresh_debounce: float
    jellyfin_refresh_interval: float
    poll_interval: float
    full_scan_interval: float
    connect_retry_interval: float
    initial_sync: str
    photo_date_from: datetime | None
    raw_preview_max_side: int
    raw_preview_quality: int
    connect_command: str
    udhcpc_command: str
    static_wifi_address: str
    log_level: str

    @classmethod
    def load(cls, path: str | Path = "/etc/pentax-sync.env") -> "Config":
        values = _read_env_file(Path(path))
        values.update(os.environ)

        def val(key: str, default: str = "") -> str:
            return values.get(key, default).strip()

        date_from = val("PHOTO_DATE_FROM")
        try:
            parsed_date_from = datetime.fromisoformat(date_from) if date_from else None
        except ValueError as exc:
            raise ValueError("PHOTO_DATE_FROM must be YYYY-MM-DD or ISO local date/time") from exc
        if parsed_date_from and parsed_date_from.tzinfo is not None:
            parsed_date_from = parsed_date_from.astimezone().replace(tzinfo=None)

        return cls(
            camera_ssid=val("CAMERA_SSID"),
            camera_wifi_password=val("CAMERA_WIFI_PASSWORD"),
            camera_base_url=val("CAMERA_BASE_URL", "http://192.168.0.1").rstrip("/"),
            wifi_interface=val("WIFI_INTERFACE", "wlan0"),
            photo_root=Path(val("PHOTO_ROOT", "/home/daniel/media/fotex/DIRECT")),
            state_dir=Path(val("STATE_DIR", "/var/lib/pentax-sync")),
            status_file=Path(val("STATUS_FILE", "/run/pentax-sync/status.json")),
            jellyfin_url=val("JELLYFIN_URL", "http://127.0.0.1:8096").rstrip("/"),
            jellyfin_api_key=val("JELLYFIN_API_KEY"),
            jellyfin_refresh_debounce=max(1.0, float(val("JELLYFIN_REFRESH_DEBOUNCE", "10"))),
            jellyfin_refresh_interval=max(60.0, float(val("JELLYFIN_REFRESH_INTERVAL", "300"))),
            poll_interval=max(1.0, float(val("POLL_INTERVAL", "2"))),
            full_scan_interval=max(10.0, float(val("FULL_SCAN_INTERVAL", "60"))),
            connect_retry_interval=max(3.0, float(val("CONNECT_RETRY_INTERVAL", "12"))),
            initial_sync=val("INITIAL_SYNC", "baseline").lower(),
            photo_date_from=parsed_date_from,
            raw_preview_max_side=int(val("RAW_PREVIEW_MAX_SIDE", "1920")),
            raw_preview_quality=int(val("RAW_PREVIEW_QUALITY", "85")),
            connect_command=val("IWCTL_COMMAND", "/usr/bin/iwctl"),
            udhcpc_command=val("UDHCPC_COMMAND", "/sbin/udhcpc"),
            static_wifi_address=val("CAMERA_STATIC_ADDRESS", "192.168.0.2/24"),
            log_level=val("LOG_LEVEL", "INFO").upper(),
        )

    def validate(self) -> None:
        if self.camera_ssid:
            if any(ch in self.camera_ssid for ch in "\x00\r\n/\\"):
                raise ValueError("CAMERA_SSID contains a forbidden character")
            if not self.camera_wifi_password:
                raise ValueError("CAMERA_WIFI_PASSWORD is required when CAMERA_SSID is set")
            if "\x00" in self.camera_wifi_password or "\r" in self.camera_wifi_password or "\n" in self.camera_wifi_password:
                raise ValueError("CAMERA_WIFI_PASSWORD contains a forbidden line break")
        if not self.camera_base_url.startswith("http://"):
            raise ValueError("CAMERA_BASE_URL must use http:// for the camera's local API")
        if self.initial_sync not in {"baseline", "all"}:
            raise ValueError("INITIAL_SYNC must be baseline or all")
        if not 320 <= self.raw_preview_max_side <= 12000:
            raise ValueError("RAW_PREVIEW_MAX_SIDE must be between 320 and 12000 pixels")
        if not 1 <= self.raw_preview_quality <= 100:
            raise ValueError("RAW_PREVIEW_QUALITY must be between 1 and 100")
