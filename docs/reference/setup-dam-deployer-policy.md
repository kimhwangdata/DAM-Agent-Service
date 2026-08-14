# dam-deployer — Scoped Deploy Role for this Repo

A per-project deployer identity that confines the dev machine's blast
radius to this project's resources (`dam-*` Lambdas/roles/rules,
`knh-dam-*` tables/buckets). It replaces direct use of the broad
`knh-dev` user for everything this repo's `scripts/aws/*` do; the user
remains only the login identity that assumes the role.

Everything here is dev-stage, account `664751480155`, region
`ap-northeast-2`. The webapp repo keeps its own deploy identity (SST
touches CloudFront and other non-dam resources).

## Shape

```
IAM user csk-homepage-dev ──sts:AssumeRole──> role dam-deployer
                                                 │ inline policy: dam-deploy (below)
                                                 └ can create runtime roles ONLY when
                                                   they carry the dam-boundary policy
```

Two documents matter:

1. **`dam-boundary`** (managed policy) — a **permissions boundary** that
   caps every runtime role this deployer creates. Without it,
   `iam:CreateRole` + `iam:PutRolePolicy` would be a privilege-escalation
   path (create a role, grant it admin, assume it).
2. **`dam-deploy`** (role inline policy) — what the deployer itself may
   do, scoped by resource-name prefix.

## 1. Trust policy (who can assume `dam-deployer`)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "AWS": "arn:aws:iam::664751480155:user/csk-homepage-dev" },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

## 2. `dam-boundary` — permissions boundary for runtime roles

