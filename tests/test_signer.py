"""Tests for the upload-signer Lambda handler (DynamoDB/S3 mocked)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "upload-signer"))
import handler  # noqa: E402  (upload-signer/handler.py)

GOOD_TOKEN = "test-token-123"


class FakeTable:
    def __init__(self, items):
        self.items = items

    def get_item(self, Key):
        item = self.items.get(Key["token_hash"])
        return {"Item": item} if item else {}


class FakeS3:
    def __init__(self):
        self.calls = []

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.calls.append((operation, Params, ExpiresIn))
        return f"https://s3.example/{Params['Key']}?sig=abc"


@pytest.fixture()
def table():
    return FakeTable(
        {
            handler.token_hash(GOOD_TOKEN): {
                "location_id": "DIO21",
                "enabled": True,
            },
            handler.token_hash("disabled-token"): {
                "location_id": "PHL",
                "enabled": False,
            },
        }
    )


def _event(body, path="/sign", method="POST"):
    return {
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "body": json.dumps(body),
    }


def _body(resp):
    return json.loads(resp["body"])


GOOD_BODY = {
    "token": GOOD_TOKEN,
    "date": "2026-08-13",
    "filename": "143059123.jpg",
    "content_type": "image/jpeg",
    "metadata": {"ulid": "01ABC", "device-id": "dam-x", "timezone": "Asia/Seoul"},
}


def test_happy_path_signs_correct_key(table):
    s3 = FakeS3()
    resp = handler.handle(_event(GOOD_BODY), s3=s3, table=table)
    assert resp["statusCode"] == 200
    body = _body(resp)
    assert body["key"] == "images/DIO21/2026-08-13/143059123.jpg"
    assert body["url"].startswith("https://s3.example/images/DIO21/")
    (operation, params, ttl) = s3.calls[0]
    assert operation == "put_object"
    assert params["ContentType"] == "image/jpeg"
    assert params["Metadata"]["ulid"] == "01ABC"
    assert ttl == 60


def test_prefix_comes_from_token_not_request(table):
    sneaky = dict(GOOD_BODY)
    sneaky["location_id"] = "OTHER"  # ignored — not part of the contract
    resp = handler.handle(_event(sneaky), s3=FakeS3(), table=table)
    assert _body(resp)["key"].startswith("images/DIO21/")


def test_unknown_token_401(table):
    resp = handler.handle(
        _event({**GOOD_BODY, "token": "wrong"}), s3=FakeS3(), table=table
    )
    assert resp["statusCode"] == 401


def test_disabled_device_403(table):
    resp = handler.handle(
        _event({**GOOD_BODY, "token": "disabled-token"}), s3=FakeS3(), table=table
    )
    assert resp["statusCode"] == 403


@pytest.mark.parametrize(
    "patch",
    [
        {"date": "2026/08/13"},
        {"date": "26-08-13"},
        {"filename": "143059.jpg"},
        {"filename": "143059123.png"},
        {"filename": "../../etc/passwd"},
        {"content_type": "text/html"},
        {"metadata": {"evil": "x"}},
    ],
)
def test_bad_shapes_rejected_400(table, patch):
    resp = handler.handle(_event({**GOOD_BODY, **patch}), s3=FakeS3(), table=table)
    assert resp["statusCode"] == 400


def test_wrong_path_and_method(table):
    assert (
        handler.handle(_event(GOOD_BODY, path="/x"), s3=FakeS3(), table=table)[
            "statusCode"
        ]
        == 404
    )
    assert (
        handler.handle(_event(GOOD_BODY, method="GET"), s3=FakeS3(), table=table)[
            "statusCode"
        ]
        == 405
    )


def test_missing_token_401(table):
    body = {k: v for k, v in GOOD_BODY.items() if k != "token"}
    resp = handler.handle(_event(body), s3=FakeS3(), table=table)
    assert resp["statusCode"] == 401
