# Timeline

## Overview

This timeline preserves the existing phase structure from `design/todos.md`
while translating it into a concise status document. Several later-phase ideas
from the older roadmap are already partially implemented in code, so the phase
labels should be read as planning buckets rather than as a strict historical
sequence.

TV support is landing feature-by-feature: the TV library, TV poster pipeline,
TV taste engine, and TV HDR management backend have shipped (`design/plans/04`–`06`);
the HDR management frontend is planned in `design/plans/07`. The audio/subtitle
and letterbox workflows are next: landing pages + TV support (coverage rollups,
season × episode granularity, embedded Subgen) are planned in
`design/plans/08`–`11`, refined in `design/plans/12`. Letterbox TV phase 2 —
open-matte/pillarbox classification, confidence-filtered batch operations, the
TV re-encode flow, show-page job progress, and TV letterbox resets — is planned
in `design/plans/13` (same-number backend + frontend docs with one shared
timeline; backend first). Phase 3 follows a verified production incident
(2026-07-09: a stalled NVENC re-encode plus abandoned DB locks wedged the job
platform and the cancel endpoint): `design/plans/14` hardens the job platform
(stderr drain, stall watchdog, transaction hygiene, lock timeouts, automatic
force-cancel, persistent re-encode bars) and `design/plans/15` replaces the
Clear/Clean/Mixed vocabulary with content-descriptive TV verdicts (Widescreen /
Open Matte / Pillarbox), the Uniform / Clean Mix / Dirty Mix uniformity model,
and unique filled colors across the TV letterbox surfaces.

## Phase 1 - Core Infrastructure

Completed:

- application startup, shutdown, health, and logging foundations
- database/session infrastructure and schema baseline
- path translation and media-root validation

Remaining:

- no major Phase 1 backlog is called out in the current docs

## Phase 2 - Integration Layer

Completed:

- Radarr, Sonarr, and TMDB client wiring
- sync service and core route scaffolding
- request logging and rate limiting
- test pipeline endpoint for visual validation

Remaining:

- no major Phase 2 backlog is called out in the current docs

## Phase 3 - AI Pipeline

Completed:

- OCR, CLIP, k-NN taste matching, dedup, and the end-to-end movie pipeline
- production pipeline routes, review queue, batch runs, and feedback capture
- extended features such as DINO, quality signals, typicality, and the learned
  scorer plumbing
- runtime improvements such as process-lifetime feature extraction

Current focus:

- tuning OCR strictness and style gates across more movies
- tuning ranking weights such as `WEIGHT_TASTE_TYPICALITY` and `WEIGHT_DINO_KNN`
- continuing feedback-driven learned-head maturation
- documentation cleanup and consolidation

Remaining:

- expose poster counts in sync reporting
- clear stale `poster_path` values when source files disappear
- finish the OCR false-positive labeling workflow preserved in the archive
- continue validating thresholds like `DEDUP_PHASH_THRESHOLD`

## Phase 4 - API And Webhooks

Completed:

- browse, pipeline, feedback, taste, settings, system, webhook, and run-history
  routes
- subtitle and letterbox route families
- documented route corrections where older design notes had drifted

Remaining:

- a dedicated public poster-restore endpoint remains deferred
- dedicated subtitle batch endpoints remain deferred

## Phase 5 - Poster Management

Completed:

- poster cache
- poster restore on upgrades
- self-healing restore scans
- audit history through `ArtworkEvent`

Remaining:

- no major Phase 5 backlog is called out separately from ongoing pipeline
  tuning

## Phase 6 - Web UI

Completed in practice:

- the repository now contains a SvelteKit frontend with pages for films,
  pipeline review, HDR, letterbox, subtitles, settings, onboarding, jobs, and
  dashboard views

Remaining:

- continue refining UX and review workflows preserved in `design/plans/`

## Phase 7 - Deployment And Release

Completed:

- Alembic migrations are present and active
- Docker assets exist under `docker/`

Remaining:

- continue tightening release and setup documentation
- keep deployment guidance aligned with the current compose and hardware
  profile setup

## Deferred

Needs validation:

- **Dolby Vision conversion (movies)**: the Profile 5 / FEL → 8.1 remediation
  path (`dovi_convert`) is implemented but has **not been tested on real
  files** — it must be validated end-to-end (analysis → conversion → playback
  check → restore path) before it can be relied on.

Deferred with no active target phase:

- **Dolby Vision conversion for TV episodes** — deferred until the movie
  conversion path above is tested and trusted (episode DoVi *analysis* ships
  with `design/plans/06`).
- per-episode custom-format score breakdown (planned for a future dedicated
  page, not the HDR pages)
- Sonarr webhook-driven poster restoration (heal scan covers it meanwhile)
- TV cold-start onboarding (rank test)
- ~~**TV letterbox re-encode execution**~~ — un-deferred: schema shipped
  TV-ready in `design/plans/10`; the episode re-encode planner/executor and
  its UI are now planned in `design/plans/13` (operator decision 2026-07-09)
- a native faster-whisper generation engine inside Marquee (the
  `SubtitleGenerator` protocol keeps this open; embedded Subgen ships first
  via `design/plans/08`)
- additional poster sources beyond TMDB in the active pipeline
- broader artwork types such as backdrops, logos, and banners
- deeper health probes and some storage/runtime follow-up work
- future ranking and UX experiments currently preserved in `design/plans/`
