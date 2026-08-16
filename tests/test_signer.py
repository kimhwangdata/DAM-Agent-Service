"""Tests for the upload-signer Lambda handler v2 (DynamoDB/S3 mocked)."""

import importlib.util
import json
import sys
from decimal import Decimal
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


_load_service_constants(Path(__file__).resolve().parent.parent / "upload-signer")
# load under a unique module name (test_monitor loads its own "handler")
_spec = importlib.util.spec_from_file_location(
    "signer_handler",
    Path(__file__).resolve().parent.parent / "upload-signer" / "handler.py",
)
handler = importlib.util.module_from_spec(_spec)
sys.modules["signer_handler"] = handler
_spec.loader.exec_module(handler)

GOOD_TOKEN = "test-token-123"


class FakeTokenTable:
    def __init__(self, items):
        self.items = items

    def get_item(self, Key):
        item = self.items.get(Key["token_hash"])
        return {"Item": item} if item else {}


class FakeAgentsTable:
    """Mimics the handler's update_item contract (not generic DynamoDB)."""

    DEFAULT_ASSIGNMENT = {"location_id": None, "assigned_at": None}
    DEFAULT_CONTROL = {
        "capturing": True,
        "video_window_start": "00:00",
        "video_window_end": "00:00",
    }

    def __init__(self, items=None):
        self.items = items or {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames,
                    ExpressionAttributeValues, ReturnValues):
        device_id = Key["device_id"]
        item = self.items.setdefault(device_id, {"device_id": device_id})
        item["reported"] = ExpressionAttributeValues[":r"]
        item.setdefault("first_seen", ExpressionAttributeValues[":now"])
        item.setdefault("assignment", dict(self.DEFAULT_ASSIGNMENT))
        item.setdefault("control", dict(self.DEFAULT_CONTROL))
        return {"Attributes": item}


class FakeS3:
    def __init__(self):
        self.calls = []

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.calls.append((operation, Params, ExpiresIn))
        return f"https://s3.example/{Params['Key']}?sig=abc"


@pytest.fixture()
def tokens():
    return FakeTokenTable(
        {
            handler.token_hash(GOOD_TOKEN): {
                "device_id": "dam-imx477-2",
                "location_id": "IGNORED-legacy-field",
                "enabled": True,
            },
            handler.token_hash("disabled-token"): {
                "device_id": "dam-x",
                "enabled": False,
            },
        }
    )


