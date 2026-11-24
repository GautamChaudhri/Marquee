# Architecture

How the Marquee package is laid out, what each part is for, which way imports may point, and
where the application keeps its runtime state.

## Packages

```
marquee/
  __init__.py      version
  cli.py           the `marquee` command; one entry point for everything an operator runs
  doctor.py        `marquee doctor`: read-only checks of the machine and its connections
  model_fetch.py   `marquee models fetch`: builds the model files and their checksum manifest
  cancellation.py  cooperative cancellation protocol shared by long-running work
  host_metrics.py  CPU, memory, disk, and NVIDIA GPU readings (NVML, optional)
  logging.py       text and JSON log formatting
  config/          the two settings classes; the only code that reads the environment
  db/              the SQLAlchemy async engine, the declarative Base, the ORM models (movies,
                   artwork, jobs, poster_runs, run_candidates, deployments), the Alembic
                   migrations, and migrate.py, which applies them
  fs/              filesystem safety: confined roots, symlink rejection, download validation,
                   and paths.py, the only place a path from Radarr becomes a local path
  integrations/    thin HTTP clients: arr/ (Radarr, Sonarr), tmdb/, URL validation
  ml/              model wrappers (CLIP, aesthetic head, faces, people, DINOv2, zero-shot axes),
                   hardware and execution-provider selection, the taste store, calibration,
                   the residual learner, geometry types, the model checksum manifest
  pipeline/        the selection engine: gate and rank stages, OCR text gate, feature assembly,
                   normalisation, scoring, design stacking, run output, text profiles, and
                   single_run.py, which runs one subject and leaves a complete run folder
  library/         the movie library: a read-only scan of the movie folders (folders.py, used
                   by the seed), the Radarr sync (sync.py), and the read queries over the
                   library (queries.py)
  artwork/         what Marquee chooses and writes: the run record (runs.py), poster backups
                   (backup.py), and the one path that writes poster.jpg into a movie folder,
                   for a deploy and for a restore (deploy.py)
  taste/           building and enriching the taste profile (trainer, map, profile enrichment)
                   and the first profile seeded from the library's posters (seed.py)
  jobs/            the job queue on PostgreSQL: kinds and class limits (registry.py), every
                   state change (queue.py), batches, schedules, the read queries, the worker,
                   the GPU child process (gpu_child.py), and handlers/, adapters from a job to
                   the domain code that does the work (the sync, the run, deploy and restore)
  api/             the HTTP API: the app factory and server (app.py), API-key auth (auth.py),
                   routes/ (health, library, jobs, runs, deployments), schemas/ (response
                   models), file responses (files.py), and a rate limiter
```

## Layering

Imports only point downward. `uv run lint-imports` enforces this with the contracts in
`pyproject.toml`.

```
6  api, cli, doctor, model_fetch
5  jobs
4  taste  ->  artwork  ->  library     (within the layer, only in this direction)
3  pipeline
2  ml
1  fs, integrations, db
0  config, logging, cancellation, host_metrics
```

Three more contracts:

- `marquee.ml` and `marquee.pipeline` never import `marquee.db` or a database library
  (`sqlalchemy`, `alembic`, `asyncpg`, `psycopg`). The engine takes data in and hands results
  out; it does not know a database exists. A pipeline run identifies its subject with
  `pipeline.types.SubjectRef` (id, title, TMDB id), not an ORM row. The layers contract alone
  would not catch an import of `marquee.db`, because `db` sits below `ml`.
- Only `marquee.config` imports `pydantic_settings` or `dotenv`, and only it reads settings
  from the environment. Other modules import `settings` or `pipeline_settings` from
  `marquee.config`.
- Only `marquee.api` imports a web framework (`fastapi`, `starlette`). The command line
  starts the API through `marquee.api`.

## Settings

There are exactly two settings classes, and both read the same dotenv file (`.env` at the
repository root; `.env.example` lists every setting):

