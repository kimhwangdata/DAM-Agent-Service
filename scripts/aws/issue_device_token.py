"""Issue a device upload token — plan 1.4, ADR-0003.

Generates a random token, stores its SHA-256 hash in knh-dam-devices, and
prints the plaintext token ONCE (put it in the device's .env.{STAGE} as
DEVICE_TOKEN; it is never stored anywhere else). Re-running for the same
location replaces the old token (old one stops working).

Run:  python scripts/aws/issue_device_token.py LOCATION_ID
Disable a device (kill-switch):
      python scripts/aws/issue_device_token.py LOCATION_ID --disable
"""

from __future__ import annotations

import hashlib
import secrets
import sys
from datetime import UTC, datetime

import boto3

PROFILE = "knh-dev"
REGION = "ap-northeast-2"
TABLE = "knh-dam-devices"


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: issue_device_token.py LOCATION_ID [--disable]")
    location_id = sys.argv[1]
    disable = "--disable" in sys.argv[2:]

    table = boto3.Session(profile_name=PROFILE, region_name=REGION).resource(
        "dynamodb"
    ).Table(TABLE)

    # One token per location: drop any existing rows for this location first.
    existing = table.scan(
        ProjectionExpression="token_hash, location_id"
    ).get("Items", [])
    for item in existing:
        if item.get("location_id") == location_id:
            if disable:
                table.update_item(
                    Key={"token_hash": item["token_hash"]},
                    UpdateExpression="SET enabled = :f",
                    ExpressionAttributeValues={":f": False},
                )
                print(f"[ok] {location_id} disabled (kill-switch)")
                return
            table.delete_item(Key={"token_hash": item["token_hash"]})
            print(f"[ok] old token for {location_id} revoked")

    if disable:
        sys.exit(f"no token found for {location_id} to disable")

    token = secrets.token_urlsafe(32)
    table.put_item(
        Item={
            "token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "location_id": location_id,
            "enabled": True,
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    print(f"[ok] token issued for {location_id} - copy into the device env NOW:")
    print(f"DEVICE_TOKEN={token}")


if __name__ == "__main__":
    main()
