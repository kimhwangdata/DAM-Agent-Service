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

_REQUIRED_KEYS = ("LOCATION_ID", "DEVICE_ID", "TIMEZONE")


class ConfigError(Exception):
    """Raised when the stage env file is missing or incomplete."""


@dataclass(frozen=True)
class Settings:
    stage: str
    location_id: str
    device_id: str
    timezone: str
    s3_bucket: str = DEFAULT_S3_BUCKET
    s3_image_prefix: str = DEFAULT_S3_IMAGE_PREFIX
    video_minutes: int = DEFAULT_VIDEO_MINUTES
    capture_size: tuple[int, int] = DEFAULT_CAPTURE_SIZE
    queue_max: int = DEFAULT_QUEUE_MAX
    viewer_port: int = DEFAULT_VIEWER_PORT


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

    return Settings(
        stage=stage,
        location_id=values["LOCATION_ID"],
        device_id=values["DEVICE_ID"],
        timezone=values["TIMEZONE"],
        s3_bucket=values.get("S3_BUCKET", DEFAULT_S3_BUCKET),
        s3_image_prefix=values.get("S3_IMAGE_PREFIX", DEFAULT_S3_IMAGE_PREFIX),
        video_minutes=int(values.get("VIDEO_MINUTES", DEFAULT_VIDEO_MINUTES)),
        capture_size=(
            _parse_capture_size(values["CAPTURE_SIZE"])
            if "CAPTURE_SIZE" in values
            else DEFAULT_CAPTURE_SIZE
        ),
        queue_max=int(values.get("QUEUE_MAX", DEFAULT_QUEUE_MAX)),
        viewer_port=int(values.get("VIEWER_PORT", DEFAULT_VIEWER_PORT)),
    )
