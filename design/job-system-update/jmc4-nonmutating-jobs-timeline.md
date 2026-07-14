# JMC4 Non-mutating Jobs Timeline

Shared implementer log for JMC4A → JMC4B → JMC4C. Append after every phase commit.

## Initial state — 2026-07-13 (JMC4A Phase A0)

- Branch/HEAD: `job-manager` at
  `640ba6b2a0f94945ff9c9ca7e10791a9b6e285d2` (`chunk 4 planned`), tracking
  `origin/job-manager`. This commit is the exact JMC4A plan base and its sole parent is the
  compact JMC3C commit `9a61b7d8368443799abe91f8401d133f8c35cac7`.
- Working tree: clean before the timeline was created. No JMC4 completion/recovery tag,
  branch, or prior shared timeline existed. Configured Git author is
  `Gautam Chaudhri <gautam.chaudhri@gmail.com>` and remains unchanged.
- Tooling: Serena instructions were read and the Marquee project is active; ByteRover MCP
  was queried before work; RTK is active for every development and Git command after the
  initial tool-instruction bootstrap read. The local `brv` CLI and its optional swarm query
  remain absent, while required ByteRover query/curation is available through MCP.
- Owned test infrastructure: PostgreSQL 18.3 cluster
  `/tmp/marquee-jmc4a-pg.D7yswf/data`, loopback port `55444`, trust-auth role `marquee`,
  database `marquee_test`. The guarded development reset applied the target Marquee schema
  and PgQueuer durable schema. No operator database, normal `DATA_DIR`, media, or backup
  root was modified.

## Verified JMC3 completion and plan boundary

- Compact JMC3A: `6d4f2cf1d16354bac260db64853a11f2df6029b7`, tree
  `20a5589f5c8fd9a6f1f3b3fab19a4ccc02f9efa1`, sole parent `18a0da9`; configured author.
- Compact JMC3B: `762f66d7f9b8695a1ac8d812abc635ed4d1abe36`, tree
  `d4503a00224ac41af932029a41920d04cb007c53`, sole parent JMC3A; configured author.
- Compact JMC3C: `9a61b7d8368443799abe91f8401d133f8c35cac7`, tree
  `41bb7319176e7ba582d0a73564f10b737624b259`, sole parent JMC3B; configured author.
- Git ancestry is linear through all three compact commits. The complete JMC3 timeline was
  read and its final certification state was verified against Git, schema, generated
  contracts, registry state, and the retained suite.
- The JMC4 planning commit is
  `640ba6b2a0f94945ff9c9ca7e10791a9b6e285d2`, tree
  `3382bdb06472127be1f24932f5b7c3cba99838ce`, sole parent JMC3C; configured author.
- JMC4A is authoritative. PgQueuer remains the sole transport, claim, heartbeat,
  stale-redelivery, retry-timing, schedule-row, and queue-concurrency authority. Production
  dispatch remains exactly `control/system_noop`; all other definitions and all real
  schedule occurrences remain disabled. `media_write` remains unavailable for product work.

## A0 schema, runtime, and generated-contract baseline

- Runtime: Python 3.13.14, asyncpg 0.31.0, SQLAlchemy 2.0.50, PgQueuer 1.1.1, PostgreSQL
  client/server 18.3.
- Sole Alembic head/current: `0003_jmc3b`. `alembic check` reports no new upgrade
  operations. Offline `upgrade head --sql` renders 1,352 lines.
- Marquee schema marker:
  `marquee:0003_jmc3b:none:16adc26f7e28158bbc77dd1f8fa9ce283b429b07968f62db757d918aa403db61`.
- PgQueuer schema marker:
  `pgqueuer:1.1.1:durable:19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a`.
- Registry: 48 definitions; enabled set exactly `system_noop`; enabled entrypoint exactly
  `control`; no production canary, backup, legacy media, or feature handler is executable.
- OpenAPI artifact is deterministic and current at 196 paths. Static TypeScript regeneration
  is clean with `openapi-typescript` 7.13.0 and TypeScript 5.9.3.
- Existing connection arithmetic is API pool 15 + API event listener 1 + worker 8
  (including four safety-gate sessions) + scheduler 3 + migration 1 = 28 configured against
  the deployment maximum of 32. JMC4A must revalidate this when worker entrypoints,
  concurrency, or schedule callbacks change.

## A0 retained test and frontend baseline

