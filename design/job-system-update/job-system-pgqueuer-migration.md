# Marquee Job System — Clean-Slate PgQueuer Implementation Program

**Decided:** 2026-07-12
**Status:** Six-chunk implementation and verification program
**Target architecture:** [direct PgQueuer adoption](job-system-pgqueuer-direct-adoption.md)
**Product surface:** [Projection Room Activity redesign](projection-room-job-experience-redesign.md)
**Product research:** [activity comparison](projection-room-activity-comparison.md)
**Progress contract:** [job progress and loading experience](job-progress-and-loading-experience.md)
**Reset-window companion work:** [miscellaneous fixes](job-system-miscellaneous-reset-window-fixes.md)
**Chunk 1 implementation plan:** [JMC1 PgQueuer foundation](jmc1-pgqueuer-foundation.md)
**Chunk 2 implementation plans:** [JMC2A canonical model and configuration](jmc2a-canonical-model-and-configuration.md),
[JMC2B definition registry and policies](jmc2b-definition-registry-and-policies.md), and
[JMC2C presentation and API contracts](jmc2c-presentation-and-api-contracts.md)

## Program decision

Implement the direct-PgQueuer architecture in **six gated chunks** from a fresh PostgreSQL
database. Marquee has never been released, and the owner has explicitly approved destroying
all current development data. The current database and job history are not migration inputs.

This changes the program materially:

- build only the target canonical schema;
- replace the unreleased Alembic history with a clean PostgreSQL baseline once target models
  are stable;
- do not backfill `Job`/`MediaJob` relationships or preserve old IDs/history;
- do not ship compatibility views, legacy DTO adapters, dual writes, or backend routing;
- do not support mixed old/new binaries or drain the custom queue;
- do not preserve a rollback path to the old database schema;
- remove obsolete schema and runtime code as soon as the last handler moves.

The implementation chunks remain gated because media safety is independent of database
compatibility. Fencing, process death, cancellation, staged publishing, validation, logs,
artifacts, concurrency, connection budgets, and destructive media fixtures keep their full
test burden.

Intermediate branch states support only the handlers explicitly enabled at that chunk. They
are engineering checkpoints, not mixed-runtime production releases. A job is never visible
to two executors, and the API never executes a handler inline.

The companion miscellaneous-fixes program is part of these gates, not an optional follow-up.
Browser authentication/authorization, replacement of the existing database-reset endpoint,
Docker least-privilege hardening, and webhooks remain explicitly deferred. Deferred surfaces
are not certified or represented as completed job families by this program.

## Destructive reset boundary

> **Data-loss warning:** the initial cutover deletes the current Marquee PostgreSQL database
> contents, including library records, job history, settings stored in the database, events,
> attempts, metrics, and domain state. Recreate anything still useful from external systems
> or configuration after the reset. This exception applies only while Marquee is unreleased.

Provide an explicit development reset workflow that:

1. requires a deliberate destructive flag/confirmation;
2. stops API, worker, scheduler, and migration services;
3. drops/recreates the Marquee development database or owned schemas;
4. applies the clean Marquee Alembic baseline;
5. runs PgQueuer `pgq install`/`pgq upgrade` separately;
6. verifies both schema owners before services start;
7. initializes required seed/configuration records;
8. starts services and runs a no-op smoke.

The workflow must refuse an accidental invocation against an environment marked
production. It is not a public upgrade mechanism.

Before Marquee's first release, freeze a new migration baseline and restore the normal rule:
all subsequent schema changes are forward, data-preserving production migrations unless a
future release process explicitly says otherwise.

## Chunk map

| Chunk | Outcome | Enabled PgQueuer work |
|---|---|---|
| 1. Fresh foundation | Target database, PgQueuer schema, atomic enqueue, worker/scheduler | `system_noop` only |
| 2. Canonical model | Final product schema, definition registry, presenters, target APIs | Still `system_noop` |
| 3. Safety and evidence | Fences, process control, locks, staging, logs, artifacts, events | No-op plus selected read-only canaries |
| 4. Non-mutating work | Sync, scan, detect, analysis, ML, schedules, batches | All certified non-destructive types |
| 5. Destructive work | Poster/HDR/track/letterbox mutations and custom-runtime removal | All job types |
| 6. Activity and certification | Queue/History UI, detail, Operations, full system proof | PgQueuer only |

## Program-wide controls

### One target runtime

There is no `runtime_backend`, runtime adapter, custom fallback, dual-write bridge, or drain
mode in the target schema. Until a handler is migrated and certified, its command endpoint is
disabled in the implementation branch. Once enabled, it transactionally enqueues PgQueuer.

