# JMC1 PgQueuer Foundation Timeline

## Initial state — 2026-07-12

- Branch/HEAD: `job-manager` at `c61878f` (`chunk 1 planned`).
- Working tree: clean except pre-existing untracked `.agents/`; preserve it.
- Configured Git author: `Gautam Chaudhri <gautam.chaudhri@gmail.com>`; unchanged.
- Required tooling: Serena, ByteRover MCP, and RTK available. The optional local `brv` CLI is
  absent, but required ByteRover query/curation remains available through MCP.
- Timeline was absent and was created before the first implementation commit.
- PgQueuer was not installed in `.venv`; exact 1.1.1 installation/source verification is the
  first implementation stop gate.
- Test database: owned PostgreSQL 18.3 cluster under
  `/tmp/marquee-jmc1-pg.KpOCwe`, loopback port 55432, private socket directory. No operator
  database or `DATA_DIR` was modified.

## Clean baseline

- `pytest -q`: **807 passed, 30 failed, 2 warnings** in 35.93s.
- Retained failure set:
  - `tests/test_batch_runner.py::test_rank_failure_is_isolated_to_one_movie`
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
- `ruff check marquee tests`: passed.
- Alembic: one current head, `b1c2d3e4f5a6`; history contains the unreleased multi-revision
  chain beginning at `730ffe1dcfc3`; offline `upgrade head --sql` completed successfully.
- Frontend reference `npm run check`: 0 errors, 16 warnings in 8 files (non-blocking for JMC1).
- Current health OpenAPI paths: `/health` only.
- Current process commands: raw `alembic upgrade head`, custom
  `marquee.core.jobs.worker`, and custom `marquee.core.jobs.scheduler`.

## Current phase

- Phase 0 — contract verification and baseline: **complete** (`0ac484f`).
- Phase 1 — clean baseline, migration service, and reset CLI: **complete** (`4021ae3`).
- Phase 2 — canonical transactional enqueue gateway: **complete** (`dc1f067`).
- Phase 3 — no-op worker and scheduler roles: **complete** (`2f856f1`).
- Phase 4 — readiness, diagnostics, and connection budgets: **complete** (`a5ed503`).
- Phase 5 — integration and failure certification: **complete** (`2d55a68`).

## Exact next steps

1. Operator-only deployment smokes listed below remain intentionally outside automated JMC1.
2. Begin later chunks only from the committed JMC1 contracts; do not restore the custom runtime.

## Deviations

- ByteRover MCP became available on resume and was queried. The local `brv` CLI remains absent,
  so the optional swarm query was not available.
- The repository Docker daemon is unavailable. All baseline and destructive certification
  uses the owned `/tmp` PostgreSQL cluster instead.
- The original owned PostgreSQL directory was no longer accessible and loopback port 55432 was
  already occupied. Phase 0 verification used a new owned PostgreSQL 18.3 cluster at
  `/tmp/marquee-jmc1-pg.XyclYX` on port 55433. No operator database or `DATA_DIR` was modified.
- The first transaction test called SQLAlchemy `begin()` without issuing a SQLAlchemy statement;
  because SQLAlchemy begins lazily, the raw asyncpg call autocommitted. The test now activates the
  caller transaction before acquiring the documented raw connection, matching the gateway's
  required flush-before-enqueue sequence.
- PgQueuer 1.1.1's fresh `pgq install` omits `pgqueuer_heartbeat_id_id1_idx`, while its public
  `pgq upgrade` adds it. The migration service runs the official upgrade immediately after an
  official fresh install so fresh and repeated migrations converge; no PgQueuer SQL is copied.
- `letterbox_reencode_artifacts` and `media_backups` still reference excluded `media_jobs`.
  They are excluded from the intermediate JMC1 deployment metadata with the duplicate media
  lifecycle tables and remain available only in test metadata until later chunks rebuild their
  canonical evidence/detail relationships.
- PgQueuer presence checks were initially database-wide for triggers and could misclassify a
  trigger in another disposable schema as a partial target-schema install. Presence detection is
  now fully scoped to `current_schema()` and the target `pgqueuer` table.
