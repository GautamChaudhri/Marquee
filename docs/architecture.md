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
                   artwork, jobs, poster_runs, run_candidates, deployments,
                   preference_events, exemplars, taste_profiles, residual_trainings,
                   evaluations, evaluation_results), the Alembic migrations, and
                   migrate.py, which applies them
  fs/              filesystem safety: confined roots, symlink rejection, download validation,
                   and paths.py, the only place a path from Radarr becomes a local path
  integrations/    thin HTTP clients: arr/ (Radarr), tmdb/
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
                   (backup.py), the one path that writes poster.jpg into a movie folder,
                   for a deploy and for a restore (deploy.py), and the small copies of
                   library posters the web app shows (thumbnails.py)
  taste/           building the taste profile (trainer.py),
                   the first profile seeded from the library's posters and its import as
                   records (seed.py), exemplar records and their kept images (exemplars.py),
                   feedback (feedback.py), the profile registry and how a rebuild is published
                   (rebuild.py), agreement and the review list (agreement.py), the mask: a
                   profile without one movie's exemplars (blind.py), blind evaluation
                   (evaluation.py), and the residual's training and state (residuals.py)
  maintenance/     keeping an installation in order: database dumps, their keep counts, and
                   the restore (dumps.py), and retention, deleting by written rules what has
                   gone stale (retention.py)
  jobs/            the job queue on PostgreSQL: kinds and class limits (registry.py), every
                   state change (queue.py), batches, schedules, the read queries, the worker,
                   the GPU child process (gpu_child.py), review.py (feedback with the deploy and
                   the rebuild it calls for, in one commit), and handlers/, adapters from a job
                   to the domain code that does the work (the sync, the run, deploy and
                   restore, the taste rebuild, the database dump, retention)
  api/             the HTTP API: the app factory, the server, and the OpenAPI document the
                   web app's types come from (app.py), API-key auth (auth.py),
                   routes/ (health, library, jobs, runs, deployments, taste, system),
                   schemas/ (response models), the system summary (system.py), file
                   responses (files.py), and a rate limiter
```

## Layering

Imports only point downward. `uv run lint-imports` enforces this with the contracts in
`pyproject.toml`.

```
7  api, cli, doctor, model_fetch
6  jobs
5  maintenance
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
  worker's knobs (`SYNC_INTERVAL_MINUTES`, `GPU_IDLE_SECONDS`, `GPU_CHILD_MAX_RSS_MB`,
  `DB_BACKUP_INTERVAL_HOURS`, `DB_BACKUP_KEEP`, `RETENTION_INTERVAL_HOURS`,
  `RETENTION_DAYS`). Secrets
  are pydantic `SecretStr` values and never print; `secret_values()` lists them so error text
  can be scrubbed. It derives every runtime path and both database URLs: `database_url`
  (`postgresql+psycopg://`, used by SQLAlchemy's async engine and by Alembic) and
  `database_url_sync` (a plain libpq URL, for `psycopg.connect`). psycopg 3 is the only
  database driver. A database URL holds the password, so neither is ever logged or printed.
  `libpq_environment()` gives the same connection as libpq's variables (`PGHOST`, `PGPASSWORD`,
  ...) for a child process such as `pg_dump`: a password in a process's arguments is visible to
  every user of the machine, in its environment it is not.
- `PipelineSettings` (`marquee/config/pipeline.py`): every engine threshold, weight, and
  switch. Model and artifact paths default to locations under the data directory.

`Settings` also holds `MARQUEE_ROLE` (`all`, `api`, or `worker`: which service of the Compose
stack the process is, read by the doctor) and the two variables the CUDA driver reads,
`CUDA_CACHE_PATH` and `CUDA_CACHE_MAXSIZE`, which Marquee only reports on. `production` is true
when `MARQUEE_ENVIRONMENT` is `production`.

