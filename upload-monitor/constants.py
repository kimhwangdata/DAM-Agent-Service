"""upload-monitor constants (CLAUDE.md code style).

Service-specific values; cross-service ones come from shared/constants.py
(packaged into the Lambda zip alongside this file).
"""

from shared.constants import (
    AGENTS_TABLE_DEFAULT,
    IMAGE_PREFIX_DEFAULT,
    JPEG_EOI,
    JPEG_SOI,
)

__all__ = [
    "AGENTS_TABLE_DEFAULT", "CACHE_TTL_S", "DAMAGED_WINDOW_HOURS",
    "IMAGE_PREFIX_DEFAULT", "JPEG_EOI", "JPEG_SOI",
    "MAX_BYTES_DEFAULT", "MIN_BYTES_DEFAULT",
]

MIN_BYTES_DEFAULT = 10_000       # 10 KB
MAX_BYTES_DEFAULT = 5_242_880    # 5 MB
DAMAGED_WINDOW_HOURS = 24
CACHE_TTL_S = 300  # location -> device resolution cache
