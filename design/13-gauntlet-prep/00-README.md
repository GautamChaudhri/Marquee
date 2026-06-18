# 13 — Gauntlet Prep

**Status:** Reconciled with the codebase on 2026-06-16.

This folder contains gauntlet-test planning notes. Treat it as planning, not
as proof that every listed endpoint exists.

## Current Code Reality

Implemented and gauntlet-testable:

- poster pipeline, results, SSE, poster serving, rescore;
- feedback submit/undo with optional deployment;
- taste status/map/retrain endpoints;
- system status/heal;
- library browse endpoints;
- subtitle inventory, plans, jobs, policies, generation hooks;
- movie-only letterbox detection/preview/apply/remove/heal;
- Radarr/Sonarr/Subgen webhook routes.

Not implemented:

- `[DEFERRED]` `POST /api/movies/{movie_id}/poster/restore`;
- server-side library filters beyond pagination;
- `[DEFERRED]` `/api/subtitle-batches/...` routes.

## File Index

| File | Current status |
|---|---|
| `01-poster-restore-endpoint.md` | `[DEFERRED]` code brief; route is intentionally postponed |
| `02-library-filters.md` | `[PLANNED]` code brief; filters are not implemented |
| `03-doc-reconciliation.md` | Historical reconciliation checklist; superseded by this pass |
| `04-gauntlet-readiness.md` | Readiness checklist, updated to distinguish implemented vs planned |
| `05-gauntlet-test-plan.md` | Test plan, updated to avoid assuming planned routes exist |

## Fixture Rule

Any live gauntlet fixture IDs and mount paths must be re-derived from the
target database immediately before testing. Do not reuse IDs from older
planning snapshots.
