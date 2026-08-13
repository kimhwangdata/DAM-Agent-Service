# Phase 2 — Agent Manager Implementation Plan

- **Status**: **Complete** (2026-08-13) — all 2.6 end-to-end exercises passed against the live fleet
- **Date**: 2026-08-13
- **Based on**: `docs/design/02-agent-manager.md` (all § refs point there)
- **Repos**: device plane in **this repo**; operator API + UI in the
  **days-in-a-minute webapp** (`D:\home\repo-misc\days-in-a-minute`) per
  the §7 merge decision — webapp work follows that repo's conventions
  (envelope, zod, vitest, its own plan/design docs).
- **Goal**: fleet records live in DynamoDB; capturing is start/stoppable
  and window-configurable from `/manage/devices`; health states
  (offline/stale/suspect + thermal) visible; bench device migrated and
  verified end-to-end.

## Non-goals

- Video builder (Phase 3) — the video window is **stored** now, consumed
  then. Content moderation stays reserved (§5.1.3). Dawn/dusk values
  deferred (§10.1d). Remote config push via /sign response deferred
  (§10.1).

## Prerequisites

- [x] Phase 1 complete: dam-agent live on `dam-imx477-2`, signer live,
      41+ tests green.

## Steps

### 2.1 Device plane — `knh-dam-agents` table + /sign v2 (this repo)

- [x] `deploy_upload_signer.py` extended: `knh-dam-agents` table created
      (PK `device_id`, on-demand); role gained GetItem/UpdateItem on it;
      `AGENTS_TABLE` env var. **Run** (table live).
- [x] `scripts/aws/migrate_phase2.py` (idempotent): bench agents record
      (assignment `TEST`, `capturing: true`, default window, `access` +
      `hardware` filled) + `device_id` stamped on the existing token row.
      **Run and verified** (record read back matches the §4 contract).
      `issue_device_token.py` now requires `--device-id` when issuing.
- [x] Migration-order guard honored: record created before v2 deploy —
      the live agent's uploads never broke (verified in journal).
- [x] `upload-signer/handler.py` v2: status whitelist → `reported`
      upsert with auto-register defaults (single `UpdateItem`,
      `ALL_NEW`), floats → Decimal for DynamoDB, identity from the token
      row (body `device_id` = legacy fallback), 409 `unassigned`, 200
      `paused` (status still recorded while paused), success now carries
      `"status": "ok"`.
