# ADR-0004 — Video-builder trigger: EventBridge sweep over DynamoDB cycles

- **Status**: Accepted
- **Date**: 2026-08-13
- **Deciders**: cskim
- **Blocks**: Phase 3 (`video-builder/` dispatch)

## Context

Each location's daily video must be built when its **capture cycle ends** —
the end of its video window in its own timezone (design 02 §5.3; default:
local midnight, legacy behavior). Windows, timezones, and assignments live
in `knh-dam-agents` and can be changed by the operator at any moment.
Devices span many timezones, so build times differ per location.

## Decision drivers

- operator edits (window/assignment/timezone) must take effect without any
  extra orchestration step
- missed builds (outage, bug) should recover without human action
- minimal moving parts; a daily product tolerates minutes of latency

## Options

### Option A — one EventBridge rule per Post (`ScheduleExpressionTimezone`)

- `+` fires exactly at each window end; zero polling
- `−` every operator edit must create/update/delete a schedule — a second
  writer to keep consistent with DynamoDB, and drift is silent
- `−` no built-in catch-up: a missed fire is simply gone

### Option B — single sweep rule + cycle math from DynamoDB

One `rate(15 minutes)` rule invokes the builder in dispatch mode; it scans
the agents table, computes each device's most recent completed cycle from
`video_window_*` + timezone, and builds where `last_video.date` lags.

- `+` DynamoDB stays the single source of truth — edits apply on the next
  sweep with no choreography
- `+` self-healing: a missed cycle is rebuilt by the next sweep that sees
  the `last_video` gap
- `+` one rule, no schedule management code
- `−` up to 15 min latency after window end (irrelevant for daily videos)
- `−` a scan every 15 min (trivial at fleet scale; a GSI is the escape
  hatch if the fleet grows large)

## Open questions

- none

## Decision

**Option B — the sweep.** Correctness follows from reading current truth
every time; the latency cost is meaningless for this product.

## Consequences

- `last_video` on the agents record doubles as dedup state and manager
  display (design 03 §4); failed builds leave it unwritten → automatic
  retry each sweep.
- Deep backfill (more than one missed cycle) stays a manual build invoke.

## Next

- Implement dispatch mode per design 03 §2/§3 (plan 3.3).
