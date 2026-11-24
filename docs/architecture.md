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
                   artwork), the Alembic migrations, and migrate.py, which applies them
  fs/              filesystem safety: confined roots, symlink rejection, download validation,
                   and paths.py, the only place a path from Radarr becomes a local path
  integrations/    thin HTTP clients: arr/ (Radarr, Sonarr), tmdb/, URL validation
  ml/              model wrappers (CLIP, aesthetic head, faces, people, DINOv2, zero-shot axes),
                   hardware and execution-provider selection, the taste store, calibration,
                   the residual learner, geometry types, the model checksum manifest
  pipeline/        the selection engine: gate and rank stages, OCR text gate, feature assembly,
                   normalisation, scoring, design stacking, run output, text profiles, and
                   single_run.py, which runs one subject and leaves a complete run folder
  library/         the movie library: a read-only scan of the movie folders (folders.py),
                   resolving a typed title to one movie and its TMDB id (subjects.py), the
                   Radarr sync (sync.py), and the read queries over the library (queries.py)
  taste/           building and enriching the taste profile (trainer, map, profile enrichment)
                   and the first profile seeded from the library's posters (seed.py)
  api/             the HTTP API: the app factory and server (app.py), API-key auth (auth.py),
                   routes/ (health, library), schemas/ (response models), file responses
                   (files.py), and a rate limiter
```

Planned packages, each added when its first real module arrives:

| Package | Holds |
|---|---|
| `artwork/` | naming, deploy with backup, restore, heal |
| `jobs/` | the Postgres-backed job queue, worker, schedules |

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
  (`RADARR_MOVIE_ROOTS`), integration credentials, and the API's key, host, and port. Secrets
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
| `marquee doctor` | Read-only checks of the machine, the models, the manifest, the taste profile, and every connection |
| `marquee models fetch [--force ITEM]` | Builds every model that is missing (CLIP, DINOv2, zero-shot axes, YOLO person, OCR weights) and downloads the LAION aesthetic head and the SCRFD face detector, pinned by SHA-256. Checks each one before putting it in place, and writes `data/models/manifest.json`. CLIP, DINOv2, the zero-shot axes, and the aesthetic head's conversion need the `export` dependency group; the YOLO exporter runs in its own isolated `uv` tool environment |
| `marquee taste seed [--dry-run] [--rescan \| --reuse]` | Builds the first taste profile from the `poster.jpg` in each movie folder, staged as lossless 500 px wide copies. Refuses to scan again unless given `--rescan`; `--reuse` rebuilds from the staged copies |
| `marquee run "<title>" [--year YYYY] [--tmdb-id N] [--json]` | Finds the movie through its library folder (the IMDb id in the movie file's name), runs it through the pipeline, and leaves the ranked posters and the run archive in a new run folder |
| `marquee db upgrade` | Applies every migration the database lacks |
| `marquee db status` | Shows the database's revision and the newest one; exits 1 when they differ |
| `marquee db revision -m "<text>"` | For developers: writes a new migration from the difference between the models and a database at the newest revision |
| `marquee sync [--dry-run] [--json]` | Checks that Radarr's root folders map to folders here, copies Radarr's movie list into the database, records each folder's poster, and fetches TMDB details once per movie; exits 1 when a movie failed or the path check failed |
| `marquee api [--host] [--port]` | Serves the HTTP API under uvicorn, on `API_HOST` and `API_PORT` by default |

## Database

PostgreSQL, reached through SQLAlchemy 2's async engine on psycopg 3 (`marquee/db/engine.py`
builds it; nothing connects at import time). Constraints and indexes are named by a naming
convention on `Base.metadata` (`pk_`, `fk_`, `uq_`, `ck_`, `ix_`), and every table carries
`created_at` and `updated_at`, both timezone-aware.

**Migrations live inside the package**, in `marquee/db/migrations/` (the environment script,
the revision template, and `versions/`), because the container image installs the package
without its source tree. There is no `alembic.ini`: `marquee/db/migrate.py` builds the
Alembic configuration in code and hands the environment script an open connection, so no
database URL passes through Alembic's configuration. There is one baseline migration; every
later schema change is a new numbered migration (`0002`, ...), and a merged migration is
never edited.

The schema is movies only:

| Table | Holds |
|---|---|
| `movies` | Every movie Radarr lists, with or without a file (`has_file`): Radarr's id (unique), the TMDB and IMDb ids (`tmdb_id` is indexed but not unique), title, sort title, year, genres, Radarr's folder path, the movie file's relative path, size, and Radarr id, the TMDB details the text gate uses (director, production companies, tagline), and presence: a movie Radarr stops listing is kept, with `is_present` false and `retired_at` set |
| `artwork` | One row per movie per artwork kind, for files found in the movie folder: the file's name (never a path), its SHA-256, size, and modification time when it was hashed, its origin, and when it was first and last seen. `subject_kind`, `artwork_kind`, and `origin` each accept one value today (`movie`, `poster`, `library`), enforced by check constraints; adding a value is a migration. Rows go with their movie (`ON DELETE CASCADE`) |

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
   gone from a folder the scan found, its row is deleted. A row whose origin is not
   `library` is never changed.
5. TMDB details for movies with a file that were never enriched, at most five requests at a
   time. A changed TMDB id clears them, so they are fetched again. A failure is logged and
   does not fail the sync.

Everything happens in one transaction, committed at the end (rolled back for `--dry-run`).
Filesystem work runs off the event loop, and the sync checks an optional cancellation signal
(`marquee.cancellation`) between phases. It returns a report of the movies it created,
updated, left unchanged, retired, and failed, the posters it found, hashed, and removed, the
folders on disk no Radarr movie points at, and the movies with a file whose folder was not
found.

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

Every `/api` route requires the key in `API_KEY`, as `Authorization: Bearer <key>` or
`X-Api-Key: <key>`, compared in constant time: missing or wrong is 401, and with no key
configured the API answers 503. There is no bypass: not for debug mode, not for loopback
clients, and never a key in a query string. Every response is a pydantic model, so the
OpenAPI schema is fully typed, and no response carries a local path or Radarr's path. The
interactive documentation and `/openapi.json` are served only when `MARQUEE_ENVIRONMENT` is
`development`. Requests are logged as method, path, status, and time taken, never a header
or a query string; uvicorn's own access log is off. Queries live in
`marquee/library/queries.py`, not in the routes.

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
  cache/      embeddings, poster downloads, thumbnails, and in a container cuda-jit/, the
              CUDA driver's kernel cache (the image points the driver there, not Marquee)
  runs/       one folder per pipeline run, named <UTC timestamp>-<title>: candidates, ranked/
              output, pipeline.log, and the run archive pipeline_run.json
  backups/    posters replaced by a deploy
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
up the full stack arrive later.

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
   | anything that reads or writes library rows, the sync, resolving a subject | `marquee/library/` |
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