- `DEBUG=true pytest -q` against the owned PostgreSQL cluster: **1014 passed, 21 failed,
  2 warnings** in 54.33s. This exactly reproduces the JMC3C retained set with no new error,
  skip, or `xfail`:
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
- Frontend: OpenAPI drift and static generated-TypeScript drift passed; `npm run check`
  passed with 0 errors and the inherited 16 warnings in 8 files; `npm run lint` passed;
  `npm run build` passed.

## Phase A0 inventory and installed PgQueuer contract

- Legacy product producers remain fail-closed through `JobManager`/`MediaJobManager`.
  Source inventory records route and handler call sites for `create`, `create_and_run`,
  `create_batch`, and `create_job`; none is currently an enabled canonical producer.
- Canonical implemented surfaces are `create_system_noop`, `PgQueuerGateway`, the registry,
  delivery kernel, parent-progress prototype, bounded job routes, and separate PgQueuer
  worker/scheduler roles. `tests/fixtures/jmc4a/a0_contract_freeze.json` freezes these surfaces,
  all four canonical model column sets, and every legacy manager call site.
- Installed `Queries.enqueue()` accepts ordered lists for entrypoint, payload, priority,
  execute-after, dedupe key, and headers and returns `list[JobId]` directly from the supplied
  driver fetch order. It uses the caller-owned asyncpg connection and translates unique
  violations to `DuplicateJobError`.
- Installed `PgQueuer.entrypoint()` owns a database-global `concurrency_limit` and
  `on_failure`; installed `PgQueuer.schedule()` owns entrypoint, cron expression,
  executor/callback context, cleanup, and context admission. PostgreSQL contract tests prove
  ordered bulk IDs/rollback, UTC schedule values and shared context, duplicate registration,
  cancellation requeue, and database-global entrypoint concurrency through `QueueManager`.

## Phase A0 completion — 2026-07-13

- Phase commit: `e5fed0a0afcbf2e11469e4d25a3a7d943642c4a9`
  (`freeze jmc4a producer contracts`), tree
  `d9f27b4ce871c8f27d6c1ae5864f7e7a3f970ef5`, sole parent the exact JMC4A plan base
  `640ba6b2a0f94945ff9c9ca7e10791a9b6e285d2`; configured repository author only.
- Completed: verified the compact JMC3 chain and all §1 baselines; inspected installed
  PgQueuer 1.1.1 enqueue, schedule, cancellation, duplicate-registration, and concurrency
  implementation; added the machine-checkable canonical/route/registry/worker/scheduler and
  legacy-producer inventory; and added live PostgreSQL contract coverage for every A0 transport
  question.
- Focused tests: **11 passed** in 1.46s. Retained full suite: **1,022 passed, 21 failed,
  2 warnings** in 56.45s. The failures and warnings are exactly the baseline set recorded above;
  no new failure, error, skip, or `xfail` was introduced.
- `ruff check marquee tests`, `git diff --check`, Alembic head/current/check, OpenAPI drift at
  196 paths, generated-TypeScript drift, frontend check (0 errors, inherited 16 warnings in
  8 files), frontend lint, and frontend build all passed. No schema or generated contract changed.

## Current phase and exact next steps

- Phase A0: **complete**.

## Phase A1 completion — 2026-07-13

- Phase commit: `c5cd3b135378dbb203c76b44bad6dd241cc39717`
  (`add canonical job submission`), tree
  `2c99d4d090313b128b03902763579c619dc230bc`, sole parent the A0 timeline commit
  `a4e2f19309ae1ccc7b7e1a1e96a496a7c57768d6`; configured repository author only.
- Added the sole typed caller-transaction `submit_job` authority and common-parent ordered
  `submit_jobs` primitive. Canonical advisory-lock idempotency returns an exact semantic reuse,
  types mismatches as conflicts, and prevents a concurrent loser from creating a transport
  ticket. Results expose only canonical ID, disposition, phase, snapshot link, and detail link.
- Definition-owned request, subject, configuration, priority, eligibility, retry, timeout,
  execution class, progress, presenter, and action policy are snapshotted before dispatch.
  Webhooks, disabled definitions, client policy injection, unbounded/invalid input, inconsistent
  bulk provenance, and unresolved subjects fail with bounded redacted errors.
