# JMC2 Canonical Product Timeline

Shared implementer log for JMC2A → JMC2B → JMC2C. Append after every phase commit.

## Initial state — 2026-07-12 (JMC2A Phase A0)

- Branch/HEAD: `job-manager` at `9009be3` (`chunk 1 complete`).
- Working tree: clean; pre-existing untracked `.agents/` preserved (operator-owned, not
  part of this plan).
- Configured Git author: `Gautam Chaudhri <gautam.chaudhri@gmail.com>`; unchanged.
- Tooling: Serena and ByteRover MCP available; RTK proxy active. ByteRover context tree
  is stale for job-system topics (pre-JMC1 SQLite-era entries); code and the JMC plans
  are authoritative. JMC2A results will be curated at completion.
- JMC1 verification: timeline claims confirmed against `git log` — phase commits
  `0ac484f`, `4021ae3`, `dc1f067`, `2f856f1`, `a5ed503`, `2d55a68` all present;
  `2aa7722` (timeline) and `9009be3` (JMC2 plan docs only) contain no code. Alembic has
  the single root head `0001_jmc1`. Only `control`/`system_noop` is dispatch-enabled.
- Test database: default 127.0.0.1:5432 role lacks CREATEDB, so the 11
  `test_jmc1_migration.py` owned-database tests error there. All JMC2A gates therefore
  run against an owned disposable PostgreSQL 18.3 cluster at
  `/tmp/marquee-jmc2a-pg.XewMet`, loopback port 55434, private socket directory, trust
  auth, database `marquee_test`. No operator database or `DATA_DIR` was modified.

## JMC2A baseline (Phase A0)

- `DEBUG=true pytest -q` with `DB_URL=postgresql+asyncpg://marquee@127.0.0.1:55434/marquee_test`:
  **879 passed, 29 failed, 2 warnings** in 50.70s — exactly the JMC1 Phase 5 retained
  set, test-for-test:
  - `tests/test_cooperative_cancellation.py::test_subtitle_scan_all_raises_when_registry_event_is_set`
  - `tests/test_dev_ocr_labels.py::test_capture_false_rejection_uses_this_runs_log`
  - `tests/test_dev_ocr_labels.py::test_capture_synthesises_from_archive_when_log_missing`
  - `tests/test_dev_ocr_labels.py::test_capture_ignores_stale_pipeline_log_from_another_run`
  - `tests/test_dev_ocr_labels.py::test_capture_false_acceptance_with_nan_features`
  - `tests/test_dev_ocr_labels.py::test_capture_flags_missing_image_and_ocr_diagnostics`
  - `tests/test_dev_ocr_labels.py::test_capture_includes_full_ocr_trace`
  - `tests/test_dev_ocr_labels.py::test_capture_flags_stale_null_ocr_read`
  - `tests/test_dev_ocr_labels.py::test_capture_does_not_flag_pre_ocr_reject`
  - `tests/test_dev_ocr_labels.py::test_list_run_labels_returns_persisted_filenames`
  - `tests/test_frontend_gap_routes.py::test_media_job_snapshot_reflects_generic_job_failure`
  - `tests/test_frontend_gap_routes.py::test_scan_library_subtitles_endpoint`
  - `tests/test_frontend_gap_routes.py::test_subtitle_scan_all_handler`
  - `tests/test_jobs.py::test_create_and_run_never_retries_even_for_retryable_types`
  - `tests/test_jobs.py::test_cancelling_parent_batch_cascades_and_preserves_completed_children`
  - `tests/test_jobs.py::test_cancelled_before_execution_updates_parent_to_terminal`
  - `tests/test_letterbox_tv_api.py::test_tv_dev_reset_all_deletes_episode_rows_and_previews_only`
  - `tests/test_pipeline_revised.py::test_effective_ocr_workers_caps_cuda_unless_gpu_forced`
  - `tests/test_run_endpoints.py::test_run_conflict_returns_409`
  - `tests/test_run_endpoints.py::test_events_404_for_unknown_run`
  - `tests/test_run_endpoints.py::test_run_refused_during_rebuild`
  - `tests/test_run_endpoints.py::test_retrain_refused_during_run`
  - `tests/test_run_endpoints.py::test_cancel_retrain_endpoint_requests_process_stop`
  - `tests/test_sync.py::test_movie_media_replacement_resets_letterbox_state_on_signature_mismatch`
  - `tests/test_sync.py::test_episode_media_replacement_resets_stale_letterbox_state_and_links`
  - `tests/test_system_metrics.py::test_metrics_history_returns_points_rates_and_job_overlay`
  - `tests/test_taste_artifacts.py::test_heads_endpoint_backfills_active_head`
  - `tests/test_taste_artifacts.py::test_taste_status_normalizes_duplicate_active_profiles`
  - `tests/test_whisper_catalog.py::test_gpu_recommendation_prefers_turbo_on_8gb`
- `ruff check marquee tests`: passed (ruff 0.15.17).
- Alembic: one head `0001_jmc1`; offline `upgrade head --sql` clean (855 lines).
- Guarded CLI reset (`python -m marquee.dev_reset`) succeeded twice against the owned
  database with identical contract fingerprints:
  `marquee=615d675113b53db90d0c7c93fe7891da178fcafa10e39a623f34c617031a785f`,
  `pgqueuer=19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a`
  (pgqueuer 1.1.1, durable).
- Frontend (`frontend/`): `npm run check` 0 errors / 16 warnings / 8 files;
  `npm run lint` clean; `npm run build` exit 0.

## Phase A0 inventories

- **Legacy job-platform writers still reachable from routes** (all become fail-closed
  in Phase A1): `job_manager.create*` called from `api/routes/{pipeline, pipeline_tv,
  letterbox, hdr, audio_subs, taste, onboarding, system, backup, jobs, subtitles,
  webhooks}.py`, plus `core/jobs/{scheduler, dovi_handlers}.py`,
  `core/media_jobs/manager.py`, `core/system_metrics_sampler.py` (reads).
  `media_job_manager` (excluded media-jobs path) backs subtitle mutation routes via
  `core/media_jobs/manager.py` and `core/jobs/legacy_media.py`.
  `PUT /api/settings` upserts a `JobSchedule` row (`api/routes/settings.py`).
- **Deployment-excluded models** (9 tables in `marquee/models/deployment.py`):
  `job_resources`, `job_resource_reservations`, `job_schedules`, `job_workers`,
  `media_batches`, `media_jobs`, `media_job_events` (deleted in A1);
  `letterbox_reencode_artifacts`, `media_backups` (re-homed to canonical `jobs` FKs and
  restored to deployment metadata in A2).
- **JSON override persistence + singleton mutation to remove** (Phase A3/A5):
  `data/settings_overrides.json` (`marquee/config.py:776–836`),
  `data/pipeline_overrides.json` (`marquee/core/pipeline_config.py:629–662`),
  `data/subtitle_overrides.json` (`marquee/core/subtitles/config.py:120–153`);
  runtime `setattr` sites `api/routes/config.py:115`,
  `api/routes/settings.py:345,351,367`, `api/routes/subtitle_generators.py:136`,
  `api/routes/audio_subs.py:449`, `core/subtitles/embedded_subgen.py:102`;
  `core/backup.py:50` backs up `pipeline_overrides.json`.
