# Marquee Reset-Window Miscellaneous Fixes

**Decided:** 2026-07-12
**Status:** Required companion work to the clean-slate PgQueuer program
**Delivery program:** [clean-slate migration](job-system-pgqueuer-migration.md)
**Related architecture:** [direct PgQueuer adoption](job-system-pgqueuer-direct-adoption.md)
**Chunk 3 filesystem work:** [JMC3A execution kernel and filesystem safety](jmc3a-execution-kernel-and-filesystem-safety.md)
**Chunk 3 backup/ingress work:** [JMC3C backup, ingress, and certification](jmc3c-backup-ingress-and-certification.md)
**Final frontend/test/CI work:** [JMC6A shared Activity client and progress](jmc6a-shared-activity-client-and-progress.md),
[JMC6B Projection Room and feature pages](jmc6b-projection-room-and-feature-pages.md), and
[JMC6C zero-green certification and CI](jmc6c-zero-green-certification-and-ci.md)

## Purpose

The database reset and API replacement create a one-time opportunity to correct application
boundaries that are not queue algorithms but would otherwise weaken the new job system.
This document records the accepted findings, explicit deferrals, implementation placement,
and final green-test/CI requirements.

This is not a second migration program. The accepted work is folded into the six existing
chunks according to dependency and risk. The final GitHub Actions rewrite happens only after
the local target suite is green, so CI automation reflects the finished architecture rather
than preserving obsolete runtime assumptions.

## Scope decisions

### Accepted into the program

The program will address:

1. filesystem confinement and unsafe raw-path fallback;
2. cross-process runtime-configuration consistency;
3. durable history surviving deletion/retirement of live library projections;
4. consistent database plus `DATA_DIR` backup pairs and an honest restore contract;
5. liveness/readiness and schema/runtime compatibility checks;
6. removal of the unused inline `/api/test` pipeline endpoint;
7. legacy-test triage and replacement, with zero failures required at completion;
8. backend/frontend API contract generation and drift prevention;
9. streaming request-size enforcement at the frontend proxy and backend;
10. stale frontend page-state cleanup;
11. GitHub Actions modernization after the local green baseline.

### Explicitly deferred

These findings remain real but are not implementation requirements of this program:

- **Browser authentication and authorization.** User/session/role/CSRF work is deferred to a
  later security phase. The current API-key boundary is not being redesigned here.
- **Database-reset endpoint replacement.** `POST /api/system/reset-db` and the complete
  DB-plus-`DATA_DIR` destructive reset workflow are deferred for separate owner work.
- **Docker least-privilege hardening.** Non-root containers, capability dropping, read-only
  filesystems, and per-service media mounts are deferred to the Docker phase.
- **Webhooks.** Radarr/Sonarr/Subgen webhook behavior is deferred. Route-level webhook tests
  are removed from the target test baseline; webhook implementation is not certified by
  this program and must not be counted as a completed job family.

Deferral is not an assertion of safety or completion. The first public-release checklist
must either implement these items or continue to mark the associated surface unsupported.

## Filesystem boundary hardening

### Confirmed problems

Path confinement currently compares strings with `startswith`. A sibling such as
`/media/movies-old` can therefore pass a root check for `/media/movies`. This pattern exists
in media-root validation, poster-run image serving, letterbox preview serving, letterbox
target checks, onboarding images, and maintenance/artifact handlers.

Poster deletion is worse: `_delete_subject_poster` validates the parent and then, on any
exception, retries by unlinking the raw database `poster_path`. That fallback defeats the
validation it just performed. Radarr Rename also stores an external folder path without the
normal translation/confinement boundary.

### Target rules

- Introduce one filesystem-boundary service for media, application data, artifacts, cache,
  staging, backup, and temporary roots.
- Resolve both candidate and root and use `Path.is_relative_to`; string-prefix checks are
  prohibited.
- Reject file-changing work when the applicable allowed roots are empty or unresolved.
- Treat paths from integrations, payloads, database rows, archives, and tool output as
  untrusted until classified and confined.
- Separate confined storage keys from physical paths in public APIs and artifact metadata.
- Remove the raw poster unlink fallback. Validation failure records an error and performs no
  filesystem mutation.
- Revalidate immediately before destructive use and use directory-relative/open-descriptor
  operations where practical to reduce symlink/time-of-check races.
- Centralize serving, copying, atomic replacement, deletion, archive extraction, and cleanup;
  direct route-level filesystem mutation fails static review.

