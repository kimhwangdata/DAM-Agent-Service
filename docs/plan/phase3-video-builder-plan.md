# Phase 3 — Video Builder Implementation Plan

- **Status**: Complete (overnight hands-off verification passed
  2026-08-14 00:26 KST)
- **Date**: 2026-08-13
- **Based on**: `docs/design/03-video-builder.md` (all § refs point there)
- **Goal**: the 15-minute sweep builds every completed capture cycle into
  `videos/{location_id}/{LOC}-{date}.mp4` automatically; the first real
  days-in-a-minute videos exist for all four JAYANG locations and play in
  the webapp.

## Non-goals

- Auto-registration of Video items in the webapp table (§8 — v1 is
  pool-sync; revisit as an ADR after the flow proves out).
- Dawn/dusk windows, adaptive interval, rebuild UI, notifications (§9).

## Prerequisites

- [x] Phase 2 complete: fleet capturing at 4 locations, windows +
      timezones + `assignment` live in `knh-dam-agents`.
- [x] At least one **completed** capture cycle (first full day finishes at
      the fleet's local midnight 2026-08-14 00:00 KST); JAYANG3's
      2026-08-13 folder carries the two tagged damaged files as the
      skip-path test case.

## Steps

### 3.1 ADRs (decisions made in the design, recorded per convention)

- [x] **ADR-0004 build trigger**: EventBridge 15-min sweep + cycle math
      from DynamoDB, vs per-Post schedules (§3 rationale: operator edits
      always current, zero schedule choreography, self-healing catch-up).
- [x] **ADR-0005 ffmpeg packaging**: static-binary Lambda layer vs
      container image (§6 rationale: existing zip pipeline, no ECR,
      small cold starts).
- [x] Index both in `docs/design/adr/README.md`.

### 3.2 ffmpeg layer

- [x] `scripts/aws/build_ffmpeg_layer.py`: download a pinned static
      ffmpeg build (arm64/x86_64 matching the Lambda arch), verify its
      checksum, zip as `bin/ffmpeg`, publish as layer
      `dam-ffmpeg` (versioned); print the layer ARN.
- [x] Smoke: invoke a scratch Lambda (or the builder later) running
      `ffmpeg -version` from the layer.

### 3.3 Handler (`video-builder/handler.py`) + tests

- [x] `cycles.py` (pure, no AWS): window parsing, cycle-end math for the
      three window shapes (§3), most-recent-completed-cycle for a given
      `now` + timezone, and the `hhmmssfff` range filter for listings
      (incl. the two-folder split for midnight-crossing windows).
- [x] Dispatch mode: scan agents → skip unassigned → compute due cycle →
      compare `last_video.date` → async self-invoke build events; log a
      per-sweep summary (due/skipped counts).
- [x] Build mode (§5): list with `< 10 KB` drop → threaded download to
      `/tmp/frames/` → SOI/EOI validation with `skipped_damaged` count →
      ffmpeg (legacy settings verbatim; stderr captured to logs) →
      upload with metadata → `last_video` UpdateItem; `/tmp` cleaned on
      entry; zero-frames guard (§5.7).
- [x] Tests (fake S3/DynamoDB, ffmpeg stubbed; real `ffmpeg -version`
      integration test auto-skipped when ffmpeg is absent locally):
      cycle math table (default / same-day / midnight-crossing, around
      midnight boundaries), dispatch dedup (`last_video` current → no
      invoke; lagging → invoke; unassigned skipped), listing range
      filter + size drop, magic validation skip+count, zero-frames
      guard, `last_video` written only after upload.

### 3.4 Deploy

- [x] `scripts/aws/deploy_video_builder.py` (idempotent, pattern of the
      other two): role per §6 (images read, videos write, agents table
      RW, self-invoke), Lambda (python3.12, **3008 MB / 900 s / 2048 MB
      /tmp**, ffmpeg layer attached, env: bucket/table/prefixes/stage
      tz fallback), EventBridge rule `rate(15 minutes)` →
      `{"mode": "dispatch"}` + invoke permission. **Deploy.**
- [x] Verify a dispatch fires in CloudWatch (no builds due yet is fine).
      Scheduled sweep observed at 07:01:52 UTC: `{"due": [], "skipped": 4}`.

### 3.5 First real builds (needs a completed cycle — 2026-08-14)

- [x] Manual build first: invoke
      `{"mode":"build","location_id":"JAYANG3","date":"2026-08-13"}` for
      the partial-but-real first day → video lands at
      `videos/JAYANG3/JAYANG3-2026-08-13.mp4`, `skipped_damaged == 2`
      (the tagged test files), `last_video` recorded, duration ≈
      frames/30. **Result**: ok, 255 frames, 1.4 MB, duration 8.5 s
      (= 255/30), build 14.5 s. `skipped_damaged` was 0, not 2 — see
      deviations (both planted files are 88 B, so the < 10 KB listing
      drop excluded them before the magic check that feeds the counter;
      the invariant "damaged frames never enter the video" held).
- [x] Then hands-off: after local midnight, the sweep builds all four
      locations' cycles without intervention; verify all four videos +
      `last_video` records; check one video's content (download, play,
      spot-check duration and that day-spanning frames are ordered).
      **Verified 2026-08-14 00:26 KST**: the 00:01:53 KST scheduled
      sweep dispatched all four (`skipped=0`), all four builds ok
      within 51 s — JAYANG1 846 frames / 6.2 MB, JAYANG2 599 / 4.6 MB,
      JAYANG3 849 / 5.0 MB (full-day rebuild overwrote the 1.4 MB
      partial), JAYANGN 457 / 3.7 MB; `last_video.date=2026-08-13` on
      all four devices; every `duration_s` = frames/30 exactly.
      (Frame counts differ per device because day 1 was partial —
      agents started capturing at different times.)
