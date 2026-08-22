# Agent Operator Guide

How to configure, run, and manage a `dam-agent` device day to day, and
what to do when hardware changes. Companion references:
`rpi-camera-list.md` (camera modules), `rpi-lens-list.md` (lenses),
`rpi-agent-security.md` (security model), `camera-info.md` (per-module
operational notes).

Throughout, `<pi>` is the device's IP or `.local` hostname and
`<webapp-host>` is the days-in-a-minute frontend host.

## 1. The `.env` file — per-device configuration

Every device runs **identical agent code**; all per-device behavior comes
from `/opt/dam-agent/.env.{stage}` (dev fleet: `.env.dev`, mode 600).
The committed template `.env.example` documents every key. There is no
plain `.env`.

Required keys:

```
DEVICE_ID=dam-imx477-45-1        # = hostname (naming convention below)
TIMEZONE=Asia/Seoul              # IANA tz — drives all local dates/keys
UPLOAD_SIGNER_URL=https://<signer-host>
DEVICE_TOKEN=<issued token>      # written once via SSH, never displayed
```

Common optional keys (defaults in `agent/config.py`):

| Key | Default | When to set |
| --- | ------- | ----------- |
| `VIDEO_MINUTES` | 1 | length of the daily video (drives interval) |
| `CAPTURE_SIZE` | 1280,720 | output resolution |
| `VIEWER_PORT` | 8080 | 0 disables the live viewer |
| `TEMP_*` | 75/80/75/85 | thermal thresholds; `TEMP_SHUTDOWN_ENABLED=false` on remote devices |
| `RAW_SIZE` | auto | pin the sensor mode when auto-pick crops the FoV (see §5) |
| `TUNING_FILE` | auto | e.g. `imx219_noir.json` on filterless NoIR modules |
| `MAX_EXPOSURE_MS` | 0 | frame-duration ceiling for night mode |
| `NIGHT_EXPOSURE_MS` / `NIGHT_GAIN` | 0 / 8 | manual night mode on low-light sensors (IMX462: `250`/`2` measured best for indoor-lit sites). While night mode is on, every capture cycle starts with an **AE metering probe** (~3 s) so the exit decision reads true lux — without it a fixed night exposure saturates at dawn and exits ~25 min late (fixed 2026-08-23) |

`LOCATION_ID` stays **empty**: the location assignment and the capture
window are operator-owned in the cloud control plane (§3), not on the
device.

After editing the env file, restart the service (§2) — the agent reads
it only at startup.

## 2. Service control on the device

The agent runs as the `dam-agent` systemd service
(`ExecStart=/opt/dam-agent/.venv/bin/python -m agent.main`,
`WorkingDirectory=/opt/dam-agent`).

```
ssh cskim@<pi>
systemctl status dam-agent            # state + recent log lines
sudo systemctl stop dam-agent         # camera released immediately
sudo systemctl start dam-agent
sudo systemctl restart dam-agent      # after any .env change
journalctl -u dam-agent -f            # follow the log
journalctl -u dam-agent --since "-1 hour" | grep -E "uploaded|night|window"
```

Useful log lines: `uploaded key=…` (frame landed), `skipped … reason=`
(paused/unassigned — deliberate), `capture window idle until …`,
`night mode ON/OFF`, `capture interval Ns -> Ms (window change)`.

Deploy new agent code from the dev machine (code + `shared/` + unit file,
then restart): `scripts/deploy.sh <pi>`.

The live viewer is at `http://<pi>:8080/` — it streams ~1 frame/s while
open (preview boost) without affecting the upload cadence; `/healthz`
returns the full status JSON.

**Stopping capture is normally done from the frontend, not systemd**:
the operator "capturing" switch pauses uploads server-side while the
device stays online and heartbeating.

## 3. Managing agents from the DAM frontend

`https://<webapp-host>/manage/devices` (admin login):

- **Fleet table**: health badge, location, capturing state, camera,
  temperature, **power** (core volts + under-voltage state: plain =
  clean, `dip` = sagged since boot, red `LOW NOW` = supply problem —
  swap the 5 V adapter/cable), last seen. Auto-refreshes every 30 s.