- `PgQueuerGateway.enqueue_many` makes one public ordered list enqueue call, verifies bounded
  one-for-one unique numeric IDs, and links canonical/dispatch rows before caller commit. The
  exact three-field transport payload is unchanged. `create_system_noop` is now a compatible
  committing wrapper over the canonical service; it remains the only enabled product producer.
- Focused producer/delivery/definition certification: **81 passed** in 11.59s. Retained full
  suite: **1,030 passed, 21 failed, 2 warnings** in 58.37s. The failures and warnings are exactly
  the recorded baseline set; no new failure, error, skip, or `xfail` was introduced.
- `ruff check marquee tests`, `git diff --check`, Alembic head/current/check, OpenAPI drift at
  196 paths, and generated-TypeScript drift all passed. No schema, API, generated contract, or
  frontend source changed; the A0 frontend check/lint/build baseline therefore remains current.

## Current phase and exact next steps

- Phase A1: **complete**. Phase A2 — batch schema and fixed creation: **next**.
- Exact next steps:
  1. Add the strict 1:1 canonical batch projection model and Alembic revision with mode,
     generation, permanent seal state, immutable sealed total, terminal/outcome counters,
     projection sequence, timestamps, and bounded attention metadata.
  2. Implement fixed atomic parent/projection/child/event/dispatch creation through the A1 bulk
     primitive, including a sealed empty `no_change` parent with no transport ticket.
  3. Add bounded parent response and child-listing APIs while keeping the parent ticketless and
     preventing any transport attempt or inline execution.
  4. Prove fresh-reset/model/Alembic equivalence plus rollback at every fixed-creation boundary,
     bulk ticket ordering, parent-only invariants, empty scope, caps, and API bounds.

## Deviations and pending operator work

- ByteRover's local CLI/swarm helper is absent; ByteRover MCP query is available and used.
  No ByteRover outage is claimed.
- The execution sandbox denies loopback socket creation/access. PostgreSQL start and
  database-backed commands use the approved unsandboxed path against only the owned
  disposable cluster.
- Pending host-only work inherited from JMC3: delegated cgroup-v2 kill/empty proof; real
  API/worker/listener SIGKILL and restart while picked; orphan/workspace reconciliation;
  PostgreSQL restart and LISTEN/NOTIFY disruption; multiprocess safety-gate contention and
  connection loss; deployment-filesystem symlink/path-swap/cross-device behavior;
  long-running 100 MiB log capacity and retention; real-storage backup rotation/restore;
  and a real Node-adapter proxy stream/abort smoke.
- Also inherited: apply the target `0003_jmc3b` schema or sanctioned reset to the live
  development database, rerun isolated migration tests with a suitable role where needed,
  and evaluate four low-severity npm advisories separately.

## Phase A2 completion — 2026-07-13

- Phase commit: `5469bb86938d74a4579e451e91210e4b6d1ab666`
  (`add fixed canonical batches`), tree
  `101d3078f1eaf9c3990b9e53565c605624e0a28f`, sole parent the A1 timeline commit
  `4d8798da3829aaf117eadd1e9fe69197798cf248`; configured repository author only.
- Added the strict 1:1 `job_batches` coordination projection and sole Alembic head
  `0004_jmc4a`. The projection owns permanent seal state, generation, immutable sealed total,
  terminal/outcome counters, projection sequence, bounded summary documents, and timestamps;
  it owns no transport, claim, attempt, heartbeat, retry-timing, or schedule authority.
- Fixed batch creation is caller-transaction-owned and completely atomic. It validates the
  parent, scope, all ordered children, provenance, child policy, and caps before creating a
  ticketless `PARENT_ONLY` canonical parent, sealed projection, seal event, and ordinary A1
  canonical children. Exact retries validate the parent and every child semantic intent;
  conflicting reuse fails closed. Empty scope terminalizes `no_change` with no PgQueuer ticket,
  dispatch, attempt, or inline execution.
- Added `GET /api/jobs/{job_id}/batch` for bounded aggregate state while retaining the separate
  cursor-bounded child listing. The deterministic OpenAPI artifact now has 197 paths; schema
  SHA-256 is `14da3443c2ed4d7e9cdaf0d226f20a93ba4432632c067de4235fc15188694b65`
  and generated TypeScript SHA-256 is
  `72a352d704e5fdda92046686ec1e47c4e58b66fc5a50488672a55d27122c916a`.
