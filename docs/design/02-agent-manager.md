# 02 — Agent Manager Design (fleet operations)

- **Status**: Draft (v2 — signer-centric control, two access groups)
- **Date**: 2026-08-13
- **Based on**: `00-architecture.md` (§3, §6), `01-agent.md` (viewer §6,
  upload §5, ADR-0003 token model)
- **Scope**: the operator tool for the device fleet — device information in
  DynamoDB, remote start/stop of capturing, `location_id` assignment,
  status visibility, and per-device access info (SSH where possible).

## 1. Purpose

1. keep **all agent-related information in DynamoDB**: identity, location
   assignment, status, and how the device can be reached (if at all)
2. **start / stop capturing per device** — enforced in the cloud
3. **assign `location_id` to a device** (and change it)
4. see each device's status and its latest camera frame
5. know, per device, whether direct control (SSH) is possible, and with
   what address
6. surface **malfunction states**: a device that stops sending images, a
   device sending damaged files, and (reserved for later) a device
   sending disallowed content — each visible as a health state in the
   manager
7. **thermal protection**: temperature reported with every image submit;
   graduated automatic response (warn → pause → last-resort shutdown),
   with the condition visible in the manager (§5.2)
8. record **hardware info** per device (Pi model, camera, lens) — partly
   auto-reported, partly operator-maintained
9. **video window per day**: capture runs at the given interval **all the
   time**; the operator-set start/end times select which frames the daily
   video is built from (default 00:00→00:00 = the whole day; start may be
   later than end, crossing midnight — §5.3)

## 2. Control model — what can actually be managed

Remote agents sit behind NAT: the cloud can never connect **to** a device.
The only inbound-free touchpoints we have are:

1. **DynamoDB** — the operator edits device records there (via the manager
   API/UI);
2. **the uploader Lambda (signer)** — the one endpoint every device calls
   anyway, once per capture (~48 s). It becomes the control point:
   on every `/sign` it checks the device's record and answers
   **go on** (presigned URL) or **skip** (`paused`), and at the same time
   records the device's reported status.

So start/stop is **enforced server-side**: a stopped device's sign
requests are answered `paused`, the agent skips that frame (no retry, no
backoff spam, counted as `skipped`), and nothing reaches S3 — even a
tampered device cannot upload while paused. Effect latency ≤ one capture
interval. No separate heartbeat channel, no desired-state polling loop.

### Two device groups

| | **ssh-accessible** (development / same LAN — like the bench today) | **ssh-not-accessible** (deployed remote, behind NAT) |
| --- | --- | --- |
| Info in DynamoDB | full record incl. `access` (IP, SSH user) | full record, `access.ssh_accessible: false` |
| Start/stop capture | signer gate **and** direct `systemctl` over SSH | signer gate only |
| Deploy / config / restart | `scripts/deploy.sh <ip>`, edit `.env`, `systemctl` over SSH | not possible remotely — device must be prepared before shipping; config changes need the signer channel (§10) or a site visit |
| Viewer | live MJPEG `http://<ip>:8080/` (linked from the UI) + S3 latest frame | S3 latest frame only (what the agent last uploaded) |
| Kill-switch | token revoke + SSH stop | token revoke (uploads die instantly) |

The manager UI shows the group per device and offers only the operations
that group supports. The manager itself never SSHes — for the accessible
group it surfaces the address and the existing scripts do the work, same
as today.

## 3. Components

```
┌ Raspberry Pi ─────────────────────────┐
│ dam-agent                             │
│  capture loop ── uploader             │   POST /sign  (status piggybacked)
│  viewer :8080 (LAN group only) ───────────────┐  ▲ presigned URL | paused
└───────────────────────────────────────┘       ▼  │
                                        ┌──────────────────────────────┐
   operator browser ──▶ days-in-a-minute webapp (/manage/devices UI +
                        /api/v1/devices* routes, admin RBAC)  ──┐
                                        ┌──────────────────────────────┐
                                        │ agent-api Lambda (this repo) │
                                        │  /sign      (device, gate +  │
                                        │              status collect) │
                                        └──────┬───────────────────────┘
                                               │            ◀───────────┘
                                               ▼   (webapp reads/writes the
                                                    same tables directly)
                                               │
                                DynamoDB: knh-dam-devices (tokens)
                                          knh-dam-agents  (fleet records)
                                S3: latest-frame presigned GETs

   S3 ObjectCreated on images/… ──▶ upload-monitor Lambda ──▶ knh-dam-agents
   (server-observed truth:           - last_object_at, sizes    (health.*)
    every upload that actually       - damaged-file check
    landed, §5.1)                    - content-moderation hook (reserved)
```

