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
  fs/              filesystem safety: confined roots, symlink rejection, download validation
  integrations/    thin HTTP clients: arr/ (Radarr, Sonarr), tmdb/, URL validation
  ml/              model wrappers (CLIP, aesthetic head, faces, people, DINOv2, zero-shot axes),
                   hardware and execution-provider selection, the taste store, calibration,
                   the residual learner, geometry types, the model checksum manifest
  pipeline/        the selection engine: gate and rank stages, OCR text gate, feature assembly,
                   normalisation, scoring, design stacking, run output, text profiles, and
                   single_run.py, which runs one subject and leaves a complete run folder
  library/         the movie library: a read-only scan of the movie folders (folders.py) and
                   resolving a typed title to one movie and its TMDB id (subjects.py)
  taste/           building and enriching the taste profile (trainer, map, profile enrichment)
                   and the first profile seeded from the library's posters (seed.py)
  api/             HTTP API (only the rate limiter exists so far)
```

Planned packages, each added when its first real module arrives:

| Package | Holds |
|---|---|
| `db/` | SQLAlchemy engine, `Base`, ORM models |
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

Two more contracts:

- `marquee.ml` and `marquee.pipeline` never import a database library (`sqlalchemy`,
  `asyncpg`, `psycopg`) or, once it exists, `marquee.db`. The engine takes data in and hands
  results out; it does not know a database exists. A pipeline run identifies its subject with
  `pipeline.types.SubjectRef` (id, title, TMDB id), not an ORM row.
- Only `marquee.config` imports `pydantic_settings` or `dotenv`, and only it reads settings
  from the environment. Other modules import `settings` or `pipeline_settings` from
  `marquee.config`.

## Settings

There are exactly two settings classes, and both read the same dotenv file (`.env` at the
repository root; `.env.example` lists every setting):

- `Settings` (`marquee/config/app.py`): environment name, debug and log level, the data
  directory, PostgreSQL connection parts, media roots, and integration credentials. Secrets are
  pydantic `SecretStr` values and never print. It derives every runtime path and both database
  URLs (`database_url` for asyncpg, `database_url_sync` for psycopg).
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
   | filesystem safety, path translation, download validation | `marquee/fs/` |
   | a model wrapper, embedding maths, the taste store, the residual learner | `marquee/ml/` |
   | a selection stage, a gate, a scorer, feature assembly | `marquee/pipeline/` |
   | reading the movie library or resolving a subject | `marquee/library/` |
   | taste profile building, feedback events, exemplars, seeding | `marquee/taste/` |
   | an HTTP route or schema | `marquee/api/` |

2. Check the layer table: it may import only from layers below it. Run `uv run lint-imports`.
3. Start the module with `from __future__ import annotations`, type every signature, and log
   through `logging.getLogger(__name__)`.
4. Add its tests at `tests/<package>/test_<module>.py`. Unit tests use no network, no real
   media folder, and no GPU; mark tests that need model files `models`, a GPU `gpu`, or the
   Compose database `db`.
5. No `utils`, `helpers`, `common`, `misc`, or `core` modules, and no empty placeholder
   packages: a package appears with its first real module.
