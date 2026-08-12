# ADR-0002 — Still-image archive mechanics & bucket staging

- **Status**: Accepted
- **Date**: 2026-08-13
- **Deciders**: cskim
- **Blocks**: Phase 0.5 (bucket setup)

## Context

Architecture §7: stills live in `knh-dam-store/images/` for 30 days; after
that only a **Glacier** copy in `knh-dam-backup` (same layout) remains.
Native S3 lifecycle rules cannot move objects across buckets, so the intent
needs an implementation choice. Separately, §10 leaves open whether dev and
prod share `knh-dam-store` or get stage-suffixed buckets.

## Decision drivers

- zero custom code to maintain, if possible
- backup should also protect against accidental deletion, not just age-out
- small fleet, one AWS account today — avoid premature infrastructure
- predictable cost (Glacier ~US$0.0036/GB-mo; stills ≈0.5 GB/device/day)

## Options

### Option A — replication-at-upload + 30-day expiration

S3 Replication rule `images/` → `knh-dam-backup` (destination storage class
`GLACIER`), plus a 30-day lifecycle **expiration** on
`knh-dam-store/images/`.

- `+` fully native — no code, no scheduler, nothing to monitor
- `+` backup exists from upload moment → also covers accidental deletion
  (delete markers are not replicated)
- `+` per-object, no day-boundary edge cases
- `−` requires **versioning** on both buckets (replication prerequisite)
- `−` double storage for the first 30 days (Standard + Glacier; Glacier side
  is ~1/6 the Standard price — negligible)

### Option B — day-30 copy job

A scheduled Lambda copies day-folders older than 30 days to
`knh-dam-backup` (Glacier), then deletes them from the store.

- `+` no versioning requirement; single copy at any time
- `−` custom code + schedule + failure handling to build and babysit
- `−` no backup during the first 30 days; a bad delete loses data
- `−` day-folder edge cases across timezones

### Stage separation — sub-decision

- **Shared buckets** (one `knh-dam-store`/`knh-dam-backup` for dev+prod):
  `+` matches the single bucket named in §7; location-id prefixes already
  isolate devices; test stage never touches real S3. `−` bench data mixes
  with future prod data.
- **Stage-suffixed buckets**: `+` clean separation; `−` more setup, breaks
  the simple naming, premature for today's fleet.

## Open questions

- none blocking; revisit stage separation if/when a real prod fleet ships.

## Decision

**Option A (replication-at-upload)** with **shared buckets**. Enable
versioning on both buckets; replication rule on `images/` with Glacier
destination class; 30-day expiration on `knh-dam-store/images/` plus
noncurrent-version cleanup (1 day) and expired-delete-marker removal so
versioning does not silently retain expired data.

## Consequences

- Phase 0.5 setup: versioning (both buckets) → replication role + rule →
  lifecycle (30-day expiration + `NoncurrentVersionExpiration: 1 day` +
  `ExpiredObjectDeleteMarker: true` on `images/`).
- Dev and prod share the buckets; bench devices use distinct
  `LOCATION_ID`s. Revisit as a superseding ADR if a prod fleet needs
  isolation.
- `knh-dam-backup` restore is a Glacier restore (hours) — acceptable for a
  backup-of-last-resort.

## Next

- Implement in Phase 0.5; verify rules per plan §0.6.