- **Secret-edit affordances to remove** (M14): `PUT /api/settings`
  `subgen.callback_token`; `PUT /api/subtitle-generators/subgen/settings`
  `callback_token`; frontend password inputs in
  `frontend/src/lib/components/subtitles/SubtitleSettings.svelte` and
  `frontend/src/routes/audio-subs/movies/[id]/+page.svelte`.
- **Configuration ownership boundary (finalized as a code catalog in A3):**
  database-owned = non-restart pipeline knobs (current `PUT /api/config/pipeline`
  surface minus its `RESTART_REQUIRED` set), non-secret `SUBTITLE_*`/`SUBGEN_*`
  behavior values from `PUT /api/settings` and `PUT /api/audio-subs/preferences`,
  poster formats/restore method, heal enable/interval, non-secret provider
  URL/profile/path-mapping labels. Environment/restart-owned = all secrets including
  `SUBGEN_CALLBACK_TOKEN` (env-only from A5), database/auth/host/port/pool/process-role
  settings, model/artifact paths and execution-provider identity, embedded-Subgen
  process/device/model/concurrency startup-bound values.
- Contract freeze: `tests/fixtures/jmc2a/jmc1_contract_freeze.json` +
  `tests/test_jmc2a_contract_freeze.py` pin the JMC1 deployment table set, job-platform
  columns, lifecycle vocabularies, and reset fingerprints. Phase A1 updates these
  expectations in the same commit that changes the schema.

## Current phase

- Phase A0 — verify JMC1 and freeze contracts: **complete** (`10cb470`).
- Phase A1 — canonical schema and migration: **complete** (`3659626`).
- Phase A2 — durable evidence and subject retirement: **complete** (`6cc38dc`).
- Phase A3 — configuration storage and service: **complete** (`f2662b6`).
- Phase A4 — cross-process cache and job snapshots: **complete** (`e49bd07`).
- Phase A5 — settings API/frontend continuity and certification: **complete** (phase commit:
  this commit).
- JMC2A: **complete**. JMC2B Phase B0 is the next authorized work.

## Phase A1 result

- Replaced the transitional JMC1 lifecycle with canonical `Job`, `JobDispatch`,
  `JobAttempt`, and `JobEvent` fields and constraints. `system_noop` creation and delivery
  now use request documents, immutable subject snapshots, phase/outcome state, fencing, and
  canonical attempt evidence.
- Removed the excluded legacy runtime mappings and custom worker/scheduler implementation.
  Every not-yet-migrated job command now fails closed through one stable HTTP 503 envelope;
  only `system_noop` is admitted by delivery.
- Added the irreversible pre-release forward revision `0002_jmc2a`. It recreates the four
  transitional job tables without backfill, restores re-homed re-encode/backup evidence, and
  leaves PgQueuer DDL exclusively package-owned.
- Updated reset head/fingerprint expectations. A fresh JMC1-to-JMC2A upgrade, `alembic check`,
  offline SQL, and two guarded resets passed against the owned disposable PostgreSQL cluster
  on port 55435. Fingerprints: Marquee
  `095cf7b5e4413d64d7bb494a0bda0ec4e2e1f4ca21f6a8c4d1eafdba5b254484`, PgQueuer
  `19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a`.
- Focused job/gateway/readiness/contract suite: **54 passed**. Affected A1 route, reader,
  migration, and reset suite: **198 passed, 1 retained failure**. Full suite:
  **776 passed, 23 failed, 2 warnings** in 43.08s. Ruff passed.
- The 23 failures are a strict subset of A0's 29 and are unchanged pre-existing failures:
  `test_dev_ocr_labels.py` (9 capture/list cases),
  `test_letterbox_tv_api.py::test_tv_dev_reset_all_deletes_episode_rows_and_previews_only`,
  `test_pipeline_revised.py::test_effective_ocr_workers_caps_cuda_unless_gpu_forced`,
  five `test_run_endpoints.py` cases, two media-replacement cases in `test_sync.py`,
  `test_system_metrics.py::test_metrics_history_returns_points_rates_and_job_overlay`, two
  `test_taste_artifacts.py` cases, and
  `test_whisper_catalog.py::test_gpu_recommendation_prefers_turbo_on_8gb`.
- Superseded test disposition: legacy scheduler/worker/media bridge, legacy Job/MediaJob API,
  retention, child tracking, progress enrichment, legacy system-metrics purge, and legacy
  audio/subtitle mutation execution suites were removed rather than skipped. Remaining route
  tests were converted to assert the intentional fail-closed contract, while canonical no-op,
  schema, migration, activity, and metrics-reader coverage was retained or rewritten.

## Phase A2 result

- Added confined `JobLog`/`JobArtifact` metadata, observation-only `WorkerNode` records, and
  strict 1:1 `MediaOperationDetail` evidence. Deliberate job purge cascades owned evidence;
  deleting a live media file nulls its optional detail link while immutable snapshots remain.
- Added `is_present`, `retired_at`, and `last_seen_at` projection state for Movie, Series,
  Season, Episode, and MediaFile. Authoritative Radarr/Sonarr synchronization now retires
  absent stable identities, reactivates them in place, retires replaced media, and never
  retires from malformed/partial top-level responses. Ordinary library queries exclude
  retired rows.
- Changed historical Movie/Series/Season/Episode/MediaFile links to `ON DELETE SET NULL` and
  added immutable subject snapshots. Regression tests delete movie and complete TV subject
  trees while preserving job, pipeline, artwork/letterbox, log, artifact, and media-detail
  evidence.
- Folded A2 DDL into the single irreversible `0002_jmc2a` revision. Offline SQL, a clean
  forward upgrade, `alembic check`, and two guarded reset repetitions passed. Reset
  fingerprints: Marquee
  `1aca93f96c368164532db5bc474b36735410d4517994d0d655bdf064e3df5cce`, PgQueuer
  `19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a`.
- Focused final evidence suite: **6 passed**. Full suite: **786 passed, 23 failed,
  2 warnings** in 45.02s. Ruff passed. The exact 23 A1-retained failures remain; A2 added no
  failure, error, skip, or xfail.

## Phase A3 result

- Added the code-owned configuration catalog with Pydantic validation owner, public metadata,
  scope, sensitivity, apply mode, and database/environment ownership. Pipeline hot knobs,
  subtitle policy/language/generation values, non-secret Subgen connection labels, poster
  formats/restore, and healing controls are database-owned; secrets, process/device/model
  identity, and filesystem paths remain environment/restart-owned.
- Added immutable `configuration_revisions` plus singleton `configuration_current`, seeded
  revision 1 in migration and guarded reset, and linked canonical job snapshots to immutable
  revisions. Canonical JSON checksums are deterministic and every revision is revalidated
  before use.
- Implemented row-locked optimistic updates: simultaneous version-1 writers produce exactly
  one revision-2 winner and one conflict; stale updates expose only current version/ETag;
  invalid, unknown, secret-like, secret, and restart-owned changes create no revision; no-op
  updates create no churn. `pg_notify('marquee_configuration', decimal_version)` is issued in
  the same transaction as revision insertion/pointer movement.
