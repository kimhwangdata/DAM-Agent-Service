# Raspberry Pi Camera Inventory

Catalog of the camera modules we own for the days-in-a-minute fleet, with
manufacturer specifications researched 2026-08-14 (sources at the bottom).
For the per-module state of cameras already deployed on a device (tuning
files, firmware fixes, quirks), see `camera-info.md` — this page is the
selection overview; that page is the operational detail.

## Inventory table

| # | Camera | Maker | Sensor (size, pixel) | Max still (W×H) | Video (typical modes) | Lens | Night vision | Notes |
|---|--------|-------|----------------------|-----------------|----------------------|------|--------------|-------|
| 1 | RPi HQ Camera, **CS mount** | Raspberry Pi | Sony IMX477R, 1/2.3" (7.9 mm diag), 1.55 µm, back-illuminated | **4056×3040** (12.3 MP) | 4056×3040@~10, 2028×1520@40–50, 2028×1080@50–75, 1332×990@120 | Interchangeable **CS-mount** (and C-mount via the included 5 mm adapter); adjustable back focus 12.5–22.4 mm | No IR illumination; IR-cut filter fixed (NoIR conversion voids warranty). Night measured 2026-08-14 (`dam-imx477-2`, outdoor): AE pinned at 66 ms / ISO 1580 despite `FrameDurationLimits` 5 s — same tuning-file shutter ceiling as row 3; long exposures need `AeExposureMode=Long` too | Our fleet standard (`dam-imx477-2/-3`). RAW12/10/8, COMP8 |
| 2 | RPi HQ Camera, **M12 mount** | Raspberry Pi | Sony IMX477R — same as #1 | **4056×3040** (12.3 MP) | same as #1 | Interchangeable **M12-mount**; adjustable back focus 2.6–11.8 mm | same as #1 | Same sensor/board as #1, shorter back focus for compact M12 lenses (`dam-imx477-8mm` uses an 8 mm M12) |
| 3 | Arducam Pivariety ultra-low-light (**UC-955**) | Arducam | Sony IMX462 STARVIS, 1/2.8", 2.9 µm | **1920×1080** (2 MP) | 1080p up to 60 fps | Pre-fitted wide-aperture **F/0.95** lens (M12/M16 barrel per variant), ~92° HFoV | **Yes (hardware)** — starlight-class color at 0.01 lux with the F/0.95 lens, but ONLY with long exposure + high gain. Measured 2026-08-14: AE stays pinned at **66 ms even with `FrameDurationLimits` extended to 5 s** (EXIF 66.65 ms / ISO 788 at night) — the AE shutter ceiling comes from the tuning file's exposure-mode shutter list, not the frame duration, so `AeExposureMode=Long` (or manual night exposure) is additionally required — not yet implemented | Needs Arducam Pivariety driver + tuning file (see `camera-info.md` / memory notes). Deployed as `dam-imx462`. Not supported on Pi 2B/Zero 1 |
| 4 | Arducam 8 MP "V2" (**UC-958**, board rev 2.3) | Arducam | Sony IMX219, 1/4", 1.12 µm | **3280×2464** (8 MP) | 1080p30, 1640×1232@~40, 720p60 | Fixed-focus stock lens (V2-class, ~62° DFoV; M12/CS variants exist) | No | Compatible with the official Camera Module 2, BUT firmware `camera_auto_detect` does not recognize this clone — force `camera_auto_detect=0` + `dtoverlay=imx219` (verified on `dam-imx219-z1`, Pi Zero 2 W, 2026-08-14) |
| 5 | RPi Camera Module, board **rev 1.3** (V1) | Raspberry Pi | OmniVision OV5647, 1/4" | **2592×1944** (5 MP) | 1080p30, 720p60, 640×480p60/90 | Fixed-focus ~3.6 mm, f/2.9, ~54°×41° FoV | No | The original 2013 module; still fully supported by libcamera (`ov5647`) |
| 6 | Arducam standard 5 MP board (**UC-261**) | Arducam | OmniVision OV5647, 1/4" | **2592×1944** (5 MP) | 1080p30, 720p60, 640×480p60/90 | Fixed-focus stock lens, ~72° DFoV (rev C/D improved optics; M12-lens variants of the board exist) | No | UC-261 is the PCB code on Arducam's classic V1-compatible OV5647 board (B0033 family); works with the stock `ov5647` driver. Confirm rev with `rpicam-still --list-cameras` when connected |
| 7 | No-name IR night-vision camera (photo-identified) | generic (OV5647 IR-CUT class) | OmniVision OV5647, 1/4" | **2592×1944** (5 MP; "1080p" is its video spec) | 1080p30, 720p60 | **3.6 mm** M12, f/1.8, ~75° FoV, manually focusable | **Yes** — motorized **IR-CUT** filter (3-pin connector), onboard **LDR** light sensor for auto day/night switching, screw tabs for two 850 nm IR LED boards (~8 m range) | Board layout (LDR bottom-left, IR-CUT plug right, side LED mounts) matches the common RPi IR-CUT 5 MP night camera sold by Waveshare/MakerFocus and others |

