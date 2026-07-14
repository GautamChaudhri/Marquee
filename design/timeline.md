# Timeline

## JMC1 — PgQueuer foundation (planned)

The clean-slate job-manager migration begins with one resumable infrastructure plan:
[`jmc1-pgqueuer-foundation.md`](job-system-update/jmc1-pgqueuer-foundation.md). It replaces
the unreleased Alembic history, installs PgQueuer 1.1.1 in durable mode outside Alembic,
proves same-transaction enqueue through PgQueuer's public `Queries` API, establishes isolated
worker/scheduler roles, and certifies liveness/readiness plus `system_noop`. The first
implementing agent creates the shared
`design/job-system-update/jmc1-pgqueuer-foundation-timeline.md`; the architect intentionally
does not pre-create it. All non-noop job families remain disabled until later chunks.

## JMC2 — Canonical product model (planned)

Chunk 2 is one ordered program split into three independently gated plans:

1. [`jmc2a-canonical-model-and-configuration.md`](job-system-update/jmc2a-canonical-model-and-configuration.md)
   creates the final clean-slate job/evidence schema, durable subject history, library
   retirement semantics, and versioned cross-process configuration authority.
2. [`jmc2b-definition-registry-and-policies.md`](job-system-update/jmc2b-definition-registry-and-policies.md)
   inventories every built-in and establishes the sole typed definition, snapshot, progress,
   retry, safety, action, and execution-policy registry while keeping non-noop dispatch off.
3. [`jmc2c-presentation-and-api-contracts.md`](job-system-update/jmc2c-presentation-and-api-contracts.md)
   completes backend presenters, bounded canonical job APIs, deterministic OpenAPI export,
   and static TypeScript types for the existing frontend fetch runtime.

They execute strictly JMC2A → JMC2B → JMC2C and share
`design/job-system-update/jmc2-canonical-product-timeline.md`. The JMC2A implementer creates
that timeline; the architect intentionally does not pre-create it. The three implementation
plans begin only after JMC1 Phase 5 and its recorded gates are complete.

## JMC3 — Execution safety and evidence (planned)

Chunk 3 is one ordered safety/evidence program split into three independently gated plans:

1. [`jmc3a-execution-kernel-and-filesystem-safety.md`](job-system-update/jmc3a-execution-kernel-and-filesystem-safety.md)
   generalizes delivery through the typed registry, enforces fenced writes and ordered
   advisory gates, contains/cancels child processes, centralizes path confinement, and proves
   coordinator-only staged publication.
2. [`jmc3b-progress-logs-artifacts-and-events.md`](job-system-update/jmc3b-progress-logs-artifacts-and-events.md)
   implements durable semantic progress, native tool adapters, redacted capped attempt logs,
   confined physical/virtual artifacts, and one multiplexed replayable event stream.
3. [`jmc3c-backup-ingress-and-certification.md`](job-system-update/jmc3c-backup-ingress-and-certification.md)
   coordinates PostgreSQL plus `DATA_DIR` backup/restore, replaces buffered/header-only body
   limits with streaming enforcement, and certifies the complete Chunk 3 failure matrix.

They execute strictly JMC3A → JMC3B → JMC3C and share
`design/job-system-update/jmc3-safety-and-evidence-timeline.md`. The JMC3A implementer creates
that timeline; the architect intentionally does not pre-create it. JMC3 begins only after
JMC2C certification. Only `system_noop` remains production-enabled; fixed development/test
canaries exercise the new infrastructure, and real read-only jobs wait for Chunk 4.

## JMC4 — Non-mutating jobs (planned)

Chunk 4 is one ordered migration/certification program split into three independently gated
plans:

1. [`jmc4a-producers-batches-and-schedules.md`](job-system-update/jmc4a-producers-batches-and-schedules.md)
   establishes typed canonical production, atomic fixed/dynamic batches, PgQueuer schedule
   callbacks, and all non-mutating execution classes while leaving real handlers disabled.
2. [`jmc4b-library-scans-and-media-analysis.md`](job-system-update/jmc4b-library-scans-and-media-analysis.md)
   migrates library synchronization, audio/subtitle inventory and policy audits, letterbox
   detection, and Dolby Vision analysis without source-media mutation.
3. [`jmc4c-poster-ml-and-certification.md`](job-system-update/jmc4c-poster-ml-and-certification.md)
   migrates non-deploying poster analysis, immutable taste/model work, poster rescans, and
   certifies the complete Chunk 4 schedule/batch/fairness/saturation matrix.

They execute strictly JMC4A → JMC4B → JMC4C and share
`design/job-system-update/jmc4-nonmutating-jobs-timeline.md`. JMC4A creates that timeline;
the architect intentionally does not pre-create it. Each plan retains phase commits until
all gates pass, then makes verified repository-external recovery material and compacts only
its own unpushed range to one tree-identical completion commit/tag. No JMC4 implementer
pushes. Recommended implementation tiers are God for JMC4A, Mid for JMC4B, and God for
JMC4C.

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
