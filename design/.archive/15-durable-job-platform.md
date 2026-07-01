# 15 — Durable Job Platform

Marquee runs expensive work (poster pipeline, ML/taste rebuilds, letterbox
detect/apply/re-encode, subtitle ops, backups) through a central, durable,
resource-aware job platform backed by PostgreSQL — not by API routes spawning
work in-process. This replaced the SQLite + media-only `MediaJob` manager.

## Topology

Three roles share one codebase and the same PostgreSQL database:

- **API** (`uvicorn marquee.main:app`) — validates requests and enqueues jobs;
  serves reads and durable SSE. Never runs business work on its event loop.
- **Worker** (`python -m marquee.core.jobs.worker`) — the `DurableWorker` claim
  loop. Claims queued jobs, reserves resources, runs the registered handler,
  heartbeats its lease, persists attempts/events/results.
- **Scheduler** (`python -m marquee.core.jobs.scheduler`) — enqueues recurring
  jobs (poster-heal, letterbox-heal, backup) from `job_schedules`, gated on the
  matching feature flags. Never runs work itself.

### How they start (the important part for dev)

In standalone dev you run **only** `uvicorn`. The API's lifespan auto-spawns the
worker and scheduler as **supervised child processes** via
`marquee/core/jobs/supervisor.py` (`WorkerSupervisor`), controlled by
`JOB_EMBEDDED_WORKERS` (default `true`) and `JOB_EMBEDDED_WORKER_COUNT`
(default `1`). No manual `python -m ...worker` step.

- Children are spawned with `start_new_session=True` (own process group) plus a
  Linux parent-death signal (`PR_SET_PDEATHSIG`).
- The supervisor respawns a child that dies unexpectedly (capped backoff).
- On API shutdown it SIGTERMs each child's **process group**, waits
  `JOB_SHUTDOWN_GRACE_SECONDS`, then SIGKILLs survivors. Group-kill reaps the
  worker *and* any ffmpeg/Paddle subprocess it started — nothing is orphaned.
  The hard kill runs in a `finally`, so it still fires if the API's overall
  shutdown budget (`SHUTDOWN_TIMEOUT_SECONDS`) cancels the drain.

The Compose stack runs **dedicated** worker/scheduler services and sets
`JOB_EMBEDDED_WORKERS=false` on its API container so it does not double-spawn.

## Data model (`marquee/models/job.py`)

`jobs`, `job_attempts` (immutable execution history incl. PID/process-group),
`job_events` (append-only ordered SSE history), `job_resources` (named
capacities), `job_resource_reservations` (durable leases), `job_schedules`,
`job_workers` (heartbeat/health). Applied by Alembic revision
`9e1c0b3d7f42_add_durable_job_platform` (PostgreSQL baseline is the head).

## Lifecycle & resources

States: `planned`, `queued`, `waiting_resource`, `claimed`, `running`,
`waiting_external`, `cancelling`, `paused`, `retry_scheduled`, `succeeded`,
`failed`, `cancelled`, `interrupted`, `dead_letter`.

- **Claiming**: `claim_next` selects claimable jobs with
  `FOR UPDATE SKIP LOCKED`, reserves every requested resource transactionally,
  and only then marks the job `claimed`. No two workers take the same job or the
  same file. If a resource is unavailable the job parks in `waiting_resource`
  and is retried on the next poll.
- **Resources**: `gpu` (cap 1), `media_read` (cap 2), `media_write`/`transcode`
  (cap 1), `network_external`, `maintenance_exclusive` (fences all admissions),
  and per-physical-file `media-file:{media_file_id}` (cap 1). Every operation
  that touches one file — letterbox detect/apply/remove **and** the
  subtitle/reencode media jobs — shares the `media-file:{id}` key, so a
  read-only detect can never overlap a crop write or a re-encode of that file.
- **Leases/recovery**: workers heartbeat each `JOB_HEARTBEAT_SECONDS`; a lease
  lasts `JOB_LEASE_SECONDS`. On startup (and periodically) `recover()` releases
  reservations for expired leases, requeues retryable work or marks it
  `interrupted`, and reaps `job_workers` rows whose heartbeat went stale so
  `/api/jobs/metrics` doesn't report ghosts.
- **Parent/child**: a batch (e.g. letterbox detect/apply) is a parent job kept
  in `waiting_external` (never claimed — no handler) whose children do the work;
  `_update_parent` rolls children up to a terminal parent status.

## Handlers (`marquee/core/jobs/`)

Routes never name a callable; they enqueue a `job_type` that the worker resolves
in the registry. `builtin_handlers.py` covers poster/heal/letterbox/taste/sync/
webhook follow-up; `legacy_media.py` bridges subtitle + `letterbox_reencode`
into the established `MediaJob` operation code (the generic `Job` owns
scheduling/leases; the `MediaJob` row stays the detailed operation record).

## Enqueue vs inline

Heavy/GPU/file-mutating → enqueue (202 + job summary; consume via
`GET /api/jobs/{id}` and the replayable SSE at `/api/jobs/{id}/events`).
Fast, CPU-light work stays inline: reads, listing, config, in-memory rescore,
and **library sync** (`/api/sync/all` — network + DB only). Expensive-endpoint
cooldowns are skipped when `DEBUG`.
