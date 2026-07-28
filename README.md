<div align="center">

# Marquee

**AI-powered poster selection for self-hosted media libraries.**

Marquee pulls every poster a title has, runs the candidates through a staged computer-vision
pipeline, and ranks them against a taste profile learned from your own picks — so a library of
thousands of titles ends up looking the way you would have made it look by hand. It plugs into an
existing Radarr and Sonarr setup and writes artwork your media server already reads.

[![CI](https://github.com/GautamChaudhri/Marquee/actions/workflows/ci.yml/badge.svg)](https://github.com/GautamChaudhri/Marquee/actions/workflows/ci.yml)
[![Python 3.12 | 3.13](https://img.shields.io/badge/python-3.12%20%7C%203.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![SvelteKit](https://img.shields.io/badge/SvelteKit-2-FF3E00?logo=svelte&logoColor=white)](https://kit.svelte.dev/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%2B-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![ONNX Runtime](https://img.shields.io/badge/ONNX%20Runtime-CPU%20%7C%20CUDA%20%7C%20OpenVINO-005CED?logo=onnx&logoColor=white)](https://onnxruntime.ai/)
[![Ruff](https://img.shields.io/badge/lint-ruff-D7FF64?logo=ruff&logoColor=black)](https://docs.astral.sh/ruff/)
[![License: GNU GPL v3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)
[![100% local](https://img.shields.io/badge/telemetry-none-success)](#privacy)

</div>

---

## Contents

- [Why](#why)
- [What it does](#what-it-does)
- [How the pipeline works](#how-the-pipeline-works)
- [Architecture](#architecture)
- [Engineering highlights](#engineering-highlights)
- [Tech stack](#tech-stack)
- [Getting started](#getting-started)
- [Configuration](#configuration)
- [API](#api)
- [Testing and CI](#testing-and-ci)
- [Privacy](#privacy)
- [Project status](#project-status)
- [Repository layout](#repository-layout)
- [Design documentation](#design-documentation)
- [Contributing and security](#contributing-and-security)
- [License](#license)

---

## Why

A title on TMDB can have thirty or more posters. Most of them are wrong for a given library: a
foreign-language plate, a text-plastered fan edit, a floating-head composite, a blurry upscale, a
release-date announcement graphic. The right one is a matter of _taste_ — and taste is exactly what
existing artwork managers cannot express.

The established tools here are [tinyMediaManager](https://www.tinymediamanager.org/) and
[MediaElch](https://github.com/Komet/MediaElch) — both excellent, mature applications that scrape
metadata and download artwork from TMDB, TheTVDB, and Fanart.tv, write NFO files, and name images for
Kodi, Plex, Emby, or Jellyfin. tinyMediaManager even ships a headless CLI so a server can scrape and
rename on a schedule. But when either one picks a poster _for_ you, it picks from a handful of
declarative rules: preferred poster size, an ordered list of accepted languages, and a fallback to
any image. When you want a specific picture instead, you open an image-chooser dialog and click
through the candidates by title.

Those rules describe a _file_, not a picture. "1000×1500, English, from Fanart.tv" is satisfied
equally by a gorgeous painted one-sheet and by a muddy render with four taglines and a laurel wreath
across the bottom. So you either accept whatever the rules return, or you choose by hand — which is
fine for fifty titles and impossible for five thousand.

**Marquee looks at the picture.** Every candidate is embedded with CLIP and DINOv2, scored for
aesthetic quality, measured for face and person geometry, palette, composition, and compression
artifacts, and read with OCR. Then it is ranked against a taste profile built from posters _you_
chose — a k-nearest-neighbour match in embedding space, not a generic quality score — and every
approval, override, and rejection you make feeds back into that profile. The longer you use it, the
more it agrees with you.

### How that differs in practice

|                        | tinyMediaManager / MediaElch                                                                                                                     | Marquee                                                                                                                                                                   |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Selection basis**    | Metadata fields: size, language tag, source                                                                                                      | The image itself: CLIP + DINOv2 embeddings, aesthetic score, face/person geometry, palette, composition, artifacts                                                        |
| **Personalization**    | None — the same rules give everyone the same answer                                                                                              | A taste profile learned from your own picks, with negative exemplars for art you rejected                                                                                 |
| **Text on the poster** | tinyMediaManager has a "prefer artwork without text" option for fanart, decided by the provider's language tag — neither tool inspects the image | OCR reads the text actually rendered on the candidate, requires the title to be present, and rejects anything carrying taglines, credits, review quotes, or release dates |
| **Duplicate designs**  | Every language and crop variant is a separate entry in the chooser                                                                               | Same-design variants are grouped, so you review distinct designs                                                                                                          |
| **Choosing by hand**   | An image chooser, one title at a time, in unranked provider order                                                                                | A ranked review queue: best candidate first, with the score breakdown that produced the order                                                                             |
| **Learning**           | None                                                                                                                                             | Every decision is an immutable feedback event feeding the profile and the ranking residual                                                                                |
| **Rollback**           | Overwrites the artwork file                                                                                                                      | Backs up what it replaced, with one-click restore and full audit history                                                                                                  |

Marquee is not a metadata manager and does not try to be one: it writes no NFOs and scrapes no cast
lists. It does one job — choosing the poster — and tries to do it better than a size-and-language
rule can.

## What it does

- **Fetches** every poster candidate TMDB has for a movie, series, or individual season.
- **Rejects** invalid art with absolute gates — resolution floors, aesthetic floors, off-style
  detection, exact-duplicate removal, and an OCR text gate that drops posters carrying anything
  beyond the title.
- **Groups** same-design variants (different crops or language plates of one design) so the review
  screen shows distinct _designs_, not near-identical duplicates.
- **Ranks** the survivors with a weighted feature model plus an optional bounded residual correction
  learned from your own approvals and rejections.
- **Deploys** the chosen artwork into the media folder next to the file, backing up whatever it
  replaced, with a one-click restore.
- **Learns** from every decision — approvals, overrides, and rejections become immutable feedback
  events that feed the taste profile and the ranking residual.
- **Runs it in bulk** through a durable job platform: batch a whole library, watch live progress,
  cancel mid-run, and review the results later.

<p align="center">
  <img src="frontend/e2e/activity-shell.spec.ts-snapshots/projection-room-shell-chromium-linux.png" width="850" alt="Marquee Activity workspace showing durable queue, history, and operations controls">
</p>

<p align="center"><sub>The Activity workspace keeps durable work, completed outcomes, and infrastructure diagnostics in one place.</sub></p>

## How the pipeline works

Two separate decisions, in a deliberate order.

**GATE** is absolute: identical thresholds for every title, removing candidates that are simply
invalid. **RANK** is relative: the best survivor for _this_ title wins, even if the whole field is
mediocre. Style preferences (a floating-head composite, say) are rank penalties rather than gates,
so a title whose only art is floating-head still gets its best available poster.

Stages are ordered by cost. Nothing expensive runs on a candidate that a cheaper signal can already
reject — the resolution gate reads TMDB metadata and spends no inference at all, and the embedding
gates run before the multi-pass OCR.

```mermaid
flowchart TD
    A["FETCH<br/><sub>TMDB candidates @ w500</sub>"] --> B["SHA-256 DEDUPE<br/><sub>exact duplicates</sub>"]
    B --> C{"GATE: resolution<br/><sub>metadata only — no inference</sub>"}
    C -->|reject| X1([dropped])
    C --> D["STYLE FEATURES<br/><sub>batched CLIP → k-NN taste + aesthetic</sub>"]
    D --> E{"GATE: style<br/><sub>aesthetic floor w/ k-NN rescue</sub>"}
    E -->|reject| X2([dropped])
    E --> F{"GATE: OCR text<br/><sub>PaddleOCR, title-only</sub>"}
    F -->|reject| X3([dropped])
    F --> G["STACK<br/><sub>DINO grouping of same-design variants</sub>"]
    G --> H["DETAIL FEATURES<br/><sub>DINOv2 k-NN, face/person geometry,<br/>palette, artifacts, typicality</sub>"]
    H --> I["RANK<br/><sub>weighted score + bounded residual</sub>"]
    I --> J["OUTPUT<br/><sub>re-download top designs at full resolution</sub>"]
    J --> K["REVIEW & DEPLOY<br/><sub>feedback returns to the taste profile</sub>"]
```

| Stage            | Module                   | What it does                                                                 |
| ---------------- | ------------------------ | ---------------------------------------------------------------------------- |
| FETCH            | `poster_sources/tmdb.py` | Download candidates at w500 into the attempt workspace                       |
| SHA-256          | `pipeline/deduper.py`    | Exact-duplicate removal                                                      |
| GATE: resolution | `pipeline/gate.py`       | Width floor from TMDB metadata — no inference spent                          |
| STYLE FEATURES   | `pipeline/features.py`   | Batched CLIP → `knn_sim` + aesthetic + metadata scalars                      |
| GATE: style      | `pipeline/gate.py`       | Aesthetic floor (with k-NN rescue) and off-style floor, before OCR           |
| OCR              | `pipeline/ocr_filter.py` | Multi-process PaddleOCR gate — title-only text by default                    |
| STACK            | `pipeline/stacker.py`    | DINO-based grouping of same-design variants                                  |
| DETAIL FEATURES  | `pipeline/features.py`   | DINOv2 k-NN, face/person geometry, CV palette, quality artifacts, typicality |
| RANK             | `pipeline/scorer.py`     | Weighted baseline, optionally corrected by a learned residual                |
| OUTPUT           | `pipeline/output.py`     | Rank-name the survivors; re-download the top designs at original resolution  |

### Taste matching is exemplar-based

`knn_sim` is the similarity-weighted mean cosine similarity to the _k_ nearest exemplars in the taste
profile — not cosine similarity to a single centroid. A centroid collapses a varied taste ("I like
stark minimal one-sheets _and_ painted 70s illustration") into a bland average that matches neither.
The profile is built from hand-picked posters, and optional negative exemplars penalise candidates
that sit closer to disliked art than to liked art.

### Ranking

`WeightedScorer` is the permanent baseline: a weighted sum over the extracted features, renormalised
over the features actually present on each candidate, so an optional feature dropping out never
distorts the score range. A residual model trained on accumulated feedback can then apply a
_bounded_ correction on top — and only when its frozen evidence, compatibility check, held-out
evaluation, and delta bound all pass. `SCORER=auto|weighted|residual` chooses the policy; `auto`
falls back to the baseline whenever a compatible residual is not available.

## Architecture

An async FastAPI service, a SvelteKit UI, PostgreSQL for state, and a durable job platform that
keeps every heavy workload off the request loop.

```mermaid
flowchart LR
    subgraph Client
        UI["Svelte 5 / SvelteKit 2 UI<br/><sub>typed client generated from OpenAPI</sub>"]
    end

    subgraph Service["FastAPI service"]
        API["HTTP API<br/><sub>API-key auth, rate limits, SSE</sub>"]
        SUP["Supervisor<br/><sub>spawns worker + scheduler</sub>"]
    end

    subgraph Jobs["Durable job platform"]
        Q[("PgQueuer<br/>queue")]
        W["Worker<br/><sub>claims and executes</sub>"]
        SCH["Scheduler<br/><sub>recurring jobs</sub>"]
    end

    RUN["Internal runner<br/><sub>own process group — CLIP / DINO / OCR</sub>"]
    DB[("PostgreSQL<br/><sub>SQLAlchemy async + Alembic</sub>")]

    subgraph External["Your network"]
        ARR["Radarr / Sonarr"]
        TMDB["TMDB"]
        MEDIA[("Media folders")]
    end

    UI -->|"REST + SSE"| API
    API -->|enqueue| Q
    API --> DB
    SUP --> W
    SUP --> SCH
    SCH -->|enqueue| Q
    Q --> W
    W -->|"spawn"| RUN
    W --> DB
    RUN -->|"artifacts + progress"| W
    API --> ARR
    RUN --> TMDB
    W --> MEDIA
```

### Layers

| Layer            | Path                                                        | Responsibility                                                                     |
| ---------------- | ----------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Configuration    | `marquee/config.py`                                         | Singleton `settings`: env, path derivation, Radarr/Sonarr path translation         |
| Pipeline knobs   | `marquee/core/pipeline_config.py`                           | Separate `pipeline_settings` singleton: gates, weights, normalization, model paths |
| DB configuration | `marquee/core/configuration.py`                             | Versioned, revisioned configuration authority with a closed key catalog            |
| Integrations     | `marquee/core/arr_clients/`, `marquee/core/poster_sources/` | Async HTTP clients built in `lifespan()` and attached to `app.state`               |
| Pipeline         | `marquee/pipeline/`                                         | The staged selection stages and the records that flow between them                 |
| ML               | `marquee/ml/`                                               | ONNX inference wrappers, taste profile store, calibration, normalization           |
| Jobs             | `marquee/core/jobs/`                                        | Job manifest, delivery kernel, workers, scheduler, supervisor                      |
| Persistence      | `marquee/models/`, `alembic/`                               | SQLAlchemy async models; schema owned by Alembic migrations                        |
| API              | `marquee/api/`                                              | Routes, auth, request limits, serializers                                          |
| UI               | `frontend/`                                                 | SvelteKit 5 app consuming the generated OpenAPI client                             |

### Key design decisions

- **Runner containment.** Heavy pipeline work never touches the API or worker event loop. The job
  handler launches `python -m marquee.core.jobs.internal_runner` in its own process group; the child
  may only write inside its attempt workspace, and the handler converts what it produced into
  artifacts and a run record. A wedged or cancelled run cannot take the service with it.
- **Enqueue vs inline.** Anything GPU-bound, long-running, or that mutates a media file goes through
  the job platform and returns a job summary. Genuinely cheap work — reads, listings, configuration
  changes, in-memory rescoring, library sync — stays inline.
- **A closed job catalog.** `jobs/manifest.py` is the only place a job type can exist, and
  `jobs/kernel_handlers.py` is the scope boundary: a handler family that is not bound there cannot
  execute, no matter what is enqueued.
- **One fenced write path.** Every poster write goes through a single canonical mutation handler
  that backs up the existing artwork, validates, publishes, updates projections, and records audit
  history.
- **Path translation is centralised.** Radarr and Sonarr run in their own mount namespaces;
  `settings.translate_radarr_path()` / `translate_sonarr_path()` are the only supported way to map
  their container paths onto host-visible media paths.
- **The API contract is a build artifact.** The OpenAPI schema is exported to `design/api-schema.json`,
  committed, and CI-checked; the frontend's TypeScript client is generated from it, so a backend
  change that breaks the contract fails the build rather than the browser.

## Engineering highlights

Things in here that were interesting to build:

- **A durable, restart-safe job platform** on PgQueuer and PostgreSQL — a closed job manifest,
  parent/child batch coordination, persisted progress events streamed to the UI over SSE,
  cooperative cancellation, retry capability, recurring schedules, runtime-instance safety so two
  workers cannot claim the same work, and supervised worker/scheduler child processes.
- **Cost-ordered inference.** Gates run as early as their inputs allow, so the expensive stages only
  ever see candidates that already survived the cheap ones — the resolution gate rejects undersized
  art straight from TMDB metadata, before a single tensor is allocated, and the OCR pass (by far the
  most expensive stage) only ever sees candidates that already cleared the embedding gates.
- **A hardware abstraction for ONNX.** Every session is created through `ml/hardware.py`, which
  resolves CUDA, OpenVINO, CoreML, or CPU execution providers from one place — the same code path
  runs on an RTX 3070 and on a fanless Intel box.
- **Process-isolated ML execution** with a defined runner protocol, structured progress reporting
  back to the parent, workspace fencing, and log capture.
- **A learned preference model in two layers** — an exemplar k-NN taste profile plus a bounded
  residual correction over a hand-weighted baseline, so the system degrades to a sane default
  instead of to nonsense when evidence is thin.
- **Versioned database configuration.** Runtime configuration lives in revisioned database records
  validated against a closed catalog; an unknown key rejects at startup, so removing a knob requires
  a migration that strips it from the stored revision.
- **Security posture for a self-hosted service**: static API key accepted three ways (Bearer header,
  `X-Api-Key`, query parameter) with constant-time comparison, per-IP brute-force lockout, fail-closed
  behaviour when no key is configured, loopback exemption, request size limits, and per-route
  cooldowns.

## Tech stack

| Area        | Choices                                                                                                                              |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| Backend     | Python 3.12/3.13, FastAPI, Uvicorn, Pydantic v2 + pydantic-settings                                                                  |
| Persistence | PostgreSQL, SQLAlchemy 2 (async, asyncpg), Alembic                                                                                   |
| Jobs        | PgQueuer, supervised worker/scheduler processes, SSE progress streams                                                                |
| Vision / ML | ONNX Runtime (CPU / CUDA / OpenVINO), CLIP ViT-B/32, DINOv2 ViT-S/14, PaddleOCR, SCRFD, YOLO11n, LAION aesthetic head, OpenCV, NumPy |
| Frontend    | Svelte 5, SvelteKit 2, TypeScript, Vite, generated OpenAPI client, Plotly (taste map)                                                |
| Quality     | pytest + pytest-asyncio, Vitest, Playwright + axe accessibility checks, Ruff, svelte-check, ESLint, Prettier                         |
| Packaging   | Hatchling, Docker Compose (API / worker / scheduler / web / Caddy split)                                                             |

## Getting started

For local development, run the API and UI directly. Inference stays local; nothing here phones home.

### Prerequisites

| Requirement                  | Notes                                                      |
| ---------------------------- | ---------------------------------------------------------- |
| Python 3.12 or 3.13          | `python --version`                                         |
| PostgreSQL 16+               | A database and a role that owns it                         |
| Node.js 22+                  | For the SvelteKit frontend                                 |
| A TMDB API read access token | Free — <https://www.themoviedb.org/settings/api>           |
| Radarr and/or Sonarr         | Optional for a first look, required to sync a real library |
| An NVIDIA GPU                | Optional. CPU-only works; it is just slower                |

### 1. Backend environment

```bash
git clone https://github.com/GautamChaudhri/Marquee.git
cd Marquee
python -m venv .venv
source .venv/bin/activate
```

On Linux, pick **exactly one** accelerator extra — the three ship conflicting `onnxruntime` builds,
and installing more than one breaks inference:

```bash
pip install -e ".[dev,nvidia]"   # NVIDIA GPU (CUDA)
pip install -e ".[dev,cpu]"      # CPU only
pip install -e ".[dev,intel]"    # Intel iGPU / Arc via OpenVINO
```

Apple Silicon needs no accelerator extra — CoreML ships in the base `onnxruntime`, so `.[dev]` is
enough.

That is everything the API, the job platform, and the test suite need. Add the ML extras for the OCR
gate and the model-export tooling, and the viz extras for the taste map's UMAP projection:

```bash
pip install -e ".[ml,viz]"
```

PaddlePaddle — PaddleOCR's runtime — is only pulled in automatically on macOS, so install it
yourself on Linux:

```bash
pip install paddlepaddle                                                                  # CPU
pip install paddlepaddle-gpu==3.3.1 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/  # CUDA 12.6
```

### 2. Configuration

```bash
cp .env.example .env
```

Fill in at minimum:

```ini
DB_URL=postgresql+asyncpg://marquee:your-password@127.0.0.1:5432/marquee
API_KEY=generate-a-long-random-string
TMDB_READ_ACCESS_TOKEN=your-tmdb-bearer-token

RADARR_URL=http://localhost:7878
RADARR_API_KEY=your-radarr-key
SONARR_URL=http://localhost:8989
SONARR_API_KEY=your-sonarr-key

# Map *arr container paths onto host paths, if they differ
RADARR_PATH_PREFIX=/movies
RADARR_MEDIA_PATH=/mnt/media/Movies
SONARR_PATH_PREFIX=/tv
SONARR_MEDIA_PATH=/mnt/media/TV
```

`.env.example` documents every knob with its default. `DEBUG=true` disables the API-key gate and the
rate-limit cooldowns and serves `/docs` — development only.

### 3. Database

```bash
createdb marquee                      # if it does not exist yet
python -m marquee.db_migration        # applies migrations + installs PgQueuer
```

### 4. Model files

Model weights are not committed (they are hundreds of megabytes). Export them once into
`marquee/ml/models/` — this needs the `[ml]` extras:

```bash
python -m marquee.ml.clip_export      # clip-vit-b-32.onnx  — CLIP ViT-B/32 vision tower
python -m marquee.ml.dino             # dinov2-vits14.onnx  — DINOv2 ViT-S/14
python -m marquee.ml.zeroshot         # zero-shot style axes, from the CLIP text tower
python -m marquee.ml.aesthetic        # converts the LAION aesthetic head to its .npz sidecar
```

Three weights come from upstream rather than from an export step — drop them in
`marquee/ml/models/`:

| File                         | Source                                                                                                                                               |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sa_0_4_vit_b_32_linear.pth` | LAION aesthetic predictor v1 (B/32 linear head); converted to a NumPy sidecar by the command above, after which torch is no longer needed at runtime |
| `scrfd_500m_bnkps.onnx`      | InsightFace SCRFD, for face geometry                                                                                                                 |
| `yolo11n.onnx`               | Ultralytics YOLO11n, for person detection — `yolo export model=yolo11n.pt format=onnx dynamic=True`                                                  |

The API, the job platform, the library views, and the full test suite all run without any model
files; only the inference stages of a pipeline run need them.

### 5. Run it

```bash
uvicorn marquee.main:app --reload --host 127.0.0.1 --port 3165
```

The API supervises an embedded worker and scheduler in development (`JOB_EMBEDDED_WORKERS=true`), so
that one command gives you a working job platform. In another shell:

```bash
cd frontend
npm ci
npm run dev
```

Then open the UI, point Settings at your Radarr/Sonarr, and run a library sync.

### Containers

A Compose stack is provided that splits the API, worker, scheduler, web UI, and a Caddy reverse
proxy so a GPU-bound run never occupies Uvicorn:

```bash
docker compose -f docker/docker-compose.yml up -d
```

Add `docker/compose.nvidia.yml` for the GPU reservation on the worker.

## Configuration

Configuration has two ownership boundaries. Secrets, connection details, paths, and other
restart-owned values come from environment variables or `.env`. Safe runtime knobs are stored as
immutable, versioned PostgreSQL revisions and apply to the next job:

- **`Settings`** (`marquee/config.py`) — application runtime: `DB_URL`, `API_KEY`, `DEBUG`,
  `HOST`/`PORT`, media paths and `*arr` path translation, rate limits, backups,
  `JOB_EMBEDDED_WORKERS`, `MOVIE_POSTER_FORMAT`, `SERIES_POSTER_FORMAT`.
- **`PipelineSettings`** (`marquee/core/pipeline_config.py`) — the pipeline's tunables.

<details>
<summary><b>Representative pipeline knobs</b></summary>

| Knob                        | Default         | Meaning                                                                         |
| --------------------------- | --------------- | ------------------------------------------------------------------------------- |
| `AI_MODEL`                  | `clip-vit-b-32` | Embedding model; also selects the matching taste profile                        |
| `EXECUTION_PROVIDER`        | `auto`          | ONNX execution provider (CUDA / OpenVINO / CoreML / CPU)                        |
| `GATE_MIN_WIDTH`            | `500`           | Resolution floor, applied from metadata before any inference                    |
| `GATE_MIN_AESTHETIC`        | `4.5`           | Aesthetic floor; `GATE_AESTHETIC_RESCUE_KNN` (`0.55`) is the taste-based rescue |
| `GATE_MIN_KNN_SIM`          | `0.45`          | Off-style floor on taste similarity                                             |
| `K_NEIGHBORS`               | `10`            | Neighbours used for the k-NN taste similarity                                   |
| `TASTE_NEG_WEIGHT`          | `1.0`           | Weight of negative exemplars in the taste match                                 |
| `OCR_TEXT_MODE`             | `title_only`    | What text a poster is allowed to carry                                          |
| `OCR_MAX_RESIDUAL_BOXES`    | `0`             | Non-title text boxes tolerated                                                  |
| `OCR_WORKERS`               | `8`             | PaddleOCR process-pool size                                                     |
| `STACK_ENABLED`             | `true`          | DINO grouping of same-design variants                                           |
| `SCORER`                    | `auto`          | `auto` / `weighted` / `residual` ranking policy                                 |
| `WEIGHT_*`                  | —               | Per-feature weights of the baseline scorer                                      |
| `TMDB_POSTER_SIZE`          | `w500`          | Working resolution during scoring                                               |
| `PIPELINE_BATCH_MAX_MOVIES` | `500`           | Batch-run ceiling                                                               |

</details>

## API

More than 100 documented paths across pipeline runs and review, library and sync, jobs and batches, feedback,
taste tooling, configuration, backups, and system metrics. With `DEBUG=true` the interactive docs
are served at `/docs`; the committed schema lives at [`design/api-schema.json`](design/api-schema.json).

```http
POST /api/pipeline/movie/{movie_id}/run     # queue a poster run for one movie
POST /api/pipeline/tv/series/{series_id}/run  # …or for a series and its seasons
POST /api/pipeline/batch                    # queue a run across the library
GET  /api/pipeline/runs/{run_id}            # the archived run: candidates, features, scores
POST /api/pipeline/runs/{run_id}/rescore    # re-rank an archived run with new weights — no re-inference
GET  /api/pipeline/review-queue             # what is waiting for a human decision
POST /api/feedback                          # approve / override / reject — deploys and trains
GET  /api/jobs/{job_id}/events              # SSE progress stream for one job
GET  /api/jobs/events/stream                # SSE stream for all job activity
POST /api/sync/all                          # pull the library from Radarr and Sonarr
```

Every route except the `/health`, `/health/live`, and `/health/ready` probes requires the API key,
presentable as `Authorization: Bearer <key>`,
`X-Api-Key: <key>`, or `?apikey=<key>`.

Regenerating the contract after a route change:

```bash
python scripts/export_openapi.py && cd frontend && npm run api:generate
```

## Testing and CI

```bash
pytest                                   # backend suite
pytest tests/test_pipeline.py            # one file
ruff check marquee tests scripts         # lint gate
ruff format --check marquee tests scripts # format gate
alembic check                            # models must match migrations

cd frontend
npm run check      # svelte-check, zero errors and zero warnings
npm run lint       # prettier + eslint
npm run test:unit  # vitest
npm run test:e2e   # playwright + axe, against a hermetic synthetic backend
```

**900+ backend tests, 110+ frontend unit tests, and 14 end-to-end scenarios.** The backend suite runs against
**PostgreSQL, not SQLite** — `tests/conftest.py` provisions an isolated `test_<uuid>` schema inside
the configured database for the session and drops it afterwards, so point `DB_URL` at a disposable
database before running it. A handful of migration and backup tests create and drop their own
throwaway databases; those need a role with `CREATEDB` (or a superuser) and error out without one.

The suite needs no GPU, no model weights, and none of the heavy ML extras — inference is exercised
through the wrappers and fixtures rather than by loading real networks. The Playwright run is equally
hermetic: it boots a synthetic
backend on a local port and the built SvelteKit app against it, so it never touches a real library,
database, or media file.

CI runs on pushes to `main`, pull requests targeting `main`, and manual dispatches. This keeps topic
branches inexpensive while making the merge gate visible:

| Job                     | Steps                                                                                                                                                                 |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Backend (3.12 and 3.13) | dependency check → Ruff lint/format → fresh schema migration + PgQueuer install → `alembic check` → full pytest suite with a 70% coverage floor → OpenAPI drift check |
| Frontend                | generated-client drift → svelte-check (zero warnings) → ESLint + Prettier → Vitest → production build + bundle budget → Playwright, axe, and visual regression        |
| CodeQL                  | static security analysis for Python and TypeScript, weekly, on `main`, and on pull requests                                                                           |

## Privacy

**Marquee collects nothing, reports nothing, and phones home to nobody.**

- **No telemetry, no analytics, no crash reporting, no usage statistics.** There is no code in this
  repository that sends any information about you, your library, or your usage anywhere.
- **No accounts, no cloud service, no vendor backend.** Marquee is a program you run on your own
  hardware. There is nothing to sign up for.
- **All inference is local.** CLIP, DINOv2, PaddleOCR, face and person detection, and the aesthetic
  head all run on your machine through ONNX Runtime. No image is ever uploaded to a hosted model API.
- **Your taste profile is a file on your disk.** Feedback, ranking evidence, and the trained profile
  live in your PostgreSQL database and your data directory, and go nowhere else.
- **The only outbound traffic** is to TMDB, to look up titles and download poster candidates, and to
  the Radarr/Sonarr instances _you_ configure — which are on your own network. (The one-time model
  setup also fetches weights from their upstream sources — Hugging Face, `torch.hub`, and PaddleOCR's
  model server. After that, inference never leaves the machine.)
- **Media files are never modified.** Marquee writes poster images beside your media and backs up
  whatever it replaces. It does not touch, transcode, or rewrite the media files themselves.

## Project status

Actively developed at **v0.1.0**, pre-1.0, and honest about it: this is a personal self-hosted tool that is
maturing in the open, not a finished product with a support contract.

**Working today**

- Movie, series, and season poster runs, single and batched, with live progress and cancellation
- Review, deployment with backup, restore, and immutable feedback capture
- Taste profile building and the taste map, plus residual ranking on top of the weighted baseline
- Library sync from Radarr and Sonarr, the durable job platform, recurring schedules, backups,
  and system metrics

**Deliberately out of scope**

The project previously carried HDR/Dolby Vision management, letterbox detection, and audio/subtitle
management. All three were removed to get poster selection right first. If a change appears to need
`ffmpeg`, `mkvmerge`, or track manipulation, it is out of scope.

**Next**

- Cold-start onboarding ("Rank Test") is built but gated off (`ONBOARDING_ENABLED=false`) pending
  a shipped seed bundle
- Poster sources beyond TMDB
- Broader accuracy work on the ranking residual as feedback accumulates

## Repository layout

```
marquee/
├── api/            FastAPI routes, auth, request limits, serializers
├── core/
│   ├── jobs/       durable job platform — manifest, delivery kernel, worker, scheduler, handlers
│   ├── arr_clients/  async Radarr and Sonarr clients
│   └── poster_sources/  TMDB client
├── ml/             ONNX wrappers, taste profile, calibration, hardware/provider selection
├── models/         SQLAlchemy async models
├── pipeline/       the staged selection pipeline
├── config.py       application settings singleton
└── main.py         app wiring and lifespan
alembic/            migrations (schema is never created by the app)
frontend/           Svelte 5 / SvelteKit 2 UI
design/             design documentation and the committed OpenAPI schema
docker/             Compose stack and images
scripts/            developer tooling
tests/              pytest suite
```

## Design documentation

`design/poster-pipeline.md` is the authoritative pipeline reference.

- [`design/overview.md`](design/overview.md) — application-level architecture
- [`design/poster-pipeline.md`](design/poster-pipeline.md) — stages, gates, features, scoring
- [`design/library.md`](design/library.md) — sync, deployment, restore
- [`design/job-platform.md`](design/job-platform.md) — job execution, batches, schedules

## Contributing and security

Development happens on public topic branches and draft pull requests so decisions and progress stay
visible without treating `main` as a scratch branch. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the
workflow and quality gates.

Please report sensitive issues through the process in [`SECURITY.md`](SECURITY.md), not in a public
bug report.

## License

[GNU GPL v3](LICENSE) © 2026 Gautam Chaudhri