### Gate

Tests cover sibling-prefix escapes, `..`, symlinks, missing roots, path replacement after
planning, poisoned database values, archive traversal, arbitrary file serving, and deletion.
The source gate finds no ad hoc string-prefix confinement or raw fallback delete.

## Distributed runtime configuration

### Confirmed problem

The API mutates in-memory `settings`, `pipeline_settings`, and `subtitle_settings` singletons
and writes JSON override files. Dedicated workers and the scheduler are separate processes;
their imported singleton values do not change with the API process. Concurrent writers can
also lose updates to the shared JSON files. A job may therefore execute with a different
policy from the value shown in the UI.

### Target model

- Environment/container secrets remain restart-scoped and are never written into product
  configuration documents.
- Runtime-mutable non-secret configuration is stored transactionally with a monotonically
  increasing version and optimistic-concurrency token.
- API, workers, and scheduler load the same validated effective version and receive a
  notification/invalidation when it changes.
- Each canonical job snapshots the configuration version and the bounded execution-relevant
  values needed to explain/reproduce its result.
- Definitions declare which settings are live, next-job, or restart-scoped. UI updates show
  when a change takes effect.
- Concurrent updates conflict rather than silently overwriting one another.
- Corrupt/unavailable configuration leaves the last valid version active and raises an
  Operations alert.

## Durable library identity and history

### Confirmed problem

`PipelineRun`, `ArtworkEvent`, `MediaFile`, `MediaJob`, letterbox, and Dolby Vision records
have cascading foreign keys to Movie/Series/Season/Episode/MediaFile rows. Those live rows
are integration projections and can be retired or recreated. Cascading them into audit data
contradicts the durable subject-snapshot and Projection Room history contract.

### Target rules

- Canonical jobs, attempts, events, outcomes, logs, artifacts, and immutable subject
  snapshots survive live-library deletion.
- Historical domain outcomes use nullable/`SET NULL` references or stable identity records,
  never cascade through an ephemeral integration projection.
- Live current-state projections may cascade only when their loss cannot destroy history or
  evidence.
- Library synchronization retires/marks absent entities before physical deletion.
- Exactly one canonical subject/detail relationship is enforced without requiring the live
  subject row to remain.
- Tests delete/retire movies, series, seasons, episodes, and media files and prove History,
  retry lineage, logs, artifacts, and presentations remain readable.

## Backup and restore consistency

### Confirmed problem

Backup creation takes `pg_dump` and then independently archives `DATA_DIR`. Jobs can change
artifact/log/model files and their database metadata between those two snapshots. The API
restore endpoint currently validates a backup and returns `restored=false`; actual restore
belongs to an external maintenance command.

### Target contract

- Backup is an ordinary canonical maintenance job but acquires the exclusive maintenance
  barrier before the consistency point.
- Active log segments/artifact writers are sealed or checkpointed before the snapshot.
- The manifest records Marquee schema revision, PgQueuer version/schema mode, configuration
  version, database snapshot identity, file checksums/sizes, and included/excluded roots.
- Database credentials are not placed in visible process arguments; use a confined pgpass
  file or supported environment/descriptor mechanism.
- Restore remains an offline maintenance operation. The product API may validate and stage
  a request, but must not claim the system has been restored.
- Certification restores into a fresh target and verifies job/history/log/artifact/model
  linkage, followed by application and PgQueuer schema checks.

## Health and compatibility readiness

### Confirmed problem

`/health` reports `degraded` in its JSON when PostgreSQL fails but still returns HTTP 200.
It checks only `SELECT 1`, so a reachable database with a missing/wrong Marquee or PgQueuer
schema is considered healthy.

### Target endpoints

- **Liveness** reports only whether the API process/event loop can serve requests and does
  not require PostgreSQL.
- **Readiness** returns 503 unless PostgreSQL is reachable, the exact Marquee baseline is
  compatible, PgQueuer durable schema/version is compatible, required event infrastructure
  is initialized, and mandatory configuration is valid.
- **Operations health** separately reports worker/scheduler/node capability, PgQueuer queue
  health, event lag, log/artifact storage, and integration state.
- Migration/API/worker/scheduler startup fails closed on incompatible schema rather than
  waiting for ordinary requests to produce 500s.

Tests cover unavailable PostgreSQL, empty/wrong schema, PgQueuer mismatch, migration in
progress, listener degradation, and recovery.

## Removed inline test endpoint

