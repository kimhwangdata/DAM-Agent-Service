"""Tests for agent.uploader — queue overflow, retry/backoff, no network."""

import io
import json
import urllib.error
from datetime import datetime
from zoneinfo import ZoneInfo

from agent.capture import CaptureItem
from agent.config import Settings
from agent.uploader import Uploader

TZ = ZoneInfo("Asia/Seoul")

SETTINGS = Settings(
    stage="test",
    location_id="TEST",
    device_id="dam-test",
    timezone="Asia/Seoul",
    upload_signer_url="https://signer.example",
    device_token="tok",
    queue_max=2,
)


def make_item(second=0):
    ts = datetime(2026, 8, 13, 12, 0, second, tzinfo=TZ)
    return CaptureItem(
        jpeg=b"\xff\xd8jpeg",
        captured_at=ts,
        ulid=f"ULID{second:022d}",
        key=f"images/TEST/2026-08-13/1200{second:02d}000.jpg",
        camera_metadata={},
    )


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHttp:
    """Callable standing in for urllib.request.urlopen."""

    def __init__(self, fail_signs=0, fail_puts=0):
        self.sign_requests = []
        self.put_requests = []
        self.fail_signs = fail_signs
        self.fail_puts = fail_puts

    def __call__(self, request, timeout=None):
        if request.full_url.endswith("/sign"):
            self.sign_requests.append(json.loads(request.data))
            if self.fail_signs > 0:
                self.fail_signs -= 1
                raise urllib.error.URLError("signer unreachable")
            return FakeResponse(
                json.dumps(
                    {"url": "https://s3.example/put", "key": "signed/key.jpg"}
                ).encode()
            )
        self.put_requests.append(request)
        if self.fail_puts > 0:
            self.fail_puts -= 1
            raise urllib.error.HTTPError(
                request.full_url, 403, "expired", None, io.BytesIO(b"expired")
            )
        return FakeResponse(b"")


def make_uploader(http, sleeps=None):
    return Uploader(
        SETTINGS,
        urlopen=http,
        sleep=(sleeps.append if sleeps is not None else (lambda s: None)),
    )


def test_submit_overflow_drops_oldest_never_blocks():
    uploader = make_uploader(FakeHttp())
    items = [make_item(s) for s in range(3)]
    for item in items:
        uploader.submit(item)
    assert uploader.queue_depth == 2
    assert uploader.counters()["dropped"] == 1
    # oldest (second=0) was dropped; head is second=1
    assert uploader._queue.get_nowait().ulid == items[1].ulid


def test_upload_success_sends_sign_body_and_put_headers():
    http = FakeHttp()
    uploader = make_uploader(http)
    assert uploader.process(make_item(5)) is True

    sign = http.sign_requests[0]
    assert sign["token"] == "tok"
    assert sign["date"] == "2026-08-13"
    assert sign["filename"] == "120005000.jpg"
    assert sign["content_type"] == "image/jpeg"
    assert sign["metadata"]["ulid"] == "ULID0000000000000000000005"
    assert sign["metadata"]["timezone"] == "Asia/Seoul"

    put = http.put_requests[0]
    assert put.data == b"\xff\xd8jpeg"
    assert put.get_header("Content-type") == "image/jpeg"
    assert put.get_header("X-amz-meta-device-id") == "dam-test"
    assert uploader.counters() == {
        "uploaded": 1,
        "dropped": 0,
        "failed_attempts": 0,
    }


def test_sign_failure_retries_with_backoff():
    http = FakeHttp(fail_signs=2)
    sleeps = []
    uploader = make_uploader(http, sleeps)
    assert uploader.process(make_item()) is True
    assert sleeps == [1.0, 2.0]  # exponential backoff
    assert len(http.sign_requests) == 3
    assert uploader.counters()["failed_attempts"] == 2
    assert uploader.counters()["uploaded"] == 1


def test_expired_put_gets_fresh_presign_on_retry():
    http = FakeHttp(fail_puts=1)
    sleeps = []
    uploader = make_uploader(http, sleeps)
    assert uploader.process(make_item()) is True
    assert len(http.sign_requests) == 2  # fresh presign after the 403 PUT
    assert len(http.put_requests) == 2
    assert sleeps == [1.0]


def test_backoff_caps_at_60s():
    http = FakeHttp(fail_signs=8)
    sleeps = []
    uploader = make_uploader(http, sleeps)
    assert uploader.process(make_item()) is True
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]


def test_stop_aborts_retry_loop():
    http = FakeHttp(fail_signs=1000)
    uploader = Uploader(SETTINGS, urlopen=http, sleep=lambda s: uploader._stop.set())
    assert uploader.process(make_item()) is False
    assert uploader.counters()["uploaded"] == 0