- Removed every app/pipeline/subtitle JSON override reader/writer, backup inclusion, and
  imported-singleton mutation call site. Backend settings/config mutation routes now write the
  revision service and require `expected_version`; secret mutation is rejected.
- Offline SQL, clean forward upgrade, `alembic check`, seeded-revision inspection, and two
  guarded resets passed. Reset fingerprints: Marquee
  `651ba3f0efe8fffb6a262b95962d9a99b9568609b2bd52936cf34bfac04aba89`, PgQueuer
  `19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a`.
- Focused configuration/settings suite: **37 passed**; contract/backup suite: **10 passed**.
  Full suite: **798 passed, 23 failed, 2 warnings** in 46.93s. Ruff passed. The exact retained
  failure set remains unchanged.

## Phase A4 result

- Added one shared `ConfigurationProvider` lifecycle for API, worker, and scheduler roles. It
  fails closed when no initial revision exists, treats the fixed PostgreSQL notification as an
  invalidation hint, ignores malformed/old/duplicate versions, and repairs from the durable
  pointer at least every 30 seconds.
- Later invalid revisions and database/listener outages retain the last valid state while
  exposing structured valid/stale/error health through settings, system status, and readiness.
  Startup, shutdown, restart, missed-notification repair, and real after-commit notification
  behavior are covered without mutating Pydantic settings singletons.
- Added bounded `snapshot_for(keys)` validation for database-owned, execution-scoped,
  non-secret keys. `system_noop` records its configuration version and bounded values inside
  the same transaction that creates/enqueues the canonical job.
- Focused cache/settings/gateway/readiness suite: **89 passed, 1 retained failure**. Full suite:
  **805 passed, 23 failed, 2 warnings** in 47.76s. Ruff and `git diff --check` passed. The exact
  A3-retained failure set is unchanged; A4 added no failure, error, skip, or xfail. The phase
  has no schema change, so the A3 reset fingerprints remain authoritative.

## Phase A5 result

- Settings, pipeline, Subgen, and global audio/subtitle preference frontend clients now send
  the version observed with the edited document. A 409 reloads current values, explains the
  conflict, and keeps the local draft available for an explicit retry. Frontend types include
  version, ETag, health/stale state, and ownership/apply metadata.
- Removed both callback-token password fields and every frontend token draft/payload. The UI
  shows callback-token presence as environment-only redacted state. The Subgen form writes
  only database-owned connection/label values and presents model/device/compute/concurrency
  as restart-owned read-only values. Removed the final JSON-override wording.
- Added settings, Subgen, and audio/subtitle stale-version route regressions and ownership
  metadata assertions. Focused configuration/cache/API suite: **42 passed**. Full suite:
  **808 passed, 23 failed, 2 warnings** in 46.93s. Ruff passed; no failure, error, skip, or
  xfail was added.
- Frontend: `npm run check` **0 errors, 16 pre-existing warnings in 8 files**;
  `npm run lint` clean; `npm run build` exit 0. The pinned Prettier check also required a
  mechanical, behavior-free reformat of two previously committed Svelte files.
- Final schema certification: sole Alembic head `0002_jmc2a`; offline upgrade SQL clean
  (**1,337 lines**); `alembic check` reports no new operations; migration/reset suite
  **14 passed**, including two identical guarded resets. Contract fingerprints are Marquee
  `651ba3f0efe8fffb6a262b95962d9a99b9568609b2bd52936cf34bfac04aba89` and PgQueuer
  `19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a`
  (`1.1.1`, durable).

## Final configuration ownership/apply-mode inventory

- Code-owned catalog: **185 keys**. Every database-owned key is public and `next_job`; every
  environment-owned key is `restart`.
- App: **6 database/application** keys (`HEAL_ENABLED`, `HEAL_INTERVAL_MINUTES`,
  `MOVIE_POSTER_FORMAT`, `POSTER_RESTORE_METHOD`, `SEASON_POSTER_FORMAT`,
  `SERIES_POSTER_FORMAT`); **1 environment/application** key (`POSTER_BACKUP_DIR`).
- Pipeline: **109 database/execution** keys — every catalogued pipeline field except the
  following **22 environment/execution** keys: `AESTHETIC_MODEL_PATH`, `AI_MODEL`,
  `CLIP_MODEL_PATH`, `DINO_MODEL_PATH`, `EMBEDDING_CACHE_DIR`, `EXECUTION_PROVIDER`,
  `FACE_MODEL_PATH`, `FEEDBACK_LABELS_PATH`, `LEARNED_HEAD_PATH`, `LEARNED_HEAD_TV_PATH`,
  `NEGATIVE_DATA_DIR`, `NEGATIVE_DATA_TV_DIR`, `ONBOARDING_SEED_PROFILE_PATH`,
  `ONBOARDING_STATE_PATH`, `ONBOARDING_TASTE_TEST_DIR`, `PERSON_MODEL_PATH`,
  `TASTE_PROFILE_PATH`, `TASTE_PROFILE_TV_PATH`, `TRAINING_DATA_DIR`,
  `TV_TRAINING_SEASON_DIR`, `TV_TRAINING_SHOW_DIR`, `ZEROSHOT_AXES_PATH`.
- Subtitle/Subgen database/execution: **25 keys** — `AUDIO_SUBS_DEEP_SCAN_BATCH`,
  `AUDIO_SUBS_DEEP_SCAN_ENABLED`, `AUDIO_SUBS_DEEP_SCAN_HOUR`, `SUBGEN_DEPLOYMENT`,
  `SUBGEN_LOCAL_PATH_PREFIX`, `SUBGEN_MODE`, `SUBGEN_MODEL_LABEL`,
  `SUBGEN_NAME_INCLUDES_MODEL`, `SUBGEN_NAME_INCLUDES_SUBGEN`, `SUBGEN_NAMING_TYPE`,
  `SUBGEN_PROFILE_NAME`, `SUBGEN_REMOTE_PATH_PREFIX`, `SUBGEN_URL`, `SUBTITLE_BACKUP_MODE`,
  `SUBTITLE_ENABLED`, `SUBTITLE_EXTERNAL_DELETE_MODE`, `SUBTITLE_GENERATION_CONCURRENCY`,
  `SUBTITLE_MUTATION_CONCURRENCY`, `SUBTITLE_PREFERRED_AUDIO_LANGUAGES`,
  `SUBTITLE_PREFERRED_LANGUAGES`, `SUBTITLE_PREFERRED_SUBTITLE_LANGUAGES`,
  `SUBTITLE_PROTECT_FORCED`, `SUBTITLE_PROTECT_LAST_FULL_DIALOGUE`,
  `SUBTITLE_SCAN_CONCURRENCY`, `SUBTITLE_UNKNOWN_LANGUAGE_ACTION`.