The obsolete `POST /api/test/pipeline/movie/{id}` route ran a full poster pipeline inline in
the API, outside canonical job safety. It is removed immediately:

- delete `marquee/api/routes/test_pipeline.py`;
- remove its import/router registration from `marquee/main.py`;
- import shared test helpers directly from their real owner,
  `marquee.pipeline.runner`;
- ensure OpenAPI/static searches contain no `/api/test/pipeline` path.

No production replacement is required. Normal poster execution uses canonical jobs.

## API contract and frontend state

### Confirmed contract drift

`POST /api/subtitles/scan-library` requires a JSON `LibraryScanRequest`, while the frontend
sends `force` as a query parameter and no body, producing 422. The frontend also maintains a
large handwritten type surface with broad `Record<string, unknown>`/`any` use in critical
media-editing flows.

### Target contract

- The target OpenAPI document is generated deterministically from the clean canonical API.
- Generate or derive the TypeScript client/types from that document; do not duplicate job,
  command, progress, presentation, or media-target wire types manually.
- Critical responses receive runtime boundary validation where a malformed value could
  produce a destructive request or misleading success state.
- API schema compatibility/version is explicit before the first release.
- Contract tests exercise every frontend API function against the corresponding FastAPI
  request/response schema, including omitted/default bodies and error envelopes.

### Frontend state cleanup

Svelte currently reports initial-value capture warnings on several routes, and many other
instances suppress the same warning. During the Projection Room/frontend rebuild:

- server data remains derived from page data unless intentionally copied into an editable
  draft;
- drafts reset when subject ID or source version changes;
- navigation/invalidation cannot retain the prior movie/show/job/filter state;
- shared stores own cross-page state; page components do not create hidden parallel sources
  of truth;
- the final `svelte-check` result has zero warnings, not merely zero errors.

## Request-size enforcement

The backend currently trusts `Content-Length`, while the frontend proxy buffers every
non-GET body with `request.arrayBuffer()`. Requests without a length or using streaming can
bypass the backend cap or exhaust frontend memory first.

Implement streaming byte accounting at the first public ingress and the FastAPI receive
boundary. Reject malformed lengths, stop reading immediately at the cap, apply smaller
endpoint-specific limits where possible, and exempt only intentionally bounded streaming
downloads/SSE responses. Tests cover omitted length, chunked transfer, mismatched length,
slow upload, proxy/backend limits, and connection cleanup after 413.

## Current failing-test inventory

### Measured baseline

On 2026-07-12, the isolated PostgreSQL suite with `DEBUG=true` produced **814 passed, 31
failed, 2 warnings**. Ruff passed. `svelte-check` produced zero errors and 16 warnings.

The 31 failing tests are:

```text
tests/test_cooperative_cancellation.py
  test_subtitle_scan_all_raises_when_registry_event_is_set

tests/test_dev_ocr_labels.py
  test_capture_false_rejection_uses_this_runs_log
  test_capture_synthesises_from_archive_when_log_missing
  test_capture_ignores_stale_pipeline_log_from_another_run
  test_capture_false_acceptance_with_nan_features
  test_capture_flags_missing_image_and_ocr_diagnostics
  test_capture_includes_full_ocr_trace
  test_capture_flags_stale_null_ocr_read
  test_capture_does_not_flag_pre_ocr_reject
  test_list_run_labels_returns_persisted_filenames

tests/test_frontend_gap_routes.py
  test_media_job_snapshot_reflects_generic_job_failure
  test_scan_library_subtitles_endpoint
  test_subtitle_scan_all_handler

tests/test_jobs.py
  test_create_and_run_never_retries_even_for_retryable_types
  test_cancelling_parent_batch_cascades_and_preserves_completed_children
  test_cancelled_before_execution_updates_parent_to_terminal

tests/test_letterbox_tv_api.py
  test_tv_dev_reset_all_deletes_episode_rows_and_previews_only

tests/test_pipeline_revised.py
  test_effective_ocr_workers_caps_cuda_unless_gpu_forced

tests/test_run_endpoints.py
  test_run_conflict_returns_409
  test_events_404_for_unknown_run
  test_run_refused_during_rebuild
  test_retrain_refused_during_run
  test_cancel_retrain_endpoint_requests_process_stop

tests/test_sync.py
  test_movie_media_replacement_resets_letterbox_state_on_signature_mismatch
  test_episode_media_replacement_resets_stale_letterbox_state_and_links

tests/test_system_metrics.py
  test_metrics_history_returns_points_rates_and_job_overlay

tests/test_taste_artifacts.py
  test_heads_endpoint_backfills_active_head
  test_taste_status_normalizes_duplicate_active_profiles

tests/test_webhooks_heal.py
  test_webhook_upgrade_restores_poster
  test_webhook_upgrade_noop_when_poster_survives

tests/test_whisper_catalog.py
  test_gpu_recommendation_prefers_turbo_on_8gb
```

