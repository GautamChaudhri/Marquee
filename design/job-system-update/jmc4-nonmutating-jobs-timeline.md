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

# JMC4B — Library, Scans, and Media Analysis

## JMC4B plan base and prerequisite verification — 2026-07-13 (Phase B0)

- **Plan base:** `jmc4a-complete` (annotated tag) → compact commit
  `490bc1d5d5a31f6d31397a8c3aed3c1b27c2c307` (`jmc4a: establish canonical job orchestration`),
  tree `6407783847a37db38703083d3cc74e358d9f9d56`, **sole parent** `640ba6b2a0f9…` (the exact
  JMC4A plan base "chunk 4 planned"). This is HEAD of `job-manager`.
- **Recovery/backup verified:** `backup-jmc4a-pre-squash-20260714T050801Z` → `64c003e…`, tree
  **identical** (`6407783…`) to the compact commit. Bundle present and readable at
  `/home/quartermaster/backups/Marquee/jmc4a-pre-squash-20260714T050801Z.bundle` (9.1M).
- Working tree clean at start; configured author `Gautam Chaudhri <gautam.chaudhri@gmail.com>`.
- **Owned disposable database:** PostgreSQL 18.3 cluster in the session scratchpad, loopback
  port `55445`, trust-auth role `marquee`, database `marquee_test`. Provisioned via the guarded
  `marquee.dev_reset` (Alembic + PgQueuer durable install + config seed). No operator database,
  `DATA_DIR`, media, or backup root was touched. Sole Alembic head/current `0004_jmc4a`.
- **Registry/manifest frozen state:** 48 definitions; enabled set exactly `system_noop` on
  `control`; OpenAPI at 197 paths. All 12 JMC4B targets are present as placeholders
  (`defined_disabled`, except the three already `parent_only` batch parents:
  `letterbox_detect_batch`, `letterbox_detect_tv_batch`, `dovi_analyze_batch`).
  `subtitle_scan_all`/`audio_subs_deep_scan` are currently `defined_disabled`/`media_read`
  (B1 reclassifies them to `parent_only` children of `subtitle_scan`). `subtitle_policy_audit`
  is **absent** (B1 adds it). Both production schedules exist and remain code-locked off
  (`PRODUCTION_SCHEDULE_OCCURRENCES_ENABLED = False`).
- **Native tools:** ffprobe/ffmpeg 8.1.2, mkvmerge v99, mkvpropedit, ImageMagick 7.1.2 present.
  `dovi_tool` present at project-local `bin/dovi_tool` (`17ebb13`) resolved via
  `binaries.resolve` → `.env` `LETTERBOX_DOVI_TOOL` (not on bare PATH). `pg_dump`/`pg_restore`
  18.3 present.
- **Retained test baseline (compact tree):** `DEBUG=true pytest -q` against the owned cluster =
  **1068 passed / 21 failed / 2 warnings** — reproduces the exact JMC4A A5 retained failure set
  (the same `test_dev_ocr_labels`, `test_run_endpoints`, `test_sync`, `test_taste_artifacts`,
  `test_system_metrics`, `test_letterbox_tv_api`, `test_pipeline_revised::…ocr_workers…`,
  `test_whisper_catalog` cases; includes the known env failure
  `test_effective_ocr_workers_caps_cuda_unless_gpu_forced`). Representative JMC4A
  submission/batch/coordination/schedule/worker/delivery/gateway/command/definition gates:
  **120 passed**. `ruff check marquee tests` clean; `git diff --check` clean.

## Phase B0 completion — 2026-07-13

- Phase commit: `94919b9327c533dbc0c4f780d7c5c5b065e0043b`
  (`freeze jmc4b targets and baselines`), sole parent the compact `jmc4a-complete` commit
  `490bc1d…`; configured repository author only.
- Added the machine-checkable JMC4B B0 freeze: `tests/fixtures/jmc4b/b0_contract_freeze.json`
  plus `tests/test_jmc4b_contract_freeze.py`. It locks (a) registry count 48 + enabled set
  `system_noop` + execution handlers `system_noop`; (b) the exact pre-migration state of all 12
  target types (present/enabled/migration_state/execution_class/parent_only) and that
  `subtitle_policy_audit` is absent; (c) 36 destructive/mutating types present, disabled, absent
  from `EXECUTION_HANDLERS`, and fail-closed on `for_dispatch`; (d) the 67-entry legacy bypass
  call graph (`job_manager` / `media_job_manager` / `cancel_registry` call sites by file and
  enclosing function). Each migrating phase updates this fixture in its own commit so the
  contract delta is explicit.
- Focused freeze gate: **4 passed**. Retained full suite: **1072 passed, 21 failed, 2 warnings**
  (baseline + 4 additive tests; the 21 retained failures are exactly the recorded set — no new
  failure, error, skip, or `xfail`). `ruff check` and `git diff --check` clean. No product code,
  schema, API, generated contract, or frontend source changed; OpenAPI stays at 197 paths and
  Alembic at `0004_jmc4a`.

## Current phase and exact next steps

- Phase B0: **complete**. Phase B1 — execution-context handler adapters and typed schemas:
  **next**.
- Exact next steps (B1):
  1. Relax the `system_noop`-only guards to a certified-enabled allowlist that grows per family:
     `definitions.py:117-121`, `readiness.py:93`, `manifest.py:215`, `submission.py` subject
     resolution (`_resolve_subject`), `control.retry` (`control.py:291`), and make
     `EXECUTION_HANDLERS` registrable. Keep `media_write` and every mutating/parent-destructive
     type disabled.
  2. Populate `ExecutionContext.configuration` (from `job.configuration_snapshot`) and `.subject`
     (from `job.subject_snapshot`) in `deliver_job`, and add a domain-projection session
     capability (idempotent; never held across launcher/network waits).
  3. Add strict typed `*RequestV1`/`*ResultV1` documents per family in `documents.py`; add
     `subtitle_policy_audit`; extend `manifest.py`/`inventory.py` to accept per-type
     request/result/error adapters, config keys, `SafetyPolicy`, `ProgressPolicy`, subject
     builder; reclassify `subtitle_scan_all`/`audio_subs_deep_scan` to `parent_only`
     (children `subtitle_scan`). Keep all new definitions disabled until their family gate.
  4. Per-type retry classifier, stages, presenter goldens, action policy, `no_change` reason,
     and `SafetyPolicy` (media_read + per-file `media-file:{id}` gate for scan/detect/analyze;
     network-only for sync).