- **Device page** (click a row; ←/→ steps through the fleet):
  - **Latest frame** — refreshes each capture interval. *Live view
    (LAN)* link appears when `access.ip` is set (operator-maintained:
    devices behind NAT can't know their reachable address — update it
    after any network change).
  - **Location (Post)** — assigning is what turns uploads on; the Post
    must exist first (Post name = location id, the join key for image
    prefixes and video filenames). Unassigned devices capture but skip
    uploads.
  - **Capturing on/off** — operator pause (agent skips, no retry).
  - **Daily video window** — `start`–`end` (device-local). This GATES
    capture: the agent rests outside the window and adapts its interval
    so the window still fills `VIDEO_MINUTES` of video (24 h → 48 s,
    12 h → 24 s). Devices resting outside their window show **ok**, not
    stale.
  - **Token rotate** — new token hash server-side; put the new plaintext
    into the device's `.env` over SSH and restart (§1). The old token
    dies immediately.
- **Health states**: `ok` → `stale` (alive but no frames landing while
  capture is expected — real malfunction) → `offline` (no heartbeats);
  `suspect` (≥5 damaged uploads in 24 h), `quarantined` (reserved).

## 4. Provisioning a new device (summary)

1. Flash Bookworm with the standard first-boot config (`custom.toml`
   with baked Wi-Fi — the card joins the network on first boot; see the
   imaging recipe in the fleet memory / scratchpad `flash-sd.ps1`).
2. Boot; find it as `dam-new.local`; verify the camera with
   `rpicam-still --list-cameras` (apply a sensor recipe from §5 if not
   detected).
3. Rename to the convention `dam-{sensor}[-{maxFoV°}][-z{N}]`
   (`hostnamectl`, `/etc/hosts`, restart avahi).
4. `ssh cskim@<pi> 'bash -s' < scripts/provision-pi.sh` (installs
   picamera2 + venv + poweroff sudoers).
5. Issue a token (`scripts/aws/issue_device_token.py`-style: hash to
   DynamoDB, plaintext piped straight into the device `.env` — never
   displayed), write the full `.env.dev`, then `scripts/deploy.sh <pi>`.
6. Create the Post in the frontend and assign the device; seed
   `last_video` to yesterday so the builder doesn't chase empty days;
   set `access.ip` for the Live view link.

## 5. When the camera or lens changes

**A. Identify what libcamera sees** (stop the agent first — it owns the
camera): `rpicam-still --list-cameras`. Match the sensor against
`rpi-camera-list.md`.

**B. Sensor-specific recipes** (config.txt + env):

| Sensor | Needs |
| ------ | ----- |
| IMX477 (HQ cam) | nothing — auto-detected; stock AE policy |
| IMX219 Arducam clone (UC-958) | `camera_auto_detect=0` + `dtoverlay=imx219` in `/boot/firmware/config.txt`; `RAW_SIZE=1640,1232` (the auto-picked 1080p mode crops FoV to ~60% and unbinned pixels inflate night noise/video size); NoIR variant: `TUNING_FILE=imx219_noir.json` |
| OV5647 (v1.3 / UC-261 / IR cam) | auto-detected; `RAW_SIZE=1296,972` (1080p mode crops FoV to 74% width) |
| IMX462 Pivariety (UC-955) | full recipe: `dtoverlay=arducam-pivariety`, copy `imx462.json` → `arducam-pivariety.json` in `/usr/share/libcamera/ipa/rpi/vc4/`, MCU firmware update if modes read `2147485568x1080` (needs i2c enabled); night mode env (`MAX_EXPOSURE_MS=5000`, `NIGHT_EXPOSURE_MS=250`, `NIGHT_GAIN=2`) |

**C. Rename to match the new hardware** (convention:
`dam-{sensor}[-{maxFoV°}][-z{N}]`, lens part = the lens's max FoV in
degrees since dots are illegal in hostnames). Order matters so no
heartbeat falls into a gap:

1. Copy the `knh-dam-agents` record to the new `device_id` (assignment,
   control, `last_video`, `access` intact).
2. Repoint the token row's `device_id` in `knh-dam-devices` (same hash —
   no re-issue).
3. On the device: `hostnamectl set-hostname`, patch `/etc/hosts`, set
   `DEVICE_ID` in `.env.dev`, restart `dam-agent` + avahi.
