"""upload-signer Lambda — presigned PUT URLs for device uploads (ADR-0003).

POST /sign  body: {"token", "date", "filename", "content_type", "metadata"?}

The device token is verified against the ``knh-dam-devices`` DynamoDB table
(PK ``token_hash``; attributes ``location_id``, ``enabled``). The S3 prefix
is derived from the token identity — never from the request — so a token
can only ever sign uploads into its own location's folder. Key shape and
content type are validated before anything is signed (architecture §6
layer 1).

Responses: 200 {"url", "key", "expires_in"} | 400 bad request shape |
401 unknown token | 403 disabled device | 404/405 wrong path/method.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from typing import Any

BUCKET = os.environ.get("BUCKET", "knh-dam-store")
TABLE = os.environ.get("TABLE", "knh-dam-devices")
IMAGE_PREFIX = os.environ.get("IMAGE_PREFIX", "images/")
URL_TTL_SECONDS = int(os.environ.get("URL_TTL_SECONDS", "60"))

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FILENAME_RE = re.compile(r"^\d{9}\.jpg$")
ALLOWED_CONTENT_TYPE = "image/jpeg"
ALLOWED_METADATA_KEYS = {"ulid", "device-id", "captured-utc", "timezone"}


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def handle(event: dict[str, Any], s3: Any, table: Any) -> dict[str, Any]:
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

    # Prefix derived from the token identity — request cannot influence it.
    key = f"{IMAGE_PREFIX}{device['location_id']}/{date}/{filename}"
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
    return _response(200, {"url": url, "key": key, "expires_in": URL_TTL_SECONDS})


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    import boto3

    s3 = boto3.client("s3")
    table = boto3.resource("dynamodb").Table(TABLE)
    return handle(event, s3=s3, table=table)
