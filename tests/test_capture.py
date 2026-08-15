"""Tests for agent.capture — keys, timestamps, pacing (no real sleeps)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from agent.capture import CaptureLoop, build_key, format_hhmmssfff
from agent.config import Settings

TZ = ZoneInfo("Asia/Seoul")

SETTINGS = Settings(
    stage="test",
    location_id="TEST",
    device_id="dam-test",
    timezone="Asia/Seoul",
    upload_signer_url="https://signer.example",
    device_token="tok",
)


class StubCamera:
    def __init__(self, ts):
        self.ts = ts

    def start(self):
        pass

    def capture_jpeg(self):
        return b"\xff\xd8fakejpeg", self.ts, {"source": "stub"}

    def stop(self):
        pass


def test_hhmmssfff_truncates_to_milliseconds():
    ts = datetime(2026, 8, 13, 14, 30, 59, 123456, tzinfo=TZ)
    assert format_hhmmssfff(ts) == "143059123"


def test_hhmmssfff_zero_pads():
    ts = datetime(2026, 8, 13, 0, 0, 0, 1000, tzinfo=TZ)
    assert format_hhmmssfff(ts) == "000000001"


def test_build_key_shape():
    ts = datetime(2026, 8, 13, 14, 30, 59, 123456, tzinfo=TZ)
    assert build_key("images/", "DIO21", ts) == (
        "images/DIO21/2026-08-13/143059123.jpg"
    )


def test_build_key_rolls_to_new_day_folder_at_midnight():
    before = datetime(2026, 8, 13, 23, 59, 59, 999000, tzinfo=TZ)
    after = datetime(2026, 8, 14, 0, 0, 0, 0, tzinfo=TZ)
    assert build_key("images/", "TEST", before).startswith("images/TEST/2026-08-13/")
    assert build_key("images/", "TEST", after).startswith("images/TEST/2026-08-14/")
    assert build_key("images/", "TEST", after).endswith("/000000000.jpg")


def test_capture_once_builds_item_and_calls_sink():
    ts = datetime(2026, 8, 13, 12, 0, 0, 500000, tzinfo=TZ)
    items = []
    loop = CaptureLoop(StubCamera(ts), SETTINGS, items.append)
    item = loop.capture_once()
    assert items == [item]
    assert item.jpeg.startswith(b"\xff\xd8")
    assert item.key == "images/TEST/2026-08-13/120000500.jpg"
    assert len(item.ulid) == 26
    assert item.camera_metadata == {"source": "stub"}


def test_ulids_are_unique_per_capture():
    ts = datetime(2026, 8, 13, 12, 0, 0, tzinfo=TZ)
    loop = CaptureLoop(StubCamera(ts), SETTINGS, lambda item: None)
    assert loop.capture_once().ulid != loop.capture_once().ulid


def _run_one_iteration(capture_seconds):
    """Run exactly one loop iteration with a fake clock; return sleep arg."""
    ticks = iter([0.0, capture_seconds])  # t0, then t after capture
    sleeps = []
    ts = datetime(2026, 8, 13, 12, 0, 0, tzinfo=TZ)
    loop = CaptureLoop(
        StubCamera(ts),
        SETTINGS,
        sink=lambda item: None,
        clock=lambda: next(ticks),
        sleep=lambda s: (sleeps.append(s), loop.stop()),
    )
    loop.run()
    return sleeps


def test_pacing_compensates_for_capture_duration():
    assert _run_one_iteration(capture_seconds=3.0) == [45.0]  # 48 - 3


def test_pacing_never_sleeps_negative():
    assert _run_one_iteration(capture_seconds=50.0) == [0.0]  # slower than interval


def test_capture_failure_does_not_kill_loop():
    class BrokenCamera(StubCamera):
        def capture_jpeg(self):
            raise RuntimeError("boom")

    ticks = iter([0.0, 1.0])
    sleeps = []
    loop = CaptureLoop(
        BrokenCamera(None),
        SETTINGS,
        sink=lambda item: None,
        clock=lambda: next(ticks),
        sleep=lambda s: (sleeps.append(s), loop.stop()),
    )
    loop.run()  # must not raise
    assert sleeps == [47.0]  # still paced after the failure


class TestPreviewBoost:
    def _run_interval(self, preview_on, gate=None):
        """Run one full interval with a simulated clock; sleep advances it."""
        ts = datetime(2026, 8, 13, 12, 0, 0, tzinfo=TZ)
        camera = StubCamera(ts)
        settings = SETTINGS
        published = []
        sunk = []
        state = {"t": 0.0}
        loop = CaptureLoop(
            camera,
            settings,
            sunk.append,
            clock=lambda: state["t"],
            sleep=lambda s: state.__setitem__("t", state["t"] + s)
            or (state["t"] >= settings.interval_s and loop.stop()),
            gate=gate,
            preview_active=lambda: preview_on,
            preview_publish=lambda jpeg, at: published.append(jpeg),
        )
        loop.run()
        return sunk, published

    def test_preview_fills_wait_and_never_reaches_sink(self):
        sunk, published = self._run_interval(preview_on=True)
        assert len(sunk) == 1  # exactly one scheduled upload capture
        # ~one preview per second across the 48 s interval
        assert 40 <= len(published) <= 48

    def test_no_viewer_no_preview_captures(self):
        sunk, published = self._run_interval(preview_on=False)
        assert len(sunk) == 1
        assert published == []

    def test_thermal_pause_stops_previews_too(self):
        sunk, published = self._run_interval(preview_on=True, gate=lambda: False)
        assert sunk == []  # gate skips the scheduled capture
        assert published == []
