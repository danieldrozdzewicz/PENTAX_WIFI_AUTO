# Pentax K-70 Wi-Fi photo sync

# PROMPT   WIFI PENTAX

Small Python daemon for copying new Pentax K-70 files over its own Wi-Fi AP to Alpine Linux. It uses the existing `iwd` service and OpenRC. Camera discovery and the first real transfer still need to be verified against this K-70 firmware before the service can be considered production-ready.

## Current host layout

- Ethernet remains on `eth0`, `192.168.1.150/24`, with the default gateway on `192.168.1.1`.
- Camera Wi-Fi uses `wlan0` and must never become the default route.
- Photo destination is `/home/daniel/media/fotex/DIRECT`, exposed as `\\192.168.1.150\daniel\media\fotex\DIRECT` through Samba's `daniel` home share.
- Jellyfin runs locally at `http://127.0.0.1:8096`.
- State database is `/var/lib/pentax-sync/state.db`.

## Features

- Polls camera status and `/v1/photos/latest/info`; periodically lists `/v1/photos` to recover missed events.
- Downloads JPG/JPEG, DNG and PEF files sequentially. On first camera listing it records existing card contents as a baseline; set `INITIAL_SYNC=all` to import those files too.
- Tracks camera path and filename in SQLite and recovers after restarts.
- Writes to `.part`, fsyncs, checks the received size when the API provides it, hashes SHA-256, then atomically renames.
- Retries missed files on later listings; does not delete anything from the camera.
- Optional inclusive `PHOTO_DATE_FROM` date/time filter; the K-70 per-photo metadata endpoint supplies capture dates when the list omits them.
- Extracts the DNG/PEF embedded JPEG with `simple_dcraw` when installed, so Jellyfin can display a normal JPEG photo alongside each RAW. The RAW remains untouched.
- IWD profile uses mode 0600. DHCP config ignores the router option, so it does not install a default route over Wi-Fi.
- Waits for the camera AP to become available and retries after the camera disconnects or Alpine resumes.
- Can request a debounced Jellyfin library refresh with an API key.

## Install on Alpine

Required components on this host are already present: Python 3, SQLite's Python module, `iwd`, BusyBox `udhcpc`, and OpenRC. The code uses only Python standard library modules.

For Jellyfin previews of DNG/PEF, install Alpine's `libraw-tools` package; the daemon uses its `simple_dcraw` executable to extract each RAW's embedded JPEG, then uses `ffmpeg` to resize and recompress it. All camera JPEGs and generated previews are stored in a `_jpeg` subfolder under `PHOTO_ROOT`; RAW files remain directly under `PHOTO_ROOT`. Jellyfin's current Skia image encoder cannot decode this camera's DNG directly, even though it recognizes the DNG extension.

Preview settings are in `/etc/pentax-sync.env` on the Alpine host, not in Jellyfin's UI. `RAW_PREVIEW_MAX_SIDE` caps either image dimension in pixels (default 1920); `RAW_PREVIEW_QUALITY` is 1–100 (default 70, lower means stronger JPEG compression). Restart `pentax-sync` after changing either value. Existing previews must be regenerated to apply changed settings.

Copy the `pentax_sync` directory to `/opt/pentax-sync/pentax_sync`, copy `udhcpc-script` to `/opt/pentax-sync/udhcpc-script`, and copy `etc/init.d/pentax-sync` to `/etc/init.d/pentax-sync`. Then:

    chmod 0755 /opt/pentax-sync/udhcpc-script /etc/init.d/pentax-sync
    cp etc/pentax-sync.env.example /etc/pentax-sync.env
    chmod 0600 /etc/pentax-sync.env
    rc-update add pentax-sync default

Set the camera SSID and Wi-Fi passphrase in `/etc/pentax-sync.env`. Do not put secrets in source files. Confirm `CAMERA_BASE_URL` with the camera before relying on the default `192.168.0.1`.

