# Phase 0 — Config & Groundwork Plan

- **Status**: Not started
- **Date**: 2026-08-13
- **Based on**: `docs/design/00-architecture.md` (esp. §3 agent, §7 storage,
  §10 environments, §11 open questions)
- **Goal**: everything the code phases need is in place — repo skeleton,
  Python tooling, stage/env handling, AWS buckets + IAM per §7, and the ADRs
  that block Phase 1 — **without writing feature code**.

## Non-goals

- No capture, scheduler, queue, or uploader logic (Phase 1).
- No video-builder Lambda (Phase 2).
- No upload-security layers beyond the IAM baseline (§6 is future work).

## Prerequisites (already done, for the record)

- [x] Bench fleet ready: `dam-imx477-1` (Trixie, HQ cam), `dam-imx477-2`
      (Bookworm, HQ cam), `dam-imx462` (Bookworm, Arducam) — key-only SSH,
      Wi-Fi, `docs/reference/camera-info.md`.
- [x] Design doc `00-architecture.md` with storage layout (`knh-dam-store` /
      `knh-dam-backup`) and key scheme decided.

## Steps

### 0.1 Repository skeleton

- [x] `git init` this repo; first commit pushed to
      **https://github.com/kimhwangdata/DAM-Agent-Service.git** (branch
      `main`, rebased onto the remote's auto-generated README). Note:
      `legacy-rpi-camera/` is gitignored — it stays local reference only.
- [x] `.gitignore`: `.env.*` (except `.env.example`), `__pycache__/`,
      `.venv/`, captured images (`captures/`), local queue (`queue/`).
- [ ] Create target layout from CLAUDE.md: `agent/` (empty package with
      `__init__.py`), `video-builder/`, `systemd/`, `scripts/`, `tests/`
      (placeholder test so pytest runs green).
- [ ] `docs/design/adr/` with `ADR-0000-template.md` and `README.md` index
      (format per CLAUDE.md).

### 0.2 Python project setup (agent-side, Windows-first)

- [ ] `pyproject.toml`: package metadata, Python `>=3.11` (fleet floor is
      Bookworm's 3.11), deps `boto3`, `python-dotenv`, `python-ulid`;
      dev deps `pytest`, `ruff`.
- [ ] Local venv on the dev machine; `pytest` and `ruff` run clean on the
      placeholder test.
- [ ] `agent/config.py` skeleton only: loads `.env.{STAGE}` per the stage
      table and exposes typed settings — the single place for all config and
      magic values (no literals elsewhere). Unit-testable on Windows.

### 0.3 Stages & env files

- [ ] `.env.example` (committed) with placeholders:
      `STAGE`, `LOCATION_ID`, `DEVICE_ID`, `TIMEZONE` (IANA),
      `S3_BUCKET=knh-dam-store`, `S3_IMAGE_PREFIX=images/`,
      `AWS_REGION`, credentials fields left empty + comment on where real
      values live.
- [ ] Create local `.env.test` (gitignored) for Windows runs — fake camera,
      test bucket or mock.
- [ ] Verify: `STAGE=test` loads `.env.test`; missing stage file fails loudly.

### 0.4 ADRs that block later phases (write + accept)

- [ ] **ADR-0001 capture backend**: picamera2 vs. shell-out to
      `rpicam-still` (must serve both camera stacks — see
      `docs/reference/camera-info.md`).
- [ ] **ADR-0002 archive mechanics** (needed before bucket setup below):
      replication-at-upload to `knh-dam-backup` (Glacier destination class)
      vs. day-30 copy job; dev/prod bucket stage separation
      (shared `knh-dam-store` vs. stage-suffixed buckets).
- [ ] Index both in `docs/design/adr/README.md`.
      (Builder-phase ADRs — trigger mechanism, ffmpeg packaging, video
      registration — are written at the start of Phase 2, not now.)

### 0.5 AWS groundwork (per §7, admin profile `knh-dev`)

- [ ] Create bucket **`knh-dam-store`** — private, Block Public Access on,
      default encryption.
- [ ] Create bucket **`knh-dam-backup`** — same settings.
- [ ] Lifecycle rule on `knh-dam-store` prefix `images/`: expire objects
      after **30 days**.
- [ ] Archive path per ADR-0002 (e.g., replication rule `images/` →
      `knh-dam-backup` with Glacier storage class; versioning enabled if
      replication requires it).
- [ ] IAM: per-device upload policy **template**
      (`scripts/aws/device-upload-policy.json`): `s3:PutObject` on
      `arn:aws:s3:::knh-dam-store/images/{location_id}/*` only — no list, no
      delete, no other prefixes.
- [ ] Create one real device identity for the bench (e.g. location `DIO21`)
      from the template; store its keys only in the device's `.env.dev`
      (never committed).

### 0.6 Verification (phase exit criteria)

- [ ] `pytest` green on Windows; `ruff` clean.
- [ ] `python -c "from agent.config import settings"`-style smoke run loads
      the test stage.
- [ ] With the scoped device credentials: upload to
      `images/{location_id}/2026-08-13/000000000.jpg` **succeeds**; upload to
      another location's prefix and to `videos/` **is denied**; test objects
      cleaned up afterwards.
- [ ] Lifecycle + archive rules visible on the buckets
      (`aws s3api get-bucket-lifecycle-configuration --profile knh-dev`).
- [ ] Both ADRs accepted and indexed.

## Deviations / decisions during execution

(fill in as steps complete — keep `[x]` marks current per CLAUDE.md)
