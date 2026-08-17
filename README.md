# DAM-Agent-Service

The **capture/encode pipeline of the days-in-a-minute service**: Raspberry
Pi agents around the world capture a location's scenery as still images all
day, and an AWS Lambda turns each completed local day into a **1-minute
30 fps time-lapse video** consumed by the
[days-in-a-minute webapp](https://github.com/kimhwangdata/days-in-a-minute).

```
Pi agents ──presigned PUT──> s3://knh-dam-store/images/{loc}/{date}/
   │  (upload-signer Lambda: auth + heartbeat + capture window)
   │  (upload-monitor Lambda: size/JPEG validation, health)
   └─ per-frame {hhmmssfff}.json hardware sidecars

video-builder Lambda (15-min EventBridge sweep, per-device local midnight)
   └──> s3://knh-dam-store/videos/{loc}/{LOC}-{YYYY-MM-DD}.mp4  (+ .log)
```

## Components

| Folder | Runs on | What it does |
| ------ | ------- | ------------ |
| `agent/` | each Raspberry Pi (`dam-agent` systemd service) | window-gated capture with adaptive interval, bounded in-memory upload queue with retry, per-frame hardware sidecars, manual night mode for low-light sensors, MJPEG live viewer with 1 s preview boost |
| `upload-signer/` | AWS Lambda (API Gateway) | presigns every upload (devices hold app tokens, never AWS credentials — ADR-0003); each call doubles as fleet heartbeat and control gate (pause / unassigned / capture window) |
| `upload-monitor/` | AWS Lambda (S3 events) | validates every landed image (size bounds, JPEG magic), tags damaged files, maintains device health |
| `video-builder/` | AWS Lambda (EventBridge sweep) | builds each completed local day into the 1-minute video (legacy-proven ffmpeg settings) plus a daily hardware log summarized from the sidecars |
| `shared/` | everywhere | cross-service constants (S3 layout, JPEG magic, content types, fps math) |
| `scripts/` | dev machine | Pi provisioning/deploy over SSH; idempotent boto3 deploy scripts for the Lambdas (AWS profile `dam-deployer`, a scoped role — see `docs/reference/setup-dam-deployer-policy.md`) |

## Key behaviors

- **Capture window** (operator-set per device): the agent captures only
  inside `video_window_start–end` (learned from every signer response) and
  adapts its interval so the window still fills `VIDEO_MINUTES` of video —
  full day = 48 s, 12 h window = 24 s.
- **Night mode** (IMX462 units): libcamera AE cannot exceed the tuning
  file's ~66 ms shutter ceiling, so below ~10 lux the agent switches to
  manual exposure/gain (per-device `NIGHT_EXPOSURE_MS`/`NIGHT_GAIN`), back
  to auto above ~30 lux, with a blown-frame escape hatch for daylight.
- **Hardware logging**: every frame is paired with a `{hhmmssfff}.json`
  sidecar (camera settings actually used, temperature, core voltage,
  under-voltage flags, image size); the builder digests each day into
  `videos/{loc}/{LOC}-{date}.log`.
- **Never lose a day**: builds are idempotent (same output key), damaged
  frames are skipped and counted, images are only lifecycle-expired after
  the video exists.
- **Timezones**: a device's "day" is its **local** midnight-to-midnight;
  all image keys use device-local dates and times.

## Development

Development happens on Windows; hardware truth lives on the Pis and in
Lambda. See `CLAUDE.md` for conventions and the three-runtime reality.

```
./.venv/Scripts/python -m pytest -q      # full suite (camera & S3 mocked)
./.venv/Scripts/python -m ruff check .   # lint
scripts/deploy.sh <pi-ip>                # ship agent/ + shared/ + restart
scripts/aws/deploy_upload_signer.py      # (and _monitor / _video_builder)
```

Design documents in `docs/design/` (numbered) and ADRs in
`docs/design/adr/` are the source of truth; phase plans in `docs/plan/`
track execution; `docs/reference/` holds hardware/fleet references
(cameras, lenses, security model, deployer IAM setup).

## Security model (summary)

Devices are the least-trusted tier: no AWS credentials, no boto3 — only a
revocable app token whose hash lives server-side. All AWS identities are
scoped Lambda execution roles capped by a permissions boundary; the dev
machine deploys through the `dam-deployer` assumed role, confined to
`dam-*`/`knh-dam-*` resources. Full write-up:
`docs/reference/rpi-agent-security.md`.
