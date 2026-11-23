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
to an empty string. The test suite uses it so that no test reads a real `.env`.

## Commands

Everything an operator runs goes through the `marquee` command:

| Command | Does |
|---|---|
| `marquee doctor` | Read-only checks of the machine, the models, the manifest, the taste profile, and every connection |
| `marquee models fetch [--force ITEM]` | Builds every model that is missing (CLIP, DINOv2, zero-shot axes, YOLO person, OCR weights), checks each build before putting it in place, and writes `data/models/manifest.json`. CLIP, DINOv2, and the zero-shot axes need the `export` dependency group; the YOLO exporter runs in its own isolated `uv` tool environment |
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
  cache/      embeddings, poster downloads, thumbnails
  runs/       one folder per pipeline run, named <UTC timestamp>-<title>: candidates, ranked/
              output, pipeline.log, and the run archive pipeline_run.json
  backups/    posters replaced by a deploy
  text_profiles.json
```

PaddleX keeps its OCR weights wherever `PADDLE_PDX_CACHE_HOME` points. `marquee.ml.hardware`
sets it to `data/models/paddlex` before anything imports PaddleOCR, unless it is already set.

Artifacts are tagged with the model that produced them: a taste profile, embedding cache entry,
or residual built under one model name fails loudly when loaded under another.

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