`agent-manager/` in this repo holds the Lambda (the upload-signer handler
grows into it — same stack, same deploy pattern) and the static UI.

## 4. Data model (DynamoDB)

**`knh-dam-devices`** (existing) — auth only: `token_hash` (PK),
`enabled`, `created_at`, plus **`device_id`** (a token belongs to a
device; location comes from the agents table — §6).

**`knh-dam-agents`** (new) — one record per device, PK `device_id`:

```
device_id: "dam-imx477-2"                      (PK; from the device env)
assignment:
  location_id: "DIO21" | null                  ← operator-assigned
  assigned_at: ISO-UTC
control:
  capturing: true | false                      ← operator start/stop
                                                 (enforced by /sign)
  video_window_start: "00:00"                  ← daily VIDEO window (§5.3):
  video_window_end:   "00:00"                    which frames the builder
                                                 uses; capture itself never
                                                 stops. Device-local time;
                                                 equal values = full 24 h;
                                                 start > end crosses midnight.
                                                 RESERVED nominal values
                                                 "dawn" | "dusk" (§5.3)
hardware:                                      ← operator-maintained
  lens_type: "M12 wide" | null                 (not machine-detectable)
  note: "HQ cam, CS mount"                     (free text)
access:                                        ← operator-maintained
  ssh_accessible: true | false                 (the two groups, §2)
  ip: "192.168.70.109" | null                  (last known / static)
  ssh_user: "cskim" | null
  note: "bench, KNHPL wifi"                    (free text)
reported:                                      ← updated by every /sign
  at: ISO-UTC                                  ("last seen"; self-reported)
  local_ip, hostname, agent_version
  seq / uploaded / dropped / skipped / failed_attempts / queue_depth
  interval_s, capture_size, timezone
  pi_model: "Raspberry Pi 3 Model B Rev 1.2"   (/proc/device-tree/model)
  camera: "imx477"                             (picamera2 properties)
  temp_c: 61.2                                 (SoC, per submit — §5.2)
  throttled: "0x0"                             (vcgencmd get_throttled)
  thermal_state: "ok" | "warn" | "paused"      (agent's own thermal state)
health:                                        ← server-observed (§5.1)
  state: "ok" | "offline" | "stale" |          derived; shown as the UI badge
         "suspect" | "quarantined"
  last_object_at: ISO-UTC                      last upload that actually
                                               landed in S3 (upload-monitor)
  damaged_recent: 0                            damaged files in the last 24 h
  last_damaged_key: string | null
  content_flag: null | "pending-review"        ← RESERVED for content
                | "flagged"                      moderation (porn/illegal;
  content_flag_detail: string | null             architecture §6 layer 4)
first_seen: ISO-UTC                            (auto-registered at first /sign)
```

- `reported` costs nothing extra: the agent includes its `status()`
  payload in each `/sign` body and the Lambda upserts it — the sign call
  **is** the heartbeat. A paused device keeps calling `/sign` (and being
  told `paused`), so status keeps flowing while stopped.
- Offline = `now − reported.at` > 3× the device's interval.
- `access` is operator-maintained truth (which group, where it lives);
  `reported.local_ip` is what the device itself last claimed — the UI
  shows both and flags mismatches.

## 5. Device-facing API (unchanged endpoint, extended contract)

`POST /sign` — body as today (token, date, filename, content_type,
metadata) **plus** `device_id` and a `status` object (the agent's healthz
payload). The Lambda:

1. verifies the token (401/403 as today; 403 disabled = kill-switch)
2. upserts `reported` in `knh-dam-agents` (auto-registers on first call)
3. resolves `token → device_id → assignment.location_id`
   - unassigned → `409 {"error": "unassigned"}` — agent skips the frame
4. checks `control.capturing`
   - `false` → `200 {"status": "paused"}` — agent skips the frame,
     increments `skipped`, no retry. (The video window §5.3 does NOT
     gate uploads — capture and upload run around the clock.)
5. otherwise → `200 {"status": "ok", "url": …, "key": …}` as today

Agent-side handling of `paused`/`unassigned` is a **skip**, not a
failure: the frame is dropped deliberately, counted, and the loop simply
waits for the next interval (still calling /sign each time so status and
resume stay live).

### 5.1 Malfunction detection & content safety (upload-monitor)

`/sign` only signs — it never sees whether the PUT landed or what bytes
were sent. Server-observed truth comes from a small **upload-monitor
Lambda** subscribed to `s3:ObjectCreated` on `knh-dam-store/images/*`. On
each event it resolves the key's `location_id` back to the device and
updates `health` in `knh-dam-agents`:

1. **No images (silent device)** — every landed object refreshes
   `health.last_object_at`. Health derivation (in the manager, at read
   time — no cron needed):
   - `offline` — no `/sign` calls at all (`reported.at` aged out): device
     dead, powered off, or no network.
   - `stale` — the device IS calling `/sign` (or was told to capture) but
     no object has landed for > 3 intervals: camera failure, capture
     exceptions, or a broken upload path. This is the "malfunctioning but
     alive" state that self-reported counters alone could hide.
2. **Damaged files** — the monitor sanity-checks each object cheaply:
   JPEG magic bytes (`FF D8 … FF D9`), size within bounds (e.g.
   10 KB – 5 MB for the configured resolutions). Failures increment
   `health.damaged_recent` (24 h window), record `last_damaged_key`, and
   tag the S3 object (`damaged=true`) so the video builder can skip it
   without re-checking. State `suspect` when `damaged_recent` crosses a
   threshold (e.g. ≥ 5/24 h — a failing sensor or SD/RAM corruption).
3. **Disallowed content (RESERVED)** — `health.content_flag` +
   `content_flag_detail` exist from day one but nothing sets them in v1.
   The upload-monitor is the designated future hook for automated
   moderation (architecture §6 layer 4, e.g. Rekognition moderation
   labels on a sample of frames): a hit sets `content_flag =
   "pending-review"` / `"flagged"`, drives state `quarantined`, and MAY
   auto-set `control.capturing = false` (policy decision deferred —
   §10). Operator review buttons in the UI clear or confirm the flag.

State precedence: `quarantined` > `suspect` > `stale` > `offline` > `ok`.
Any non-`ok` state is prominent in the fleet list; `quarantined` also
hides nothing — the evidence keys stay recorded for review.

### 5.2 Thermal protection

The SoC temperature (`/sys/class/thermal/thermal_zone0/temp`, no sudo
needed) is read **at every image submit** and included in the `/sign`
status, together with `vcgencmd get_throttled` flags. Facts the policy is
built on: the Pi SoC is rated to 85 °C and the firmware already
soft-throttles at 80 °C / hard-throttles at 85 °C — heat slows a Pi, it
does not destroy it. The real risk for this fleet is different: **a
shut-down NAT-group device cannot be restarted remotely** — an outdoor
enclosure on a summer afternoon must not turn into a site visit.

Graduated response (agent-side, thresholds from device env, defaults):