The old runtime code may remain unreachable in source until Chunk 5 makes deletion practical,
but it has no schema, process, routing assignment, or authority after the reset.

### Schema ownership

- Alembic owns only Marquee tables, indexes, constraints, and seed/reference data.
- PgQueuer owns its queue, schedule, completion, statistics, functions, indexes, and schema
  upgrades through `pgq install`/`pgq upgrade`.
- Never copy PgQueuer DDL into the Marquee baseline.
- Fresh schema/model equivalence is a release gate.
- A PgQueuer schema/version mismatch prevents API, worker, and scheduler startup.

### Required environments

- fast unit tests where PostgreSQL mechanics are not involved;
- disposable PostgreSQL with the real PgQueuer durable schema for integration tests;
- Docker Compose with separate migration, API, worker, and scheduler roles;
- representative MKV/MP4 and poster fixtures with ffmpeg/ffprobe/mkvtoolnix;
- NVIDIA hardware for final HDR/letterbox/ML destructive gates;
- production-like `DATA_DIR`, media mounts, database volume, and connection limits.

### Evidence per gate

Each chunk records:

- commands/tests and results;
- fresh schema/PgQueuer version verification;
- relevant failure-injection evidence;
- API latency and PostgreSQL connection samples;
- media/log/artifact outputs where applicable;
- known limitations and explicit go/no-go decision.

Rollback between chunks means reverting code and recreating an empty target database. It
does not mean converting target rows back into the discarded legacy schema. Target-database
backup/restore is still tested because it is required once the system is in use.

## Chunk 1 — Fresh target database and PgQueuer foundation

### Objective

Create the clean target database, install PgQueuer 1.1.1 in durable mode, and prove that one
canonical no-op job and its transport ticket commit or roll back together.

### Implementation

1. Pin `pgqueuer==1.1.1` in dependencies and images.
2. Add the explicit destructive development reset workflow and data-loss guard.
3. Establish the clean Marquee PostgreSQL baseline; it contains no obsolete custom queue,
   resource reservation, scheduler, recovery, or `MediaJob` lifecycle tables.
4. Extend the migration service to apply Alembic, install/upgrade PgQueuer, and verify its
   expected durable version/indexes before reporting healthy.
5. Apply PgQueuer's documented autovacuum/index guidance.
6. Add the narrow `PgQueuerGateway` for same-transaction enqueue through public
   `Queries.from_asyncpg_connection(...).enqueue()` on the SQLAlchemy transaction's
   documented raw asyncpg connection, plus official cancellation, bounded diagnostic
   lookup, and health/metrics. It must not copy queue SQL, create a replacement enqueue
   function, or add an outbox.
7. Queue only `{job_id, payload_version, dispatch_generation}`.
8. Register `system_noop` and start dedicated PgQueuer worker and scheduler processes.
9. Budget listener, producer, worker, scheduler, metrics, and migration connections.
10. Add transport health and consistency diagnostics without exposing raw tables publicly.
11. Split process liveness from dependency readiness. Readiness returns 503 unless
    PostgreSQL, the exact Marquee baseline, PgQueuer durable schema/version, event
    infrastructure, and mandatory configuration are compatible.
12. Fail API, worker, scheduler, and migration startup closed on incompatible schema instead
    of allowing ordinary requests to discover drift through 500 responses.

### Automated gate

#### Fresh creation and reset

- empty PostgreSQL creates exactly the target Marquee schema;
- repeated destructive reset produces the same schema and seed state;
- reset refuses a production-marked environment and requires explicit intent;
- current obsolete job tables are absent;
- SQLAlchemy metadata and Alembic head match;
- PgQueuer schema is present only after its own install step;
- repeated `pgq install/upgrade` is safe and startup fails on mismatch;
- application image contains exactly PgQueuer 1.1.1.

#### Transactional enqueue

- commit creates canonical job, dispatch audit, and one PgQueuer ticket;
- rollback at every boundary leaves neither product job nor ticket;
- returned numeric PgQueuer ID and dispatch generation link uniquely;
- idempotent duplicate commands return the existing canonical job;
- transport deduplication prevents duplicate dispatch generation;
- priority and eligible timestamp round-trip;
- payload contains no media plan or secrets.

#### Delivery and failure

- no-op executes and terminalizes once;
- API/worker restart before pickup;
- worker `SIGTERM`/`SIGKILL` while picked and stale redelivery;
- PostgreSQL restart before and after pickup;
- queued/picked cancellation;
- delayed retry persistence;
- final failure hold and diagnostics;
- schedule fires once under two scheduler replicas;
- listener failure triggers the configured shutdown/restart behavior;
- polling fallback executes when notifications are disrupted.