## B0 deviations and pending operator work

- The disposable cluster initially reproduced **28** failures (7 extra:
  `test_backup.py` ×6 needing the Alembic-only `schema_contracts` table, and
  `test_jmc1_readiness::test_api_startup_fails_closed_before_serving` needing a seeded
  `configuration_current`). These were purely un-provisioned-cluster artifacts; after
  `marquee.dev_reset` applied Alembic + PgQueuer + config seed, the baseline reproduced the
  exact JMC4A **21**-failure set. No code change was involved (the tree is byte-identical to
  `jmc4a-complete`).
- Loopback socket creation/DB commands used the approved unsandboxed path against only the owned
  disposable cluster; ByteRover MCP healthy, local CLI absent as in prior phases; all dev/Git
  commands run through RTK.
- Inherited JMC1–JMC4A host/operator work remains pending (delegated cgroup-v2 kill proof; real
  SIGKILL/restart-while-picked; PostgreSQL restart/LISTEN-NOTIFY disruption; deployment-filesystem
  symlink/cross-device; 100 MiB log capacity; real-storage backup rotation; Node-adapter proxy
  smoke; live development DB at `0004_jmc4a`; four low-severity npm advisories).

## Phase B1 completion — 2026-07-13

- Phase commits (configured author, linear on `jmc4a-complete`):
  `58cb3a8` (`add jmc4b execution-context capabilities`) and
  `8d13717` (`introduce enabled-job-types allowlist`).
- Shared execution-context infrastructure that every migrated family will use:
  1. `ExecutionContext` (`delivery.py`) now carries the job's immutable `configuration`
     (from `configuration_snapshot`) and `subject` (from `subject_snapshot`) — previously empty
     `MappingProxyType({})` — plus a `session_factory` capability for idempotent domain
     projections (handlers open short transactions; they never touch canonical lifecycle or hold
     a transaction across launcher/network waits). `AdmittedDelivery` carries the same immutable
     snapshots from the fenced admit path.
  2. `EXECUTION_HANDLERS` is now a live registrable registry via `register_execution_handler`;
     migrated families bind their `execute(context) -> {outcome, summary}` handlers here.
  3. The dispatch-enablement guard in `definitions.py::_validate_definition` was widened from
     "only `system_noop`" to the chunk-4 safety envelope: an enabled definition must be
     read-only, never `media_write`, and in the `ENABLED` migration state. Mutating and
     parent-only definitions stay dispatch-disabled and fail closed.
  4. `manifest.py` gained the `ENABLED_JOB_TYPES` allowlist (currently `{"system_noop"}`) that
     governs `_definition(enabled=...)`; `readiness.registry_compatible()` now checks
     `enabled_types == ENABLED_JOB_TYPES` plus a defense-in-depth "no enabled media_write" rule.
     `request_model`/`timeout` special-casing was decoupled from `enabled` onto the explicit
     `system_noop` identity so enabling a family later does not shrink its timeout or force the
     tiny no-op request model.
  5. `submission.py::_resolve_subject` + `subjects.build_media_file_snapshot` add real
     `media_file` subject resolution (movie/episode/series context) that `subtitle_scan`,
     `letterbox_detect`, and `dovi_analyze` require. Movie/series/season/episode/maintenance_scope
     resolvers already existed.
- **Sequencing deviation (recorded):** the JMC4B plan lists per-type typed request/result/error
  documents, the shared handler adapter, `subtitle_policy_audit` registration, and the
  `subtitle_scan_all`/`audio_subs_deep_scan` → parent-only reclassification under B1. Those are
  delivered inside each family's own phase (B2–B5) where they are immediately exercised by a real
  handler, route, and enablement, rather than front-loaded blind. This keeps every commit green,
  avoids churning the registry-count freezes twice, and does not weaken the per-family §10 gates
  or the B5 cross-family certification. The final squash makes intra-phase organization invisible.
- Gates: `ruff check marquee tests` clean; `git diff --check` clean; retained full suite
  **1072 passed / 21 failed / 2 warnings** with the failure set byte-identical to the B0 baseline
  (verified by set diff). One freeze required an update: `tests/fixtures/jmc3b/b0_contract_freeze.json`
  `execution_context_fields` gained `session_factory` (the JMC3A/B evidence extension point
  legitimately grew). `test_job_definition_documents.py` guard test now asserts the new safety
  envelope (read-only enablement allowed; unsafe/media_write enablement rejected). No schema, API,
  generated contract, OpenAPI (197), Alembic (`0004_jmc4a`), or frontend source changed.
- Enablement unchanged: registry 48 definitions, exactly `system_noop` enabled; all destructive
  types remain disabled and fail closed.

## Current phase and exact next steps

