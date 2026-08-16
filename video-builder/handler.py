"""video-builder Lambda — dispatch + build (design 03 §2/§5).

Two event shapes on one function:

  {"mode": "dispatch"}                         from the 15-min EventBridge
      scan knh-dam-agents, compute each device's most recent completed
      cycle (cycles.py), async self-invoke a build for every device whose
      last_video lags.

  {"mode": "build", "location_id", "date", "window": {start, end},
   "timezone", "device_id"}
      list -> download -> validate -> ffmpeg -> upload -> record
      last_video. Idempotent: rebuilding overwrites the same key.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import cycles

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BUCKET = os.environ.get("BUCKET", "knh-dam-store")
AGENTS_TABLE = os.environ.get("AGENTS_TABLE", "knh-dam-agents")
IMAGE_PREFIX = os.environ.get("IMAGE_PREFIX", "images/")
VIDEO_PREFIX = os.environ.get("VIDEO_PREFIX", "videos/")
DEFAULT_TIMEZONE = os.environ.get("DEFAULT_TIMEZONE", "Asia/Seoul")
MIN_BYTES = int(os.environ.get("MIN_BYTES", "10000"))
FFMPEG = os.environ.get("FFMPEG_PATH", "/opt/bin/ffmpeg")
DOWNLOAD_THREADS = 16
BUILDER_VERSION = "1.0"

JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"



# ── dispatch ─────────────────────────────────────────────────────────────────

def handle_dispatch(
    agents: Any, invoke: Any, function_name: str, now_utc: datetime
) -> dict[str, Any]:
    due: list[dict[str, Any]] = []
    skipped = 0
    items: list[dict] = []
    scan = agents.scan()
    items.extend(scan.get("Items", []))
    while "LastEvaluatedKey" in scan:
        scan = agents.scan(ExclusiveStartKey=scan["LastEvaluatedKey"])
        items.extend(scan.get("Items", []))

    for record in items:
        location_id = (record.get("assignment") or {}).get("location_id")
        if not location_id:
            skipped += 1
            continue
        control = record.get("control") or {}
        start = control.get("video_window_start", "00:00")
        end = control.get("video_window_end", "00:00")
        tz = (record.get("reported") or {}).get("timezone") or DEFAULT_TIMEZONE
        cycle = cycles.latest_completed_cycle(
            now_utc.astimezone(ZoneInfo(tz)), start, end
        )
        last = (record.get("last_video") or {}).get("date")
        if last is not None and str(last) >= cycle:
            skipped += 1
            continue
        event = {
            "mode": "build",
            "location_id": location_id,
            "date": cycle,
            "window": {"start": start, "end": end},
            "timezone": tz,
            "device_id": record["device_id"],
        }
        invoke(
            FunctionName=function_name,
            InvocationType="Event",
            Payload=json.dumps(event).encode("utf-8"),
        )
        due.append({"location_id": location_id, "date": cycle})

    summary = {"mode": "dispatch", "due": due, "skipped": skipped}
    logger.info(json.dumps(summary))
    return summary


# ── build ────────────────────────────────────────────────────────────────────

def _list_frame_keys(s3: Any, location_id: str, frame_range: cycles.FrameRange
                     ) -> list[str]:
    prefix = f"{IMAGE_PREFIX}{location_id}/{frame_range.day}/"
    keys: list[str] = []
    kwargs: dict[str, Any] = {"Bucket": BUCKET, "Prefix": prefix}
    while True:
        page = s3.list_objects_v2(**kwargs)
        for obj in page.get("Contents", []):
            basename = obj["Key"].rsplit("/", 1)[-1]
            if not basename.endswith(".jpg"):
                continue
            if obj.get("Size", 0) < MIN_BYTES:
                continue  # damaged uploads never enter the video (§5.1)
            if cycles.in_range(basename, frame_range):
                keys.append(obj["Key"])
        if not page.get("IsTruncated"):
            break
        kwargs["ContinuationToken"] = page["NextContinuationToken"]
    return sorted(keys)


def _download_frames(s3: Any, keys: list[str], frames_dir: Path) -> None:
    def fetch(index_key: tuple[int, str]) -> None:
        index, key = index_key
        # sequential names keep cross-folder (midnight) ordering for glob
        s3.download_file(BUCKET, key, str(frames_dir / f"{index:06d}.jpg"))

    with ThreadPoolExecutor(max_workers=DOWNLOAD_THREADS) as pool:
        list(pool.map(fetch, enumerate(keys)))


def _drop_invalid(frames_dir: Path) -> tuple[int, int]:
    """Delete non-JPEG files (legacy remove_invalid_images equivalent)."""
    kept = 0
    dropped = 0
    for path in sorted(frames_dir.glob("*.jpg")):
        data = path.read_bytes()
        if data[:2] == JPEG_SOI and data[-2:] == JPEG_EOI:
            kept += 1
        else:
            path.unlink()
            dropped += 1
    return kept, dropped


def run_ffmpeg(frames_dir: Path, output: Path) -> None:
    # Legacy-proven encode settings (build-upload-video.py) — do not tune.
    command = [
        FFMPEG, "-y", "-framerate", "30",
        "-pattern_type", "glob", "-i", str(frames_dir / "*.jpg"),
        "-c:v", "libx264", "-r", "30", "-pix_fmt", "yuv420p", str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=780)
    if result.returncode != 0:
        logger.error("ffmpeg stderr tail: %s", result.stderr[-2000:])
        raise RuntimeError(f"ffmpeg failed rc={result.returncode}")


def _list_sidecar_keys(
    s3: Any, location_id: str, frame_range: cycles.FrameRange
) -> list[str]:
    prefix = f"{IMAGE_PREFIX}{location_id}/{frame_range.day}/"
    keys: list[str] = []
    kwargs: dict[str, Any] = {"Bucket": BUCKET, "Prefix": prefix}
    while True:
        page = s3.list_objects_v2(**kwargs)
        for obj in page.get("Contents", []):
            basename = obj["Key"].rsplit("/", 1)[-1]
            if not basename.endswith(".json"):
                continue
            stem = basename[: -len(".json")]
            if frame_range.lo <= stem < frame_range.hi:
                keys.append(obj["Key"])
        if not page.get("IsTruncated"):
            break
        kwargs["ContinuationToken"] = page["NextContinuationToken"]
    return sorted(keys)


def _summarize_sidecars(
    s3: Any, location_id: str, cycle_date: str,
    ranges: list[cycles.FrameRange], build_summary: dict[str, Any],
) -> str | None:
    """Digest the day's {hhmmssfff}.json sidecars into one text log for
    videos/{loc}/{LOC}-{date}.log — the hardware-condition record that
    pairs with the daily video. Returns the log text, or None if the day
    has no sidecars (pre-sidecar agents)."""
    keys: list[str] = []
    for frame_range in ranges:
        keys.extend(_list_sidecar_keys(s3, location_id, frame_range))
    if not keys:
        return None

    def fetch(key: str) -> dict[str, Any] | None:
        import io
        buf = io.BytesIO()
        try:
            s3.download_fileobj(BUCKET, key, buf)
            data = json.loads(buf.getvalue())
            data["_stem"] = key.rsplit("/", 1)[-1][: -len(".json")]
            return data
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=DOWNLOAD_THREADS) as pool:
        entries = [e for e in pool.map(fetch, keys) if e]

    lines = [
        f"# {location_id} {cycle_date} capture log "
        f"(builder {BUILDER_VERSION}, {datetime.now(UTC).isoformat()})",
    ]
    first_status = next((e.get("status") or {} for e in entries), {})
    lines.append(
        "# device={device_id} pi={pi_model} camera={camera} "
        "agent={agent_version} capture={capture_size}".format(
            device_id=first_status.get("device_id", "?"),
            pi_model=first_status.get("pi_model", "?"),
            camera=first_status.get("camera", "?"),
            agent_version=first_status.get("agent_version", "?"),
            capture_size=first_status.get("capture_size", "?"),
        )
    )
    lines.append(
        f"# video frames={build_summary.get('frames')} "
        f"skipped_damaged={build_summary.get('skipped_damaged')} "
        f"sidecars={len(entries)}"
    )
    lines.append(
        "# time      bytes  exp_s     gain  lux     temp_c volt   "
        "throttled night"
    )

    def num(entry: dict, *path: str) -> Any:
        value: Any = entry
        for part in path:
            value = value.get(part) if isinstance(value, dict) else None
        return value

    stats: dict[str, list[float]] = {"exp": [], "temp": [], "volt": [], "bytes": []}
    for e in entries:
        meta = e.get("camera_meta") or {}
        status = e.get("status") or {}
        exp_us = meta.get("ExposureTime")
        exp_s = round(float(exp_us) / 1e6, 5) if exp_us is not None else None
        gain = meta.get("AnalogueGain")
        lux = meta.get("Lux")
        temp = status.get("temp_c")
        volt = status.get("volt_core")
        night = "Y" if status.get("night_mode") else "-"
        size = e.get("image_bytes")
        for bucket, value in (("exp", exp_s), ("temp", temp),
                              ("volt", volt), ("bytes", size)):
            if value is not None:
                stats[bucket].append(float(value))
        lines.append(
            f"{e.get('_stem', '?'):9} {size or 0:>6} "
            f"{exp_s if exp_s is not None else '-':<9} "
            f"{round(float(gain), 2) if gain is not None else '-':<5} "
            f"{round(float(lux), 1) if lux is not None else '-':<7} "
            f"{temp if temp is not None else '-':<6} "
            f"{volt if volt is not None else '-':<6} "
            f"{status.get('throttled', '-'):<9} {night}"
        )

    def rng(name: str, values: list[float]) -> str:
        if not values:
            return f"{name}=-"
        return f"{name}={min(values):g}..{max(values):g}"

    lines.append(
        "# stats " + " ".join([
            rng("exp_s", stats["exp"]), rng("temp_c", stats["temp"]),
            rng("volt", stats["volt"]), rng("bytes", stats["bytes"]),
        ])
    )
    return "\n".join(lines) + "\n"


def handle_build(
    event: dict[str, Any],
    s3: Any,
    agents: Any,
    *,
    work_dir: str = "/tmp",
    encoder: Any = run_ffmpeg,
) -> dict[str, Any]:
    started = time.monotonic()
    location_id = event["location_id"]
    cycle_date = event["date"]
    window = event.get("window") or {}
    start = window.get("start", "00:00")
    end = window.get("end", "00:00")

    frames_dir = Path(work_dir) / "frames"
    output = Path(work_dir) / "out.mp4"
    shutil.rmtree(frames_dir, ignore_errors=True)  # warm-container hygiene
    output.unlink(missing_ok=True)
    frames_dir.mkdir(parents=True)

    keys: list[str] = []
    for frame_range in cycles.frame_ranges(cycle_date, start, end):
        keys.extend(_list_frame_keys(s3, location_id, frame_range))
    _download_frames(s3, keys, frames_dir)
    frames, skipped_damaged = _drop_invalid(frames_dir)

    if frames == 0:
        summary = {
            "mode": "build", "location_id": location_id, "date": cycle_date,
            "status": "no-frames", "skipped_damaged": skipped_damaged,
        }
        logger.error(json.dumps(summary))
        return summary

    encoder(frames_dir, output)

    video_key = (
        f"{VIDEO_PREFIX}{location_id}/{location_id}-{cycle_date}.mp4"
    )
    s3.upload_file(
        str(output), BUCKET, video_key,
        ExtraArgs={
            "ContentType": "video/mp4",
            "Metadata": {
                "frames": str(frames),
                "skipped-damaged": str(skipped_damaged),
                "window": f"{start}-{end}",
                "builder-version": BUILDER_VERSION,
            },
        },
    )

    build_ms = int((time.monotonic() - started) * 1000)
    last_video = {
        "date": cycle_date,
        "key": video_key,
        "built_at": datetime.now(UTC).isoformat(),
        "frames": frames,
        "skipped_damaged": skipped_damaged,
        "duration_s": Decimal(str(round(frames / 30.0, 1))),
        "build_ms": build_ms,
    }
    agents.update_item(
        Key={"device_id": event["device_id"]},
        UpdateExpression="SET last_video = :v",
        ExpressionAttributeValues={":v": last_video},
    )

    summary = {
        "mode": "build", "location_id": location_id, "date": cycle_date,
        "status": "ok", "key": video_key, "frames": frames,
        "skipped_damaged": skipped_damaged, "build_ms": build_ms,
    }

    # Daily hardware log from the frame sidecars — best-effort: the video
    # is the deliverable, a log failure must never fail the build.
    try:
        log_text = _summarize_sidecars(
            s3, location_id, cycle_date,
            cycles.frame_ranges(cycle_date, start, end), summary,
        )
        if log_text is not None:
            log_path = Path(work_dir) / "day.log"
            log_path.write_text(log_text, encoding="utf-8")
            log_key = f"{VIDEO_PREFIX}{location_id}/{location_id}-{cycle_date}.log"
            s3.upload_file(
                str(log_path), BUCKET, log_key,
                ExtraArgs={"ContentType": "text/plain"},
            )
            summary["log_key"] = log_key
    except Exception:
        logger.exception("sidecar log failed (build unaffected)")

    logger.info(json.dumps(summary))
    return summary


# ── entrypoint ───────────────────────────────────────────────────────────────

def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    import boto3

    mode = event.get("mode")
    if mode == "dispatch":
        agents = boto3.resource("dynamodb").Table(AGENTS_TABLE)
        lam = boto3.client("lambda")
        return handle_dispatch(
            agents, lam.invoke,
            os.environ["AWS_LAMBDA_FUNCTION_NAME"], datetime.now(UTC),
        )
    if mode == "build":
        s3 = boto3.client("s3")
        agents = boto3.resource("dynamodb").Table(AGENTS_TABLE)
        return handle_build(event, s3, agents)
    raise ValueError(f"unknown mode: {mode!r}")
