# Marquee Job System — Current-State Architecture Review

**Reviewed:** 2026-07-11  
**Status:** Source-verified architecture review; no implementation changes proposed in this document  
**Companion documents:** [runtime comparison](job-system-runtime-comparison.md),
[selected direct-PgQueuer architecture](job-system-pgqueuer-direct-adoption.md), and
[historical redesign](job-system-redesign.md)

## Executive finding

Marquee has one real scheduling/runtime platform and one legacy media-operation control
plane. The names make it look like two interchangeable job managers, but their intended
responsibilities differ:

- [`JobManager`](../marquee/core/jobs/manager.py) owns the generic PostgreSQL queue,
  attempts, resource admission, worker leases, recovery, cancellation, parent/child
  aggregation, and canonical event writes. Projection Room's HTTP SSE route independently
  polls and delivers those persisted events.
- [`MediaJobManager`](../marquee/core/media_jobs/manager.py) owns media-specific planning
  and result data, but also duplicates lifecycle state, cancellation, progress, events,
  batches, identity, and live fan-out.

That second set of responsibilities is no longer justified. It functionally behaves like
migration scaffolding that became a permanent bridge, although repository history was not
used to prove that original intent. Media-specific plans and results deserve a domain
extension table; a second job lifecycle does not.

The generic runtime has several strong ideas worth keeping: PostgreSQL row-lock claiming,
attempt records, named resource pools, per-file locks, append-only events, atomic generic
batches, separate worker processes, cooperative cancellation, and partial PID tracking for
selected subprocesses. However, the current implementation still has critical durability
holes. Most importantly:

1. A worker crash after claim commit but before `start()` can strand a claimed job forever.
2. Reservation lease expiry is written and renewed but never consulted.
3. Recovery can release resource and per-file locks without proving the old process is dead.
4. There is no fencing token to stop a stale attempt from continuing or committing.
5. Several non-trivial jobs run inline in the API or scheduler without admission or lease
   renewal.
6. The media bridge creates and updates two job records and two event streams through
   fallible multi-step transactions.

Projection Room is mostly a read/control surface over the central `jobs` platform, not a
third scheduler. Its host metrics are an add-on subsystem, and media-job pages still depend
on the legacy control plane. Its current polling shape can materially amplify database and
API load.

## Scope and evidence

This review traced:

- FastAPI startup and shutdown;
- embedded and dedicated worker/scheduler process boundaries;
- direct and media-specific enqueue paths;
- claim, admission, attempts, heartbeats, timeouts, retries, cancellation, and recovery;
- subprocess registration and cleanup;
- parent/child and media batch behavior;
- generic and media progress/event paths;
- Projection Room and other frontend consumers;
- host metrics, job metrics, and job detail queries;
- recent hardening plans and the recorded production incident behind them.

The current implementation uses PostgreSQL through async SQLAlchemy/asyncpg. Older
ByteRover context that describes SQLite or table creation at application startup is stale;
[`init_db()`](../marquee/database.py) now verifies connectivity while a dedicated migration
service applies Alembic migrations.

The findings are source-verified. The existing hardening timeline records automated test
runs, but its GPU/live-media cancellation smoke remains pending. This review did not claim
dynamic reproduction of every defect.

## Process and data architecture

```mermaid
flowchart LR
    UI[Frontend / Projection Room] -->|HTTP commands| API[FastAPI API process]
    UI -->|poll + SSE| API
    API -->|create Job / MediaJob| PG[(PostgreSQL)]
    API -->|optional local-dev supervision| SUP[WorkerSupervisor]
    SUP --> SCH[Scheduler child process]
    SUP --> W[Worker child process(es)]
    SCH -->|schedule rows + enqueue| PG
    W -->|claim, attempt, lease, events| PG
    W --> H[Registered handler]
    H --> P[ffmpeg / mkv / OCR / ML children]
    H -->|media bridge dual writes| PG
    API -->|0.5 s event polling per SSE client| PG
    API -->|host sampler and metrics queries| PG
```

### Runtime roles

