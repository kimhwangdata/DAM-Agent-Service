"""upload-monitor Lambda — server-observed upload truth (design 02 §5.1).

Triggered by s3:ObjectCreated on knh-dam-store/images/*. For each landed
object it resolves the key's location back to the device (agents table)
and updates the device's ``health``:

- ``last_object_at`` — proof an upload actually landed (the signer only
  signs; it never sees the PUT), which drives the ``stale`` health state.
- damaged-file detection — JPEG magic bytes (FF D8 … FF D9 via two ranged
  GETs) and size bounds. Failures tag the object (``damaged=true``) so
  the video builder can skip it, and maintain a rolling ~24 h counter
  (``damaged_recent``) that drives the ``suspect`` state.
- content moderation — RESERVED (§5.1.3); ``content_flag`` fields exist
  in the schema but nothing sets them here in v1.

Counter updates are read-modify-write; concurrent events for one device
could lose an increment. Accepted for v1: frames arrive one interval
apart and thresholds tolerate ±1.
"""

from __future__ import annotations

import logging
import os
import time
import urllib.parse
from datetime import UTC, datetime, timedelta
from typing import Any

from constants import (
    AGENTS_TABLE_DEFAULT,
    CACHE_TTL_S,
    DAMAGED_WINDOW_HOURS,
    IMAGE_PREFIX_DEFAULT,
    JPEG_EOI,
    JPEG_SOI,
    MAX_BYTES_DEFAULT,
    MIN_BYTES_DEFAULT,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

AGENTS_TABLE = os.environ.get("AGENTS_TABLE", AGENTS_TABLE_DEFAULT)
IMAGE_PREFIX = os.environ.get("IMAGE_PREFIX", IMAGE_PREFIX_DEFAULT)
MIN_BYTES = int(os.environ.get("MIN_BYTES", str(MIN_BYTES_DEFAULT)))
MAX_BYTES = int(os.environ.get("MAX_BYTES", str(MAX_BYTES_DEFAULT)))

# location_id -> (device_id, cached_until_monotonic)
_location_cache: dict[str, tuple[str, float]] = {}


def _resolve_device(agents: Any, location_id: str) -> str | None:
    cached = _location_cache.get(location_id)
    if cached and cached[1] > time.monotonic():
        return cached[0]
    scan = agents.scan(
        ProjectionExpression="device_id, assignment"
    )
    for item in scan.get("Items", []):
        if (item.get("assignment") or {}).get("location_id") == location_id:
            device_id = item["device_id"]
            _location_cache[location_id] = (
                device_id, time.monotonic() + CACHE_TTL_S
            )
            return device_id
    return None


def _is_damaged(s3: Any, bucket: str, key: str, size: int) -> str | None:
    """Return a reason string if the object is damaged, else None."""
    if size < MIN_BYTES:
        return f"too small ({size} B)"
    if size > MAX_BYTES:
        return f"too large ({size} B)"
    head = s3.get_object(Bucket=bucket, Key=key, Range="bytes=0-1")["Body"].read()
    if head != JPEG_SOI:
        return "bad JPEG start marker"
    tail = s3.get_object(Bucket=bucket, Key=key, Range="bytes=-2")["Body"].read()
    if tail != JPEG_EOI:
        return "bad JPEG end marker (truncated?)"
    return None


def _updated_health(current: dict, now: datetime, damaged_key: str | None) -> dict:
    health = dict(current or {})
    health["last_object_at"] = now.isoformat()
    if damaged_key is not None:
        window_start = health.get("damaged_window_start")
        expired = (
            window_start is None
            or datetime.fromisoformat(window_start)
            < now - timedelta(hours=DAMAGED_WINDOW_HOURS)
        )
        if expired:
            health["damaged_window_start"] = now.isoformat()
            health["damaged_recent"] = 1
        else:
            health["damaged_recent"] = int(health.get("damaged_recent", 0)) + 1
        health["last_damaged_key"] = damaged_key
    return health


def handle_record(record: dict, s3: Any, agents: Any, now: datetime) -> str:
    """Process one S3 event record; returns a short outcome string."""
    bucket = record["s3"]["bucket"]["name"]
    key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
    size = int(record["s3"]["object"].get("size", 0))

    if not key.startswith(IMAGE_PREFIX) or not key.endswith(".jpg"):
        return f"ignored (not an image key): {key}"
    location_id = key[len(IMAGE_PREFIX):].split("/", 1)[0]
    device_id = _resolve_device(agents, location_id)
    if device_id is None:
        return f"ignored (no device for location {location_id}): {key}"

    damage = _is_damaged(s3, bucket, key, size)
    if damage is not None:
        s3.put_object_tagging(
            Bucket=bucket,
            Key=key,
            Tagging={"TagSet": [{"Key": "damaged", "Value": "true"}]},
        )

    current = (
        agents.get_item(Key={"device_id": device_id}).get("Item") or {}
    ).get("health") or {}
    agents.update_item(
        Key={"device_id": device_id},
        UpdateExpression="SET health = :h",
        ExpressionAttributeValues={
            ":h": _updated_health(current, now, key if damage else None)
        },
    )
    if damage:
        return f"DAMAGED ({damage}): {key}"
    return f"ok: {key}"


def lambda_handler(event: dict, context: Any) -> dict[str, Any]:
    import boto3

    s3 = boto3.client("s3")
    agents = boto3.resource("dynamodb").Table(AGENTS_TABLE)
    now = datetime.now(UTC)
    outcomes = []
    for record in event.get("Records", []):
        try:
            outcome = handle_record(record, s3=s3, agents=agents, now=now)
        except Exception:
            logger.exception("record failed")
            outcome = "error"
        logger.info(outcome)
        outcomes.append(outcome)
    return {"outcomes": outcomes}