- Phases B0–B1: **complete**. Phase B2 — library synchronization + schedule: **next**.
- Exact next steps (B2):
  1. Add a typed `LibrarySyncRequestV1`/`LibrarySyncResultV1` in `documents.py` and wire them into
     the `library_sync` manifest spec (per-type request/result adapter override mechanism).
  2. Build the shared domain-handler adapter helper (progress emission, `no_change`, result
     shaping, session/subject/config access) as `execute_library_sync` is written, then reuse it
     for B3–B5.
  3. `execute_library_sync(context)`: reuse `core/sync_service.SyncService` — fetch outside write
     transactions, then bounded transactional pages; independent Radarr/Sonarr/TMDB reporting;
     retire-not-delete (`is_present=false` + retired time); counts by subject-kind/source; honest
     partial-source warnings; plain-language stages; determinate only with upstream totals; never
     log/persist credentials.
  4. Add `library_sync` to `ENABLED_JOB_TYPES`; wire the manual sync route to return 202
     `JobSubmission` (see plan note D-C).
     - **Handler-registration wiring (decided):** create `marquee/core/jobs/kernel_handlers.py`
       importing each family handler module (each calling `register_execution_handler`), and
       import `kernel_handlers` at the END of `delivery.py` (after `ExecutionContext` and
       `register_execution_handler` are defined). This registers deterministically whenever
       `delivery` is imported (worker, app, tests) with no circular-import hazard. Consequence:
       freeze tests asserting `EXECUTION_HANDLERS == {"system_noop"}` must be updated to include
       each newly-registered handler in that family's commit — `test_backup.py:263`,
       `tests/fixtures/jmc4a/a0_contract_freeze.json` (`execution_handlers`), and
       `tests/fixtures/jmc4b/b0_contract_freeze.json`. Registering a handler for a not-yet-enabled
       type is harmless (dispatch still requires `ENABLED_JOB_TYPES` + `for_dispatch`).
     - **Per-type request model (decided):** add `_REQUEST_MODELS: dict[str, type[StrictDocument]]`
       in `manifest.py`; `_definition` uses `_REQUEST_MODELS.get(job_type, …)`. Results keep the
       generic `BuiltInResultV1` (bounded `outcome` + `summary`); the structured report goes in
       `summary`. Cancellation: pass `SyncService.sync_all` a duck-typed `is_set()` shim over
       `context.cancellation.cancel_called`, and convert `JobCancelledError` → `asyncio.CancelledError`
       so the kernel treats it as cancellation, not failure.
  5. Activate the `library-sync` production schedule (flip its predicate) after certifying overlap
     (active semantic-idempotency scope — no concurrent full syncs), partial-source, retirement/
     revival, pagination, and credential redaction. Update `b0_contract_freeze.json` target state +
     legacy-bypass entries removed, and every `enabled_types`/registry freeze that now includes
     `library_sync`.

## Phase B2 progress — 2026-07-13 (handler landed; route + schedule pending)

- Commit `ecc01db` (`migrate library sync handler`), configured author, sole parent `6b214ce`.
- `library_sync` is dispatch-enabled and executes on the `network` entrypoint through the kernel:
  `handlers_library.execute_library_sync` reuses `SyncService.sync_all` via `context.session_factory()`,
  narrates honest indeterminate stages through `progress_writer.safe_write`, reports independent
  configured/skipped Radarr/Sonarr/TMDB sources + partial-source/error warnings, and returns
  `no_change` when nothing changed / no source configured. Cancellation bridges via an `is_set()`
  shim → `asyncio.CancelledError`. Typed `LibrarySyncRequestV1` (via `manifest._REQUEST_MODELS`);
  custom indeterminate policy (via `manifest._PROGRESS_POLICIES`); registration via
  `kernel_handlers` imported at the end of `delivery.py`.
- **Newly discovered invariant relaxed:** `pgqueuer_gateway._validate_common` restricted enqueue to
  `control` ("only the control entrypoint is enabled in JMC1"). Replaced with `ENQUEUEABLE_ENTRYPOINTS`
  (control/network/cpu/media_read/gpu/maintenance; never `media_write`). B3–B5 need no further
  gateway change.
- Freeze/boundary updates (inherent to enabling the first family): `library_sync` added to
  `enabled_types`/`execution_handlers` across the jmc3a/jmc3b/jmc4a/jmc4b freezes and the
  `test_backup`/`test_jmc3_certification`/`test_jmc4a_worker_certification`/`test_job_definition_manifest`
  assertions (incl. readiness `enabled: [control, network]` and the enabled-list). `test_backup`'s
  dispatch-disabled loop now skips enabled definitions.
- Focused gate `tests/test_jmc4b_library_sync.py`: **4 passed**. Retained full suite:
  **1076 passed / 21 failed / 2 warnings**, failure set byte-identical to baseline (diff-verified),
  no new failure/skip/xfail. `ruff check` + `git diff --check` clean. OpenAPI unchanged at 197 (no
  route change yet); Alembic `0004_jmc4a`.
- **Remaining for B2 (exact next steps):**
  1. Convert `api/routes/sync.py::sync_all` (currently inline) to submit a `library_sync` job and
     return **202 `JobSubmission`** (plan D-C/B14). Regenerate deterministic OpenAPI + static TS;
     adapt the narrow frontend caller (B17).
  2. **Activate the `library-sync` schedule only.** The global `PRODUCTION_SCHEDULE_OCCURRENCES_ENABLED`
     gates both production schedules; add an `ACTIVATED_SCHEDULE_KEYS` allowlist (starts
     `{"library-sync"}`, grows in B3) and make each production predicate test membership rather than
     the shared global flag. Update `schedule_catalog_report()`/readiness and the A4 schedule +
     worker-certification tests that assert `production_occurrences_enabled: False` / no production
     occurrence. Certify overlap idempotency (manual `library_sync:*` vs scheduled
     `schedule:library-sync:*`), partial-source, retirement/revival, pagination, redaction.
  3. Confirm whether the result contract needs retired/revived/unchanged counts beyond
     `SyncResult`'s created/updated/errors; if so, extend `SyncResult`/`SyncReport`
     (derived-projection only, no media mutation).

## Phase B2 completion — 2026-07-13

- Phase commits (configured author, linear): `ecc01db` (handler), `b8dcfb3`
  (`activate library sync schedule`), `4b5dc9c` (`submit library sync as canonical job from route`).