- Fresh-database migration through the complete chain reached `0004_jmc4a` with zero batch rows,
  then the proof database was dropped. The owned retained database has sole head/current
  `0004_jmc4a`; `alembic check` reports no new upgrade operations. Marquee marker is
  `marquee:0004_jmc4a:none:f79bdd25a17e5c10048536516552262195cf82a2a2b696a4aaf8746fdaff0d02`;
  PgQueuer remains
  `pgqueuer:1.1.1:durable:19377622f52c906a7a5cb6e68b4db6d30e7cc9534aac933c156c33666f4eb21a`.
- Final focused contract/batch/submission/migration gate: **45 passed** in 13.89s. Retained full
  suite: **1,037 passed, 21 failed, 2 warnings** in 56.76s. The failures and warnings are exactly
  the baseline set recorded above; no new failure, error, skip, or `xfail` was introduced.
- `ruff check marquee tests`, `git diff --check`, Alembic head/current/check, deterministic
  OpenAPI drift, and generated-TypeScript drift all passed. Frontend check passed with 0 errors
  and the inherited 16 warnings in 8 files; frontend lint and production build passed.
- Production enablement is unchanged: exactly `system_noop` remains enabled, real feature
  handlers and schedule occurrences remain disabled, and `media_write` remains unavailable for
  product work. Connection arithmetic is unchanged at 28 configured connections against 32.

## Current phase and exact next steps

- Phases A0-A2: **complete**. Phase A3 — dynamic sealing, aggregation, and commands:
  **in progress**.
- Exact next steps:
  1. Implement fenced dynamic open/append/seal with row locks, generation, cap, child-policy,
     idempotency, and permanent same-generation sealing; sealed empty batches terminalize
     `no_change`, while open batches can never terminalize.
  2. Integrate first-terminal child accounting and ticketless parent phase/progress/outcome
     aggregation with the locked outcome matrix and bounded summaries.
  3. Add bounded compare-and-set projection repair that never mutates children or PgQueuer rows.
  4. Implement policy-gated parent cancellation, priority, and retry lineage over bounded child
     pages without correlation-only targeting.
  5. Prove concurrent append/seal/terminal races and all parent-command boundaries on PostgreSQL,
     then rerun every focused and retained gate.

## A2 deviations and pending operator work

- The first final frontend lint invocation overlapped deterministic TypeScript regeneration and
  observed the generated file mid-write. It was rerun after generation completed and passed;
  no source or contract deviation remained. Future generator and lint gates run sequentially.
- The ByteRover local CLI/swarm-helper and sandbox loopback limitations remain as recorded above;
  ByteRover MCP is healthy and was curated for A2, and all PostgreSQL work remains confined to
  the approved owned disposable cluster.
- The inherited host/operator list above remains pending. The live-development schema handoff
  target is now `0004_jmc4a`; no operator database, media, backup root, or production schedule
  was modified during A2.

## Phase A3 completion — 2026-07-13

- Phase commit: `520de2c58f544906e771d77f7b31ec48daec1f49`
  (`add canonical batch coordination`), tree
  `c9dc6bd91ce380ad5d2f841eeb6d5b0916780a21`, sole parent the A2 timeline commit
  `2764f40aad261df36b522a63364426e90502e841`; configured repository author only.
- Dynamic opening creates a ticketless parent and open projection with a random positive 63-bit
  producer-fence generation. Append row-locks the projection, verifies the exact generation,
  permanent open state, cap, provenance, child policy, and canonical idempotency, then submits one
  ordinary A1 child. Same-intent retries reuse the child; changed intent conflicts. Seal row-locks,
  freezes the exact total permanently, and is idempotent only for the same generation. Open batches
  cannot terminalize; sealed empty batches terminalize `no_change`.
- Direct-child first-terminal paths in the fenced writer, queued cancellation, pre-admission
  cancellation, and transport-intent reconciliation now advance the ticketless parent projection
  in the same transaction. Projection recomputation is bounded to 500 children, serializes on the
  batch row, updates exact counters and max-20 failure summaries, and applies the locked outcome
  matrix. Parent phase is queued before child work, running after child start/terminal activity,
  stopping after cancellation, and terminal only after seal plus complete terminal accounting.
- Compare-and-set repair checks the caller's projection sequence, recomputes bounded durable child
  state, emits `batch.repaired` only when state changes, and never modifies a child or PgQueuer row.
  Concurrent append/append, append/seal, and terminal/terminal races were exercised on PostgreSQL.
