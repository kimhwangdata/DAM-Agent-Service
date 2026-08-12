# ADR-0003 — Device upload auth: presigned URLs via upload-signer

- **Status**: Accepted
- **Date**: 2026-08-13
- **Deciders**: cskim
- **Blocks**: Phase 1 uploader; supersedes the per-device IAM user plan in
  Phase 0.5

## Context

The original plan gave each device an IAM user whose access key lives in the
Pi's `.env` file. Anyone who compromises or steals a Pi can read that file —
and an AWS secret access key is a long-lived cloud credential that stays
valid until manually revoked in IAM. The blast radius was already limited
(PutObject on the device's own `images/{location_id}/` prefix, no read, no
list, no delete), but the credential class is wrong: real AWS keys should
not sit on field devices at all.

Reality check: a device that can upload must hold *some* secret; an attacker
on the device can always do whatever the device can do. The goal is to
minimize what the stolen secret is worth and make revocation instant.

## Decision drivers

- no AWS credentials on any device, ever
- instant, per-device revocation without touching IAM
- stolen secret's worst case ≈ junk uploads to one prefix, rate-limited
- server-side chokepoint that can also enforce key shape / quotas /
  moderation later (architecture §6 layers)
- agent simplicity (ideally fewer device-side dependencies, not more)

## Options

### Option A — static per-device IAM access keys (original plan)

- `+` no extra infrastructure
- `−` long-lived AWS credential readable in `.env` on the device
- `−` revocation/rotation = IAM operations per device
- `−` no central enforcement point for key shape/quotas

### Option B — presigned URLs from an upload-signer Lambda

Device holds a random per-device **token** (not an AWS credential). A small
Lambda (function URL) checks the token against a `knh-dam-devices` DynamoDB
table (`location_id`, `token_hash`, `enabled`), validates/derives the exact
S3 key server-side, and returns a presigned `PUT` URL (~60 s TTL). Only the
Lambda's execution role can write to S3.

- `+` zero AWS credentials on devices; S3 write permission exists only in
  one Lambda role
- `+` signer derives `location_id` **from the token** — a stolen token can
  never touch another device's prefix
- `+` kill-switch = `enabled=false` in the table; app-level, instant
- `+` key-shape validation now; natural home for quotas/moderation later
- `+` device uploads via plain HTTPS — **boto3 drops off the device**
- `−` one new small piece of infrastructure to build and deploy
- `−` upload needs 2 HTTPS calls per frame (sign + PUT) — trivial at 48 s
  cadence
- `−` a secret (the token) still lives on the device — unavoidable, but it
  is app-scoped, rate-limitable, and worthless against AWS APIs

### Option C — AWS IoT Core credentials provider (X.509 → temporary STS)

- `+` AWS-native device identity, auto-rotating short-lived credentials
- `−` heaviest setup (things, certs, role aliases); still a per-device cert
  file on disk; no app-level chokepoint for key shape/quotas
- `−` overkill for this fleet size and adds a whole service to learn

## Open questions

- Presigned `POST` (supports `content-length-range`) vs `PUT` (simpler) —
  start with PUT; revisit if size capping at the S3 layer becomes required.

## Decision

**Option B.** The signer is a single small Python Lambda with a function
URL, a `knh-dam-devices` DynamoDB table, and an execution role scoped to
`s3:PutObject` on `knh-dam-store/images/*`. Devices keep only
`UPLOAD_SIGNER_URL` and `DEVICE_TOKEN` in `.env.{STAGE}`.

## Consequences

- Phase 0.5's "create per-device IAM user" item is **cancelled** (it was
  also blocked on `iam:CreateUser` permissions — moot now).
- New component: `upload-signer/` in this repo; built in Phase 1 before the
  agent uploader (which becomes stdlib-HTTP, no boto3 on the Pi).
- `.env.example` loses AWS credential fields, gains `UPLOAD_SIGNER_URL` +
  `DEVICE_TOKEN`.
- Token issuance: an operator script hashes a generated token into the
  device table; the plaintext token goes onto the Pi once (same handling as
  any stage env value).
- Architecture §6 layer 1 (key-shape validation) is effectively implemented
  by the signer from day one; quotas (layer 2) get an obvious home.

## Next

- Design detail in `01-agent.md` §5 (updated) and implement per the revised
  Phase 1 plan.