- Subtitle/Subgen environment/execution public: **21 keys** — `SUBGEN_COMPUTE_TYPE`,
  `SUBGEN_CONCURRENT_TRANSCRIPTIONS`, `SUBGEN_EMBEDDED_PORT`, `SUBGEN_GPU_INDEX`,
  `SUBGEN_MODEL_PATH`, `SUBGEN_POLL_SECONDS`, `SUBGEN_TIMEOUT_MINUTES`,
  `SUBGEN_TRANSCRIBE_DEVICE`, `SUBGEN_WHISPER_MODEL`, `SUBGEN_WHISPER_THREADS`,
  `SUBTITLE_BACKUP_COPY_BWLIMIT_KBPS`, `SUBTITLE_FILE_STABILITY_SECONDS`,
  `SUBTITLE_HARDLINK_POLICY`, `SUBTITLE_IMPORT_DELAY_SECONDS`,
  `SUBTITLE_JOB_EVENT_RETENTION_DAYS`, `SUBTITLE_MUTATION_TEMP_DIR`,
  `SUBTITLE_MUTATION_USE_TEMP_DIR`, `SUBTITLE_NORMALIZE_TEXT_UTF8`,
  `SUBTITLE_PLAN_TTL_MINUTES`, `SUBTITLE_PREVIEW_MAX_CUES`,
  `SUBTITLE_TEMP_SPACE_MARGIN_PERCENT`; secret: **1 key**, `SUBGEN_CALLBACK_TOKEN`.

## Final retained pytest failures

- `tests/test_cooperative_cancellation.py::test_subtitle_scan_all_raises_when_registry_event_is_set`
- `tests/test_dev_ocr_labels.py::test_capture_false_rejection_uses_this_runs_log`
- `tests/test_dev_ocr_labels.py::test_capture_synthesises_from_archive_when_log_missing`
- `tests/test_dev_ocr_labels.py::test_capture_ignores_stale_pipeline_log_from_another_run`
- `tests/test_dev_ocr_labels.py::test_capture_false_acceptance_with_nan_features`
- `tests/test_dev_ocr_labels.py::test_capture_flags_missing_image_and_ocr_diagnostics`
- `tests/test_dev_ocr_labels.py::test_capture_includes_full_ocr_trace`
- `tests/test_dev_ocr_labels.py::test_capture_flags_stale_null_ocr_read`
- `tests/test_dev_ocr_labels.py::test_capture_does_not_flag_pre_ocr_reject`
- `tests/test_dev_ocr_labels.py::test_list_run_labels_returns_persisted_filenames`
- `tests/test_letterbox_tv_api.py::test_tv_dev_reset_all_deletes_episode_rows_and_previews_only`
- `tests/test_pipeline_revised.py::test_effective_ocr_workers_caps_cuda_unless_gpu_forced`
- `tests/test_run_endpoints.py::test_run_conflict_returns_409`
- `tests/test_run_endpoints.py::test_events_404_for_unknown_run`
- `tests/test_run_endpoints.py::test_run_refused_during_rebuild`
- `tests/test_run_endpoints.py::test_retrain_refused_during_run`
- `tests/test_run_endpoints.py::test_cancel_retrain_endpoint_requests_process_stop`
- `tests/test_sync.py::test_movie_media_replacement_resets_letterbox_state_on_signature_mismatch`
- `tests/test_sync.py::test_episode_media_replacement_resets_stale_letterbox_state_and_links`
- `tests/test_system_metrics.py::test_metrics_history_returns_points_rates_and_job_overlay`
- `tests/test_taste_artifacts.py::test_heads_endpoint_backfills_active_head`
- `tests/test_taste_artifacts.py::test_taste_status_normalizes_duplicate_active_profiles`
- `tests/test_whisper_catalog.py::test_gpu_recommendation_prefers_turbo_on_8gb`

## Performed manual checks

- Source searches found no runtime JSON override filename/reference, imported settings
  singleton mutation, callback-token input/draft, compatibility view, or alternate enabled
  delivery path. Gateway/delivery still reject every type except `system_noop` version 1.
- Settings responses were inspected through route tests for redaction, current version/ETag,
  health, and exact ownership/apply metadata. No live deployment or browser smoke was run.

## Exact JMC2B starting point

1. Begin JMC2B Phase B0 only: verify all JMC2A phase commits/gates and recreate the disposable
   target schema from the recorded `0002_jmc2a` head/fingerprints.
2. Reconfirm that no transitional lifecycle runtime object or custom worker/writer remains and
   only `control/system_noop` is dispatch-enabled.
3. Re-inventory decorators, handler/media-operation maps, route-created job types, schedule
   callbacks, healing jobs, and parent-only batch types; record drift and the fresh
   pytest/Ruff/frontend baseline before implementing the Phase B1 `JobDefinition` registry.

## Deviations

- The original A0 PostgreSQL cluster on port 55434 was no longer running when work resumed.
  Verification used a newly owned disposable cluster at
  `/tmp/marquee-jmc2a-resume.loyICz`, port 55435; no operator database or data directory was
  touched.

## Pending operator actions

- Inherited from JMC1 (unchanged, listed in
  `jmc1-pgqueuer-foundation-timeline.md`): real-deployment SIGKILL-while-picked,
  PostgreSQL restart after pickup, notification-channel disruption, migration rerun
  with held/queued/deferred tickets.
- Live database: the operator applies `0002_jmc2a` (or performs the sanctioned
  development reset) manually once JMC2A completes; no live migration is run by the
  implementer.

## JMC2B Phase B0 — in progress (2026-07-13)

- Branch/HEAD: `job-manager` at `2d6f678` (`finish versioned configuration`). Configured
  Git author remains Gautam Chaudhri `<gautam.chaudhri@gmail.com>`.
- Tracked working tree was clean when B0 resumed. Three unrelated untracked JMC3 plan files
  are preserved and excluded from JMC2B commits.
- Verified JMC2A commits: `10cb470`, `3659626`, `6cc38dc`, `f2662b6`, `e49bd07`, and
  `2d6f678`. Sole Alembic head is `0002_jmc2a`; offline upgrade SQL remains 1,337 lines.
- Recreated an owned disposable PostgreSQL 18.3 cluster under `/tmp` and ran the guarded
  reset. Contract fingerprints exactly match JMC2A: Marquee
  `651ba3f0efe8fffb6a262b95962d9a99b9568609b2bd52936cf34bfac04aba89`; PgQueuer
  `19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a`
  (`1.1.1`, durable). The temporary server was stopped after verification.
- Baseline: full pytest reproduced **808 passed, 23 retained failures, 2 warnings**; no new
  failure or error. `ruff check marquee tests` passed. Frontend check reported 0 errors and
  the same 16 warnings in 8 files; lint and build passed.
- Static/runtime inspection confirms the canonical schema has no legacy lifecycle table,
  custom claim/lease/heartbeat authority, or executable legacy writer. PgQueuer worker and
  scheduler roles remain separate. Only `control/system_noop` is dispatch-enabled.
- Serena regenerated 30 decorated handlers, 12 media operations, route construction sites,
  schedule/healing producers, retry linkage, and six parent-only types. The inventory has no
  type drift from JMC2B section 6. ByteRover confirms B0 is the next authorized phase.
- Current phase: B0 inventory/interface contract freeze in progress. Exact next steps:
  commit machine-checkable inventories and stable taxonomy/registry interface tests, run
  focused/full/Ruff gates, then begin B1 strict document envelopes and registry core.
- Deviation: the `brv` CLI is absent, but the required ByteRover MCP query/curate interface
  is available and used. Serena MCP is available and used.

