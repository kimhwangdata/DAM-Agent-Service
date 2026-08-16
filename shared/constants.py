"""Cross-service constants (CLAUDE.md code style).

Values shared by the agent and the Lambda services (upload-signer,
upload-monitor, video-builder). Each Lambda package and the device deploy
carries a copy of this folder; service-specific constants live in the
service's own ``constants.py``.
"""

# ── S3 layout (architecture §7) ──────────────────────────────────────────────
S3_BUCKET_DEFAULT = "knh-dam-store"
AGENTS_TABLE_DEFAULT = "knh-dam-agents"
DEVICES_TABLE_DEFAULT = "knh-dam-devices"
IMAGE_PREFIX_DEFAULT = "images/"
VIDEO_PREFIX_DEFAULT = "videos/"
JPG_SUFFIX = ".jpg"
JSON_SUFFIX = ".json"

# ── JPEG magic bytes (damage detection: monitor + builder) ───────────────────
JPEG_SOI = b"\xff\xd8"  # start of image
JPEG_EOI = b"\xff\xd9"  # end of image

# ── content types ────────────────────────────────────────────────────────────
CONTENT_TYPE_JPEG = "image/jpeg"
CONTENT_TYPE_JSON = "application/json"
CONTENT_TYPE_MP4 = "video/mp4"
CONTENT_TYPE_TEXT = "text/plain"

# ── video math (design 01 §3 / legacy capture-24h.py) ────────────────────────
FPS = 30  # matches the builder's -framerate/-r
FRAME_PER_MINUTE = 60 * FPS
CAPTURE_DURATION_SECONDS = 24 * 60 * 60