| Role | Current responsibility | Process boundary |
|---|---|---|
| API | Validate requests, create jobs, expose control/read APIs, serve SSE, collect host metrics | Uvicorn process |
| Worker | Claim and execute jobs with configured concurrency | Dedicated service in Compose; supervised child in embedded mode |
| Scheduler | Reconcile recurring schedules and enqueue/run due work | Dedicated service in Compose; supervised child in embedded mode |
| Supervisor | Spawn, monitor, and restart embedded workers/scheduler; shut down process groups | Runs in API only when `JOB_EMBEDDED_WORKERS=true` |
| Subgen | Optional embedded subtitle-generation service | Separate supervised child |
| PostgreSQL | Queue state, attempts, resources, schedules, events, workers, metrics, domain records | Shared system of record |

Production Compose correctly separates `marquee-api`, `marquee-worker`, and
`marquee-scheduler` and disables embedded workers. Local defaults favor convenience by
spawning child processes from FastAPI. Heavy queued handlers therefore normally run outside
the API event loop. The exception is the `create_and_run()` path described below.

### Central job tables

[`marquee/models/job.py`](../marquee/models/job.py) defines:

- `Job`: type, JSON payload, lifecycle, priority, hierarchy/correlation, subject,
  idempotency, progress/result/error, resource request, retry counters, control flags, and
  timestamps;
- `JobAttempt`: attempt number, worker, state, heartbeat, child PIDs, error, and metrics;
- `JobEvent`: append-only stage/state/message/detail records;
- `JobResource` and `JobResourceReservation`: named capacities and attempt reservations;
- `JobSchedule`: recurring schedule cursor;
- `JobWorker`: worker liveness and a minimal capability document.

### Legacy media tables

[`marquee/models/media_job.py`](../marquee/models/media_job.py) defines:

- `MediaJob`: operation, media file, plan/request/result/error, lifecycle, stage, progress,
  idempotency, cancellation, and attempt count;
- `MediaJobEvent`: a second append-only progress stream;
- `MediaBatch`: a second batch lifecycle and progress summary.

The generic-to-media relationship is not a foreign key. A generic job stores
`payload["media_job_id"]`, so joins, integrity, retries, cancellation, and retention are
implemented manually.

## End-to-end lifecycle

### 1. Startup

FastAPI's [`lifespan()`](../marquee/main.py) initializes external clients, verifies the
database, and bootstraps resource capacities. In embedded mode it starts a supervisor that
spawns scheduler and worker Python processes in new sessions. A lightweight host metrics
sampler remains an asyncio task inside the API process.

Each worker also verifies the database, bootstraps capacities, calls recovery once, registers
a `JobWorker` row, and starts its own worker/recovery heartbeat loop. Every worker runs
recovery periodically; there is no elected recovery leader.

### 2. Generic enqueue

Most feature routes call `JobManager.create()`. Creation:

1. looks up an optional globally unique idempotency key;
2. ensures resource rows exist;
3. inserts a `Job` with `queued` or caller-selected status;
4. appends a creation event;
5. commits.

Poster pipelines, TV pipelines, taste work, many letterbox operations, HDR/DoVi jobs,
maintenance, sync, and webhook-triggered work use this path. Job type resolution is
server-side through the handler registry; clients cannot enqueue arbitrary callables.

`create_batch()` inserts a `waiting_external` parent and all children in one transaction.
This is the correct batch invariant: a worker cannot observe a partial batch.

### 3. Media plan and enqueue

Subtitle mutations, generation, restoration, and letterbox re-encoding generally call
`MediaJobManager.create_job()`:

1. create a `MediaJob` with plan/request data;
2. with the default `commit=True`, commit it immediately;
3. derive generic resource requirements;
4. create a separate generic `Job` whose payload contains the media ID;
5. commit again.

Planned mutations create both rows in `planned`. Confirmation revalidates expiry,
capability warnings, and the media file signature, then sets both rows to `queued` in one
route transaction. The pre-execution file-signature check is valuable and should survive
the redesign.

The default creation sequence is not atomic. A crash or exception after step 2 leaves an
orphan `MediaJob`; an idempotent retry returns the existing media row before repairing the
missing generic job.

