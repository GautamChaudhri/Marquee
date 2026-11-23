# Architecture

How the Marquee package is laid out, what each part is for, which way imports may point, and
where the application keeps its runtime state.

## Packages

```
marquee/
  __init__.py      version
  cli.py           the `marquee` command; one entry point for everything an operator runs
  doctor.py        `marquee doctor`: read-only checks of the machine and its connections
  cancellation.py  cooperative cancellation protocol shared by long-running work
  host_metrics.py  CPU, memory, disk, and NVIDIA GPU readings (NVML, optional)
  logging.py       text and JSON log formatting
  config/          the two settings classes; the only code that reads the environment
  fs/              filesystem safety: confined roots, symlink rejection, download validation
  integrations/    thin HTTP clients: arr/ (Radarr, Sonarr), tmdb/, URL validation
  ml/              model wrappers (CLIP, aesthetic head, faces, people, DINOv2, zero-shot axes),
                   hardware and execution-provider selection, the taste store, calibration,
                   the residual learner, geometry types
  pipeline/        the selection engine: gate and rank stages, OCR text gate, feature assembly,
                   normalisation, scoring, design stacking, run output, text profiles
  taste/           building and enriching the taste profile (trainer, map, profile enrichment)
  api/             HTTP API (only the rate limiter exists so far)
```

Planned packages, each added when its first real module arrives:

| Package | Holds |
|---|---|
| `db/` | SQLAlchemy engine, `Base`, ORM models |
| `library/` | Radarr and Sonarr sync, movie, series, and season subjects |
| `artwork/` | naming, deploy with backup, restore, heal |
| `jobs/` | the Postgres-backed job queue, worker, schedules |

## Layering

Imports only point downward. `uv run lint-imports` enforces this with the contracts in
`pyproject.toml`.

```
6  api, cli, doctor
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

## Runtime data

Everything the application writes lives under one directory, `DATA_DIR` (default `data/` in
the repository, never committed). Only `marquee/config/` builds these paths.

```
data/
  models/     ONNX and other model files
  ml/         taste profiles, residual artifacts, zero-shot axes
  cache/      embeddings, poster downloads, thumbnails
  runs/       one folder per pipeline run: candidates, ranked output, run archive JSON
  backups/    posters replaced by a deploy
  text_profiles.json
```

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
