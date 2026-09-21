from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

LOG = logging.getLogger(__name__)


class JellyfinClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def refresh(self) -> bool:
        if not self.api_key:
            LOG.warning("Jellyfin refresh skipped: JELLYFIN_API_KEY is not configured")
            return False
        request = urllib.request.Request(
            self.base_url + "/Library/Refresh",
            data=b"",
            method="POST",
            headers={"X-Emby-Token": self.api_key, "Content-Length": "0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if response.status not in (200, 202, 204):
                    LOG.warning("Jellyfin refresh returned HTTP %s", response.status)
                    return False
            LOG.info("Jellyfin library refresh requested")
            return True
        except urllib.error.HTTPError as exc:
            LOG.warning("Jellyfin refresh failed: HTTP %s", exc.code)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            LOG.warning("Jellyfin refresh failed: %s", exc)
        return False

    def health(self) -> str:
        request = urllib.request.Request(self.base_url + "/System/Info/Public")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                json.loads(response.read(1024 * 1024))
            return "OK"
        except Exception:
            return "OFFLINE"
