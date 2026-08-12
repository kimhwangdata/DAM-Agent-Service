# 00 — days-in-a-minute Service Architecture

- **Status**: Draft
- **Date**: 2026-08-13
- **Scope**: Overall architecture of the days-in-a-minute service across its
  three running components (capture agent, video builder, webapp) plus the
  planned upload-security layer (§6, design-only, **not implemented yet**).

## 1. Purpose

days-in-a-minute turns each day of a location's scenery into a **1-minute
time-lapse video**. Raspberry Pi camera devices ("Posts", one per location,
worldwide) capture still images all day; a cloud pipeline builds a daily video
per device; a web app lets viewers watch the videos grouped by location.

## 2. System overview

```
┌─────────────────────────────┐
│ Post (one per location)     │
│ Raspberry Pi + camera       │
│                             │
│  rpi-camera-agent (Part 1)  │      still images + metadata
│  - capture on schedule      │ ───────────────────────────────┐
│  - local queue + retry      │      (upload as captured)      │
└─────────────────────────────┘                                ▼
        many devices,                        ┌──────────────────────────────┐
        each in its own                      │ AWS S3: knh-dam-store        │
        IANA timezone                        │  images/{location_id}/       │
                                             │   {YYYY-MM-DD}/hhmmssfff.jpg │
                                             │  videos/                     │
                                             │   {LOCATION}-{Y-M-D}.mp4    │
┌─────────────────────────────┐              └──────────┬───────────────────┘
│ video-builder (Part 2)      │   read day's images     │        ▲
│ AWS Lambda + ffmpeg         │ ◄───────────────────────┘        │ 1-min mp4
│ - fires after each Post's   │                                  │
│   local midnight            │ ─────────────────────────────────┘
│ - 30 fps, libx264, yuv420p  │      write to video pool
└─────────────────────────────┘
                                             ┌──────────────────────────────┐
                                             │ webapp (Part 3)              │
                                             │ Next.js on Lambda/CloudFront │
                                             │ - browse Posts ("Locations") │
                                             │ - play videos via presigned  │
                                             │   URLs; manage Posts/Users   │
                                             └──────────────────────────────┘
```

Repositories:

| Part | Component | Repository |
| ---- | --------- | ---------- |
| 1 | `rpi-camera-agent` (capture agent) | this repo (`agent/`) |
| 2 | `video-builder` (AWS Lambda) | this repo (`video-builder/`) |
| 3 | `days-in-a-minute` webapp | `D:\home\repo-misc\days-in-a-minute` (own CLAUDE.md and design docs) |
| 4 | upload security & content filter | design-only for now (§6) |

## 3. Part 1 — Capture agent (Raspberry Pi)

A Python agent (`agent/`) runs on each Pi as a systemd service.

- **Capture**: still images on a configurable schedule. For a 1-minute video
  at 30 fps, a full day needs 1,800 frames → one capture every ~48 s.
- **Camera abstraction**: capture sits behind a small interface. The fleet
  already has two stacks — auto-detected native sensors (IMX477 HQ, the
  standard camera) and the Arducam Pivariety IMX462 (vendor libcamera build).
  See `docs/reference/camera-info.md`. The rest of the agent (scheduler,
  queue, uploader) is testable on Windows with a fake camera.
- **Upload**: each image goes to `knh-dam-store` with capture metadata
  (location/device id, capture time + timezone, camera settings). Keys:
  `images/{location_id}/{YYYY-MM-DD}/{hhmmssfff}.jpg` where date and time are
  **device-local** (`hhmmssfff` from `strftime("%H%M%S%f")` truncated to
  milliseconds) — one day-folder is exactly one video's input, sorted by
  filename. Capture IDs remain **ULIDs** (time-ordered) in the metadata.
- **Never lose a capture**: failed uploads go to a local disk queue and retry
  with backoff; the queue has a size/age cap with a logged eviction policy.
- **Identity**: `device_id` is a stable per-device config value. Hostname
  convention for bench devices: `dam-{sensor}[-{lens}][-{n}]`.
- **Credentials**: a dedicated IAM identity per device, scoped to `PutObject`
  under its own `images/{location_id}/` prefix only (see §6 baseline).

## 4. Part 2 — Video builder (AWS Lambda)

A Lambda function (`video-builder/`) converts one device-local day of images
into the daily video.

- **Trigger — per-Post local time**: each Post's build fires shortly after
  that Post's **local midnight** (legacy ran at 00:01), for the just-finished
  local day. Devices span many timezones, so trigger times differ per Post.
  Mechanism (one EventBridge Scheduler schedule per Post with
  `ScheduleExpressionTimezone`, vs. an hourly UTC sweep that selects Posts
  that just passed midnight) is an **ADR to be written**.