## Fleet fit notes

- **Time-lapse suitability**: the IMX477 boards (#1/#2) give the best
  stills and lens flexibility — our standard. The IMX462 (#3) trades
  resolution for genuine night capability, useful for locations where the
  scenery matters after dark. #4/#5 are serviceable spares at lower
  resolution. #7 is interesting for 24 h locations: the IR-CUT + LDR
  gives usable frames at night if IR LED boards are fitted, at 5 MP.
- **Driver support**: #1/#2 (`imx477`), #4 (`imx219`), #5/#7 (`ov5647`)
  all work with stock Raspberry Pi OS libcamera. Only #3 needs the
  Arducam Pivariety stack (pinned — see `camera-info.md`).
- The agent's `CAPTURE_SIZE` (default 1280×720) is well inside every
  module's capability, so any of these can back a location; sensor choice
  is about optics and light, not pipeline compatibility.

## Sources

- [Raspberry Pi HQ Camera product page](https://www.raspberrypi.com/products/raspberry-pi-high-quality-camera/)
  and [product brief (CS + M12, back-focus figures)](https://datasheets.raspberrypi.com/hq-camera/hq-camera-product-brief.pdf)
- [Arducam IMX477 wiki](https://docs.arducam.com/Raspberry-Pi-Camera/Native-camera/12MP-IMX477/)
- [Arducam Pivariety IMX462 product page (UC-955 / B0333)](https://www.arducam.com/arducam-for-raspberry-pi-ultra-low-light-camera-1080p-hd-wide-angle-pivariety-camera-module-based-on-1-2-7inch-2mp-starvis-sensor-imx462-compatible-with-raspberry-pi-isp-and-gstreamer-plugin.html)
  and [Arducam IMX462 wiki](https://docs.arducam.com/Raspberry-Pi-Camera/Pivariety-Camera/IMX462/)
- [Arducam 8 MP IMX219 wiki](https://docs.arducam.com/Raspberry-Pi-Camera/Native-camera/8MP-IMX219/)
- [Arducam 5 MP OV5647 wiki](https://docs.arducam.com/Raspberry-Pi-Camera/Native-camera/5MP-OV5647/),
  [Arducam standard OV5647 board (B0033 / UC-261)](https://www.arducam.com/arducam-ov5647-standard-raspberry-pi-camera-b0033.html),
  and [Arducam Rev.C OV5647 optics note](https://blog.arducam.com/raspberry-pi-camera-rev-c-improves-optical-performance/)
- [Waveshare RPi IR-CUT camera (OV5647, 3.6 mm, LDR + IR boards)](https://www.waveshare.com/rpi-ir-cut-camera.htm)
  and [The Pi Hut IR-CUT 5 MP night-vision camera](https://thepihut.com/products/raspberry-pi-night-vision-camera-ir-cut)
