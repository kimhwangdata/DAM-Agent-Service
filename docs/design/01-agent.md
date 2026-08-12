# 01 — Capture Agent Design (rpi-camera-agent)

- **Status**: Draft
- **Date**: 2026-08-13
- **Based on**: `docs/design/00-architecture.md` §3; legacy
  `legacy-rpi-camera/allsky-service/capture-24h.py` (capture loop, interval
  math) and `legacy-rpi-camera/allsky-camera/camera_viewer.py` (viewer idea —
  replaced by something much simpler).

## 1. Scope & key decisions

The agent captures still images all day and **uploads each one directly to
S3 — no local image files are ever written** (decision; differs from the
legacy design, which saved a day of files to disk and uploaded later).

| Decision | Choice |
| -------- | ------ |
| Capture backend | **picamera2** (legacy-proven; both fleet camera stacks verified working with it; in-memory JPEG without temp files). This resolves ADR-0001. |
| Image path | camera → in-memory JPEG → **bounded in-memory upload queue** → S3. No disk writes. |
| Interval | legacy formula, parameterized by **minutes of video per 24 h** (default 1) — §3 |
| Viewer | built-in **HTTP mini-viewer** serving the latest captured frame — §6 (the Qt `camera_viewer.py` is not ported) |
| Durability trade-off | if power dies or an outage outlives the queue, those frames are lost — **accepted** (a daily video tolerates missing frames; simplicity wins). The queue + retries cover ordinary network blips. |

## 2. Component overview

```
             agent process (one systemd service: dam-agent)
┌───────────────────────────────────────────────────────────────────┐
│  capture loop (main thread)                                       │
│   every interval_s:                                               │
│     jpeg, ts = camera.capture_jpeg()   ── in memory only          │
│     queue.put(CaptureItem(jpeg, ts, ulid))                        │
│                                                                   │
│  uploader (worker thread)              viewer (daemon thread)     │
│   item = queue.get()                    GET /          HTML page  │
│   put_object → s3://knh-dam-store/      GET /latest.jpg last frame│
│     images/{location_id}/               GET /healthz   status JSON│
│       {YYYY-MM-DD}/{hhmmssfff}.jpg                                │
│   retry w/ backoff on failure                                     │
└───────────────────────────────────────────────────────────────────┘
```

- **camera** — small interface (`CameraSource`): `start()`,
  `capture_jpeg() -> (bytes, datetime, metadata)`, `stop()`.
  Implementations: `Picamera2Camera` (Pi, preview configuration `BGR888` at
  the configured size, as in `capture-24h.py`) and `FakeCamera`
  (Windows/tests: generated image with the timestamp drawn in).
- **capture loop** — owns pacing and day rollover (§4).
- **upload queue** — `queue.Queue(maxsize=QUEUE_MAX)` of in-memory items.
  When full, **drop the oldest** item and count it (logged); never block the
  capture loop.
- **uploader** — boto3 `put_object` with capture metadata as S3 object
  metadata (ULID, device id, UTC timestamp, timezone). Exponential backoff
  retry (e.g. 1 s → 2 s → … capped at 60 s) on failure, then re-queue at the
  front; per-item attempt count in logs.
- **viewer** — §6. Shares the last captured frame via a single in-memory
  reference; no camera access of its own.

## 3. Capture interval (legacy method, parameterized)

Same computation as `capture-24h.py`:

```python
FPS = 30                            # matches the builder's -framerate 30
FRAME_PER_MINUTE = 60 * FPS         # 1800
CAPTURE_DURATION_SECONDS = 24 * 60 * 60

interval_s = CAPTURE_DURATION_SECONDS // (FRAME_PER_MINUTE * VIDEO_MINUTES)
```

**`VIDEO_MINUTES`** (config; legacy `APP_REC_SCALE`) = how many minutes of
video one 24-hour day becomes. **Default 1.**