Set Jellyfin's API key in that same root-only config file. The daemon requests a library refresh after a quiet period following new downloads and at `JELLYFIN_REFRESH_INTERVAL` (default 300 seconds). During long camera transfers it checks after each file, so the library can refresh while the rest of the card is still downloading. Without a key, downloads still work and the daemon logs that refresh was skipped.

Set `PHOTO_DATE_FROM=YYYY-MM-DD` to accept photos captured on or after that date. You can include a time, for example `PHOTO_DATE_FROM=2026-09-20T14:30:00`; camera-local time is compared with the server's local time. The date is inclusive. Leave it empty to disable date filtering. When the configured value changes, previously filtered entries are reconsidered. With `INITIAL_SYNC=baseline`, the first inventory is still just a baseline and existing card contents are not imported.

Start and inspect:

    rc-service pentax-sync start
    rc-service pentax-sync status
    /usr/bin/python3 -m pentax_sync --status
    tail -f /var/log/pentax-sync.log

## Networking safety

The camera is an access point for `wlan0`; Ethernet keeps the LAN and default route. The DHCP helper assigns only the camera interface address and deliberately does not install a router or DNS setting. It falls back to `CAMERA_STATIC_ADDRESS` only if the camera did not give the interface an IPv4 address. Check `ip route` before and after first connection. If the camera uses a different subnet, change the static fallback only after confirming its address.

## Jellyfin photos and RAW files

Jellyfin 10.11.11 lists DNG and PEF among its supported photo input extensions. Actual thumbnail decoding can still depend on the installed image encoder, so confirm with one real file. The `jellyfin` OS account already has read and traverse permission on the destination folder. The existing library named `Zdjęcia` currently points to `/home/daniel/media/fotex/Do Oglądania`; it does not yet include `DIRECT`. Add `/home/daniel/media/fotex/DIRECT` as another folder path in that library, or add a separate Photos library for it. This requires an admin session in Jellyfin. Then set `JELLYFIN_API_KEY` so the daemon can request debounced refreshes.

## Camera protocol discovery

After joining the K-70 AP, probe these read-only endpoints first:

    curl -v http://192.168.0.1/v1/status
    curl -v http://192.168.0.1/v1/apis
    curl -v http://192.168.0.1/v1/photos/latest/info
    curl -v http://192.168.0.1/v1/photos

The K-70 has been verified live: its AP and `/v1` API respond, `/v1/photos` lists files under `dirs[].name` and `dirs[].files`, and a DNG download returns TIFF data. The daemon downloads via `/v1/photos/<camera-path>`. Do not issue camera-control or delete requests.

## Status and recovery

`--status` reads the runtime status file and SQLite counts. The daemon lists the camera card on first availability, when the latest photo changes, and periodically thereafter. This recovers files made while the camera or server was unavailable. It retries incomplete files on later scans; `.part` is never exposed as a complete image.

For troubleshooting, check:

- `iwctl station wlan0 show` and `rfkill list` for Wi-Fi state.
- `ip route` to confirm the default route still points at `eth0`.
- `curl http://192.168.0.1/v1/status` to distinguish Wi-Fi association from API availability.
- `ls -l /home/daniel/media/fotex/DIRECT` and `/var/log/pentax-sync.log` for file and transfer state.
- Jellyfin's photo library root and its logs if files exist but thumbnails are missing.
- A 401 from Jellyfin means the API key is absent or invalid; update `JELLYFIN_API_KEY` and restart the service.

## Upgrade and uninstall

Stop the service before replacing code, preserve `/etc/pentax-sync.env` and `/var/lib/pentax-sync/state.db`, then copy the new package and restart. To uninstall, stop and remove the OpenRC service and code directory. Keep the state database and downloaded images unless you intentionally want to remove them. Removing an IWD profile requires a separate explicit choice because it contains the camera Wi-Fi credential.

## Known unverified items

The AP and photo-list/download endpoints have been checked against the user's K-70. Initial unattended operation still needs one newly captured photo to verify the full path through Samba and Jellyfin. The daemon polls `latest/info` and rescans the card periodically, so it can recover files made while disconnected.
