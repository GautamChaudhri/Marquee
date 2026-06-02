# Marquee v2 — Quick Reference & Migration Checklist

## Executive Summary

**What exists**: A FastAPI backend that syncs from Radarr and fetches TMDB posters (no AI). Plus standalone experiment scripts that do OCR filtering and DINOv2 taste profiling. These two halves have never been connected.

**What we're building**: A unified pipeline application that syncs → fetches → deduplicates → OCR-filters → AI-scores → selects posters. All running locally on consumer hardware.

## Salvageable Assets

| Asset | How to reuse |
|---|---|
| `goals/Marquee.md` | **Design bible** — do not modify, build to this spec |
| `experiments/filtering/filter_posters_ocr.py` | Refactor into `PosterTextFilter` class in `marquee/filtering/` |
| `experiments/profiling/export_dinov2.py` | Adapt pattern for CLIP ONNX export |
| `experiments/profiling/profiling_utils.py` | Extract `cosine_similarity`, `extract_color_histogram`, `preprocess_image` |
| `backend/app/core/arr_clients/` | Carry forward as-is, add `tvdb_id` to `tmdb_id` resolution |
| `backend/app/core/external_databases/tmdb.py` | Extend with TV endpoints and `/find` |
| `backend/app/database.py` | Keep engine/session pattern, new models |
| `backend/app/config.py` | Extend with new feature configs |

## Files to Discard Entirely

| File | Why |
|---|---|
| `backend/app/models/movie.py` | Redesign as single `Media` model |
| `backend/app/models/series.py` | Wrong domain (HDR tracking), merge into `Media` |
| `backend/app/schemas/media.py` | MicroManagerr schemas, not relevant |
| `backend/app/core/image_fetcher.py` | Replace with unified pipeline stage |
| `backend/app/core/library_scanner.py` | Merge into pipeline |
| `backend/app/core/sync_service.py` | Redesign for single `Media` model |
| `backend/app/api/routes/*.py` | Redesign for new API surface |
| `experiments/filtering/filter_posters.py` | Florence-2 GPU-heavy alternative — not needed |
| `CLAUDE.md` / `GEMINI.md` | Rebuild from scratch |
| `README.md` | Rebuild from scratch |

## New Project Structure (Target)

```
Marquee/
├── goals/                    # Keep goals doc as spec
├── marquee/                  # Main package
│   ├── __init__.py
│   ├── main.py               # FastAPI app + uvicorn
│   ├── config.py             # Pydantic settings
│   ├── database.py           # Async SQLAlchemy setup
│   ├── models/
│   │   ├── __init__.py
│   │   ├── base.py           # TimestampMixin
│   │   ├── media.py          # Single Media table
│   │   └── poster_candidate.py
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── media.py          # Media Pydantic models
│   │   └── pipeline.py       # Pipeline result models
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py           # FastAPI dependencies
│   │   └── routes/
│   │       ├── sync.py
│   │       ├── pipeline.py
│   │       ├── library.py
│   │       ├── webhooks.py
│   │       ├── taste.py
│   │       └── settings.py
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── orchestrator.py   # Pipeline orchestrator
│   │   ├── fetch.py          # Stage 1: Fetch from APIs
│   │   ├── dedup.py          # Stage 2: SHA-256 + pHash
│   │   ├── ocr_filter.py     # Stage 3: PaddleOCR text check
│   │   └── ai_score.py       # Stage 4: CLIP embedding + scoring
│   ├── core/
│   │   ├── arr_clients/      # Radarr/Sonarr HTTP clients (salvaged)
│   │   ├── poster_sources/   # TMDB, Fanart, TVDB, TVmaze clients
│   │   └── cache/            # Poster cache manager
│   └── ml/
│       ├── clip.py           # CLIP model wrapper (ONNX/onnxruntime)
│       ├── taste.py          # Taste profile training/scoring
│       └── color.py          # LAB color histogram (salvaged)
├── tests/
│   ├── test_pipeline.py
│   ├── test_filtering.py
│   └── ...
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── alembic/
├── requirements.txt
├── pyproject.toml
└── README.md
```

## Migration Phases

### Phase 0: Git Init & Project Scaffold
- [ ] Create new repo `Marquee` (clean git history)
- [ ] Set up project structure
- [ ] Copy `goals/Marquee.md` as spec
- [ ] Set up packaging (`pyproject.toml`, `requirements.txt`)

### Phase 1: Core Infrastructure
- [ ] Database layer with new `Media` model
- [ ] Config system
- [ ] FastAPI app skeleton with health check
- [ ] Tests for database layer

### Phase 2: Integration Layer
- [ ] Arr clients (salvage from old code)
- [ ] TMDB client (extend from old code)
- [ ] Fanart.tv, TVDB, TVmaze clients (new)
- [ ] Sync service for single Media model
- [ ] Tests for sync

### Phase 3: AI Pipeline
- [ ] CLIP ONNX export script
- [ ] CLIP embedding extraction
- [ ] SHA-256 dedup (salvage from old code)
- [ ] pHash near-dedup (new)
- [ ] PaddleOCR filter (refactor from old code)
- [ ] Taste profile trainer
- [ ] Poster scorer/selector
- [ ] Pipeline orchestrator
- [ ] Tests for each stage

### Phase 4: API & Webhooks
- [ ] Library CRUD routes
- [ ] Pipeline trigger routes
- [ ] Webhook endpoints
- [ ] Taste profile routes
- [ ] Tests for API

### Phase 5: Poster Management
- [ ] Poster cache (keyed by TMDB ID)
- [ ] Self-healing periodic scan
- [ ] Media upgrade restoration

### Phase 6: Web UI
- [ ] React/Vue frontend
- [ ] Library grid view
- [ ] Poster selection UI
- [ ] Manual override + feedback

### Phase 7: Docker & Docs
- [ ] Dockerfile + compose
- [ ] GPU passthrough config
- [ ] README
- [ ] User docs

---

## Key Design Decisions Made

1. **Single `media` table** per goals doc — movies and shows in one table
2. **CLIP over DINOv2** — semantic understanding > fine-grained detail for taste matching
3. **PaddleOCR over Florence-2** — CPU-only, no GPU needed, multiprocessing support
4. **Direct PyTorch in container** over Ollama sidecar — simpler v1 deployment
5. **SQLite** — right for personal library scale
6. **FastAPI** — proven, async-first, good DX
7. **Test-first** — we're doing TDD this time
