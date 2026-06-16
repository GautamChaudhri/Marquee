# 04 — Gauntlet Readiness Checklist

**Status:** Reconciled with the codebase on 2026-06-16.

Use this before running a write-enabled endpoint gauntlet.

## Implemented Mutation Families

| Family | Representative endpoints |
|---|---|
| Feedback deploy | `POST /api/feedback` with `deploy=true` |
| Poster heal | `POST /api/system/heal` |
| Radarr webhook restore | `POST /api/webhooks/radarr` on upgrade download |
| Letterbox | apply/remove/heal routes under `/api/letterbox` |
| Subtitle plans/jobs | `/api/media-files/.../subtitle-plans`, `/api/media-jobs/...` |
| Subtitle policies | `/api/subtitle-policies/{id}/apply` |
| Subtitle generation | `/api/media-files/{id}/subtitle-generations`, requires `SUBGEN_URL` |

Do not test planned routes as if they exist:

- `POST /api/movies/{movie_id}/poster/restore`
- `/api/subtitle-batches/...`
- library filters beyond pagination

## Required Preflight

- `GET /health` returns healthy database status.
- `GET /api/system/status` reports expected tool availability.
- `SUBTITLE_ENABLED=true` if media jobs are part of the run.
- `SUBGEN_URL` status is known; generation routes should be expected-skipped
  when unset.
- Media roots point to writable lab copies, not irreplaceable library files.
- `ffmpeg`, `ffprobe`, `mkvmerge`, and `mkvpropedit` are installed for write
  tests that need them.
- If `WEBHOOK_TOKEN` is set, gauntlet webhook calls include it.
- If `WEBHOOK_DRY_RUN=true`, webhook write tests are expected-skipped.

## Reset Discipline

Write tests must use expendable media or a lab copy. Capture a baseline before
mutation and verify reversal after mutation. For MKV metadata edits, byte-for-
byte identity may not hold after tag operations; verify semantic state with
`mkvmerge`/`ffprobe` where appropriate.

## Fixture And Media Rules

- Fixture IDs and file paths must be selected from the current target database
  after sync.
- Hardlink behavior must be checked on lab copies before subtitle mutation
  tests are allowed against real media.

## Current Subgen/GPU Snapshot

Verified on 2026-06-16:

- `http://localhost:9000/status` returned `200 OK` with Subgen
  `2026.06.3`, stable-ts `2.19.1`, and faster-whisper `1.2.1`.
- Docker reported `mccloud/subgen:latest` as container `subgen`, bound to
  `0.0.0.0:9000->9000/tcp`.
- The container has an NVIDIA GPU device request.
- `nvidia-smi` on the host and inside the container saw the RTX 3070, with no
  running GPU processes at the time of the check.

Before running the gauntlet, repeat `nvidia-smi` and keep GPU-heavy Marquee
pipeline work out of the subtitle-generation phase so Subgen is the only active
GPU consumer during that pass.
