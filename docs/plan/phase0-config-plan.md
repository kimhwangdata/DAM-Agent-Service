# Phase 0 — Config & Groundwork Plan

- **Status**: **Complete** (2026-08-13; device-identity item cancelled by
  ADR-0003 — replaced by the upload-signer built in Phase 1)
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
- [x] Create target layout from CLAUDE.md: `agent/` package,
      `video-builder/`, `systemd/`, `scripts/` (`.gitkeep`s), `tests/`.
- [x] `docs/design/adr/` with `ADR-0000-template.md` and `README.md` index
      (format per CLAUDE.md; planned ADR-0001/0002 pre-listed).

### 0.2 Python project setup (agent-side, Windows-first)

- [x] `pyproject.toml`: `dam-agent` 0.1.0, Python `>=3.11`, deps `boto3`,
      `python-dotenv`, `python-ulid`; dev deps `pytest`, `ruff`, `pillow`;
      ruff configured (py311, E/F/I/UP/B).
- [x] Local venv (`.venv`, Python 3.12.7) with editable install; `pytest`
      (6 tests) and `ruff` run clean.
- [x] `agent/config.py`: loads `.env.{STAGE}` (cwd, then repo root),
      typed frozen `Settings` with the design §7 defaults, loud
      `ConfigError` on missing stage file / required keys / bad
      `CAPTURE_SIZE`. Covered by `tests/test_config.py`.

### 0.3 Stages & env files

- [x] `.env.example` (committed) with identity/storage/capture placeholders
      and empty credential fields (comments point at the scoped IAM design).
      Note: `STAGE` itself is an OS env var selecting the file, not a key
      inside it.
- [x] Local `.env.test` created (gitignored — verified with
      `git check-ignore`); viewer disabled for Windows runs.
- [x] Verified: `STAGE=test` loads `.env.test` into `Settings`; missing
      stage file raises `ConfigError` listing the searched paths.

### 0.4 ADRs that block later phases (write + accept)

- [x] **ADR-0001 capture backend** — Accepted: **picamera2** (in-memory
      JPEGs, legacy-proven, both fleet stacks verified; consequences:
      `--system-site-packages` venv on Pi, import guard + FakeCamera).
- [x] **ADR-0002 archive mechanics** — Accepted:
      **replication-at-upload** to `knh-dam-backup` (Glacier destination
      class) + 30-day expiration on the store, with versioning on both
      buckets and noncurrent/delete-marker cleanup; **shared buckets** for
      dev+prod (revisit via superseding ADR when a prod fleet ships).
- [x] Both indexed in `docs/design/adr/README.md`.
      (Builder-phase ADRs — trigger mechanism, ffmpeg packaging, video
      registration — are written at the start of Phase 2, not now.)

### 0.5 AWS groundwork (per §7, admin profile `knh-dev`)

- [x] Bucket **`knh-dam-store`** created — private, BPA on, SSE-S3,
      versioning enabled (via `scripts/aws/setup_storage.py`, idempotent).
- [x] Bucket **`knh-dam-backup`** created — same settings.
- [x] Lifecycle on `knh-dam-store/images/`: expire after **30 days** +
      noncurrent-version (1 day) and expired-delete-marker cleanup
      (ADR-0002 consequences).
- [x] Archive path per ADR-0002: replication role `knh-dam-replication` +
      rule `images/` → `knh-dam-backup` with **GLACIER** destination class
      (delete markers not replicated).
- [x] IAM per-device upload policy **template**:
      `scripts/aws/device-upload-policy.json` (PutObject on own prefix only).
- [x] ~~Create bench device identity (per-device IAM user)~~ —
      **CANCELLED by ADR-0003**: devices carry no AWS credentials at all.
      Uploads go through the upload-signer Lambda (presigned URLs,
      per-device app tokens). The signer + token issuance are built in
      Phase 1; `scripts/aws/create_device_identity.py` is retired.

### 0.6 Verification (phase exit criteria)

- [x] `pytest` green on Windows (6 passed); `ruff` clean (agent, tests,
      scripts).
- [x] Config smoke run: `STAGE=test` loads `.env.test` →
      `location_id=TEST`, `s3_bucket=knh-dam-store`.
- [x] Buckets reachable with `knh-dev` (`head_bucket` OK on both);
      anonymous HTTPS list of `knh-dam-store` → **HTTP 403** (denied).
      (Device-credential allow/deny test moved to Phase 1's signer tests —
      ADR-0003.)
- [x] Rules verified live via boto3: lifecycle `expire-images-30d` (30 d,
      prefix `images/`) + `cleanup-noncurrent-and-markers` (noncurrent 1 d,
      expired delete markers); replication `images-to-backup-glacier`
      Enabled → `knh-dam-backup`, storage class **GLACIER**.
- [x] Three ADRs (0001 picamera2, 0002 archive, 0003 presigned upload)
      Accepted and indexed in `docs/design/adr/README.md`.

## Deviations / decisions during execution

- 0.2: dev venv uses Python 3.12.7 (Anaconda base); `pip install -e` needs
  `PIP_USER=0` on this machine because Anaconda's pip config forces user
  installs.
- 0.2: instead of a placeholder test, `tests/test_config.py` covers the
  config module (6 tests) — this already satisfies most of 0.3's
  "fails loudly" verification.
- 0.2: `config.py` went slightly beyond a skeleton (full typed load +
  validation) since Phase 1.1 only needs to add the derived `interval_s`.
- 0.5: no AWS CLI on the dev machine — groundwork done with committed
  boto3 scripts (`scripts/aws/setup_storage.py`,
  `create_device_identity.py`) instead; arguably better (idempotent,
  reviewable, reproducible).
- 0.5: buckets created in `ap-northeast-2`. `csk-homepage-dev` (knh-dev
  profile) can manage roles but not IAM users → device-identity step was
  briefly blocked, then **cancelled entirely by ADR-0003** (security
  review: no AWS keys on devices; presigned-URL upload-signer instead).
