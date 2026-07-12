# Marquee Job System — External Runtime and Media-Scheduler Comparison

**Reviewed:** 2026-07-11  
**Status:** Primary-source architecture comparison  
**Companion documents:** [current-state review](job-system-current-state-review.md),
[selected direct-PgQueuer architecture](job-system-pgqueuer-direct-adoption.md), and
[historical redesign](job-system-redesign.md)

## Bottom line

No compared product or library solves all of Marquee's hard problems.

- Sonarr and Radarr are useful examples of download/import orchestration and of keeping a
  UI queue as a projection, but they are not models for durable GPU/media compute.
- Tdarr, FileFlows, and Unmanic are better product analogues. Their strongest lessons are
  separate nodes, explicit worker capabilities, weighted concurrency, path mapping, worker
  liveness, and per-job reports.
- Generic task queues can replace transport claim, delivery redelivery/delay, and schedule
  wakeup plumbing, but none supplies Marquee's canonical domain retry policy,
  multi-resource admission, per-file exclusion, filesystem fencing, or reliable
  media-process termination.
- PgQueuer is the leading transport/runtime candidate because it is async, PostgreSQL-native,
  has heartbeat-based stale-work recovery, database-wide concurrency, `LISTEN/NOTIFY`,
  retries, Prometheus support, and no new broker. Its 1.x line is only months old and must
  pass destructive testing before adoption.
- Procrastinate is the conservative PostgreSQL-native fallback, but stalled-job retry is an
  application-installed recipe and its external transaction integration does not directly
  reuse Marquee's async SQLAlchemy transaction.
- DBOS is the strongest major-rewrite challenger if Marquee deliberately wants durable,
  resumable stage workflows. Temporal is technically stronger at the distributed-workflow
  end, but disproportionate for Marquee today.

The recommended path is a staged hybrid: first create one canonical Marquee job model and a
safe execution/resource boundary, then put PgQueuer behind a runtime adapter if it passes a
fault-injection bake-off. Projection Room must read Marquee's canonical control-plane data,
never a library's internal queue tables.

## Compare the right categories

| Category | Primary concern | Examples | What it does not automatically provide |
|---|---|---|---|
| Download queue orchestration | Reconcile external client state, import completed downloads | Sonarr, Radarr | GPU/media compute scheduling and durable activity recovery |
| Generic task queue | Deliver a function/message with retries and concurrency | Celery, Dramatiq, ARQ, Procrastinate, PgQueuer | Product job model, weighted resources, safe file mutation |
| Media-processing scheduler | Route files to capable nodes and control expensive tools | Tdarr, FileFlows, Unmanic | General workflow durability/fencing guarantees |
| Durable execution runtime | Persist workflow history or completed steps and recover orchestration | DBOS, Temporal | Hardware/file admission without application policy |
| Observability/control plane | Explain, control, and audit work for users/operators | Projection Room, Temporal UI, Tdarr reports | Execution correctness by itself |

Marquee spans the last four categories. Replacing only the queue cannot replace its resource
broker, process supervisor, domain planning, or product control plane.

## Media-system comparison

### Sonarr and Radarr