### 4. Claim and resource admission

`DurableWorker.run()` keeps up to `JOB_WORKER_CONCURRENCY` tasks in one asyncio worker
process. It polls every `JOB_POLL_SECONDS` and calls `claim_next()`.

`claim_next()`:

1. selects up to 32 eligible jobs, ordered by priority and age, using
   `FOR UPDATE SKIP LOCKED`;
2. handles pre-execution pause/cancel flags;
3. creates a `claimed` attempt;
4. calls `_reserve()`;
5. marks the job claimed, appends an event, and commits.

If no candidate can reserve resources, the worker expands lookahead on later cycles up to
256 rows. Blocked jobs become `waiting_resource` and are reconsidered on every poll.

`_reserve()` serializes the maintenance fence, locks requested resource rows in sorted
order, verifies capacity, and inserts attempt reservations. Pools include:

- `gpu`;
- `media_read`;
- `media_write`;
- `transcode`;
- `network_external`;
- `maintenance_exclusive`;
- dynamic `media-file:{id}` locks.

The deterministic lock order and per-file exclusion are good design. Admission currently
holds every requested resource for the whole job; the reservation `stage` field is not used
for stage-specific acquisition.

### 5. Execution

The worker reloads the job/attempt, calls `start()`, resolves the handler, and launches:

- an attempt lease-renewal task;
- a cancellation watcher;
- the handler task under a configured maximum runtime.

Heavy ML work is often offloaded with `asyncio.to_thread()`. Media tools use async
subprocesses. A process-local cancellation registry gives cooperative code an event to poll.
Selected subprocesses register their PIDs against the attempt.

Media operations resolve to `_run_media()`, which marks `MediaJob` running, dispatches to
the legacy media handler, and mirrors progress into both job systems. Persisted progress can
update:

- `MediaJob.stage` and progress counters;
- `MediaJobEvent`;
- `Job.current_stage` and `Job.progress`;
- `JobEvent`.

The bridge was optimized to use one short transaction for both event rows, but the duplicate
records and lifecycle remain.

### 6. Completion, retries, and batches

`finish()` marks job and attempt succeeded, records runtime, releases reservations, appends
an event, updates the parent, and commits.

`fail()` refreshes the durable cancellation flag, then chooses cancelled, retry scheduled,
failed, or dead letter. Retry delay is exponential and capped at ten minutes. Retryability
is a hard-coded set of five job types, independent of the caller's `max_attempts` value.

Parent progress is recomputed by scanning child statuses on every child terminalization.
When all children are terminal, the parent receives a summary and terminal state. This is a
useful domain-neutral aggregation pattern.

Not every route uses atomic `create_batch()`. At least these construct visible batches over
multiple commits:

- movie letterbox apply;
- TV letterbox re-encode;
- subtitle policy apply.

A fast child can be claimed or complete before later children exist or before its parent ID
is attached. This can terminalize the parent early or leave it unaware of children.

### 7. Cancellation and timeout

Central cancellation sets `Job.cancel_requested` and moves active work toward
`cancelling`. Queued/planned work is terminalized immediately. Parent requests cascade to
children. Media cancellation is bridged best-effort to `MediaJob.cancel_requested`.

The worker polls every two seconds, sets its process-local event, bridges media cancellation,
and terminates PIDs registered on the attempt. Cooperative handlers observe the event at
their own safe points. Timeout handling signals cooperative cancellation, terminates tracked
children, cancels the handler task after a grace period, and finalizes using fresh sessions
with a last-resort database update.

These are meaningful improvements from the July 2026 hardening work. They do not provide
physical cancellation in every path. In particular, subtitle generation looks up the
process-local cancel event using the media ID rather than the generic job ID and does not
register its extraction ffmpeg process. It can continue after the generic job has been
cancelled and resources released.

### 8. Recovery

Each worker's `recover()` pass finds `claimed` or `running` attempts whose heartbeat is
older than the configured lease window. It marks attempts interrupted, releases resources,
retries selected job types, synchronizes media status, updates parents, and appends events.
A second pass force-finalizes cancellation requests older than the configured force window.
Stale worker rows are marked dead.