#### Isolation and load

- no queue manager runs inside Uvicorn;
- API remains responsive while workers restart repeatedly;
- connection counts remain inside budget;
- depth, age, completion, failure, and listener metrics are bounded/scrapeable;
- database roles have only required permissions.
- liveness remains available during a database outage while readiness returns 503;
- empty/wrong Marquee schemas, PgQueuer version mismatch, migration-in-progress state, and
  invalid mandatory configuration all fail readiness and service admission;
- readiness recovers without presenting a false healthy interval after dependencies recover.

### Manual smoke and exit

Use diagnostics to create, defer, cancel, retry, fail, and complete no-op work; restart every
service and PostgreSQL; verify canonical/transport identities and timing. Exit when the reset
is deterministic, same-transaction enqueue is proven, and no non-noop endpoint is enabled.

## Chunk 2 — Canonical product model and definition registry

Implementation is split into three sequential plans sharing one implementer timeline:
[JMC2A](jmc2a-canonical-model-and-configuration.md) →
[JMC2B](jmc2b-definition-registry-and-policies.md) →
[JMC2C](jmc2c-presentation-and-api-contracts.md). This split does not add a compatibility
stage or enable additional handlers; `system_noop` remains the only dispatch-enabled
definition throughout Chunk 2.

### Objective

Build the final Marquee product vocabulary directly, without compatibility columns, old ID
maps, backfills, or legacy serializers.

### Implementation

Create only the target structures:

- canonical `Job` with phase, outcome, desired state, fence, dispatch generation, PgQueuer
  link, payload/result versions, subject snapshot, progress, error/result, trigger,
  feature area, attention, retry lineage, parent/correlation, and timestamps;
- strict 1:1 typed domain-detail tables, including media detail keyed by canonical `job_id`;
- `JobDispatch`, audit-only `JobAttempt`, one semantic `JobEvent` stream, `JobLog`,
  `JobArtifact`, and small worker-node/capability records;
- no claim lease, custom heartbeat, resource reservation, scheduler cursor, recovery sweep,
  duplicate media lifecycle, or compatibility view.

Register every built-in handler, operation, and parent type in `JobDefinition`, including:

- payload/result/error versions and upcasters;
- feature area and presentation family;
- primary execution class and safety requirements;
- retry/timeout policy;
- subject snapshot builder and presenter;
- trigger provenance and allowed-action policy;
- mandatory progress policy: honest strategy, stages, subject context, units/denominator,
  nested aggregation, native-tool adapter, persistence cadence, and ETA capability.

Add the shared product contracts that the new process topology requires:

- versioned, transactional non-secret runtime configuration with optimistic concurrency and
  cross-process invalidation for API, workers, and scheduler;
- bounded execution-relevant configuration snapshots on canonical jobs, with explicit
  live/next-job/restart-scoped setting semantics;
- durable-history foreign-key rules so retiring a live Movie, Series, Season, Episode, or
  MediaFile projection cannot cascade-delete canonical jobs, attempts, outcomes, logs,
  artifacts, retry lineage, or subject snapshots;
- deterministic OpenAPI generation and generated/derived frontend wire types for command,
  job, progress, presentation, and media-target contracts.

Expose only the target bounded API/presentation contracts. Unmigrated command endpoints
remain disabled rather than writing legacy rows.

### Automated gate

- fresh Alembic head equals SQLAlchemy metadata;
- target uniqueness, foreign keys, hierarchy, dispatch generation, and 1:1 detail integrity;
- no legacy table/column/view exists;
- every registered handler/operation/parent has exactly one definition and presenter;
- every long-running definition has a typed progress policy; immediate definitions
  explicitly declare `none` and cannot emit fabricated percentages;
- invalid progress units/sequences/fences are rejected and subject snapshots cover movie,
  series, season, episode, file, track, poster, model, and batch contexts;
- payload/result versions and supported upcasters are enforced;
- clients cannot choose execution/safety policy;
- unsafe mutation definitions default to one attempt unless explicitly justified;
- subject snapshots render after live subjects are renamed/deleted;
- corrupt optional data becomes a presentation warning, not a 500;
- idempotency and parent/child sealing are deterministic;
- fresh canonical list/snapshot/presentation/diagnostic API contracts pass;
- presentation/log/raw fixtures contain no secret;
- concurrent configuration updates conflict rather than overwrite; every process observes
  the same validated version and every job exposes the bounded version/snapshot it used;