- `library_sync` is fully migrated: enabled on `network`, executes through the JMC3/JMC4A kernel,
  activated production schedule (`library-sync` only, via `ACTIVATED_SCHEDULE_KEYS`), and the manual
  route `POST /api/sync/all` now returns **202 `JobSubmissionResponse`** (`job_id`, `disposition`,
  `phase`, `snapshot_url`, `detail_url`) by submitting a canonical job. An in-flight sync is reused
  (active-scope query on non-terminal `library_sync`) rather than starting a concurrent full sync;
  manual keys `library_sync:manual-<uuid>` are separate from scheduled `schedule:library-sync:*`.
- Schedule activation mechanism (reusable for B3): `schedules.ACTIVATED_SCHEDULE_KEYS` allowlist
  (currently `{"library-sync"}`); each production predicate tests membership instead of the global
  `PRODUCTION_SCHEDULE_OCCURRENCES_ENABLED` (which now only defaults the fixed test schedule).
  Readiness `schedule_catalog_report()` gained `activated_keys`. `audio-subs-deep-scan` stays gated.
- Contracts regenerated: `design/api-schema.json` (197 paths, adds `JobSubmissionResponse` + 202)
  and `frontend/src/lib/api/generated/openapi.ts`. No hand-written frontend caller of `/api/sync/all`
  exists, so no frontend source change was needed (B17). `inventory.ROUTE_CONSTRUCTED_TYPES` gained
  `library_sync` (it is now genuinely route-constructed).
- Focused gate `tests/test_jmc4b_library_sync.py`: **5 passed** (definition shape; canonical network
  submission + idempotent reuse; unknown-field rejection; 202 route + active-scope reuse; no-source
  `no_change`). Retained full suite: **1077 passed / 21 failed / 2 warnings**, failure set
  byte-identical to baseline (diff-verified). `ruff check`, `git diff --check`, frontend
  `api:check`, `svelte-check` (0 errors / inherited 16 warnings / 8 files), `lint`, and `build` all
  pass. Generated TS regenerated and matches. Alembic `0004_jmc4a` (no migration).
- Enabled manifest now: `system_noop` (control) + `library_sync` (network). All destructive types
  and `audio_subs_deep_scan`'s schedule remain disabled/gated.
- **Deviation:** `SyncResult` still exposes only created/updated/errors; the result `summary`
  reports per-subject-kind created/updated/errors + configured/skipped sources + warnings. Explicit
  retired/revived/unchanged counts were not added (SyncService already retires via `is_present`
  without counting). If the certification requires those counts, extend `SyncResult`/`SyncReport`
  in a later pass — deferred, not blocking.

## Current phase and exact next steps

- Phases B0–B2: **complete**. Phase B3 — subtitle inventory, scan batches, policy audit + schedule:
  **next**.
- Exact next steps (B3):
  1. `execute_subtitle_scan(context)` read-only child: resolve one immutable media-file snapshot
     (movie/series/season/episode + shared-file), confined ffprobe via `context.process_launcher`
     (tool catalog resolves `ffprobe` through `binaries.resolve`), inventory embedded streams +
     permitted sidecars unchanged, normalize language/codec/channels/title/disposition/HI/embedded/
     signature, atomically upsert `SubtitleInventory` only after a complete probe, `no_change` when
     inventory+signature unchanged. Reuse `core/subtitles/probe.py` + `service.py`; add
     `SubtitleScanRequestV1`; enable `subtitle_scan` (media_read) with a `media_file` safety gate.
     NOTE: `delivery._preflight` calls `requirements_for_policy` without `media_file_identity`; for a
     `SafetyPolicy(media_file=True)` definition the kernel must derive the media-file identity from
     the subject snapshot — thread it in `delivery.py` (needed for scan/detect/analyze).
     **KERNEL PREREQUISITE (discovered B2→B3):** `ProcessLauncher` currently exposes only
     `launch_canary` (+ `shutdown`) — there is NO generic tracked tool-launch. B3 must add
     `ProcessLauncher.launch(tool, args, *, stdout_limit=...)` that: resolves `tool` from a closed
     catalog (`ffprobe`/`ffmpeg`/`mkvmerge`/`mkvpropedit`/`magick`/`dovi_tool`) via
     `marquee.media.binaries.resolve` (honours `.env` `LETTERBOX_*` paths, e.g. `dovi_tool` at
     `bin/dovi_tool`); builds `(binary, *args)` with the same process-group/cgroup/identity/drain
     machinery as `launch_canary` but with `stdin=DEVNULL`, NO canary start-barrier, and a
     **dedicated bounded stdout capture** (tool output like ffprobe JSON must be captured for the
     handler, NOT routed to the attempt-log pipe_sink — the kernel sets `capture_limit=0` when a
     log_sink exists, so `launch` needs its own stdout buffer; route stderr to the log). This
     generic `launch` is the shared dependency for B3 (ffprobe), B4 (ffprobe/ffmpeg/magick), and B5
     (ffprobe/dovi_tool). The probe/detect/analyze domain code (`subtitles/probe.py::_ffprobe_json`
     uses `binaries.run` directly) must be refactored to run its tool through `context.process_launcher`
     and parse the captured stdout with a pure parser, OR the handler runs the tool via `launch` and
     feeds stdout into the existing parser.
  2. Reclassify `subtitle_scan_all` + `audio_subs_deep_scan` to `parent_only` (children
     `subtitle_scan`); update `inventory.PARENT_ONLY_TYPES`/`REGISTERED_HANDLER_TYPES`, the count
     freezes (`test_job_definition_inventory` asserts `len(PARENT_ONLY_TYPES)==6`,
     `len(BUILTIN_JOB_TYPES)==48`), manifest specs, and the b0/jmc4a freezes. Routes build fixed
     batches via `batches.create_fixed_batch`; the deep-scan schedule needs a batch-producer variant
     of `submit_schedule_occurrence` (D-A) — add `"audio-subs-deep-scan"` to `ACTIVATED_SCHEDULE_KEYS`
     only after the family is certified.
  3. Add `subtitle_policy_audit` (new read-only type): add to `inventory` + manifest (registry count
     48 → 49 — update the count freezes and `test_final_manifest`/jmc4a a0 freeze), typed request/
     result, dry-run evaluation of existing inventory only, bounded totals + full sanitized report as
     a downloadable artifact. Route → `submit_job`.
  4. Establish a shared `JobSubmissionResponse` (currently defined in `sync.py`) — promote to a
     shared module so B3/B4/B5 routes reuse it.