- **Input**: `s3://knh-dam-store/images/{location_id}/{YYYY-MM-DD}/*.jpg`
  (time-of-day filenames sort in capture order). Zero-byte/corrupt images are
  skipped and counted in logs.
- **Encode**: ffmpeg with the legacy-proven settings
  `-framerate 30 -c:v libx264 -r 30 -pix_fmt yuv420p`, run inside Lambda
  (ffmpeg layer vs. container image — ADR). Lambda `/tmp` (up to 10 GB) must
  hold one day of images plus the output; watch memory and timeout.
- **Output**: `videos/{LOCATION_ID}-{YYYY-MM-DD}.mp4` in the video pool —
  this exact naming is the contract the webapp's pool sync/assign depends on.
- **Reliability**: builds are idempotent (same Post+date overwrites the same
  key) and retryable; failures are visible (structured logs, retry/DLQ).
  The builder never deletes source images — retention is lifecycle-managed
  (30 days in the store, Glacier archive in `knh-dam-backup`, §7), which
  also leaves a re-build window if a video turns out bad.
- **Registration**: whether the builder auto-registers the Video item in the
  webapp's DynamoDB or relies on the existing pool-sync flow is an open
  design decision.

## 5. Part 3 — Webapp (separate repo)

The `days-in-a-minute` Next.js app (App Router, DynamoDB single table, S3
video pool with presigned playback URLs, JWT/RBAC auth; deployed via
OpenNext + CloudFront). Complete and live on its dev stage. From this
pipeline's perspective the contract is narrow:

- The builder drops correctly named MP4s into `s3://knh-dam-store/videos/`.
  (The webapp's pool bucket is configurable via its `STORAGE_BUCKET` env —
  its dev stage currently points at `csk-allsky/videos/` and will be
  repointed to `knh-dam-store` when this pipeline goes live.)
- The webapp discovers them via its pool sync/assign (filename prefix →
  Location) or, later, direct registration (§4).
- Each webapp Post carries a `timezone` attribute — the same per-Post
  timezone drives the builder's day boundary; `captured_date` is a plain
  `YYYY-MM-DD` in the Post's local timezone.
- The bucket is never public; viewers get short-lived presigned URLs after
  server-side RBAC checks.

## 6. Part 4 — Upload security & content filtering (future; NOT implemented)

Long-term, uploads must be protected against abuse (stolen credentials,
malicious or illegal content, junk data). **Only scenery images are wanted.**
Nothing in this section is implemented now; it is recorded so the current
design leaves room for it.

**Baseline that already exists (implemented as part of Parts 1–3):**

- Private buckets; no public access; playback via presigned URLs only.
- Per-device IAM identity scoped to `PutObject` on its own
  `images/{location_id}/` prefix — a leaked device credential cannot touch
  other prefixes, the video pool, or anything else.
- Key-only SSH on devices; no credentials in code or repos.

**Planned layers (design placeholders, roughly in adoption order):**

1. **Upload validation**: S3 policy/agent-side constraints on content-type,
   max object size, and key shape
   (`images/{location_id}/YYYY-MM-DD/hhmmssfff.jpg`); reject anything else.
   Cheap, catches accidents and crude abuse.
2. **Quotas & rate limits**: expected volume is known (~1,800 images/day per
   device, bounded size). Per-device daily object/byte budgets with alerts —
   an out-of-budget device signals a bug or a compromised credential.
3. **Quarantine flow**: new uploads land as "unverified"; the video builder
   only consumes images that passed checks. Suspect objects move to a
   quarantine prefix instead of the pipeline.
4. **Content moderation (scenery-only)**: automated screening (e.g., AWS
   Rekognition moderation labels or equivalent) to detect people-centric,
   explicit, or illegal content before images enter a video. Policy: this
   service publishes **scenery only** — cameras must point at landscapes/sky,
   and moderation enforces it server-side. Flagged content → quarantine +
   device flag + operator review.
5. **Device kill-switch**: per-device disable (revoke IAM key / deny prefix)
   that an operator can flip quickly; the webapp's Post visibility already
   hides content from viewers independently.
6. **Audit trail**: retained upload logs (who/what/when) sufficient to
   investigate an incident after the fact.

An ADR should pick the first increment (likely layers 1–2) when this becomes
active work; none of it blocks Parts 1–3.

## 7. Storage layout (S3)

**Primary bucket: `knh-dam-store`** (private). Two first-level folders:
`images/` for still images, `videos/` for the time-lapse videos.