## JMC2B Phase B0 result

- Added stable serialized execution-class, feature-area, trigger, effect-safety, progress,
  attention, action, and migration-state taxonomies plus the bounded duplicate-safe
  `JobDefinitionRegistry` interface.
- Added a source-derived inventory freeze covering **30 handlers**, **12 media operations**,
  **29 route-constructed types**, **6 parent-only types**, healing/schedule producers, and
  the reserved disabled webhook type. The complete built-in union is **48 types** and has no
  difference from JMC2B section 6.
- Focused inventory/interface suite: **3 passed**. Full suite: **811 passed, 23 retained
  failures, 2 warnings** in 47.72s. The exact JMC2A retained failure set is unchanged; B0
  added no failure, error, skip, or xfail. Ruff passed.
- Schema/frontend gates are unchanged from the verified B0 baseline; B0 changes no schema or
  frontend contract. Only `system_noop` remains dispatch-enabled.
- Phase B0 commit: `32eb5a8` (`freeze job definition inventory`).
- Current phase: B0 complete; B1 is next. Exact next steps: implement strict current-version
  request/result/error models and typed unsupported-version errors, then expand the registry
  to immutable complete definitions with startup validation while retaining fail-closed
  dispatch.
- Pending operator work remains unchanged from JMC2A/JMC1.

## JMC2B Phase B1 result

- Added strict frozen request/result/error model foundations with `extra=forbid`, typed
  permanent unsupported-version errors, immutable version/model maps, and an explicit
  upcaster chain. The only upcaster fixture performs a real `name` to `display_name`
  conversion; no identity chain exists.
- Added bounded safe error summaries/remediation/diagnostics. Secret-like keys, unconfined
  absolute paths, extra fields, oversized content, and unsupported future versions fail
  validation.
- Expanded `JobDefinition` and `JobDefinitionRegistry` into immutable policy containers with
  bounded lookup/iteration, duplicate/type/presenter/config validation, exact coverage
  checks, and fail-closed dispatch lookup. Registry validation prohibits dispatch for every
  type except `control/system_noop`.
- Removed the decorator's independent instant/runtime policy maps and their arguments.
  Decorators now retain handler inventory only; policy authority belongs to definitions.
- Focused B1 contract suite: **8 passed**. Full suite: **815 passed, 23 retained failures,
  2 warnings** in 48.63s. The exact retained failure set is unchanged; B1 added no failure,
  error, skip, or xfail. Ruff passed. No schema or frontend change applies.
- Phase B1 commit: `ac60f8d` (`build job definition registry`).
- Current phase: B1 complete; B2 is next. Exact next steps: implement every discriminated
  subject snapshot variant and transaction-friendly builders, connect bounded definition
  configuration keys to JMC2A `snapshot_for`, and prove snapshots render after live subject
  deletion.
- Inventory reconciliation: unchanged at 48 built-ins (30 handlers, 12 media operations,
  6 parents). Only `system_noop` remains dispatch-enabled.
- Pending operator work remains unchanged from JMC2A/JMC1.

## JMC2B Phase B2 result

- Added the strict version-1 discriminated `SubjectSnapshot` union covering movie, series,
  season, episode, media file, audio/subtitle track, poster candidate set,
  model/profile/training, aggregate batch, maintenance scope, and system work.
- Media snapshots preserve stable IDs, complete useful title/year/season/episode hierarchy,
  display filename, media kind, artwork key, integration IDs, track language/codec/channels,
  flags, stream/tool identity, and embedded/external state where applicable. Builders strip
  directory paths and never include secrets or unbounded child lists.
- Added transaction-friendly Movie/Series/Season/Episode builders with explicit not-found
  failures and pure builders for already-loaded media/track/non-media subjects. Serialized
  movie and complete TV-tree snapshots validated after the live rows were physically
  deleted.
- Added the definition-owned configuration snapshot helper. JMC2A remains the validation
  authority: only declared database-owned, public, execution-scoped keys enter the snapshot;
  secret/restart keys fail closed.
- Focused B2 suite: **8 passed**. Full suite: **823 passed, 23 retained failures, 2 warnings**
  in 49.43s. The retained failure set is unchanged; B2 added no failure, error, skip, or
  xfail. Ruff passed. No schema or frontend change applies.
- Phase B2 commit: `7f900a4` (`add durable job subjects`).
- Current phase: B2 complete; B3 is next. Exact next steps: implement pure `JobProgress`
  validation/transitions, progress policies, retry/safety/action policies, and fixed/sealed
  parent aggregation with terminal outcome precedence.
- Inventory and dispatch reconciliation are unchanged: 48 definitions required; only
  `control/system_noop` may be enabled.
- Pending operator work remains unchanged from JMC2A/JMC1.

## JMC2B Phase B3 result

- Added a strict immutable `JobProgress` contract with server-owned percentages,
  determinate/indeterminate/none measurements, finite bounded metrics, wait reasons,
  bounded concurrent-subject summaries, and attempt/fence/sequence identity.
- Added definition-owned progress policies with stable stages, optional native adapter
  identifiers, persistence cadence, and ETA credibility rules. Transition validation rejects
  stale writers and sequence regressions, preserves monotonic overall progress, and permits
  current-scope resets only when the scope identity changes.
- Added bounded retry classifiers and decisions, unsafe-mutation proof requirements,
  state-aware action computation, and fixed-membership sealed parent aggregation. Terminal
  failure/cancellation retains the last measurement; successful completion alone closes the
  remaining measurement.
- Focused B3 suite: **10 passed**. Full suite: **833 passed, 23 retained failures,
  2 warnings** in 52.07s. The exact retained failure set is unchanged; B3 added no failure,
  error, skip, or xfail. Ruff passed. No schema or frontend change applies.
- Phase B3 commit: `86daa96` (`define job execution policies`).
- Current phase: B3 complete; B4 is next. Exact next steps: build the complete immutable
  48-definition manifest, certify document/subject/progress/retry/safety/action/configuration
  coverage, keep every type except `control/system_noop` disabled, and remove remaining
  duplicate policy authority.
- Inventory and dispatch reconciliation are unchanged: 48 definitions required; only
  `control/system_noop` may be enabled.
- Pending operator work remains unchanged from JMC2A/JMC1.

## JMC2B Phase B4 result and completion handoff

- Added the authoritative immutable built-in manifest and startup certification. It has
  exactly **48** definitions: the reconciled **30** decorated handlers, **12** media
  operations, and **6** parent-only types frozen in B0. Route, schedule, healing, reserved
  webhook, and parent inventories are all subsets with no missing, duplicate, unexpected,
  unclassified, or generic-fallback type.
- Every definition now owns strict current-version request/result/error adapters, explicit
  trigger provenance, execution class/entrypoint, bounded timeout and configuration-key
  selection, accepted subject kinds and builder, honest progress/stages, retry/effect safety,
  state-aware actions, and a unique non-generic presenter key. Parent definitions additionally
  own fixed/sealed child eligibility and aggregation policy.
- Dispatch state is certified as **1 enabled** (`system_noop`), **41 defined-disabled**, and
  **6 parent-only**. `radarr_upgrade` is defined only with the reserved webhook trigger and
  remains disabled. The command, gateway, API readiness, and worker import path now consult
  the manifest; no non-noop handler migration or execution was introduced.
