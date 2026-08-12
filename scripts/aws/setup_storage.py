"""One-time (idempotent) storage setup for days-in-a-minute — plan 0.5.

Creates/configures per docs/design/00-architecture.md section 7 and ADR-0002:
  - buckets knh-dam-store / knh-dam-backup (private, BPA, SSE-S3, versioning)
  - replication images/ -> knh-dam-backup with GLACIER destination class
  - lifecycle on knh-dam-store images/: expire after 30 days, clean up
    noncurrent versions (1 day) and expired delete markers

Run:  python scripts/aws/setup_storage.py   (uses AWS profile knh-dev)
"""

from __future__ import annotations

import json
import sys

import boto3
from botocore.exceptions import ClientError

PROFILE = "knh-dev"
REGION = "ap-northeast-2"
STORE = "knh-dam-store"
BACKUP = "knh-dam-backup"
IMAGES_PREFIX = "images/"
RETENTION_DAYS = 30
REPLICATION_ROLE = "knh-dam-replication"

session = boto3.Session(profile_name=PROFILE, region_name=REGION)
s3 = session.client("s3")
iam = session.client("iam")


def ensure_bucket(name: str) -> None:
    try:
        s3.create_bucket(
            Bucket=name,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
        print(f"[created] bucket {name}")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            if code == "BucketAlreadyExists":
                print(f"[FATAL] bucket name {name} is taken by another account")
                sys.exit(1)
            print(f"[ok] bucket {name} already exists")
        else:
            raise
    s3.put_public_access_block(
        Bucket=name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_encryption(
        Bucket=name,
        ServerSideEncryptionConfiguration={
            "Rules": [
                {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
            ]
        },
    )
    s3.put_bucket_versioning(
        Bucket=name, VersioningConfiguration={"Status": "Enabled"}
    )
    print(f"[ok] {name}: BPA on, SSE-S3, versioning enabled")


def ensure_replication_role() -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "s3.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetReplicationConfiguration", "s3:ListBucket"],
                "Resource": f"arn:aws:s3:::{STORE}",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "s3:GetObjectVersionForReplication",
                    "s3:GetObjectVersionAcl",
                    "s3:GetObjectVersionTagging",
                ],
                "Resource": f"arn:aws:s3:::{STORE}/{IMAGES_PREFIX}*",
            },
            {
                "Effect": "Allow",
                "Action": ["s3:ReplicateObject", "s3:ReplicateTags"],
                "Resource": f"arn:aws:s3:::{BACKUP}/{IMAGES_PREFIX}*",
            },
        ],
    }
    try:
        role = iam.create_role(
            RoleName=REPLICATION_ROLE,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="S3 replication knh-dam-store/images -> knh-dam-backup",
        )
        print(f"[created] role {REPLICATION_ROLE}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
        role = iam.get_role(RoleName=REPLICATION_ROLE)
        print(f"[ok] role {REPLICATION_ROLE} already exists")
    iam.put_role_policy(
        RoleName=REPLICATION_ROLE,
        PolicyName="replicate-images",
        PolicyDocument=json.dumps(policy),
    )
    return role["Role"]["Arn"]


def ensure_replication(role_arn: str) -> None:
    s3.put_bucket_replication(
        Bucket=STORE,
        ReplicationConfiguration={
            "Role": role_arn,
            "Rules": [
                {
                    "ID": "images-to-backup-glacier",
                    "Priority": 1,
                    "Status": "Enabled",
                    "Filter": {"Prefix": IMAGES_PREFIX},
                    "DeleteMarkerReplication": {"Status": "Disabled"},
                    "Destination": {
                        "Bucket": f"arn:aws:s3:::{BACKUP}",
                        "StorageClass": "GLACIER",
                    },
                }
            ],
        },
    )
    print(f"[ok] replication {STORE}/{IMAGES_PREFIX} -> {BACKUP} (GLACIER)")


def ensure_lifecycle() -> None:
    s3.put_bucket_lifecycle_configuration(
        Bucket=STORE,
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": f"expire-images-{RETENTION_DAYS}d",
                    "Status": "Enabled",
                    "Filter": {"Prefix": IMAGES_PREFIX},
                    "Expiration": {"Days": RETENTION_DAYS},
                },
                {
                    "ID": "cleanup-noncurrent-and-markers",
                    "Status": "Enabled",
                    "Filter": {"Prefix": IMAGES_PREFIX},
                    "NoncurrentVersionExpiration": {"NoncurrentDays": 1},
                    "Expiration": {"ExpiredObjectDeleteMarker": True},
                },
            ]
        },
    )
    print(f"[ok] lifecycle on {STORE}/{IMAGES_PREFIX}: "
          f"expire {RETENTION_DAYS}d + noncurrent/marker cleanup")


def main() -> None:
    print(f"account: {session.client('sts').get_caller_identity()['Account']}")
    ensure_bucket(STORE)
    ensure_bucket(BACKUP)
    role_arn = ensure_replication_role()
    ensure_replication(role_arn)
    ensure_lifecycle()
    print("DONE")


if __name__ == "__main__":
    main()
