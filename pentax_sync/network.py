from __future__ import annotations

import ipaddress
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from .config import Config

LOG = logging.getLogger(__name__)


class WifiController:
    def __init__(self, config: Config):
        self.config = config
        self.profile = Path("/var/lib/iwd") / (config.camera_ssid + ".psk")

    def _run(self, args: list[str], timeout: float = 15.0) -> subprocess.CompletedProcess:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)

    def install_profile(self) -> None:
        if not self.config.camera_ssid:
            return
        if not self.config.camera_wifi_password:
            raise ValueError("camera Wi-Fi password is not configured")
        self.profile.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        content = (
            "[Security]\n"
            "Passphrase=" + self.config.camera_wifi_password.replace("\n", "") + "\n"
            "[Settings]\nAutoConnect=true\n"
        )
        fd, tmpname = tempfile.mkstemp(prefix=".pentax-", dir=self.profile.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(tmpname, self.profile)
        finally:
            if os.path.exists(tmpname):
                os.unlink(tmpname)

    def connect(self) -> bool:
        if not self.config.camera_ssid:
            return False
        self.install_profile()
        if Path(self.config.connect_command).exists() is False:
            LOG.error("iwctl not found at %s", self.config.connect_command)
            return False
        self._run([self.config.connect_command, "device", self.config.wifi_interface, "set-property", "Powered", "on"])
        result = self._run([self.config.connect_command, "station", self.config.wifi_interface, "connect", self.config.camera_ssid], timeout=25)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().replace("\n", " ")
            LOG.debug("iwd connection attempt: %s", detail[:200])
        time.sleep(1)
        self.configure_address()
        return self.is_associated()

    def configure_address(self) -> None:
        udhcpc = self.config.udhcpc_command
        if Path(udhcpc).exists():
            subprocess.run(
                [udhcpc, "-q", "-n", "-T", "2", "-t", "2", "-i", self.config.wifi_interface,
                 "-s", self.config.udhcpc_script],
                capture_output=True, text=True, timeout=12, check=False,
            )
        if not self._has_ipv4():
            try:
                address = ipaddress.ip_interface(self.config.static_wifi_address)
                subprocess.run(["ip", "addr", "replace", str(address), "dev", self.config.wifi_interface], check=False)
            except ValueError:
                LOG.error("invalid CAMERA_STATIC_ADDRESS")

    def _has_ipv4(self) -> bool:
        result = subprocess.run(["ip", "-4", "addr", "show", "dev", self.config.wifi_interface], capture_output=True, text=True, check=False)
        return "inet " in result.stdout

    def is_associated(self) -> bool:
        result = self._run([self.config.connect_command, "station", self.config.wifi_interface, "show"], timeout=5)
        return result.returncode == 0 and re.search(r"State\s+connected", result.stdout, re.IGNORECASE) is not None

    def connected_ssid(self) -> str | None:
        result = self._run([self.config.connect_command, "station", self.config.wifi_interface, "show"], timeout=5)
        match = re.search(r"Connected network\s+(.+)", result.stdout)
        return match.group(1).strip() if match else (self.config.camera_ssid if self.is_associated() else None)

    def default_route_uses_wifi(self) -> bool:
        result = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, check=False)
        return any(
            line.lstrip().startswith("default ") and f"dev {self.config.wifi_interface}" in line
            for line in result.stdout.splitlines()
        )