- `Settings` (`marquee/config/app.py`): environment name, debug and log level, the data
  directory, PostgreSQL connection parts, the movie roots and how Radarr sees them
  (`RADARR_MOVIE_ROOTS`), integration credentials, the API's key, host, and port, and the
  worker's two knobs (`SYNC_INTERVAL_MINUTES`, `GPU_IDLE_SECONDS`). Secrets
  are pydantic `SecretStr` values and never print; `secret_values()` lists them so error text
  can be scrubbed. It derives every runtime path and both database URLs: `database_url`
  (`postgresql+psycopg://`, used by SQLAlchemy's async engine and by Alembic) and
  `database_url_sync` (a plain libpq URL, for `psycopg.connect`). psycopg 3 is the only
  database driver. A database URL holds the password, so neither is ever logged or printed.
- `PipelineSettings` (`marquee/config/pipeline.py`): every engine threshold, weight, and
  switch. Model and artifact paths default to locations under the data directory.

The environment variable `MARQUEE_ENV_FILE` selects a different dotenv file, or none when set
to an empty string. The test suite uses it so that no test reads a real `.env`, and the
container image uses it so that every setting comes from the environment.

## Commands

Everything an operator runs goes through the `marquee` command:

| Command | Does |
|---|---|
| `marquee doctor` | Read-only checks of the machine, the backups directory, the models, the manifest, the taste profile, and every connection |
| `marquee models fetch [--force ITEM]` | Builds every model that is missing (CLIP, DINOv2, zero-shot axes, YOLO person, OCR weights) and downloads the LAION aesthetic head and the SCRFD face detector, pinned by SHA-256. Checks each one before putting it in place, and writes `data/models/manifest.json`. CLIP, DINOv2, the zero-shot axes, and the aesthetic head's conversion need the `export` dependency group; the YOLO exporter runs in its own isolated `uv` tool environment |
| `marquee taste seed [--dry-run] [--rescan \| --reuse]` | Builds the first taste profile from the `poster.jpg` in each movie folder, staged as lossless 500 px wide copies. Refuses to scan again unless given `--rescan`; `--reuse` rebuilds from the staged copies. `--rescan` is refused once Marquee has written a poster |
| `marquee run "<title>" [--year Y] [--movie-id N] [--no-wait] [--json]` | Finds the movie in the database (exact title, case ignored), queues a `poster_run`, and follows it, printing the stages and the summary; with no worker running it queues nothing unless `--no-wait` |
| `marquee runs list [--movie-id N] [--state S] [--json]` / `marquee runs show <id> [--json]` | The newest 50 runs; one run and its candidates |
| `marquee runs batch --scope never-run\|no-poster\|all [--limit N]` | Queues a batch of runs, one line per job |
| `marquee deploy <run id> [--candidate N]` | Queues the owner's deploy of the run's pick, or of another candidate |
| `marquee deployments list [--movie-id N] [--json]` | The newest 50 deployments |
| `marquee restore <deployment id>` | Queues a restore of a movie's newest deployment |
| `marquee db upgrade` | Applies every migration the database lacks |
| `marquee db status` | Shows the database's revision and the newest one; exits 1 when they differ |
| `marquee db revision -m "<text>"` | For developers: writes a new migration from the difference between the models and a database at the newest revision |
| `marquee sync [--dry-run] [--json]` | Checks that Radarr's root folders map to folders here, copies Radarr's movie list into the database, records each folder's poster, and fetches TMDB details once per movie; exits 1 when a movie failed or the path check failed |
| `marquee api [--host] [--port]` | Serves the HTTP API under uvicorn, on `API_HOST` and `API_PORT` by default |
| `marquee worker [--drain]` | Runs the job queue until SIGTERM or Ctrl-C, which stop it gracefully. Only one runs at a time; a second exits with "a worker is already running". `--drain` exits once nothing is runnable and queues no scheduled work |
| `marquee jobs list [--state] [--kind] [--json]` | The newest 50 jobs |
| `marquee jobs show <id>` | One job and its events |
| `marquee jobs enqueue <kind> [--payload JSON] [--priority N]` | Queues a job of a registered kind; a kind that allows one active job returns the one already queued or running |
| `marquee jobs cancel <id>` / `marquee jobs retry <id>` | Cancels a queued job, or asks a running one to stop; queues a failed or cancelled job again |

## Database

PostgreSQL, reached through SQLAlchemy 2's async engine on psycopg 3 (`marquee/db/engine.py`
builds it; nothing connects at import time). Constraints and indexes are named by a naming
convention on `Base.metadata` (`pk_`, `fk_`, `uq_`, `ck_`, `ix_`), and every table carries
`created_at` and `updated_at`, both timezone-aware.

**Migrations live inside the package**, in `marquee/db/migrations/` (the environment script,
the revision template, and `versions/`), because the container image installs the package
without its source tree. There is no `alembic.ini`: `marquee/db/migrate.py` builds the
Alembic configuration in code and hands the environment script an open connection, so no
database URL passes through Alembic's configuration. There is one baseline migration
(`0001`); every later schema change is a new numbered migration (`0002` adds the job tables,
`0003` the run and deployment tables), and a merged migration is never edited.

