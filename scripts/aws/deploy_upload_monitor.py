"""Deploy the upload-monitor Lambda (idempotent) — plan 2.2, design 02 §5.1.

Creates/updates: execution role (logs + ranged reads/tagging on
knh-dam-store/images/* + agents-table access), the Lambda from
upload-monitor/handler.py, the S3-invoke permission, and the bucket
ObjectCreated notification for images/*.jpg.

NOTE: put_bucket_notification_configuration REPLACES the bucket's whole
notification config — this script owns it (nothing else subscribes today).

Run:  python scripts/aws/deploy_upload_monitor.py   (profile knh-dev)
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

# Scoped deploy role - docs/reference/setup-dam-deployer-policy.md
PROFILE = "dam-deployer"
REGION = "ap-northeast-2"
AGENTS_TABLE = "knh-dam-agents"
FUNCTION = "dam-upload-monitor"
ROLE = "dam-upload-monitor-role"
BOUNDARY_ARN = "arn:aws:iam::664751480155:policy/dam-boundary"
BUCKET = "knh-dam-store"
RUNTIME = "python3.12"
HANDLER_FILE = Path(__file__).resolve().parents[2] / "upload-monitor" / "handler.py"

session = boto3.Session(profile_name=PROFILE, region_name=REGION)
iam = session.client("iam")
lam = session.client("lambda")
s3 = session.client("s3")
sts = session.client("sts")


def ensure_role() -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    try:
        role = iam.create_role(
            RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust),
            PermissionsBoundary=BOUNDARY_ARN,
        )
        print(f"[created] role {ROLE}")
        time.sleep(10)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
        role = iam.get_role(RoleName=ROLE)
        print(f"[ok] role {ROLE} already exists")
    iam.attach_role_policy(
        RoleName=ROLE,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObjectTagging"],
                "Resource": f"arn:aws:s3:::{BUCKET}/images/*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "dynamodb:Scan",
                    "dynamodb:GetItem",
                    "dynamodb:UpdateItem",
                ],
                "Resource": f"arn:aws:dynamodb:{REGION}:*:table/{AGENTS_TABLE}",
            },
        ],
    }
    iam.put_role_policy(
        RoleName=ROLE, PolicyName="monitor-access", PolicyDocument=json.dumps(policy)
    )
    return role["Role"]["Arn"]


def zip_handler() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(HANDLER_FILE, "handler.py")
    return buffer.getvalue()


def ensure_function(role_arn: str) -> str:
    code = zip_handler()
    env = {
        "Variables": {
            "AGENTS_TABLE": AGENTS_TABLE,
            "IMAGE_PREFIX": "images/",
            "MIN_BYTES": "10000",
            "MAX_BYTES": "5242880",
        }
    }
    try:
        fn = lam.create_function(
            FunctionName=FUNCTION,
            Runtime=RUNTIME,
            Role=role_arn,
            Handler="handler.lambda_handler",
            Code={"ZipFile": code},
            Timeout=30,
            MemorySize=128,
            Environment=env,
        )
        print(f"[created] lambda {FUNCTION}")
        arn = fn["FunctionArn"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceConflictException":
            raise
        lam.update_function_code(FunctionName=FUNCTION, ZipFile=code)
        lam.get_waiter("function_updated_v2").wait(FunctionName=FUNCTION)
        lam.update_function_configuration(FunctionName=FUNCTION, Environment=env)
        arn = lam.get_function(FunctionName=FUNCTION)["Configuration"]["FunctionArn"]
        print(f"[ok] lambda {FUNCTION} code+config updated")
    lam.get_waiter("function_active_v2").wait(FunctionName=FUNCTION)
    return arn


def ensure_s3_trigger(function_arn: str) -> None:
    account = sts.get_caller_identity()["Account"]
    try:
        lam.add_permission(
            FunctionName=FUNCTION,
            StatementId="s3-images-invoke",
            Action="lambda:InvokeFunction",
            Principal="s3.amazonaws.com",
            SourceArn=f"arn:aws:s3:::{BUCKET}",
            SourceAccount=account,
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceConflictException":
            raise
    s3.put_bucket_notification_configuration(
        Bucket=BUCKET,
        NotificationConfiguration={
            "LambdaFunctionConfigurations": [
                {
                    "Id": "images-to-upload-monitor",
                    "LambdaFunctionArn": function_arn,
                    "Events": ["s3:ObjectCreated:*"],
                    "Filter": {
                        "Key": {
                            "FilterRules": [
                                {"Name": "prefix", "Value": "images/"},
                                {"Name": "suffix", "Value": ".jpg"},
                            ]
                        }
                    },
                }
            ]
        },
    )
    print(f"[ok] s3://{BUCKET} images/*.jpg -> {FUNCTION}")


def main() -> None:
    role_arn = ensure_role()
    function_arn = ensure_function(role_arn)
    ensure_s3_trigger(function_arn)
    print("DONE")


if __name__ == "__main__":
    main()