- corrupt configuration leaves the last valid version active and raises an Operations alert;
- deleting or retiring each live subject kind preserves readable History, presentations,
  retry lineage, logs, and artifacts;
- generated OpenAPI/client types are deterministic and contract tests cover every frontend
  API function, default/omitted request body, and error envelope.

### Manual smoke and exit

Render target presentations for representative movie, episode, batch, track mutation,
poster, HDR, letterbox, ML, sync, and maintenance fixtures. Exit when the registry covers all
built-in work and the final product schema needs no identity bridge.

## Chunk 3 — Execution safety, logs, artifacts, and events

### Objective

Make delivery safe enough for real media handlers and durable enough to explain every
attempt before broad migration. Canary only no-op and selected read-only definitions.

### Implementation

1. Atomically validate job/dispatch state, advance the fence, and create the audit attempt.
2. Separate PgQueuer transport attempts from admitted Marquee execution attempts.
3. Add isolated process group/cgroup execution and durable process identity.
4. Route child tools through one tracked launcher with cooperative, TERM, and KILL
   cancellation.
5. Acquire ordered advisory file, GPU, media-write, and maintenance safety gates before an
   execution attempt is admitted.
6. Add attempt-scoped staging, coordinator-only publish, and stale-fence rejection.
7. Add startup/orphan cleanup and unsafe/quarantine outcomes.
8. Capture Python and subprocess logs as redacted capped JSONL, gzip on completion, and
   expose cursor tail/stream/download.
9. Register confined physical and virtual artifacts, including bounded failure diagnostics.
10. Add one durable semantic event cursor and multiplexed SSE broadcaster with replay.
11. Add the fenced semantic-progress writer, server-side percentage validation, separate
    overall/current scopes, high-frequency coalescing, and maximum snapshot staleness.
12. Add native FFmpeg and mkvmerge progress adapters plus named indeterminate adapters for
    opaque probes, providers, model loading, and validation.
13. Add one filesystem-boundary service for media, application data, artifacts, cache,
    staging, backup, and temporary roots. All path classification uses resolved
    `Path.is_relative_to` checks; public contracts store confined keys, not arbitrary paths.
14. Remove raw-path deletion fallbacks and route-level filesystem mutation. Revalidate paths
    immediately before destructive use and fail closed when required roots are unresolved.
15. Make database plus `DATA_DIR` backup a coordinated maintenance operation with sealed
    writers/checkpoints, an exclusive consistency barrier, checksummed manifest, and a
    tested offline restore contract.
16. Enforce request-size limits by streaming byte accounting at public ingress and the
    FastAPI receive boundary instead of trusting `Content-Length` or buffering an unbounded
    body in the frontend proxy.

### Automated gate

#### Fences and duplicate delivery

- simultaneous/serial duplicate delivery admits at most one effect;
- stale fence cannot progress, finalize, or publish;
- stale/out-of-order progress sequence cannot replace the current snapshot;
- overall progress cannot regress and current progress can reset only under a new
  `scope_id`;
- redelivery after committed success no-ops;
- pause/cancel race before admission creates no execution attempt;
- transport retries do not incorrectly consume domain-attempt budget.

#### Processes and cancellation

- worker/handler/child `SIGKILL` at launch, progress, validation, and finish;
- orphan cleanup uses durable process identity and cannot kill an unrelated reused PID;
- cooperative cancellation then TERM/KILL escalation with death confirmation;
- cancellation cannot report terminal until the owned process tree is dead;
- API and event loop remain responsive during blocking tools.

#### Locks and publishing

- same-file exclusion across every mutating definition;
- media-read/write and shared/exclusive maintenance rules;
- ordered multi-lock acquisition has no circular wait;
- lock wait remains friendly Queue state rather than an admitted attempt;
- crash at each stage/validate/fsync/rename/commit boundary never publishes duplicate or
  unvalidated output;
- stale staged output is cleaned or quarantined deterministically.

#### Logs, artifacts, and events

- concurrent Python/stdout/stderr cursors remain readable;
- configured secrets and sensitive arguments are redacted;
- 100 MB cap writes one visible truncation record;
- active tail and terminal compressed download work after restart;
- arbitrary paths/traversal are rejected;
- sibling-prefix escapes, symlinks, poisoned stored paths, archive traversal, path swaps,
  missing roots, arbitrary serving, and raw fallback deletion are rejected;