The model suggests leases, but the actual recovery predicate uses only attempt heartbeat.
`JobResourceReservation.lease_expires_at` is never read by admission or recovery.

### 9. Scheduling and supervision

The scheduler reconciles rows for heal, deep scan, backup, retention, and metrics cleanup.
Due rows are locked, then either enqueued or executed through the inline path for handlers
marked `instant`.

The embedded supervisor starts scheduler/workers in separate sessions, restarts a crashed
child with backoff, escalates shutdown from `SIGTERM` to `SIGKILL`, and performs an orphan
PID sweep during supervisor shutdown. It does not perform that sweep when a worker crashes
and is respawned. Dedicated workers have no external supervisor implementation in this repo.

## Why there appear to be two job managers

| Concern | Generic `JobManager` | Legacy `MediaJobManager` | Verdict |
|---|---|---|---|
| Scheduling and claim | Authoritative | Delegates by creating generic row | Keep generic only |
| Resource admission | Authoritative | Derives request for generic row | Keep policy, move to typed job definition |
| Attempts and leases | Authoritative | Unused `attempts` counter | Remove media copy |
| Plan/request details | Generic JSON payload | Rich media plan and file signature | Keep as 1:1 domain extension |
| Status | Authoritative for runtime | Independently written and reconciled | Unify |
| Cancellation | Generic flag plus worker watcher | Separate flag plus bridge | Unify |
| Progress snapshot | `Job.progress` | Stage and counters | Unify |
| Events | `JobEvent` | `MediaJobEvent` | Unify |
| Batch lifecycle | Generic parent/children | `MediaBatch` counters | Unify |
| Live fan-out | Process-local listener map | Process-local `JobStream` | Remove or replace with cross-process delivery |

The code shape suggests that media planning/mutation preceded or was migrated into the
generic durable runtime, but this review did not reconstruct commit history to prove that
sequence. It is accurate to say there are two job managers in code, but inaccurate to say
there are two equivalent worker systems. There is one worker system and two overlapping
control/state models.

The right boundary is:

- one canonical job identity and lifecycle;
- optional typed domain tables keyed 1:1 by that identity;
- Projection Room derived from canonical jobs/events;
- execution transport hidden behind the runtime boundary.

## Strengths to preserve

1. **PostgreSQL-native atomic claim.** `SKIP LOCKED` is an appropriate base for multiple
   workers and avoids a Redis/RabbitMQ dependency.
2. **Workload-aware resources.** GPU, media read/write, transcode, network, maintenance,
   and per-file locks are more relevant to Marquee than generic queue concurrency alone.
3. **Safe planning checks.** Planned media mutations expire and revalidate the file
   signature before destructive work.
4. **Attempt and event history.** Attempts and append-only events give the product a useful
   audit foundation.
5. **Separate production processes.** Compose keeps normally queued work out of Uvicorn.
6. **Server-controlled handler registry.** Persisted types resolve to reviewed code, not
   arbitrary serialized callables.
7. **Atomic generic batch primitive.** `create_batch()` establishes the correct visibility
   invariant and should become the only construction path.
8. **Conservative media retries.** Mutating operations default to one automatic attempt,
   reducing unsafe replay.
9. **Fresh-session finalization and database timeouts.** Recent hardening reduced the chance
   that an old/locked session wedges cancellation and terminalization.
10. **Progress coalescing.** Pipeline progress throttling and publish-only media ticks show
    awareness of event-write cost.
11. **Graceful degradation.** External integrations and telemetry often fail soft instead
    of breaking the whole API.
12. **Supervisor backoff and shutdown escalation.** These are useful operational behaviors
    even though crash cleanup remains incomplete.

## Weaknesses and risks

### Critical durability and correctness risks

#### 1. Claim-to-start crash hole

`claim_next()` commits a `claimed` attempt without `heartbeat_at`. `start()` fills the
heartbeat later. Recovery filters on `heartbeat_at < cutoff`; SQL `NULL < cutoff` is not
true. A crash between those transactions leaves:

- `Job.status = claimed`;
- an unfinished `JobAttempt` with `heartbeat_at = NULL`;
- unreleased reservations;
- no recovery path.

This is a permanent wedge, not a delayed retry.

#### 2. Reservation expiry is ornamental

Reservations receive `lease_expires_at`, and heartbeat renews it, but no code reads it.
Admission counts every reservation with `released_at IS NULL`, including expired rows. The
runtime therefore has a heartbeat timeout convention, not an enforced lease.

#### 3. No execution fencing

State transitions do not compare an attempt/fencing token. After recovery, a stale handler
can still finish, write files, emit progress, or overwrite the newer state. Releasing
resource and per-file reservations does not revoke filesystem authority. At-least-once
recovery without fencing can turn a worker pause into concurrent mutation.

#### 4. Logical recovery can outlive physical work

`recover()` does not terminate `child_pids`. The supervisor's orphan sweep runs on API
shutdown, not worker crash/respawn. A hard-killed worker can leave ffmpeg or another child
running while recovery releases `media_write`, `gpu`, `transcode`, and per-file locks.

`process_id` and `process_group_id` exist on attempts but are unused. Stored child PIDs have
no process-start identity or pidfd, so delayed cleanup also has PID-reuse risk. OCR process
tracking is partly process-local rather than durable.

#### 5. Inline execution bypasses the platform

`create_and_run()` records a running job/attempt and awaits the handler directly. It does
not reserve resources, renew a lease, start a cancellation watcher, or isolate execution.
API callers include:

- database/state backup;
- manual poster heal;
- pipeline cache clear;
- full deployed-poster reset;
- one-off letterbox apply/remove.

The poster reset walks the library and performs synchronous file deletion and ORM updates
inside the API process. `asyncio.to_thread()` variants still consume the API process's CPU,
I/O, thread pool, and request lifetime. Scheduler `instant` work has the same lease-renewal
gap even though it runs outside the API.

#### 6. Dual media lifecycle has transactional gaps

Creation, confirmation, cancellation, execution, retry, events, and retention all require
manual synchronization. The initial create is split across commits. There is no FK from
generic to media state. A manual retry creates another generic job pointing to the same
media row; repeated retries can replay sequential side effects, and media lookups may select
an old generic row.

#### 7. Non-atomic feature batches

Several routes ignore the existing atomic batch API. Parent completion is derived from the
children visible at that instant, so partial construction can yield false terminal state.

#### 8. Known state/result mismatches

- Poster pipeline execution can record a failed `PipelineRun` while the generic handler
  returns normally and the `Job` becomes succeeded.
- `audio_reorder` exists in media dispatch/resource policy but is not registered through the
  central bridge, so worker handler resolution fails.
- `MediaJob.attempts` is not maintained.
- Successful retry does not consistently clear prior error state.
- Retry configuration says some media reads/generation have three attempts, while the
  hard-coded retryable type set excludes them.
- Graceful worker shutdown marks work interrupted instead of requeueing retryable work;
  crash recovery behaves differently.

### Reliability and operability weaknesses

1. Every worker independently runs the global recovery sweep every heartbeat interval.
2. Resource-blocked jobs are re-locked and re-queried every poll with no wakeup or backoff.
3. Lookahead stops at 256; enough high-priority blocked jobs can hide runnable lower-priority
   work indefinitely.
4. `maintenance_exclusive` excludes active reservations, not active jobs. Jobs with empty
   resource requests can overlap maintenance.
5. Worker capabilities are recorded but not used for routing. The runtime assumes every
   worker can see every media path and run every required tool/GPU workload.
6. Capacities are global and overwritten from each process's settings at startup, which is
   not a sound heterogeneous-worker model.
7. Resources are held for entire jobs even when network, GPU, and disk phases are sequential,
   lowering utilization.
8. Unknown resource keys silently receive capacity one, hiding configuration mistakes.
9. Retry decisions are based on job type rather than transient/permanent/unsafe failure
   classification, and backoff has no jitter.