| VIDEO_MINUTES | interval_s | frames/day | video length @30fps |
| ------------- | ---------- | ---------- | ------------------- |
| 1 (default)   | 48         | 1,800      | 1 min               |
| 2             | 24         | 3,600      | 2 min               |
| 3             | 16         | 5,400      | 3 min               |

(Integer division, as in legacy — values of `VIDEO_MINUTES` that don't
divide evenly floor the interval and slightly overshoot the frame count;
acceptable.)

## 4. Capture loop (from `capture-24h.py`, simplified)

Per iteration, exactly like legacy: note `t0`, capture, then
`sleep(max(0, interval_s - capture_duration))` so capture time doesn't drift
the cadence. Differences from legacy:

- **No output directory / files** — the JPEG goes to a `BytesIO`, then to
  the queue. (Legacy's dir creation, `sudo chown/chmod`, and the separate
  upload service disappear.)
- **Timestamps**: `now()` in the device's configured IANA timezone. Key parts
  `{YYYY-MM-DD}` and `{hhmmssfff}` come from
  `strftime("%Y-%m-%d")` / `strftime("%H%M%S%f")[:-3]` (milliseconds), per
  architecture §7.
- **Day rollover**: legacy looped `while today_str == date-part` and let an
  outer loop restart the day. Same effect here, folded into one loop: the
  S3 key's date folder simply follows the capture-time date — nothing else
  needs to happen at midnight (the video builder owns day handling).
- **Legacy sleep window dropped**: `capture-24h.py` idled 00:00–00:10 so the
  Pi could build/upload yesterday's video. Building moved to Lambda, so the
  agent captures around the clock — those ~12 frames/day come back for free.
- **Camera settings**: keep legacy defaults — preview configuration,
  `BGR888`, configurable capture size (`CAPTURE_SIZE`, default `1280,720`),
  auto exposure/AWB on. Per-camera tuning stays out of the agent (it lives
  in the camera stack; see `docs/reference/camera-info.md`).

## 5. S3 upload

- Key: `images/{location_id}/{YYYY-MM-DD}/{hhmmssfff}.jpg` (device-local
  date/time, architecture §7).
- `ContentType: image/jpeg`; metadata as **S3 object metadata**
  (`x-amz-meta-ulid`, `-device-id`, `-captured-utc`, `-timezone`) — no `.json`
  sidecar in v1 (simpler; the sidecar remains optional in the architecture).
- Credentials: the device's scoped IAM identity from `.env.{STAGE}`
  (PutObject on `images/{location_id}/*` only).

## 6. Viewer — "what is the camera seeing right now?"

Purpose of legacy `camera_viewer.py` (an 800-line Qt6-tab application):
aim/focus/verify the camera. The fleet is headless (Lite OS, no desktop) and
the agent owns the camera exclusively, so a GUI app can't run alongside the
service. Replacement — **a ~100-line HTTP viewer inside the agent**:

**Latest-frame state**: there is no file. After every capture the loop
atomically swaps one shared immutable reference:
`LatestFrame(jpeg: bytes, captured_at: datetime, seq: int)` — `seq`
increments per capture and doubles as the change token / ETag. Readers can
never observe a torn frame; exactly one frame (~300 KB) is retained.

Endpoints (stdlib `ThreadingHTTPServer`, daemon thread, zero new deps):

- `GET /stream.mjpg` — **live view without any refresh**: an MJPEG stream
  (`multipart/x-mixed-replace`). The handler sends the current frame
  immediately, then blocks on a per-connection event; each new capture
  wakes all streams and pushes the new JPEG part. The browser's `<img>`
  updates natively the moment a frame arrives — no JavaScript, no reload.
  One thread per connected viewer (LAN aiming/checking use → a handful at
  most).
- `GET /latest.jpg` — single most recent frame, served with `ETag: "{seq}"`
  (`If-None-Match` → `304`), for scripts/thumbnails/one-shot checks.