- metadata/file retention is idempotent;
- SSE `Last-Event-ID` replay, missed-notify repair, bounded slow clients, and authorization;
- active snapshot reconciliation stays bounded.
- progress-write failure preserves media safety and leaves an observable last-good
  snapshot; high-frequency tool output stays within write/event budgets;
- backup manifests bind the database snapshot, file set, checksums, schema revisions,
  PgQueuer mode/version, and configuration version; restore into a fresh target preserves
  history/log/artifact/model linkage;
- omitted, chunked, mismatched, slow, and over-limit request bodies stop at the configured
  boundary and cleanly return 413 without exhausting frontend or backend memory.

### Manual smoke and exit

Run a read-only canary with a child process, inspect live Queue progress/logs, cancel it, kill
its worker, replay events, and download final evidence. Exit when process death, stale fences,
locks, staging, logs, artifacts, and events are proven.

## Chunk 4 — Non-mutating jobs, schedules, and batches

### Objective

Move all read-only and non-destructive work to PgQueuer and prove normal scheduling,
fairness, batching, and saturation before media mutation.

### Migration groups

- Radarr/Sonarr synchronization and library/media scans;
- subtitle/audio inventory and policy audits;
- letterbox/HDR/Dolby Vision detection and analysis only;
- poster candidate fetch, feature analysis, ranking, and review preparation without deploy;
- taste-map/profile/head analysis/training that publishes only versioned model artifacts;
- metrics, cache inspection, retention dry-runs, health, and other non-destructive
  maintenance;
- fixed and dynamic aggregate parents.

PgQueuer schedule callbacks enqueue ordinary canonical jobs. Fixed batches create parent,
children, and tickets atomically. Dynamic child sets seal before terminal aggregation.

### Per-group gate

For every definition:

- success, no-change/not-required, permanent failure, transient retry, cancellation, timeout,
  duplicate delivery, and stale fence;
- typed payload/result and presenter golden;
- immutable subject snapshot and trigger provenance;
- correct execution class, retry policy, attention, and allowed actions;
- declared progress strategy, plain-language stages, durable current-subject hierarchy,
  and correct determinate/indeterminate behavior;
- stable batch overall scope while the current child/stage changes, including series,
  season, episode, movie, file, and model subjects as applicable;
- logs/artifacts/events linked to the right attempt;
- all filesystem access passes through the confined boundary and execution observes the
  canonical job's configuration snapshot;
- no legacy writer or executor is invoked.

### Schedule, batch, and load gate

- schedule overlap, multi-replica uniqueness, misfire, disable/re-enable, and clock handling;
- fixed-batch atomicity and dynamic sealing;
- parent counts/progress/outcome under retry, skip, failure, and cancellation;
- sealed parent totals, monotonic overall progress, bounded concurrent-subject projection,
  and no reset when a child or analysis stage changes;
- failed-child and paginated-child queries remain bounded;
- cancellation cascades without cancelling unrelated correlation work;
- CPU, GPU-analysis, media-read, network, and maintenance queue fairness;
- API p95/event lag remain in budget under saturated workers;
- PgQueuer/advisory locks do not exhaust the PostgreSQL connection budget.

### Exit criteria

All enabled non-mutating endpoints create PgQueuer-backed canonical jobs only. Schedules and
batches use the final model, and saturation does not starve control/API paths.

## Chunk 5 — Destructive media jobs and custom-runtime removal

### Objective

Migrate every operation that changes media, artwork, backups, or deployed state; prove
atomicity and validation; then delete the unreachable custom runtime and obsolete schema
history.

### Migration groups

- poster deployment, reset, restoration, and healing writes;
- audio/subtitle remove, reorder, metadata, embed, extract, generation, policy application,
  restoration, and sidecar changes;
- letterbox tag apply/remove/revert and permanent re-encode for movie/TV;
- Dolby Vision/HDR conversion and preservation workflows;
- media and configuration backup/restore plus destructive maintenance.

Each mutating result records requested targets, before/expected/actual snapshots,
per-target outcome/stage/reason, validation, atomicity, backup, publish result, and bounded
failure evidence. A post-operation rescan supplies actual state.

Each migration group also implements its definition's progress policy: native FFmpeg
processed-time progress for reliable-duration transforms, `mkvmerge --gui-mode` for MKV
remuxes, item/track/sample counts where the scope is sealed, and named indeterminate stages
where tools expose no defensible denominator. No handler leaves percentage interpretation
to a feature page.

Every destructive handler also uses the centralized filesystem boundary and its job's
configuration snapshot. No route or handler directly serves, copies, replaces, archives,
extracts, or deletes an unclassified path.

After the final handler passes its gate:

- remove custom worker, scheduler, bridge, claim/recovery/resource code and startup hooks;
- remove `MediaJob` lifecycle/event/cancellation/progress authority;
- remove obsolete custom-runtime migrations from the unreleased baseline;
- prove no process, model, route, or task can write/execute through the old lifecycle.

### Media-fixture gate

Use small representative MKV/MP4 fixtures covering:

- multiple audio/subtitle codecs, languages, dispositions, sidecars, and duplicate metadata;
- single/multi-track success and target-specific failure;
- all-or-nothing remux failure with every target `not_applied`;
- partial external-sidecar failure accurately separated from embedded atomic work;
- stale plans, input signature changes, hardlinks, permissions, full disk, and read-only
  targets;
- backup creation/restore and post-operation ffprobe/mkvmerge rescan;
- repeated delivery without duplicate mutation;
- poster deployment/reset with original preservation;
- letterbox tag/re-encode/revert with dimension/crop validation;
- Dolby Vision profile/RPU/HDR/color metadata preservation;
- CPU/GPU selection and supported fallback behavior.
- monotonic overall versus resettable current scope across multi-file mutations;
- FFmpeg/mkvmerge native progress, safe fallback for invalid duration/tool output, and
  correct post-tool rescan/validation stages;

### Crash and cancellation matrix

Inject worker/child/API/database failure and cancellation:

- before tool launch;
- while reading/encoding/remuxing;
- after candidate output but before validation;
- during validation;
- before and after fsync/atomic replace;
- after publish but before canonical success;
- after canonical success but before PgQueuer acknowledgment.

Required proofs: no duplicate destination publish, no conflicting file mutation, no stale
publish, no leaked process, no false cancelled/succeeded outcome, and no loss of linked logs,
artifacts, or target results.

### Runtime-removal gate

- fresh schema contains no custom queue/resource/schedule/recovery or legacy media lifecycle
  table/view;
- no custom worker/scheduler/bridge process or startup hook exists;
- every command path maps to exactly one `JobDefinition` and PgQueuer entrypoint;
- source/static searches find no legacy enqueue/claim/recovery writer;
- OpenAPI/source searches find no `/api/test/pipeline` route, route module, inline pipeline
  executor, or production dependency on test-only helpers;
- unmigrated/unknown job types fail closed;
- clean build, reset, startup, and full backend test suite pass without compatibility code.

### Exit criteria

All product work uses PgQueuer delivery plus Marquee safety/evidence. Real media fixtures pass
validation and the custom runtime has no executable or persistent remains.

## Chunk 6 — Projection Room Activity and final certification

### Objective

Build the final Queue/History Activity experience and certify the complete target system.

### Implementation

- Queue for running/cancelling/queued/waiting/held/paused/deferred/retrying;
- History for succeeded/partial/no-change/failed/cancelled/superseded;
- grouped batch parents and bounded child drill-down, with no Batches primary tab;
- attention strip/sidebar severity, deterministic ordering, URL search/filters/sort, and
  persisted column/density preferences;
- server-authorized row/bulk cancel, pause/resume, class-scoped priority, retry, log,
  artifact, and detail actions;
- subject-first rows, trigger provenance, wait/remediation text, optional approximate
  class-local rank, media impact, and retry lineage;
- job detail Overview, Timeline, Logs, Artifacts, Raw Data, and collapsed Execution;
- direct active/terminal report access and bounded retained failure evidence;
- separate lazy Operations view;
- complete typed presenters for posters, HDR, audio/subtitles, letterbox, and supporting work.
- replace every page-specific job tracker/stage map/loading bar with one shared
  `JobProgressStore` and `JobProgressCard` family used inline and in Activity;
- rediscover active jobs from bounded Queue filters on load, reconcile multiplexed SSE with
  compact snapshots, and retain last-good cards through reconnect/stale periods;
- render distinct overall/current scopes, determinate/indeterminate modes, complete current
  subject hierarchy, credible metrics, and bounded concurrent children.
- replace handwritten critical wire contracts with the generated OpenAPI client/types and
  add runtime validation at destructive or success-reporting boundaries;
- correct stale page-state ownership so navigation/subject changes cannot retain the prior
  movie, show, job, or filter, and finish with zero `svelte-check` warnings;
- triage the recorded legacy suite only after the target architecture lands: fix product
  defects, replace still-required behavior with canonical/PgQueuer tests, remove obsolete
  custom-runtime expectations, and record a removed/replaced/fixed disposition for every
  former failure;
