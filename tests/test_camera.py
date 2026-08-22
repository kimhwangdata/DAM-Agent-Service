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


class TestNightController:
    def _controller(self, **kw):
        from agent.camera import NightController
        state = {"t": 0.0}
        kw.setdefault("clock", lambda: state["t"])
        c = NightController(**kw)
        c._test_time = state  # advance via c._test_time["t"]
        return c

    def test_entry_needs_consecutive_dark_frames(self):
        c = self._controller()
        assert c.update(2.0, None) is False
        assert c.update(2.0, None) is False
        assert c.update(2.0, None) is True  # third confirmation enters

    def test_lux_blip_does_not_enter(self):
        c = self._controller()
        c.update(2.0, None)
        c.update(15.0, None)  # blip above threshold resets the streak
        c.update(2.0, None)
        assert c.update(2.0, None) is False

    def _make_night(self, c):
        for _ in range(3):
            c.update(2.0, None)
        assert c.is_night

    def test_single_blown_frame_does_not_exit(self):
        c = self._controller()
        self._make_night(c)
        assert c.update(0.5, 255.0) is True  # one blown frame ignored
        assert c.update(0.5, 60.0) is True   # streak reset

    def test_three_blown_frames_exit_with_cooldown(self):
        c = self._controller()
        self._make_night(c)
        c.update(0.5, 255.0)
        c.update(0.5, 255.0)
        assert c.update(0.5, 255.0) is False  # exits
        # still dark by lux, but cooldown blocks re-entry
        for _ in range(5):
            assert c.update(0.5, None) is False
        c._test_time["t"] += 901  # cooldown elapsed
        c.update(0.5, None)
        c.update(0.5, None)
        assert c.update(0.5, None) is True  # re-enters after cooldown

    def test_daylight_lux_exit_has_no_cooldown(self):
        c = self._controller()
        self._make_night(c)
        for _ in range(3):
            c.update(500.0, 100.0)
        assert c.is_night is False
        # night falls again - no cooldown for a trusted lux exit
        c.update(2.0, None)
        c.update(2.0, None)
        assert c.update(2.0, None) is True

    def test_unknown_values_keep_state(self):
        c = self._controller()
        assert c.update(None, None) is False
        self._make_night(c)
        assert c.update(None, None) is True


class _FakeCam:
    """Records set_controls calls; returns a scripted Lux per probe."""

    def __init__(self, lux_values):
        self._lux = iter(lux_values)
        self.controls_calls = []

    def set_controls(self, controls):
        self.controls_calls.append(controls)

    def capture_metadata(self):
        return {"Lux": next(self._lux)}


class TestAeProbe:
    """The dawn-exit fix (2026-08-23): while night mode is on, every
    capture cycle meters with AE first so the exit decision never sees
    a saturated lux estimate."""

    def _night_camera(self, lux_values, monkeypatch):
        monkeypatch.setattr("agent.camera.time.sleep", lambda s: None)
        cam = Picamera2Camera(tz=TZ, night_exposure_ms=250, night_gain=2.0)
        cam._night.is_night = True
        cam._cam = _FakeCam(lux_values)
        return cam

    def test_dark_probe_stays_night_and_restores_manual(self, monkeypatch):
        cam = self._night_camera([0.5], monkeypatch)
        cam._probe_ae()
        assert cam.is_night is True
        assert cam._cam.controls_calls[0] == {"AeEnable": True}
        assert cam._cam.controls_calls[-1] == {
            "AeEnable": False,
            "ExposureTime": 250_000,
            "AnalogueGain": 2.0,
        }

    def test_bright_probes_exit_after_confirm_frames(self, monkeypatch):
        # dawn: true lux is far above NIGHT_LUX_OFF once AE meters it
        cam = self._night_camera([100.0, 120.0, 150.0], monkeypatch)
        cam._probe_ae()
        cam._probe_ae()
        assert cam.is_night is True  # two agreeing probes are not enough
        cam._probe_ae()
        assert cam.is_night is False  # third confirmation exits
        # AE was left enabled — no manual re-apply after the exit probe
        assert cam._cam.controls_calls[-1] == {"AeEnable": True}


def test_pivariety_model_reports_real_sensor():
    from agent.camera import resolve_camera_model
    assert resolve_camera_model("arducam-pivariety") == "imx462"
    assert resolve_camera_model("imx477") == "imx477"
    assert resolve_camera_model(None) is None