The ceiling for every Lambda execution role in this project (signer,
monitor, video-builder). A runtime role can never exceed this, no matter
what inline policy the deployer writes onto it.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "Logs",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:ap-northeast-2:664751480155:*"
    },
    {
      "Sid": "ProjectBuckets",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:GetObjectTagging",
        "s3:PutObjectTagging",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::knh-dam-store",
        "arn:aws:s3:::knh-dam-store/*",
        "arn:aws:s3:::knh-dam-backup",
        "arn:aws:s3:::knh-dam-backup/*"
      ]
    },
    {
      "Sid": "ProjectTables",
      "Effect": "Allow",
      "Action": ["dynamodb:*"],
      "Resource": [
        "arn:aws:dynamodb:ap-northeast-2:664751480155:table/knh-dam-*",
        "arn:aws:dynamodb:ap-northeast-2:664751480155:table/knh-dam-*/index/*"
      ]
    },
    {
      "Sid": "SelfInvoke",
      "Effect": "Allow",
      "Action": "lambda:InvokeFunction",
      "Resource": "arn:aws:lambda:ap-northeast-2:664751480155:function:dam-*"
    }
  ]
}
```

## 3. `dam-deploy` — the deployer role's own policy

Covers everything `scripts/aws/*.py` and `scripts/deploy.sh`-side ops
actually do: Lambda + layer lifecycle, EventBridge sweep rule, runtime
role management (boundary-enforced), project tables/buckets, CloudWatch
log reading, API Gateway for the signer endpoint.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "LambdaAdminProjectFunctions",
      "Effect": "Allow",
      "Action": ["lambda:*"],
      "Resource": [
        "arn:aws:lambda:ap-northeast-2:664751480155:function:dam-*",
        "arn:aws:lambda:ap-northeast-2:664751480155:layer:dam-*",
        "arn:aws:lambda:ap-northeast-2:664751480155:layer:dam-*:*"
      ]
    },
    {
      "Sid": "RuntimeRolesOnlyWithBoundary",
      "Effect": "Allow",
      "Action": ["iam:CreateRole"],
      "Resource": "arn:aws:iam::664751480155:role/dam-*",
      "Condition": {
        "StringEquals": {
          "iam:PermissionsBoundary": "arn:aws:iam::664751480155:policy/dam-boundary"
        }
      }
    },
    {
      "Sid": "RuntimeRoleManagement",
      "Effect": "Allow",
      "Action": [
        "iam:GetRole",
        "iam:PutRolePolicy",
        "iam:DeleteRolePolicy",
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
        "iam:TagRole",
        "iam:ListRolePolicies",
        "iam:ListAttachedRolePolicies"
      ],
      "Resource": "arn:aws:iam::664751480155:role/dam-*"
    },
    {
      "Sid": "PassRuntimeRolesToLambda",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::664751480155:role/dam-*",
      "Condition": {
        "StringEquals": { "iam:PassedToService": "lambda.amazonaws.com" }
      }
    },
    {
      "Sid": "SweepRule",
      "Effect": "Allow",
      "Action": [
        "events:PutRule",
        "events:PutTargets",
        "events:RemoveTargets",
        "events:DeleteRule",
        "events:DescribeRule"
      ],
      "Resource": "arn:aws:events:ap-northeast-2:664751480155:rule/dam-*"
    },
    {
      "Sid": "ProjectTables",
      "Effect": "Allow",
      "Action": ["dynamodb:*"],
      "Resource": [
        "arn:aws:dynamodb:ap-northeast-2:664751480155:table/knh-dam-*",
        "arn:aws:dynamodb:ap-northeast-2:664751480155:table/knh-dam-*/index/*"
      ]
    },
    {
      "Sid": "ProjectBuckets",
      "Effect": "Allow",
      "Action": ["s3:*"],
      "Resource": [
        "arn:aws:s3:::knh-dam-store",
        "arn:aws:s3:::knh-dam-store/*",
        "arn:aws:s3:::knh-dam-backup",
        "arn:aws:s3:::knh-dam-backup/*"
      ]
    },
    {
      "Sid": "ReadLambdaLogs",
      "Effect": "Allow",
      "Action": [
        "logs:FilterLogEvents",
        "logs:GetLogEvents",
        "logs:DescribeLogGroups",
        "logs:DescribeLogStreams"
      ],
      "Resource": "arn:aws:logs:ap-northeast-2:664751480155:log-group:/aws/lambda/dam-*"
    },
    {
      "Sid": "SignerHttpApi",
      "Effect": "Allow",
      "Action": ["apigateway:GET", "apigateway:POST", "apigateway:PUT",
                 "apigateway:PATCH", "apigateway:DELETE"],
      "Resource": "arn:aws:apigateway:ap-northeast-2::/apis*"
    }
  ]
}
```

Notes on the deliberate rough edges:

- **API Gateway cannot be name-scoped** — its ARNs carry API ids, not
  names, so `SignerHttpApi` covers all HTTP APIs in the region. Accepted:
  this account's only HTTP API is the signer's. Tag-based conditions are
  the tighter alternative if that changes.
- **`s3:*` on the two project buckets** includes bucket-level config
  (lifecycle, replication, notifications) that `setup_storage.py` and
  `deploy_upload_monitor.py` manage. It does NOT reach any other bucket —
  the legacy `csk-allsky` copy was a one-off already done and is
  intentionally not granted.
- Runtime roles keep their existing `AWSLambdaBasicExecutionRole`
  managed-policy attachment; `AttachRolePolicy` on `role/dam-*` is safe
  because the boundary caps whatever gets attached.

> **Status (2026-08-14): ACTIVE.** `dam-boundary` + `dam-deploy`
> (managed) + role `dam-deployer` were created via the admin console;
> `~/.aws/config` has the profile; all `scripts/aws/*.py` use
> `PROFILE = "dam-deployer"` and pass `PermissionsBoundary` on
> `create_role`. Verified: full idempotent video-builder redeploy under
> the role, and denials on out-of-scope probes (foreign bucket, webapp
> table, unbounded `iam:CreateRole`, unscoped `lambda:ListFunctions`).
> `dam-boundary` is attached as the permissions boundary on all three
> runtime roles (set via the admin console 2026-08-14 — neither
> `knh-dev` nor `dam-deploy` holds `iam:PutRolePermissionsBoundary`, by
> design); fleet uploads verified flowing under the bounded roles.
> Setup is fully complete — §4 below is kept for rebuild-from-scratch.

## 4. One-time setup

Creating the role itself needs IAM permissions `knh-dev` may not have
(`iam:CreatePolicy`, `iam:CreateRole` outside `dam-*`). If a script run
fails with `AccessDenied`, do this once from the account's admin console:

1. Create managed policy `dam-boundary` (§2).
2. Create role `dam-deployer` with the trust policy (§1) and inline
   policy `dam-deploy` (§3).
3. On the dev machine, add to `~/.aws/config` (no new keys — the role is
   assumed with the existing user's credentials):

   ```ini
   [profile dam-deployer]
   role_arn = arn:aws:iam::664751480155:role/dam-deployer
   source_profile = knh-dev
   region = ap-northeast-2
   ```

4. Switch this repo's scripts: `PROFILE = "dam-deployer"` in
   `scripts/aws/*.py`.
5. Redeploy one Lambda as a smoke test; on the first redeploy of each
   runtime role, add `PermissionsBoundary` to the `create_role` call in
   the deploy scripts (existing roles need a one-time
   `iam:PutRolePermissionsBoundary` from the admin, or recreate them).

## 5. What this buys

A leaked `dam-deployer` session can modify **only** `dam-*` Lambdas,
rules, and roles (capped by the boundary) and `knh-dam-*` data. It cannot
touch the homepage's resources, other buckets/tables, create users, or
mint an admin role. The Pi agents are unaffected either way — they hold
no AWS credentials at all (see `rpi-agent-security.md`).