- [x] Failure-path check: a location with `last_video` current is NOT
      rebuilt by subsequent sweeps (CloudWatch dispatch summaries).
      **Verified**: consecutive sweeps 06:49/07:01/07:16 UTC all logged
      `due=[] skipped=4` with `last_video` current. Bonus finding: the
      very first sweep (06:46, before seeding) dispatched the empty
      2026-08-12 cycle for all four locations — every build hit the
      zero-frames guard (`status: "no-frames"` logged as errors, no
      video written, no `last_video` recorded), production-proving §5.7.

### 3.6 Webapp: pool sync over subfolders + first viewing

- [x] In the webapp repo: verify `listPoolObjects` handles
      `videos/{location_id}/…` keys (S3 listing is already recursive —
      confirm the key-parsing/prefix→Location matching works on the new
      basenames; fix if it assumes a flat folder). Its plan/docs updated
      per that repo's convention. **No code changes needed**: the listing
      is a plain recursive `ListObjectsV2`, `parseCapturedDateFromKey`
      matches the date anywhere in the key, and `init-pool-assign.sh`
      prefix-matches on the basename. The real gap was the pool bucket
      itself — see deviations (repointed `csk-allsky` → `knh-dam-store`).
- [x] `/manage/pool` sync → the new MP4s appear → assign to their
      JAYANG Posts (or via `init-pool-assign` prefixes) → **play a
      days-in-a-minute video in the webapp** — the full pipeline,
      capture → build → watch, end to end. **Verified via API**: sync
      added exactly 1 (the subfolder key; the 227 copied legacy keys
      already had Video records, no duplicates), assigned to the
      existing JAYANG3 Post, presigned `play_url` range-GET → 206 from
      `knh-dam-store`; a legacy flat-key video also presigns 206.

### 3.7 Exit criteria

- [x] Suites green in this repo (and webapp if touched). 2026-08-13:
      agent repo 94 passed + 1 skipped (local ffmpeg), ruff clean
      repo-wide; webapp 116 passed.
- [x] Four consecutive-day videos appear with no human action (check the
      morning after 3.5). All four 2026-08-13 videos were built by the
      scheduled sweep alone; subsequent sweeps (00:16/00:31/00:46 KST)
      logged `due=[] skipped=4` — no rebuilds.
- [x] `last_video` visible for all four devices; damaged-skip counted
      exactly where expected; no orphan `/tmp` growth across warm runs
      (CloudWatch memory/storage metrics sane). REPORT lines: builds
      used 444–502 MB of 3008 MB; the JAYANG3 build ran on a warm
      container (no Init Duration) and succeeded — `/tmp` hygiene on
      entry works; no disk/ENOSPC errors anywhere.
- [x] Plans updated with `[x]` + deviations (this file, continuously).

## Deviations / decisions during execution

- 3.6: the webapp's pool still pointed at the legacy `csk-allsky` bucket
  (us-east-1, "during development" per its docs). Cut over to the real
  pool: copied all 227 non-empty `csk-allsky/videos/*` objects
  server-side into `knh-dam-store/videos/` (flat keys preserved, so
  existing Video records keep working; `csk-allsky` left untouched),
  set `STORAGE_BUCKET=knh-dam-store` / `STORAGE_REGION=ap-northeast-2`
  in the webapp's `.env.dev`/`.env.test`/`.env.example`, updated its
  CLAUDE.md + `00-architecture.md`, redeployed (tests 116 green).
  Follow-up 2026-08-14: the legacy flat keys were moved into the same
  per-location layout (`videos/CAMAS/`, `videos/PHL/`, `videos/PHLW/`,
  227 objects) with the webapp's Video records (`s3_key`, and `GSI1SK`
  for pool items) repointed in both stage tables — the pool now has one
  uniform key shape.

- 3.5: `skipped_damaged` expectation corrected — the two tagged test
  files are 88-byte objects, excluded by the size drop at the listing
  stage (silent by design, §5.1); the SOI/EOI `skipped_damaged` counter
  only sees ≥ 10 KB files with bad magic (unit-tested). After verifying
  the manual partial-day build, JAYANG3's `last_video` was reset to
  2026-08-12 so the overnight sweep rebuilds the full day (idempotent
  overwrite of the same key).
- 3.4: seeded `last_video = {date: "2026-08-12", seeded: true}` on all
  four devices at deploy time — with no `last_video`, dispatch would
  retry the empty pre-capture 2026-08-12 cycle every sweep (harmless
  no-frame builds, but noisy). The first real due cycle is 2026-08-13,
  completing at local midnight. Manual dispatch verified:
  `{"due": [], "skipped": 4}`.
- 3.2: layer `dam-ffmpeg:1` = ffmpeg 7.0.2 static (johnvansickle),
  sha256-pinned in the build script; 29.5 MB zipped. Smoke test in a
  scratch Lambda returned `ffmpeg version 7.0.2-static` (rc 0).
  Gotcha fixed en route: `ZipInfo` + `writestr` defaults to STORED —
  the compress_type must be set explicitly or the layer exceeds the
  70 MB publish request cap.
