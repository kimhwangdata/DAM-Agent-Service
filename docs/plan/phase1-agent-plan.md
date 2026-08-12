# Phase 1 — Capture Agent Implementation Plan

- **Status**: Not started
- **Date**: 2026-08-13
- **Based on**: `docs/design/01-agent.md` (all section refs below point there)
- **Goal**: the `dam-agent` systemd service runs on a bench Pi, capturing on
  the computed interval and uploading straight to
  `s3://knh-dam-store/images/…` with no local saves, with the HTTP
  mini-viewer working — plus a green Windows test suite.

## Non-goals

- No video builder (Phase 2), no upload-security layers beyond the scoped
  device credential, no multi-camera tuning UI (viewer is view-only, §6).

## Prerequisites (from Phase 0)

- [ ] Phase 0 complete: repo skeleton, `pyproject.toml` + pytest/ruff,
      `.env` stages, `knh-dam-store` bucket, scoped device IAM identity.
      (If Phase 0 is not finished, do the blocking items first.)

## Steps

### 1.1 Config & constants (§3, §7)

- [ ] `agent/config.py`: full settings per the §7 table (required keys fail
      loudly; defaults per table), plus derived `interval_s` from
      `VIDEO_MINUTES` using the legacy formula (§3).
- [ ] Tests: interval table (1→48 s, 2→24 s, 3→16 s), required-key
      validation, `CAPTURE_SIZE` parsing.

### 1.2 Camera interface (§2)

- [ ] `agent/camera.py`: `CameraSource` protocol
      (`start / capture_jpeg -> (bytes, datetime, metadata) / stop`).
- [ ] `FakeCamera`: generated image with timestamp text (Pillow, test-only
      dep) — works on Windows.
- [ ] `Picamera2Camera`: preview configuration, `BGR888`, `CAPTURE_SIZE`,
      JPEG to `BytesIO` (no files) — import guarded so the module loads on
      Windows without picamera2.
- [ ] Tests: FakeCamera returns decodable JPEG + aware timestamp.

### 1.3 Capture loop (§4)

- [ ] `agent/capture.py`: drift-compensated loop
      (`sleep(max(0, interval_s - capture_duration))`), timestamps in the
      configured IANA timezone, S3 key building
      (`images/{location_id}/{YYYY-MM-DD}/{hhmmssfff}.jpg`,
      `%H%M%S%f` → milliseconds), ULID per capture, non-blocking queue put.
- [ ] Tests: key formatting around midnight rollover (fixed tz fixtures),
      hhmmssfff truncation, pacing math (mocked clock — no real sleeps).

### 1.4 Upload queue & uploader (§2, §5)

- [ ] `agent/uploader.py`: bounded `queue.Queue(QUEUE_MAX)`; drop-oldest on
      overflow with counter; uploader thread doing boto3 `put_object`
      (`ContentType`, `x-amz-meta-*` per §5) with exponential backoff
      (1 s → 60 s cap) and re-queue-at-front on failure; counters
      (uploaded / dropped / failed / attempts).
- [ ] Tests: overflow drop-oldest, retry/backoff with mocked S3 client,
      counters, capture-side put never blocks.

### 1.5 HTTP viewer (§6)

- [ ] Latest-frame state: immutable `LatestFrame(jpeg, captured_at, seq)`
      swapped atomically by the capture loop; a `threading.Condition`
      notifies waiting streams on each new frame.
- [ ] `agent/viewer.py` on `ThreadingHTTPServer` (`VIEWER_PORT`,
      0 = disabled), endpoints:
      - [ ] `/stream.mjpg` — MJPEG push (`multipart/x-mixed-replace`):
            send current frame, then wait on the condition and push each
            new frame; handle client disconnects cleanly (broken pipe →
            end thread).
      - [ ] `/latest.jpg` — current frame with `ETag: "{seq}"` and
            `If-None-Match` → `304`.
      - [ ] `/` — static HTML: `<img src="/stream.mjpg">` + timestamp
            (no framework, no build step).
      - [ ] `/healthz` — JSON: last capture time, `seq`, queue depth,
            uploaded/dropped/failed counters, uptime, config summary
            (no secrets).
- [ ] Tests (stubbed frame state + condition, no real camera, no sleeps):
      ETag/304 behavior; MJPEG handler emits the boundary + current frame
      on connect and one new part per notify; disconnect mid-stream does
      not kill the server; `/healthz` shape.
- [ ] On-Pi check happens in 1.8 (browser shows the frame updating each
      interval without any page action).

### 1.6 Wiring, service, logging (§8, §9)

- [ ] `agent/main.py`: build camera from config (Picamera2 on Pi, fake via
      `STAGE=test`), start uploader + viewer threads, run capture loop;
      SIGTERM → stop camera, bounded uploader drain (~10 s), exit 0.
- [ ] Structured single-line logging to stdout per §9; startup config echo
      (no secrets).
- [ ] `systemd/dam-agent.service`: `Restart=always`,
      `After=network-online.target`, `EnvironmentFile` pointing at the stage
      env, runs as user `cskim`.
- [ ] End-to-end test on Windows: FakeCamera → queue → mocked S3, plus
      `STAGE=test python -m agent.main` smoke run (Ctrl+C clean exit).

### 1.7 Deploy & provisioning (CLAUDE.md deploy loop)

- [ ] `scripts/provision-pi.sh`: install `python3-picamera2` (needed on
      `dam-imx477-1`/Trixie; already present on the Bookworm Pis), create
      venv **with `--system-site-packages`** (picamera2 comes from apt),
      `pip install boto3 python-dotenv python-ulid`, install the systemd
      unit.
- [ ] `scripts/deploy.ps1` (+ `.sh`): rsync/scp `agent/` + unit file to a
      target Pi over SSH (key auth), `systemctl daemon-reload && restart
      dam-agent`, tail `journalctl` for a quick health check.
- [ ] Device `.env.dev` on the bench Pi (real scoped credentials — never
      committed).

### 1.8 On-Pi verification (phase exit criteria)

- [ ] `pytest` + `ruff` green on Windows.
- [ ] Deploy to one bench Pi (`dam-imx477-2`, Bookworm + HQ cam suggested);
      service active, `journalctl -u dam-agent` shows capture/upload lines
      at the 48 s cadence, no errors.
- [ ] Objects appear in
      `s3://knh-dam-store/images/{location_id}/{today}/` with millisecond
      filenames and `x-amz-meta-*` metadata; count matches elapsed time.
- [ ] Viewer: `http://<pi>.local:8080/` shows the latest frame refreshing
      each interval; `/healthz` counters move.
- [ ] Resilience check: disconnect the Pi's network ~2 min → frames queue,
      reconnect → queue drains, drop counter still 0; `systemctl stop`
      exits clean within the drain window.
- [ ] Run through a local midnight (or fake the clock): keys roll to the
      new `{YYYY-MM-DD}` folder without a restart.
- [ ] Update this plan's checkboxes + deviations; record ADR-0001
      (picamera2) in the ADR index if not already done in Phase 0.

## Deviations / decisions during execution

(fill in as steps complete)
