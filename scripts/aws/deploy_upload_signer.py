"""Deploy the upload-signer Lambda (idempotent) — plan 1.4, ADR-0003.

Creates/updates: knh-dam-devices DynamoDB table, execution role
(logs + PutObject on knh-dam-store/images/* + GetItem on the table),
the Lambda from upload-signer/handler.py, and a public function URL.

Run:  python scripts/aws/deploy_upload_signer.py   (profile knh-dev)
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

PROFILE = "knh-dev"
REGION = "ap-northeast-2"
TABLE = "knh-dam-devices"
AGENTS_TABLE = "knh-dam-agents"
FUNCTION = "dam-upload-signer"
ROLE = "dam-upload-signer-role"
BUCKET = "knh-dam-store"
RUNTIME = "python3.12"
HANDLER_FILE = Path(__file__).resolve().parents[2] / "upload-signer" / "handler.py"

session = boto3.Session(profile_name=PROFILE, region_name=REGION)
ddb = session.client("dynamodb")
iam = session.client("iam")
lam = session.client("lambda")


def ensure_table(name: str, pk: str) -> None:
    try:
        ddb.create_table(
            TableName=name,
            AttributeDefinitions=[{"AttributeName": pk, "AttributeType": "S"}],
            KeySchema=[{"AttributeName": pk, "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )
        print(f"[created] table {name}")
        ddb.get_waiter("table_exists").wait(TableName=name)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceInUseException":
            raise
        print(f"[ok] table {name} already exists")


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
            RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust)
        )
        print(f"[created] role {ROLE}")
        time.sleep(10)  # IAM propagation before Lambda uses the role
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
                "Action": "s3:PutObject",
                "Resource": f"arn:aws:s3:::{BUCKET}/images/*",
            },
            {
                "Effect": "Allow",
                "Action": "dynamodb:GetItem",
                "Resource": f"arn:aws:dynamodb:{REGION}:*:table/{TABLE}",
            },
            {
                "Effect": "Allow",
                "Action": ["dynamodb:GetItem", "dynamodb:UpdateItem"],
                "Resource": f"arn:aws:dynamodb:{REGION}:*:table/{AGENTS_TABLE}",
            },
        ],
    }
    iam.put_role_policy(
        RoleName=ROLE, PolicyName="signer-access", PolicyDocument=json.dumps(policy)
    )
    return role["Role"]["Arn"]


def zip_handler() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(HANDLER_FILE, "handler.py")
    return buffer.getvalue()


def ensure_function(role_arn: str) -> None:
    code = zip_handler()
    env = {
        "Variables": {
            "BUCKET": BUCKET,
            "TABLE": TABLE,
            "AGENTS_TABLE": AGENTS_TABLE,
            "IMAGE_PREFIX": "images/",
            "URL_TTL_SECONDS": "60",
        }
    }
    try:
        lam.create_function(
            FunctionName=FUNCTION,
            Runtime=RUNTIME,
            Role=role_arn,
            Handler="handler.lambda_handler",
            Code={"ZipFile": code},
            Timeout=10,
            MemorySize=128,
            Environment=env,
        )
        print(f"[created] lambda {FUNCTION}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceConflictException":
            raise
        lam.update_function_code(FunctionName=FUNCTION, ZipFile=code)
        lam.get_waiter("function_updated_v2").wait(FunctionName=FUNCTION)
        lam.update_function_configuration(FunctionName=FUNCTION, Environment=env)
        print(f"[ok] lambda {FUNCTION} code+config updated")
    lam.get_waiter("function_active_v2").wait(FunctionName=FUNCTION)


def ensure_http_api(function_arn: str) -> str:
    """HTTP API (payload v2 — same event shape as a function URL).

    Note: a plain public function URL kept returning the edge-level 403
    'Function URL authorization issues' in this account despite a correct
    resource policy, so the signer fronts with API Gateway instead.
    """
    apigw = session.client("apigatewayv2")
    api_name = f"{FUNCTION}-api"
    api_id = None
    for api in apigw.get_apis(MaxResults="100").get("Items", []):
        if api["Name"] == api_name:
            api_id = api["ApiId"]
            print(f"[ok] api {api_name} already exists")
            break
    if api_id is None:
        api_id = apigw.create_api(
            Name=api_name, ProtocolType="HTTP", Target=function_arn
        )["ApiId"]
        print(f"[created] api {api_name}")
    account = session.client("sts").get_caller_identity()["Account"]
    try:
        lam.add_permission(
            FunctionName=FUNCTION,
            StatementId="apigw-invoke",
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=f"arn:aws:execute-api:{REGION}:{account}:{api_id}/*",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceConflictException":
            raise
    return f"https://{api_id}.execute-api.{REGION}.amazonaws.com"


def cleanup_function_url() -> None:
    """Remove the abandoned public function URL config, if present."""
    for call in (
        lambda: lam.delete_function_url_config(FunctionName=FUNCTION),
        lambda: lam.remove_permission(
            FunctionName=FUNCTION, StatementId="public-url-invoke"
        ),
    ):
        try:
            call()
        except ClientError:
            pass


def main() -> None:
    ensure_table(TABLE, "token_hash")
    ensure_table(AGENTS_TABLE, "device_id")
    role_arn = ensure_role()
    ensure_function(role_arn)
    cleanup_function_url()
    function_arn = lam.get_function(FunctionName=FUNCTION)["Configuration"][
        "FunctionArn"
    ]
    url = ensure_http_api(function_arn)
    print(f"UPLOAD_SIGNER_URL={url}")
    print("DONE")


if __name__ == "__main__":
    main()
