# Architecture Decision Records

One file per decision, named `ADR-NNNN-short-kebab-title.md`, following
`ADR-0000-template.md`. Accepted ADRs are immutable — supersede with a new
ADR instead of editing.

## Index

| ADR | Title | Status |
| --- | ----- | ------ |
| [ADR-0000](ADR-0000-template.md) | Template | — |
| [ADR-0001](ADR-0001-capture-backend-picamera2.md) | Capture backend: picamera2 | Accepted |
| [ADR-0002](ADR-0002-still-archive-mechanics.md) | Still-image archive mechanics & bucket staging | Accepted |
| [ADR-0003](ADR-0003-device-upload-auth-presigned.md) | Device upload auth: presigned URLs via upload-signer | Accepted |
| [ADR-0004](ADR-0004-builder-trigger-sweep.md) | Video-builder trigger: EventBridge sweep over DynamoDB cycles | Accepted |
| [ADR-0005](ADR-0005-ffmpeg-lambda-layer.md) | ffmpeg packaging: static binary in a Lambda layer | Accepted |

Still open (design 03 §8): video auto-registration in the webapp table —
revisit after the pool-sync flow proves out.
