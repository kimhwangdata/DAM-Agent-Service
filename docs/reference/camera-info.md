# Camera Reference

Catalog of camera modules used by the rpi-camera-agent fleet. One section per
camera module, all in the same format (Module / Sensor modes / Software stack /
Known quirks). Add new cameras as new sections; keep a copy of the relevant
section as `~/camera-info.md` on each device.

**Hostname convention**: `dam-{sensor}[-{lens}][-{n}]` — e.g. `dam-imx477-1`,
`dam-imx462`; append a lens type suffix once the lens is identified
(e.g. `dam-imx462-m16`) and a number when several units share the same
sensor (IMX477 is the fleet's standard camera).

---

## Raspberry Pi HQ Camera (IMX477) — on dam-imx477-1

Recorded: 2026-08-13, from `rpicam-still --list-cameras` on the device.

### Module

- **Sensor**: Sony IMX477 (12.3 MP, 12-bit RGGB Bayer)
- **Product**: Raspberry Pi High Quality (HQ) Camera
- **Detection**: auto-detected (`camera_auto_detect=1`, no dtoverlay needed)
- **Device path**: `/base/soc/i2c0mux/i2c@1/imx477@1a` (camera index 0)
- **Max resolution**: 4056 x 3040
- **Camera firmware**: n/a (native sensor, no MCU)

### Sensor modes

| Format         | Resolution  | Max fps | Crop                |
| -------------- | ----------- | ------- | ------------------- |
| SRGGB10_CSI2P  | 1332 x 990  | 120.50  | (696,528)/2664x1980 |
| SRGGB10_CSI2P  | 2028 x 1080 | 74.74   | (0,440)/4056x2160   |
| SRGGB10_CSI2P  | 2028 x 1520 | 53.77   | (0,0)/4056x3040     |
| SRGGB10_CSI2P  | 4056 x 2160 | 19.58   | (0,440)/4056x2160   |
| SRGGB10_CSI2P  | 4056 x 3040 | 14.00   | (0,0)/4056x3040     |
| SRGGB12_CSI2P  | 1332 x 990  | 101.68  | (696,528)/2664x1980 |
| SRGGB12_CSI2P  | 2028 x 1080 | 62.81   | (0,440)/4056x2160   |
| SRGGB12_CSI2P  | 2028 x 1520 | 45.19   | (0,0)/4056x3040     |
| SRGGB12_CSI2P  | 4056 x 2160 | 16.39   | (0,440)/4056x2160   |
| SRGGB12_CSI2P  | 4056 x 3040 | 11.72   | (0,0)/4056x3040     |
| SRGGB8         | 1332 x 990  | 147.91  | (696,528)/2664x1980 |
| SRGGB8         | 2028 x 1080 | 92.27   | (0,440)/4056x2160   |
| SRGGB8         | 2028 x 1520 | 66.38   | (0,0)/4056x3040     |
| SRGGB8         | 4056 x 2160 | 24.32   | (0,440)/4056x2160   |
| SRGGB8         | 4056 x 3040 | 17.39   | (0,0)/4056x3040     |

### Software stack

- OS: Raspberry Pi OS Lite, Debian 13 (Trixie), arm64, Python 3.13.5
- libcamera / rpicam-apps: stock Raspberry Pi OS packages, working
- python3-picamera2: **not installed yet** — install before agent capture work
  (`sudo apt install python3-picamera2`), or shell out to rpicam-still

### Known quirks / notes

- None known — standard official camera on the stock camera stack;
  safe to apt-upgrade

---

## Arducam Pivariety IMX462 (UC-955) — on dam-imx462

Recorded: 2026-08-13, from `rpicam-still --list-cameras` after the Arducam
firmware update.

### Module

- **Sensor**: Sony IMX462 STARVIS (2 MP, 10-bit RGGB Bayer, ultra low light)
- **Product**: Arducam Pivariety IMX462 (board UC-955, SKU B0333/B0444 family)
- **Detection**: NOT auto-detected — requires `dtoverlay=arducam-pivariety`
  in `/boot/firmware/config.txt` (Pivariety bridge MCU at I2C 0x0c)
- **Device path**: `/base/soc/i2c0mux/i2c@1/arducam_pivariety@c` (camera index 0)
- **Max resolution**: 1920 x 1080
- **Camera firmware**: MCU firmware updated 2026-08-13 with the Arducam B0444
  tool (previous 0x10003 reported broken mode descriptors on modern kernels)

### Sensor modes

| Format         | Resolution  | Max fps | Crop            |
| -------------- | ----------- | ------- | --------------- |
| SRGGB10_CSI2P  | 1920 x 1080 | 60.00   | (0,0)/1920x1080 |

### Software stack

- OS: Raspberry Pi OS Lite, Debian 12 (Bookworm), arm64, Python 3.11.2
  — Bookworm chosen because Arducam Pivariety needs their libcamera build
- libcamera: Arducam build 0.5.2 (`install_pivariety_pkgs.sh -p libcamera_dev`
  and `-p libcamera_apps`)
- Tuning file: `/usr/share/libcamera/ipa/rpi/vc4/arducam-pivariety.json`
  (manual copy of imx462.json — Arducam package does not ship that name)
- python3-picamera2 0.3.31: installed and verified (1920x1080 still capture OK)
- libtinfo5 (bullseye deb): only needed by the firmware_update tool

### Known quirks / notes

- Harmless "[read32] Failed to set I2C address" lines at camera init
- Max exposure ~15.5 s (sensor limit) — fine for daytime time-lapse
- An apt upgrade that replaces libcamera can break the camera; if
  "invalid configuration" / garbage modes (2147485568 width) return,
  re-run the Arducam installer and, if needed, the B0444 firmware tool
