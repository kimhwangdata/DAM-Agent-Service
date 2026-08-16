"""upload-signer constants (CLAUDE.md code style).

Service-specific values; cross-service ones come from shared/constants.py
(packaged into the Lambda zip alongside this file).
"""

import re

from shared.constants import (
    AGENTS_TABLE_DEFAULT,
    CONTENT_TYPE_JPEG,
    DEVICES_TABLE_DEFAULT,
    IMAGE_PREFIX_DEFAULT,
    S3_BUCKET_DEFAULT,
)

BUCKET_DEFAULT = S3_BUCKET_DEFAULT
TOKEN_TABLE_DEFAULT = DEVICES_TABLE_DEFAULT
AGENTS_TABLE_DEFAULT = AGENTS_TABLE_DEFAULT
IMAGE_PREFIX = IMAGE_PREFIX_DEFAULT
URL_TTL_SECONDS_DEFAULT = 60

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FILENAME_RE = re.compile(r"^\d{9}\.jpg$")
ALLOWED_CONTENT_TYPE = CONTENT_TYPE_JPEG
ALLOWED_METADATA_KEYS = {"ulid", "device-id", "captured-utc", "timezone"}

# status fields accepted into `reported` (02-agent-manager.md §4)
REPORTED_KEYS = {
    "local_ip", "hostname", "agent_version",
    "seq", "uploaded", "dropped", "skipped", "failed_attempts",
    "queue_depth", "interval_s", "capture_size", "timezone", "uptime_s",
    "pi_model", "camera", "temp_c", "throttled", "thermal_state", "event",
    "stage", "capturing", "volt_core", "night_mode",
}