- Parent cancellation seals an open generation and cancels only direct nonterminal descendants in
  bounded pages through PgQueuer. Priority changes apply only to direct still-queued child tickets.
  Pause/resume fail closed unless an explicit parent policy opts in. Retry creates a new canonical
  parent and definition-selected failed/cancelled or all-child successors with parent and child
  retry lineage; original semantic history remains immutable. Correlation-only peers are untouched.
- The bounded batch response now includes failure and attention summaries. OpenAPI remains at 197
  paths; schema SHA-256 is
  `a87f0255baa3ebbf308d559da8760719b5a3942dc6c325482dc31256ff011e7e` and generated
  TypeScript SHA-256 is
  `625032a62cb22e7893906d2cfa8c5df4837e4b199c182ece456e6d3b588b57b6`.
- Final focused definition/submission/batch/command/delivery/gateway/progress/monitor gate:
  **105 passed** in 14.68s. Retained full suite: **1,053 passed, 21 failed, 2 warnings**
  in 60.66s. A final child-start projection regression gate passed **16 tests** in 1.96s after
  the admission hook was verified. The failures and warnings are exactly the recorded baseline
  set; no new failure,
  error, skip, or `xfail` was introduced.
- `ruff check marquee tests`, `git diff --check`, deterministic OpenAPI and generated-TypeScript
  drift, frontend check (0 errors and inherited 16 warnings in 8 files), frontend lint, and
  frontend production build passed. Alembic remains at sole head/current `0004_jmc4a` and
  `alembic check` reports no new upgrade operations.
- Production registry remains 48 definitions with exactly `system_noop` enabled on `control`; all
  six parent-only definitions are transport-disabled, real feature handlers and schedule
  occurrences remain disabled, and `media_write` remains unavailable for product work. Connection
  arithmetic remains 28 configured connections against 32.

## Current phase and exact next steps

- Phases A0-A3: **complete**. Phase A4 — schedule catalog and callbacks: **in progress**.
- Exact next steps:
  1. Add the code-owned schedule catalog with stable key, PgQueuer entrypoint/expression, produced
     job type, trigger/initiator, enabled predicate, occurrence policy, and request/subject builder;
     add no Marquee schedule table.
  2. Register fixed test schedules plus inactive periodic library-sync and hourly audio/subtitle
     deep-scan callbacks. Callbacks normalize PgQueuer's UTC schedule value, read current versioned
     configuration, and submit only ordinary canonical jobs.
  3. Implement canonical `schedule:<key>:<normalized-due-utc>` occurrence keys, interval bucketing,
     missed-bucket coalescing, and bounded diagnostics for disabled/ineligible occurrences while
     leaving callback failure recovery to PgQueuer.
  4. Prove duplicate scheduler/restart occurrence idempotency, configuration disable/re-enable,
     multi-scheduler uniqueness, coalescing, and misfire behavior without enabling a real product
     occurrence.
  5. Rerun focused, retained, generated-contract, frontend, schema, and manifest gates before the
     A4 phase commit.

## A3 deviations and pending operator work

- No schema, transport, handler-enable, schedule-enable, inline-execution, or connection-budget
  deviation was introduced. The existing JMC3 synthetic parent-progress prototype remains only as
  retained compatibility coverage; JMC4A canonical parents use no ticket or execution attempt and
  derive aggregate state solely from the batch projection.
- The ByteRover local CLI/swarm-helper and sandbox loopback limitations remain as previously
  recorded; ByteRover MCP is healthy and was curated for A3, and PostgreSQL work remained confined
  to the approved owned disposable cluster.
- The inherited host/operator list remains pending. No operator database, media, backup root,
  production handler, or production schedule was modified during A3.

## Phase A4 completion — 2026-07-13

- Phase commit: `f5b8a552fc2a4f253f769f40bd6d17b63c1e76f2`
  (`add canonical schedules`), tree
  `5bcf9a76deb1a0b0e2d97323292de3c1f2075022`, sole parent the A3 timeline commit
  `dac3fb9ca5ee1a0efc26d0cfb1f31da91c185528`; configured repository author only.
- The immutable code-owned production catalog contains exactly `library-sync` and
  `audio-subs-deep-scan`. Each entry owns a stable catalog key, PgQueuer
  entrypoint/expression, produced type, trigger and sanitized initiator, enabled predicate,
  occurrence policy, and request/subject builders. No Marquee schedule table, cursor, claim,
  retry clock, heartbeat, or custom scheduler was added.