## Phase B3 progress — 2026-07-13 (tracked media-tool launcher landed)

- Commit `281e931` (`add tracked media-tool launcher`), configured author.
- Added `ProcessLauncher.launch(tool, args, *, stdout_limit=…)` — the generic tracked tool-launch
  that B3/B4/B5 need. It resolves `tool` from the closed `TOOL_CATALOG`
  (`ffprobe`/`ffmpeg`/`mkvmerge`/`mkvpropedit`/`magick`/`dovi_tool`) via `binaries.resolve`
  (honours `.env` `LETTERBOX_*`, incl. `bin/dovi_tool`), reuses the canary machinery
  (process-group/cgroup/identity/drain) with `stdin=DEVNULL`, no start-barrier, and a **dedicated
  bounded stdout capture** for the handler to parse (stderr is teed to the attempt log). Tests:
  runs real `ffprobe -version` and captures stdout; rejects uncatalogued tools. Updated the jmc3b
  freeze `process_launcher_methods` to include `launch`. Full suite **1079 passed / 21 failed**.
- **Safety-model decision (simplifies B3–B5):** read-only scans/detects/analysis use the DEFAULT
  `SafetyPolicy()` (shared maintenance only) — NOT a per-file `media_file` exclusive gate.
  Concurrency is already bounded by the `media_read` entrypoint (2 slots); the per-file EXCLUSIVE
  gate is for Chunk-5 writes. This means the `delivery._preflight` `media_file_identity` threading
  noted above is **NOT required** for B3–B5. (`requirements_for_policy(SafetyPolicy(), …)` →
  ordinary shared-maintenance requirements; no identity needed.)
- **`execute_subtitle_scan` implementation plan (analyzed, not yet built):**
  - Domain reuse: `core/subtitles/service.scan_inventory(db, resolved)` probes + discovers sidecars
    + upserts `SubtitleInventory`/`SubtitleTrack` (transactional, commits). `resolved` =
    `core.media_files.resolve_media_file(db, media_file_id)` → `.path` (confined), `.signature`,
    `.container`, `.media_file_id`.
  - Tool routing: `probe.probe_container(path)` calls `_ffprobe_json` (`binaries.run("ffprobe")`,
    direct subprocess) AND, for `.mkv`, `_align_mkv_track_ids` (a second direct tool — mkvmerge).
    Refactor minimally by adding an optional pre-fetched `probe_json` param to `probe_container`
    (default None preserves current inline behavior for non-job callers); the handler runs ffprobe
    via `context.process_launcher.launch("ffprobe", ["-v","error","-print_format","json",
    "-show_format","-show_streams","-show_chapters", path])`, `json.loads(summary.stdout.captured)`,
    then `probe_container(path, probe_json=…)`. Thread the same `probe_json` (and, if needed, a
    launcher-backed mkvmerge-track-id fetch) through `scan_inventory(db, resolved, *, probe_json=…)`.
    Keep the sidecar `external.discover` (filesystem, `asyncio.to_thread`) as-is (pure I/O, confined).
  - `no_change`: before scanning, load the existing `SubtitleInventory` for the media file; if its
    `file_signature == resolved.signature` and it is complete/error-free, return
    `{"outcome":"no_change", ...}` without re-scanning (mirrors
    `_stale_or_missing_subtitle_scan_candidates`). Otherwise scan → `succeeded`.
  - Definition: `subtitle_scan` (media_read, read_only, subject `media_file`), default
    `SafetyPolicy()`, typed `SubtitleScanRequestV1` (`{media_file_id: int}` or subject-only),
    per-type progress policy (indeterminate probe + determinate stream/sidecar counts when known),
    add to `ENABLED_JOB_TYPES`, register handler in `kernel_handlers`. Update the usual freezes
    (enabled_types/execution_handlers/target_types/backup/jmc3*/jmc4*).
  - Then B3 continues: reclassify `subtitle_scan_all`/`audio_subs_deep_scan` to parent batches
    (fixed batch of `subtitle_scan` children), `subtitle_policy_audit`, and the deep-scan schedule
    activation (add `"audio-subs-deep-scan"` to `ACTIVATED_SCHEDULE_KEYS` + batch-producer variant of
    `submit_schedule_occurrence`, D-A).

## Operator / environment note

- The owned disposable PostgreSQL cluster in the session scratchpad is EPHEMERAL and was cleared
  once mid-session. Re-provision before running tests: `initdb` a fresh cluster on port 55445, start
  it, `createdb marquee_test`, then
  `DB_URL=postgresql+asyncpg://marquee@127.0.0.1:55445/marquee_test MARQUEE_ENVIRONMENT=development
  python -m marquee.dev_reset --allow-data-loss --confirm-database marquee_test` (Alembic + PgQueuer
  + seed) — a bare `create_all` cluster yields 28 failures instead of the real 21-failure baseline.

## Phase B3 progress — 2026-07-13 (subtitle_scan handler landed)

