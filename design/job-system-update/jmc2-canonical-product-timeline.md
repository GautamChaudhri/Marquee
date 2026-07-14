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
