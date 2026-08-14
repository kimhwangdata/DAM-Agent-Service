"""Deploy the video-builder Lambda (idempotent) — plan 3.4, design 03 §6.

Creates/updates: execution role, the Lambda (handler.py + cycles.py zip,
ffmpeg layer attached, 3008 MB / 900 s / 2048 MB /tmp), and the 15-minute
EventBridge dispatch rule.

Run:  python scripts/aws/deploy_video_builder.py   (profile knh-dev)
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
FUNCTION = "dam-video-builder"
ROLE = "dam-video-builder-role"
BOUNDARY_ARN = "arn:aws:iam::664751480155:policy/dam-boundary"
RULE = "dam-video-builder-sweep"
BUCKET = "knh-dam-store"
AGENTS_TABLE = "knh-dam-agents"
FFMPEG_LAYER = "arn:aws:lambda:ap-northeast-2:664751480155:layer:dam-ffmpeg:1"
RUNTIME = "python3.12"
SRC_DIR = Path(__file__).resolve().parents[2] / "video-builder"

session = boto3.Session(profile_name=PROFILE, region_name=REGION)
iam = session.client("iam")
lam = session.client("lambda")
events = session.client("events")
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
                "Action": "s3:ListBucket",
                "Resource": f"arn:aws:s3:::{BUCKET}",
            },
            {
                "Effect": "Allow",
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{BUCKET}/images/*",
            },
            {
                "Effect": "Allow",
                "Action": "s3:PutObject",
                "Resource": f"arn:aws:s3:::{BUCKET}/videos/*",
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
            {  # dispatch -> build self-invocation
                "Effect": "Allow",
                "Action": "lambda:InvokeFunction",
                "Resource": f"arn:aws:lambda:{REGION}:*:function:{FUNCTION}",
            },
        ],
    }
    iam.put_role_policy(
        RoleName=ROLE, PolicyName="builder-access", PolicyDocument=json.dumps(policy)
    )
    return role["Role"]["Arn"]


def zip_code() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(SRC_DIR / "handler.py", "handler.py")
        zf.write(SRC_DIR / "cycles.py", "cycles.py")
    return buffer.getvalue()


def ensure_function(role_arn: str) -> str:
    code = zip_code()
    config = {
        "Runtime": RUNTIME,
        "Role": role_arn,
        "Handler": "handler.lambda_handler",
        "Timeout": 900,
        "MemorySize": 3008,
        "EphemeralStorage": {"Size": 2048},
        "Layers": [FFMPEG_LAYER],
        "Environment": {
            "Variables": {
                "BUCKET": BUCKET,
                "AGENTS_TABLE": AGENTS_TABLE,
                "IMAGE_PREFIX": "images/",
                "VIDEO_PREFIX": "videos/",
                "DEFAULT_TIMEZONE": "Asia/Seoul",
                "MIN_BYTES": "10000",
            }
        },
    }
    try:
        fn = lam.create_function(
            FunctionName=FUNCTION, Code={"ZipFile": code}, **config
        )
        print(f"[created] lambda {FUNCTION}")
        arn = fn["FunctionArn"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceConflictException":
            raise
        lam.update_function_code(FunctionName=FUNCTION, ZipFile=code)
        lam.get_waiter("function_updated_v2").wait(FunctionName=FUNCTION)
        lam.update_function_configuration(
            FunctionName=FUNCTION,
            **{k: v for k, v in config.items() if k != "Runtime"},
        )
        arn = lam.get_function(FunctionName=FUNCTION)["Configuration"]["FunctionArn"]
        print(f"[ok] lambda {FUNCTION} code+config updated")
    lam.get_waiter("function_active_v2").wait(FunctionName=FUNCTION)
    return arn


def ensure_sweep_rule(function_arn: str) -> None:
    events.put_rule(
        Name=RULE,
        ScheduleExpression="rate(15 minutes)",
        State="ENABLED",
        Description="video-builder dispatch sweep (ADR-0004)",
    )
    events.put_targets(
        Rule=RULE,
        Targets=[
            {
                "Id": "builder-dispatch",
                "Arn": function_arn,
                "Input": json.dumps({"mode": "dispatch"}),
            }
        ],
    )
    account = sts.get_caller_identity()["Account"]
    try:
        lam.add_permission(
            FunctionName=FUNCTION,
            StatementId="events-sweep-invoke",
            Action="lambda:InvokeFunction",
            Principal="events.amazonaws.com",
            SourceArn=f"arn:aws:events:{REGION}:{account}:rule/{RULE}",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceConflictException":
            raise
    print(f"[ok] rule {RULE}: rate(15 minutes) -> dispatch")


def main() -> None:
    role_arn = ensure_role()
    function_arn = ensure_function(role_arn)
    ensure_sweep_rule(function_arn)
    print("DONE")


if __name__ == "__main__":
    main()