- `library-sync` is registered on `schedule_library_sync` with `* * * * *`; the callback floors
  PgQueuer's aware pick value to the current UTC `SYNC_INTERVAL_MINUTES` bucket, so missed work
  coalesces to at most one current bucket. `audio-subs-deep-scan` is registered on
  `schedule_audio_subs_deep_scan` with `0 * * * *`; only the current configured UTC
  `AUDIO_SUBS_DEEP_SCAN_HOUR` is eligible, and the current versioned enabled/hour/batch values
  are read for every callback.
- Both production predicates include the code-locked
  `PRODUCTION_SCHEDULE_OCCURRENCES_ENABLED = False` gate. Product occurrences therefore record
  a bounded disabled/ineligible diagnostic and create no canonical job or PgQueuer ticket.
  Diagnostics are sanitized and capped at 100 process-local observations. Unexpected callback
  failures propagate to PgQueuer rather than creating a second recovery authority.
- Schedule idempotency keys are
  `schedule:<catalog-key>:<normalized-UTC-occurrence>` using compact UTC timestamps. Duplicate,
  restart, and concurrent two-scheduler callback attempts resolve through the canonical
  advisory-lock/idempotency boundary to one job. The fixed one-second test catalog is injected
  explicitly, temporarily grants the test-only schedule trigger to `system_noop`, and proved an
  actual PgQueuer dispatch calls only canonical submission; it is not registered by the
  production scheduler.
- `library_sync` now declares schedule provenance, scheduled maintenance subjects resolve from
  two bounded code-owned references, and `audio_subs_deep_scan` snapshots its current versioned
  enabled/hour/batch configuration. Real feature definitions remain dispatch-disabled.
- Final focused A0/A1/A2/A3/A4/PgQueuer/command gate: **97 passed** in 14.63s; the dedicated A4
  schedule gate passed **9 tests** in 1.01s. Retained full suite: **1,062 passed, 21 failed,
  2 warnings** in 60.46s. The failures and warnings are exactly the recorded baseline set; no new
  failure, error, skip, or `xfail` was introduced.
- `ruff check marquee tests`, `git diff --check`, deterministic OpenAPI and generated-TypeScript
  drift, frontend check (0 errors and inherited 16 warnings in 8 files), frontend lint, and
  frontend production build passed. OpenAPI remains at 197 paths; schema SHA-256 is
  `a87f0255baa3ebbf308d559da8760719b5a3942dc6c325482dc31256ff011e7e` and generated
  TypeScript SHA-256 is
  `625032a62cb22e7893906d2cfa8c5df4837e4b199c182ece456e6d3b588b57b6`. Alembic remains at sole
  head/current `0004_jmc4a`, and `alembic check` reports no new upgrade operations.
- Production registry remains 48 definitions with exactly `system_noop` enabled on `control`;
  all real product handlers, both production schedule occurrences, and `media_write` product work
  remain disabled. Connection arithmetic remains 28 configured connections against 32.

## Current phase and exact next steps

- Phases A0-A4: **complete**. Phase A5 — entrypoints, load gate, and certification:
  **in progress**.
- Exact next steps:
  1. Generalize worker registration so every manifest entrypoint binds the same delivery kernel
     with an immutable expected-entrypoint value and no route, handler, or inline execution path.
  2. Add or verify explicit CPU and maintenance global limits while retaining control, network,
     media-read, GPU, and unavailable media-write boundaries; prove worker concurrency, PgQueuer
     batch size, safety-gate, and database connection arithmetic remain coherent.
  3. Extend readiness with sanitized registered/enabled entrypoints, definition counts, schedule
     catalog health, batch projection schema, and connection arithmetic without raw transport
     rows, schedule IDs, payloads, advisory keys, or secrets.
  4. Exercise concurrent control/network/CPU/media-read/GPU/maintenance test canaries, entrypoint
     mismatch rejection before attempt admission, schedule and batch certification, and the final
     production enabled/disabled manifest.
  5. Complete every automated, manual, exception, host, and operator gate. Only then execute the
     exact §9 final-only compaction procedure; stop on any ancestry, ownership, backup, or tree
     identity uncertainty and do not begin JMC4B.