- exclude deferred webhook route tests from the target baseline. Webhooks remain unsupported
  by this program; their production implementation is neither migrated nor certified.

### Presenter and Activity gate

- every built-in definition has Queue/History row and detail goldens;
- each lifecycle state appears in exactly one view;
- stable ordering/pagination under equal timestamps/priorities;
- URL filters and browser navigation; local column/density persistence;
- mandatory subject/action/status cannot be hidden;
- attention counts/severity and event reconciliation;
- no-change/not-required distinct from success and skip;
- batch expansion never loads an unbounded graph;
- rank labelled approximate and absent when meaningless;
- allowed-action and stale-command conflicts;
- bulk partial result per job;
- operator retry original/replacement lineage;
- direct log/artifact affordances select the correct attempt;
- movie/episode/show/batch and missing/deleted subject labels;
- accessibility, keyboard/screen-reader behavior, mobile/narrow layouts;
- large logs, malformed raw documents, reconnects, hidden tabs, and virtualization.
- refresh with local storage empty and navigation between feature pages/Activity restores
  the same active progress snapshot;
- EventSource interruption never clears or falsely fails a job; duplicate/late events,
  snapshot repair, API restart, PostgreSQL restart, and hidden-tab throttling reconcile;
- overall progress is monotonic, current progress resets only with a new scope, and failed or
  cancelled work preserves its last measured value;
- determinate, indeterminate, hybrid, and immediate jobs render without invented
  percentages/ETAs or raw stage/status codes;
- letterbox TV, poster batch, and Dolby Vision batch regressions verify stable parent
  progress plus movie/show/season/episode/current-stage context;
- all four primary feature families and supporting work have compact/expanded progress
  golden fixtures;
- every retained backend, PgQueuer integration, media, frontend, and contract test passes
  with zero failures; no blanket `xfail`, quarantine, ignored job, or warning suppression is
  accepted as the green baseline;
- generated client use fixes known request drift such as the subtitle library scan body, and
  subject/navigation fixtures prove stale page state is not reused;
- `ruff`, frontend formatting/lint/tests/build, and `svelte-check` all pass, with
  `svelte-check` reporting zero warnings.

### API and performance gate

- list/snapshot/presentation/attempt/event/child/log/artifact endpoints enforce cursors and
  maximums;
- search/filter/sort works beyond the first page;
- list query count is bounded and independent of attempt/event/child history;
- presenter query count is bounded by definition, not target count;
- multiplexed SSE replay and slow-client behavior;
- active discovery/snapshot queries remain bounded under many inline feature-page and
  Activity consumers; progress coalescing stays within database/event budgets;
- hidden Operations/raw/log panels stop polling and abort superseded requests;
- API p95, event lag, database connections, and log/storage rates stay in target budgets
  under saturated workers and multiple Activity clients.

## Final production-like certification

### Workload matrix

Enqueue every definition against representative:

- movie, series, season, episode, media file, batch, model, integration, schedule, backup,
  maintenance, and system subjects;
- AI poster analysis/select/deploy/reset;
- HDR/Dolby Vision analyze/convert/preserve;
- audio/subtitle scan/generate/extract/embed/remove/reorder/metadata/restore;
- letterbox detect/tag/re-encode/revert;
- success, partial, no-change/not-required, failure, retry, cancellation, and supersession.

### Saturation matrix

Simultaneously saturate CPU, GPU, media-read, media-write, network, PostgreSQL,
scheduler/listener, SSE clients, live log tails, artifact downloads, and History queries.
Verify fairness, API/control latency, event lag, bounded queries, pool limits, storage caps,
and Operations alerts.

### Fault matrix

Inject worker, child, API, scheduler, listener, and PostgreSQL failures at claim, admission,
start, progress, retry, cancellation, validation, publish, product commit, and transport
acknowledgment. Repeat with file-lock contention, full disk, unavailable media, stale plan,
GPU loss, and slow/disconnected clients.

### Required proofs

- no duplicate destination publish or sequential duplicate unsafe effect;
- no conflicting mutation of one media file;
- no leaked owned process after bounded cleanup;
- stale attempt cannot progress/finalize/publish;
- progress survives refresh/reconnect, remains monotonic at the overall scope, identifies the
  correct current subject/stage, and never reports an unsupported percentage or ETA;
- every terminal job has coherent outcome, attempts, events, logs, and artifacts;
- requested-versus-actual track/crop/HDR/poster results are accurate;
- cancellation does not finish before process death;
- no orphan domain detail or missing evidence linkage;
- no custom runtime schema/process/writer exists;
- target database plus `DATA_DIR` backup/restore preserves readable Activity/history;
- PgQueuer schema upgrade rehearsal with queued/picked/deferred/retrying/held tickets passes;
- code rollback by empty target reset is documented and works.