- `GET /` — minimal static HTML: `<img src="/stream.mjpg">` + capture
  timestamp (updated by ~5 lines of inline JS polling `/healthz`, or left
  static — the image itself needs no JS).
- `GET /healthz` — JSON status: last capture time + `seq`, queue depth,
  uploaded / dropped / failed counters, uptime, config summary (no
  secrets). This also satisfies the architecture's "simple operational
  visibility" endpoint.

**No web framework** — deliberately. React/Next.js would add a runtime and
build step to a 1 GB Pi for a one-image page (a fleet dashboard, if ever
wanted, belongs in the days-in-a-minute webapp reading `/healthz`).
Playwright is a browser-testing tool, not a server, and is not used here.
The whole viewer is ~100 lines of stdlib Python.

- Explicitly **not** ported from the Qt app: exposure/gain/AWB tabs, sensor
  mode selection, pan/zoom, DNG capture. Camera tuning, when needed, is done
  with `rpicam-still` on the device outside the service.

## 7. Configuration (all via `.env.{STAGE}`, read once in `agent/config.py`)

| Key | Default | Meaning |
| --- | ------- | ------- |
| `STAGE` | — (required) | selects `.env.{STAGE}` |
| `LOCATION_ID` | — (required) | S3 prefix + video naming identity |
| `DEVICE_ID` | — (required) | stable device identity (metadata) |
| `TIMEZONE` | — (required) | IANA tz; drives all local dates/times |
| `S3_BUCKET` | `knh-dam-store` | target bucket |
| `S3_IMAGE_PREFIX` | `images/` | first-level folder |
| `VIDEO_MINUTES` | `1` | minutes of video per 24 h (§3) |
| `CAPTURE_SIZE` | `1280,720` | capture resolution `W,H` |
| `QUEUE_MAX` | `64` | in-memory queue bound (≈50 min at default interval, ~30 MB at 1280×720) |
| `VIEWER_PORT` | `8080` | HTTP viewer/health port; `0` = disabled |
| AWS credentials | — | scoped device identity (env/instance standard chain) |

## 8. Module layout & runtime

```
agent/
  config.py     settings from .env.{STAGE} (only source of constants)
  camera.py     CameraSource interface + Picamera2Camera + FakeCamera
  capture.py    capture loop: interval math, timestamps, key building
  uploader.py   queue + uploader thread (boto3, backoff, counters)
  viewer.py     http.server thread: /, /latest.jpg, /healthz
  main.py       wiring, signal handling (clean SIGTERM stop for systemd)
systemd/
  dam-agent.service   Restart=always, After=network-online.target
```

Threads: main (capture loop), uploader, viewer. Shutdown: SIGTERM → stop
camera, let the uploader drain briefly (bounded, e.g. 10 s), exit; anything
still queued is lost (accepted, §1).

## 9. Logging

Structured single-line logs (stdout → journald): per capture (key, capture
duration, queue depth), per upload (key, attempt, duration), per drop
(count), startup config echo (no secrets). `journalctl -u dam-agent` is the
primary debugging surface, as required by CLAUDE.md.

## 10. Testing (Windows, pytest)

- Interval math table (§3) and key/timestamp formatting (fixed tz fixtures).
- Queue overflow: oldest dropped, counters correct, capture never blocks.
- Uploader retry/backoff against a mocked S3 client (and failure → requeue).
- `FakeCamera` end-to-end: capture → queue → mocked upload, byte-for-byte.
- Viewer handlers with a stubbed latest-frame reference.
On-Pi verification (per CLAUDE.md): deploy, `journalctl`, open the viewer,
confirm objects appear under the correct S3 day folder.

## 11. Consequences for existing docs

- `00-architecture.md` §3 / §9 said "local **disk** queue" — superseded by
  the in-memory, no-local-save decision (§1); updated alongside this doc.
- CLAUDE.md error-handling wording updated the same way.
- ADR-0001 (capture backend) is resolved as picamera2 by §1; recorded in the
  ADR index when Phase 0 creates it.
