# Phase 1 — Capture Agent Implementation Plan

- **Status**: In progress (1.1–1.7 done; 1.8 verified except two deferred
  checks — see §1.8). **dam-agent is live on dam-imx477-2, uploading.**
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

- [x] `FrameStore`: immutable `LatestFrame(jpeg, captured_at, seq)` behind
      a `threading.Condition`; `publish()` notifies all waiting streams,
      `wait_newer_than(seq, timeout)` lets handlers block without polling.
- [x] `agent/viewer.py` on `ThreadingHTTPServer` (daemon threads;
      `Viewer(port=0)` → ephemeral port for tests):
      - [x] `/stream.mjpg` — MJPEG push with per-part `Content-Length`;
            current frame sent on connect, new frames pushed on notify;
            1 s wait slices so threads notice shutdown; broken pipe /
            reset handled without touching the server.
      - [x] `/latest.jpg` — `ETag: "{seq}"`, `If-None-Match` → 304; 503
            before the first frame.
      - [x] `/` — static HTML page with the stream image + ~5 lines of JS
            updating the timestamp from `/healthz`.
      - [x] `/healthz` — JSON merging frame info (`last_capture`, `seq`)
            with an injected `status_fn()` (queue depth/counters wired in
            1.6).
- [x] Tests (6, real server on 127.0.0.1 ephemeral port): 503-before-frame,
      ETag/304/new-frame-after-304, `/healthz` shape, page embeds stream,
      404, and a live MJPEG test — current frame on connect, pushed second
      frame with no client action, then mid-stream disconnect with the
      server still serving. Suite: 47 green, ruff clean.
- [ ] On-Pi check happens in 1.8 (browser shows the frame updating each
      interval without any page action).

### 1.6 Wiring, service, logging (§8, §9)

- [x] `agent/main.py`: `Agent` class wiring camera (Fake on `STAGE=test`,
      Picamera2 otherwise) → sink (FrameStore publish + uploader submit) →
      viewer (`status()` feeds `/healthz`, no secrets). The capture loop's
      sleep is the stop `Event.wait`, so SIGTERM/SIGINT interrupts a 48 s
      sleep instantly → camera stop, viewer stop, bounded uploader drain.
- [x] Structured single-line logging to stdout; startup config echo.
- [x] `systemd/dam-agent.service`: `Restart=always`, network-online,
      user `cskim`, `WorkingDirectory=/opt/dam-agent` (stage env read from
      there; `Environment=STAGE=dev`), `TimeoutStopSec=20` > drain.
- [x] End-to-end test on Windows (4 tests): FakeCamera → queue → mocked
      sign+PUT (uploaded bytes verified), `request_stop()` ends `run()`
      within 5 s. Smoke run `STAGE=test python -m agent.main`: starts,
      captures at cadence, uploader retries the unreachable test signer
      with 1→2→4 s backoff. (Windows CTRL_BREAK is a hard kill — clean
      SIGTERM shutdown is unit-tested; real check on the Pi in 1.8.)
      Suite: 51 green, ruff clean.

### 1.7 Deploy & provisioning (CLAUDE.md deploy loop)

- [x] `scripts/provision-pi.sh`: apt `python3-picamera2` + `python3-venv`,
      `/opt/dam-agent` with a `--system-site-packages` venv, pip
      `python-dotenv python-ulid typing-extensions` (typing-extensions:
      python-ulid needs it on 3.11; **no boto3 on devices** — ADR-0003).
      Executed on dam-imx477-2.
- [x] `scripts/deploy.sh` (+ thin `deploy.ps1` wrapper): scp `agent/` +
      unit → install unit, daemon-reload, enable, restart, status +
      journal tail. Executed against dam-imx477-2.
- [x] `/opt/dam-agent/.env.dev` on the Pi (chmod 600): `LOCATION_ID=TEST`
      (bench uses the TEST location during dev), `DEVICE_ID=dam-imx477-2`,
      signer URL + freshly issued token (token transited only via SSH;
      never stored on the dev machine or in git).

### 1.8 On-Pi verification (phase exit criteria)

- [x] `pytest` (51) + `ruff` green on Windows.
- [x] Deployed to `dam-imx477-2` (Bookworm + HQ cam, Wi-Fi `.109`):
      service **active**, journal shows capture lines at exactly 48 s
      (09:36:01 → 09:36:49 → 09:37:37) and `uploaded … attempt=1` for
      every frame (first upload 8 s — Lambda cold start; then ~1.5 s).
- [x] Objects in `s3://knh-dam-store/images/TEST/2026-08-13/` with
      millisecond filenames, `image/jpeg`, and full `x-amz-meta-*`
      (ulid / device-id / captured-utc / timezone); count matched uptime.
- [x] Viewer from the dev machine: `/latest.jpg` HTTP 200 — a real
      1280×720 capture (city view); `/healthz` counters advance
      (`uploaded: 3→4`, `queue_depth: 0`, `dropped: 0`).
- [x] `systemctl stop` → "dam-agent stopped {uploaded: 4 …}",
      `Deactivated successfully`, `ExecMainStatus=0`; restart → active
      and capturing again.
- [ ] **Deferred**: network-disconnect resilience (the bench Pi is
      Wi-Fi-only — cutting wlan0 remotely risks stranding it; do when
      physically at the device or via wired connection). Queue/backoff
      behavior is covered by unit tests.
- [ ] **Deferred**: real midnight rollover — the service is left running,
      so tonight's local midnight verifies it naturally (unit-tested
      already); check `images/TEST/{tomorrow}/` exists next session.
- [x] Plan checkboxes + deviations updated; ADR-0001 recorded in Phase 0.

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