10. Payload version exists but handlers do not expose a disciplined version/upgrade path.
11. Fixed per-process SQLAlchemy pools can multiply across API, scheduler, and worker
    processes and exceed sensible PostgreSQL connection budgets.
12. There is no Prometheus/OpenTelemetry job instrumentation in Marquee itself.

### Projection Room integration

Projection Room is integrated with the generic job platform:

- live/queued/history lists use `/api/jobs`;
- detail uses the generic job, attempts, reservations, events, children, and linked media
  details;
- cancel, pause, priority, and retry control `JobManager`;
- worker and resource panels read central worker/reservation tables;
- by-type stats derive from generic terminal jobs.

It is not a separate execution manager. Two add-on areas remain:

- host telemetry uses `SystemMetricsSampler`/`SystemMetricsSample`, not job events;
- subtitle/media surfaces still use `/api/media-jobs` and the duplicate media lifecycle.

### Projection Room strengths

- durable event replay survives API/worker process separation;
- active jobs rehydrate after refresh and are also remembered locally;
- attempts, reservations, errors, results, and child context are exposed;
- queue priority, pause/resume, retry, and cancel are first-class controls;
- resource and worker panels make admission visible;
- job/host timelines can correlate work with CPU/GPU/RAM/disk/network samples;
- poll intervals include jitter, and transient failures keep stale UI rather than blanking it.

### Projection Room and observability weaknesses

#### Event delivery

Both generic and media SSE endpoints poll PostgreSQL every 0.5 seconds per connected client,
querying new events and the job row. Generic SSE supports `Last-Event-ID`; media SSE resets
its cursor to zero on every connection and therefore replays all persisted media events.
The media stream terminates with an empty `done` payload; `TrackTable` treats a missing
terminal status as success, so a failed media job can take the success follow-up path.

The process-local `JobManager.subscribe()` and `MediaJobManager.JobStream` facilities have
no HTTP subscribers and cannot cross worker/API process boundaries. Publish-only events sent
to them are effectively invisible to clients. Comments that describe live in-memory fan-out
are stale.

The event `state` field also mixes lifecycle values with progress actions such as `start` and
`progress`. Shared tracking code temporarily treats those action values as job statuses until
the next snapshot replaces them. SSE updates the compact progress state, but the detailed
attempt/event timeline is generally refreshed only at completion, so the page is neither a
pure event view nor a consistent snapshot view.

#### Read amplification

- The shared `trackJob()` performs a full `GET /jobs/{id}` every 1.5 seconds for each active
  job while also keeping SSE open.
- That endpoint is not a lightweight snapshot: it loads every attempt, reservation, event,
  linked media row, child, and child detail on each poll.
- Initial Projection Room rehydration fetches full detail for every active job, then
  `trackJob()` immediately fetches the same detail again.
- Projection Room separately refreshes active jobs, queued jobs, job metrics, current host
  metrics, and the selected host-history window every roughly four seconds, regardless of
  the active tab.
- There is no in-flight guard; a slow request can overlap the next interval.
- Host history loads every raw sample for the window and downsamples in Python. A 24-hour
  window at the default 15-second sampling interval is 5,760 JSON-heavy rows every refresh.
- Metrics-by-type issues one query per distinct type after a distinct-type query.
- Generic job detail events and children are unbounded.
- Media job listing repeats `_generic_for_media_job()` for every row. Each lookup loads all
  generic jobs of that operation and scans JSON payloads in Python: an N+1 multiplied by a
  full-type scan.
- History filters for multiple types, resource, compute class, and subject are applied in
  the browser after fetching only the first 100 rows, so results can be incomplete while
  still producing repeated requests.

#### Missing signals

Projection Room does not currently provide:

- durable per-attempt stdout/stderr logs;
- queue wait/admission latency;
- cancellation latency and escalation reason;
- lease expiry/recovery counters;
- retry reason taxonomy or next-retry explanation;
- worker version, capabilities, mounted-path/tool readiness, or per-worker resource use;
- resource wait reason and time;
- duplicate/stale attempt detection;
- event/log retention visibility;
- SLO-oriented rates and histograms;
- an explicit split between job outcome and attempt outcome;
- durable scheduler, supervisor, dispatcher, and recovery-loop health.