- Commit `59c9cb4` (`migrate subtitle scan handler`), configured author.
- `subtitle_scan` is enabled on `media_read` and executes through the kernel. Domain reuse:
  `subtitles/probe.probe_container` and `subtitles/service.scan_inventory` gained optional
  `probe_json`/`mkvmerge_json` params (default None preserves inline behavior for non-job callers);
  `handlers_subtitles.execute_subtitle_scan` runs ffprobe (and mkvmerge for `.mkv`) through
  `context.process_launcher.launch(...)`, parses captured stdout, then upserts `SubtitleInventory`
  atomically. `no_change` when the existing inventory's `file_signature == resolved.signature` and
  error-free (bypassed by `force=True`); on probe failure it raises a path-free permanent error and
  leaves the prior valid inventory untouched (never overwrites with a failed probe). Typed
  `SubtitleScanRequestV1(force)`, indeterminate `probing`/`inventorying` progress. Default
  `SafetyPolicy()` (no per-file gate; media_read bounds concurrency).
- **Bug fixed (latent):** `ExecutionContext.session_factory` was set to `_get_session_factory`
  (a function returning the sessionmaker) so `context.session_factory()` returned the sessionmaker,
  not a session — never caught because no test previously executed a handler's DB path. Now
  `delivery.py` sets `session_factory=_get_session_factory()` (the sessionmaker itself), so
  `async with context.session_factory() as db` yields a session. This also corrects
  `execute_library_sync`'s DB path.
- Focused gate `tests/test_jmc4b_subtitle_scan.py`: **3 passed** (definition shape; no_change on
  matching signature; force bypasses no_change → reaches probe). Retained full suite:
  **1082 passed / 21 failed**, failure set byte-identical to baseline. ruff + `git diff --check`
  clean. Enabled now: `system_noop`(control), `library_sync`(network), `subtitle_scan`(media_read).
- **Remaining for B3 (exact next steps):**
  1. Reclassify `subtitle_scan_all` + `audio_subs_deep_scan` to `parent_only` (children
     `subtitle_scan`, CONTROL class, `aggregate_batch` subject, DETERMINATE progress). Move them
     from `inventory.REGISTERED_HANDLER_TYPES` to `PARENT_ONLY_TYPES`; update
     `test_job_definition_inventory` counts (`len(PARENT_ONLY_TYPES)` 6→8,
     `len(REGISTERED_HANDLER_TYPES)`, `len(BUILTIN_JOB_TYPES)` stays 48 since they were already in
     it). Manifest specs → parent-only (they currently pass `media_read` non-parent).
  2. Routes (`api/routes/audio_subs.py`, `subtitles.py`): select candidate media files
     (`_stale_or_missing_subtitle_scan_candidates` logic) and build a fixed batch of `subtitle_scan`
     children via `batches.create_fixed_batch(...)`; empty scope → parent `no_change`. Remove the
     legacy media-job fan-out. Return 202. Reuse/promote the shared `JobSubmissionResponse`.
  3. Deep-scan schedule (D-A): the `audio-subs-deep-scan` catalog entry produces a PARENT batch, but
     `submit_schedule_occurrence` only calls `submit_job`. Add a batch-producer variant (select
     candidates + `create_fixed_batch`) invoked by that schedule; add `"audio-subs-deep-scan"` to
     `ACTIVATED_SCHEDULE_KEYS` only after certification. Keep manual (`subtitle_scan_all:*`) vs
     scheduled idempotency separate.
  4. `subtitle_policy_audit` (new read-only type): add to `inventory.MEDIA_OPERATION_TYPES` or a new
     set + `BUILTIN_JOB_TYPES` (registry count 48→49 — update `test_final_manifest`
     `len(...)==48`, jmc4a a0 `definition_count`, jmc4b b0 count, and coverage). Typed request/result,
     dry-run evaluation of existing `SubtitleInventory`/policy only (no mutation plan/apply), bounded
     totals + full sanitized per-subject report as a downloadable artifact. Route → `submit_job`.
     `subtitle_policy` stays disabled/mutating (Chunk 5).

## Phase B3 completion — 2026-07-14

- Phase commits (configured author, linear): `281e931` (tracked media-tool launcher), `59c9cb4`
  (read-only `subtitle_scan`), `60c27e9` (scan parents), `da42f94` (canonical route batches),
  `dd4f924` (deep-scan schedule activation), and `901774c` (policy audit).
- `subtitle_scan` remains the only child handler: it receives immutable `ExecutionContext`, uses the
  JMC3 tracked `ffprobe`/`mkvmerge` launcher, emits indeterminate probing/inventorying stages, and
  only writes the derived subtitle inventory. `subtitle_scan_all` and `audio_subs_deep_scan` are
  JMC4A ticketless fixed-batch parents; their manual routes submit bounded canonical responses and
  never construct legacy media jobs. The configured hourly deep-scan schedule is product-active;
  its schedule occurrence/idempotency scope remains separate from manual submissions.
- Added and enabled `subtitle_policy_audit` on the `cpu` entrypoint. The enqueue request freezes the
  selected scope plus policy revision/content. The handler reads existing inventory only, uses JMC3
  cancellation/progress/evidence services, and returns bounded removals, protected tracks,
  review-required subjects, coverage before/after, warnings, missing inventory, and unavailable
  media. Overflows publish a confined, sanitized, byte-bounded JSON report artifact. It creates no
  mutation plan and does not apply a policy; `subtitle_policy` stays disabled/fail-closed.
- Manifest/inventory/handler/presenter freezes are updated for 49 definitions. Enabled definitions
  are `system_noop` (control), `library_sync` (network), `subtitle_scan` (media_read), and
  `subtitle_policy_audit` (cpu). There is still no enabled `media_write` product dispatch. Scheduled
  product keys are `library-sync` and `audio-subs-deep-scan`; global production occurrences remain
  disabled except for the explicit activation allowlist.
