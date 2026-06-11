# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (dev + ML extras)
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"

# Run server
uvicorn marquee.main:app --reload        # port 3165
# or
python -m uvicorn marquee.main:app --reload

# Tests
pytest                                   # all tests
pytest tests/test_pipeline_revised.py   # single file
pytest -k "test_normalization"          # single test by name
pytest --cov=marquee                    # with coverage

# Lint
ruff check marquee tests
ruff format marquee tests
```

The `ml` extras (`paddlepaddle`, `paddleocr`, `torch`, `transformers`) are heavy. Install with `pip install -e ".[dev]"` to skip them during general development; the pipeline tests that need OCR or CLIP will be skipped or fail with import errors.

## Architecture

Marquee is a FastAPI service that fetches movie/TV posters from external sources and selects the best one using an AI scoring pipeline.

### Layers

**`marquee/config.py`** — singleton `settings` (pydantic-settings). All env vars, path derivation, and Radarr/Sonarr path translation live here. Import `settings` directly; never instantiate `Settings` yourself.

**`marquee/core/pipeline_config.py`** — separate singleton `pipeline_settings` (also pydantic-settings) for the AI pipeline's tunable knobs: gate thresholds, scorer weights, normalization ranges, model paths. Every gate and scorer reads from this; override via env vars or `.env`.

**`marquee/core/arr_clients/`** — async HTTP clients for Radarr and Sonarr. They're constructed at startup in `lifespan()` (`main.py`) and attached to `app.state`.

**`marquee/core/poster_sources/`** — poster source clients (TMDB currently). Emit `PosterCandidate` dataclasses consumed by the pipeline.

**`marquee/pipeline/`** — the poster selection pipeline, called from `marquee/api/routes/test_pipeline.py`. Stages run in order:

| Stage | Module | What it does |
|---|---|---|
| FETCH | `poster_sources/tmdb.py` | Download candidates at w500 to staging |
| SHA-256 | `pipeline/deduper.py` | Exact-duplicate removal |
| GATE:resolution | `pipeline/gate.py` | Width floor from TMDB metadata — no inference spent |
| STYLE FEATURES | `pipeline/features.py` | Batched CLIP → knn_sim + aesthetic + metadata scalars |
| GATE:style | `pipeline/gate.py` | Aesthetic floor (with knn rescue), off-style floor — before OCR |
| OCR | `pipeline/ocr_filter.py` | PaddleOCR gate — strict title-only text by default (`OCR_MAX_RESIDUAL_BOXES=0`); emits title bbox + residual boxes |
| pHash | `pipeline/deduper.py` | Near-dupe removal on OCR survivors |
| DETAIL FEATURES | `pipeline/features.py` | face_area, title colorfulness, sharpness, text_residual on survivors |
| GATE:fan-junk | `pipeline/gate.py` | Optional combo gate (off by default) |
| RANK | `pipeline/scorer.py` | Weighted sum over normalized features → `CandidateScore` list |
| OUTPUT | `pipeline/output.py` | Rename ranked files; re-download top-5 at full resolution |

**`pipeline/types.py`** defines the shared data records: `OCRCandidateResult`, `FeatureVector`, `CandidateScore`. These flow between all stages.

**`marquee/ml/`** — ML inference wrappers: hardware/provider selection (`hardware.py` — every ONNX session goes through it; `EXECUTION_PROVIDER=auto` resolves CUDA/OpenVINO/CoreML/CPU and sizes the CLIP batch + OCR worker pool), CLIP embedding (`embedding.py`, batched), aesthetic scorer (`aesthetic.py`, numpy sidecar — torch is export-only), face detector (`face.py`), colorfulness (`colorfulness.py`), taste profile store (`taste_store.py` — weighted k-NN + optional negative exemplars), normalization (`normalize.py`). Model files live in `marquee/ml/models/` (not committed; export/download required). Deployment matrix: `design/06-multi-platform-deployment.md`.

**`marquee/models/`** — SQLAlchemy ORM models (`Movie`, `Series`, `Season`, `Episode`) backed by async SQLite (`aiosqlite`). `marquee/database.py` manages the engine/session factory singleton.

**`marquee/core/sync_service.py`** — polls Radarr/Sonarr to upsert media into the DB.

### Key design decisions

**GATE then RANK**: The pipeline makes two separate decisions. GATE uses absolute hard thresholds (same for every movie) to remove invalid/junk candidates. RANK is relative — the best surviving candidate wins even if all are mediocre. Floating-head style is a rank penalty, not a gate — so a movie with only floating-head art still gets its best poster.

**k-NN over centroid**: Style similarity (`knn_sim`) is the similarity-weighted mean cosine to the k nearest exemplars in the taste profile (softmax weighting by default), not cosine to a single centroid. The taste profile (`taste_profile.clip-vit-b-32.npz`) is built from ~430 hand-picked posters in `marquee/experiments/training_data/`; optional negative exemplars in `marquee/experiments/negative_data/` penalize candidates that sit closer to disliked posters than liked ones.

**Cheapest signal first**: gates run as early as their inputs allow — resolution from metadata before any inference, embedding gates before the expensive multi-pass OCR. Gate semantics are identical; only evaluation order moved.

**Path mapping**: Radarr/Sonarr run in their own mount namespace. `settings.translate_radarr_path()` / `translate_sonarr_path()` rewrite paths using `{RADARR,SONARR}_PATH_PREFIX` → `{RADARR,SONARR}_MEDIA_PATH`.

### Tests

`tests/conftest.py` redirects the database to a `tmp_path` for the whole session. Tests that exercise the full pipeline (OCR, CLIP) require ML extras and real model files; unit tests of individual stages (gate thresholds, normalization math, scorer weights) work without them.

### Design docs

`design/` contains the full project spec and pipeline design. `04-revised-pipeline-design.md` is the authoritative reference for the current pipeline; `04-agent-build-instructions.md` has the build checklist.
