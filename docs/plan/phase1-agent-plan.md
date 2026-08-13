# Phase 1 — Capture Agent Implementation Plan

- **Status**: In progress (1.1–1.4 done; signer live on AWS)
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

- [x] Phase 0 complete: repo skeleton, `pyproject.toml` + pytest/ruff,
      `.env` stages, `knh-dam-store`/`knh-dam-backup` live with lifecycle +
      Glacier replication. (Per-device IAM identity cancelled by ADR-0003 —
      the upload-signer in 1.4 replaces it.)

## Steps

### 1.1 Config & constants (§3, §7)

- [x] `agent/config.py`: full settings per the §7 table — required keys now
      include `UPLOAD_SIGNER_URL` + `DEVICE_TOKEN` (ADR-0003); legacy-formula
      constants (`FPS=30`, `FRAME_PER_MINUTE`, `CAPTURE_DURATION_SECONDS`)
      and derived `Settings.interval_s`; `VIDEO_MINUTES >= 1` validated.
- [x] Tests: interval table (1→48 s, 2→24 s, 3→16 s parametrized),
      `VIDEO_MINUTES=0` rejected, required-key validation, `CAPTURE_SIZE`
      parsing — 10 tests green, ruff clean; smoke run shows
      `interval_s=48` on the test stage.

### 1.2 Camera interface (§2)

- [x] `agent/camera.py`: `CameraSource` protocol
      (`start / capture_jpeg -> (bytes, datetime, metadata) / stop`).
- [x] `FakeCamera`: generated image with timestamp text (Pillow, dev-only,
      imported lazily) — works on Windows.
- [x] `Picamera2Camera`: preview configuration, `BGR888`, size from config,
      JPEG to `BytesIO` via `capture_request().save(...)` (no files);
      picamera2 imported inside `start()` → module loads on Windows and
      raises a clean `CameraError` if unavailable.
- [x] Tests: FakeCamera JPEG decodable (format/size), timezone-aware
      timestamp, use-before-start errors, Windows import guard —
      13 tests green, ruff clean.

### 1.3 Capture loop (§4)

- [x] `agent/capture.py`: `CaptureItem` (jpeg, aware local timestamp, ULID,
      key, camera metadata), pure `format_hhmmssfff`/`build_key`, and
      `CaptureLoop` with drift-compensated pacing
      (`sleep(max(0, interval_s - dur))`), injectable clock/sleep, and a
      capture-failure guard (loop keeps pacing after an exception).
- [x] Tests: hhmmssfff truncation + zero-padding, key shape, midnight
      rollover to the new day folder, ULID uniqueness (26 chars), sink
      receives the item, pacing 48−3→45 s / never negative, failure
      doesn't kill the loop — 22 tests green, ruff clean.

### 1.4 Upload path — signer + queue + uploader (§2, §5, ADR-0003)

Cloud side first (the agent needs it to upload):

- [x] `upload-signer/handler.py`: pure `handle()` (clients injected) +
      `lambda_handler`; token-hash lookup in `knh-dam-devices`, prefix
      derived from the token identity, strict date/filename/content-type/
      metadata validation, presigned PUT (60 s TTL). 401/403/400/404/405.
- [x] Deploy script `scripts/aws/deploy_upload_signer.py` (idempotent):
      table + execution role + Lambda; **deployed and live** at
      `https://39o7oq9hjg.execute-api.ap-northeast-2.amazonaws.com`.
- [x] `scripts/aws/issue_device_token.py`: issue (revokes previous token
      for the location), plus `--disable` kill-switch.
- [x] Signer tests: 13 (happy path, prefix-from-token, 401/403,
      7 bad-shape cases incl. path traversal, path/method errors).
- [x] **Live smoke test passed**: sign 200 → HTTPS PUT 200 → object in S3
      with `x-amz-meta-*` → bad token 401, bad filename 400 → cleaned up.

Device side:

- [x] `agent/uploader.py`: bounded queue with drop-oldest + counter,
      uploader thread doing sign → PUT via stdlib `urllib` (injectable
      `urlopen`/`sleep`), exponential backoff 1→60 s, fresh presign per
      retry (head-item retry-in-place ≡ re-queue-at-front with one
      thread), counters + `queue_depth` for `/healthz`, bounded drain in
      `stop()`.
- [x] Tests: 6 (overflow drop-oldest/never blocks, sign body + PUT header
      contents, sign-failure backoff sequence, expired-PUT → fresh
      presign, backoff cap at 60 s, stop aborts retry). Suite: 41 green.

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
- [ ] Device `.env.dev` on the bench Pi: `UPLOAD_SIGNER_URL` + its issued
      `DEVICE_TOKEN` (no AWS credentials — ADR-0003; never committed).

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

- 1.2: Windows needs the `tzdata` package for `zoneinfo` — added to deps
  with a `platform_system == 'Windows'` marker (Pi uses system tzdata).
- 1.4: a public Lambda **function URL** stubbornly returned the edge-level
  403 "Function URL authorization issues" despite a correct resource
  policy (AuthType NONE, `lambda:InvokeFunctionUrl`, principal `*`) —
  direct invoke worked, recreation didn't help. Pivoted to **API Gateway
  HTTP API** (payload v2 = same event shape; handler unchanged); worked
  immediately. Function URL config removed.
- 1.4: signer smoke-tested live with a real `TEST` token; test object
  deleted afterwards. The `TEST` device row remains in `knh-dam-devices`
  (its plaintext token was not retained; re-issue before reusing).
