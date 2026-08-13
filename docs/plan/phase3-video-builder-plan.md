# Phase 3 — Video Builder Implementation Plan

- **Status**: Not started
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
- [ ] At least one **completed** capture cycle (first full day finishes at
      the fleet's local midnight 2026-08-14 00:00 KST); JAYANG3's
      2026-08-13 folder carries the two tagged damaged files as the
      skip-path test case.

## Steps

### 3.1 ADRs (decisions made in the design, recorded per convention)

- [ ] **ADR-0004 build trigger**: EventBridge 15-min sweep + cycle math
      from DynamoDB, vs per-Post schedules (§3 rationale: operator edits
      always current, zero schedule choreography, self-healing catch-up).
- [ ] **ADR-0005 ffmpeg packaging**: static-binary Lambda layer vs
      container image (§6 rationale: existing zip pipeline, no ECR,
      small cold starts).
- [ ] Index both in `docs/design/adr/README.md`.

### 3.2 ffmpeg layer

- [ ] `scripts/aws/build_ffmpeg_layer.py`: download a pinned static
      ffmpeg build (arm64/x86_64 matching the Lambda arch), verify its
      checksum, zip as `bin/ffmpeg`, publish as layer
      `dam-ffmpeg` (versioned); print the layer ARN.
- [ ] Smoke: invoke a scratch Lambda (or the builder later) running
      `ffmpeg -version` from the layer.

### 3.3 Handler (`video-builder/handler.py`) + tests

- [ ] `cycles.py` (pure, no AWS): window parsing, cycle-end math for the
      three window shapes (§3), most-recent-completed-cycle for a given
      `now` + timezone, and the `hhmmssfff` range filter for listings
      (incl. the two-folder split for midnight-crossing windows).
- [ ] Dispatch mode: scan agents → skip unassigned → compute due cycle →
      compare `last_video.date` → async self-invoke build events; log a
      per-sweep summary (due/skipped counts).
- [ ] Build mode (§5): list with `< 10 KB` drop → threaded download to
      `/tmp/frames/` → SOI/EOI validation with `skipped_damaged` count →
      ffmpeg (legacy settings verbatim; stderr captured to logs) →
      upload with metadata → `last_video` UpdateItem; `/tmp` cleaned on
      entry; zero-frames guard (§5.7).
- [ ] Tests (fake S3/DynamoDB, ffmpeg stubbed; real `ffmpeg -version`
      integration test auto-skipped when ffmpeg is absent locally):
      cycle math table (default / same-day / midnight-crossing, around
      midnight boundaries), dispatch dedup (`last_video` current → no
      invoke; lagging → invoke; unassigned skipped), listing range
      filter + size drop, magic validation skip+count, zero-frames
      guard, `last_video` written only after upload.

### 3.4 Deploy

- [ ] `scripts/aws/deploy_video_builder.py` (idempotent, pattern of the
      other two): role per §6 (images read, videos write, agents table
      RW, self-invoke), Lambda (python3.12, **3008 MB / 900 s / 2048 MB
      /tmp**, ffmpeg layer attached, env: bucket/table/prefixes/stage
      tz fallback), EventBridge rule `rate(15 minutes)` →
      `{"mode": "dispatch"}` + invoke permission. **Deploy.**
- [ ] Verify a dispatch fires in CloudWatch (no builds due yet is fine).

### 3.5 First real builds (needs a completed cycle — 2026-08-14)

- [ ] Manual build first: invoke
      `{"mode":"build","location_id":"JAYANG3","date":"2026-08-13"}` for
      the partial-but-real first day → video lands at
      `videos/JAYANG3/JAYANG3-2026-08-13.mp4`, `skipped_damaged == 2`
      (the tagged test files), `last_video` recorded, duration ≈
      frames/30.
- [ ] Then hands-off: after local midnight, the sweep builds all four
      locations' cycles without intervention; verify all four videos +
      `last_video` records; check one video's content (download, play,
      spot-check duration and that day-spanning frames are ordered).
- [ ] Failure-path check: a location with `last_video` current is NOT
      rebuilt by subsequent sweeps (CloudWatch dispatch summaries).

### 3.6 Webapp: pool sync over subfolders + first viewing

- [ ] In the webapp repo: verify `listPoolObjects` handles
      `videos/{location_id}/…` keys (S3 listing is already recursive —
      confirm the key-parsing/prefix→Location matching works on the new
      basenames; fix if it assumes a flat folder). Its plan/docs updated
      per that repo's convention.
- [ ] `/manage/pool` sync → the new MP4s appear → assign to their
      JAYANG Posts (or via `init-pool-assign` prefixes) → **play a
      days-in-a-minute video in the webapp** — the full pipeline,
      capture → build → watch, end to end.

### 3.7 Exit criteria

- [ ] Suites green in this repo (and webapp if touched).
- [ ] Four consecutive-day videos appear with no human action (check the
      morning after 3.5).
- [ ] `last_video` visible for all four devices; damaged-skip counted
      exactly where expected; no orphan `/tmp` growth across warm runs
      (CloudWatch memory/storage metrics sane).
- [ ] Plans updated with `[x]` + deviations.

## Deviations / decisions during execution

(fill in as steps complete)
