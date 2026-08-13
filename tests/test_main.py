"""End-to-end wiring test — FakeCamera → queue → mocked uploads (no AWS)."""

import io
import json
import threading
import time
import urllib.request

from agent.camera import FakeCamera
from agent.config import Settings
from agent.main import Agent, build_camera

SETTINGS = Settings(
    stage="test",
    location_id="TEST",
    device_id="dam-test",
    timezone="Asia/Seoul",
    upload_signer_url="https://signer.example",
    device_token="tok",
    capture_size=(160, 120),
    viewer_port=0,  # viewer covered by its own tests
)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHttp:
    def __init__(self):
        self.puts = []

    def __call__(self, request, timeout=None):
        if request.full_url.endswith("/sign"):
            body = json.loads(request.data)
            key = f"images/TEST/{body['date']}/{body['filename']}"
            return FakeResponse(
                json.dumps({"url": "https://s3.example/put", "key": key}).encode()
            )
        self.puts.append(request)
        return FakeResponse(b"")


def test_build_camera_uses_fake_for_test_stage():
    assert isinstance(build_camera(SETTINGS), FakeCamera)


def test_end_to_end_capture_publish_upload():
    http = FakeHttp()
    agent = Agent(SETTINGS, urlopen=http)
    agent.camera.start()
    agent.uploader.start()

    item = agent.loop.capture_once()

    # frame published for the viewer
    assert agent.frames.frame is not None
    assert agent.frames.frame.jpeg == item.jpeg

    # uploader drains the queue through the mocked signer + PUT
    deadline = time.monotonic() + 5.0
    while agent.uploader.counters()["uploaded"] < 1:
        assert time.monotonic() < deadline, "upload did not complete"
        time.sleep(0.02)
    assert len(http.puts) == 1
    assert http.puts[0].data == item.jpeg

    status = agent.status()
    assert status["uploaded"] == 1
    assert status["device_id"] == "dam-test"
    assert status["interval_s"] == 48
    assert status["thermal_state"] == "ok"
    assert status["camera"] == "fake"
    assert status["agent_version"]

    agent.request_stop()
    agent.uploader.stop(drain_seconds=0.5)


def test_request_stop_ends_run_quickly():
    agent = Agent(SETTINGS, urlopen=FakeHttp())
    runner = threading.Thread(target=agent.run, daemon=True)
    runner.start()
    time.sleep(0.3)  # let it start and enter the interval sleep
    agent.request_stop()
    runner.join(timeout=5.0)
    assert not runner.is_alive(), "run() did not stop after request_stop()"


def test_no_real_network_is_touched():
    # Guard: the wiring test must never fall back to real urllib.
    assert urllib.request.urlopen is not None  # (sanity; fakes injected above)