```
s3://knh-dam-store/                        (private, primary store)
  images/                                  ← agent-writable, per-location prefix
    {location_id}/{YYYY-MM-DD}/{hhmmssfff}.jpg
    {location_id}/{YYYY-MM-DD}/{hhmmssfff}.json   ← metadata sidecar (optional)
  videos/                                  ← builder-writable, webapp-readable
    {LOCATION_ID}-{YYYY-MM-DD}.mp4

s3://knh-dam-backup/                       (private, long-term archive,
  images/                                   Glacier storage class)
    {location_id}/{YYYY-MM-DD}/{hhmmssfff}.jpg    ← same layout as the store
```

- **Image filename** `hhmmssfff` = capture time of day from
  `strftime("%H%M%S%f")` truncated to milliseconds (9 digits, e.g.
  `143059123.jpg` = 14:30:59.123). Times and the `{YYYY-MM-DD}` folder are
  **device-local**, so filenames sort in capture order and one day-folder is
  exactly one video's input.
- **Retention (stills)**: images live in `knh-dam-store` for **30 days**,
  after which only the **Glacier** copy in `knh-dam-backup` remains. Native
  S3 lifecycle cannot move objects across buckets, so the intent is
  implemented as: replicate `images/` to `knh-dam-backup` (destination
  storage class Glacier) + a 30-day lifecycle **expiration** rule on
  `knh-dam-store/images/` (mechanism details — replication-at-upload vs. a
  day-30 copy job — in an ADR, §11).
- **Videos** stay in `knh-dam-store/videos/` indefinitely (they are the
  product; ~small compared to stills).

## 8. Timezone model

- Every device/Post has one **IANA timezone** in config (agent) and in the
  webapp's Post record. They must agree; the Post record is authoritative.
- A "day" = midnight-to-midnight in that timezone. Image keys and video names
  embed the **local** date; persisted event timestamps are UTC ISO.
- The video build for a Post fires on that Post's local clock (§4). Never
  assume UTC, the Lambda region's clock, or the dev machine's clock.

## 9. Failure modes

| Failure | Behavior |
| ------- | -------- |
| Network/S3 down during capture | agent queues locally, retries with backoff; capture never lost |
| Device disk pressure | queue size/age cap with explicit, logged eviction |
| Corrupt/zero-byte images | builder skips and counts them; build proceeds |
| Build crash / partial run | idempotent re-run for the same Post+date overwrites the same output key |
| Build failed silently | forbidden — structured logs + retry/DLQ make every missing daily video diagnosable |
| Premature image deletion | forbidden — the builder never deletes; stills expire only via the 30-day lifecycle, with the Glacier copy in `knh-dam-backup` remaining |

## 10. Environments

| Stage | Agent runs on | Builder | Storage |
| ----- | ------------- | ------- | ------- |
| test | Windows dev machine (fake camera, mocked/local S3) | build logic run locally | test bucket / local |
| dev | bench Pis (`dam-*` fleet) | dev Lambda | `knh-dam-store` (+ `knh-dam-backup` archive) |
| prod | deployed Pis worldwide | prod Lambda | `knh-dam-store` (stage separation TBD, §11) |

Agent stages come from `.env.{STAGE}` files (never committed); the Lambda
gets deploy-time environment variables; the webapp has its own stage setup in
its repo.

## 11. Open questions → ADRs

1. Builder trigger mechanism: per-Post EventBridge Scheduler
   (`ScheduleExpressionTimezone`) vs. hourly UTC sweep.
2. ffmpeg packaging: Lambda layer vs. container image.
3. Archive mechanics for the 30-day still retention (§7):
   replication-at-upload to `knh-dam-backup` (Glacier destination class) vs.
   a day-30 copy job; and whether dev/prod share `knh-dam-store` or get
   stage-suffixed buckets.
4. Video registration: builder writes DynamoDB Video item vs. webapp
   pool-sync only.
5. Capture backend: picamera2 vs. shelling out to `rpicam-still` (two camera
   stacks in the fleet today — see `docs/reference/camera-info.md`).
6. First increment of the upload-security layers (§6), when scheduled.

## 12. References

- This repo: `CLAUDE.md` (conventions), `docs/reference/camera-info.md`
  (fleet cameras), `legacy-rpi-camera/allsky-service/` (legacy single-Pi
  implementation: `capture-24h.py`, `build-upload-video.py`).
- Webapp repo: `D:\home\repo-misc\days-in-a-minute` — `docs/design/`
  (`00-architecture.md`, `01-server-api.md`, `02-clientside.md`,
  `er-diagram.md`).
