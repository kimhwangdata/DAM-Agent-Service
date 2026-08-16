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


class TestNightDecision:
    def test_dark_scene_turns_night_on(self):
        from agent.camera import night_decision
        assert night_decision(2.0, False) is True

    def test_bright_scene_stays_day(self):
        from agent.camera import night_decision
        assert night_decision(500.0, False) is False

    def test_hysteresis_band_keeps_current_mode(self):
        from agent.camera import night_decision
        # 20 lux: above ON (10) so day stays day; below OFF (30) so night stays night
        assert night_decision(20.0, False) is False
        assert night_decision(20.0, True) is True

    def test_bright_morning_turns_night_off(self):
        from agent.camera import night_decision
        assert night_decision(100.0, True) is False

    def test_unknown_lux_keeps_mode(self):
        from agent.camera import night_decision
        assert night_decision(None, True) is True
        assert night_decision(None, False) is False


class TestNightBlownFrameEscape:
    def test_blown_night_frame_exits_regardless_of_lux(self):
        from agent.camera import night_decision
        # lux frozen low by saturation - the old logic would stay night
        assert night_decision(0.5, True, luma=255.0) is False
        assert night_decision(0.5, True, luma=200.0) is False

    def test_dark_night_frame_stays_night(self):
        from agent.camera import night_decision
        assert night_decision(0.5, True, luma=60.0) is True

    def test_luma_does_not_affect_day_mode(self):
        from agent.camera import night_decision
        assert night_decision(2.0, False, luma=255.0) is True  # dark scene enters

    def test_mean_luma_measures_brightness(self):
        import io

        from PIL import Image

        from agent.camera import mean_luma
        white = io.BytesIO()
        Image.new("RGB", (64, 64), (255, 255, 255)).save(white, format="JPEG")
        dark = io.BytesIO()
        Image.new("RGB", (64, 64), (10, 10, 10)).save(dark, format="JPEG")
        assert mean_luma(white.getvalue()) > 240
        assert mean_luma(dark.getvalue()) < 30