Host telemetry describes the API host/process environment. In a dedicated or multi-node
worker deployment it does not describe each worker node, and the API container may not even
have access to the GPU assigned to workers. That can make the GPU panel misleading.
Database pool telemetry is returned by the backend metrics response, but the current
frontend model and panels omit it; aggregate job counts are also fetched without being fully
rendered.

#### Current logging path

Application, worker, and scheduler logs go to their process/container stdout through the
normal logger. Worker and scheduler output is inherited rather than captured into a job
record, and structured entries do not automatically carry `job_id`, `attempt_id`, fence, or
correlation context. Media handlers are responsible for consuming subprocess pipes; recent
re-encode hardening drains ffmpeg stderr and can surface bounded diagnostics on failure, but
there is no common durable stdout/stderr stream or retention policy per attempt. Embedded
Subgen output is the notable supervised/captured exception, and even that becomes application
logging rather than a paginated job log.

## Why the frontend/API can slow down

The slowdown is structural, not only a pool-size problem:

1. Inline API jobs perform file/database/thread work in the web process and bypass resource
   controls.
2. Each active UI job generates a snapshot polling loop plus a server-side SSE database
   polling loop.
3. Snapshot polls load unbounded audit/detail collections rather than a compact projection.
4. Projection Room's page timer refreshes multiple independent aggregates and host-history
   queries even when their tab is hidden.
5. Media bridge progress writes two event rows and two snapshots.
6. Media list/detail lookup lacks an indexed relationship and performs JSON scans.
7. Progress writers, worker heartbeats, worker recovery, queue polling, metrics sampling,
   and UI reads all share PostgreSQL.
8. Workers can saturate disk, memory, CPU, or GPU on the same host even when process
   boundaries protect the event loop.
9. Each runtime process owns a pool sized up to 30 connections; scaling processes multiplies
   the possible database load.
10. Synchronous host metric collection runs in the API process both for the sampler and on
    every current-metrics request.

Increasing the pool can postpone symptoms but cannot correct the query and execution shape.

## Historical evidence and verification gaps

The [job platform hardening plan](plans/14-job-platform-hardening-backend.md) records a real
July 2026 incident: an ffmpeg stderr pipe stalled, timeout cleanup was cancelled during a
transaction, row locks blocked cancellation, recovery skipped locked attempts, and resource
reservations froze the single-worker platform for more than 13 hours. Subsequent work added
stderr draining, stall detection, fresh-session finalization, database lock/idle-transaction
timeouts, cancellation escalation, and frontend feedback.

Those changes are valuable. They are mitigations around the same fundamental risks: long
transactions, fallible logical/physical cancellation, two job records, and no fencing.
The timeline explicitly leaves live GPU re-encode/reload/cancel smoke pending.

Before trusting either the current runtime or a replacement, destructive tests must cover:

- crash after claim commit and before start;
- crash during every state transition and event write;
- `SIGKILL`/OOM of worker and child independently;
- stale worker continuing after recovery;
- PostgreSQL loss/restart and lock timeout;
- duplicate enqueue and repeated cancel/retry;
- partial batch construction;
- filesystem mutation at every crash boundary;
- API latency under saturated workers and many UI streams;
- schema/runtime version mismatch with queued and running work.

## Current-state conclusion

The central PostgreSQL runtime is not valueless custom code; it contains Marquee-specific
resource and media safety concepts that generic queues do not supply. The mistake is allowing
those concepts to coexist with duplicate lifecycle ownership and unenforced leases.

The redesign should preserve workload-specific admission, planning validation, process
isolation, attempts, and durable events. It should replace:

- dual job identities and event streams;
- unconditional/fallible state transitions;
- heartbeat-only pseudo-leases;
- inline API execution;
- polling-per-client observability;
- partial batch construction;
- process cleanup without fencing or process identity.

The companion redesign document turns those conclusions into a target architecture and
migration sequence.
