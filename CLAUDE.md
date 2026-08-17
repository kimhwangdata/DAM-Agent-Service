# Days-in-a-Minute — Capture Agent & Video Builder

## This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Goal

This repo is the **capture/encode pipeline of the days-in-a-minute service**: each
day of a location's scenery is captured as still images and turned into a
**1-minute time-lapse video**. It has two deliverables:

### Part 1 — `rpi-camera-agent` (runs on each Raspberry Pi)

A Python agent that runs on a Raspberry Pi and:

- captures still images from the Pi camera on a configurable schedule
  (a full day of frames for a 1-minute 30 fps video ≈ 1,800 captures/day),
- uploads each image to a **private AWS S3 bucket** together with capture
  metadata (device id, capture time, camera settings),
- queues captures locally and retries when the network or S3 is unavailable
  (capture must never be lost because upload failed),
- exposes simple operational visibility (structured logs; optionally a small
  status endpoint / health file).

### Part 2 — `video-builder` (runs in AWS Lambda)

A Lambda function that, once a device's local day is complete:

- collects that day's images from S3, skips invalid/zero-byte files,
- builds the 1-minute daily video with **ffmpeg**
  (legacy-proven settings: `-framerate 30 -c:v libx264 -r 30 -pix_fmt yuv420p`),
- uploads the video to the S3 **video pool** consumed by the days-in-a-minute
  webapp, named `{LOCATION_ID}-{YYYY-MM-DD}.mp4` under the `videos/` prefix,
- cleans up source images **only after** the video is verified in the pool.

### Worldwide devices, per-device timezone

Agents run all over the world. Every device has its own **IANA timezone** in
config; a device's "day" starts/ends at its **local midnight**, so the
video-build trigger time differs per device (legacy built at 00:01 local).
Day boundaries always use the device's timezone — never assume UTC or the
Lambda region's clock. The trigger mechanism (per-device EventBridge Scheduler
schedule with `ScheduleExpressionTimezone` vs. an hourly UTC sweep) is decided
in a design doc / ADR.

### Related code

- **Downstream webapp**: `D:\home\repo-misc\days-in-a-minute` (own CLAUDE.md) —
  Next.js viewer/management app over the video pool. Its pool sync/assign
  matches videos by the `{LOCATION_ID}-{YYYY-MM-DD}.mp4` naming; keep that
  contract stable.
- **Legacy reference**: `legacy-rpi-camera/` (read-only, do not modify) — the
  single-Pi predecessor. `allsky-service/capture-24h.py` captured all day and
  `allsky-service/build-upload-video.py` built + uploaded the daily video
  **on the Pi**. The new architecture keeps its proven pieces (ffmpeg settings,
  video naming, invalid-image cleanup, delete-only-after-verified-upload) but
  moves the video build off the Pi into Lambda.

Development happens on Windows in this repo; the agent **runs on the Pi**
(Raspberry Pi OS Trixie / Debian 13, arm64, Python 3.13) and is deployed to it
over SSH; the video builder **runs in AWS Lambda** and is deployed with AWS
tooling.

## Repository Layout

```
agent/              Python package — capture, scheduler, uploader, local queue, config
video-builder/      AWS Lambda video builder — handler, ffmpeg invocation, IaC/deploy
                    config (packaging: ffmpeg layer or container image — see ADR)
upload-signer/      AWS Lambda issuing presigned upload URLs to devices
                    (ADR-0003 — devices hold app tokens, never AWS credentials)
agent-manager/      Fleet operations: agent-api Lambda (heartbeat/desired state,
                    operator API) + static manager UI (docs/design/02-agent-manager.md)
shared/             Cross-service constants (deployed with the agent and inside
                    each Lambda zip)
systemd/            Unit files: agent service (+ timer if used) installed on the Pi
scripts/            deploy.sh / deploy.ps1 (rsync/scp over SSH + service restart),
                    provisioning helpers (enable camera, install deps on the Pi),
                    Lambda deploy script
tests/              pytest suite — runs on Windows/CI with mocked camera and S3
docs/design/        Design documents, numbered (00-…)
docs/design/adr/    Architecture Decision Records — see docs/design/adr/README.md
docs/plan/          Phase plan documents
docs/reference/     Hardware/fleet reference (camera-info.md — one section per
                    camera module, same format for each)
legacy-rpi-camera/  Legacy allsky code — reference only, never modified or imported
.env.example        Template for stage env files (committed; real stage files are not)
```

(Layout is the target structure — create pieces as the plan reaches them.)

## Three-Runtime Reality

- **Hardware-dependent code (Picamera2 / `rpicam-still`) only runs on the Pi.**
  Keep capture behind a small interface so the rest of the agent (scheduler,
  queue, uploader) runs and is testable on Windows with a fake camera source
  (static file / generated image).
- Verifying camera behavior means deploying to the Pi and running there via
  SSH; never assume a Windows-side run proves capture works.
- Deploy loop: edit here → `scripts/deploy` (rsync/scp + restart systemd
  service) → check `journalctl -u rpi-camera-agent` on the Pi.