- Final post-restart gates were briefly paused when the execution approval service reached its
  session limit. The user explicitly approved continuation; no alternate database or command
  path was used.

## Phase 5 completion — 2026-07-12

- Commit: `2d55a68` (`certify pgqueuer failure recovery`).
- Canonical cancellation now uses the gateway's public `Queries` bridge and the caller's sole
  transaction. Queued tickets terminalize canonically with cancelled outcome; picked tickets
  enter stopping and cooperative PgQueuer cancellation terminalizes the test blocker.
- Delivery attempts record the PgQueuer queue-manager identity. Same-manager in-flight
  duplicates no-op, while a new manager recovering a stale heartbeat marks the abandoned attempt
  interrupted and safely re-executes the idempotent no-op.
- Real-manager tests certify persisted retry delay/two attempts, held failure, stale pickup,
  polling fallback on an intentionally unmatched notification channel, queued/picked
  cancellation, terminal-before-ack, duplicate/stale redelivery, and one callback effect across
  two scheduler managers.
- Static certification proves Uvicorn has no inline handler or legacy resource bootstrap, shipped
  roles run only their owned manager, process targets exclude the custom worker/scheduler, and
  the new runtime calls no legacy claim/recovery/resource/schedule tables.
- Final focused Phase 0–5 gate after PostgreSQL restart: `84 passed`.
- Final retained suite: `879 passed, 29 failed, 2 warnings` in 49.60s. The failure set is
  exactly the retained Phase 0 set; no failure was added.
- Repeated CLI development reset succeeded twice against the owned database. Final
  migration/catalog verification, `alembic check`, offline `upgrade head --sql`,
  `ruff check marquee tests`, Compose configuration, and `git diff --check` passed.
- Curated the implemented JMC1 gateway, delivery, readiness, migration, verification results,
  phase hashes, and operator-smoke boundary into ByteRover.
- Performed real smokes on the owned PostgreSQL 18.3 cluster:
  - shipped worker received OS SIGTERM and exited cleanly with status 0;
  - PostgreSQL fast shutdown while the shipped worker was connected caused worker exit status 1;
  - during the outage liveness stayed 200 and readiness returned 503;
  - PostgreSQL restarted on the isolated port and readiness returned to `ready`;
  - no operator database, normal `DATA_DIR`, Docker service, or public reset endpoint changed.
- Pending operator-only deployment smokes are listed at the end of this timeline.

## Phase 4 completion — 2026-07-12

- Commit: `a5ed503` (`add job runtime readiness`).
- Added explicit API (10+5), worker (2+1 plus one direct PgQueuer connection), scheduler (1+1
  plus one direct PgQueuer connection), and migration (one) budgets. One configured worker totals
  23 connections under the enforced default deployment maximum of 32.
- SQLAlchemy pools now select role-specific limits and retain role-tagged
  `application_name`; direct PgQueuer and migration connections remain separately tagged.
- Added database-free `/health/live`, bounded dependency `/health/ready`, and the temporary
  `/health` readiness alias. Responses expose only component states and return 503 when not
  ready. All health routes remain authentication-exempt.
- Readiness checks the mandatory configuration budget, exact pinned package, PostgreSQL
  reachability, non-held migration lock, live catalog, and atomic contract markers. API startup
  performs bounded retries; API, worker, and scheduler fail before serving or starting a manager
  on incompatibility.
- Added authenticated `/api/system/job-transport` bounded diagnostics: PgQueuer queue aggregates,
  oldest canonical eligible age, picked/held counts, embedded listener process health, contract
  fingerprints/durability, and role connection counts. It returns no payload, numeric ticket, or
  arbitrary transport row.
- Destructive disposable-database tests reject wrong Alembic head, stale contract fingerprint,
  unlogged PgQueuer tables, and missing PgQueuer index, trigger, or function. Unit/API tests cover
  healthy, unreachable/recovered, lock-held, wrong-package, invalid-budget, sanitized failure,
  database-free liveness, 503 readiness, and all three startup fail-closed paths.
- Focused Phase 0–4 gate: `76 passed`.
- Full retained suite: `871 passed, 29 failed, 2 warnings` in 47.95s. The failure set is exactly
  the retained Phase 0 set; no failure was added.