- Verification: focused B3/JMC4A producer, batch, schedule, worker, contract, route, read-only
  audit, coverage/missing/unavailable, and bounded-report tests: **63 passed**. `ruff check marquee
  tests`, `git diff --check`, Alembic drift check on reset schema, OpenAPI `api:check` (197 paths),
  generated TypeScript, frontend lint, build, and `svelte-check` (**0 errors; inherited 16
  warnings**) pass. Ordered retained pytest baseline on a guarded-reset disposable PostgreSQL
  database: **1086 passed / 21 failed / 2 warnings** in 61.34s; the 21 retained failures are the
  established dev-OCR fixtures, legacy/deferred pipeline/taste/run paths, hardware expectation,
  letterbox/sync/system-metrics fixtures, and are unrelated to this B3 diff.
- **Environment deviation/operator action:** port 55445 was occupied and the default PostgreSQL
  socket directory was inaccessible. The owned disposable cluster instead runs only for this
  session at `127.0.0.1:55446`, with its socket/data directory under `/tmp`; it was initialized by
  the guarded `marquee.dev_reset` for database `marquee_test`. Recreate an owned reset-backed test
  database before subsequent certification; do not use a bare `create_all` database.

## Current phase and exact next steps

- Phases B0–B3: **complete**. Phase B4 — letterbox detection families: **next**.
- Migrate only `letterbox_detect`, `letterbox_detect_episode`, `letterbox_detect_tv_scope`,
  `letterbox_detect_batch`, and `letterbox_detect_tv_batch` through the JMC3/JMC4A kernel. Preserve
  derived observations and evidence only: no crop metadata application, re-encode, heal, apply,
  destructive child, `media_write` dispatch, legacy lifecycle mutation, detached task, or direct
  subprocess path. Certify movie/show/season/episode scopes, shared files, nested parent progress,
  tool indeterminacy, cancellation/retry/evidence, and `LETTERBOX_AUTO_APPLY_HIGH` ignored under
  every configuration before enabling the family. Do not begin B5 until B4 is recorded here.

## Phase B4 completion — 2026-07-14

- Phase commit `c3a10c138e9569f9677327968ea204cbe4dfc0a2` (`migrate letterbox detection`),
  configured author. The three leaf definitions are enabled `media_read`/`read_only` handlers;
  `letterbox_detect_batch` and `letterbox_detect_tv_batch` are JMC4A sealed ticketless parents
  (the TV parent creates `letterbox_detect_tv_scope` children). No enabled definition has
  `media_write` dispatch.
- The canonical handlers receive immutable execution context and typed requests. Routes freeze all
  detector controls into a bounded request snapshot at enqueue; workers use the tracked launcher
  for ffprobe, FFmpeg cropdetect, and ImageMagick trim through the confined workspace. They emit
  indeterminate probe/sample/validation stages, use cancellation, request the definition retry
  budget for unavailable tools, and preserve no-change reasons. The direct result remains bounded;
  detailed sample evidence stays in the derived projection.
- Detection writes only derived `LetterboxState`/`LetterboxEvent` observations transactionally.
  Applied-crop fields and a pre-existing `tagged` status are preserved. The migrated paths no
  longer use legacy Job lifecycle mutation/emission, `cancel_registry`, `media_job_manager`,
  detached execution, or direct subprocess APIs. Static/runtime coverage proves
  `LETTERBOX_AUTO_APPLY_HIGH` is absent from the handler and no apply/re-encode/heal path or
  `media_write` ticket is reachable.
- Verification: focused canonical letterbox, manifest/contract/worker/backup certification tests:
  **58 passed**; existing letterbox API suite: **123 passed / 1 retained baseline failure**
  (`test_tv_dev_reset_all_deletes_episode_rows_and_previews_only`). `ruff check marquee tests`,
  `git diff --check`, Alembic drift check, OpenAPI export/generate/check (197 paths), frontend
  lint/build, and `svelte-check` (**0 errors; inherited 16 warnings**) pass. Retained full pytest:
  **1093 passed / 21 retained failures / 2 warnings** in 63.48s; the failure set is identical to
  B3's established dev-OCR, deferred legacy pipeline/taste/run, hardware, letterbox/sync, and
  system-metrics failures. Native smoke: ffprobe and FFmpeg 8.1.2; ImageMagick 7.1.2 `convert`
  resolves (with its upstream deprecation warning).
- Deviation/operator action: no product schedule changed. Before operator certification, ensure
  ffprobe, FFmpeg, and the binary configured by `LETTERBOX_CONVERT` are installed for every worker;
  the session's disposable PostgreSQL database remains `127.0.0.1:55446/marquee_test` and must be
  recreated with guarded `marquee.dev_reset` before later gates.

## Current phase and exact next steps

- Phases B0–B4: **complete**. Phase B5 — Dolby Vision analysis: **next**.
- Migrate only `dovi_analyze` and `dovi_analyze_batch` through the JMC3/JMC4A kernel. Freeze
  subject/file-signature/HDR intent, use the tracked ffprobe/dovi launcher, retain the last valid
  derived state on error, and prove no conversion, staged media, backup, replacement, or
  `media_write` path. Keep all other destructive HDR definitions disabled and do not begin JMC4C.

## Phase B5 completion — 2026-07-14

- Phase commit `430ecb7` (`migrate dovi analysis`), configured author. `dovi_analyze` is now an
  enabled `media_read`/`read_only` canonical handler and `dovi_analyze_batch` is a sealed JMC4A
  ticketless parent. The only enabled product definitions are `system_noop`, `library_sync`,
  `subtitle_scan`, `subtitle_policy_audit`, the five letterbox definitions, and `dovi_analyze`;
  there remains no enabled `media_write` product dispatch. No DOVI schedule is activated.
- The immutable request freezes canonical media-file subject, exactly one movie or episode owner,
  source signature, bounded source hints, and `standard` or `deep` analysis intent. The handler
  uses the tracked ffprobe launcher for all standard work and the tracked `dovi_tool` launcher only
  for an explicit deep request. It reports subject-aware probing/analysis/validation status with
  indeterminate progress, records bounded metadata/warnings/validation evidence, and persists only
  a derived, source-signature-bound `DoviState` projection. `0005_jmc4b` adds the required derived
  metadata and source-fence fields.