| Threshold | Action |
| --------- | ------ |
| `TEMP_WARN` = **70 °C** | `thermal_state: "warn"` in reports → health badge in the manager; capture continues |
| `TEMP_PAUSE` = **75 °C** | agent **pauses capturing itself** (camera load drops, device stays online and reporting); auto-**resume at ≤ 70 °C** (5 °C hysteresis so it doesn't flap) |
| `TEMP_SHUTDOWN` = **85 °C**, 3 consecutive submits | last-resort OS shutdown (`poweroff`), preceded by a final `/sign` status carrying `event: "thermal-shutdown"` so the manager records why the device went dark. **Default: enabled only for ssh-accessible devices**; remote devices default to pause-only (firmware throttling protects the silicon; shutdown would strand them) |

The requested 75 °C limit is adopted — as the **pause** threshold, not
shutdown: same protective effect (capture load stops), none of the
stranding risk. Every thermal event is visible in the manager via
`reported.temp_c` / `thermal_state`, and a paused-by-heat device shows a
distinct badge (it is not operator-paused).

### 5.3 Daily video window (frame selection, not a capture gate)

**Capture and upload run at the configured interval around the clock,
regardless of the window.** `control.video_window_start` / `_end`
(device-local `HH:MM`) tell the **video builder** which frame range makes
that day's video:

- `start == end` (default `00:00`/`00:00`) — the whole day, exactly
  today's behavior.
- `start < end` (e.g. `06:00`→`18:00`) — the builder selects frames from
  one day-folder where `hhmmssfff` falls inside the window.
- `start > end` (e.g. `18:00`→`06:00`, **crossing midnight** — night-sky
  use) — the video for day `D` spans `D start` → `D+1 end`: the builder
  reads the tail of folder `{D}` (≥ start) plus the head of folder
  `{D+1}` (< end). Time-of-day filenames make both selections a simple
  prefix listing + lexicographic filter.

Consequences for the builder (Phase 3 design details, recorded here as
the contract):

- **Trigger**: the build for day `D` fires shortly after the **window
  end** in device-local time (which is on `D+1` when the window crosses
  midnight) — generalizing the "after local midnight" rule, which remains
  the default-window case.
- **Naming**: the video keeps `{LOCATION_ID}-{YYYY-MM-DD}.mp4` with the
  **window-start date** as its label.
- **Length**: capture density is constant, so a shorter window yields a
  proportionally shorter video (12 h ≈ 30 s at `VIDEO_MINUTES=1`).
  Whether the interval should adapt so any window still renders the full
  `VIDEO_MINUTES` is an open question (§10.1c).
- Frames outside the window are still captured and uploaded (they age out
  via the 30-day lifecycle like all stills) — the window can be widened
  retroactively for up to 30 days.
- **Nominal values `"dawn"` / `"dusk"` (RESERVED, deferred)**: the window
  bounds will also accept `"dawn"` and `"dusk"` (e.g. `dawn`→`dusk` for
  daylight-only videos, `dusk`→`dawn` for night skies). Because sun times
  shift every day, the builder resolves them **per build date** from the
  location's geographic position — obtainable from the Post's
  city/address (geocoded once into lat/lon on the Post record) or roughly
  from its timezone. Sunrise/sunset is a local solar computation (e.g.
  the `astral` library) — no external API at build time. Not implemented
  in v1: the schema accepts only `HH:MM` until the Post record carries
  coordinates (§10.1d).

## 6. Location authority moves to the manager

`LOCATION_ID` leaves the device env (the signer builds authoritative keys
anyway). The operator assigns/reassigns locations in the manager; `/sign`
answers `unassigned` until then. `TIMEZONE` stays device-side (it must
match the physical site) and is visible in `reported` for cross-checking
against the webapp's Post record. Bench/test stages keep a `LOCATION_ID`
env override for offline tests only.

## 7. Operator-facing API + UI — **inside the days-in-a-minute webapp**

**Decided** (was open question): the manager frontend is NOT standalone.
It becomes an admin-only section of the days-in-a-minute webapp
(`D:\home\repo-misc\days-in-a-minute`) — `/manage/devices` pages plus
`/api/v1/devices*` route handlers — because:

- auth is already solved there (JWT sessions + RBAC; the `admin` role
  gates device ops — no separate operator token to invent or leak),
- `location_id` **is** the webapp's Post entity: assignment becomes a
  Post picker, with the device's reported timezone cross-checked against
  the Post's timezone attribute,
- the webapp already presigns from `knh-dam-store` and already has the
  envelope/zod/test/deploy scaffolding — zero new infrastructure.

**Boundary**: the device plane (`/sign`, upload-monitor, token hashes)
stays in this repo's Lambdas — devices never talk to the webapp, and the
webapp never sees plaintext device tokens (rotation writes the token
table the same way `issue_device_token.py` does today). The contract
between the repos is the two DynamoDB table schemas (§4).

Operator endpoints (webapp route handlers, admin RBAC, standard
envelope):

- `GET  /devices` — fleet list: group badge (ssh / remote), **health
  badge** (`ok/offline/stale/suspect/quarantined`, §5.1), assignment,
  capturing flag, reported summary.
- `GET  /devices/{id}` — full record + presigned GET for the latest
  uploaded frame (Lambda lists `images/{location_id}/{today}/` tail).
- `PUT  /devices/{id}/assignment` — `{location_id | null}`.
- `PUT  /devices/{id}/control` — `{capturing?, video_window_start?,
  video_window_end?}` (start/stop + daily video window).
- `PUT  /devices/{id}/access` — `{ssh_accessible, ip, ssh_user, note}`.
- `PUT  /devices/{id}/hardware` — `{lens_type, note}` (Pi model and
  camera are auto-reported; the lens cannot be detected).
- `POST /devices/{id}/token` — issue/rotate token (replaces
  `issue_device_token.py`); `DELETE` — kill-switch (`enabled=false`).
- `PUT  /devices/{id}/health-review` — operator clears/confirms
  `content_flag` and resets `damaged_recent` after fixing a device
  (reserved flow, minimal in v1).
- UI (`/manage/devices`, Next.js per the webapp's conventions): device
  table with group + health badges → detail panel with the latest frame
  (auto-refresh at the device's interval), status fields, start/stop +
  Post-picker assignment + rotate-token buttons; for the ssh-accessible
  group also the copyable SSH command (`ssh cskim@<ip>`), a
  `scripts/deploy.sh <ip>` hint, and a direct link to the live MJPEG
  viewer `http://<ip>:8080/`.

## 8. Agent-side changes (minimal)

- Uploader `/sign` body gains `device_id` + `status` (from `Agent.status()`).
- New response handling: `paused` / `unassigned` → skip frame, count
  `skipped`, no retry/backoff (these are healthy states, not errors).
- New `skipped` counter in uploader counters (visible in `/healthz` and,
  via the piggyback, in the manager).
- **Thermal (§5.2)**: read `thermal_zone0` per capture; include `temp_c`,
  `throttled`, `thermal_state` in status; pause/resume the capture loop
  at the configured thresholds; optional sustained-85 °C shutdown path
  (final status submit, then `poweroff` via a sudoers-limited rule).
- **Hardware report**: `pi_model` from `/proc/device-tree/model` once at
  startup; camera model from picamera2 properties.
- **No new threads, no polling loop, no manager client** — the existing
  uploader call carries everything.

## 9. Security

- Device calls: existing per-device token; pause/kill enforced
  server-side — a modified agent cannot upload while paused or revoked.
- Operator calls: webapp admin session (JWT + RBAC, re-checked
  server-side per its architecture rules) — no separate operator
  credential exists.
- SSH info in DynamoDB is addressing metadata (user + private-LAN IP,
  key-only auth as fleet policy); no passwords or keys are ever stored.
- Frame links are short-lived presigned GETs; buckets stay private.

## 10. Open questions → ADRs (write at build time)

1. Remote config changes for the NAT group (e.g. `video_minutes`): the
   `/sign` response could carry desired config for the agent to apply —
   v1 ships without it (capturing gate only); decide when needed.
1b. Content-moderation activation (when §5.1.3 goes live): sampling rate,
   provider (Rekognition vs. other), and whether a `flagged` device is
   auto-paused or only surfaced for operator review.
1c. Video window refinement (§5.3): should the capture interval adapt to
   the window duration so shorter windows still render the full
   `VIDEO_MINUTES` (denser capture inside the window), or stay constant
   (shorter video)? Constant for v1.
1d. Dawn/dusk window values (§5.3, deferred): where the location's
   coordinates come from (geocode the Post's city/address vs. manual
   lat/lon entry vs. timezone approximation), which twilight definition
   to use (civil twilight vs. sunrise/sunset), and daily resolution at
   build time. Needs a small Post-record extension in the webapp.
2. ~~UI home~~ — **decided (§7)**: integrated into the days-in-a-minute
   webapp `/manage/devices` with its RBAC; device plane stays in this
   repo.
3. `/sign` growth: same Lambda gains the operator routes (this design)
   vs. separate Lambdas sharing tables.
4. Live-view boost for aiming/focus at remote sites (temporary cadence
   raise via the §10.1 channel) — nice-to-have.

## 11. Failure modes

| Failure | Behavior |
| ------- | -------- |
| agent-api down | agent retries /sign with backoff (existing path); frames queue up to `QUEUE_MAX`; status goes stale in the UI |
| Device offline / powered off | `reported.at` ages out → offline badge; control edits apply when it returns |
| Device paused | frames skipped device-side AND unsignable server-side; status keeps flowing; resume ≤ 1 interval |
| Device unassigned | same as paused (`skipped` counts up) until the operator assigns a location |
| Camera broken / capture dying (device still online) | no objects land → `stale` within ~3 intervals, visible even though the device self-reports as alive |
| Corrupt/damaged uploads | upload-monitor tags the objects (builder skips them), `damaged_recent` climbs → `suspect` badge |
| Disallowed content uploaded (future) | reserved: moderation hook sets `content_flag` → `quarantined` (+ optional auto-pause); evidence keys retained for review |
| Stolen/compromised device | token revoke: uploads dead instantly; record kept for audit; ssh group additionally reachable for forensics |
| Device overheating | 70 °C warn badge → 75 °C self-pause (stays online, keeps reporting temp) → auto-resume ≤ 65 °C; sustained 85 °C optional shutdown with a final `thermal-shutdown` event recorded (§5.2) |
| Frames outside the video window | still captured and uploaded (not an error); simply not selected by the builder; expire via the normal 30-day lifecycle (§5.3) |
| Manager UI down | devices fully autonomous — capture/upload unaffected |

## 12. Build plan

Phase 2 spans both repos, device plane first:

- **This repo** (`docs/plan/phase2-agent-manager-plan.md`): agents table +
  `/sign` extension (gate, status collect, location lookup) →
  **upload-monitor Lambda** (S3 events: `last_object_at`, damaged check;
  content fields reserved) → agent-side skip handling + status piggyback
  → bench device records (fill `access`).
- **Webapp repo** (its own phase plan, per its conventions):
  `/api/v1/devices*` route handlers over the shared tables +
  `/manage/devices` UI (Post-picker assignment, health badges, latest
  frame, controls).

The video builder moves to Phase 3.