### Triage policy

Do not mechanically change assertions until they pass. At the final stabilization stage:

1. Remove tests for deleted custom-runtime/compatibility behavior.
2. Rewrite still-required behavior against PgQueuer and canonical APIs.
3. Fix confirmed product defects before changing their assertions.
4. Preserve/rebuild destructive media, cancellation, fencing, path, backup, progress, and
   subject-history regression coverage.
5. Remove route-level webhook tests because webhooks are deferred.
6. Add a short disposition record mapping every former failing test to removed, replaced,
   or fixed.

Confirmed defects already visible in the list include ignored scan-all cancellation when no
loop iteration reaches its cancellation check, letterbox replacement assigning nullable
state to a non-null column, and the scan-library frontend/backend request mismatch. Other
failures may be stale fixtures or expectations and require classification after the target
architecture lands.

Completion requires the entire retained backend suite, target PgQueuer integration suite,
frontend checks, and contract tests to pass with zero failures. No quarantine, blanket
`xfail`, or ignored test job satisfies completion.

## Final GitHub Actions modernization

Modernize `.github/workflows/ci.yml` only after the local target suite is green.

The current workflow is out of date for the application because it has no PostgreSQL
service despite the session fixture requiring PostgreSQL, checks a nonexistent
`design/api-schema.json`, runs no frontend checks, does not install/verify PgQueuer's durable
schema, and tests only Python 3.12 even though the project supports 3.12 and 3.13.

The replacement workflow must:

- provision PostgreSQL with a health check and isolated CI credentials;
- install the Marquee target baseline and pinned PgQueuer durable schema;
- run fresh-schema equivalence and migration/reset rehearsal tests;
- run backend lint and the complete retained backend/integration suite on supported Python
  versions selected by the project;
- use `npm ci`, then run frontend formatting/lint, `svelte-check`, tests, and production
  build on the pinned Node version;
- generate OpenAPI and the frontend client/types, then fail on an uncommitted diff;
- run bounded security/path/secret scans and ensure deferred production routes are not
  accidentally certified;
- upload useful test reports/coverage on failure without uploading secrets or media;
- preserve branch/PR concurrency cancellation and least-required token permissions.

Docker image/build hardening remains deferred and is not smuggled into this workflow update.

## Placement in the six chunks

| Program stage | Miscellaneous work |
|---|---|
| Chunk 1 | Correct liveness/readiness, schema/PgQueuer compatibility checks, and startup failure behavior |
| Chunk 2 | Configuration versioning/snapshots, non-cascading durable history, authoritative OpenAPI/type contract |
| Chunk 3 | Filesystem boundary, raw-delete removal, backup consistency/restore contract, streaming request limits |
| Chunks 4–5 | Apply confined paths and configuration snapshots to every migrated handler; prove no inline/test executor remains |
| Chunk 6 | Repair frontend state/contracts, triage and replace legacy tests, reach the complete local green baseline |
| After local green | Rewrite GitHub Actions and require the same green result in CI before merge |

## Completion criteria

The accepted miscellaneous work is complete when:

- no unsafe raw-path fallback or string-prefix confinement remains;
- API/workers/scheduler agree on a versioned configuration and each job records its effective
  execution configuration;
- live subject deletion cannot erase canonical job/history/evidence;
- backup restore into a fresh target preserves DB/`DATA_DIR` linkage;
- readiness returns 503 for database/schema/PgQueuer incompatibility;
- `/api/test/pipeline` and its route module are absent;
- backend/frontend request contracts are generated/verified and the known scan-library drift
  is gone;
- request bodies are bounded without trusting `Content-Length`;
- `ruff`, the full retained backend suite, PgQueuer/media certification, frontend lint,
  `svelte-check`, frontend tests, contract generation, and build are all green;
- the final GitHub Actions workflow reproduces that green baseline on the merge target;
- authentication, reset replacement, Docker hardening, and webhooks remain explicitly
  deferred rather than accidentally reported complete.
