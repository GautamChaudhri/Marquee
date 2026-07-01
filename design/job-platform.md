# Durable Job Platform

## Overview

Marquee runs heavy, long-lived, or filesystem-mutating work through a durable
job platform instead of keeping that work on the FastAPI request loop. The
platform persists jobs in the database, applies resource reservations, emits
progress events, recovers stale attempts, and can run either as dedicated
processes or as embedded child processes supervised by the API.

## Core Components

- `marquee/core/jobs/manager.py` contains `job_manager`, the sole authority for
  creating jobs, reserving resources, claiming work, updating lifecycle state,
  and emitting durable events.
- `marquee/core/jobs/worker.py` implements `DurableWorker`, the claim / execute
  loop.
- `marquee/core/jobs/scheduler.py` reconciles recurring schedules and creates
  scheduled jobs; it never performs the scheduled work itself.
- `marquee/core/jobs/supervisor.py` starts and reaps embedded worker and
  scheduler child processes when `JOB_EMBEDDED_WORKERS=true`.
- `marquee/core/jobs/handlers.py` resolves job types to server-side handlers,
  and `builtin_handlers.py` plus `legacy_media.py` register the handlers.
- `marquee/models/job.py` stores `Job`, `JobAttempt`, `JobEvent`,
  `JobResource`, `JobResourceReservation`, `JobSchedule`, and `JobWorker`.

## Lifecycle

Jobs move through durable states such as created, queued, claimed, running,
completed, failed, interrupted, cancelled, or paused. `JobManager` owns the
transitions so handlers do not update status tables directly.

Important behavior:

- `claim_next()` uses the database to safely pick work for a worker.
- Each running attempt renews its lease on a heartbeat.
- `recover()` can reclaim stale attempts left behind by dead workers.
- `JOB_MAX_RUNTIME_SECONDS` bounds handler runtime so hung OCR or ffmpeg work
  does not block resources forever.

## Resource Reservations

The platform reserves named capacities before a job runs. The important pools
come from `marquee/config.py`:

- `gpu`
- `media_read`
- `media_write`
- `network_external`
- `maintenance_exclusive`
- per-file locks such as `media-file:{id}`

Capacity defaults are controlled by settings like `JOB_GPU_SLOTS`,
`JOB_MEDIA_READ_SLOTS`, `JOB_MEDIA_WRITE_SLOTS`, and `JOB_NETWORK_SLOTS`.

This is the mechanism that keeps poster pipeline runs, subtitle mutations,
letterbox re-encodes, and similar work from colliding with each other.

## Embedded Versus Dedicated Runtime

`marquee/main.py` starts the worker supervisor during API startup when
`JOB_EMBEDDED_WORKERS=true`. That mode is convenient for local development: the
API starts the worker and scheduler for you and also reaps them on shutdown.

In containerized setups, the API can run with `JOB_EMBEDDED_WORKERS=false`
while dedicated worker and scheduler services run separately.

## Recurring Schedules

`scheduler.py` durably enables or disables schedule rows based on feature
flags. The current schedule set includes:

- `poster-heal`
- `letterbox-heal`
- `backup`
- `job-retention-purge`
- `system-metrics-purge`

Backups are skipped in `DEBUG`, while hygiene jobs remain enabled.

## Handler Registry And Safety

Handlers are registered in code with `@register(...)`. API requests enqueue a
validated `job_type` and payload, but they never choose an arbitrary Python
callable. This keeps job execution server-controlled and reviewable.

Representative built-in job types include:

- `poster_pipeline`
- `poster_pipeline_batch`
- `taste_rebuild`
- `learned_head_train`
- `letterbox_detect`
- `letterbox_apply`
- `letterbox_remove`
- `subtitle_scan_all`
- `backup_create`
- `poster_heal`

## Progress And API Surface

Routes under `marquee/api/routes/jobs.py` provide browse, detail, metrics,
children, events, cancel, pause, resume, priority, and retry controls. The
platform persists progress in `JobEvent` rows so the UI can reconnect and still
see the history of long-running operations.

Pipeline-specific and media-job-specific routes layer on top of the same job
records rather than inventing separate worker systems.

## Enqueue Versus Inline

As a rule:

- Queue work that is GPU-bound, long-running, or mutates media files.
- Keep reads, listing, settings reads/writes, and pure rescoring inline.
- Keep library sync inline unless it becomes meaningfully heavier than its
  current network-plus-database shape.

That split is used consistently across the poster pipeline, letterbox, subtitle
management, HDR analysis/conversion, taste rebuilds, and maintenance tasks.

## Important Configuration

Representative job knobs in `marquee/config.py`:

- `JOB_EMBEDDED_WORKERS=true`
- `JOB_EMBEDDED_WORKER_COUNT=1`
- `JOB_WORKER_CONCURRENCY=4`
- `JOB_POLL_SECONDS=0.5`
- `JOB_HEARTBEAT_SECONDS=10`
- `JOB_LEASE_SECONDS=60`
- `JOB_MAX_RUNTIME_SECONDS=3600`
- `JOB_GPU_SLOTS=1`
- `JOB_MEDIA_READ_SLOTS=2`
- `JOB_MEDIA_WRITE_SLOTS=1`
- `JOB_NETWORK_SLOTS=4`
- `JOB_RETENTION_DAYS=30`

## Cross References

- `poster-pipeline.md` for poster-run and retraining jobs
- `letterbox.md` for detect/apply/remove/re-encode jobs
- `audio-subs.md` for subtitle scan, mutation, and generation jobs
- `library.md` for recurring heal and backup behavior
