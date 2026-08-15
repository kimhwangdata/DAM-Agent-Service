"""Camera sources — capture behind a small interface (design 01-agent.md §2).

``Picamera2Camera`` runs on the Pi (picamera2, ADR-0001); ``FakeCamera``
runs anywhere and draws the timestamp into a generated image. Both return
in-memory JPEG bytes — nothing is written to disk (no-local-save design).
"""

from __future__ import annotations

import io
import logging
import time
from datetime import datetime, tzinfo
from typing import Any, Protocol

from agent.config import NIGHT_LUX_OFF, NIGHT_LUX_ON

log = logging.getLogger(__name__)


class CameraError(Exception):
    """Raised when the camera is unavailable or used before start()."""


class CameraSource(Protocol):
    def start(self) -> None: ...

    def capture_jpeg(self) -> tuple[bytes, datetime, dict[str, Any]]: ...

    def stop(self) -> None: ...


class FakeCamera:
    """Test/dev camera: a generated image with the capture time drawn in."""

    model = "fake"

    def __init__(self, tz: tzinfo, size: tuple[int, int] = (1280, 720)) -> None:
        self._tz = tz
        self._size = size
        self._started = False

    def start(self) -> None:
        self._started = True

    def capture_jpeg(self) -> tuple[bytes, datetime, dict[str, Any]]:
        if not self._started:
            raise CameraError("FakeCamera used before start()")
        from PIL import Image, ImageDraw  # dev-only dependency

        captured_at = datetime.now(self._tz)
        image = Image.new("RGB", self._size, color=(24, 48, 96))
        draw = ImageDraw.Draw(image)
        draw.text((20, 20), captured_at.isoformat(), fill=(255, 255, 255))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG")
        return buffer.getvalue(), captured_at, {"source": "fake"}

    def stop(self) -> None:
        self._started = False


# Shortest frame duration we ever ask for (30 fps) when extending the AE
# exposure ceiling via MAX_EXPOSURE_MS.
_FRAME_DURATION_MIN_US = 33_333


def night_decision(
    lux: float | None,
    is_night: bool,
    lux_on: float = NIGHT_LUX_ON,
    lux_off: float = NIGHT_LUX_OFF,
) -> bool:
    """Should the camera be in manual night mode? Hysteresis between the
    two thresholds; unknown lux keeps the current mode."""
    if lux is None:
        return is_night
    if is_night:
        return lux < lux_off
    return lux < lux_on


class Picamera2Camera:
    """Real camera via picamera2 — preview configuration, BGR888 (legacy)."""

    model: str | None = None

    def __init__(
        self,
        tz: tzinfo,
        size: tuple[int, int] = (1280, 720),
        max_exposure_ms: int = 0,
        tuning_file: str | None = None,
        night_exposure_ms: int = 0,
        night_gain: float = 8.0,
        raw_size: tuple[int, int] | None = None,
    ) -> None:
        self._tz = tz
        self._size = size
        self._max_exposure_ms = max_exposure_ms
        self._tuning_file = tuning_file
        self._night_exposure_ms = night_exposure_ms
        self._night_gain = night_gain
        self._raw_size = raw_size
        self.is_night = False
        self._cam: Any = None

    def start(self) -> None:
        try:
            from picamera2 import Picamera2  # only importable on the Pi
        except ImportError as exc:
            raise CameraError("picamera2 is not available on this system") from exc
        tuning = None
        if self._tuning_file:
            # e.g. "imx219_noir.json" — corrects AWB for filterless NoIR
            # modules (whites render pink under the default tuning).
            tuning = Picamera2.load_tuning_file(self._tuning_file)
        cam = Picamera2(tuning=tuning)
        controls: dict[str, Any] = {}
        if self._max_exposure_ms > 0:
            # Let AE extend exposure up to the configured ceiling at night
            # (stock preview config caps frame duration at ~66 ms, which
            # blinds low-light sensors like the IMX462 — see
            # docs/reference/rpi-camera-list.md).
            controls["FrameDurationLimits"] = (
                _FRAME_DURATION_MIN_US,
                self._max_exposure_ms * 1000,
            )
        kwargs: dict[str, Any] = {}
        if self._raw_size is not None:
            # Pin the sensor mode — some sensors' auto-picked video modes
            # crop the FoV (OV5647 1080p uses 74% of the sensor width).
            kwargs["raw"] = {"size": self._raw_size}
        config = cam.create_preview_configuration(
            main={"format": "BGR888", "size": self._size},
            controls=controls,
            **kwargs,
        )
        cam.configure(config)
        cam.start()
        time.sleep(1)  # let AE/AWB settle after start, as the legacy code did
        self._cam = cam
        self.model = cam.camera_properties.get("Model")

    def capture_jpeg(self) -> tuple[bytes, datetime, dict[str, Any]]:
        if self._cam is None:
            raise CameraError("Picamera2Camera used before start()")
        captured_at = datetime.now(self._tz)
        buffer = io.BytesIO()
        request = self._cam.capture_request()
        try:
            request.save("main", buffer, format="jpeg")
            metadata = request.get_metadata()
        finally:
            request.release()
        if self._night_exposure_ms > 0:
            self._update_night_mode(metadata.get("Lux"))
        return buffer.getvalue(), captured_at, metadata

    def _update_night_mode(self, lux: float | None) -> None:
        """Legacy camera_viewer.py AEC pattern: below the lux threshold,
        disable AE and set manual ExposureTime/AnalogueGain (the tuning
        file caps AE shutter at ~66 ms, far too short for night); above
        it, hand control back to AE. Applies to the NEXT capture."""
        want_night = night_decision(lux, self.is_night)
        if want_night == self.is_night:
            return
        if want_night:
            self._cam.set_controls({
                "AeEnable": False,
                "ExposureTime": self._night_exposure_ms * 1000,
                "AnalogueGain": self._night_gain,
            })
            log.info(
                "night mode ON lux=%.1f exposure_ms=%d gain=%.1f",
                -1.0 if lux is None else lux,
                self._night_exposure_ms, self._night_gain,
            )
        else:
            self._cam.set_controls({"AeEnable": True})
            log.info("night mode OFF lux=%.1f", -1.0 if lux is None else lux)
        self.is_night = want_night

    def stop(self) -> None:
        if self._cam is not None:
            self._cam.stop()
            self._cam.close()
            self._cam = None
