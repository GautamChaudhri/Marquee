# 05 — Write-Enabled Gauntlet Test Plan

**Status:** Reconciled with the codebase on 2026-06-16.

This plan exercises implemented endpoints only. Planned routes are listed as
expected gaps, not test failures.

## Scope

Run against writable lab media, never the only copy of a library file.
Generate the fixture list from the current lab library immediately before the
run; do not reuse stale movie IDs from older planning snapshots.

Implemented endpoint families:

- health/system/config;
- sync and library browse;
- pipeline run/events/results/posters/rescore;
- feedback submit/undo/deploy;
- taste status/map/retrain/exemplar routes;
- subtitle inventory/scan/preview/download/plans/jobs/policies/generation;
- letterbox status/candidates/detect/preview/apply/remove/heal;
- webhooks;
- poster heal and artwork-event history.

Expected gaps:

- deferred manual poster restore endpoint;
- deferred subtitle-batches endpoints;
- library filters beyond pagination.

## Suggested Passes

1. Baseline snapshot: git status, health, system status, config, DB counts,
   fixture manifest.
2. Read-only API sweep.
3. Poster pipeline sweep with SSE capture.
4. Feedback deploy and undo tests.
5. Subtitle inventory and safe preview/download tests.
6. Subtitle mutation job tests with backup/restore.
7. Letterbox detect/preview/apply/remove tests.
8. Webhook dry-run or lab-media webhook tests.
9. Final manifest and DB diff.

Run a second pass with `EXECUTION_PROVIDER=cpu` and `OCR_DEVICE=cpu` only when
the target machine and time budget allow it.

## Subgen Generation Pass

Generation tests are enabled for the current target because Subgen was verified
on 2026-06-16 at `http://localhost:9000/status`:

- response: `200 OK`;
- version: `Subgen 2026.06.3, stable-ts 2.19.1, faster-whisper 1.2.1 (Docker)`;
- container: `mccloud/subgen:latest` named `subgen`, bound to host port `9000`;
- GPU: RTX 3070 visible on host and inside the container, with no running GPU
  processes at the time of verification.

For the generation pass, keep other GPU workloads idle. Subgen should be the
only active GPU consumer while subtitle generation is being tested.

## Capture Requirements

Store:

- request/response JSONL;
- headers and status codes;
- SSE transcripts;
- served binary artifacts and hashes;
- ffprobe/mkvmerge verification output;
- before/after DB snapshots;
- error log with repro command and response body.
