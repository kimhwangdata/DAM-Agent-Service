"""Agent code constants (CLAUDE.md code style).

Pure code constants for the agent service. Environment-driven settings and
their defaults stay in ``agent.config`` (the env-file config module).
"""

# ── uploader (design 01 §5) ──────────────────────────────────────────────────
BACKOFF_INITIAL_S = 1.0
BACKOFF_CAP_S = 60.0
SIGN_TIMEOUT_S = 30
PUT_TIMEOUT_S = 60
HEARTBEAT_MIN_INTERVAL_S = 30.0  # rest-state heartbeat floor
# Camera metadata worth logging per frame (scalars/short tuples only — the
# full picamera2 metadata also carries matrices and histograms).
SIDECAR_META_KEYS = (
    "ExposureTime", "AnalogueGain", "DigitalGain", "Lux",
    "ColourTemperature", "ColourGains", "ScalerCrop",
    "SensorTemperature", "FocusFoM",
)

# ── capture loop ─────────────────────────────────────────────────────────────
MIN_INTERVAL_S = 2  # capture+upload needs ~1.5 s on a Pi 3

# ── camera ───────────────────────────────────────────────────────────────────
# Shortest frame duration we ever ask for (30 fps) when extending the AE
# exposure ceiling via MAX_EXPOSURE_MS.
FRAME_DURATION_MIN_US = 33_333
# The Arducam Pivariety bridge MCU reports its own name instead of the
# sensor behind it; every Pivariety module in this fleet is an IMX462
# (UC-955), so report the real sensor.
CAMERA_MODEL_ALIASES = {"arducam-pivariety": "imx462"}

# ── viewer (design 01 §6) ────────────────────────────────────────────────────
MJPEG_BOUNDARY = "damframe"
STREAM_WAIT_S = 1.0  # condition-wait slice so handler threads notice shutdown

# ── main ─────────────────────────────────────────────────────────────────────
UPLOADER_DRAIN_S = 10.0
