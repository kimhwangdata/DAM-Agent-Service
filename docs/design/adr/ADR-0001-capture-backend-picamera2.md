# ADR-0001 — Capture backend: picamera2

- **Status**: Accepted
- **Date**: 2026-08-13
- **Deciders**: cskim
- **Blocks**: Phase 1 (`agent/camera.py`)

## Context

The agent needs a way to capture still JPEGs on the Pi. The fleet runs two
camera stacks (`docs/reference/camera-info.md`): native sensors on stock
libcamera (IMX477 HQ — the standard camera) and the Arducam Pivariety IMX462
on Arducam's libcamera build. The design (`01-agent.md`) requires in-memory
JPEGs — no files on disk — at one frame per ~48 s.

## Decision drivers

- must work on both fleet camera stacks without per-stack agent code
- no-local-save design: JPEG must land in memory
- legacy `capture-24h.py` is proven long-running (24 h loops) on picamera2
- simplicity: one obvious code path, testable on Windows via a fake

## Options

### Option A — picamera2 (Python library)

- `+` proven by the legacy service for day-long capture loops
- `+` captures straight to `BytesIO` — no temp files, fits no-local-save
- `+` verified working on both fleet stacks (IMX462 Pi tested end-to-end;
  IMX477 is the reference platform)
- `+` camera stays open between captures — no per-frame startup cost
- `−` apt-installed system package → venv needs `--system-site-packages`
- `−` not importable on Windows → import guard + `FakeCamera` for tests

### Option B — shell out to `rpicam-still`

- `+` no Python camera dependency; works wherever rpicam-apps works
- `−` writes a file per capture (or pipes) — clashes with no-local-save
- `−` ~1–2 s process start + camera init per frame; wasteful at 48 s cadence
- `−` weaker programmatic control/metadata access; string parsing

## Open questions

- none

## Decision

**Option A — picamera2.** It is the legacy-proven path, satisfies the
in-memory requirement directly, and both fleet camera stacks already run it.

## Consequences

- Pi provisioning installs `python3-picamera2` via apt and creates the agent
  venv with `--system-site-packages` (Phase 1.7).
- `agent/camera.py` guards the picamera2 import so the package imports on
  Windows; tests use `FakeCamera`.
- Camera tuning stays outside the agent (in the camera stack / config.txt).

## Next

- Implement `Picamera2Camera` + `FakeCamera` per `01-agent.md` §2 (Phase 1.2).