## A4 deviations and pending operator work

- An exploratory definition-only pytest grouping left the singleton configuration provider
  uninitialized and reproduced its known two order-dependent failures; the retained full suite
  initialized it through the supported session path and preserved the exact 21-failure baseline.
  A proposed test-only enablement of the real deep-scan definition was rejected by the registry's
  locked `system_noop`-only invariant; the final tests use the fixed no-op schedule and never
  weaken that boundary.
- The ByteRover local CLI/swarm-helper and sandbox loopback limitations remain as previously
  recorded; ByteRover MCP is healthy and was curated for A4, and PostgreSQL work remained confined
  to the approved owned disposable cluster.
- The inherited host/operator list remains pending. No operator database, media, backup root,
  production handler, or production schedule occurrence was modified during A4.

## Phase A5 completion and final pre-squash certification — 2026-07-13

- Phase commit: `cf77a0b60085851b2d56ff7babf0f9c6fbc66428`
  (`certify worker entrypoints`), tree
  `801c0e8b0bc2c899c0c85bae2c1b71bc715514c6`, sole parent the A4 timeline commit
  `6c9d4fc272269f6b1a671a5054207d4388713118`; configured repository author only.
- The worker now registers exactly the six JMC4A execution entrypoints `control`, `network`,
  `cpu`, `media_read`, `gpu`, and `maintenance`. Every registration captures an immutable
  expected entrypoint and calls the same canonical delivery kernel. A mismatch is rejected before
  transport payload parsing, database preflight, or execution-attempt admission. `media_write`
  retains a reserved limit but is not registered and remains unavailable for product work.
- PgQueuer remains the sole queue-concurrency authority. Explicit entrypoint limits are control 4,
  network 4, CPU 2, media-read 2, GPU 1, and maintenance 1; the worker-global limit is 4 and its
  dequeue batch size is 2. The safety gate retains four sessions per worker. Settings and
  readiness fail closed if batch size exceeds worker concurrency or safety-gate sessions fall
  below worker concurrency.
- Concurrent test transport canaries exercised four queued items for every registered entrypoint
  through a real PgQueuer drain. They proved the worker-global bound, every entrypoint bound, and
  the GPU/maintenance singleton bounds. Existing live PostgreSQL coverage retains the separate
  database-global entrypoint-concurrency proof across workers.
- Readiness now reports only sanitized registered/enabled entrypoints and limits, immutable
  schedule catalog policy, batch projection version/caps/required fields, and explicit connection
  arithmetic. It exposes no raw transport row or schedule ID, payload, dedupe key, advisory key,
  or secret. The connection budget remains API 15 + API listener 1 + worker 8 + scheduler 3 +
  migration 1 = **28 configured against 32**.
- Final focused A0-A5/PgQueuer/command certification: **107 passed** in 15.02s; the dedicated A5
  worker certification passed **6 tests** in 1.21s and the delivery regression gate passed
  **28 tests**. Retained full suite: **1,068 passed, 21 failed, 2 warnings** in 61.98s. The
  failures and warnings are exactly the initial retained baseline; no new failure, error, skip,
  or `xfail` was introduced.
- `ruff check marquee tests` and `git diff --check` passed. Alembic has sole head/current
  `0004_jmc4a`, and `alembic check` reports no new upgrade operations. OpenAPI remains current at
  197 paths with schema SHA-256
  `a87f0255baa3ebbf308d559da8760719b5a3942dc6c325482dc31256ff011e7e`; generated TypeScript
  remains current with SHA-256
  `625032a62cb22e7893906d2cfa8c5df4837e4b199c182ece456e6d3b588b57b6`. Frontend API drift,
  generated-TypeScript drift, check (0 errors and the inherited 16 warnings in 8 files), lint,
  and production build all passed.
- Production registry certification remains exactly 48 definitions with only
  `system_noop/control` enabled. All real handlers, all parent-only transport execution, both
  production schedule occurrences, and all `media_write` product work remain disabled. No
  handler runs inline or inside Uvicorn.

## Complete JMC4A pre-squash phase map

- Exact plan base: `640ba6b2a0f94945ff9c9ca7e10791a9b6e285d2`.
- A0 implementation `e5fed0a0afcbf2e11469e4d25a3a7d943642c4a9`; A0 timeline
  `a4e2f19309ae1ccc7b7e1a1e96a496a7c57768d6`.