- Source/owner/signature and current-attempt fence are revalidated in the short persistence
  transaction. A missing, retired, changed, or already-current source produces a plain-language
  no-change outcome; an unavailable deep tool requests the definition retry budget; a failed probe
  or tool leaves the preceding valid state intact. Routes freeze snapshots at enqueue and return
  canonical 202 submissions; movie and television batches create only canonical `media_file`
  children. The migrated path has no conversion, source-media write, crop/application path,
  destructive child, backup/replacement, Job ORM lifecycle mutation, legacy manager emission,
  cancellation registry, detached task, or direct subprocess invocation. `dovi_convert` remains
  disabled and deferred.
- Verification: focused DOVI/route/manifest/presenter/worker/history certification gates passed
  (`54`, `44`, `33`, and `36` tests respectively); `ruff check marquee tests`, `git diff --check`,
  Alembic drift check on the guarded reset schema, OpenAPI export/generate/check (197 paths),
  frontend lint/build, and `svelte-check` (0 errors; inherited 16 warnings) pass. The retained
  full pytest gate on `127.0.0.1:55446/marquee_test` is **1100 passed / 21 retained failures /
  2 warnings** in 61.33s, with the byte-identical established dev-OCR, deferred legacy
  pipeline/taste/run, hardware, letterbox/sync, and system-metrics failures.
- Native/fixture smoke: ffprobe 8.1.2 is available and parser/fence/retry fixtures passed.
  `dovi_tool` is absent in this environment, so standard analysis is certified but no live deep
  fixture was run; install the configured tool on each worker and run a controlled deep Dolby
  Vision fixture before operator enablement. Continue to recreate the owned disposable database
  with guarded `marquee.dev_reset`; do not use a bare `create_all` database.

## Current phase and exact next steps

- Phases B0–B5 are **complete**. JMC4B is ready only for the §11 final compaction: verify the
  clean, linear, unpushed JMC4B-only range after `jmc4a-complete`; commit this final shared-timeline
  state; create and verify timestamped external recovery material and a Git bundle; certify the
  pre-squash tree; soft-reset to the exact JMC4B base; create the sole configured-author compact
  commit and annotated `jmc4b-complete` tag; then prove parent and tree identity. Do not begin
  JMC4C.

## JMC4B final pre-squash certification — 2026-07-14

- Exact plan base: `jmc4a-complete` =
  `490bc1d5d5a31f6d31397a8c3aed3c1b27c2c307`, tree
  `6407783847a37db38703083d3cc74e358d9f9d56`, sole parent
  `640ba6b2a0f94945ff9c9ca7e10791a9b6e285d2`. The entire post-base range is linear and has the
  configured author only. Its complete ordered commit record is: B0 `94919b9`, `9ba78afe`; B1
  `58cb3a8`, `8d13717`, `6b214ce`; B2 `ecc01db`, `73cc1c7`, `b8dcfb3`, `4b5dc9c`, `6e8d20c`;
  B3 `c40d7f7`, `281e931`, `712f7d8`, `59c9cb4`, `9f3136b`, `60c27e9`, `da42f94`, `dd4f924`,
  `901774c`, `0abb9aa`; B4 `c3a10c1`, `a87e4d3`; B5 `430ecb7`, `10b8b6e`; and this final
  certification entry.
- Certified verification: representative JMC4A producer/batch/schedule gates and focused
  DOVI/route/manifest/presenter/worker/history gates passed (B5 groups: 54, 44, 33, and 36 tests).
  `ruff check marquee tests`, staged/working `git diff --check`, guarded-reset Alembic drift,
  OpenAPI export/generate/check (197 paths), frontend API check/lint/build, and svelte-check
  (0 errors; inherited 16 warnings) pass. The retained full suite was run before this entry on the
  owned guarded-reset PostgreSQL database: **1100 passed / 21 retained failures / 2 warnings** in
  61.33s. The unchanged 21 are dev-OCR, deferred legacy pipeline/taste/run, hardware,
  letterbox/sync, and system-metrics failures; no skips, errors, or xfails were added.
- Enabled executable definitions are `system_noop`, `library_sync`, `subtitle_scan`,
  `subtitle_policy_audit`, `letterbox_detect`, `letterbox_detect_episode`,
  `letterbox_detect_tv_scope`, and `dovi_analyze`. Canonical parent-only definitions are
  `subtitle_scan_all`, `audio_subs_deep_scan`, `letterbox_detect_batch`,
  `letterbox_detect_tv_batch`, and `dovi_analyze_batch`; each uses JMC4A ticketless aggregation.
  Mutating/destructive product definitions, including `dovi_convert`, remain disabled/deferred;
  no enabled product dispatch uses `media_write`.
- Schedule state: only the canonical `library-sync` and `audio-subs-deep-scan` product schedules
  are activated through the explicit allowlist; the deep scan produces its canonical parent batch.
  DOVI, letterbox, and subtitle-policy-audit have no activated product schedule. Global production
  occurrences remain disabled outside that allowlist. No schedule or route creates a destructive
  child.
- Deviations/operator work: use a guarded-reset owned test database (this session:
  `postgresql+asyncpg://marquee@127.0.0.1:55446/marquee_test`) and recreate it rather than using
  bare `create_all`. Native ffprobe 8.1.2 (plus the B4 FFmpeg/ImageMagick tools) is available;
  `dovi_tool` is absent. Install the configured DOVI binary on every worker and perform a
  controlled deep-analysis fixture smoke before enabling that optional request depth in operation.
- **Pre-squash tip:** `HEAD` at this final pre-squash timeline commit (resolved immediately before
  recovery creation). It will be protected by timestamped local recovery branch/tag and a verified
  complete external Git bundle. The compact-tree resolver is the local annotated tag
  `jmc4b-complete`, created only after exact tree/parent verification.
