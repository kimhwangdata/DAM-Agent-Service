"""Camera sources — capture behind a small interface (design 01-agent.md §2).

``Picamera2Camera`` runs on the Pi (picamera2, ADR-0001); ``FakeCamera``
runs anywhere and draws the timestamp into a generated image. Both return
in-memory JPEG bytes — nothing is written to disk (no-local-save design).
"""

from __future__ import annotations

import io
import time
from datetime import datetime, tzinfo
from typing import Any, Protocol


class CameraError(Exception):
    """Raised when the camera is unavailable or used before start()."""


class CameraSource(Protocol):
    def start(self) -> None: ...

    def capture_jpeg(self) -> tuple[bytes, datetime, dict[str, Any]]: ...

    def stop(self) -> None: ...


class FakeCamera:
    """Test/dev camera: a generated image with the capture time drawn in."""

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


class Picamera2Camera:
    """Real camera via picamera2 — preview configuration, BGR888 (legacy)."""

    def __init__(self, tz: tzinfo, size: tuple[int, int] = (1280, 720)) -> None:
        self._tz = tz
        self._size = size
        self._cam: Any = None

    def start(self) -> None:
        try:
            from picamera2 import Picamera2  # only importable on the Pi
        except ImportError as exc:
            raise CameraError("picamera2 is not available on this system") from exc
        cam = Picamera2()
        config = cam.create_preview_configuration(
            main={"format": "BGR888", "size": self._size}
        )
        cam.configure(config)
        cam.start()
        time.sleep(1)  # let AE/AWB settle after start, as the legacy code did
        self._cam = cam

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
        return buffer.getvalue(), captured_at, metadata

    def stop(self) -> None:
        if self._cam is not None:
            self._cam.stop()
            self._cam.close()
            self._cam = None