- Safety reconciliation is **28 unsafe mutations** and **20 read-only definitions**. Every
  unsafe mutation has exactly one transport attempt. There are **no retry/safety exceptions**
  and therefore no unproven staged/fenced retry claim. Native adapter declarations are
  limited to 2 verified FFmpeg-capable definitions and 9 MKVToolNix-capable definitions;
  parsing and persistence remain deferred to Chunk 3.
- Focused B4 manifest/integration suite: **38 passed**. Full suite: **841 passed,
  23 retained failures, 2 warnings** in 49.70s. The exact retained failure set is unchanged
  from the B0 baseline; B4 added no failure, error, skip, or xfail. Ruff passed. Alembic head
  remains `0002_jmc2a`, and `alembic check` reports no new upgrade operations. No frontend
  change applies.
- Phase B4 commit: this commit (`certify job definition registry`). JMC2B's focused commits
  are `32eb5a8`, `ac60f8d`, `7f900a4`, `86daa96`, and this final commit.
- JMC2B is complete. JMC2C must start by implementing the presenter registry for every
  definition's unique `presenter_key`, then replace public job list/detail/action contracts
  from the immutable definition, subject, progress, and action-policy data without enabling
  any additional definition.
- Pending operator work remains unchanged from JMC2A/JMC1. No new JMC2B operator action is
  required.

## JMC2C Phase C0 — in progress (2026-07-13)

- Branch/HEAD: `job-manager` at `c0bca7c` (`certify job definition registry`). Tracked
  working tree clean; the pre-existing untracked `.agents/` and JMC3 plan files remain
  preserved and excluded. Configured Git author remains Gautam Chaudhri
  `<gautam.chaudhri@gmail.com>`.
- JMC2A/B verification: all recorded commits present in `git log`
  (`10cb470`, `3659626`, `6cc38dc`, `f2662b6`, `e49bd07`, `2d6f678`, `32eb5a8`, `ac60f8d`,
  `7f900a4`, `86daa96`, `c0bca7c`). The manifest (`marquee/core/jobs/manifest.py`) holds
  exactly 48 immutable definitions; every definition has a unique non-generic
  `presenter_key` (`jobs.{job_type}`); only `control/system_noop` is enabled
  (41 defined-disabled, 6 parent-only); `radarr_upgrade` remains webhook-reserved and
  disabled. No generic fallback exists for any built-in.
- Baseline (owned disposable PostgreSQL 18.3 cluster `/tmp/marquee-jmc2c-pg.Jzijld`, port
  55440, trust auth, database `marquee_test`): `DEBUG=true pytest -q` reproduced
  **841 passed, 23 failed, 2 warnings** in 45.13s — exactly the JMC2B retained failure set,
  test-for-test. `ruff check marquee tests` passed (ruff 0.15.17). Frontend:
  `npm run check` 0 errors / 16 warnings / 8 files; `npm run lint` clean;
  `npm run build` exit 0.
- Route inventory to replace/remove in C3/C4:
  - `/api/jobs` (jobs.py): `GET ""` (offset-window list, `next_before`), `GET /metrics`,
    `GET /metrics/by-type`, `GET /{id}` (unbounded detail: all attempts + all events + all
    children), `GET /{id}/children` (unbounded), `GET /{id}/events` (per-job polling SSE,
    0.5 s DB loop), `POST /{id}/cancel` (noop-only), `POST /{id}/pause|resume` +
    `PATCH /{id}/priority` + `POST /{id}/retry` (all fail-closed 503).
  - `/api/media-jobs` (media_jobs.py): 7 fail-closed routes (confirm/get/events/cancel/
    list/restore/delete-backup).
  - `GET /api/pipeline/runs/{run_id}/events`: 307 redirect onto the per-job SSE.
  - `GET /api/activity`: separate feed endpoint (unchanged scope for C3 unless its job
    branch breaks; verify then).
  - `events_url` producers pointing at per-job SSE: `jobs.job_summary`, letterbox.py (3),
    hdr.py (3), audio_subs.py, pipeline.py; subtitle_generators.py (2) point at the
    fail-closed media-jobs SSE.
- Frontend call-site inventory: 18 API modules under `frontend/src/lib/api/` (~180 exported
  functions; jobs.ts 13, media-jobs.ts 6 plus letterbox 38, taste 20, pipeline 17,
  subtitles 16, etc.). Job/SSE consumers: `lib/jobs.ts` (`trackJob` poll+SSE engine),
  `lib/sse.ts`, and 38 files importing jobs/media-jobs/trackJob/sse (Projection Room pages,
  pipeline/letterbox/hdr/audio-subs/taste/television/films pages and components).
- OpenAPI state: `scripts/export_openapi.py` writes `design/api-schema.json` from
  `marquee.main.app` with `DEBUG=true`; the artifact is **not** currently committed and no
  drift check exists. CI (`.github/workflows/ci.yml`) references it only historically.
- `openapi-typescript` verification (npm registry metadata, 2026-07-13): dist-tag
  `latest` = **7.13.0** (next = 7.0.0-rc.1 is older-line RC; swagger-v2 = 5.4.2). C5 will
  pin exactly `7.13.0` in `frontend/package.json` + lockfile.
- Current phase: C0 — freeze presentation/section schemas, the pagination envelope/opaque
  cursor contract, and the command error model with contract tests, then commit and record
  the result.

## JMC2C Phase C0 result

- Froze the presentation contract in `marquee/core/jobs/presentation.py`: the nine typed
  value kinds (text, number/unit, duration, bytes, timestamp, boolean, badge, subject
  reference, safe relative link), the exact eleven-section discriminated vocabulary from the
  plan, and the versioned `JobPresentation` envelope (subject/action/trigger/attention/
  status/compact progress/impact/sections/warnings/failures/suggested actions/evidence/
  bounded diagnostic links). All models are `extra=forbid`, bounded, and reject external or
  scheme-bearing URLs.
- Froze opaque cursor pagination in `marquee/core/jobs/pagination.py`: base64url JSON
  envelope binding each cursor to a deterministic sha256 fingerprint of the exact
  view/filter/sort contract; malformed, oversized, non-scalar, and cross-query cursors fail
  closed with `InvalidCursorError`.
- Froze the typed command/read error detail (`marquee/core/jobs/api_errors.py`) following
  the established `{"detail": {code, ...}}` envelope, with bounded secret-free context and
  the frozen code set (`job_not_found`, `stale_job_version`, `action_not_allowed`,
  `invalid_cursor`, `invalid_filter`, `unmigrated_job_command`).
- Focused C0 contract suite: **11 passed** (`tests/test_job_presentation_contract.py`).
  Full suite: **852 passed, 23 retained failures, 2 warnings** in 48.71s — failure set
  compared test-for-test with the C0 baseline: identical. Ruff passed. No schema or
  frontend change applies.
- Phase C0 commit: this commit (`freeze job presentation contracts`).
- Current phase: C0 complete; C1 next. Exact next steps: implement presenter resolution
  keyed by the manifest's `presenter_key`, snapshot-first presentation helpers, and the
  complete AI poster, HDR/Dolby Vision, audio/subtitle, and letterbox presenter families
  with compact/detail goldens and malformed-optional-evidence warning tests.