Logging (`marquee/logging.py`) writes UTC times everywhere, and the text format ends its time
with `Z`; setting it up also makes other formatters in the process (the pipeline's run log)
write UTC. `httpx` and `httpcore` log warnings only, so a request line never puts Radarr's
address or a TMDB URL in a log.

The environment variable `MARQUEE_ENV_FILE` selects a different dotenv file, or none when set
to an empty string. The test suite uses it so that no test reads a real `.env`, and the
container image uses it so that every setting comes from the environment.

## Commands

Everything an operator runs goes through the `marquee` command:

| Command | Does |
|---|---|
| `marquee doctor [--role all\|api\|worker] [--json]` | Read-only checks of the machine, the backups directory, the models, the manifest, the taste profile (and, once one is registered, whether the file is that build), the CUDA kernel cache's size against the driver's limit, and every connection. The role (default `MARQUEE_ROLE`, which the Compose stack sets) decides what is checked: `api` leaves out the engine's checks (ONNX Runtime, GPU, OCR, kernel cache) and accepts a read-only library; `worker` needs to write the library. With `MARQUEE_ENVIRONMENT=production` a fall-back to the CPU, a missing GPU, a library the worker cannot write, and (for the worker) missing or old backup tools fail instead of warning. Two lines report on backups: the client tools (`pg_dump`, `pg_restore`, `psql`, the server's major version or newer) and the newest dump's age against `DB_BACKUP_INTERVAL_HOURS` (a warning past two intervals, or with the schedule off). Whether the library is writable is read from the mount table (`/proc/self/mountinfo`: the options of the deepest mount holding the folder) and the folder's permission bits (`access(2)`), never by writing |
| `marquee models fetch [--force ITEM]` | Builds every model that is missing (CLIP, DINOv2, zero-shot axes, YOLO person, OCR weights) and downloads the LAION aesthetic head and the SCRFD face detector. Every input is pinned in `marquee/model_fetch.py`: each downloaded file is checked against its SHA-256 before anything uses it (the CLIP files at a fixed Hugging Face revision, the DINOv2 code at a fixed commit and its weights, the YOLO weights, the aesthetic head, the face detector, and the three OCR folders by a digest over their files); the exported models are pinned through those inputs. Checks each build before putting it in place, and writes `data/models/manifest.json`, which records what each machine built. The models' licences are in [third-party.md](third-party.md). CLIP, DINOv2, the zero-shot axes, and the aesthetic head's conversion need the `export` dependency group; the YOLO exporter runs in its own isolated `uv` tool environment |
| `marquee taste seed [--dry-run] [--rescan \| --reuse]` | Builds the first taste profile from the `poster.jpg` in each movie folder, staged as lossless 500 px wide copies. Refuses to scan again unless given `--rescan`; `--reuse` rebuilds from the staged copies. `--rescan` is refused once Marquee has written a poster, and every form is refused once exemplar records exist |
| `marquee taste import-seed` | Makes each staged seed poster an exemplar record (matched to its movie by IMDb id) and registers the profile file as it is; writes nothing and exits 1 if anything does not match; a second run changes nothing |
| `marquee taste rebuild [--force] [--no-wait]` | Queues a `taste_rebuild` of the active exemplars and follows it |
| `marquee taste status [--json]` | Exemplars by state and polarity, the active profile and whether a rebuild is due, agreement, and the residual's state: whether runs use it and why, its evidence against its thresholds, the active artifact, and the last training |
| `marquee taste evaluate [--movie ID ...] [--limit N] [--wait]` | Queues a blind evaluation (see [Blind evaluation](#blind-evaluation)); refused while one runs or when a rebuild is due |
| `marquee taste evaluation [ID] [--results] [--json]` | An evaluation's summary (the newest by default), seeds apart from feedback; `--results` adds one line per movie |
| `marquee taste residual status [--json]` / `marquee taste residual train` | The residual's state; one training now, recorded whatever comes of it |
| `marquee review list [--json]` / `marquee review show <run> [--json]` | Runs waiting for a review; one run with its ranked candidates' ids, whether it was blind, and its events |
| `marquee review approve <run>` / `pick <run> --candidate N` / `rank <run> --order IDS [--hate IDS]` / `reject <run> [--hate IDS]` | One act of feedback (see [Review and learning](#review-and-learning)); approve, pick, and rank also deploy unless given `--no-deploy`. Follows the jobs it queued when a worker runs |
| `marquee review revoke <event>` | Takes one act of feedback back |
| `marquee run "<title>" [--year Y] [--movie-id N] [--no-wait] [--json]` | Finds the movie in the database (exact title, case ignored), queues a `poster_run`, and follows it, printing the stages and the summary; with no worker running it queues nothing unless `--no-wait` |
| `marquee runs list [--movie-id N] [--state S] [--masked false\|true\|any] [--json]` / `marquee runs show <id> [--json]` | The newest 50 runs (masked runs only when asked); one run, its scorer, and its candidates |
| `marquee runs batch --scope never-run\|no-poster\|all [--limit N]` | Queues a batch of runs, one line per job |
| `marquee deploy <run id> [--candidate N]` | Queues the owner's deploy of the run's pick, or of another candidate |
| `marquee deployments list [--movie-id N] [--json]` | The newest 50 deployments, with what a restore of each would put back |
| `marquee restore <deployment id>` | Queues a restore of any deployment of a movie: it puts back what was in the folder before it |
| `marquee deployments verify [--json]` | Read-only: for each movie's newest deployment, whether its `poster.jpg` is the file recorded, was replaced since, is gone, or cannot be read; for every backup a deployment names, whether it exists with its recorded hash. Compares hashes, never times. Exits 1 when a backup is missing or damaged |
| `marquee db backup` | Dumps the database now, in this process (no worker or job), to `backups/db/manual-<UTC time>-<revision>.dump`; never deleted by Marquee |
| `marquee db backups [--json]` | Every dump in `backups/db/`, newest first |
| `marquee db restore <file> --yes` | Replaces the database's contents with a dump (see [Database dumps](#database-dumps)) |
| `marquee retention [--dry-run] [--json]` | One retention pass now, or with `--dry-run` the list of what it would delete, per rule, with counts and bytes (see [Retention](#retention)) |
| `marquee db upgrade` | Applies every migration the database lacks |
| `marquee db status` | Shows the database's revision and the newest one; exits 1 when they differ |
| `marquee db revision -m "<text>"` | For developers: writes a new migration from the difference between the models and a database at the newest revision |
| `marquee sync [--dry-run] [--json]` | Checks that Radarr's root folders map to folders here, copies Radarr's movie list into the database, records each folder's poster, and fetches TMDB details once per movie; exits 1 when a movie failed or the path check failed |
| `marquee api [--host] [--port]` | Serves the HTTP API under uvicorn, on `API_HOST` and `API_PORT` by default |
| `marquee api-schema [--check] [--output PATH]` | Writes the API's OpenAPI document to `frontend/openapi.json`, which the web app's types are generated from; it needs no database and does not depend on `MARQUEE_ENVIRONMENT`. `--check` writes nothing and exits 1 when the file is missing or stale; the backend CI job runs it |
| `marquee worker [--drain \| --health]` | Runs the job queue until SIGTERM or Ctrl-C, which stop it gracefully. Only one runs at a time; a second exits with "a worker is already running". `--drain` exits once nothing is running and nothing queued can start (a job that finishes while the worker looks keeps it going), and queues no scheduled work. `--health` runs nothing: it exits 0 when a worker holds the queue's lock and the database answers, 1 otherwise (the worker service's health check) |
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
`0003` the run and deployment tables, `0004` the taste tables, `0005` the event that ended
an exemplar and `updated_at` on exemplars and taste profiles, `0006` masked runs, the
residual trainings, and blind evaluations), and a merged migration is never edited.

The library schema is movies only (the job tables are described under [Jobs](#jobs)):

| Table | Holds |
|---|---|
| `movies` | Every movie Radarr lists, with or without a file (`has_file`): Radarr's id (unique), the TMDB and IMDb ids (`tmdb_id` is indexed but not unique), title, sort title, year, genres, Radarr's folder path, the movie file's relative path, size, and Radarr id, the TMDB details the text gate uses (director, production companies, tagline), and presence: a movie Radarr stops listing is kept, with `is_present` false and `retired_at` set |
| `artwork` | One row per movie per artwork kind, for the file in the movie folder: the file's name (never a path), its SHA-256, size, and modification time when it was hashed, its origin, and when it was first and last seen. The origin is `library` (found there by a sync: the owner's) or `marquee` (written by a deploy). `subject_kind` and `artwork_kind` accept one value today (`movie`, `poster`); every value set is a check constraint, and adding a value is a migration. Rows go with their movie (`ON DELETE CASCADE`) |
| `poster_runs` | One row per pipeline run of one movie: the movie (no cascade: movies are retired, never deleted), the job that ran it (unique, so a job has one run; set null if the job is deleted), the state (`running`, `completed`, `failed`, `cancelled`), the run folder's name inside the runs directory (never a path), the engine's run id and status, the scorer that ranked it and, when that was the residual, which training's artifact (`residual_id`), the model, the taste profile's SHA-256 when it started, the counts and stage timings (JSONB), how many candidates and how many ranked, a one-line error, when it started and finished, and `masked`: a blind evaluation's run, never part of the product loop |
| `run_candidates` | One row per candidate a run considered (unique per run and TMDB file name): the SHA-256 of the 500 px file it downloaded (null when it never downloaded it), the stage it reached, its gate decision and rejection reason, its rank, final score, and design stack, and whether it is the pick (a partial unique index allows one per run). Feature values stay in the archive. Rows go with their run |
| `deployments` | Append-only (`created_at`, no `updated_at`): every write to a movie folder, a `deploy` or a `restore`, who asked (`owner` or `policy`), the run, candidate, and TMDB file it came from, the job (unique: a job writes at most one row), the file's name, the new file's SHA-256 and size (null when the write removed the poster), the previous poster's SHA-256 and origin (null when there was none), its backup's name relative to the backups directory, and for a restore the row it undoes |
| `preference_events` | Append-only (`created_at` only; no code updates or deletes a row): one act of feedback (`approval`, `override`, `rank`, `reject`, `revoke`) on a movie's run (set null if the run is deleted). It keeps its own copy of what was shown, because candidate rows go with their run: `exposed`, one object per shown candidate (TMDB's file name as `candidate_id`, its SHA-256, `baseline_rank`, `baseline_score`, `stack`, and `normalized_features` from the archive), at most 100; `context` (`order`, `hated`, `selected_candidate`, as file names); the chosen and the picked file, the chosen file's hash, the run's profile hash and scorer, and `blind` (null: unknown). A `revoke` names the event it revokes (unique). `idempotency_key` is unique, 1 to 160 characters |
| `exemplars` | A poster the taste profile is built from: the movie, `positive` or `negative`, a weight in (0, 1], the source (`seed`, or `feedback` with its event and TMDB file), the state (`active`, `superseded`, `revoked`, when it ended, and the act of feedback that ended it, `ended_by_event_id`: null while active, and for rows ended before `0005`), the kept image's SHA-256 and name in `data/ml/exemplars/`, the name the trainer sees (`staged_name`, positives only), and the exemplar it replaced. Partial unique indexes allow one active positive per movie and one active row per movie, polarity, and image |
| `residual_trainings` | Every training of the residual, whatever came of it: the rebuild job (or null from the command), the profile it was trained for (id and SHA-256), the engine's outcome (`activate`, `no_change`) or `failed`, the reason, movies, pairs, events, the evidence digest, and the engine's report (JSONB). A row that activated keeps its artifact's SHA-256 and name (`<sha256>.npz` in `data/ml/residuals/`); a partial unique index allows one active row, and `deactivated_at` says when it stopped |
| `evaluations` | One blind evaluation: its batch's parent job (unique), the active profile and residual artifact when it was queued, the scorer the runs were expected to use, and how many movies it asked for and queued. Its state is its batch's |
| `evaluation_results` | One row per evaluation and movie (unique): the exemplar and its source (`seed` or `feedback`), the masked run (set null when a newer one replaces it; the numbers stay), the outcome (`ranked`, `gated`, `not_offered`, `failed`), how the match was found (`file` or `cosine`), its file, hash, and cosine, the closest candidate and its cosine, how many candidates had no cached embedding, rank of ranked count, card of cards, the pick's file, whether the pick is the match and whether it is the same design, and a gate's stage and reason |
| `taste_profiles` | Every profile built or registered: its SHA-256 (unique), the exemplar revision it was built from, `seed` or `rebuild`, its positive and negative counts, the movie ids of its positives (sorted; what makes a run blind), its copy's name in `data/ml/profiles/`, the job that built it, and whether it is the active one (a partial unique index allows one) |

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
`media_read` 2, `media_write` 1, and `maintenance` 1 jobs of each at once (`maintenance`, the
database dump and retention, from migration 0007). It claims the highest-priority,
oldest queued job whose class has a free slot, with `FOR UPDATE SKIP LOCKED`, and runs it as
a task with its own database sessions, a cancel signal (the `is_set()` shape of
`marquee.cancellation`), a progress function that writes at most about once a second, and its
kind's timeout.

| Kind | Class | Attempts | Timeout | Does |
|---|---|---|---|---|
| `library_sync` | network | 3 | 15 min | One Radarr sync (`library.sync.sync_from_settings`). Another sync holding its lock is a success marked skipped; folders that do not map or do not exist are a permanent error, while a share or Radarr that did not answer in time is an ordinary failure, retried with backoff. One active job at a time (dedupe key) |
| `gpu_selftest` | gpu | 1 | 5 min | In the GPU child, loads CLIP as the doctor does (once per child) and embeds one batch; reports the provider, the time, and whether the session was already loaded |
| `poster_run` | gpu | 2 | 10 min | One movie (`{"movie_id": N}`) through the pipeline, with its run record (see [Runs](#runs)). One active run per movie (`poster_run:<movie id>`) |
| `poster_run_masked` | gpu | 2 | 10 min | The same run against the active profile without the movie's own exemplars (`{"movie_id", "evaluation_id"}`), recorded as masked, and the evaluation's result for the movie (see [Blind evaluation](#blind-evaluation)). Its own dedupe key (`poster_run_masked:<movie id>`), so it and a normal run never swallow each other |
| `poster_deploy` | media_write | 3 | 5 min | Writes a candidate's original into the movie folder (`{"candidate_id", "initiated_by", "movie_id"}`, and `event_id` when feedback asked for it); see [Writing a poster](#writing-a-poster). No dedupe key: the newest write request for a movie wins |
| `poster_restore` | media_write | 3 | 5 min | Puts back what was in the folder before a deployment, any deployment of the movie (`{"deployment_id", "movie_id"}`); the newest write request wins here too |
| `db_backup` | maintenance | 3 | 2 h | Dumps the database (`maintenance.dumps.make_dump`) to `backups/db/marquee-<UTC time>-<revision>.dump`, checked with `pg_restore --list` before it is renamed into place, then keeps the newest `DB_BACKUP_KEEP` scheduled dumps. Its result: the file's name, size, SHA-256, schema revision, seconds, and what was deleted. Missing or old client tools are a permanent error; anything else is retried. One active job (`db_backup`) |
| `retention` | maintenance | 2 | 30 min | Deletes what the retention rules name (see [Retention](#retention)); its result holds counts and bytes per rule, never a path. A data folder that overlaps a movie root is a permanent refusal. One active job (`retention`) |
| `taste_rebuild` | gpu | 2 | 15 min | Builds the taste profile from the active exemplars and publishes it (`{"revision", "force"}`), then trains the residual for it; see [Review and learning](#review-and-learning). One active rebuild per exemplar revision (`taste_rebuild:{revision}`) |

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
the GPU memory it holds. The child's resident memory also grows with every run (about
150 MiB, without levelling off), so when a GPU job ends and the child holds more than
`GPU_CHILD_MAX_RSS_MB` (default 4096; 0 turns it off; read from `VmRSS` in
`/proc/<pid>/status`), the worker stops it the same way and logs the size; the next GPU job
starts a new one. The check runs while the finished job still holds the gpu class's slot, so
it never happens during a job.

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
table holds only when each is next due. A schedule seen for the first time is due at once,
unless it is declared to wait one whole interval first. When one is due, its job is queued
under a dedupe key and its due time moves on by the interval, in one transaction, so a
schedule never has two jobs active and a long stop does not queue a burst. Three schedules:
the library sync, every `SYNC_INTERVAL_MINUTES` (default 360); the database dump,
`db_backup`, every `DB_BACKUP_INTERVAL_HOURS` (default 24); and `retention`, every
`RETENTION_INTERVAL_HOURS` (default 24), which waits one interval after it is first seen, so
an updated installation deletes nothing before its owner has looked. 0 turns any of them
off. With `RUN_NEW_MOVIES` true, a sync job
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
   TMDB id, the three text-gate fields from the `movies` table, and the active profile's and
   active residual artifact's identity (id, SHA-256, file name). In the child,
   `jobs/handlers/gpu.py::poster_run` hands the engine those fields by the names it reads
   (`production_companies_json` for the table's `production_companies`), builds the residual's
   runtime context when an artifact is active (namespace `movies`, the scorer weights'
   signature, the profile's checksum and id, the artifact's id and checksum), runs
   `pipeline/single_run.py::run_single_subject` on the child's one event loop with the
   artifact's path and that context, and returns a small result: the status, counts, stage
   timings, the scorer the engine says ranked, engine run id, the taste profile's SHA-256, and
   the pick's file name. The engine decides whether the artifact ranks (`SCORER=auto` uses a
   compatible one, `weighted` never does) and otherwise falls back to the weighted scorer with
   a log line. The archive stays on disk.
4. Back in the worker it reads `pipeline_run.json` off the event loop, hashes each downloaded
   candidate (the images at the top of the run folder, one per candidate, named by TMDB's file
   name), and `runs.complete` stores the candidates and marks the pick in one transaction. The
   run is recorded as ranked by `residual` (with the training's id) only when every ranked
   candidate's contributions in the archive carry the residual's; the engine can fall back part
   way through a run, and then the run says `weighted`.

A cancel that was asked for marks the run cancelled and removes its folder; a stopping worker
puts the job back instead, and its next attempt starts the run over. A failure marks it failed
with the one-line error and keeps the folder for its log. A completed run is never run again:
when an attempt starts and the job's run already completed (the worker died between completing
it and finishing the job), the handler returns the recorded result and changes nothing, since
feedback and deployments point at its candidates.

**The pick is the engine's recommendation:** the highest-scoring candidate, rank 1. Stacks are
how designs are shown, not what is deployed. A run of a movie whose own poster seeded the taste
profile picks that poster, because the poster's exemplar is its candidate's nearest neighbour.

**Batches.** `enqueue_scope` builds a list of eligible movies and queues one batch of runs:
`never-run` (no run record of the product loop), `no-poster` (no `artwork` row), or `all`. A movie whose run is
already queued or running is left out by its dedupe key.

**The embedding cache** (`data/cache/embeddings/<model>/<sha256>.npz`) is keyed by the model
name and the SHA-256 of the candidate file's bytes, and each entry records that hash; TMDB
serves different bytes under one file name, and a name key would pair an embedding of one
encoding with features measured on another.

### The ranking weights

The weighted scorer adds each ranked candidate's normalised features, each times its
`WEIGHT_*` setting in `config/pipeline.py`, and divides by the total weight of the features
that candidate has. The defaults were fitted on 2026-11-25 from the run archives of the
fourth blind evaluation (147 movies whose poster the owner picked by hand, with each
movie's own exemplars masked): a pairwise logistic fit of the owner's poster against
posters of other designs, pulled towards the earlier weights, trained on four fifths of the
movies and measured on the other fifth, over six shuffles. Held out, the pick is the
owner's design about 73 times in 147, against 53 with the earlier weights; the owner's
design is in the first three cards about 107 times, against 93.

| Feature | Weight | Earlier |
|---|---:|---:|
| `knn_sim` (taste similarity) | 0.20 | 0.30 |
| `face_area` | 0.19 | 0.15 |
| `taste_typicality` | 0.18 | 0.12 |
| `official_family` | 0.18 | 0.12 |
| `text_residual` | 0.14 | 0.10 |
| `aesthetic` | 0.10 | 0.12 |
| `quality_artifacts` | 0.10 | 0.03 |
| `sharpness` | 0.06 | 0.03 |
| `title_colorfulness` | 0.04 | 0.15 |
| `provenance` | 0.04 | 0.02 |
| `dino_knn` | 0.03 | 0.12 |
| `resolution`, `lang_match` | 0 | 0 |

They sum to 1.26, as the earlier ones did. Taste similarity stays the largest weight on
purpose: it is how feedback reaches later picks, and a blind fit undervalues it because a
masked run has the movie's own exemplar removed. Since the fit used the hand picks, a
later blind evaluation is in sample by about four picks. Any weight can still be set in
`.env` by name.

### The text gate

`pipeline/ocr_filter.py` keeps a candidate only when it shows the movie's title and nothing
else (the `title_only` text profile). It reads the candidate at the working size, 500 px wide,
with PaddleOCR: the whole poster, the top and bottom strips at twice their size, a
contrast-boosted copy when nothing was read, and a low-threshold title recovery when nothing
read matches the title. A box whose text matches the title (whole, a word of it, a fuzzy or
letter-spaced read) is title evidence; every other box is leftover text. One significant
leftover box (a word of four letters or more that is not the title's, or a box past a size
limit) rejects the poster as `text_heavy`; a poster where no title was found is rejected as
`no_title`, or `no_text` when nothing was read at all. The title's words are matched in a fixed
order (sorted, and each word once in reading order when scores are summed), so the same reader
answers give the same decision and trace in every process, whatever Python's hash seed.

The reader is built in `_load_ocr`, once per OCR worker: PaddleOCR 3.6.0 with the
`PP-OCRv5_mobile_det` detector and `PP-OCRv5_server_rec` recogniser, and text-line
orientation on (a single line set vertically is read through it). PaddleOCR's **document
preprocessor is off**. It is made for scanned pages: it classifies the whole image as upright
or turned and turns it by 90°, 180°, or 270°, then unwarps it as if it were a photographed
sheet. On posters the classifier turned about a third of them before reading, so their text
boxes came back in the turned image's coordinates, every rule about size and position saw the
wrong place, and the title often went unread; the unwarping bent every poster, turned or
not. With it off the reader reads the poster as it is and every box is in the poster's own
frame. It is a constant, not a setting (`OCR_DOCUMENT_PREPROCESSOR` in `config/pipeline.py`,
`False`), so two coordinate frames never live side by side, and the run archive reports it as
`config.ocr.document_preprocessor`; an archive without that field was made by the reader
before the change. The reader opens three model folders under `data/models/paddlex/`
(detection, recognition, text-line orientation). An installation from before the change also
holds the preprocessor's two folders (`PP-LCNet_x1_0_doc_ori` and `UVDoc`); nothing opens them.

Three rules, each behind a switch, recover title-only posters the reader gets wrong. The first
and the third are on by default, the second off:

- **Smaller copies** (`OCR_SMALL_COPY_RETRY`). When every pass ends with no title found and the
  poster is rejected, the reader reads the whole poster again on copies at 60% and 40% of its
  size, made from the 500 px image, at the title recovery's confidence threshold
  (`OCR_TITLE_RECOVERY_CONFIDENCE_THRESHOLD`). Large or letter-spaced titles that come back
  empty or as one stray letter at 500 px are often read on a smaller copy. The boxes are mapped
  back to the poster's own coordinates and merged with what was read before, and when the
  merged reads hold the title the poster is decided again by the same rules; otherwise it
  keeps its reason. A poster where a title was found, an accepted poster, and a poster with a
  format word never get these passes.
- **Title pieces** (`OCR_TITLE_PIECE_RULE`). When a poster with a title found is rejected for
  leftover text, a leftover box counts as part of the title when it sits with a box that has
  title evidence (on its line, or directly above or below it while overlapping it), its glyphs
  are about as tall as that box's and not far below the poster's tallest title box, three
  quarters of its letters are letters of the title, and it holds no more letters than the
  title does. Glyph height and letters together carry the rule: a studio mark or a credit
  line beside a title shares many of its letters but is set smaller, and a small subtitle
  line does not take in a tagline set at its own size. An accepted poster is never looked at
  again. The bounds are constants beside the rule, each saying what it was tuned on. The rule
  is off by default: on a blind sample, measured before the reader's document preprocessor
  was switched off, 72% of the posters it let through held only their title, against a bar of
  90% (actor names, character names, and studio marks set at a title line's size). With the
  reader fixed, 80% of a sample of what it would let through held only their title; the
  misses were of the same kinds. Set `OCR_TITLE_PIECE_RULE=true` to run it.
- **Second reads** (`OCR_SECOND_READ_RULE`). The passes read a big title more than once, and
  `_dedupe_boxes` drops a box only when another one has the same text. A second read that comes
  back as other text ("NATT" lying on the RIVER of WIND RIVER, read by the bottom strip; "si"
  and "ns" inside "signs") is not the title, so it was left over and rejected the poster. When
  a poster with a title found is rejected for leftover text, a leftover box that counts toward
  rejection is set aside as a second read when it was read by another pass than a box with
  title evidence, at least 75% of its own polygon lies inside that box's polygon, its glyphs
  are at least a fifth as tall as that box's, and the gate's classifier does not call it a
  studio mark or a director's credit; a box whose polygon is in a turned frame takes no part.
  Overlap is measured on the polygons, never on their bounding rectangles: the rectangle of a
  slanted title covers the credit line above it. Marks, credits, and names often lie inside a
  title box's slack, read by a strip while the title came from the whole poster; they are set
  much smaller than the title, which the height bound uses. A box set aside stops counting
  toward rejection and nothing else: it is not title evidence, so the title box, the title
  geometry, and the taste profile's title rows do not change, and it stays among the leftover
  boxes the ranking's `text_residual` feature counts. An accepted poster is never looked at
  again. The bounds were tuned on half of a labelled set drawn from what a loose form of the
  rule admits among the third blind evaluation's `text_heavy` rejections (133 posters in 57
  movies, labelled by eye) and measured on the other half: 42 of the 46 posters it let through
  there held only their title (91%, against a bar of 90%). On the session E5 labelled set it
  accepts six more title-only posters and rejects none that was accepted. Set
  `OCR_SECOND_READ_RULE=false` to switch it off.

With every switch off each decision and the OCR trace are what they were before the three
rules. With one on, the trace says what happened: `passes_run` lists `small_60` and `small_40`
when they ran, a `small_copy_retry`, a `title_piece_rule`, and a `second_read_rule` block say
whether each rule ran and what it found, and every region carries `is_title_piece` with the
title-piece rule on and `is_second_read` with the second-read rule on. The run archive then
keeps every candidate's regions, accepted ones too, each with the pass that read it, whether
it counted as a piece of the title, and whether it was set aside as a second read.

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
5. What the write is about to do is kept beside the staged original,
   `data/cache/deploys/<job id>.json` (written under a temporary name and renamed): the movie,
   the new hash, and the previous poster's hash, origin, and backup. Then the new bytes go to a
   temporary file in the same folder, flushed and synced, and are renamed over the target (the
   replaced file's mode is kept; a new one gets 0644). A failure removes the temporary file.
6. The target is read back and its hash compared; if it differs, the previous poster is put
   back and the write fails loudly.
7. One transaction, under the sync's advisory lock so a sync in progress cannot overwrite the
   change with what it saw, updates the `artwork` row (origin, hash, size, and the
   modification time the file has now) and inserts the `deployments` row.

Every step is safe to repeat. A job that already wrote its `deployments` row is done. When
step 3 finds the new bytes in place because an earlier attempt renamed them and died, the
previous poster is the one that attempt's intent names, not the `artwork` row, which a sync may
have rewritten in between (it records what it finds: the new bytes, as the library's); its
backup must exist. The intent is removed once the write is recorded. A refusal no retry can
change (a `poster.jpg` that is a symbolic link or not a regular file, a movie folder that is
missing or a link, a path outside the roots) fails the job on its first attempt; a read-back
mismatch or an I/O error retries. When
the row and the newest deployment already describe the new bytes, nothing is added and the
result says the poster was already deployed; a deploy of the candidate already deployed does
not even fetch it again.

**The newest write request for a movie wins** (`jobs/handlers/poster_write.py`). A deploy or
a restore is always queued, except a request identical to the movie's newest write job (same
kind and payload) while that job is still queued, which is returned instead; requests for one
movie are queued one at a time under a transaction-level advisory lock. A deploy that feedback
asks for carries its event's id, so each act of feedback names its own deploy. When a write
job starts, first attempt or retry, and another write job of the same movie wins over it, it
succeeds with `skipped: superseded by job N` and writes nothing, unless it already wrote its
row (it is done) or kept an intent (it may have renamed its file into place, so it records
that write first and the winner writes over it). Two write jobs still never run at once: the
class allows one.

**An owner's request always beats the deploy policy.** Every restore and every deploy the
owner asked for is the owner's; a deploy the policy queued is the policy's. An owner's job
steps aside only for a newer owner's job that is queued, running, or succeeded. A policy
deploy steps aside for any newer write job, and also for any owner's job of the movie,
whatever its id, that is queued or running, or that succeeded after the policy deploy was
queued. The worker runs the older job first, so when the owner picks and a run's policy deploy
is queued just after, the owner's job writes and the policy's then steps aside instead of
writing the engine's pick over it. A policy deploy queued after the owner's write had
finished (a later run's) still deploys under the policy.

**Restore** works on any deployment of a movie, not only the newest: restoring deployment N
backs up what is in the folder now, whatever it is, and puts back what was there before N
(from its backup, with its origin), or removes the poster when there was none, and records a
`restore` row naming N. Restoring the same deployment twice in a row writes nothing the
second time. A restore is itself a deployment, so it can be undone in turn.

**Deploy policy.** Nothing deploys by itself unless `DEPLOY_POLICY` says so. When a run
completes with a pick: `off` (the default) queues nothing; `missing` queues a deploy only when
the movie has no poster or its poster is the one the policy itself deployed last; `all` queues
one whatever is there. A deploy the policy queued accepts only the pick, and checks the policy
again when it runs: under `missing` the file in the folder must be absent, or be the file the
policy deployed last (the movie's newest deployment was initiated by `policy`, its
`new_sha256` is the file on disk, and the `artwork` row still says origin `marquee` with that
hash), or the job finishes as skipped and writes nothing. So a poster the owner chose in a
review (an owner's deploy), put back (a restore), replaced by hand, or already had is never
replaced by `missing`. A deploy the owner asks for
accepts any downloaded candidate of a completed run.

**The sync and Marquee's posters.** A poster row of origin `marquee` is kept, hashed again, or
deleted the way a library row is; when its bytes changed, the owner replaced it by hand, so it
becomes origin `library` with the new hash and the sync's report counts it. A sync never
touches `deployments`. `marquee taste seed --rescan` is refused once any deployment exists.

## Review and learning

The loop: a run picks (rank 1), the pick may be deployed, the owner reviews the run, and the
review changes the exemplars, which a rebuild turns into the next taste profile.

**Exemplars are records.** `marquee taste import-seed` makes each poster the seed staged
(`data/ml/seed/movies.json`, matched to its movie by IMDb id) an active positive with source
`seed`, keeping a copy of the staged PNG in `data/ml/exemplars/<sha256>.png`, and registers the
profile file built from them as it is (a `taste_profiles` row, source `seed`, active, with a
copy in `data/ml/profiles/`). It writes nothing when an entry matches no movie or the profile
does not hold exactly the listed files. From then on, the profile is built from the records.

**Feedback** goes to a movie's latest completed run only, and each act is one
`preference_events` row (`marquee/taste/feedback.py`):

| Action | Chosen | Positive exemplar | Negative exemplars | Deploys (unless told not to) |
|---|---|---|---|---|
| `approval` | the run's pick | weight 0.75, only when the movie has no active positive | none | the pick |
| `override` | the candidate named | weight 1.0, replaces the active positive | none | that candidate |
| `rank` | first of the order | weight 1.0, replaces the active positive | each hated candidate ranked `FEEDBACK_HARD_NEGATIVE_RANK_MAX` (10) or better | first of the order |
| `reject` | none | none | the same, from every shown candidate when none is named | nothing |
| `revoke` | (undoes one earlier event) | its exemplars are revoked, then what revoked events ended comes back, as far as the rules allow (below) | | nothing |

An override that names the pick is recorded as an approval. A hated candidate whose bytes are
the movie's active positive ends that positive. A movie has at most one active positive (a new
one supersedes it) and 8 active negatives (a ninth ends the oldest, never one the same event
made). When one event hates more hard negatives than that, the best-ranked 8 become negatives
and the rest make nothing. Every exemplar that feedback ends records the event that ended it.
A revoke revokes the exemplars its event made; then a superseded exemplar of the movie whose
ending event is revoked is eligible again: the newest eligible positive becomes active if the
movie has none, and eligible negatives become active, newest first, up to 8. The revoke event
keeps the ids it brought back in its `context` (`reactivated`), so a replayed revoke answers
the same. Each new exemplar keeps a
copy of the candidate's 500 px file from the run folder, checked against its recorded hash. A
positive's `staged_name` is `<title> (<year>)<suffix>` from the movies table, with `/` replaced:
the trainer reads the title for OCR from it. An exemplar is active as soon as its event is
recorded, whatever happens to the deploy. A key already used returns the event it made and
changes nothing. A revoke leaves the library alone; restoring a deployment is how a poster is
put back. `marquee/jobs/review.py` records the feedback, queues the owner's deploy and, when
the exemplars' revision changed, a `taste_rebuild`, and commits once.

**The revision** is a SHA-256 over the active exemplars (polarity, image hash, weight to six
places, staged name), sorted: two sets that would build the same profile share it.

**A rebuild** (`taste_rebuild`, `marquee/jobs/handlers/taste_rebuild.py`) succeeds as skipped
when its revision is no longer the current one, or when the active profile already has it and
`force` is false. Otherwise it stages the active exemplars' kept images (each checked by its
hash) into `data/cache/taste-build/<job id>/positive/<staged_name>` and
`negative/<sha256>.<ext>`, and the GPU child runs `taste/trainer.py::rebuild_profile` on them
as it is, with both weight maps. It refuses for good below `TASTE_SEEDING_MIN_POSTERS` active
positives, or when two active positives share a staged name. The new file must load and hold
exactly what was staged. Publishing: a copy in `data/ml/profiles/<sha256>.npz`, a marker in the
build folder, then a copy beside `TASTE_PROFILE_PATH`, synced and renamed over it, then one
transaction that makes the new `taste_profiles` row the active one. The file goes first: a
crash before the row leaves a complete profile in place that the doctor reports as not the
registered build, and the job's next attempt publishes the kept build without building again.
`TASTE_PROFILE_PATH` always holds a complete profile, the old one or the new one. The build
folder is then removed, and only the five newest profile copies, the seed's, and the active
one are kept. A run queued before a rebuild uses the profile that was in place when it
started; `poster_runs.profile_sha256` says which.

**Blind.** A run is blind when its movie had no positive exemplar in the profile it used (the
`taste_profiles` row with the run's `profile_sha256`), and "unknown" when that profile is not
registered. A movie's own exemplar makes its run pick the owner's earlier choice, so only blind
runs measure taste.

**Agreement** (`marquee/taste/agreement.py`) is counted over events that are not revoked, on
blind runs, the first such event of each run: agreed when the chosen file is the run's pick, in
the top 3 when the chosen candidate ranked 3 or better in the run (an exposed row's `rank`;
events from before M5c have only `baseline_rank`, which was the same then); a `reject` is
reviewed and neither.
Reviews of runs that were not blind, or unknown, are counted apart. Counts are always given
("3 of 4"), a percentage only from 10 reviews, in total and by profile and month.

**The residual** (`marquee/taste/residuals.py`) is a bounded correction to the weighted score,
learned from preference events only: the hand-picked seed posters never train it, so the blind
evaluation stays a clean measurement of it. Its life:

1. **Evidence.** `feedback.residual_events` gives every stored event in the shape the engine
   reads (a revoked event names the revoke that revoked it, and gives no pairs); the engine's
   `freeze_residual_evidence` fixes their order and digest, and `build_residual_pairs` turns
   them into pairs within a movie.
2. **Training**, at the end of every rebuild that published a profile (inside that job; a
   training that fails or raises never fails the rebuild, and the job's result says what it
   did) and by `marquee taste residual train`, one at a time under a transaction lock. The
   engine's `train_residual` gets the namespace `movies`, the scorer weights' signature, the
   active profile's SHA-256 and id, the evidence digest, `RESIDUAL_MIN_SUBJECTS` (25) and
   `RESIDUAL_MIN_PAIRS` (200), and the active artifact when there is one. It splits by movie,
   and activates only on a held-out improvement of 0.02 or more and never below the active
   artifact. Nothing here adds a gate or changes a threshold.
3. **Keeping.** Every training is a `residual_trainings` row. An activation saves the artifact
   under its SHA-256 in `data/ml/residuals/`, writing a pending record beside it first, then
   commits its row as the active one; the next training records a pending record a crash left
   behind. The five newest artifact files and the active one are kept.
4. **Binding.** An artifact is bound to the profile it was trained for: the transaction that
   registers another profile makes the active row inactive, and the engine would treat it as
   dormant anyway. Training straight after each rebuild is what keeps one active.
5. **Use.** Runs pass the active artifact to the engine (see [Runs](#runs)); a masked run
   passes the same artifact with the active profile's identity. `SCORER=weighted` is the off
   switch; no button or route trains, activates, or switches it off.

**How the engine trains it** (`ml/residual.py::train_residual`). Scoring
(`score_residual_candidate`, at run time and in the held-out check) is the reference: a
candidate's correction is `weights · features + bias`, clamped at `delta_max` (0.75), then
multiplied by `alpha` (0.5) and added to the baseline's logit. The trainer trains exactly
that. It centres the features on the mean of the training partition's distinct candidates
(a candidate is one movie's one feature vector, counted once however many pairs it is in;
never the validation or test movies), and the artifact's bias, `-(weights · mean)`, carries
the centring into scoring. A pair's logit is the baseline's logit margin plus `alpha` times
the difference of the two clamped corrections, the weighted logistic loss is minimised from
zero weights, and a candidate whose correction is at the clamp passes no gradient. The
held-out check scores with the trained weights and bias. The report holds the weights' feature
names, the `bias`, `feature_means`, and each held-out partition's metrics, including
`clamped_share` (the share of candidates whose correction is at or beyond the clamp). An
owner who agrees with the baseline can get a lower held-out accuracy with a residual; the
activation rules refuse it. (Until M5d the trainer clamped the difference between a pair's
corrections instead, so with real feature values a trained residual changed nothing.)

**The baseline stays the weighted scorer's.** An event's `exposed` rows carry `baseline_rank`
and `baseline_score`, which the trainer reads as the weighted scorer's. For a run the residual
ranked they come from the archive's contributions (`baseline_score`) and the rank by that
score, not the run's own, or each training would learn from the one before; feedback on such a
run whose archive lacks them is refused. Each row also keeps the run's own `rank`.

### Blind evaluation

A blind evaluation measures where the owner's poster lands when the pipeline runs without it.

**A masked run** is a run of one movie against the active profile with every active exemplar of
that movie left out, positives and negatives. The mask is a derived profile file
(`marquee/taste/blind.py`): the GPU child copies the active profile's kept file without the
movie's rows (every per-exemplar array, from a list of the arrays the trainer writes, refusing
one it does not know), recomputes the centroid with the trainer's `compute_centroid`, keeps it
at `data/cache/blind-profiles/<profile sha256>-<movie id>.npz` while the run lasts, and deletes
it whatever happens. The unchanged taste store loads it. The other exemplars' DINO self-kNN
values and calibration samples stay as built, an accepted approximation. The run's
`profile_sha256` is the active profile's.

**A masked run is never part of the product loop.** One condition,
`db/models/run.py::in_product_loop`, decides what "the movie's latest run" means: the review
queue, the run feedback goes to, the Library pages' latest run and has-run filter, `never-run`
batches, and the runs list (by default) use it. Feedback on a masked run and a deploy from one
are refused with the reason, and the deploy policy never acts on one. It can be opened by id,
read-only, and its candidates' images are served. `feedback.blind_for` says a masked run is
blind. A movie keeps only its newest masked run: when one completes, the movie's older masked
runs are deleted, rows and folders; a normal run is never touched.

**Queueing.** `POST /api/taste/evaluations` and `marquee taste evaluate` queue one batch of
`poster_run_masked` jobs: every movie a run accepts that has an active positive exemplar, or the
movies named. One evaluation at a time (409 while one runs), and none while a rebuild is due:
the mask names the movie's rows of the profile by the active exemplars, so they must agree.

**The match.** The owner's choice for a movie is its active positive exemplar. When the
exemplar has a TMDB file name (feedback exemplars do) and the run has that candidate, that is
the match (a candidate removed as a duplicate leads to the one kept). Otherwise the exemplar's
row in the profile is compared with each candidate's CLIP embedding from the embedding cache
(keyed by model and the file's SHA-256; nothing loads a model), and the closest candidate is
the match when its cosine is at least `EVAL_MATCH_MIN_COSINE` (0.95). A candidate rejected
before the style stage has no embedding and is counted, not guessed.

**The outcome** of each movie, written by its masked run's job: `ranked` (rank, of how many,
and card, where cards are design stacks in the order the Review page shows them; whether the
pick is that exact candidate and whether it is the same design), `gated` (the stage and
reason), `not_offered` (the closest candidate's cosine is kept), or `failed` (the run did not
complete, or it ran against another profile than the evaluation names: a review answered while
an evaluation runs queues a rebuild, and runs that start after it publishes measure the new
profile; such a result says "the taste profile changed while the evaluation ran"). **The summary** is computed from the rows whenever asked, for seed exemplars (the
headline: posters the owner picked by hand with nothing shown first), feedback exemplars, and
both: evaluated, ranked, gated by stage and reason, not offered, failed, and over the ranked:
exact, same design, first 3 and first 5 cards, the median card, and the mean reciprocal card.
A cancelled batch's runs that never ran are counted as not run.

## Database dumps

`marquee/maintenance/dumps.py`. A dump is `pg_dump`'s custom format of the whole database,
in `backups/db/` under the data folder: `marquee-<UTC time>-<revision>.dump` from the
`db_backup` job, `manual-<UTC time>-<revision>.dump` from `marquee db backup`, and
`pre-restore-<UTC time>.dump` from a restore. The tools come from PostgreSQL's own apt
repository in the image (see [Container image](#container-image)); before every dump and
restore the code checks that each is the server's major version or newer and says which is
not. The connection reaches each tool through libpq's environment (`PGPASSWORD` among it),
never its arguments, and nothing else of the process's environment is handed on; whatever a
tool prints is scrubbed of every secret, connection-string credentials, and `password=`
words before it is shown or logged.

A dump is written under a temporary name in the same folder (mode 600; the folder is 700),
read back with `pg_restore --list`, and only then renamed into place; a dump that fails or
does not read back leaves nothing. The job keeps the newest `DB_BACKUP_KEEP` scheduled dumps,
a restore the newest three pre-restore dumps, and manual dumps are never deleted.

**Restore** is a command only, never a job or a route. It refuses unless `--yes` is given,
the file reads as a dump of a schema revision this code knows, no worker holds the queue's
lock, and no other session is connected to the database. It dumps the database as it is
first (skipped when it has no tables), checked like any dump. Then `pg_restore` writes the
whole dump out as one SQL script to a private temporary file and must succeed, and `psql
--single-transaction` runs, in one transaction, a block that drops every table, view, and
sequence in the `public` schema followed by that script; any error rolls it all back.
(Streaming one tool into the other would let `psql` commit a partial restore when
`pg_restore` stopped part way at the end of a statement; dropping the schema's objects
first means a newer migration's tables do not survive a restore of an older dump.) When the
dump's revision is older than the newest migration, the restore then upgrades the schema.

## Retention

`marquee/maintenance/retention.py`, run by the `retention` job and `marquee retention`. One
pass plans what each rule would delete and, unless it is a dry run, deletes exactly that,
checking each item again just before it goes.

| Rule | Deletes |
|---|---|
| jobs | A top-level job finished more than `RETENTION_DAYS` ago, with its children and their events (they cascade). Never a queued or running job, a batch with a child that is not old enough, a job an evaluation names or its children (the evaluation's state and the Taste page's headline are read from them), or a schedule's `last_job_id` |
| runs | A run (row, candidates, folder, through `artwork.runs.delete_runs`) older than `RETENTION_DAYS` (by `finished_at`, else `started_at`), not running, not its movie's newest run or newest completed run in the product loop, not named by a deployment or a preference event, and not a masked run of the newest evaluation or the newest that succeeded. A deployment's or result's link to a deleted job or run becomes null; the row stays |
| run_folders | A folder in `runs/` that no `poster_runs.run_folder` names, untouched for 7 days |
| thumbnails, embeddings | A file in `cache/thumbs/` untouched for 30 days; in `cache/embeddings/`, for `RETENTION_DAYS` |
| leftovers | Anything in `cache/deploys/`, `cache/blind-profiles/`, `cache/taste-build/`, a dump's or restore's temporary file in `backups/db/`, a `.part` file in `ml/profiles/` or `ml/residuals/`, and `text_profiles.json.tmp`, untouched for 24 hours and owned by no queued or running job (by the job id in its name where there is one, else by kind). A residual's `.pending-<sha256>.json` goes only once a `residual_trainings` row records that artifact; until then the next rebuild reads it, so it is kept and the report says so |
| profiles, residuals | A copy in `ml/profiles/` or `ml/residuals/` that is not the active one, not among the newest 20 rows, and (profiles) not the seed's, or (residuals) not named by a pending record. Rows stay |

"Untouched" is the modification time; for a folder, the newest of anything in it. Never
deleted: `backups/posters/`, deployments, preference events, exemplars and their images,
evaluations and results, `ml/seed/`, models, the kernel cache, dumps, anything outside the
data folder, and anything in it the rules do not name. Every file deletion goes through
`marquee/fs/boundary.py` with the data folder as its root (run folders with the runs folder
as theirs), so nothing outside it, and no symbolic link, is followed. The pass refuses to run
when the data folder is inside a movie root or a movie root is inside it. The job's result
holds counts, rows, and bytes per rule; paths are logged at DEBUG only. The schedule waits
one whole interval after it is first seen.

## The HTTP API

`marquee/api/app.py` builds the app; its lifespan creates the database engine and disposes
of it. `marquee api` serves it under uvicorn.

| Route | Key | Answers |
|---|---|---|
| `GET /health/live` | no | the process is up |
| `GET /health/ready` | no | the database answers and its schema is the newest migration; 503 otherwise |
| `GET /api/movies` | yes | one page of movies with a file (`include_unavailable` adds the ones Radarr wants), with `q` (case-insensitive title search), `has_poster`, `origin` (`library` or `marquee`: whose poster it is), `has_run` (any run record), `sort` (`title`, `year`, `added`), `page`, and `page_size` (1 to 200). A movie's `poster` carries `version`, the first 12 hex of its hash |
| `GET /api/movies/{id}` | yes | one movie; 404 when absent or retired |
| `GET /api/movies/{id}/poster` | yes | the poster file, with its SHA-256 as the ETag; 404 when there is no poster row or the file is gone or changed. With `width` (160, 320, or 640; 422 otherwise) a JPEG thumbnail, never enlarged, ETag `"<sha256>-<width>"`, made once from the library file (whose bytes must hash to the row's) and kept in `data/cache/thumbs/`; 409 when the poster does not decode. With `v` equal to the poster's current `version`, the answer is `private, max-age=31536000, immutable`; otherwise `private, no-cache` |
| `GET /api/jobs` | yes | one page of jobs, newest first, with `state`, `kind`, and `parent_id` filters and paging as for movies. Every job answer, here and wherever a route returns a job, carries `subject`: the movie its payload names (id, title, year), or null; the movies of a page come from one query |
| `GET /api/jobs/events` | yes | events of every job after the event id `after`, oldest first (`limit` 1 to 500), and the cursor for the next poll |
| `GET /api/jobs/{id}` | yes | one job; 404 when absent |
| `GET /api/jobs/{id}/events` | yes | one job's events, oldest first |
| `POST /api/jobs/{id}/cancel` | yes | cancels a queued job, or asks a running one to stop; 409 for a finished job |
| `POST /api/jobs/{id}/retry` | yes | queues a failed or cancelled job again; 409 otherwise |
| `POST /api/sync` | yes | queues a library sync: 202 with the new job, or 200 with the one already queued or running |
| `GET /api/worker` | yes | whether a worker is running, and how many jobs are queued, running, and failed |
| `POST /api/movies/{id}/run` | yes | queues a run of the movie: 202 with the job, 200 with the run already queued or running; 409 when the movie cannot be run |
| `POST /api/runs/batch` | yes | `{"scope": "never-run"\|"no-poster"\|"all", "limit": N}` or `{"movie_ids": [...]}`: 202 with the batch's parent job and how many runs were queued; 422 names movies that cannot be run |
| `GET /api/runs` | yes | one page of runs, newest first, with `movie_id`, `state`, and `masked` (`false` by default, `true`, `any`) filters |
| `GET /api/runs/{id}` | yes | one run and its candidates, ranked ones first, whether it was blind, whether it is reviewed, whether it is masked, and the residual training that ranked it |
| `GET /api/runs/{id}/candidates/{candidate_id}/image` | yes | the 500 px file the run downloaded, confined to the runs directory and checked against its recorded hash (the ETag) |
| `POST /api/runs/{id}/deploy` | yes | `{"candidate_id": N}` optional (the pick by default): queues the owner's deploy, 202; 200 with the job when the same request is the movie's newest write and still queued; 409 when the candidate cannot be deployed |
| `GET /api/deployments` | yes | one page of deployments, newest first, with a `movie_id` filter |
| `POST /api/deployments/{id}/restore` | yes | queues a restore of any deployment, 202; 200 with the job when the same request is the movie's newest write and still queued |
| `GET /api/review` | yes | movies whose latest completed run has no feedback that stands, policy deploys first, each with the movie's `poster` (as a movie item has it) and `pick_image_url`, the pick's 500 px file when it was downloaded |
| `POST /api/runs/{id}/feedback` | yes | `{"action", "candidate_id", "order", "hated", "shown", "deploy" (default true), "idempotency_key" (required)}`: 201 with the event, the exemplar changes, and the job ids; 200 with the same body when the key was used before; 404 for an unknown run or candidate; 409 with the reason when refused; 422 for a malformed body |
| `GET /api/runs/{id}/feedback` | yes | the run's events and the revokes of them |
| `GET /api/feedback` | yes | every act of feedback, revokes included, newest first and paged, with an optional `movie_id`; each with its movie's title and year, how many candidates were shown (not the copy itself), and `revoked_by` |
| `POST /api/feedback/{id}/revoke` | yes | revokes one event: 201, or 200 when it was revoked under the same key; 409 when it is a revoke or already revoked |
| `GET /api/taste` | yes | what `marquee taste status` prints, and the blind evaluation's headline: the latest evaluation that succeeded, the one that succeeded before it, and one running (a cancelled or failed one is neither; it stays in the list and on its own page) |
| `POST /api/taste/evaluations` | yes | `{"movie_ids", "limit"}`, both optional: queues a blind evaluation, 202 with it and its batch job; 409 while one runs or when a rebuild is due; 422 names movies that cannot be evaluated |
| `GET /api/taste/evaluations` | yes | evaluations, newest first and paged, each with its state, progress, and summary |
| `GET /api/taste/evaluations/{id}` | yes | one evaluation and its summary |
| `GET /api/taste/evaluations/{id}/results` | yes | one page of its results by title, with `outcome` and `source` filters; each with the movie's title and year, the exemplar's image URL, and the match's and the pick's image URLs while their run exists |
| `POST /api/taste/rebuild` | yes | `{"force": bool}` optional: queues a rebuild, 202; 200 with the one already queued |
| `GET /api/taste/exemplars` | yes | one page of exemplars, newest first, with `movie_id`, `state`, and `polarity` filters; each with its movie's title and year, `updated_at`, and `ended_by_event_id` |
| `GET /api/taste/exemplars/{id}/image` | yes | the kept image, checked against its hash (the ETag) |
| `GET /api/system` | yes | what this installation is and how it is set (below) |

Every `/api` route requires the key in `API_KEY`, as `Authorization: Bearer <key>` or
`X-Api-Key: <key>`, compared in constant time: missing or wrong is 401, and with no key
configured the API answers 503. There is no bypass: not for debug mode, not for loopback
clients, and never a key in a query string. After ten requests with a missing or wrong key
within a minute from one client address, that address gets 429 with `Retry-After` until the
minute has passed (`marquee/api/rate_limit.py`, in memory, per app, at most 10,000
addresses); the right key is never slowed and clears nothing, and the health probes are
outside `/api`. In the stack only the web server reaches the API, always with the right key. Every response is a pydantic model, so the
OpenAPI schema is fully typed, and no response carries Radarr's path or a local path, except
`GET /api/system`'s movie roots and data directory, which are its point. The
interactive documentation and `/openapi.json` are served only when `MARQUEE_ENVIRONMENT` is
`development`. Requests are logged as method, path, status, and time taken, never a header
or a query string; uvicorn's own access log is off. Queries live in
`marquee/library/queries.py` and `marquee/jobs/queries.py`, not in the routes. No route
queues a job of an arbitrary kind.

**`GET /api/system`** (`marquee/api/system.py`) is read-only and cheap: the version and
environment; the database's schema revision against the newest in the code; the settings that
shape behaviour (`DEPLOY_POLICY`, `RUN_NEW_MOVIES`, the sync interval, the movie roots, the
data directory); library counts (movies, movies with a file, posters by origin, the product
loop's runs by state, blind evaluations' masked runs by state apart (`masked_runs`),
deployments, exemplars by state and polarity, preference events); each file the model
manifest lists, whether it is there and its size (a stat, never a hash); the newest
`library_sync` job's state and times; and the size of `runs/`, `backups/`, `cache/`, and `ml/`,
measured off the event loop and kept for a minute. Every field is named: nothing serialises
the settings object, so no key, token, password, database URL, or environment variable can
reach the answer; a test sets every secret and looks for each in it. It never runs the
doctor or loads a model.

## The web app

`frontend/` is a SvelteKit 3 app (Svelte 5 runes, TypeScript strict) built with
`adapter-node`, configured in `vite.config.ts` (there is no `svelte.config.js`); the unit tests'
`vitest.config.ts` extends it. Modules under `src/lib/` are imported as `#lib/...` with their
extension, through the `imports` map in `package.json`. It talks to nothing but the HTTP API, and
the browser never holds the API key.

**How the browser reaches the API.** The browser calls this site's own `/api/*`. The route
`src/routes/api/[...path]/+server.ts` forwards each request to `MARQUEE_API_URL` with
`X-Api-Key: MARQUEE_API_KEY` attached (`src/lib/server/proxy.ts`): it drops hop-by-hop headers,
the browser's cookies, and any credential the browser sent; refuses a body over 1 MB (413); gives
up after 20 s (504) and says so when the API is down (502); and streams the answer back with its
status, `ETag`, and `Cache-Control`, so a 304 stays a 304. It uses the plain `fetch`, not
SvelteKit's, which would add the browser's cookies to a request for the same host name. Both
settings are declared in `src/env.ts` as private and not static, and read through
`$app/env/private` (`src/lib/server/env.ts`) when the server starts, so the built app holds no
key, and nothing with the key reaches page data, a public variable, or a client bundle. Both may
be missing: the sign-in page then says no key is configured, and the API address has a default.

**Sign-in.** The owner signs in with the API key at `/signin` (a form action). The server
compares it with its own in constant time (both sides hashed first) and sets the cookie
`marquee_session`: an expiry time and an HMAC-SHA256 of it, keyed by an HMAC of a fixed label
under the API key (`src/lib/server/session.ts`); `HttpOnly`, `SameSite=Strict`, `Path=/`,
`Secure` when the site's address (`ORIGIN`) is `https://`, 30 days. There is no session
store: signing out forgets the cookie in that browser, and changing the API key signs everyone
out. Five failed attempts within a minute from one address answer 429 until the minute passes
(in memory). Behind a reverse proxy every visitor arrives from the proxy's address; the
adapter's `ADDRESS_HEADER` and `XFF_DEPTH` (from `MARQUEE_WEB_ADDRESS_HEADER` and
`MARQUEE_WEB_XFF_DEPTH`) make it read the visitor's own from the proxy's header. `POST /signout` deletes the cookie. With no key configured
the sign-in page says so and nothing else is served.

**Every request** passes two same-origin checks. SvelteKit's own, always on, comes first: a
change (POST, PUT, PATCH, DELETE) sent as a form or with no `Content-Type` is refused with a
plain-text 403 unless its `Origin` is this site's. Then `src/hooks.server.ts`: a request that
changes something (anything but GET, HEAD, OPTIONS) is refused with 403 unless the browser's
`Origin` (or, with none, `Sec-Fetch-Site: same-origin`) says it came from this site. Only the
sign-in page is open: any other page without a valid session goes to `/signin?next=...`, and
`/api/*` answers 401. Static assets are served before the hooks run, from a list made at build
time, and only to GET and HEAD (any other method is 405). Every answer that passes the hooks
carries `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, and `Referrer-Policy:
same-origin`, and, when the site's address is `https://`, `Strict-Transport-Security:
max-age=31536000` (`src/lib/server/headers.ts`); static files and SvelteKit's own refusals do
not.

**The Content-Security-Policy.** Every page carries one, set by SvelteKit itself from
`vite.config.ts`: `default-src 'self'`; scripts from this origin and SvelteKit's per-response
nonce (its own inline scripts and `app.html`'s theme script, which carries
`%sveltekit.nonce%`); styles, fonts, and requests from this origin; images also from `data:`
and `blob:`; `object-src 'none'`, `base-uri 'self'`, `form-action 'self'`,
`frame-ancestors 'none'`. Nothing inline is allowed but style attributes
(`style-src-attr 'unsafe-inline'`), which SvelteKit's own route announcer uses; the app's
components set dynamic styles through the DOM (`element.style`), which the policy allows. In
development SvelteKit adds `'unsafe-inline'` for styles so that Vite can inject them. Every
end-to-end test fails if the browser reports a violation (`e2e/fixtures.ts`). The built server needs
`ORIGIN` set to the address the browser uses (or a proxy's `PROTOCOL_HEADER` and `HOST_HEADER`),
or it takes the request for HTTPS and the same-origin check refuses every form.

**API types are generated, never written by hand.** `marquee api-schema` writes the API's
OpenAPI document to `frontend/openapi.json`, committed and checked in CI. `npm run api:types`
generates `src/lib/api/generated/schema.d.ts` (gitignored) from it with `openapi-typescript`,
on `prepare`, before `check`, and before `build`. `src/lib/api/client.ts` is `openapi-fetch`
over those types; `call()` folds every answer into one result shape, `errors.ts` is the one
place a backend `detail` or a status becomes a sentence, and one middleware sends a 401 back to
sign-in. `src/lib/api/types.ts` only names schemas.

**Layout of `src/lib/`:** `api/` (the client), `server/` (server-only: settings, session,
rate limit, origin check, security headers, proxy), `ui/` (design-system primitives: buttons, notices, the top
bar, navigation, toasts, the confirm dialog), `features/<area>/` (what knows about the library,
runs, jobs, review, taste, or the system), and `stores/` (theme, toasts, how many reviews
wait). Colours, radii, and fonts come from the tokens in `src/app.css`. The one runtime
dependency besides `openapi-fetch` is `svelte-dnd-action`, for dragging a card in the rank
panel.

**The pages** render in the browser (`ssr = false`); the server still checks the session before
it sends one.

| Page | Shows | Can do |
|---|---|---|
| `/` Library | the grid of movies with a file, width-320 thumbnails with their `version`, title and year, a mark for no poster and for Marquee's poster; search, filters (poster, whose, runs), and sort, kept in the address; more load as the owner scrolls | Sync now (`POST /api/sync`), then follows the job |
| `/movies/[id]` | the poster at width 640, the facts, the latest run (when, blind, reviewed, the ranked candidates with rank, score, stack, and the pick, read-only), and the poster history, newest first | Run (`POST /api/movies/{id}/run`, then follows the job and shows the new run); put back what was there before a deployment, behind a confirmation |
| `/review` | the runs waiting for an answer: the movie's poster now and the pick, title and year, how many were ranked, a Blind mark, and a mark when the deploy policy already wrote the pick | opens a run's review |
| `/review/[runId]` | the movie, its poster now and whose it is, the run's line, the run's earlier answers (newest first), and the rank panel | the four answers, Undo, Next (below) |
| `/taste` | the active profile, the exemplars by state and whether a rebuild is due, agreement over blind reviews (a percentage from 10; the others apart), the residual (in use or not and why, in one plain sentence that says each fact once and never shows an engine identifier, held-out accuracy before and after, the features it leans on, the last training, what is missing), the blind evaluation (the latest headline in a sentence, the one before, and one running with its progress), recent feedback, and a paged grid of exemplars filtered by polarity and state, each saying what ended it | Rebuild now (followed to its end); Run a blind evaluation, behind a confirmation that says how long it takes and that it changes nothing in the library (followed to its end); Undo on a recent answer |
| `/taste/evaluations/[id]` | one evaluation's summary, then a row per movie: the owner's poster, the blind pick, the outcome in words ("Your design was 4th of 16", "Removed by the text gate: text_heavy", "Not among TMDB's candidates"), how the poster was found, filtered by outcome and by seed or feedback, paged | opens a result's masked run on its review page, read-only |
| `/activity` | the worker's state; jobs newest first with kind (a masked run is a "Blind run"), the movie they are about (linked), state, progress, and times, filtered by state and kind and paged; a job's events and error | Cancel and Retry where the API allows |
| `/system` | plain tables of `GET /api/system` and `GET /api/worker` (blind runs on their own line); in production the paths are labelled as the containers' and the settings that hold the host's folders are named; a schema behind the code, a missing model file or manifest, and a worker that is not running are listed first as problems, in words; at the foot, Credits: TMDB's logo and the notice its terms ask for | nothing: it changes no setting |

The navigation is Library, Review (with the number waiting, read from `GET /api/taste` when
the shell loads and after each answer), Taste, Activity, and System; on a narrow screen it
shows icons, with the labels kept for screen readers. The Library pages have no deploy, pick,
approve, reject, or revoke button: choosing a poster is feedback, and belongs to the review
page. A movie's page links its latest run to its review when that run completed.

**The rank panel** (`src/lib/features/review/`). One card is one design: a run's ranked
candidates are grouped by design stack (`3B` is stack 3, variant B), and a card shows the
stack's best-ranked variant until the owner opens the stack and makes another variant the
one it shows. An answer names one candidate per card, the variant the card shows; the old app
sent every variant of a stack, and eight near-identical negatives teach less than eight
different ones. A card whose candidate was never downloaded says it cannot be chosen or hated.
The cards stand in the owner's order, with a hate pile below; every card has buttons (move up,
move down, hate, and in the pile bring back), and dragging a card by its handle, with the
pointer, by touch, or from the keyboard, does the same. A line says whether the order differs
from the engine's. The four answers, each `POST /api/runs/{id}/feedback`:

| Button | `action` | Body besides the key |
|---|---|---|
| Keep the pick | `approval` | `shown`, `deploy` |
| Use this one (on any card) | `override` | `candidate_id`, `shown`, `deploy` |
| Save this ranking | `rank` | `order` (the cards not hated, in the owner's order), `hated`, `shown`, `deploy` |
| None of these | `reject` | `shown` (the API hates every shown one and keeps the best-ranked 8) |

`shown` is every card's shown candidate in the order the page first showed the cards (rank
order), at most 100. `deploy` comes from a checkbox, "Write the chosen poster to the movie's
folder", on by default. Saving a ranking and refusing all ask first, saying which poster is
written (or that none is) and that the hated designs become examples of what the owner does
not want; keeping the pick and using another ask only when the deploy is on and the folder
holds the owner's own poster. Each answer gets its own idempotency key when the button is
pressed, kept while that request is retried after a network failure (no answer, 502, 503,
504) and replaced for the next answer; a 200 instead of a 201 means the key was used before,
and the page shows the event it returned. After an answer the page says what happened until
the owner moves on: the event the API recorded (an override that names the pick is recorded,
and shown, as keeping it), the exemplars made, ended, and brought back, the deploy and rebuild
jobs followed to their end, and why a deploy was refused. Undo revokes the answer and says, in
one sentence with a link to the movie's poster history, that the poster was not put back;
Next opens the next run waiting. A run already answered can be answered again: its earlier
answers are listed above the panel, each with Undo unless it was undone or is an undo. The
review page of a masked run, a run that is not the movie's latest completed one, one that is
not completed, or one with no ranked candidate shows its candidates read-only and says why,
with a link to the latest run. Activity polls `GET /api/jobs/events` every 2 s and reads again the
jobs it mentions, and reads the whole list again every 15 s whatever the feed says, because the
feed can skip an event; on opening it pages through the feed once to find where it ends.

**Tests.** Unit tests (Vitest) cover the server code and the logic in `src/lib/`. End-to-end
tests (Playwright, Chromium) run the built app against a small fake API in `frontend/e2e/`
that refuses any request without the test key and records every JSON body it receives; they
cover sign-in, the session and its cookie, the same-origin check, the proxy, both Library
pages, Activity, the review queue and each of the four answers' bodies (with buttons, from the
keyboard, and by dragging), Undo, Next, a retried request keeping its key, Taste, and System,
at 390 and 1440 px. CI's `frontend` job runs `npm ci`, the types, `check`, `lint`, the unit tests, the
build, and the end-to-end tests.

## Runtime data

Everything the application writes lives under one directory, `DATA_DIR` (default `data/` in
the repository, never committed). Only `marquee/config/` builds these paths.

```
data/
  models/     ONNX and other model files, manifest.json (their checksums and where each came
              from), and paddlex/, the OCR weights
  ml/         the taste profile, residuals/: residual artifacts by hash (<sha256>.npz; the
              five newest and the active one), zero-shot axes, seed/: the staged
              copies of the library posters the first profile was built from (movies/) and
              the record of what was seeded (movies.json), exemplars/: each exemplar's kept
              image (<sha256>.<ext>), and profiles/: a copy of each profile built or
              registered (<sha256>.npz; the five newest, the seed's, and the active one)
  cache/      embeddings (keyed by model and file hash), poster downloads, thumbs/ (the web
              app's poster thumbnails, <poster sha256>-<width>.jpg),
              deploys/ (originals a deploy fetched and the write's intent, <job id>.json, until
              it records its write), taste-build/<job id>/ (a rebuild's staged exemplars and
              its build, removed once published), blind-profiles/ (a masked run's derived
              profile, <profile sha256>-<movie id>.npz, while it runs), and in a
              container cuda-jit/, the CUDA driver's kernel cache (the image points the
              driver there, not Marquee)
  runs/       one folder per run, named <run id, 8 digits>-<title slug>: the candidates it
              downloaded, ranked/ output, pipeline.log, and the run archive pipeline_run.json
  backups/    posters/<movie id>/<sha256>.jpg: every poster a write replaced, and db/: the
              database dumps (marquee-<UTC time>-<revision>.dump from the schedule,
              manual-... from `marquee db backup`, pre-restore-<UTC time>.dump from a restore)
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
| `runtime` | The package and its `nvidia` dependencies, installed from `uv.lock` into `/opt/venv`, and the PostgreSQL client tools. No PyTorch | Everything that runs the product |
| `export` | `runtime` plus the `export` dependency group and `uv` | `marquee models fetch` only |

CUDA and cuDNN come from the Python wheels, as on a bare-metal install. The base image is a
slim Python image, and the NVIDIA Container Toolkit on the host supplies only the driver. The
base images (`PYTHON_IMAGE`, `UV_IMAGE`) are pinned by tag and digest. The image runs as a
non-root user whose user and group ids are build arguments (`MARQUEE_UID` and `MARQUEE_GID`,
default 1000; Compose passes them from `.env`). Dependencies are installed from the lockfile
in their own layer, before any source, so a code change rebuilds only the layer that installs
the package. The build works in `/src`, which the images do not keep.

**The CPU build, for CI only.** The build argument `MARQUEE_EXTRA` (default `nvidia`) names
the dependency extra. CI builds the `runtime` target with `MARQUEE_EXTRA=cpu`, since a hosted
runner cannot build the 18 GB NVIDIA image, and smoke-tests it: a non-root user, `marquee
--help`, `pg_dump` 19, and the stack's `db`, `migrate`, `api`, and `web` started from the
images CI built, with a sign-in through the web app and `/api/system` answering 200. The CPU
build is not a supported way to run Marquee.

**The PostgreSQL client.** The `runtime` target installs `postgresql-client-19` (`pg_dump`,
`pg_restore`, `psql`) for the database dumps and the restore. The client must be the server's
major version or newer and Debian's own is older, so it comes from the PostgreSQL project's
apt repository, set up as postgresql.org describes it: the repository's signing key, fetched
with `ADD` and pinned by SHA-256 (its fingerprint is in the Dockerfile), and a deb822 source
with `Signed-By`. Only the client package is installed (about 66 MB with the Perl it needs);
its folder, `/usr/lib/postgresql/19/bin`, comes first on `PATH`. `PG_CLIENT_MAJOR` (a build
argument, 19) follows the server's version in `compose.yaml`.

**State lives in one folder.** The image holds code and dependencies, never a model. The folder
mounted at `/data` is the data directory (`DATA_DIR=/data`): models, taste data, caches, runs,
and backups. It must be writable by the image's user; PaddleX also writes lock and temporary
folders beside the OCR weights. The image also keeps the CUDA driver's kernel cache there (`CUDA_CACHE_PATH`, set to
`/data/cache/cuda-jit`). The driver compiles some kernels for the card on first use; on a
bare-metal host it keeps them in the user's home folder, but a container's home folder is
discarded with the container, and compiling again made every run three times slower.

**Configuration comes from the environment.** The image sets `MARQUEE_ENV_FILE` to an empty
string, so the application reads no dotenv file inside a container, and `marquee doctor` says
the settings come from the environment. No `.env` file and no secret is ever copied into an
image.

The web app has an image of its own, built from `frontend/Dockerfile` with its own allowlist
`.dockerignore`: a build stage runs `npm ci` and `npm run build`, and the runtime stage, on the
official Node 26 slim image (pinned by tag and digest), holds only the build output and the
production dependencies and runs `node build` as the image's non-root `node` user (uid 1000).
Nothing is read at build time: the server takes `MARQUEE_API_URL`, `MARQUEE_API_KEY`,
`ORIGIN`, `HOST`, `PORT`, `BODY_SIZE_LIMIT`, `ADDRESS_HEADER`, and `XFF_DEPTH` from its
environment when it starts, so the image holds no key.

## The Compose stack

`compose.yaml` is both development's database and the product. `docker compose up -d` with no
profile starts only `db`, which is what development on the host needs. The stack is the `app`
profile: `docker compose --profile app up -d`, or `docker compose up -d` where `.env` sets
`COMPOSE_PROFILES=app`, as a machine that runs Marquee with Docker does. Two one-command
containers sit behind the `tools` profile, which `docker compose run` turns on by itself.

| Service | Profile | Image | Runs | GPU | Data folder | Library | Published | Health check | Restart |
|---|---|---|---|---|---|---|---|---|---|
| `db` | none | `postgres:19` | PostgreSQL | no | (volume `db-data`) | no | `127.0.0.1:POSTGRES_PORT` | `pg_isready` | `unless-stopped` |
| `migrate` | `app` | `marquee:runtime` | `marquee db upgrade`, once | no | no | no | no | (exits 0) | no |
| `api` | `app` | `marquee:runtime` | `marquee api` on `0.0.0.0:8000` | no | read-write | read-only | no | its own `/health/ready` | `unless-stopped` |
| `worker` | `app` | `marquee:runtime` | `marquee worker` | yes | read-write | read-write | no | `marquee worker --health` | `unless-stopped` |
| `web` | `app` | `marquee:web` | `node build` on `0.0.0.0:3000` | no | no | no | `MARQUEE_WEB_BIND:MARQUEE_WEB_PORT` | its own `/signin` | `unless-stopped` |
| `setup` | `tools` | `marquee:export` | `marquee models fetch` | yes | read-write | no | no | none | no |
| `marquee` | `tools` | `marquee:runtime` | any `marquee` command | yes | read-write | read-only | no | none | no |

- **Order.** `migrate` waits for `db` to be healthy; `api` and `worker` start only after
  `migrate` has exited successfully; `web` starts after `api`. A change of schema is applied by
  `docker compose up -d` before anything uses it.
- **One data directory.** The application containers bind-mount one host folder at `/data`
  (`MARQUEE_DATA_HOST_PATH`, default `./data`, the repository's own `data/`), not a named
  volume. Commands on the host and the containers therefore see the same models, taste
  profile, exemplars, runs, and backups, and the owner can back the folder up. The containers'
  user (1000:1000 unless `MARQUEE_UID` and `MARQUEE_GID` in `.env` say otherwise) must own it. The database is the one thing that lives in a Docker
  volume (`db-data`).
- **The library** is mounted from `MOVIE_LIBRARY_HOST_PATH` at `/media/movies`, read-write in
  the worker, the only service that writes posters, and read-only everywhere else.
- **Settings.** `api`, `worker`, `migrate`, `setup`, and `marquee` take `.env` through
  `env_file` and override what differs inside the stack: `MARQUEE_ENVIRONMENT=production`,
  `MARQUEE_ENV_FILE` empty (the containers read no dotenv file), `DATA_DIR=/data`,
  `MOVIE_ROOTS=/media/movies`, `POSTGRES_HOST=db`, `POSTGRES_PORT=5432`; the API also gets
  `API_HOST=0.0.0.0` and `API_PORT=8000`. Each long-running service is told its role
  (`MARQUEE_ROLE=api` or `worker`). **`web` gets no `env_file`**: only `MARQUEE_API_URL`
  (`http://api:8000`), `MARQUEE_API_KEY` (the value of `API_KEY`: one secret, two names),
  `ORIGIN` (from `MARQUEE_WEB_ORIGIN`), `HOST`, `PORT`, `BODY_SIZE_LIMIT=1M`, the proxy's own
  limit (the server's default, 512 KiB, would cut a larger body first), and `ADDRESS_HEADER`
  and `XFF_DEPTH` (from `MARQUEE_WEB_ADDRESS_HEADER`, empty, and `MARQUEE_WEB_XFF_DEPTH`, 1 when
  empty), for a reverse proxy. The web container never
  holds the database password, the TMDB token, or Radarr's key.
- **The browser's address is required.** Without `ORIGIN` every form post would be refused, so
  the `web` service refuses to start without `MARQUEE_WEB_ORIGIN` and says why in its log. It
  is checked in the service's command rather than with `${MARQUEE_WEB_ORIGIN:?}`, because
  Compose reads the whole file even to start `db` alone, and development's `.env` has no reason
  to set it. `MOVIE_LIBRARY_HOST_PATH` is required that way already.
- **Only `web` is published.** The API is reached through the web server's proxy inside the
  stack's network; a commented `ports` entry in `compose.yaml` publishes it on loopback.
- **Health and restarts.** The four long-running services restart `unless-stopped`: a crash
  brings a service back by itself, while `docker compose stop` (or `docker kill`) is taken as
  meaning it. Health checks use what the images hold (Python and Node; no `curl`). The worker's
  check, `marquee worker --health`, runs nothing: it exits 0 only when a worker holds the
  queue's advisory lock and the database answers, on one short connection. It does not prove
  the worker makes progress: a worker stuck inside a job still holds the lock.
- **The worker** runs with `init: true` (as PID 1 nothing would reap the GPU child and the OCR
  processes it stops) and a 45-second stop grace period, enough for its own shutdown: it
  signals cancel, waits up to 10 s, puts unfinished jobs back in the queue without counting
  the attempt, and stops the GPU child. A worker killed outright leaves its job `running`; the
  next worker to start puts it back as an orphan. Only one worker can run: a second one, in a
  container or on the host, finds the lock taken and exits.
- **Production mode.** `MARQUEE_ENVIRONMENT=production` hides the API's interactive
  documentation and makes `marquee doctor` strict (see "Commands"): run in the `worker` it
  fails when the engine falls back to the CPU, when no GPU is visible, or when the library is
  not writable; run in the `api` it leaves the GPU checks out and accepts a read-only library.
- **Logs** go to Docker's log (`docker compose logs`). Times are UTC, and the text format says
  so (`2026-11-26T01:02:03.456Z`). `httpx` (and `httpx2`, the copy huggingface_hub uses) logs
  warnings only, so request lines with Radarr's address and TMDB's URLs stay out; so do
  Alembic's plugin lines. An OCR process stopped with SIGTERM ends quietly instead of printing
  PaddlePaddle's C++ traceback. The API logs each request's method, path, status, and time.
  Every service, the database included, uses Docker's `json-file` driver with `max-size: 10m`
  and `max-file: "5"` (the `x-logging` block), so no log grows without limit.

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