- **The video builder only truly runs in AWS Lambda.** Keep the build logic
  (list day's images, validate, run ffmpeg, upload, clean up) as plain testable
  Python invoked by a thin Lambda handler, so it runs on Windows against local
  files / mocked S3. Mind Lambda limits: `/tmp` ephemeral storage (configurable
  up to 10 GB) must hold one day of images plus the output video; watch
  timeout and memory. Verifying the real build means deploying to Lambda and
  checking CloudWatch logs.

## Environment Stages

Stages selected by the `STAGE` env var; the agent loads `.env.{STAGE}`.
There is **no plain `.env`**. `.env.test` / `.env.dev` / `.env.prod` are
gitignored — only `.env.example` is committed.

| Stage | Env file    | Runs on                        | S3 target                    |
| ----- | ----------- | ------------------------------ | ---------------------------- |
| test  | `.env.test` | Windows dev machine (mock cam) | local MinIO or a test bucket |
| dev   | `.env.dev`  | bench Pi                       | dev bucket                   |
| prod  | `.env.prod` | deployed Pi(s)                 | production bucket            |

The video builder follows the same stages: its Lambda configuration
(environment variables set at deploy time, not `.env` files in the package)
points the dev/prod function at the dev/prod bucket; `test` runs the build
logic locally against local files or mocked S3.

## Architecture Decision Records

- Significant decisions are recorded as ADRs in `docs/design/adr/`, one file
  per decision, named `ADR-NNNN-short-kebab-title.md`, following the format in
  `ADR-0000-template.md` (Status / Date / Deciders / Blocks, Context, Decision
  drivers, Options with `+`/`−` trade-offs, Open questions, Decision,
  Consequences, Next). Index new ADRs in `docs/design/adr/README.md`.
- Accepted ADRs are immutable — supersede with a new ADR instead of editing.

## ID and S3 Key Conventions

- **Capture IDs are ULIDs**, generated on the Pi at capture time
  (`python-ulid`), carried in capture metadata. ULIDs are time-ordered.
- **S3 bucket `knh-dam-store`** holds everything, split at the first level
  into `images/` and `videos/` (full layout in
  `docs/design/00-architecture.md` §7). Image keys:
  `images/{location_id}/{YYYY-MM-DD}/{hhmmssfff}.jpg`
  with an optional metadata sidecar `{hhmmssfff}.json`; `hhmmssfff` is the
  capture time of day from `strftime("%H%M%S%f")` truncated to milliseconds.
- Dates and times in image keys are **device-local** (device's IANA timezone
  from config), so one day-folder listing is exactly one video's input for
  the builder, sorted by filename.
- Daily videos go to `videos/{location_id}/{LOCATION_ID}-{YYYY-MM-DD}.mp4`
  (per-location subfolder like `images/`; date in the device's local
  timezone) — the **basename** is the naming contract the days-in-a-minute
  webapp's pool sync/assign depends on.
- **Still retention**: 30 days in `knh-dam-store`, then only the Glacier
  archive copy in bucket `knh-dam-backup` (same `images/` layout) remains.
  The builder never deletes images.
- `location_id`/`device_id` are stable per-Post config values — never
  hardcoded.
- No auto-increment integers or random UUIDv4 for IDs.

## Code Style Guidelines

- Python 3.11+, full type hints, small modules; keep every change as simple
  and narrow as possible.
- All documentation and code comments must be written in English.
- Configuration and magic values live in one config module read from the env
  file — no literals scattered through the code.
- Use constants from `shared/constants.py` or service own folder `./constants.py` if the service is not shared; no magic numbers or string literals in code.

## Standard Workflow

1. First think through the problem, read the codebase for relevant files, and write a plan using the TodoWrite tool.
2. The plan should have a list of todo items that you can check off as you complete them.
3. Before you begin working, check in with me and I will verify the plan.
4. Then, begin working on the todo items, marking them as complete as you go.
5. Please every step of the way just give me a high level explanation of what changes you made.
6. Make every task and code change you do as simple as possible. We want to avoid making any massive or complex changes. Every change should impact as little code as possible. Everything is about simplicity.
7. Finally, provide a summary of the changes made and any relevant information.

## Plan Documentation

- Plans live in `docs/plan/`, one file per phase.
- **After completing each step, update the corresponding phase plan file** —
  mark completed items with `[x]` and document any deviations or decisions.

## Error Handling

- Upload failures must not crash the agent: captures wait in the **bounded
  in-memory upload queue** and retry with backoff. The agent **never writes
  images to device storage** (no-local-save design, `docs/design/01-agent.md`
  §1); if the queue overflows, the oldest frame is dropped and the drop is
  counted in logs.
- Log errors with context (capture id, S3 key, attempt count) for debugging;
  never log secrets, tokens, or credentials.
- **Never lose a day**: video builds are idempotent and retryable — rerunning
  the builder for the same device + date overwrites the same output key.
  Skip (and count in logs) invalid/zero-byte images instead of failing the
  whole build; source images are deleted or lifecycle-expired **only after**
  the video is verified in the pool.
- Failed builds must be visible (structured logs, retry/DLQ per the design
  doc), never silent — a missing daily video should be diagnosable from logs.

## Security must-follows

- **Never** commit or print `.env*` contents (real keys live there;
  `.env.example` is the template).
- No real AWS keys, hostnames, or credentials in code, tests, fixtures, or
  docs — placeholders only.
- The S3 bucket stays private. The agent's AWS credentials must be a dedicated
  IAM identity scoped to `PutObject` on its own prefix — no wildcard policies,
  no root/account keys.
- The video builder uses its **Lambda execution role** (no keys in code or
  package), scoped to read/delete on the image prefixes and `PutObject` on the
  `videos/` pool prefix only.
- SSH to the Pi uses key auth only; deploy scripts must not embed passwords.
- Captured images may show private spaces — treat them as sensitive data:
  don't copy them into the repo, tests, or docs.