Sonarr/Radarr are principally download/import orchestrators. The Activity download queue is
explicitly not stored in the application; it is reconstructed from download-client API
responses. That is a valuable reminder that a UI queue can be a projection rather than a
second scheduler. [Sonarr Activity documentation](https://wiki.servarr.com/sonarr/activity)

Their internal command subsystem is closer to Marquee's maintenance scheduler: commands are
persisted, mirrored in process memory, and executed by a small in-application executor.
Startup recovery marks prior running commands orphaned and reloads queued work. Running
command cancellation is limited compared with Marquee's media-process needs. Source links
are pinned to the releases current at review time.
[Sonarr 4.0.17 command queue manager](https://github.com/Sonarr/Sonarr/blob/v4.0.17.2952/src/NzbDrone.Core/Messaging/Commands/CommandQueueManager.cs),
[Radarr 6.1.1 command executor](https://github.com/Radarr/Radarr/blob/v6.1.1.10360/src/NzbDrone.Core/Messaging/Commands/CommandExecutor.cs),
[Radarr 6.1.1 command repository](https://github.com/Radarr/Radarr/blob/v6.1.1.10360/src/NzbDrone.Core/Messaging/Commands/CommandRepository.cs)

**Lesson:** copy the distinction between authoritative state and a UI projection. Do not copy
an in-process thread-pool executor for long GPU/ffmpeg work.

### Tdarr

Tdarr is a distributed media-compute scheduler with a central server and separate nodes.
Nodes expose CPU/GPU transcode and health-check workers; plugin flows can requeue by node
tags; the UI exposes queue/status tables and detailed job reports.
[Why Tdarr](https://docs.tdarr.io/docs/welcome/why/),
[node workers](https://docs.tdarr.io/docs/nodes/workers/),
[tag routing](https://docs.tdarr.io/docs/plugins/flow-plugins/index/tools/Tags-%20Requeue/),
[job reports](https://docs.tdarr.io/docs/other/job-reports/)

Its public product documentation does not establish the kind of lease fencing or durable
workflow guarantees Marquee needs, so it should be treated as a control-plane and worker
routing reference, not as proof of execution semantics.

**Lessons:** explicit worker classes, tags/capabilities, separate server/nodes, and bounded
per-job tool logs.

### FileFlows

FileFlows separates its server from processing nodes and can disable processing on the
server. Nodes have priority, schedules, library/file-size/codec/resolution restrictions,
path mappings, runner counts, and weighted costs for SD/720p/1080p/4K work.
[Node documentation](https://fileflows.com/docs/webconsole/nodes),
[path mapping](https://fileflows.com/docs/webconsole/nodes/mapping),
[connection modes](https://fileflows.com/docs/webconsole/nodes/connection-modes)

Weighted runner costs are the closest external precedent for Marquee's resource ledger.
Some multi-node, database, and weighted-runner capabilities depend on paid tiers; the
official pricing page should be checked before treating them as an open implementation
reference. [FileFlows pricing](https://fileflows.com/pricing)

**Lessons:** resource cost should depend on workload size, nodes need explicit path/tool
mapping, and server processing should be optional.

### Unmanic

Unmanic uses a pending queue, configurable worker groups/counts/tags/schedules, linked
installations, a completed-task dashboard, and structured lifecycle plugin events.
[Worker settings](https://docs.unmanic.app/docs/configuration/workers_settings/),
[linked installations](https://docs.unmanic.app/docs/configuration/linking/link_overview/),
[plugin lifecycle events](https://docs.unmanic.app/docs/development/writing_plugins/plugin_runner_types/)

Its linked-routing model is media/library-specific rather than a general lease-based durable
runtime.

**Lessons:** prefer one lifecycle-event vocabulary and keep capability routing simple enough
for operators to understand.

## Runtime/library comparison

### PgQueuer

PgQueuer is an async PostgreSQL queue using `FOR UPDATE SKIP LOCKED` and
`LISTEN/NOTIFY`. Its current documentation covers heartbeat-based recovery, database retry,
failed-job holds, completion logs, deduplication, schedules, best-effort cancellation,
database-wide per-entrypoint concurrency, Prometheus metrics, and tracing.

- [Reliability guide](https://janbjorge.github.io/pgqueuer/guides/reliability/)
- [Heartbeat guide](https://janbjorge.github.io/pgqueuer/guides/heartbeat/)
- [Cancellation guide](https://janbjorge.github.io/pgqueuer/guides/job-cancellation/)
- [Concurrency guide](https://janbjorge.github.io/pgqueuer/guides/concurrency-control/)
- [Performance and PostgreSQL tuning](https://janbjorge.github.io/pgqueuer/guides/performance-tuning/)
- [Releases](https://github.com/janbjorge/pgqueuer/releases)

Version 1.1.1 was released on 2026-07-07. The 1.0 line froze a strict-semver public API but
also represents a recent hard reset. Recent releases fixed stale-job recovery at concurrency
limits and widened queue IDs with a migration that can take an exclusive lock. That is a
reason to test upgrade and recovery paths, not to reject the project automatically.

**Good fit:** asyncpg, PostgreSQL-only operations, low infrastructure burden, queue wakeups,
standardized transport stale-delivery recovery/retry, and useful instrumentation.

**Missing:** weighted multi-resource acquisition, shared constraints across combinations of
entrypoints, worker/media-path capability routing, safe child-process escalation, and
filesystem fencing. Cancellation is explicitly best effort.

### Procrastinate

Procrastinate is a mature PostgreSQL queue with priorities, task/queue locks, retries,
schedules, events, worker heartbeats, cancellation/abort support, and administration tools.

- [Stalled-job recovery](https://procrastinate.readthedocs.io/en/stable/howto/production/retry_stalled_jobs.html)
- [Cancellation](https://procrastinate.readthedocs.io/en/main/howto/advanced/cancellation.html)
- [Monitoring](https://procrastinate.readthedocs.io/en/main/howto/production/monitoring.html)
- [External transactions](https://procrastinate.readthedocs.io/en/main/howto/production/external_connection.html)

On `SIGKILL`, a job can remain `doing` forever unless the application installs a periodic
task that finds stalled worker heartbeats and retries their jobs. That is documented and
configurable, but Marquee must make it mandatory. External transactional enqueue supports
psycopg and synchronous SQLAlchemy connection patterns; it does not directly share an
asyncpg SQLAlchemy transaction.

**Good fit:** established Postgres-only deployment, lower API churn than PgQueuer, queues and
locks.

**Missing:** the same media resource/process requirements as PgQueuer, plus built-in
automatic stalled-job retry and a rich operator UI.

### Celery

Celery is the most mature general task queue in this set. RabbitMQ/Redis delivery,
prefork workers, routing, rate limits, late acknowledgement, retries, child recycling,
memory limits, events, and Flower are substantial strengths.
[Task reliability settings](https://docs.celeryq.dev/en/stable/userguide/tasks.html),
[worker control](https://docs.celeryq.dev/en/stable/userguide/workers.html),
[monitoring](https://docs.celeryq.dev/en/stable/userguide/monitoring.html)

Desired at-least-once behavior depends on careful combinations of acknowledgements,
worker-lost behavior, broker visibility, and idempotency. Revoke reliably prevents queued
execution; terminating a running task is documented as a last-resort administrative process
kill rather than a product cancellation primitive.

**Verdict:** capable but adds broker/result/monitoring infrastructure and another split state
model while still requiring Marquee's resource, cancellation, and file-safety layers.

### Dramatiq

Dramatiq offers RabbitMQ/Redis persistence, retry/dead-letter middleware, worker processes
and threads, routing, and Prometheus metrics.
[Message persistence](https://dramatiq.io/advanced.html#message-persistence),
[interrupt behavior](https://dramatiq.io/advanced.html#message-interrupts),
[Prometheus metrics](https://dramatiq.io/advanced.html#prometheus-metrics)

Thread interrupts and time limits cannot stop system calls reliably, and there is no strong
arbitrary running-job cancellation primitive.

**Verdict:** simpler than Celery but leaves most of Marquee's hard control-plane work custom.

### ARQ

ARQ is a small Redis/asyncio queue with pessimistic execution, retry, cron, status/results,
worker health, and optional queued/running abort. Interrupted work can remain queued and run
again. Blocking media/ML still needs executors or subprocesses.
[Official repository](https://github.com/python-arq/arq)

The repository describes ARQ as maintenance-only.

**Verdict:** reject for a new central dependency.

### DBOS

DBOS stores workflows, queues, and execution progress in PostgreSQL. Workflows recover from
the last completed step; steps are at-least-once until complete; DBOS transactions commit
exactly once. It provides durable workflow IDs/idempotency, timeouts, cancellation at step
boundaries, optional async-step preemption, persisted queues, global/per-worker concurrency,
rate limits, priorities, deduplication, partitions, and workflow inspection.
[Workflow tutorial and guarantees](https://docs.dbos.dev/python/tutorials/workflow-tutorial),
[queue reference](https://docs.dbos.dev/python/reference/queues),
[workflow management](https://docs.dbos.dev/python/tutorials/workflow-management),
[separate worker service example](https://docs.dbos.dev/python/examples/queue-worker)

**Good fit:** poster/media pipelines can be expressed as durable stages, and PostgreSQL
remains the only infrastructure dependency.

**Missing/risk:** weighted resource vectors and media process supervision remain custom;
framework coupling and deterministic workflow/step boundaries make this a real rewrite.
Filesystem side effects still need idempotency and fencing.

### Temporal

Temporal persists workflow history and replays orchestration after failure. Activities retry
from the beginning unless they heartbeat checkpoint details; durable cancellation delivery to
activities also depends on heartbeats. It provides task queues, worker slot suppliers,
visibility, a strong Web UI, reset/terminate controls, and mature workflow semantics.
[Workflow execution](https://docs.temporal.io/workflow-execution),
[activities](https://docs.temporal.io/activities),
[Python cancellation](https://docs.temporal.io/develop/python/workflows/cancellation),
[worker tuning](https://docs.temporal.io/develop/worker-performance),
[self-hosting](https://docs.temporal.io/self-hosted-guide/deployment)

**Verdict:** technically strongest for multi-host durable orchestration, but operational and
conceptual cost is disproportionate today. It also does not remove the custom GPU/VRAM/disk/
file resource layer.

## Decision matrix

Ratings are relative to Marquee's workload, not general library quality.

| Candidate | Postgres-only | Crash recovery | Running cancel | Async fit | Observability | Media resource fit | Infra footprint | Custom engineering burden | Recommendation |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Current custom | Excellent | Weak/incomplete | Mixed | Good | Product-specific but costly | Strong concept, weak enforcement | Low | Very high | Refactor regardless |
| PgQueuer | Excellent | Strong transport candidate | Best effort | Excellent | Good | Low without custom broker | Low | Medium | Preferred hybrid after bake-off |
| Procrastinate | Excellent | Manual recipe | Cooperative | Good | Moderate | Low without custom broker | Low | Medium/high | Hybrid fallback |
| Celery | No | Mature/config-sensitive | Weak product semantics | Moderate | Strong ecosystem | Low | High | High | Do not choose |
| Dramatiq | No | Mature broker redelivery | Weak | Moderate | Moderate | Low | Medium | High | Do not choose |
| ARQ | No | Basic pessimistic replay | Async abort | Excellent | Basic | Low | Medium | High | Reject: maintenance-only |
| DBOS | Excellent | Strong stage recovery | Durable boundary/preemption | Good | Strong workflow view | Medium with custom broker | Low | High/rewrite | Major-rewrite challenger |
| Temporal | External service | Excellent | Strong with heartbeats | Good | Excellent | Medium with custom broker | Very high | High | Revisit later |

## Redesign options

### Option A — Keep the custom PostgreSQL runtime, refactor heavily

Retain Marquee's queue tables and replace the unsafe internals:

- one job lifecycle and event stream;
- atomic claim/admission with a non-null deadline;
- enforced reservation expiry;
- attempt fencing and compare-and-set transitions;
- isolated media/tool attempts, warm ML execution pools, and reliable group termination;
- `LISTEN/NOTIFY` wakeups plus polling fallback;
- typed retry/error policy;
- worker capabilities and weighted resources;
- one event tailer per API instance;
- no inline API execution.

**Advantages:** exact fit, no transport/control-plane duality, predictable migrations, and
full control over resource fairness.

**Disadvantages:** Marquee continues owning the hardest distributed-systems code. Recent
incidents show that correctness requires more than small patches. Observability and schema
maintenance remain internal burdens.

### Option B — Hybrid canonical control plane plus PostgreSQL queue runtime

Use a library for durable dispatch, delivery-heartbeat recovery/redelivery, delayed tickets,
schedules, wakeups, and transport telemetry. Keep one Marquee `Job`/`Attempt`/`Event` control
plane, canonical attempt lease recovery and domain retry policy, the resource broker, typed
handlers, process supervisor, fencing, and Projection Room.

PgQueuer is the preferred candidate; Procrastinate is the fallback. The pre-admission
transport payload should contain only schema version, canonical `job_id`, dispatch
generation, and correlation/authenticity metadata. Attempt and fence are allocated later by
Marquee's admission transaction. Library queue state is not a second product job model.

**Advantages:** removes custom transport claim/redelivery/schedule machinery, keeps
PostgreSQL, gains standardized transport recovery/metrics, and preserves Marquee-specific
safety.

**Disadvantages:** there are still transport tables plus product tables. Enqueue/terminal
coordination needs an outbox and reconciler. Resource admission remains custom. PgQueuer's
recent 1.x maturity is a real adoption risk.

### Option C — Durable-workflow rewrite

Re-express poster pipelines, subtitle plans, and re-encode flows as DBOS workflows/steps.
Use DBOS queues for durable orchestration, concurrency, scheduling, cancellation, and
visibility. Keep Marquee's resource broker and isolated media activity runner. Temporal is
the alternative only if managed service or a much larger distributed deployment becomes
acceptable.

**Advantages:** durable stage boundaries, resume/fork potential, clearer multi-step
orchestration, and less hand-built workflow state.

**Disadvantages:** highest migration and framework-coupling cost; deterministic workflow
constraints; filesystem steps still at-least-once and require fencing/compensation; custom
Projection Room integration remains.

## Required bake-off

Before selecting PgQueuer over Procrastinate or the custom driver, run the same destructive
suite against all candidates:

1. Kill the worker after dequeue but before handler start.
2. Kill during resource acquisition and after admission but before process spawn.
3. Kill ffmpeg/ML child, then kill the worker, including OOM/SIGKILL.
4. Pause a worker longer than its heartbeat timeout and let it resume after repick.
5. Cancel queued, resource-waiting, running, and uncooperative work.
6. Disconnect/restart PostgreSQL during enqueue, progress, heartbeat, and completion.
7. Verify dedupe and no duplicate unsafe mutation after every crash point.
8. Upgrade the runtime schema with queued and running jobs present.
9. Saturate CPU, RAM, GPU, disk, and DB while measuring API p95/p99 latency.
10. Verify audit/event retention and Projection Room reconciliation after every fault.

The go/no-go criteria are in the companion redesign document. A successful library demo is
not enough; it must prove the failure semantics that matter for media mutation.
