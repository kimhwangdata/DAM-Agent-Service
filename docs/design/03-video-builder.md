# 03 — Video Builder Design (AWS Lambda)

- **Status**: Draft
- **Date**: 2026-08-13
- **Based on**: `00-architecture.md` §4/§7, `02-agent-manager.md` §5.3
  (video window), and the legacy
  `legacy-rpi-camera/allsky-service/build-upload-video.py` — the on-Pi
  predecessor this design lifts into the cloud.

## 1. Purpose & legacy heritage

Turn one completed capture cycle (a location's daily video window) into a
1-minute time-lapse MP4 in the video pool. The legacy script did this on
the Pi at 00:01 local; the builder does the same job in Lambda, with the
fleet's DynamoDB records replacing the Pi's local filesystem and cron.

| Legacy (`build-upload-video.py`) | Builder (this design) |
| --- | --- |
| `schedule` at 00:01 local, on-device | EventBridge sweep + per-device window end from DynamoDB (§3) |
| images in `/opt/allsky/{date}/` | `s3://knh-dam-store/images/{location_id}/{date}/` |
| `remove_invalid_images` (PIL verify) | size filter at listing + JPEG SOI/EOI check after download (§5) |
| `ffmpeg -framerate 30 -pattern_type glob -i '{date}/*.jpg' -c:v libx264 -r 30 -pix_fmt yuv420p` | **identical settings**, run in Lambda against `/tmp` (§5) |
| upload to `videos/{LOC}-{date}.mp4` | upload to `videos/{location_id}/{LOC}-{date}.mp4` (§7 layout) |
| delete images after upload | **never deletes** — 30-day lifecycle + Glacier archive own retention |
| silent failures possible | structured logs + `last_video` state + visible failure modes (§7) |

## 2. Architecture

One Lambda (`video-builder/`), two event shapes:

```
EventBridge rule (rate: 15 min)
        │  {"mode": "dispatch"}
        ▼
┌─ video-builder Lambda ──────────────────────────────────────────────┐
│ DISPATCH: scan knh-dam-agents →                                     │
│   for each assigned device:                                         │
│     cycle = latest completed window (video_window + timezone, §3)   │
│     if last_video.date < cycle.date → async self-invoke BUILD       │
│                                                                     │
│ BUILD {"mode":"build","location_id","date","window"}:               │
│   list day folder(s) → download to /tmp → validate → ffmpeg →       │
│   upload videos/{loc}/{LOC}-{date}.mp4 → record last_video (§4)     │
└─────────────────────────────────────────────────────────────────────┘
         │                                   │
   DynamoDB knh-dam-agents            S3 knh-dam-store
   (windows, tz, last_video)          (images in, videos out)
```

Dispatch is light (a table scan + date math); each build is heavy
(hundreds of MB, minutes of ffmpeg) and runs as its own async invocation
so one slow location never delays another and each build gets the full
timeout.

## 3. Trigger — "when the capture cycle ends" (decides the trigger ADR)

The capture cycle is defined entirely by DynamoDB (`02` §5.3):
`control.video_window_start/_end` + the device's timezone
(`reported.timezone`, fallback the stage default). The **sweep model** is
chosen over per-Post EventBridge schedules:

- windows and assignments change in DynamoDB at any time — a sweep always
  reads current truth, while per-device schedules would need create/
  update/delete choreography on every operator edit;
- one rule, zero schedule-management code, and a 15-minute sweep bounds
  video latency to ≤ 15 min after window end — irrelevant for a daily
  product.

Cycle math per device (all in the device's local timezone):

- default window (`start == end == "00:00"`): cycle for day `D` ends at
  `D+1 00:00` — the legacy midnight rule.
- `start < end`: cycle for day `D` ends at `D end`.
- `start > end` (crosses midnight): cycle labeled `D` spans
  `D start → D+1 end`, so it ends at `D+1 end`.
- The dispatcher computes the most recent **completed** cycle date and
  builds it iff `last_video.date < cycle.date` — which also self-heals:
  a day missed (Lambda outage, code bug) is built by the next sweep that
  sees the gap (one cycle back only; deeper backfill is a manual
  re-invoke with explicit `{location_id, date}`).

## 4. Fleet state in DynamoDB (read + one new attribute)

Read per device: `assignment.location_id` (skip unassigned),
`control.video_window_*`, `reported.timezone`.

New builder-owned attribute on the agents record (extends the 02 §4
contract; webapp reads it for display, never writes it):

```
last_video:
  date: "2026-08-13"            ← cycle label (window-start date)
  key: "videos/JAYANG2/JAYANG2-2026-08-13.mp4"
  built_at: ISO-UTC
  frames: 1794                  ← images encoded
  skipped_damaged: 2            ← failed validation (§5)
  duration_s: 59.8              ← frames / 30
  build_ms: 84210
```

This is both the dispatcher's dedup state and the manager's "last video"
display. A failed build does NOT write `last_video` — the next sweep
retries (§7).

## 5. Build pipeline (per invocation)

1. **List** `images/{location_id}/{date}/` (and, for midnight-crossing
   windows, the relevant slice of `{date+1}/` — §3). Filter by the
   window's `hhmmssfff` range lexicographically and drop objects
   `< 10 KB` straight from the listing (same bound as the upload-monitor
   and the webapp's latest-frame fix — free damaged-file exclusion).
2. **Download** to `/tmp/frames/` with a small thread pool (~16),
   preserving the time-ordered filenames. ~1,800 × ~120 KB ≈ 220 MB.
3. **Validate** each file's JPEG magic (SOI `FFD8` / EOI `FFD9`) — the
   legacy `remove_invalid_images` equivalent, catching corrupt-but-large
   files the size filter misses; failures are deleted from `/tmp` and
   counted as `skipped_damaged`. (The upload-monitor's `damaged=true`
   tags overlap this check; validating locally avoids 1,800
   GetObjectTagging calls.)
4. **Encode** with the legacy-proven command, unchanged:
   `ffmpeg -framerate 30 -pattern_type glob -i '/tmp/frames/*.jpg'
   -c:v libx264 -r 30 -pix_fmt yuv420p /tmp/out.mp4`
5. **Upload** to `videos/{location_id}/{LOCATION_ID}-{date}.mp4`
   (overwrite = idempotent rebuild), `ContentType: video/mp4`, metadata:
   frames, skipped, window, builder version.
6. **Record** `last_video` on the agents record; log a one-line summary.
7. Guard rails: zero usable frames → log + no video + no `last_video`
   (stays pending; visible as a failure, §7); `/tmp` cleaned at start of
   every run (warm containers).

## 6. Packaging & resources (decides the ffmpeg ADR)

- **ffmpeg as a static binary inside a Lambda layer** (johnvansickle/
  BtbN-style static build, ~26 MB zipped — under the layer limit).
  Chosen over a container image: our deploy scripts already do
  zip-based Lambdas, no ECR pipeline needed, cold starts stay small.
- Runtime `python3.12`, handler code zip like the other Lambdas.
- **Memory 3008 MB** (Lambda CPU scales with memory — this is ffmpeg's
  knob), **timeout 900 s**, **ephemeral storage 2048 MB**
  (frames + output + headroom).
- Execution role: `s3:ListBucket`+`GetObject` on `images/*`,
  `s3:PutObject` on `videos/*`, DynamoDB Scan/GetItem/UpdateItem on
  `knh-dam-agents`, `lambda:InvokeFunction` on itself (dispatch → build).

## 7. Reliability & failure modes

| Failure | Behavior |
| --- | --- |
| Build crashes / times out | `last_video` unwritten → next 15-min sweep retries the same cycle |
| Persistent build failure | retries every sweep; visible as `last_video.date` lagging in the manager (future: surface as a health badge) |
| Damaged frames | skipped + counted (`skipped_damaged`); the two tagged mid-day test files in JAYANG3's 2026-08-13 folder are the ready-made test case |
| Zero frames in window | no video, cycle stays pending, error logged (device was paused/offline all day — the manager already shows why) |
| Rebuild needed (bad video) | manual invoke `{"mode":"build", location_id, date}` — overwrite is idempotent; source stills remain ≥ 30 days |
| Sweep double-fire / concurrent builds | builds are idempotent (same output key); `last_video` written post-upload makes duplicates harmless |
| Images expired (>30 days) | rebuild impossible from the store — Glacier restore from `knh-dam-backup` is the (manual) escape hatch |

## 8. Registration with the webapp (v1 decision for the registration ADR)

**v1: webapp pool-sync only.** The builder just writes correctly-named
MP4s; the existing `/manage/pool` sync discovers them (its listing must
handle the `videos/{location_id}/` subfolder — noted in `00` §5). This
keeps the builder decoupled from the webapp's table. Auto-registering
Video items (so new videos appear without an admin sync) is the natural
fast-follow once the flow proves out — revisit as an ADR then.

## 9. Deferred (unchanged from earlier designs)

Dawn/dusk window values (02 §5.3 — needs Post coordinates), adaptive
capture interval for short windows (02 §10.1c), automated rebuild UI,
per-build notifications.

## 10. Build plan

`docs/plan/phase3-video-builder-plan.md` (to be written from this doc):
record ADR-0004 (sweep trigger), ADR-0005 (ffmpeg layer) → ffmpeg layer +
deploy script → handler (dispatch + build) with tests (fake S3/DynamoDB/
ffmpeg-stub; window math incl. midnight crossing) → deploy → first real
build against a completed JAYANG day (including the damaged-skip case) →
webapp pool-sync subfolder fix + sync → watch the first video play.
