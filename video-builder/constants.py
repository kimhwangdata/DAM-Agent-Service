"""video-builder constants (CLAUDE.md code style).

Service-specific values; cross-service ones come from shared/constants.py
(packaged into the Lambda zip alongside this file).
"""

from shared.constants import (
    AGENTS_TABLE_DEFAULT,
    IMAGE_PREFIX_DEFAULT,
    JPEG_EOI,
    JPEG_SOI,
    S3_BUCKET_DEFAULT,
    VIDEO_PREFIX_DEFAULT,
)

__all__ = [
    "AGENTS_TABLE_DEFAULT", "BUILDER_VERSION", "DEFAULT_TIMEZONE_FALLBACK",
    "DOWNLOAD_THREADS", "FFMPEG_PATH_DEFAULT", "FFMPEG_TIMEOUT_S",
    "IMAGE_PREFIX_DEFAULT", "JPEG_EOI", "JPEG_SOI", "MIN_BYTES_DEFAULT",
    "S3_BUCKET_DEFAULT", "VIDEO_PREFIX_DEFAULT",
]

DEFAULT_TIMEZONE_FALLBACK = "Asia/Seoul"
MIN_BYTES_DEFAULT = 10_000  # < 10 KB uploads never enter the video (§5.1)
FFMPEG_PATH_DEFAULT = "/opt/bin/ffmpeg"  # dam-ffmpeg layer (ADR-0005)
FFMPEG_TIMEOUT_S = 780
DOWNLOAD_THREADS = 16
BUILDER_VERSION = "1.0"
