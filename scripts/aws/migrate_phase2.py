"""Phase 2 bench fleet records (idempotent) — plan 2.1/2.4.

Creates/completes the bench devices' records in knh-dam-agents (access +
hardware are operator-maintained truth; docs/reference/camera-info.md) and
stamps device_id onto legacy token rows. Safe to re-run: an existing
assignment set by the operator is only overwritten where this script
declares one explicitly (the running bench device), and `control` is never
overwritten.

Run:  python scripts/aws/migrate_phase2.py   (profile knh-dev)
"""

from __future__ import annotations

from datetime import UTC, datetime

import boto3

PROFILE = "dam-deployer"
REGION = "ap-northeast-2"

# Bench reality — docs/reference/camera-info.md + memory notes.
BENCH_DEVICES = [
    {
        "device_id": "dam-imx477-2",
        "location_id": "JAYANG2",
        "access": {
            "ssh_accessible": True,
            "ip": "192.168.70.109",
            "ssh_user": "cskim",
            "note": "bench, KNHPL wifi; runs dam-agent",
        },
        "hardware": {
            "lens_type": "CS-mount (lens unspecified)",
            "note": "RPi HQ camera (IMX477), Bookworm",
        },
    },
    {
        "device_id": "dam-imx477-1",
        "location_id": "JAYANG1",
        "access": {
            "ssh_accessible": True,
            "ip": "192.168.70.107",
            "ssh_user": "cskim",
            "note": "bench, KNHPL wifi; Trixie; no agent yet",
        },
        "hardware": {
            "lens_type": "CS-mount (lens unspecified)",
            "note": "RPi HQ camera (IMX477), Trixie",
        },
    },
    {
        "device_id": "dam-imx462",
        "location_id": "JAYANGN",
        "access": {
            "ssh_accessible": True,
            "ip": "192.168.70.106",
            "ssh_user": "cskim",
            "note": "bench, KNHPL wifi; Arducam camera; no agent yet",
        },
        "hardware": {
            "lens_type": "M16 wide (factory)",
            "note": "Arducam Pivariety IMX462 (UC-955), Bookworm; "
                    "libcamera is Arducam's build - do not apt-upgrade blindly",
        },
    },
    {
        "device_id": "dam-imx477-3",
        "location_id": "JAYANG3",
        "access": {
            "ssh_accessible": True,
            "ip": "192.168.70.102",
            "ssh_user": "cskim",
            "note": "bench, KNHPL wifi (cable removed); "
                    "IMX477 detected; no agent yet",
        },
        "hardware": {
            "lens_type": "CS-mount (lens unspecified)",
            "note": "RPi HQ camera (IMX477), Bookworm",
        },
    },
]

session = boto3.Session(profile_name=PROFILE, region_name=REGION)
agents = session.resource("dynamodb").Table("knh-dam-agents")
devices = session.resource("dynamodb").Table("knh-dam-devices")


def upsert_device(spec: dict, now: str) -> None:
    expression = (
        "SET #c = if_not_exists(#c, :control), "
        "#a = :access, hardware = :hw, "
        "first_seen = if_not_exists(first_seen, :now), "
    )
    values = {
        ":control": {
            "capturing": True,
            "video_window_start": "00:00",
            "video_window_end": "00:00",
        },
        ":access": spec["access"],
        ":hw": spec["hardware"],
        ":now": now,
    }
    if spec["location_id"] is not None:
        expression += "assignment = :assign"
        values[":assign"] = {
            "location_id": spec["location_id"], "assigned_at": now
        }
    else:
        expression += "assignment = if_not_exists(assignment, :assign)"
        values[":assign"] = {"location_id": None, "assigned_at": None}
    agents.update_item(
        Key={"device_id": spec["device_id"]},
        UpdateExpression=expression,
        ExpressionAttributeNames={"#c": "control", "#a": "access"},
        ExpressionAttributeValues=values,
    )
    print(f"[ok] {spec['device_id']} -> location={spec['location_id']}")


def main() -> None:
    now = datetime.now(UTC).isoformat()
    for spec in BENCH_DEVICES:
        upsert_device(spec, now)

    stamped = 0
    for item in devices.scan(
        ProjectionExpression="token_hash, location_id, device_id"
    ).get("Items", []):
        if item.get("device_id"):
            continue
        if item.get("location_id") == "TEST":  # legacy bench token
            devices.update_item(
                Key={"token_hash": item["token_hash"]},
                UpdateExpression="SET device_id = :d",
                ExpressionAttributeValues={":d": "dam-imx477-2"},
            )
            stamped += 1
    print(f"[ok] token rows stamped with device_id: {stamped}")
    print("DONE")


if __name__ == "__main__":
    main()
