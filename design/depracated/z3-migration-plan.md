# Marquee v2 — Migration Phases & Checklist

> **See `design/todos.md` for the canonical task list organized by phase.**
> This document provides the high-level phase overview and cross-references.

---

## Migration Phases

### Phase 0: Project Scaffold ✅ DONE

- [x] Create project structure
- [x] Copy design docs
- [x] Set up packaging (`pyproject.toml`, `requirements.txt`)
- [x] FastAPI app skeleton with health check
- [x] Config system with Pydantic-settings
- [x] Database layer (async SQLAlchemy + SQLite + WAL mode)

### Phase 1: Core Infrastructure ✅ DONE

- [x] Implement new model schema
- [x] Path hardening
- [x] Fix DB/cache/staging paths
- [x] Tests for all models

### Phase 2: Integration Layer ✅ DONE

- [x] Arr clients salvaged + adapted
- [x] TMDB client extended
- [x] Sync service
- [x] API route stubs + working sync endpoint
- [x] Request logging middleware
- [x] API rate limiting
- [x] Verified against real Sonarr/Radarr (489 movies, 103 series, 455 seasons, 9255 episodes)

### Phase 3: AI Pipeline ← CURRENT

- [ ] CLIP ONNX export script (adapt from DINOv2 pattern)
- [ ] CLIP embedding extraction
- [ ] SHA-256 + pHash dedup pipeline stage
- [ ] PaddleOCR filter (refactor from old experiments)
- [ ] Taste profile trainer + scorer
- [ ] Pipeline orchestrator
- [ ] File write path validation
- [ ] **→ See `design/todos.md` § Phase 3 for full list**

### Phase 4: API & Webhooks

- [ ] Library CRUD routes
- [ ] Pipeline trigger routes
- [ ] Webhook endpoints (Radarr/Sonarr)
- [ ] Taste profile routes
- [ ] Settings routes
- [ ] **→ See `design/todos.md` § Phase 4 for full list**

### Phase 5: Poster Management

- [ ] Poster cache (keyed by TMDB ID)
- [ ] Self-healing periodic scan
- [ ] Media upgrade restoration
- [ ] **→ See `design/todos.md` § Phase 5 for full list**

### Phase 6: Web UI

- [ ] Frontend framework scaffold
- [ ] Library grid view
- [ ] Poster selection UI
- [ ] Manual override + taste feedback
- [ ] **→ See `design/todos.md` § Phase 6 for full list**

### Phase 7: Docker & Release

- [ ] Switch to Alembic migrations (remove `create_all()`)
- [ ] Dockerfile + Docker Compose
- [ ] GPU passthrough config
- [ ] User documentation
- [ ] **→ See `design/todos.md` § Phase 7 for full list**

### Phase N: Deferred Features

- [ ] Additional poster sources (Fanart.tv, TheTVDB, TVmaze)
- [ ] HDR/DV tracking
- [ ] Backdrops, logos, banners
- [ ] **→ See `design/todos.md` § Phase N for full list**

---

## Key Design Decisions

1. **Separate tables** for Movie, Series, Season, Episode — different file structures warrant different models. Shared `ArtworkMixin` for poster state.
2. **`poster_` column prefix** — enables clean extraction to a separate `artwork` table when adding backdrops/logos later.
3. **CLIP over DINOv2** — semantic understanding > fine-grained detail for taste matching.
4. **PaddleOCR over Florence-2** — CPU-only, no GPU needed, multiprocessing support.
5. **TMDB-first** — sole poster source for initial pipeline. Additional sources deferred to Phase N.
6. **Direct PyTorch in container** over Ollama sidecar — simpler v1 deployment.
7. **SQLite** — right for personal library scale. WAL mode enabled for concurrent read/write.
8. **FastAPI** — proven, async-first, good DX.
9. **Test-first** — we're doing TDD this time.