- Pending operator work remains unchanged from JMC2A/JMC1.

## JMC2C Phase C1 result

- Implemented the presentation engine (`marquee/core/jobs/presenters/base.py`):
  snapshot-first `load_context` validates the subject snapshot (invalid required snapshot →
  `PresentationIntegrityError`), the request/result/error documents through each
  definition's adapters, and the stored `JobProgress`; malformed optional evidence becomes
  a `malformed_evidence` warning that omits only the affected content. Shared assembly owns
  friendly status/outcome labels and tones, trigger labels with sanitized initiator,
  attention derivation, state-aware allowed actions (retry only for dispatch-enabled
  definitions; logs/artifacts stay unavailable until Chunk 3), compact typed progress,
  impact, relative-only diagnostic links, target-attributed failures, and suggested
  actions. Raw stage keys, `waiting_external`-style codes, and transport labels never
  become primary text.
- Added the compact `JobRow` + `RowLinks` contract to `marquee/core/jobs/presentation.py`
  as the only list-row shape for C3, and completed `JOB_LABELS` coverage for all 48 types.
- Implemented dedicated presenters for the four primary families
  (`presenters/{posters,hdr,audio_subs,letterbox}.py`, 32 job types): poster
  candidate/gate/score/deploy/review evidence, HDR/DoVi profile/RPU/encoder/validation and
  stage-attributed failures, audio/subtitle selector/before-after/track-table/change-list
  with all-or-nothing `not_applied` semantics, and letterbox scope/crop/confidence/
  encoder/size evidence with distinct no-bars no-change. Registry resolution is strict:
  a built-in key without a presenter raises `UnregisteredPresenterError`; the labelled
  generic presenter is not registered for any built-in.
- Golden fixtures under `tests/fixtures/jmc2c/` cover poster detail + compact row, DoVi
  analyze detail, subtitle generation detail, and letterbox re-encode detail; behavior
  tests cover no-candidate/no-bars no-change notices, remux failure with every target
  `not_applied`, running nested progress, malformed-evidence warnings, missing live
  subject notices, determinism, and machine-label absence.
- Focused C1 suite: **19 passed** (30 with the C0 contract suite). Full suite:
  **871 passed, 23 retained failures, 2 warnings** in 49.90s — failure set identical to
  the C0 baseline. Ruff passed. No schema or frontend change applies.
- Phase C1 commit: this commit (`present primary job families`).
- Current phase: C1 complete; C2 next. Exact next steps: implement supporting presenters
  (library sync/radarr upgrade, taste/ML, maintenance/backup/retention/cache, system noop)
  and the six parent-batch presenters with children aggregation, prove complete 48/48
  presenter coverage without fallback, and add retry-lineage/batch-grouping/no-change/
  partial-success fixtures.
- Pending operator work remains unchanged from JMC2A/JMC1.

## JMC2C Phase C2 result

- Implemented the supporting presenter family (`presenters/supporting.py`, 10 types:
  library sync, Radarr upgrade, taste rebuild/map, learned-head training, backup,
  pipeline-cache clear, job-retention purge, metrics purge, system noop) with
  service/count facts for integrations, model/profile/device/exemplar facts for ML work,
  and scope/dry-run/retention facts plus records/files/bytes metric cards for maintenance.
- Implemented the parent-batch presenter family (`presenters/parents.py`, 6 parent-only
  types). Parents render a bounded `children` section from live child counts when the
  caller provides them, fall back to stored terminal summary evidence, warn on malformed
  child summaries, and always link to the server-paginated child list; no child graph is
  ever embedded. Retry lineage now renders as a Lineage facts section with a link to the
  original job, alongside the row-level `retry_of_job_id`.
- Presenter coverage is complete and executable: all **48** definitions resolve to a
  dedicated presenter (`len(JOB_PRESENTER_REGISTRY) == 48`), every definition renders a
  minimal presentation and row for its first subject kind, and the labelled generic
  presenter is registered for no built-in — the fallback path is unreachable for
  registered built-ins.
- Added the `library_sync_detail` golden plus partial-success, no-change, malformed-child,
  retry-lineage, and system-noop fixtures.
- Focused C2 suite: **10 passed** (29 with the C1 suite). Full suite: **881 passed,
  23 retained failures, 2 warnings** in 48.64s — failure set identical to the C0 baseline.
  Ruff passed. No schema or frontend change applies.
- Phase C2 commit: this commit (`present supporting job families`).
- Current phase: C2 complete; C3 next. Exact next steps: implement the bounded canonical
  read APIs (`GET /api/jobs?view=queue|history` with opaque contract-bound cursors and
  allowlisted filters/sorts, `/snapshot`, `/presentation`, paginated
  `/attempts|/events|/artifacts|/children`, bounded `/raw/{doc}`), remove the superseded
  unbounded job/media-job reads and the per-job polling SSE contract, and migrate every
  internal call site/test in the same phase.
- Pending operator work remains unchanged from JMC2A/JMC1.

## JMC2C Phase C3 result

- Replaced the product job read surface with bounded canonical contracts in
  `marquee/api/routes/jobs.py`: lifecycle-partitioned queue/history lists, opaque
  filter/view/sort-bound keyset cursors, allowlisted filters and sorts, compact snapshots,
  curated presentations, separately paginated attempts/events/artifacts/children, and
  bounded validated raw request/plan/result/error documents with safe download headers.
  Unknown stored job definitions fail with a typed integrity conflict; no presenter or
  serializer fallback was added.
- Removed the superseded `/api/media-jobs` router and the pipeline/letterbox redirects onto
  per-job polling SSE. Backend producers now return canonical snapshot URLs. Active frontend
  consumers reconcile through `/api/jobs/{id}/snapshot`; no public per-job event stream,
  PgQueuer numeric ID/row, unbounded history response, or legacy media-job read remains.
- Established executable query budgets at the maximum 200-row page size: queue/history list
  is one SQL statement, presentation is one, and bounded children is two (parent existence
  plus the keyset page). Cursor tests prove cross-view reuse fails closed. Raw-document tests
  prove internal transport fields are absent.
- Migrated the cooperative-cancellation fixture away from the retired manager and updated
  the obsolete letterbox SSE redirect assertion. Focused C3 suite: **8 passed**. Full suite
  in the provided PostgreSQL environment: **876 passed, 21 retained failures, 2 warnings,
  11 environment errors** in 61.02s. The retained application failure set shrank from 23 to
  21 with no new failure; the 11 JMC1 migration cases could not create their isolated
  databases because the supplied PostgreSQL role lacks `CREATEDB`. Ruff passed.
- Frontend certification: `npm run check` passed with the same 0 errors/16 warnings,
  `npm run lint` passed, and `npm run build` passed.
- Phase C3 commit: this commit (`bound canonical job reads`).
- Current phase: C3 complete; C4 next. Exact next steps: implement optimistic,
  capability-checked cancel/pause/resume/priority/retry commands plus bounded deduplicated
  bulk actions, then prove stale conflicts, partial bulk failure, class-scoped priority and
  retry successor lineage.
