# ADR-0005 — ffmpeg packaging: static binary in a Lambda layer

- **Status**: Accepted
- **Date**: 2026-08-13
- **Deciders**: cskim
- **Blocks**: Phase 3 (`video-builder/` build mode)

## Context

The builder runs ffmpeg inside Lambda (design 03 §5/§6). Lambda offers two
ways to ship a native binary: a layer on the zip-based runtime, or a
container image.

## Decision drivers

- reuse the existing boto3 zip-deploy pipeline (signer, monitor)
- small cold starts; encode CPU comes from the memory setting either way
- reproducibility: a pinned, checksum-verified binary

## Options

### Option A — static ffmpeg binary in a Lambda layer

- `+` fits the existing zip deploy scripts — no new infrastructure
- `+` well within limits (static build ~80 MB unpacked; layer+function
  cap is 250 MB unzipped)
- `+` layer versioning pins the binary independently of handler deploys
- `−` binary must be fetched/verified by a build script

### Option B — container image (ffmpeg via apt/base image)

- `+` arbitrary size; Dockerfile-native dependency management
- `−` requires an ECR repo + image build/push pipeline we don't have,
  Docker in the deploy loop, larger cold starts

## Open questions

- none

## Decision

**Option A — the layer** (`dam-ffmpeg`), built by
`scripts/aws/build_ffmpeg_layer.py` from a pinned static x86_64 build with
its checksum verified before publishing.

## Consequences

- The builder Lambda attaches the layer and calls `/opt/bin/ffmpeg`.
- Upgrading ffmpeg = publish a new layer version + point the builder at
  it (explicit, auditable).

## Next

- Implement the layer build script and smoke-test `ffmpeg -version` in
  Lambda (plan 3.2).