### Required live media smoke

- GPU letterbox re-encode with post-operation dimension/HDR validation;
- Dolby Vision conversion with RPU/profile/color validation;
- multi-track audio/subtitle mutation with per-target rescan outcomes;
- poster select/deploy/reset with backup and visual artifact;
- cancellation during a long-running tool, reload Activity, and confirm process death;
- reload the initiating feature page during every long-running live-media smoke and confirm
  the same server-backed card, subject, overall/current progress, and logs reappear;
- restart services/PostgreSQL and confirm final Queue/History/log/artifact reconciliation.

## Post-green GitHub Actions modernization

Rewrite `.github/workflows/ci.yml` only after the complete local target suite is green. This
sequencing prevents the workflow from encoding obsolete custom-runtime assumptions while the
architecture is still moving. The replacement workflow must reproduce, not redefine, the
local completion baseline:

- provision a health-checked PostgreSQL service with isolated credentials;
- install the fresh Marquee baseline and pinned PgQueuer durable schema;
- run schema-equivalence and reset/upgrade rehearsals;
- run backend lint and the complete retained backend/integration suite on the project's
  supported Python versions;
- use `npm ci` and run frontend formatting/lint, `svelte-check`, tests, and production build
  on the pinned Node version;
- regenerate OpenAPI and frontend client/types and fail on an uncommitted diff;
- run bounded path/secret/security checks and avoid certifying deferred routes;
- retain least-required workflow permissions, PR/branch concurrency cancellation, and useful
  failure reports that contain no secrets or media.

Docker image/runtime hardening remains deferred to the separate Docker phase. Program
completion requires both the local green baseline and the modernized workflow passing on the
merge target.

## Explicitly removed migration work

The following is intentionally absent, not deferred:

- `Job`/`MediaJob` relationship or subject backfills;
- missing/ambiguous legacy-link reports;
- old job/media ID resolution;
- legacy API/DTO/SSE compatibility adapters;
- existing history retention;
- mixed-version deployment;
- expand/migrate/contract staging for the discarded job schema;
- dual-backend assignments, dual writes, routing bridges, or drain sequence;
- custom-runtime rollback;
- one-release delay before dropping obsolete job tables;
- downgrade from the target schema to the old schema.

References to these items in historical comparison/redesign documents describe superseded
analysis and are not implementation requirements.

## Explicitly deferred companion work

These are known follow-up surfaces, not part of the six-chunk completion claim:

- browser authentication, authorization, sessions, and CSRF;
- replacement/removal of `POST /api/system/reset-db` and its complete destructive reset
  workflow;
- Docker least-privilege/runtime hardening;
- Radarr, Sonarr, and Subgen webhooks and their route-level tests.

The [miscellaneous fixes document](job-system-miscellaneous-reset-window-fixes.md) records the
boundary and first-release implications in detail.

## Retained operational defaults

- PgQueuer 1.1.1 durable mode, installed/upgraded by its own CLI;
- per-attempt logs retained 30 days with a 100 MB cap and explicit truncation;
- canonical job/event/artifact metadata retention aligned with the configured policy;
- target PostgreSQL plus shared `DATA_DIR` backup/restore;
- physical media backups/staging governed separately from job-history retention;
- all public product IDs are Marquee canonical string IDs; numeric PgQueuer IDs remain
  diagnostics only.

## Completion criteria

The program is complete when:

- a fresh reset creates only the target Marquee and PgQueuer schemas;
- every built-in command uses exactly one PgQueuer-backed definition;
- no custom claim/recovery/schedule/resource/media lifecycle remains;
- all six chunk gates and final workload/saturation/fault/live-media matrices pass;
- Projection Room provides bounded Queue/History Activity and job-specific evidence for all
  four primary feature areas plus supporting work;
- every long-running job has an honest typed progress policy, and feature pages/Activity
  share durable discovery, reconciliation, and presentation;
- backup/restore and PgQueuer upgrade rehearsals pass;
- the accepted reset-window filesystem, configuration, history, backup, readiness, request
  limit, API-contract, and frontend-state gates pass;
- the retained backend, PgQueuer integration, media, frontend, and contract suites are green
  locally with zero failures, and each former failing test has a disposition;
- GitHub Actions is modernized only after that local baseline and reproduces it successfully;
- source, tests, design documents, and ByteRover context consistently describe the
  clean-slate PgQueuer-only system.
