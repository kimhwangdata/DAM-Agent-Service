# Raspberry Pi Agent — AWS Security Reference

How the capture fleet talks to AWS without ever holding AWS credentials,
and the layered controls around that path. Sources of truth:
`docs/design/adr/ADR-0003-device-upload-auth-presigned.md`,
`docs/design/02-agent-manager.md`, and the CLAUDE.md security must-follows;
this page is the operational summary.

## 1. Core principle: no AWS credentials on devices (ADR-0003)

A Raspberry Pi in the field is the least trusted machine in the system —
physically accessible, often behind someone else's NAT, and its SD card can
be cloned. Therefore:

- **Devices hold no AWS access keys, no IAM identity, no `~/.aws`, and no
  boto3.** Nothing on the device is useful against AWS APIs.
- The only secret on a device is an **application token** — an opaque
  value that our own upload-signer Lambda understands. It is revocable
  server-side in seconds and worthless anywhere else.
- All AWS identities live in **Lambda execution roles** (§4), never in
  code, packages, or env files.

## 2. The upload path

```
Pi agent ──(HTTPS, app token)──> upload-signer Lambda ──> presigned PUT URL
   │                                   │ token hash lookup (knh-dam-devices)
   │                                   │ control gate     (knh-dam-agents)
   └──(HTTPS PUT, ≤ 60 s)──> s3://knh-dam-store/images/{location}/...
```

Every capture cycle the agent calls the signer (API Gateway HTTPS) with its
token. The signer:

1. **Authenticates** by SHA-256 hash lookup — the table stores only token
   hashes, never plaintext (§3).
2. **Authorizes** against the fleet control plane: a `paused` device gets a
   no-op response, an `unassigned` device gets 409 — in both cases the
   agent skips the upload without retrying. The same call doubles as the
   device heartbeat (reported fields pass a whitelist before touching the
   table).
3. **Constrains** the presigned URL it returns:
   - key is built **server-side** from the device's assignment —
     a device can never choose its own prefix or overwrite another
     location's data;
   - filename must match `^\d{9}\.jpg$` and the date must be a valid
     `YYYY-MM-DD` — no path traversal, no arbitrary names;
   - content type is locked to `image/jpeg`;
   - the URL expires in **60 seconds** and is valid for exactly one key.

A stolen token therefore yields, at most, the ability to upload JPEG
frames into that one device's currently assigned image prefix — until the
token is revoked.

## 3. Token lifecycle

- **Issue**: `scripts/aws/issue_device_token.py` (or the webapp's rotate
  dialog) generates a random token (`secrets.token_urlsafe(32)`), stores
  only its SHA-256 hash in `knh-dam-devices`, and the plaintext exists
  exactly once — in the device's `/opt/dam-agent/.env.{stage}` (mode 600).
- **Transit**: plaintext moves only over SSH, piped directly into the
  device env file. It is never displayed in terminals, logged, or stored
  on the dev machine.
- **Rotation**: issuing a new token replaces the hash — the old token
  stops working immediately.
- **Kill switch**: setting `enabled = false` on the hash row disables the
  device without touching it.
- The agent **never logs** its token; log lines carry capture ids, S3
  keys, and attempt counts only.

## 4. AWS identities and least privilege

Each cloud component has its own execution role, scoped to exactly its
job. No wildcard policies, no long-lived user keys anywhere in the
pipeline.

| Identity (Lambda role)  | Allowed                                                    |
| ----------------------- | ---------------------------------------------------------- |
| upload-signer           | presign `PutObject` on `knh-dam-store/images/*`; R/W on the two fleet tables |
| upload-monitor          | read + tag new `images/*` objects; update `knh-dam-agents` health |
| video-builder           | `GetObject` on `images/*`, `PutObject` on `videos/*` only, R/W `knh-dam-agents`, invoke itself |
| webapp (OpenNext)       | read-only `GetObject`/`ListBucket` on the pool and image store; scoped DynamoDB CRUD |

The buckets (`knh-dam-store`, `knh-dam-backup`) are **private**; nothing
is world-readable. All viewing goes through short-lived presigned GET
URLs issued server-side after an RBAC check in the webapp.

## 5. Server-side validation of what devices send

The device is not trusted to upload good data. The upload-monitor Lambda
inspects every object that lands under `images/`:

- size must be within 10 KB – 5 MB;
- JPEG magic bytes (SOI/EOI) are verified with ranged GETs;
- failures are tagged `damaged=true` and counted into the device's
  `damaged_recent` health window (feeding the suspect/quarantine states).

The video builder independently drops sub-10 KB objects at listing time
and re-validates JPEG magic before encoding, so damaged or hostile
uploads never reach a published video.

## 6. Device and repo hygiene

- **SSH**: key-only auth (`cskim`, ed25519); password SSH is disabled.
  Deploy scripts never embed passwords.
- **sudo surface**: the agent's only privileged action is the thermal
  last-resort `poweroff`, granted via a single-command sudoers rule
  (`/etc/sudoers.d/dam-agent-poweroff`).
- **No local image storage**: frames live only in the bounded in-memory
  queue — a seized SD card contains no captured scenery, only the app
  token (revocable) and config.
- **Env files**: real `.env.{stage}` files are gitignored on the dev
  machine and mode-600 on devices; only `.env.example` with placeholders
  is committed. Never commit or print `.env*` contents.
- **No secrets in the repo**: no real AWS keys, tokens, or credentials in
  code, tests, fixtures, or docs — placeholders only.
- **Captured images are sensitive** (they may show private spaces): they
  are never copied into the repo, tests, or docs; retention is 30 days in
  `knh-dam-store`, then Glacier-class archive in `knh-dam-backup`.

## 7. What compromise of each piece costs

| Compromised          | Blast radius                                                | Recovery                       |
| -------------------- | ----------------------------------------------------------- | ------------------------------ |
| Device / SD card     | its app token; can upload JPEGs to its own assigned prefix  | revoke token (delete hash row) |
| App token in transit | same as above                                               | rotate token                   |
| Signer URL flooding  | rate of presigns for valid tokens only; invalid tokens get 401 | disable device rows            |
| Dev machine repo     | no credentials present (profile `knh-dev` lives in AWS config, outside the repo) | rotate profile keys            |

The design goal behind all of it: **a device can be lost, cloned, or
hostile without endangering the bucket, the fleet, or any AWS account
resource beyond its own narrow upload slot.**
