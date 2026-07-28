# Durable Job Platform

## Overview

Marquee runs heavy, long-lived, or filesystem-mutating work through a durable
job platform instead of keeping that work on the FastAPI request loop.
PgQueuer owns transport — the queue table, the notification channel, and the
claim protocol. Marquee owns semantics: what a job type means, what it is
allowed to touch, how its progress is reported, and what evidence it leaves
behind.

## Core Components

- `marquee/core/jobs/manifest.py` is the closed catalog of job types. A type
  that does not appear here cannot be submitted, scheduled, or executed.
- `marquee/core/jobs/kernel_handlers.py` binds handler modules to the kernel
  and is the scope boundary: a handler family that is not imported there cannot
  run, whatever is enqueued.
- `marquee/core/jobs/delivery.py` is the execution kernel — it receives a
  claimed PgQueuer job, resolves its definition, enforces the entrypoint match,
  runs the handler, and drives the terminal decision.
- `marquee/core/jobs/submission.py` and `pgqueuer_gateway.py` validate and
  enqueue work; `batches.py` coordinates parent/child batch jobs.
- `marquee/core/jobs/pgqueuer_worker.py` claims and executes jobs;
  `pgqueuer_scheduler.py` enqueues recurring ones; `supervisor.py` spawns and
  reaps both as supervised child processes when `JOB_EMBEDDED_WORKERS=true`.
- `marquee/core/jobs/internal_runner.py` and `internal_runner_host.py` fence
  pipeline work into its own process group.
- `marquee/models/job.py` and `marquee/models/job_evidence.py` store `Job`,
  `JobDispatch`, `JobBatch`, `JobAttempt`, `JobEvent`, `JobLog`, `JobArtifact`,
  `WorkerNode`, `RuntimeInstance`, and `MediaOperationDetail`.

## Lifecycle

Jobs move through durable states — created, queued, claimed, running,
completed, failed, interrupted, cancelled. The kernel owns the transitions, so
handlers never write status columns themselves.

Important behaviour:

- Claiming goes through PgQueuer; `JobDispatch` links the Marquee job to the
  PgQueuer entry so the two views can never silently diverge.
- Running attempts renew a lease (`JOB_LEASE_SECONDS`,
  `JOB_HEARTBEAT_SECONDS`); `JOB_MAX_RUNTIME_SECONDS` bounds a handler so a
  wedged run cannot hold its resources forever.
- Cancellation is cooperative: the kernel signals, the handler (or the fenced
  child process) observes and unwinds, and the terminal decision records why.
- `runtime_instances.py` records which process owns which work, so a restarted
  or duplicated runtime cannot claim work that another instance still holds.

## Execution Classes

Concurrency is enforced per *execution class*, and each job definition declares
the one it belongs to. `pgqueuer_worker.entrypoint_concurrency_limits()`
resolves them from settings:

| Class | Setting | Default |
|---|---|---|
| `control` | `JOB_CONTROL_CONCURRENCY` | 4 |
| `network` | `JOB_NETWORK_CONCURRENCY` | 4 |
| `cpu` | `JOB_CPU_CONCURRENCY` | 2 |
| `media_read` | `JOB_MEDIA_READ_CONCURRENCY` | 2 |
| `media_write` | `JOB_MEDIA_WRITE_CONCURRENCY` | 1 |
| `gpu` | `JOB_GPU_CONCURRENCY` | 1 |
| `maintenance` | `JOB_MAINTENANCE_CONCURRENCY` | 1 |

`JOB_WORKER_ENTRYPOINTS` selects which classes a given worker process serves,
so a deployment can dedicate one process to GPU work and another to everything
else. `safety_gates.py` adds the exclusive barriers that operations like backup
and reset require.

## Embedded Versus Dedicated Runtime

`marquee/main.py` starts the supervisor during API startup when
`JOB_EMBEDDED_WORKERS=true` — convenient for development, where one `uvicorn`
command yields a working platform. Containerised deployments set it to `false`
and run dedicated worker and scheduler services (see
`docker/docker-compose.yml`), which is also how GPU work is kept off the API
process.

## Recurring Schedules

`schedules.py` holds the schedule catalog. The production set is:

- `library-sync` — pull movies and series from Radarr/Sonarr
- `poster-heal` — reconcile posters that went missing on disk
- `evidence-retention` — purge expired job logs, artifacts, and metrics

Schedules stay dormant unless `JOB_PRODUCTION_SCHEDULES_ENABLED=true`, so a
development database never starts doing library work on its own. A separate
fixed test catalog drives the `system_noop` liveness schedule.

## Job Types

Every executable type lives in `manifest.py`. The current families:

| Family | Types |
|---|---|
| Pipeline | `poster_pipeline`, `poster_pipeline_batch`, `poster_pipeline_tv_batch`, `poster_analysis_adapter`, `poster_candidate_set` |
| Poster mutation | `poster_deploy`, `poster_deploy_reset`, `poster_reset`, `poster_restore`, `poster_heal`, `poster_rescan`, `poster_maintenance` |
| Poster backup | `poster_backup_subject`, `poster_backup_all` |
| Learning | `taste_rebuild`, `taste_enrich`, `taste_map`, `model_profile_training` |
| Library | `library_sync` |
| System | `backup_create`, `job_retention_purge`, `system_metrics_purge`, `pipeline_cache_clear`, `system_noop`, `system_work` |

Each definition carries its execution class, effect safety (read-only versus
unsafe mutation), progress policy, and the configuration keys it is allowed to
read. API requests submit a validated `job_type` and payload — never an
arbitrary callable.

## Progress, Evidence, And API Surface

`progress.py`, `progress_service.py`, and `execution_progress.py` persist
progress as durable `JobEvent` rows keyed to declared phases, so a browser that
reconnects mid-run still sees the whole history. `log_capture.py` and
`artifact_service.py` capture handler output and produced files as owned
evidence with retention.

Routes under `marquee/api/routes/jobs.py` expose browse, detail, batch
children, events, cancel, retry, and priority controls, plus two SSE streams:

- `GET /api/jobs/{job_id}/events` — one job
- `GET /api/jobs/events/stream` — all activity

## Enqueue Versus Inline

- Queue anything GPU-bound, long-running, or that mutates a file on disk.
- Keep reads, listings, configuration changes, and pure in-memory rescoring
  inline.
- Library sync stays inline as an API call (`POST /api/sync/all`) because it is
  network-and-database work, and also exists as a scheduled job.

## Important Configuration

Representative knobs in `marquee/config.py`:

- `JOB_EMBEDDED_WORKERS`, `JOB_EMBEDDED_WORKER_COUNT`, `JOB_WORKER_ENTRYPOINTS`
- `JOB_WORKER_CONCURRENCY` and the per-class limits above
- `JOB_PGQUEUER_BATCH_SIZE`, `JOB_PGQUEUER_DEQUEUE_SECONDS`,
  `JOB_PGQUEUER_HEARTBEAT_SECONDS`
- `JOB_RUNTIME_HEARTBEAT_SECONDS`, `JOB_RUNTIME_EXPIRY_SECONDS`
- `JOB_LEASE_SECONDS`, `JOB_HEARTBEAT_SECONDS`, `JOB_MAX_RUNTIME_SECONDS`
- `JOB_PRODUCTION_SCHEDULES_ENABLED`, `JOB_RETENTION_DAYS`

## Cross References

- `poster-pipeline.md` — how pipeline runs and learning jobs are produced
- `library.md` — sync, deployment, and heal behaviour
- `overview.md` — where the platform sits in the application