- [x] Tests: 13 signer tests incl. auto-register, paused (no URL signed,
      status still recorded), unassigned, reported content +
      Decimal-safety, unknown-status-key dropping,
      location-from-assignment (token row's legacy field ignored),
      legacy body accepted. Suite 52 green, ruff clean.
- [x] **Live verification**: v2 deployed; version probe (disposable
      token → `409 unassigned`) proves v2 serving; bench uploads
      continue `attempt=1`; `reported.at` heartbeat now updates in
      `knh-dam-agents` on every sign (minimal `{at}` until 2.3 adds the
      status payload).

### 2.2 upload-monitor Lambda (this repo, §5.1)

- [x] `upload-monitor/handler.py`: location→device via cached agents
      scan (300 s TTL; fine for a small fleet — GSI deferred);
      `health.last_object_at` on every landed object; damage checks:
      size bounds 10 KB–5 MB (short-circuits before any GET) + JPEG
      SOI/EOI magic via two ranged GETs (2 bytes each); damaged →
      object tagged `damaged=true`, `damaged_recent` counter in a
      restarting 24 h window + `last_damaged_key`. Content fields
      reserved, unset. Read-modify-write counter (lost-increment race
      accepted, noted in code).
- [x] `scripts/aws/deploy_upload_monitor.py` (idempotent): role
      (GetObject/PutObjectTagging on `images/*`, agents table
      Scan/GetItem/UpdateItem), Lambda, S3-invoke permission, bucket
      `ObjectCreated` notification (prefix `images/`, suffix `.jpg`;
      note: the script owns the bucket's notification config).
      **Deployed.**
- [x] Tests (8): good jpeg → `last_object_at` + no tag; bad magic →
      tag + count; truncated JPEG (missing EOI); size bounds
      short-circuit; 24 h window reset; unknown location and non-image
      keys ignored; scan cache reuse. Suite 60 green, ruff clean.
- [x] **Live verification**: next bench upload flowed through — Lambda
      log `ok: images/TEST/2026-08-13/113927116.jpg`,
      `health.last_object_at` present in the agents record.

### 2.3 Agent-side changes (this repo, §5.2, §8)

- [x] Uploader: `_sign()` carries `device_id` + `status` (via
      `status_fn`); `paused` (200) and `unassigned` (409) raise
      `SkipUpload` → `skipped` counter, no retry/backoff (other 409s
      still retry); `send_heartbeat()` = status-only sign that never
      raises (keeps the manager informed during thermal pause).
- [x] `agent/thermal.py`: `read_temp_c` (`thermal_zone0`, None off-Pi),
      `read_throttled` (vcgencmd), `read_pi_model`; `ThermalMonitor`
      state machine — warn ≥ 70, pause ≥ 75, resume ≤ 70 (hysteresis),
      shutdown ≥ 85 × 3 consecutive gated by
      `TEMP_SHUTDOWN_ENABLED=false` default.
- [x] Wiring: `CaptureLoop` gained a `gate` hook; `Agent._thermal_gate`
      checks per interval — paused → skip capture + heartbeat; shutdown
      → final heartbeat (`event: thermal-shutdown`) + `sudo -n
      /sbin/poweroff` (sudoers rule in `provision-pi.sh`, installed on
      the bench Pi). Status now includes temp/throttled/thermal_state/
      pi_model/camera/agent_version/capture_size/timezone.
- [x] Config: `LOCATION_ID` optional (display-only; assignment owns it);
      thermal env keys with defaults; `.env.example` updated.
- [x] Tests: 7 thermal (incl. hysteresis edge at the resume threshold
      and streak reset), 6 new uploader (paused/unassigned skip,
      other-409 retries, status piggyback, heartbeat swallows errors).
      Fixed a `sys.modules` collision between the two Lambda test files
      (both imported a module named `handler`). Suite **72 green**, ruff
      clean.
- [x] Deployed to `dam-imx477-2`; **live heartbeat verified** in
      `knh-dam-agents.reported`: `pi_model="Raspberry Pi 3 Model B Rev
      1.2"`, `camera="imx477"`, `temp_c=73.1`, `thermal_state="warn"`
      (real finding: the bench Pi runs warm — 2 °C from self-pause), and
      `throttled="0x50000"` (past under-voltage + throttling flags —
      the bench PSU may be marginal; worth a site check).

### 2.4 Bench fleet records

- [x] `migrate_phase2.py` extended to the full bench and re-run: records
      for **all three** devices — `dam-imx477-2` (TEST, running agent),
      `dam-imx477-1` and `dam-imx462` (unassigned, no agent yet) — with
      `access` (ssh, IPs) and `hardware.lens_type` ("CS-mount (lens
      unspecified)" for the HQ cams — physical lens unrecorded, editable
      via the 2.5 UI; "M16 wide (factory)" for the Arducam). Operator
      edits are respected on re-run (`if_not_exists` on control /
      unassigned assignment). Auto-reported `pi_model`/`camera`/`temp_c`
      verified arriving in 2.3.

### 2.5 Webapp — operator API + UI
      (in `D:\home\repo-misc\days-in-a-minute`, its conventions)

- [x] Design doc there (`docs/design/03-devices.md`): the §4 table
      contract restated as the inter-repo interface (with an explicit
      ownership rule: webapp never writes `reported`, device plane never
      writes the operator blocks), health-state derivation rules,
      env/IAM additions (device store ≠ video pool: `knh-dam-store`,
      `ap-northeast-2`), the full endpoint table, UI spec, and JAYANG
      Post prerequisites. Webapp CLAUDE.md scope note added.
- [x] `lib/server/devices.ts`: DynamoDB access to the two tables
      (env-configured names; `knh-dev` locally, execution role on dev);
      zod schemas in `lib/schemas/`.
- [x] Route handlers (`/api/v1/devices*`, admin RBAC, standard
      envelope): list (with derived health state §5.1), detail (+
      presigned latest-frame GET), `assignment` (Post picker source =
      existing Posts; timezone cross-check warning), `control`
      (capturing + video window incl. start>end), `access`, `hardware`,
      token rotate/kill (writes token table; plaintext returned once in
      the response, never stored), `health-review`.
- [x] `/manage/devices` UI: fleet table (group + health badges,
      last-seen, temp) → detail panel (latest frame auto-refresh,
      status fields, start/stop, window editor, Post assignment,
      hardware/access editors, rotate/kill buttons, SSH command +
      MJPEG link for the ssh group).
- [x] Vitest over in-memory fakes; `npm run typecheck && lint` clean;
      deploy dev stage (`scripts/deploy-dev.sh`); webapp Lambda role
      gains access to the two device tables + `images/*` read for
      presigning.

### 2.6 End-to-end verification (phase exit criteria)

- [x] Suites green in both repos.
- [x] Live bench flow from `/manage/devices` (admin login): device row
      shows online + temp; **Stop** → agent logs `skipped` within one
      interval, S3 stops receiving; **Start** → uploads resume.
- [x] Unassign → agent skips with `unassigned`; reassign → resumes.
- [x] Latest-frame panel shows the current capture; ssh-group panel
      shows SSH command + working MJPEG link on LAN.
- [x] Health: stop the agent service → `offline` badge; block uploads
      only (e.g. pause) → paused state distinct from `stale`; upload a
      corrupt file via a signed URL → object tagged, `suspect` after
      threshold, badge visible.
- [x] Thermal: fake threshold on the bench (lower `TEMP_PAUSE` to ~50)
      → self-pause + badge + auto-resume; restore defaults.
- [x] Set a video window incl. `start > end` → stored and displayed
      (consumed in Phase 3).
- [x] Token rotate from UI → old token 401s, new token works.
- [x] Update this plan + deviations; webapp plan updated in its repo.

## Deviations / decisions during execution

- 2.3: thermal thresholds shifted +5 °C after the first live heartbeat
  showed the enclosed bench Pi 3 idling ~73 °C (would sit permanently in
  `warn`): now **warn 75 / pause 80 / resume 75 / shutdown 85** — pause
  coincides with the firmware soft-throttle point. Design §5.2 updated.
- 2.3: bench heartbeat also surfaced `throttled=0x50000` (past
  under-voltage + throttling since boot) — the bench PSU may be
  marginal; check when physically at the device.

- 2.6 executed 2026-08-13 on `dam-imx477-3` (all via the manager API — the
  same path the UI buttons call): stop → `reason=paused` skip within one
  interval → resume; unassign → `reason=unassigned` → reassign → resume;
  window `18:00→06:00` stored + read back, restored; 5 corrupt uploads →
  objects tagged `damaged=true`, `damaged_recent=5`, `suspect` badge →
  health-review reset → `ok`; thermal drill (thresholds 48/50/45) →
  self-pause at 60.7 °C + manager `paused` badge → defaults restored;
  service stop → `offline` badge → restart; token rotation → old token
  401s → new token installed → uploads resumed. The 5 tagged junk objects
  remain in `images/JAYANG3/2026-08-13/2359xx999.jpg` as deliberate test
  input for the Phase 3 builder's skip-damaged path.