- Pending operator work remains unchanged from JMC2A/JMC1.

## JMC2C Phase C4 result

- Added the canonical command service in `marquee/core/jobs/control.py`. Every mutation
  row-locks the canonical job, requires the expected `fence_token`, evaluates the
  definition's action policy against current lifecycle state, increments the fence on
  success, and returns the new canonical snapshot. Stale and disallowed commands use the
  frozen typed conflict envelope; pause/resume correctly remain unavailable because no
  current definition advertises pause capability.
- Implemented single cancel, pause, resume, class-scoped priority, and retry routes plus the
  bounded `POST /api/jobs/actions` contract (maximum 100 items). Bulk execution deduplicates
  identical commands while preserving every caller `request_id`; each distinct item commits
  independently and reports its own success or typed failure.
- Priority changes never drift canonical and transport state: a queued PgQueuer ticket is
  cancelled, its immutable dispatch audit is marked `superseded`, and exactly one new
  dispatch generation is enqueued at the requested priority within the definition's
  execution class. Planned jobs without a ticket update canonically without inventing
  transport authority.
- Retry never reopens terminal history. The sole dispatch-enabled `system_noop` definition
  creates a new canonical successor and transport ticket with `retry_of_job_id`, stable
  `root_id`, correlation, initiator and subject snapshot lineage; the original remains
  terminal and its fence advances.
- Focused C4 suite: **22 passed** across command APIs, bounded reads and PgQueuer gateway
  integration. Full suite: **881 passed, 21 retained failures, 2 warnings, 11 environment
  errors** in 61.89s. The failure set is unchanged from C3; the 11 JMC1 migration cases are
  still blocked only by the supplied PostgreSQL role lacking `CREATEDB`. Ruff passed.
- Phase C4 commit: this commit (`add canonical job commands`).
- Current phase: C4 complete; C5 next. Exact next steps: pin `openapi-typescript`, export and
  drift-check one deterministic OpenAPI artifact, generate committed static `paths` types,
  and migrate the existing fetch runtime and every frontend API function to real generated
  route contracts without adding a generated runtime SDK.
- Pending operator work remains unchanged from JMC2A/JMC1.

## JMC2C Phase C5 result

- Exported and committed one deterministic OpenAPI 3.1 artifact at
  `design/api-schema.json` (193 paths). `scripts/export_openapi.py --check` now fails on
  byte drift, and export uses sorted, stable JSON independent of the invoking environment.
- Pinned `openapi-typescript` **7.13.0** and its compatible TypeScript **5.9.3** exactly in
  the frontend manifest and lockfile. Generation writes the committed static
  `frontend/src/lib/api/generated/openapi.ts` and formats it deterministically; no generated
  runtime client or `openapi-fetch` dependency was added.
- Typed the existing fetch runtime from generated `paths`. Route/method-aware path types now
  cover every `apiGet`/`apiSend` call and caused all removed `/jobs/{id}`, `/jobs/metrics*`,
  and `/media-jobs*` calls to fail compilation until migrated. Existing product consumers
  now use canonical lists, snapshots, bounded detail resources, and optimistic command
  bodies containing the current `fence_token`. Legacy media-job presentation adapters are
  fed only from canonical job contracts; unsupported backup actions were removed rather
  than retained against nonexistent routes.
- Reproducibility was verified across consecutive generation runs. SHA-256:
  `af9afb7e640c73ac68daea7f95d8531bc82e9bf0324a867a99a229c9b77713e4` for the OpenAPI
  artifact and `c4e4ec2d9860e0219a902383cf001d3605da0b747a249e094d2b0bcb806746f0` for the
  formatted TypeScript output.
- Frontend verification: `npm run check` passed with **0 errors / 16 retained warnings**,
  `npm run lint` passed, and `npm run build` passed. OpenAPI export and generation drift
  checks passed; Ruff passed. Full suite: **881 passed, 21 retained failures, 2 warnings,
  11 environment errors** in 60.71s, identical to C4. The 11 JMC1 migration cases remain
  blocked only because the supplied PostgreSQL role lacks `CREATEDB`.
- Phase C5 commit: this commit (`generate typed api contracts`).
- Current phase: C5 complete; C6 next. Exact next steps: run the consolidated JMC2
  presentation/API/query/security/generation certification, re-prove byte reproducibility,
  inspect subject-family fixtures, record route removals and deferred Chunk 3/6 boundaries,
  and publish the final certification result here.
- Pending operator work remains unchanged from JMC2A/JMC1; npm reports four low-severity
  dependency advisories, and no broad dependency mutation was made during this phase.

## JMC2C Phase C6 certification

- **JMC2C certification result: PASS.** The consolidated presentation, registry, document,
  subject/progress, bounded read/query, optimistic command, PgQueuer gateway, hardening and
  auth suite passed **114/114**. All 48 built-ins still resolve dedicated presenters and
  only `system_noop` remains dispatch-enabled.
- Canonical surface audit passed: neither backend nor frontend contains product calls to the
  retired `/api/media-jobs*`, legacy `/api/jobs/{id}`, `/api/jobs/metrics*`, or per-job
  streaming contracts. The product surface is the bounded Queue/History, snapshot,
  presentation, attempts, events, artifacts, children, raw-document and command API; no
  PgQueuer row or numeric transport identifier is exposed.
- Query/security certification passed: maximum-page list, presentation and children budgets
  remain executable; opaque cursors are query-bound; raw documents are bounded and strip
  transport internals; diagnostic links remain relative; optimistic commands require the
  current fence and capability; bulk actions remain bounded and independently committed.
- Generation certification passed: OpenAPI export drift, static TypeScript generation drift,
  and consecutive byte reproducibility checks all passed with the C5 SHA-256 values. The
  generated `paths`-typed fetch runtime compiles every existing API helper against a real
  route/method. Frontend check passed with **0 errors / 16 retained warnings**, lint and
  production build passed, and Ruff passed.
- Fixture/manual inspection covered movie, series, season, episode, media-file, track,
  poster-candidate-set, model-profile-training, aggregate-batch, maintenance and system-work
  subjects. Compact/detail goldens and malformed/missing optional evidence behavior remain
  deterministic and safe.
- Final full suite: **881 passed, 21 retained failures, 2 warnings, 11 environment errors**
  in 62.53s. The exact application failure set is unchanged from C3-C5 and contains no JMC2
  regression. All 11 errors are the known JMC1 isolated-database cases blocked because the
  supplied PostgreSQL role lacks `CREATEDB`.
- Deferred boundaries are unchanged: Chunk 3 owns multiplexed live events plus log/artifact
  storage; later handler migration owns currently unavailable planned media-job execution;
  Chunk 6 owns the Projection Room visual rebuild, shared progress/card state, loading-bar
  redesign and live-log UI. No compatibility endpoint or generated runtime SDK was added.
- Manual/operator follow-up: rerun the 11 JMC1 migration tests with a PostgreSQL test role
  allowed to create isolated databases; evaluate the four low-severity npm advisories in a
  separate dependency update. No deployment, model download or browser smoke test was
  required for this contract-only phase.
- Phase C6 commit: this commit (`certify jmc2c contracts`). JMC2C C0-C6 is complete.