@pytest.fixture()
def agents():
    return FakeAgentsTable(
        {
            "dam-imx477-2": {
                "device_id": "dam-imx477-2",
                "assignment": {"location_id": "DIO21", "assigned_at": "x"},
                "control": {
                    "capturing": True,
                    "video_window_start": "00:00",
                    "video_window_end": "00:00",
                },
            }
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
    "device_id": "dam-imx477-2",
    "status": {"uploaded": 7, "temp_c": 61.2, "thermal_state": "ok"},
}


def test_happy_path_signs_key_from_assignment(tokens, agents):
    s3 = FakeS3()
    resp = handler.handle(_event(GOOD_BODY), s3=s3, table=tokens, agents=agents)
    assert resp["statusCode"] == 200
    body = _body(resp)
    assert body["status"] == "ok"
    # location comes from assignment (DIO21), NOT the token row's legacy field
    assert body["key"] == "images/DIO21/2026-08-13/143059123.jpg"
    (_, params, ttl) = s3.calls[0]
    assert params["ContentType"] == "image/jpeg"
    assert ttl == 60


def test_status_is_upserted_as_reported(tokens, agents):
    handler.handle(_event(GOOD_BODY), s3=FakeS3(), table=tokens, agents=agents)
    reported = agents.items["dam-imx477-2"]["reported"]
    assert reported["uploaded"] == 7
    assert reported["temp_c"] == Decimal("61.2")  # DynamoDB-safe numbers
    assert reported["thermal_state"] == "ok"
    assert "at" in reported


def test_unknown_status_keys_are_dropped(tokens, agents):
    body = {**GOOD_BODY, "status": {"uploaded": 1, "evil": "x", "hack": 1}}
    handler.handle(_event(body), s3=FakeS3(), table=tokens, agents=agents)
    reported = agents.items["dam-imx477-2"]["reported"]
    assert "evil" not in reported and "hack" not in reported


def test_legacy_body_without_status_still_works(tokens, agents):
    body = {k: v for k, v in GOOD_BODY.items() if k not in ("status", "device_id")}
    resp = handler.handle(_event(body), s3=FakeS3(), table=tokens, agents=agents)
    assert _body(resp)["status"] == "ok"
    assert "at" in agents.items["dam-imx477-2"]["reported"]


def test_unknown_device_auto_registers_and_is_unassigned(tokens):
    agents = FakeAgentsTable()  # empty fleet
    resp = handler.handle(_event(GOOD_BODY), s3=FakeS3(), table=tokens, agents=agents)
    assert resp["statusCode"] == 409
    assert _body(resp)["error"] == "unassigned"
    item = agents.items["dam-imx477-2"]
    assert item["control"]["capturing"] is True
    assert item["assignment"]["location_id"] is None
    assert item["first_seen"]


def test_paused_device_gets_paused_not_url(tokens, agents):
    agents.items["dam-imx477-2"]["control"]["capturing"] = False
    s3 = FakeS3()
    resp = handler.handle(_event(GOOD_BODY), s3=s3, table=tokens, agents=agents)
    assert resp["statusCode"] == 200
    assert _body(resp)["status"] == "paused"
    assert _body(resp)["window"] == {"start": "00:00", "end": "00:00"}
    assert s3.calls == []  # nothing signed
    # status still recorded while paused
    assert agents.items["dam-imx477-2"]["reported"]["uploaded"] == 7


def test_unknown_token_401(tokens, agents):
    resp = handler.handle(
        _event({**GOOD_BODY, "token": "wrong"}), s3=FakeS3(), table=tokens,
        agents=agents,
    )
    assert resp["statusCode"] == 401


def test_disabled_device_403(tokens, agents):
    resp = handler.handle(
        _event({**GOOD_BODY, "token": "disabled-token"}), s3=FakeS3(),
        table=tokens, agents=agents,
    )
    assert resp["statusCode"] == 403


@pytest.mark.parametrize(
    "patch",
    [
        {"date": "2026/08/13"},
        {"filename": "143059.jpg"},
        {"filename": "../../etc/passwd"},
        {"content_type": "text/html"},
        {"metadata": {"evil": "x"}},
    ],
)
def test_bad_shapes_rejected_400(tokens, agents, patch):
    resp = handler.handle(
        _event({**GOOD_BODY, **patch}), s3=FakeS3(), table=tokens, agents=agents
    )
    assert resp["statusCode"] == 400


def test_wrong_path_and_method(tokens, agents):
    kwargs = {"s3": FakeS3(), "table": tokens, "agents": agents}
    assert handler.handle(_event(GOOD_BODY, path="/x"), **kwargs)["statusCode"] == 404
    assert (
        handler.handle(_event(GOOD_BODY, method="GET"), **kwargs)["statusCode"] == 405
    )


def test_sidecar_url_returned_when_requested(tokens, agents):
    s3 = FakeS3()
    body = dict(GOOD_BODY, sidecar=True)
    resp = handler.handle(_event(body), s3=s3, table=tokens, agents=agents)
    assert resp["statusCode"] == 200
    data = _body(resp)
    assert data["sidecar_key"] == "images/DIO21/2026-08-13/143059123.json"
    assert data["sidecar_url"].endswith("143059123.json?sig=abc")
    # sidecar presign uses JSON content type
    sidecar_call = s3.calls[-1]
    assert sidecar_call[1]["ContentType"] == "application/json"


def test_no_sidecar_url_without_request(tokens, agents):
    resp = handler.handle(
        _event(GOOD_BODY), s3=FakeS3(), table=tokens, agents=agents
    )
    assert "sidecar_url" not in _body(resp)


def test_response_includes_capture_window(tokens, agents):
    agents.items["dam-imx477-2"]["control"]["video_window_start"] = "06:00"
    agents.items["dam-imx477-2"]["control"]["video_window_end"] = "18:00"
    resp = handler.handle(
        _event(GOOD_BODY), s3=FakeS3(), table=tokens, agents=agents
    )
    assert _body(resp)["window"] == {"start": "06:00", "end": "18:00"}