- `ruff check marquee tests`, migration/catalog verification, `alembic check`, Compose
  configuration, and real migrated-database health-route smoke (`200 200 200`) passed.
- Pending operator action: no real deployment process or PostgreSQL service was restarted in
  Phase 4. These remain in the Phase 5 certification record.

## Phase 3 completion — 2026-07-12

- Commit: `2f856f1` (`run pgqueuer noop roles`).
- Added the strict `control` payload decoder and delivery wrapper. It admits only the current
  canonical ticket/generation for `system_noop` version 1, serializes duplicate effects under
  the canonical row lock, records minimal attempts/events, and commits success, retry,
  cancellation, or failure state before returning or rethrowing to PgQueuer.
- Added separate worker-only and scheduler-only process modules. The worker registers exactly one
  `control` entrypoint with `on_failure="hold"`; the scheduler registers no shipped schedules.
  Neither calls `PgQueuer.run()`, and no handler runs in Uvicorn.
- Rewired Compose and the embedded supervisor to the new role modules with explicit
  `MARQUEE_PROCESS_ROLE` environments. Removed the legacy resource bootstrap from API startup;
  the custom claim/scheduler runtime remains present only for transitional imports and is
  unreachable from process commands.
- Added fatal runtime comparison of live Alembic/PgQueuer catalogs with the atomic
  `schema_contracts` markers before worker or scheduler manager startup.
- PgQueuer integration proves terminal-before-ack, sequential/concurrent duplicate suppression,
  stale/cancelled no-op behavior, strict malformed/unknown rejection, retry/failure persistence,
  actual queue-manager acknowledgement, held rejection, stale-heartbeat recovery, and one
  reconciled schedule under two scheduler managers.
- Focused Phase 0–3 gate: `41 passed`; supervisor gate: `4 passed`.
- Full retained suite: `850 passed, 29 failed, 2 warnings` in 41.32s. The failure set is exactly
  the retained Phase 0 set; no failure was added.
- `ruff check marquee tests`: passed. Migration/catalog verification, `alembic check`, and
  Compose configuration all passed against the owned PostgreSQL 18.3 cluster.
- Deviation: PgQueuer 1.1.1 exposes handler `Context` from `pgqueuer.models`, not the package
  root. The implementation uses that installed public model while retaining the plan's locked
  manager and `Queries` surfaces.
- Pending operator action: a real OS process-kill/restart smoke and a PostgreSQL service restart
  were not performed. In-process stale-heartbeat recovery was performed against real PgQueuer
  tables and managers; deployment process/service restarts remain for Phase 5/operator smoke.

## Phase 2 completion — 2026-07-12

- Commit: `dc1f067` (`add transactional job enqueue`).
- Added the narrow `PgQueuerGateway` using only public `Queries` on SQLAlchemy's documented raw
  asyncpg connection. It validates the physical caller transaction, forbids concurrent use of one
  raw connection, serializes exactly three transport fields, links one numeric ticket, and never
  commits, rolls back, closes, or retains the connection.
- Added bounded known-ticket cancellation/status and aggregate queue-statistics operations; no
  arbitrary transport rows or payloads are returned.
- Added the atomic `system_noop` command service with strict finite JSON, secret/path rejection,
  canonical idempotency keys, generation-one dispatch/event creation, one outer commit, and fresh
  transaction reload for canonical idempotency races.
- Transport dedupe conflicts are invariant diagnostics and never create a replacement ticket.
- `job_dispatches.pgq_job_id` is nullable only during the caller transaction before the public
  enqueue returns; the gateway requires and fills it before the outer commit.
- Focused Phase 0–2 gate: `26 passed`.
- Tests prove exact payload bytes, atomic visibility, caller rollback before/after enqueue,
  canonical concurrency, transport dedupe, priority/deferral, driver/closed/absent-transaction
  rejection, nested connection-use rejection, multiple-ID rejection, connection reuse, and
  bounded companion operations.
- Owned reset and `alembic check` confirm root baseline equivalence after the dispatch-nullability
  correction; Alembic still has one root head, `0001_jmc1`.
