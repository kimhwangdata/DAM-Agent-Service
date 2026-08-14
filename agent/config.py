"""Agent configuration — the single place for settings and magic values.

Loads ``.env.{STAGE}`` (there is no plain ``.env``) and exposes typed
settings. Every other module takes values from here; no literals elsewhere.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

DEFAULT_S3_BUCKET = "knh-dam-store"
DEFAULT_S3_IMAGE_PREFIX = "images/"
DEFAULT_VIDEO_MINUTES = 1
DEFAULT_CAPTURE_SIZE = (1280, 720)
DEFAULT_QUEUE_MAX = 64
DEFAULT_VIEWER_PORT = 8080
# Night/long exposure: AE may extend exposure up to this many ms when dark.
# 0 keeps the stock picamera2 ceiling (~66 ms). Only useful on low-light
# sensors (IMX462); keep well under the capture interval (48 s).
DEFAULT_MAX_EXPOSURE_MS = 0
# Optional libcamera tuning file name (e.g. "imx219_noir.json" for
# filterless NoIR modules). Empty = picamera2's automatic choice.
DEFAULT_TUNING_FILE = ""
# Manual night mode (legacy camera_viewer.py AEC pattern): AE cannot exceed
# the tuning file's ~66 ms shutter ceiling (measured 2026-08-14), so when
# the scene lux drops below NIGHT_LUX_ON the agent switches to manual
# ExposureTime/AnalogueGain, and back to AE above NIGHT_LUX_OFF
# (hysteresis). 0 disables. Sweet spots measured: IMX462+F/0.95 ~1000 ms
# gain 4; IMX477 ~5000 ms gain 10.
DEFAULT_NIGHT_EXPOSURE_MS = 0
DEFAULT_NIGHT_GAIN = 8.0
NIGHT_LUX_ON = 10.0
NIGHT_LUX_OFF = 30.0

# Capture cadence (design 01-agent.md §3 — legacy capture-24h.py formula).
FPS = 30  # matches the video builder's -framerate 30
FRAME_PER_MINUTE = 60 * FPS
CAPTURE_DURATION_SECONDS = 24 * 60 * 60

# Thermal protection (design 02-agent-manager.md §5.2). Bench reality:
# a Pi 3 in an enclosure idles ~73 C, so warn/pause/resume sit 5 C above
# the first draft; pause equals the firmware's own soft-throttle point.
DEFAULT_TEMP_WARN_C = 75.0
DEFAULT_TEMP_PAUSE_C = 80.0
DEFAULT_TEMP_RESUME_C = 75.0
DEFAULT_TEMP_SHUTDOWN_C = 85.0
DEFAULT_TEMP_SHUTDOWN_ENABLED = False  # remote devices must not strand themselves
TEMP_SHUTDOWN_CONSECUTIVE = 3

# LOCATION_ID is optional since phase 2: the manager assigns locations
# (02-agent-manager.md §6); the signer builds authoritative keys.
_REQUIRED_KEYS = (
    "DEVICE_ID",
    "TIMEZONE",
    "UPLOAD_SIGNER_URL",
    "DEVICE_TOKEN",
)


class ConfigError(Exception):
    """Raised when the stage env file is missing or incomplete."""


@dataclass(frozen=True)
class Settings:
    stage: str
    device_id: str
    timezone: str
    upload_signer_url: str
    device_token: str
    location_id: str | None = None  # display-only; assignment is authoritative
    s3_bucket: str = DEFAULT_S3_BUCKET
    s3_image_prefix: str = DEFAULT_S3_IMAGE_PREFIX
    video_minutes: int = DEFAULT_VIDEO_MINUTES
    capture_size: tuple[int, int] = DEFAULT_CAPTURE_SIZE
    queue_max: int = DEFAULT_QUEUE_MAX
    viewer_port: int = DEFAULT_VIEWER_PORT
    temp_warn_c: float = DEFAULT_TEMP_WARN_C
    temp_pause_c: float = DEFAULT_TEMP_PAUSE_C
    temp_resume_c: float = DEFAULT_TEMP_RESUME_C
    temp_shutdown_c: float = DEFAULT_TEMP_SHUTDOWN_C
    temp_shutdown_enabled: bool = DEFAULT_TEMP_SHUTDOWN_ENABLED
    max_exposure_ms: int = DEFAULT_MAX_EXPOSURE_MS
    tuning_file: str | None = None
    night_exposure_ms: int = DEFAULT_NIGHT_EXPOSURE_MS
    night_gain: float = DEFAULT_NIGHT_GAIN

    @property
    def interval_s(self) -> int:
        """Seconds between captures — one day becomes video_minutes of video."""
        return CAPTURE_DURATION_SECONDS // (FRAME_PER_MINUTE * self.video_minutes)


def _find_env_file(stage: str) -> Path:
    name = f".env.{stage}"
    candidates = [Path.cwd() / name, Path(__file__).resolve().parent.parent / name]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    searched = ", ".join(str(c) for c in candidates)
    raise ConfigError(f"stage env file {name!r} not found (searched: {searched})")


def _parse_capture_size(raw: str) -> tuple[int, int]:
    try:
        width, height = (int(part) for part in raw.split(","))
    except ValueError as exc:
        raise ConfigError(f"CAPTURE_SIZE must be 'W,H', got {raw!r}") from exc
    return (width, height)


def load_settings(stage: str | None = None, env_file: Path | None = None) -> Settings:
    """Load settings for ``stage`` (defaults to the STAGE env var)."""
    stage = stage or os.environ.get("STAGE")
    if not stage:
        raise ConfigError("STAGE is not set and no stage was given")

    path = env_file if env_file is not None else _find_env_file(stage)
    if not Path(path).is_file():
        raise ConfigError(f"stage env file not found: {path}")
    values = {k: v for k, v in dotenv_values(path).items() if v is not None}

    missing = [key for key in _REQUIRED_KEYS if not values.get(key)]
    if missing:
        raise ConfigError(f"missing required keys in {path}: {', '.join(missing)}")

    video_minutes = int(values.get("VIDEO_MINUTES", DEFAULT_VIDEO_MINUTES))
    if video_minutes < 1:
        raise ConfigError(f"VIDEO_MINUTES must be >= 1, got {video_minutes}")

    max_exposure_ms = int(values.get("MAX_EXPOSURE_MS", DEFAULT_MAX_EXPOSURE_MS))
    if max_exposure_ms < 0:
        raise ConfigError(f"MAX_EXPOSURE_MS must be >= 0, got {max_exposure_ms}")

    night_exposure_ms = int(
        values.get("NIGHT_EXPOSURE_MS", DEFAULT_NIGHT_EXPOSURE_MS)
    )
    if night_exposure_ms < 0:
        raise ConfigError(
            f"NIGHT_EXPOSURE_MS must be >= 0, got {night_exposure_ms}"
        )

    return Settings(
        stage=stage,
        location_id=values.get("LOCATION_ID") or None,
        device_id=values["DEVICE_ID"],
        timezone=values["TIMEZONE"],
        upload_signer_url=values["UPLOAD_SIGNER_URL"],
        device_token=values["DEVICE_TOKEN"],
        s3_bucket=values.get("S3_BUCKET", DEFAULT_S3_BUCKET),
        s3_image_prefix=values.get("S3_IMAGE_PREFIX", DEFAULT_S3_IMAGE_PREFIX),
        video_minutes=video_minutes,
        capture_size=(
            _parse_capture_size(values["CAPTURE_SIZE"])
            if "CAPTURE_SIZE" in values
            else DEFAULT_CAPTURE_SIZE
        ),
        queue_max=int(values.get("QUEUE_MAX", DEFAULT_QUEUE_MAX)),
        viewer_port=int(values.get("VIEWER_PORT", DEFAULT_VIEWER_PORT)),
        temp_warn_c=float(values.get("TEMP_WARN_C", DEFAULT_TEMP_WARN_C)),
        temp_pause_c=float(values.get("TEMP_PAUSE_C", DEFAULT_TEMP_PAUSE_C)),
        temp_resume_c=float(values.get("TEMP_RESUME_C", DEFAULT_TEMP_RESUME_C)),
        temp_shutdown_c=float(
            values.get("TEMP_SHUTDOWN_C", DEFAULT_TEMP_SHUTDOWN_C)
        ),
        temp_shutdown_enabled=(
            str(values.get("TEMP_SHUTDOWN_ENABLED", DEFAULT_TEMP_SHUTDOWN_ENABLED))
            .lower() in ("1", "true", "yes")
        ),
        max_exposure_ms=max_exposure_ms,
        tuning_file=values.get("TUNING_FILE", DEFAULT_TUNING_FILE) or None,
        night_exposure_ms=night_exposure_ms,
        night_gain=float(values.get("NIGHT_GAIN", DEFAULT_NIGHT_GAIN)),
    )
