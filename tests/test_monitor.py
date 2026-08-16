"""Tests for the upload-monitor Lambda (fake S3 / agents table)."""

import importlib.util
import io
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


def _load_service_constants(service_dir: Path) -> None:
    """Register the service's constants.py as flat ``constants`` (Lambda
    zip layout) right before exec-ing its handler. Each test file re-binds
    it; handlers keep their own reference after exec."""
    spec = importlib.util.spec_from_file_location(
        "constants", service_dir / "constants.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["constants"] = module
    spec.loader.exec_module(module)


_load_service_constants(Path(__file__).resolve().parent.parent / "upload-monitor")
# load under a unique module name (test_signer loads its own "handler")
_spec = importlib.util.spec_from_file_location(
    "monitor_handler",
    Path(__file__).resolve().parent.parent / "upload-monitor" / "handler.py",
)
monitor = importlib.util.module_from_spec(_spec)
sys.modules["monitor_handler"] = monitor
_spec.loader.exec_module(monitor)

NOW = datetime(2026, 8, 13, 12, 0, 0, tzinfo=UTC)
GOOD_JPEG = b"\xff\xd8" + b"x" * 20000 + b"\xff\xd9"


class FakeBody(io.BytesIO):
    pass


class FakeS3:
    def __init__(self, objects):
        self.objects = objects  # key -> bytes
        self.tags = {}

    def get_object(self, Bucket, Key, Range):
        data = self.objects[Key]
        if Range == "bytes=0-1":
            part = data[:2]
        elif Range == "bytes=-2":
            part = data[-2:]
        else:
            raise AssertionError(f"unexpected range {Range}")
        return {"Body": FakeBody(part)}

    def put_object_tagging(self, Bucket, Key, Tagging):
        self.tags[Key] = Tagging["TagSet"]


class FakeAgents:
    def __init__(self, items):
        self.items = items
        self.scan_count = 0

    def scan(self, **kwargs):
        self.scan_count += 1
        return {"Items": list(self.items.values())}

    def get_item(self, Key):
        item = self.items.get(Key["device_id"])
        return {"Item": item} if item else {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues):
        assert UpdateExpression == "SET health = :h"
        self.items[Key["device_id"]]["health"] = ExpressionAttributeValues[":h"]


def _record(key, size):
    return {
        "s3": {
            "bucket": {"name": "knh-dam-store"},
            "object": {"key": key, "size": size},
        }
    }


@pytest.fixture(autouse=True)
def fresh_cache():
    monitor._location_cache.clear()
    yield


@pytest.fixture()
def agents():
    return FakeAgents(
        {
            "dam-imx477-2": {
                "device_id": "dam-imx477-2",
                "assignment": {"location_id": "TEST"},
            }
        }
    )


def test_good_jpeg_updates_last_object_at_no_tag(agents):
    key = "images/TEST/2026-08-13/120000000.jpg"
    s3 = FakeS3({key: GOOD_JPEG})
    outcome = monitor.handle_record(
        _record(key, len(GOOD_JPEG)), s3=s3, agents=agents, now=NOW
    )
    assert outcome.startswith("ok")
    health = agents.items["dam-imx477-2"]["health"]
    assert health["last_object_at"] == NOW.isoformat()
    assert "damaged_recent" not in health
    assert s3.tags == {}


def test_bad_magic_tags_and_counts(agents):
    key = "images/TEST/2026-08-13/120000000.jpg"
    s3 = FakeS3({key: b"PK" + b"x" * 20000 + b"\xff\xd9"})
    outcome = monitor.handle_record(
        _record(key, 20004), s3=s3, agents=agents, now=NOW
    )
    assert "DAMAGED" in outcome
    assert s3.tags[key] == [{"Key": "damaged", "Value": "true"}]
    health = agents.items["dam-imx477-2"]["health"]
    assert health["damaged_recent"] == 1
    assert health["last_damaged_key"] == key
    assert health["last_object_at"] == NOW.isoformat()


def test_truncated_jpeg_detected(agents):
    key = "images/TEST/2026-08-13/120000000.jpg"
    s3 = FakeS3({key: b"\xff\xd8" + b"x" * 20000})  # no EOI
    outcome = monitor.handle_record(
        _record(key, 20002), s3=s3, agents=agents, now=NOW
    )
    assert "truncated" in outcome


def test_size_bounds_skip_get(agents):
    key = "images/TEST/2026-08-13/120000000.jpg"
    s3 = FakeS3({})  # any GET would KeyError -> proves size check short-circuits
    outcome = monitor.handle_record(_record(key, 100), s3=s3, agents=agents, now=NOW)
    assert "too small" in outcome
    outcome = monitor.handle_record(
        _record(key, 99_999_999), s3=s3, agents=agents, now=NOW
    )
    assert "too large" in outcome
    assert agents.items["dam-imx477-2"]["health"]["damaged_recent"] == 2


def test_damaged_window_resets_after_24h(agents):
    agents.items["dam-imx477-2"]["health"] = {
        "damaged_recent": 7,
        "damaged_window_start": (NOW - timedelta(hours=30)).isoformat(),
    }
    key = "images/TEST/2026-08-13/120000000.jpg"
    monitor.handle_record(_record(key, 100), s3=FakeS3({}), agents=agents, now=NOW)
    health = agents.items["dam-imx477-2"]["health"]
    assert health["damaged_recent"] == 1  # window expired -> restarted
    assert health["damaged_window_start"] == NOW.isoformat()


def test_unknown_location_ignored(agents):
    outcome = monitor.handle_record(
        _record("images/NOPE/2026-08-13/120000000.jpg", 20000),
        s3=FakeS3({}), agents=agents, now=NOW,
    )
    assert outcome.startswith("ignored")


def test_non_image_keys_ignored(agents):
    for key in ("videos/TEST/TEST-2026-08-13.mp4", "images/TEST/2026-08-13/x.json"):
        outcome = monitor.handle_record(
            _record(key, 20000), s3=FakeS3({}), agents=agents, now=NOW
        )
        assert outcome.startswith("ignored")
    assert agents.scan_count == 0


def test_location_cache_avoids_rescans(agents):
    key1 = "images/TEST/2026-08-13/120000000.jpg"
    key2 = "images/TEST/2026-08-13/120048000.jpg"
    s3 = FakeS3({key1: GOOD_JPEG, key2: GOOD_JPEG})
    monitor.handle_record(_record(key1, len(GOOD_JPEG)), s3=s3, agents=agents, now=NOW)
    monitor.handle_record(_record(key2, len(GOOD_JPEG)), s3=s3, agents=agents, now=NOW)
    assert agents.scan_count == 1