- Full retained suite: `832 passed, 29 failed, 2 warnings` in 41.55s. The failure set is exactly
  the retained Phase 0 set; no failure was added.

## Phase 1 completion — 2026-07-12

- Commit: `4021ae3` (`establish clean job baseline`).
- Replaced the unreleased migration graph with root revision `0001_jmc1`, generated from filtered
  deployment metadata. `alembic check` reports no upgrade operations and offline SQL is clean.
- The deployed baseline has 34 Marquee-owned tables. It excludes `job_resources`,
  `job_resource_reservations`, `job_schedules`, `job_workers`, `media_batches`, `media_jobs`,
  `media_job_events`, `letterbox_reencode_artifacts`, and `media_backups`. Alembic owns no
  PgQueuer table, enum, function, trigger, or index.
- Added canonical job phase/outcome/desired-state, PgQueuer ticket/generation linkage,
  `job_dispatches`, and `schema_contracts`.
- Transitional `jobs` columns retained only for unmigrated source imports/tests: `status`,
  parent/root/correlation/subject fields, current stage/checkpoint/progress, resource request,
  legacy attempt counters/limits, cancellation/pause flags, and legacy scheduling/claim/lifecycle
  timestamps. Existing `job_attempts` process/heartbeat fields are also transitional and have no
  JMC1 claim, heartbeat, retry, or recovery authority.
- Added the advisory-lock migration service: Alembic, PgQueuer install/upgrade, durable mode,
  autovacuum, CLI verification, independent catalog verification, and atomic contract markers.
  Database credentials are passed to PgQueuer only through libpq environment variables.
- Added the CLI-only development reset with explicit environment, data-loss, exact database-name,
  PostgreSQL, active-role, and nonblocking advisory-lock guards. It resets the database schema only
  and does not call or modify the public reset endpoint or `DATA_DIR`.
- Added role-specific PostgreSQL `application_name` tagging and changed the Compose migration
  command to `python -m marquee.db_migration`; Compose configuration validates successfully.
- Focused Phase 0/1 gate: `12 passed`.
- Repeated owned-database reset produced identical catalog fingerprints and preserved the test
  `DATA_DIR` marker. Partial PgQueuer installs and active Marquee connections were refused.
- Full retained suite: `818 passed, 29 failed, 2 warnings` in 41.65s. The failure set is exactly
  the retained Phase 0 set; no failure was added.
- `ruff check marquee tests`: passed.

## Phase 0 completion — 2026-07-12

- Commit: `0ac484f` (`verify pgqueuer bridge`).
- Added the exact `pgqueuer==1.1.1` runtime pin.
- Inspected installed 1.1.1 public `Queries`, `AsyncpgDriver`, queue/scheduler managers,
  registration, cancellation, retry, CLI, durability, and schema surfaces. They match the locked
  public bridge and separate-role architecture.
- Added disposable-schema PostgreSQL contract coverage proving the exact package/API, one numeric
  ticket, caller-transaction visibility, rollback of queue and queue-log writes, no adapter commit
  or close, and continued SQLAlchemy connection usability.
- Focused contract verification: `3 passed`.
- Full retained suite: `811 passed, 29 failed, 2 warnings` in 36.91s. The failure set is exactly
  the initial 30-test set minus
  `tests/test_batch_runner.py::test_rank_failure_is_isolated_to_one_movie`; no failure was added.
- `ruff check marquee tests`: passed.
- Alembic remains one head at `b1c2d3e4f5a6`; offline `upgrade head --sql` completed successfully.
- Documentation already described the public `Queries` bridge at HEAD; no correction was needed.

## Pending operator actions

- On the real deployment, SIGKILL a worker while a ticket is picked and confirm heartbeat
  redelivery. Automated certification covers stale recovery but did not SIGKILL a picked
  production-style process.
- Restart the real deployment PostgreSQL after a ticket is picked. The owned-cluster idle/listener
  restart passed; after-pickup service restart remains operator-only.
- Disrupt the real notification channel without stopping PostgreSQL. Automated certification
  proved polling fallback with an intentionally unmatched channel.
- Rerun the migration service on the real deployment while held, queued, and deferred tickets
  exist. Disposable-database repeated migration/reset and ticket-state tests passed.