The library schema is movies only (the job tables are described under [Jobs](#jobs)):

| Table | Holds |
|---|---|
| `movies` | Every movie Radarr lists, with or without a file (`has_file`): Radarr's id (unique), the TMDB and IMDb ids (`tmdb_id` is indexed but not unique), title, sort title, year, genres, Radarr's folder path, the movie file's relative path, size, and Radarr id, the TMDB details the text gate uses (director, production companies, tagline), and presence: a movie Radarr stops listing is kept, with `is_present` false and `retired_at` set |
| `artwork` | One row per movie per artwork kind, for the file in the movie folder: the file's name (never a path), its SHA-256, size, and modification time when it was hashed, its origin, and when it was first and last seen. The origin is `library` (found there by a sync: the owner's) or `marquee` (written by a deploy). `subject_kind` and `artwork_kind` accept one value today (`movie`, `poster`); every value set is a check constraint, and adding a value is a migration. Rows go with their movie (`ON DELETE CASCADE`) |
| `poster_runs` | One row per pipeline run of one movie: the movie (no cascade: movies are retired, never deleted), the job that ran it (unique, so a job has one run; set null if the job is deleted), the state (`running`, `completed`, `failed`, `cancelled`), the run folder's name inside the runs directory (never a path), the engine's run id and status, the scorer, the model, the taste profile's SHA-256 when it started, the counts and stage timings (JSONB), how many candidates and how many ranked, a one-line error, and when it started and finished |
| `run_candidates` | One row per candidate a run considered (unique per run and TMDB file name): the SHA-256 of the 500 px file it downloaded (null when it never downloaded it), the stage it reached, its gate decision and rejection reason, its rank, final score, and design stack, and whether it is the pick (a partial unique index allows one per run). Feature values stay in the archive. Rows go with their run |
| `deployments` | Append-only (`created_at`, no `updated_at`): every write to a movie folder, a `deploy` or a `restore`, who asked (`owner` or `policy`), the run, candidate, and TMDB file it came from, the job (unique: a job writes at most one row), the file's name, the new file's SHA-256 and size (null when the write removed the poster), the previous poster's SHA-256 and origin (null when there was none), its backup's name relative to the backups directory, and for a restore the row it undoes |

**The database never stores a local path.** The host and the container see the library at
different paths and share one database, so a movie row holds its folder as Radarr reports
it, and an artwork row holds a file name inside that folder.

## Path mapping

`marquee/fs/paths.py` is the only place a path from Radarr becomes a local path. Each folder
in `MOVIE_ROOTS` pairs, by position, with the folder `RADARR_MOVIE_ROOTS` names for it; with
`RADARR_MOVIE_ROOTS` unset, Radarr is taken to see the library where Marquee does. Compose
overrides only `MOVIE_ROOTS` inside a container, so the same `RADARR_MOVIE_ROOTS` maps
Radarr's paths to the host's folders on the host and to `/media/movies` in a container.

Translation is lexical: it matches whole path components (`/movies2` is not under
`/movies`), uses the most specific root, and refuses a null byte, a `..` component, or a
result outside its root, rather than collapse or guess. Nothing opens, stats, or lists a
path from Radarr before it has been translated, and files are opened through
`fs/boundary.py`, which refuses symbolic links.

## The Radarr sync

`marquee/library/sync.py`, run by `marquee sync`:

1. Every root folder Radarr reports must translate to a folder that exists here. If one
   does not, the sync stops before it reads or writes anything else: a missing share must
   never look like a library whose posters were all deleted.
2. One request for Radarr's movie list, and one read-only scan of the movie folders
   (`library/folders.py`).
3. Under a PostgreSQL advisory lock (a second sync started meanwhile exits with "another
   sync is running"), every Radarr movie is upserted by its Radarr id. A movie that cannot be
   stored is counted and logged, and the others go on. Movies Radarr no longer lists are
   retired, and come back if they return, but only when no movie failed.
4. Each scanned folder is matched to its movie through the translated Radarr path. A
   `poster.jpg` is stat'ed, and read and hashed only when it has no row yet or its size or
   modification time changed (compared for equality, never "newer than"). When the file is
   gone from a folder the scan found, its row is deleted. A row of origin `marquee` is treated
   the same way, except that new bytes make it origin `library` (see
   [Writing a poster](#writing-a-poster)).
5. TMDB details for movies with a file that were never enriched, at most five requests at a
   time. A changed TMDB id clears them, so they are fetched again. A failure is logged and
   does not fail the sync.

Everything happens in one transaction, committed at the end (rolled back for `--dry-run`).
Filesystem work runs off the event loop, and the sync checks an optional cancellation signal
(`marquee.cancellation`) between phases. It returns a report of the movies it created,
updated, left unchanged, retired, and failed, the posters it found, hashed, and removed, the
folders on disk no Radarr movie points at, and the movies with a file whose folder was not
found.

## Jobs

`marquee/jobs/` is a small job queue on PostgreSQL and the one worker that runs it, `marquee
worker`. There is no third-party queue library.

| Table | Holds |
|---|---|
| `jobs` | One row per job: its kind and class, state, priority, JSON payload and result, a one-line error with every secret blanked out, the latest progress, how many times it has started (`attempt`) and may start, `run_after` (a queued job is not claimed before it), an optional dedupe key, the batch it belongs to (`parent_id`), and whether a cancel was asked for. A partial unique index allows one queued or running job per dedupe key; a partial index on (`priority` descending, `id`) where `state = 'queued'` serves the claim |
| `job_events` | Append-only: what happened to a job and when (`queued`, `started`, `progress`, `retry_scheduled`, `succeeded`, `failed`, `cancel_requested`, `cancelled`, `requeued`, `retried`), with a message and data. Its id is the cursor of the API's event feed. It has `at` instead of the usual timestamps |
| `job_schedules` | When each schedule declared in code is next due, and the last job it queued |

**States.** A job is `queued`, then `running`, then `succeeded`, `failed`, or `cancelled`.
Every change is one `UPDATE` guarded by the state it expects (`queue.py`) and writes its event
in the same transaction, so a late or repeated change does nothing; queueing, cancelling, and
retrying also notify the worker in that transaction.

```
queued ──claim──> running ──> succeeded
  ^  │               │  ├───> failed     (permanent error, last attempt, or cancel asked)
  │  │               │  └───> cancelled  (the handler stopped as asked)
  │  └──cancel──> cancelled
  └── back to queued: a failed attempt with attempts left (retry_scheduled), a graceful stop
      (requeued, attempt not counted), an orphan with attempts left (requeued), or a failed
      or cancelled job retried by hand (retried: attempt 0, same row, history kept)
```

A failed attempt is retried after 30 s, doubling each time, at most 10 minutes, while the job
has attempts left. An error marked permanent (`PermanentJobError`) fails at once.

**One worker.** At start the worker takes a PostgreSQL advisory lock on a connection of its
own and holds it for its whole life; a second worker finds it taken and exits. Because no
other worker can exist, a job still `running` when the worker starts was left by one that
died: it goes back to `queued` if it has attempts left, and fails otherwise. There are no
heartbeats. Whether a worker is alive is read from `pg_locks` (`marquee doctor`'s Worker line
and `GET /api/worker`), without taking the lock. The lock's connection also listens for "new
job" and "cancel" notifications; the worker polls every five seconds as well, which is how it
sees a `run_after` come due and what keeps the queue correct when a notification is missed.
If that connection is lost, the lock is gone with it and the worker stops.

**Classes.** Each kind belongs to a class, and the worker runs at most `gpu` 1, `network` 4,
`media_read` 2, and `media_write` 1 jobs of each at once. It claims the highest-priority,
oldest queued job whose class has a free slot, with `FOR UPDATE SKIP LOCKED`, and runs it as
a task with its own database sessions, a cancel signal (the `is_set()` shape of
`marquee.cancellation`), a progress function that writes at most about once a second, and its
kind's timeout.

| Kind | Class | Attempts | Timeout | Does |
|---|---|---|---|---|
| `library_sync` | network | 3 | 15 min | One Radarr sync (`library.sync.sync_from_settings`). Another sync holding its lock is a success marked skipped; folders that do not map are a permanent error. One active job at a time (dedupe key) |
| `gpu_selftest` | gpu | 1 | 5 min | In the GPU child, loads CLIP as the doctor does (once per child) and embeds one batch; reports the provider, the time, and whether the session was already loaded |
| `poster_run` | gpu | 2 | 10 min | One movie (`{"movie_id": N}`) through the pipeline, with its run record (see [Runs](#runs)). One active run per movie (`poster_run:<movie id>`) |
| `poster_deploy` | media_write | 3 | 5 min | Writes a candidate's original into the movie folder (`{"candidate_id", "initiated_by", "movie_id"}`); see [Writing a poster](#writing-a-poster). One active write per movie (`poster_write:<movie id>`) |
| `poster_restore` | media_write | 3 | 5 min | Undoes a movie's newest deployment (`{"deployment_id", "movie_id"}`); the same key as `poster_deploy` |

A dedupe key may name payload fields in braces (`poster_run:{movie_id}`), so a kind can allow
one active job per movie. A batch's children keep their kind's key: a child whose key an active
job already holds is left out, and the parent's `queued` event counts it as skipped.

**The GPU child.** No model ever loads in the worker. GPU jobs run in one child process,
started with the spawn method (CUDA does not survive a fork) on the first GPU job and kept
between jobs. It is not a daemon process, because the pipeline starts OCR worker processes
of its own. The parent sends one job at a time over a pipe; the child sends back progress and
then a result or an error, and cancel is a shared event. The child runs all its async work on
one event loop for its whole life: the engine creates asyncio semaphores when it is imported,
and a second loop in the same process would fail its downloads. The child leads its own
process group, which its OCR workers join: if it dies mid-job (a crash, an out-of-memory
kill, `kill -9`), the job fails with "the GPU process died" and the exit code or signal, the
worker kills what is left of the group, and the next GPU job starts a new child. If the
worker dies, the child sees its parent change within a second and kills its whole group.
After `GPU_IDLE_SECONDS` (default 300) without a job the worker stops the child, which frees
the GPU memory it holds.

A gpu kind either names a function the child runs (`gpu_function`, as `gpu_selftest` does), or
has a handler that runs in the worker and calls the child for the part that needs the GPU:
`await ctx.gpu("module:function", payload)`, which goes through the worker's GPU lock with the
job's own cancel signal and progress function. That is how a handler can read the database,
await the child, and write the result while the child itself never touches the database. A
handler of any other class that calls `ctx.gpu` fails for good.

**Timeouts, cancel, and stopping.** A job past its kind's timeout fails with "timed out"; a
GPU job's child is killed, since it is still busy. Cancelling a queued job cancels it at
once; a running one gets `cancel_requested`, which the worker passes to the handler, and a
handler that stops raises `JobCancelledError` and ends `cancelled`. On SIGTERM or SIGINT the
worker stops claiming, signals cancel to what is running, waits up to ten seconds, puts what
did not finish back in the queue without counting the attempt, stops the GPU child, and
exits 0.

**Batches.** A batch is a parent row (`kind` and class `batch`) with child jobs. The worker
never runs the parent; the transaction that moves its last active child to a final state
also finishes it, after locking the parent's row: succeeded if every child did, cancelled if
a cancel was asked for, otherwise failed, with the counts in its result. Cancelling a batch
cancels its queued children and asks its running ones to stop.

**Schedules** are declared in code (`schedules.py`), with their intervals in settings; the
table holds only when each is next due. A schedule seen for the first time is due at once.
When one is due, its job is queued under a dedupe key and its due time moves on by the
interval, in one transaction, so a schedule never has two jobs active and a long stop does
not queue a burst. The only schedule so far is the library sync, every
`SYNC_INTERVAL_MINUTES` (default 360; 0 turns it off). With `RUN_NEW_MOVIES` true, a sync job
that succeeds then queues a batch of runs of every eligible movie that has no run yet.

Handlers must be safe to run twice: a job interrupted by a restart runs again.

## Runs

A run is a `poster_run` job. Its handler (`jobs/handlers/poster_run.py`) runs in the worker
and keeps the database work around the GPU child's:

1. It loads the movie and refuses for good unless Radarr still lists it, it has a file, a
   TMDB id, and TMDB details (the text gate reads the director, studios, and tagline; a run
   without them would decide differently without saying so).
2. `artwork/runs.py::start` finds or creates the run's row for the job, and commits. The run's
   folder is named after its record, `<runs dir>/<run id, 8 digits>-<title slug>`. On a second
   attempt of the same job the row is reused: its candidates and its old folder are deleted
   and it is set running again, so a job never has two rows, folders, or sets of candidates. A
   run of the same movie still marked running whose job is no longer active is marked failed.
3. It awaits the child with a plain JSON payload: the folder's name, the movie's id, title, and
   TMDB id, and the three text-gate fields from the `movies` table. In the child,
   `jobs/handlers/gpu.py::poster_run` hands the engine those fields by the names it reads
   (`production_companies_json` for the table's `production_companies`), runs
   `pipeline/single_run.py::run_single_subject` on the child's one event loop, and returns a
   small result: the status, counts, stage timings, scorer, engine run id, the taste profile's
   SHA-256, and the pick's file name. The archive stays on disk.
4. Back in the worker it reads `pipeline_run.json` off the event loop, hashes each downloaded
   candidate (the images at the top of the run folder, one per candidate, named by TMDB's file
   name), and `runs.complete` stores the candidates and marks the pick in one transaction.

A cancel that was asked for marks the run cancelled and removes its folder; a stopping worker
puts the job back instead, and its next attempt starts the run over. A failure marks it failed
with the one-line error and keeps the folder for its log.

**The pick is the engine's recommendation:** the highest-scoring candidate, rank 1. Stacks are
how designs are shown, not what is deployed. A run of a movie whose own poster seeded the taste
profile picks that poster, because the poster's exemplar is its candidate's nearest neighbour.

**Batches.** `enqueue_scope` builds a list of eligible movies and queues one batch of runs:
`never-run` (no run record), `no-poster` (no `artwork` row), or `all`. A movie whose run is
already queued or running is left out by its dedupe key.

**The embedding cache** (`data/cache/embeddings/<model>/<sha256>.npz`) is keyed by the model
name and the SHA-256 of the candidate file's bytes, and each entry records that hash; TMDB
serves different bytes under one file name, and a name key would pair an embedding of one
encoding with features measured on another.

## Writing a poster

`artwork/deploy.py::write_poster` is the one path that writes `poster.jpg` into a movie folder,
for a deploy and for a restore, in this order:

1. The folder comes from `PathMapper.to_local(movie.radarr_path)` every time; it must exist
   and is never created. The target is confined to the movie roots through `fs/boundary.py`;
   a symbolic link, as the folder or as `poster.jpg`, is refused.
2. The new bytes arrive validated: a deploy fetches the candidate's file at TMDB's `original`
   size (asked for as `.jpg`), through `fs/download_guard.py`, and decodes it fully off the
   event loop; it must be a JPEG of 500 to 10,000 px on each side, and it is never converted.
   A truncated file is refused even though the engine makes Pillow tolerate one. The fetched
   bytes are kept (`data/cache/deploys/<job id>.jpg`) until the job records its write, so a
   retry writes the same bytes. A restore reads its bytes from a verified backup, or has none
   when it puts back "no poster".
3. If the target already holds exactly the new bytes, nothing is written.
4. Whatever is in the way is backed up and the backup verified: content-addressed,
   `data/backups/posters/<movie id>/<sha256>.jpg`, written under a temporary name, synced,
   renamed, and read back. A backup already there with the right hash is kept; one with the
   wrong hash is an error.
5. The new bytes go to a temporary file in the same folder, flushed and synced, then renamed
   over the target (the replaced file's mode is kept; a new one gets 0644). A failure removes
   the temporary file.
6. The target is read back and its hash compared; if it differs, the previous poster is put
   back and the write fails loudly.
7. One transaction, under the sync's advisory lock so a sync in progress cannot overwrite the
   change with what it saw, updates the `artwork` row (origin, hash, size, and the
   modification time the file has now) and inserts the `deployments` row.

Every step is safe to repeat. A job that already wrote its `deployments` row is done. When
step 3 finds the new bytes in place because an earlier attempt renamed them and died, the
previous poster is the one the `artwork` row still describes, and its backup must exist. When
the row and the newest deployment already describe the new bytes, nothing is added and the
result says the poster was already deployed; a deploy of the candidate already deployed does
not even fetch it again.

**Restore** undoes a movie's newest deployment, and only that one: it backs up what is in the
folder now, whatever it is, and puts back what the row says was there (from its backup, with
its origin), or removes the poster when there was none. A restore is itself a deployment, so it
can be undone in turn.

**Deploy policy.** Nothing deploys by itself unless `DEPLOY_POLICY` says so. When a run
completes with a pick: `off` (the default) queues nothing; `missing` queues a deploy only when
the movie has no poster or its poster is Marquee's own; `all` queues one whatever is there. A
deploy the policy queued accepts only the pick, and checks the policy again when it runs: under
`missing` the file in the folder must be absent or equal to an `artwork` row of origin
`marquee`, or the job finishes as skipped and writes nothing. A deploy the owner asks for
accepts any downloaded candidate of a completed run.

**The sync and Marquee's posters.** A poster row of origin `marquee` is kept, hashed again, or
deleted the way a library row is; when its bytes changed, the owner replaced it by hand, so it
becomes origin `library` with the new hash and the sync's report counts it. A sync never
touches `deployments`. `marquee taste seed --rescan` is refused once any deployment exists.

## The HTTP API

`marquee/api/app.py` builds the app; its lifespan creates the database engine and disposes
of it. `marquee api` serves it under uvicorn.

| Route | Key | Answers |
|---|---|---|
| `GET /health/live` | no | the process is up |
| `GET /health/ready` | no | the database answers and its schema is the newest migration; 503 otherwise |
| `GET /api/movies` | yes | one page of movies with a file (`include_unavailable` adds the ones Radarr wants), with `q` (case-insensitive title search), `has_poster`, `sort` (`title`, `year`, `added`), `page`, and `page_size` (1 to 200) |
| `GET /api/movies/{id}` | yes | one movie; 404 when absent or retired |
| `GET /api/movies/{id}/poster` | yes | the poster file, with its SHA-256 as the ETag; 404 when there is no poster row or the file is gone or changed |
| `GET /api/jobs` | yes | one page of jobs, newest first, with `state`, `kind`, and `parent_id` filters and paging as for movies |
| `GET /api/jobs/events` | yes | events of every job after the event id `after`, oldest first (`limit` 1 to 500), and the cursor for the next poll |
| `GET /api/jobs/{id}` | yes | one job; 404 when absent |
| `GET /api/jobs/{id}/events` | yes | one job's events, oldest first |
| `POST /api/jobs/{id}/cancel` | yes | cancels a queued job, or asks a running one to stop; 409 for a finished job |
| `POST /api/jobs/{id}/retry` | yes | queues a failed or cancelled job again; 409 otherwise |
| `POST /api/sync` | yes | queues a library sync: 202 with the new job, or 200 with the one already queued or running |
| `GET /api/worker` | yes | whether a worker is running, and how many jobs are queued, running, and failed |
| `POST /api/movies/{id}/run` | yes | queues a run of the movie: 202 with the job, 200 with the run already queued or running; 409 when the movie cannot be run |
| `POST /api/runs/batch` | yes | `{"scope": "never-run"\|"no-poster"\|"all", "limit": N}` or `{"movie_ids": [...]}`: 202 with the batch's parent job and how many runs were queued; 422 names movies that cannot be run |
| `GET /api/runs` | yes | one page of runs, newest first, with `movie_id` and `state` filters |
| `GET /api/runs/{id}` | yes | one run and its candidates, ranked ones first |
| `GET /api/runs/{id}/candidates/{candidate_id}/image` | yes | the 500 px file the run downloaded, confined to the runs directory and checked against its recorded hash (the ETag) |
| `POST /api/runs/{id}/deploy` | yes | `{"candidate_id": N}` optional (the pick by default): queues the owner's deploy, 202; 200 with the movie's write already queued or running; 409 when the candidate cannot be deployed |
| `GET /api/deployments` | yes | one page of deployments, newest first, with a `movie_id` filter |
| `POST /api/deployments/{id}/restore` | yes | queues a restore, 202; 409 unless it is the movie's newest deployment |

Every `/api` route requires the key in `API_KEY`, as `Authorization: Bearer <key>` or
`X-Api-Key: <key>`, compared in constant time: missing or wrong is 401, and with no key
configured the API answers 503. There is no bypass: not for debug mode, not for loopback
clients, and never a key in a query string. Every response is a pydantic model, so the
OpenAPI schema is fully typed, and no response carries a local path or Radarr's path. The
interactive documentation and `/openapi.json` are served only when `MARQUEE_ENVIRONMENT` is
`development`. Requests are logged as method, path, status, and time taken, never a header
or a query string; uvicorn's own access log is off. Queries live in
`marquee/library/queries.py` and `marquee/jobs/queries.py`, not in the routes. No route
queues a job of an arbitrary kind.

## Runtime data

Everything the application writes lives under one directory, `DATA_DIR` (default `data/` in
the repository, never committed). Only `marquee/config/` builds these paths.

```
data/
  models/     ONNX and other model files, manifest.json (their checksums and where each came
              from), and paddlex/, the OCR weights
  ml/         taste profiles, residual artifacts, zero-shot axes, and seed/: the staged
              copies of the library posters the first profile was built from (movies/) and
              the record of what was seeded (movies.json)
  cache/      embeddings (keyed by model and file hash), poster downloads, thumbnails,
              deploys/ (originals a deploy fetched, until it records its write), and in a
              container cuda-jit/, the CUDA driver's kernel cache (the image points the
              driver there, not Marquee)
  runs/       one folder per run, named <run id, 8 digits>-<title slug>: the candidates it
              downloaded, ranked/ output, pipeline.log, and the run archive pipeline_run.json
  backups/    posters/<movie id>/<sha256>.jpg: every poster a write replaced
  text_profiles.json
```

PaddleX keeps its OCR weights wherever `PADDLE_PDX_CACHE_HOME` points. `marquee.ml.hardware`
sets it to `data/models/paddlex` before anything imports PaddleOCR, unless it is already set.

Artifacts are tagged with the model that produced them: a taste profile, embedding cache entry,
or residual built under one model name fails loudly when loaded under another.

## Container image

Marquee is deployed with Docker on a Linux host with an NVIDIA GPU. One `Dockerfile` at the
repository root builds two targets:

| Target | Holds | Used for |
|---|---|---|
| `runtime` | The package and its `nvidia` dependencies, installed from `uv.lock` into `/opt/venv`. No PyTorch | Everything that runs the product |
| `export` | `runtime` plus the `export` dependency group and `uv` | `marquee models fetch` only |

CUDA and cuDNN come from the Python wheels, as on a bare-metal install. The base image is a
slim Python image, and the NVIDIA Container Toolkit on the host supplies only the driver. The
image runs as a non-root user whose user and group ids are build arguments (`MARQUEE_UID` and
`MARQUEE_GID`, default 1000). Dependencies are installed from the lockfile in their own layer,
before any source, so a code change rebuilds only the layer that installs the package.

**State lives on one volume.** The image holds code and dependencies, never a model. A volume
mounted at `/data` is the data directory (`DATA_DIR=/data`): models, taste data, caches, and
runs. It must be writable by the image's user; PaddleX also writes lock and temporary folders
beside the OCR weights. `/data` in the image belongs to that user, so a new named volume does
too. The image also keeps the CUDA driver's kernel cache there (`CUDA_CACHE_PATH`, set to
`/data/cache/cuda-jit`). The driver compiles some kernels for the card on first use; on a
bare-metal host it keeps them in the user's home folder, but a container's home folder is
discarded with the container, and compiling again made every run three times slower.

**Configuration comes from the environment.** The image sets `MARQUEE_ENV_FILE` to an empty
string, so the application reads no dotenv file inside a container, and `marquee doctor` says
the settings come from the environment. No `.env` file and no secret is ever copied into an
image.

In `compose.yaml`, the services `marquee` (target `runtime`) and `marquee-export` (target
`export`) sit behind the `app` profile, so `docker compose up -d` still starts only the
database. Both reserve the GPU, mount the volume `marquee-data` at `/data`, and mount the movie
library read-only at `/media/movies` from the host path in `MOVIE_LIBRARY_HOST_PATH`. They take
their settings from the host's `.env` through `env_file` and override the ones that differ
inside a container: `MOVIE_ROOTS`, `POSTGRES_HOST`, `POSTGRES_PORT`, `DATA_DIR`, and
`MARQUEE_ENV_FILE`. Each runs one command and exits; the API, worker, and web services that make
up the full stack arrive later. Until then, `docker compose --profile app run --rm marquee
marquee worker --drain` runs what is queued, GPU jobs included, and exits.

## Adding a module

1. Find its home:

   | If you are writing... | It goes in... |
   |---|---|
   | something that reads an environment variable or derives a path | `marquee/config/` |
   | a client for an outside service | `marquee/integrations/<service>/` |
   | an ORM model or a migration | `marquee/db/` |
   | filesystem safety, path translation, download validation | `marquee/fs/` |
   | a model wrapper, embedding maths, the taste store, the residual learner | `marquee/ml/` |
   | a selection stage, a gate, a scorer, feature assembly | `marquee/pipeline/` |
   | anything that reads or writes library rows, the sync | `marquee/library/` |
   | run records, poster backups, anything that writes artwork into a media folder | `marquee/artwork/` |
   | queue mechanics (the work a job does lives in its domain package) | `marquee/jobs/` |
   | taste profile building, feedback events, exemplars, seeding | `marquee/taste/` |
   | an HTTP route or response schema | `marquee/api/` |

2. Check the layer table: it may import only from layers below it. Run `uv run lint-imports`.
3. Start the module with `from __future__ import annotations`, type every signature, and log
   through `logging.getLogger(__name__)`.
4. Add its tests at `tests/<package>/test_<module>.py`. Unit tests use no network, no real
   media folder, and no GPU; mark tests that need model files `models`, a GPU `gpu`, or
   PostgreSQL `db`. Database tests use the `db_engine`, `db_session`, or `database_url`
   fixtures (`tests/conftest.py`), which work in a throwaway `marquee_test_*` database and
   leave no rows behind.
5. No `utils`, `helpers`, `common`, `misc`, or `core` modules, and no empty placeholder
   packages: a package appears with its first real module.
