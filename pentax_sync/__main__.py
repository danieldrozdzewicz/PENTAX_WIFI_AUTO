from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import Config
from .sync import SyncDaemon, configure_logging


def show_status(config: Config) -> int:
    if not config.status_file.exists():
        print("Pentax sync: not running (no runtime status)")
        return 1
    try:
        status = json.loads(config.status_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Cannot read status: {exc}")
        return 1
    print(f"State: {status.get('state', 'UNKNOWN')}")
    print(f"Camera: {'ONLINE' if status.get('camera_online') else 'OFFLINE'}")
    print(f"Wi-Fi: {status.get('wifi_ssid') or 'disconnected'}")
    print(f"Camera IP: {status.get('camera_ip') or 'unknown'}")
    print(f"Last sync: {status.get('last_sync') or 'never'}")
    print(f"Last file: {status.get('last_file') or 'none'}")
    print(f"Pending: {status.get('pending', 0)}")
    print(f"Downloaded total: {status.get('downloaded_total', 0)}")
    print(f"Jellyfin: {status.get('jellyfin', 'UNKNOWN')}")
    print(f"Updated: {status.get('updated_at', 'unknown')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pentax-sync")
    parser.add_argument("--config", default="/etc/pentax-sync.env")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true")
    group.add_argument("--validate-config", action="store_true")
    parser.add_argument("command", nargs="?", choices=("run",), default="run")
    args = parser.parse_args(argv)
    try:
        config = Config.load(args.config)
        config.validate()
    except (OSError, ValueError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    if args.status:
        return show_status(config)
    if args.validate_config:
        print("Configuration syntax OK")
        print(f"Photo root: {config.photo_root}")
        print(f"Camera SSID configured: {'yes' if config.camera_ssid else 'no'}")
        print(f"Jellyfin API key configured: {'yes' if config.jellyfin_api_key else 'no'}")
        return 0
    configure_logging(config.log_level)
    daemon = SyncDaemon(config)
    try:
        daemon.run()
    except KeyboardInterrupt:
        logging.info("stopping")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