- A1 implementation `c5cd3b135378dbb203c76b44bad6dd241cc39717`; A1 timeline
  `4d8798da3829aaf117eadd1e9fe69197798cf248`.
- A2 implementation `5469bb86938d74a4579e451e91210e4b6d1ab666`; A2 timeline
  `2764f40aad261df36b522a63364426e90502e841`.
- A3 implementation `520de2c58f544906e771d77f7b31ec48daec1f49`; A3 timeline
  `dac3fb9ca5ee1a0efc26d0cfb1f31da91c185528`.
- A4 implementation `f5b8a552fc2a4f253f769f40bd6d17b63c1e76f2`; A4 timeline
  `6c9d4fc272269f6b1a671a5054207d4388713118`.
- A5 implementation/pre-timeline tip `cf77a0b60085851b2d56ff7babf0f9c6fbc66428`.
- The final pre-squash tip is this timeline commit. Because a commit cannot contain its own hash,
  it is to be resolved exactly by the timestamped recovery branch/tag created immediately after
  this commit. The intended compact resolver tag is `jmc4a-complete`.

## Final locked contracts and operator handoff

- Fixed batches are atomic and sealed at creation; empty fixed batches terminalize `no_change`.
  Dynamic batches use a positive producer-fence generation, accept bounded idempotent appends only
  while open, seal permanently, and cannot terminalize before sealing. Both fixed and dynamic
  child caps are 500; failure summaries are capped at 20. Direct terminal children are counted
  exactly once. Any partial child or mixed positive/non-positive result aggregates to
  `partially_succeeded`; otherwise homogeneous positive work yields `succeeded` or `no_change`,
  failures yield `unsafe` when any child is unsafe and otherwise `failed`, all-superseded yields
  `superseded`, and cancellation-only work yields `cancelled`. Nested progress uses the stable
  sealed denominator and parent-only jobs never receive a
  PgQueuer ticket or execution attempt.
- `library-sync` uses PgQueuer cron `* * * * *`, normalizes to the current UTC configured interval
  bucket, and coalesces missed work to at most the current bucket. `audio-subs-deep-scan` uses
  `0 * * * *`, admits only the configured current UTC hourly window, and snapshots current
  enabled/hour/batch configuration. Occurrence keys are
  `schedule:<catalog-key>:<normalized-UTC-occurrence>`. Duplicate, restart, misfire, and two-
  scheduler submissions converge through canonical idempotency. Both production occurrence
  predicates remain code-locked false; sanitized diagnostics are capped at 100.
- Automated local smokes performed against the owned PostgreSQL 18.3 cluster include complete
  migration/reset/model equivalence, PgQueuer installation and transactional enqueue rollback,
  fixed/dynamic/concurrent batch races and repair, schedule duplicate/restart/misfire/coalescing
  and two-scheduler uniqueness, delivery redelivery/attempt fencing, per-worker and database-global
  concurrency, readiness/manifest/budget checks, deterministic generated contracts, and the full
  backend/frontend gates above.
- Pending host/operator work remains: delegated cgroup-v2 kill/empty proof; real
  API/worker/listener SIGKILL and picked-job restart; orphan/workspace reconciliation;
  PostgreSQL restart and LISTEN/NOTIFY disruption; multiprocess safety-gate contention and
  connection loss; deployment-filesystem symlink/path-swap/cross-device behavior; long-running
  100 MiB log capacity/retention; real-storage backup rotation/restore; and a real Node-adapter
  proxy stream/abort smoke. Apply or sanctioned-reset the live development database to
  `0004_jmc4a`, rerun role-sensitive isolated migration tests where applicable, and evaluate the
  four inherited low-severity npm advisories separately.
- No operator database, normal data directory, media, backup root, production handler, production
  schedule, external service, or pushed ref was modified. The local ByteRover CLI/swarm helper is
  absent, but ByteRover MCP remained healthy and was used; loopback database commands used only the
  approved owned disposable cluster. An isolated readiness-test ordering run and an A4
  definition-only grouping reproduced known uninitialized configuration-provider behavior; the
  retained full suite used the supported initialization path and preserved the exact baseline.
- Phases A0-A5 are **complete and certified**. After §9 history compaction and its focused smoke,
  JMC4B may start only from the compact `jmc4a-complete` tree with exactly `system_noop` product
  enabled. Do not begin JMC4B as part of this work.