4. Verify an upload under the new id, then delete the old agents record.
   S3 event delivery is at-least-once and late — delete again after a
   few minutes if the old record resurrects.
5. Update `docs/reference/camera-info.md` / the camera+lens lists.

**D. Lens-only change**: refocus against the live viewer, rename per C
if the FoV part of the hostname changes, and update `hardware.lens_type`
on the device page.

**E. Verify the day after** any optics change: check the daily
`videos/{loc}/{LOC}-{date}.log` (exposure/lux/lumas per frame) and the
video file size — oversized videos usually mean noise (wrong sensor
mode or too-aggressive night gain).

## Appendix — bench fleet `.env` values by camera and lens

Ground truth collected from the devices 2026-08-20. Every device also
carries the required keys (`DEVICE_ID`, `TIMEZONE=Asia/Seoul`,
`UPLOAD_SIGNER_URL`, `DEVICE_TOKEN`) and the standard thermal block
(75/80/75/85, shutdown disabled); the table lists only the
camera/lens-specific values. Keys not listed = defaults.

| Device (location) | Camera / lens | Camera-specific `.env` values | Why |
| ----------------- | ------------- | ----------------------------- | --- |
| `dam-imx477-120` (JAYANG2) | HQ IMX477, 120° fisheye CS | *(defaults; night/max exposure explicitly 0)* | City scenery: legacy stock-AE policy — the ~66 ms AE night look is intentional |
| `dam-imx477-45-2` (JAYANG3) | HQ IMX477, 8 mm CS (45°) | *(defaults)* | same |
| `dam-imx477-45-1` (JAYANG4) | HQ IMX477, 8 mm M12 (45°) | *(defaults)* | same |
| `dam-imx219-62-z1` (JAYANG5) | Arducam IMX219 NoIR clone, stock 62° | `TUNING_FILE=imx219_noir.json`<br>`RAW_SIZE=1640,1232` | NoIR tuning fixes pink whites; binned full-FoV mode restores the true 62° (auto-pick cropped to ~37°) and halves night noise (video was 2–3× oversized). Also needs `dtoverlay=imx219` in config.txt |
| `dam-imx477-113-z2` (JAYANG6) | HQ IMX477 M12, 2.8 mm wide IR lens (113°) | *(defaults)* | same stock-AE policy |
| `dam-imx477-80-z3` (JAYANG7) | HQ IMX477 M12, 4 mm starlight lens (80°) | *(defaults)* | same |
| `dam-ov5647-56` (JAYANG8, ex `dam-ov5647ir-75`) | Arducam OV5647 M12 board (B0031), stock 4 mm (56°) — replaced the IR-CUT cam 2026-08-22 | `RAW_SIZE=1296,972` | binned full-FoV mode (auto-pick crops FoV on every OV5647). Stock AE, no night env. Capture window 05:00–20:00 set in the frontend |
| `dam-imx477-45-3` (JAYANG9) | HQ IMX477, 8 mm (45°) | *(defaults)* | same stock-AE policy |
| `dam-imx462-92-1` (JAYANGN) | Arducam Pivariety IMX462, F/0.95 92° | `MAX_EXPOSURE_MS=5000`<br>`NIGHT_EXPOSURE_MS=250`<br>`NIGHT_GAIN=2` | manual night mode (AE is capped ~66 ms); 250/2 won the 2026-08-17 A/B on stability and quality for indoor-lit scenes; on a true dark site use 1000/4. Needs the Pivariety recipe (§5B) |
| `dam-imx462-92-2` (JAYANGN2) | Arducam Pivariety IMX462, F/0.95 92° | `MAX_EXPOSURE_MS=5000`<br>`NIGHT_EXPOSURE_MS=250`<br>`NIGHT_GAIN=2` | same |

Patterns worth remembering:

- **IMX477 units need nothing** — the HQ camera is the zero-config
  baseline regardless of lens.
- **Every non-IMX477 sensor so far needed `RAW_SIZE`** to escape a
  FoV-cropping auto-picked mode — check `ScalerCrop` in a sidecar after
  attaching any new sensor.
- **Night mode is an IMX462-only env block**; other sensors either keep
  the legacy AE look (IMX477 policy) or handle night in hardware
  (IR-CUT).
