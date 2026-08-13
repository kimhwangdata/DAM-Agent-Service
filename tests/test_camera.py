"""Tests for agent.camera — FakeCamera and the import-guarded real camera."""

import io
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from agent.camera import CameraError, FakeCamera, Picamera2Camera

TZ = ZoneInfo("Asia/Seoul")


def test_fake_camera_returns_decodable_jpeg_and_aware_timestamp():
    cam = FakeCamera(tz=TZ, size=(320, 240))
    cam.start()
    jpeg, captured_at, metadata = cam.capture_jpeg()
    cam.stop()

    image = Image.open(io.BytesIO(jpeg))
    image.verify()
    assert image.format == "JPEG"
    assert image.size == (320, 240)
    assert captured_at.tzinfo is not None
    assert captured_at.utcoffset() == TZ.utcoffset(captured_at)
    assert metadata == {"source": "fake"}


def test_fake_camera_before_start_raises():
    with pytest.raises(CameraError, match="start"):
        FakeCamera(tz=TZ).capture_jpeg()


def test_picamera2_module_loads_without_picamera2_installed():
    cam = Picamera2Camera(tz=TZ)
    with pytest.raises(CameraError, match="picamera2|start"):
        cam.start()  # not installed on Windows -> clean CameraError
    with pytest.raises(CameraError, match="start"):
        cam.capture_jpeg()
