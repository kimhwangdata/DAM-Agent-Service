"""Tests for the video-builder Lambda (cycles math + dispatch + build)."""

import importlib.util
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_DIR = Path(__file__).resolve().parent.parent / "video-builder"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cycles = _load("cycles", "cycles.py")  # handler does `import cycles`
builder = _load("builder_handler", "handler.py")

SEOUL = ZoneInfo("Asia/Seoul")
GOOD_JPEG = b"\xff\xd8" + b"x" * 20000 + b"\xff\xd9"


def local(day: str, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=SEOUL)


# ── cycles.py ────────────────────────────────────────────────────────────────

class TestCycleMath:
    @pytest.mark.parametrize(
        ("now", "start", "end", "expected"),
        [
            # default window: yesterday completes at local midnight
            (local("2026-08-14", "00:01"), "00:00", "00:00", "2026-08-13"),
            (local("2026-08-14", "23:59"), "00:00", "00:00", "2026-08-13"),
            # same-day window 06:00->18:00
            (local("2026-08-14", "17:59"), "06:00", "18:00", "2026-08-13"),
            (local("2026-08-14", "18:00"), "06:00", "18:00", "2026-08-14"),
            (local("2026-08-14", "05:00"), "06:00", "18:00", "2026-08-13"),
            # midnight-crossing 18:00->06:00: cycle D ends at D+1 06:00
            (local("2026-08-14", "06:00"), "18:00", "06:00", "2026-08-13"),
            (local("2026-08-14", "05:59"), "18:00", "06:00", "2026-08-12"),
            (local("2026-08-14", "23:00"), "18:00", "06:00", "2026-08-13"),
        ],
    )
    def test_latest_completed_cycle(self, now, start, end, expected):
        assert cycles.latest_completed_cycle(now, start, end) == expected

    def test_frame_ranges_default_full_day(self):
        ranges = cycles.frame_ranges("2026-08-13", "00:00", "00:00")
        assert ranges == [cycles.FrameRange("2026-08-13", "000000000", "999999999")]

    def test_frame_ranges_same_day(self):
        ranges = cycles.frame_ranges("2026-08-13", "06:00", "18:00")
        assert ranges == [cycles.FrameRange("2026-08-13", "060000000", "180000000")]

    def test_frame_ranges_midnight_crossing_spans_two_folders(self):
        ranges = cycles.frame_ranges("2026-08-13", "18:00", "06:00")
        assert ranges == [
            cycles.FrameRange("2026-08-13", "180000000", "999999999"),
            cycles.FrameRange("2026-08-14", "000000000", "060000000"),
        ]

    def test_in_range_bounds(self):
        r = cycles.FrameRange("2026-08-13", "060000000", "180000000")
        assert cycles.in_range("060000000.jpg", r)  # inclusive lo
        assert cycles.in_range("175959999.jpg", r)
        assert not cycles.in_range("180000000.jpg", r)  # exclusive hi
        assert not cycles.in_range("055959999.jpg", r)

    def test_bad_hhmm_rejected(self):
        with pytest.raises(ValueError):
            cycles.hhmm_to_prefix("25:99")


# ── dispatch ─────────────────────────────────────────────────────────────────

class FakeAgents:
    def __init__(self, items):
        self.items = {i["device_id"]: i for i in items}
        self.updates = []

    def scan(self, **kwargs):
        return {"Items": list(self.items.values())}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues):
        self.updates.append((Key["device_id"], ExpressionAttributeValues[":v"]))


def record(device_id, location, last_date=None, tz="Asia/Seoul"):
    return {
        "device_id": device_id,
        "assignment": {"location_id": location},
        "control": {
            "capturing": True,
            "video_window_start": "00:00",
            "video_window_end": "00:00",
        },
        "reported": {"timezone": tz},
        **({"last_video": {"date": last_date}} if last_date else {}),
    }


class TestDispatch:
    NOW = datetime(2026, 8, 13, 16, 0, tzinfo=UTC)  # 2026-08-14 01:00 KST

    def run(self, items):
        invocations = []

        def invoke(**kwargs):
            invocations.append(json.loads(kwargs["Payload"]))

        summary = builder.handle_dispatch(
            FakeAgents(items), invoke, "fn", self.NOW
        )
        return summary, invocations

    def test_lagging_device_gets_build(self):
        summary, invocations = self.run([record("d1", "JAYANG2", "2026-08-12")])
        assert summary["due"] == [{"location_id": "JAYANG2", "date": "2026-08-13"}]
        assert invocations[0]["mode"] == "build"
        assert invocations[0]["device_id"] == "d1"
        assert invocations[0]["window"] == {"start": "00:00", "end": "00:00"}

    def test_never_built_device_gets_build(self):
        _, invocations = self.run([record("d1", "JAYANG2")])
        assert len(invocations) == 1

    def test_current_device_skipped(self):
        summary, invocations = self.run([record("d1", "JAYANG2", "2026-08-13")])
        assert invocations == []
        assert summary["skipped"] == 1

    def test_unassigned_skipped(self):
        items = [record("d1", "JAYANG2", "2026-08-12")]
        items.append({"device_id": "d2", "assignment": {"location_id": None}})
        _, invocations = self.run(items)
        assert len(invocations) == 1


