"""upload-signer Lambda v2 — presign + fleet gate + status collect.

POST /sign  body: {"token", "date", "filename", "content_type",
                   "metadata"?, "device_id"?, "status"?}

Design: docs/design/02-agent-manager.md §5. The sign call is the fleet's
heartbeat: every request upserts the device's ``reported`` state into the
``knh-dam-agents`` table (auto-registering unknown devices), then the
device's prefix is resolved ``token → device_id → assignment.location_id``
and ``control.capturing`` gates the upload:

  200 {"status": "ok", url, key, expires_in}   capture allowed
  200 {"status": "paused"}                     operator stop — agent skips
  409 {"error": "unassigned"}                  no location yet — agent skips
  401/403/400/404/405                          as before (ADR-0003)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

BUCKET = os.environ.get("BUCKET", "knh-dam-store")
TABLE = os.environ.get("TABLE", "knh-dam-devices")
AGENTS_TABLE = os.environ.get("AGENTS_TABLE", "knh-dam-agents")
IMAGE_PREFIX = os.environ.get("IMAGE_PREFIX", "images/")
URL_TTL_SECONDS = int(os.environ.get("URL_TTL_SECONDS", "60"))

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FILENAME_RE = re.compile(r"^\d{9}\.jpg$")
ALLOWED_CONTENT_TYPE = "image/jpeg"
ALLOWED_METADATA_KEYS = {"ulid", "device-id", "captured-utc", "timezone"}

# status fields accepted into `reported` (02-agent-manager.md §4)
REPORTED_KEYS = {
    "local_ip", "hostname", "agent_version",
    "seq", "uploaded", "dropped", "skipped", "failed_attempts",
    "queue_depth", "interval_s", "capture_size", "timezone", "uptime_s",
    "pi_model", "camera", "temp_c", "throttled", "thermal_state", "event",
    "stage", "capturing",
}


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _clean_reported(status: Any) -> dict[str, Any]:
    """Whitelist + make DynamoDB-safe (floats -> Decimal)."""
    reported: dict[str, Any] = {}
    if isinstance(status, dict):
        for key in REPORTED_KEYS & status.keys():
            value = status[key]
            if isinstance(value, float):
                value = Decimal(str(value))
            if isinstance(value, str | int | bool | Decimal) or value is None:
                reported[key] = value
    reported["at"] = datetime.now(UTC).isoformat()
    return reported


def _upsert_reported(agents: Any, device_id: str, reported: dict) -> dict:
    """Store reported state; auto-register defaults; return the full item."""
    result = agents.update_item(
        Key={"device_id": device_id},
        UpdateExpression=(
            "SET reported = :r, "
            "first_seen = if_not_exists(first_seen, :now), "
            "assignment = if_not_exists(assignment, :assign), "
            "#c = if_not_exists(#c, :control)"
        ),
        ExpressionAttributeNames={"#c": "control"},
        ExpressionAttributeValues={
            ":r": reported,
            ":now": reported["at"],
            ":assign": {"location_id": None, "assigned_at": None},
            ":control": {
                "capturing": True,
                "video_window_start": "00:00",
                "video_window_end": "00:00",
            },
        },
        ReturnValues="ALL_NEW",
    )
    return result["Attributes"]


def handle(event: dict, s3: Any, table: Any, agents: Any) -> dict[str, Any]:
    """Pure request handler — boto3 clients injected for testability."""
    http = event.get("requestContext", {}).get("http", {})
    if event.get("rawPath") != "/sign":
        return _response(404, {"error": "not found"})
    if http.get("method") != "POST":
        return _response(405, {"error": "method not allowed"})

    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _response(400, {"error": "invalid JSON body"})

    token = body.get("token")
    date = body.get("date", "")
    filename = body.get("filename", "")
    content_type = body.get("content_type", "")
    metadata = body.get("metadata") or {}

    if not token:
        return _response(401, {"error": "missing token"})
    if not DATE_RE.match(date):
        return _response(400, {"error": "date must be YYYY-MM-DD"})
    if not FILENAME_RE.match(filename):
        return _response(400, {"error": "filename must be hhmmssfff.jpg"})
    if content_type != ALLOWED_CONTENT_TYPE:
        return _response(400, {"error": f"content_type must be {ALLOWED_CONTENT_TYPE}"})
    if not set(metadata) <= ALLOWED_METADATA_KEYS:
        return _response(400, {"error": "unknown metadata keys"})

    device = table.get_item(Key={"token_hash": token_hash(token)}).get("Item")
    if device is None:
        return _response(401, {"error": "unknown token"})
    if not device.get("enabled"):
        return _response(403, {"error": "device disabled"})

    # Identity comes from the token row; body device_id is a legacy fallback.
    device_id = device.get("device_id") or body.get("device_id")
    if not device_id:
        return _response(401, {"error": "token has no device identity"})

    # The sign call is the heartbeat: always record status (§4 reported).
    record = _upsert_reported(agents, device_id, _clean_reported(body.get("status")))

    location_id = (record.get("assignment") or {}).get("location_id")
    if not location_id:
        return _response(409, {"error": "unassigned"})
    if not (record.get("control") or {}).get("capturing", True):
        return _response(200, {"status": "paused"})

    key = f"{IMAGE_PREFIX}{location_id}/{date}/{filename}"
    params: dict[str, Any] = {
        "Bucket": BUCKET,
        "Key": key,
        "ContentType": ALLOWED_CONTENT_TYPE,
    }
    if metadata:
        params["Metadata"] = {k: str(v) for k, v in metadata.items()}
    url = s3.generate_presigned_url(
        "put_object", Params=params, ExpiresIn=URL_TTL_SECONDS
    )
    return _response(
        200, {"status": "ok", "url": url, "key": key, "expires_in": URL_TTL_SECONDS}
    )


def lambda_handler(event: dict, context: Any) -> dict[str, Any]:
    import boto3

    s3 = boto3.client("s3")
    dynamodb = boto3.resource("dynamodb")
    return handle(
        event,
        s3=s3,
        table=dynamodb.Table(TABLE),
        agents=dynamodb.Table(AGENTS_TABLE),
    )