# ── build ────────────────────────────────────────────────────────────────────

class FakeS3:
    def __init__(self, objects):
        self.objects = objects  # key -> bytes
        self.uploads = []

    def list_objects_v2(self, Bucket, Prefix, **kwargs):
        contents = [
            {"Key": k, "Size": len(v)}
            for k, v in sorted(self.objects.items())
            if k.startswith(Prefix)
        ]
        return {"Contents": contents, "IsTruncated": False}

    def download_file(self, Bucket, Key, Filename):
        Path(Filename).write_bytes(self.objects[Key])

    def upload_file(self, Filename, Bucket, Key, ExtraArgs=None):
        self.uploads.append({"key": Key, "bytes": Path(Filename).read_bytes(),
                             "extra": ExtraArgs})


def stub_encoder(frames_dir: Path, output: Path) -> None:
    names = sorted(p.name for p in frames_dir.glob("*.jpg"))
    output.write_bytes(("VIDEO:" + ",".join(names)).encode())


def day_key(loc, day, hhmmssfff):
    return f"images/{loc}/{day}/{hhmmssfff}.jpg"


class TestBuild:
    def build(self, s3, agents, tmp_path, *, date="2026-08-13",
              window=None, encoder=stub_encoder):
        event = {
            "mode": "build", "location_id": "JAYANG3", "date": date,
            "window": window or {"start": "00:00", "end": "00:00"},
            "timezone": "Asia/Seoul", "device_id": "dam-imx477-3",
        }
        return builder.handle_build(
            event, s3, agents, work_dir=str(tmp_path), encoder=encoder
        )

    def test_happy_path_builds_uploads_and_records(self, tmp_path):
        s3 = FakeS3({
            day_key("JAYANG3", "2026-08-13", "120000000"): GOOD_JPEG,
            day_key("JAYANG3", "2026-08-13", "120048000"): GOOD_JPEG,
        })
        agents = FakeAgents([])
        summary = self.build(s3, agents, tmp_path)
        assert summary["status"] == "ok"
        assert summary["frames"] == 2
        upload = s3.uploads[0]
        assert upload["key"] == "videos/JAYANG3/JAYANG3-2026-08-13.mp4"
        assert upload["extra"]["ContentType"] == "video/mp4"
        assert upload["extra"]["Metadata"]["frames"] == "2"
        device, last_video = agents.updates[0]
        assert device == "dam-imx477-3"
        assert last_video["date"] == "2026-08-13"
        assert last_video["frames"] == 2

    def test_tiny_and_corrupt_frames_are_excluded(self, tmp_path):
        s3 = FakeS3({
            day_key("JAYANG3", "2026-08-13", "120000000"): GOOD_JPEG,
            day_key("JAYANG3", "2026-08-13", "120048000"): b"tiny",  # size drop
            day_key("JAYANG3", "2026-08-13", "120136000"): b"X" * 20000,  # bad magic
        })
        agents = FakeAgents([])
        summary = self.build(s3, agents, tmp_path)
        assert summary["frames"] == 1
        assert summary["skipped_damaged"] == 1  # bad magic (tiny dropped earlier)

    def test_midnight_crossing_orders_across_folders(self, tmp_path):
        s3 = FakeS3({
            day_key("JAYANG3", "2026-08-14", "010000000"): GOOD_JPEG,  # next day
            day_key("JAYANG3", "2026-08-13", "230000000"): GOOD_JPEG,  # tail first
            day_key("JAYANG3", "2026-08-13", "120000000"): GOOD_JPEG,  # outside
        })
        agents = FakeAgents([])
        summary = self.build(
            s3, agents, tmp_path, window={"start": "18:00", "end": "06:00"}
        )
        assert summary["frames"] == 2
        video = s3.uploads[0]["bytes"].decode()
        # sequential download names preserve D-tail before D+1-head ordering
        assert video == "VIDEO:000000.jpg,000001.jpg"

    def test_zero_frames_guard_no_upload_no_record(self, tmp_path):
        s3 = FakeS3({})
        agents = FakeAgents([])
        summary = self.build(s3, agents, tmp_path)
        assert summary["status"] == "no-frames"
        assert s3.uploads == []
        assert agents.updates == []

    def test_encoder_failure_leaves_no_record(self, tmp_path):
        def broken(frames_dir, output):
            raise RuntimeError("ffmpeg failed")

        s3 = FakeS3({day_key("JAYANG3", "2026-08-13", "120000000"): GOOD_JPEG})
        agents = FakeAgents([])
        with pytest.raises(RuntimeError):
            self.build(s3, agents, tmp_path, encoder=broken)
        assert s3.uploads == []
        assert agents.updates == []


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_real_ffmpeg_encodes(tmp_path, monkeypatch):
    from PIL import Image

    frames = tmp_path / "frames"
    frames.mkdir()
    for i in range(3):
        Image.new("RGB", (64, 48), color=(i * 40, 80, 120)).save(
            frames / f"{i:06d}.jpg"
        )
    monkeypatch.setattr(builder, "FFMPEG", shutil.which("ffmpeg"))
    output = tmp_path / "out.mp4"
    builder.run_ffmpeg(frames, output)
    assert output.stat().st_size > 500
