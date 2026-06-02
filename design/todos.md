# Marquee — TODOs & Deferred Items

Organized by the phase when attention is needed. Reference `design/03-migration-plan.md` for phase definitions.

---

## Phase 1 — Core Infrastructure (current)

- [x] ~~CORS middleware~~ — added in `main.py`
- [x] ~~DB-probing health check~~ — added in `main.py`
- [x] ~~Graceful shutdown timeout~~ — added in `main.py`
- [x] ~~WAL mode for SQLite~~ — added in `database.py`
- [ ] **Implement new model schema.** `Movie`, `Series`, `Season`, `Episode` with `ArtworkMixin` per `design/02-model-schema.md`.
- [ ] **Add `ModelName` to `pyproject.toml` classifiers** once the model naming is finalized.
- [ ] **Path translation hardening.** Implement `safe_translate_and_validate()` from `design/03-config-and-paths.md`.
- [ ] **Fix DB path** — resolve relative to project root, not CWD.
- [ ] **Fix cache/staging paths** — same treatment as DB path.

---

## Phase 2 — Integration Layer

- [ ] **Request logging middleware.** Log method, path, status code, and duration for every HTTP request.
- [ ] **Structured/JSON logging.** Switch from `basicConfig` text format to JSON for production-readiness. Keep text format for dev.
- [ ] **API rate limiting.** Sync and pipeline trigger routes should enforce a cooldown (5 min minimum between syncs). Return 429 if too frequent.
- [ ] **Init *arr clients.** Connect Radarr/Sonarr/TMDB clients at startup, store on `app.state`.
- [ ] **Sync service.** Implement full *arr → DB sync for movies, series, seasons, and episodes.
- [ ] **API route stubs.** Create all route files with placeholder endpoints.

---

## Phase 3 — AI Pipeline

- [ ] **PaddleOCR integration.** Wire `PosterTextFilter` into the pipeline as a stage.
- [ ] **CLIP ONNX export.** Adapt `export_dinov2.py` pattern for CLIP.
- [ ] **CLIP embedding extraction.** Wrap ONNX inference for embedding generation.
- [ ] **SHA-256 + pHash dedup.** Two-stage deduplication integrated into the pipeline.
- [ ] **Taste profile trainer/scorer.** Train from existing posters, score candidates.
- [ ] **Pipeline orchestrator.** Connect all stages: fetch → dedup → OCR → AI → select → deploy.
- [ ] **File write path validation.** Before writing poster files, validate destination is within `MEDIA_ROOTS`. Reject writes outside allowed paths.

---

## Phase 4 — API & Webhooks

- [ ] **Library CRUD routes.** List, detail, filter media items.
- [ ] **Pipeline trigger routes.** Manual poster processing per item or batch.
- [ ] **Webhook endpoints.** `POST /api/webhooks/radarr` and `POST /api/webhooks/sonarr`.
- [ ] **Taste profile routes.** View diagnostics, trigger retraining.
- [ ] **Settings routes.** GET/PUT configuration (read-only for API keys).

---

## Phase 5 — Poster Management

- [ ] **Poster cache.** Local cache keyed by TMDB ID.
- [ ] **Self-healing periodic scan.** Verify cached posters still exist on disk; restore if missing.
- [ ] **Media upgrade restoration.** Detect Radarr/Sonarr upgrades via webhook, restore poster from cache.

---

## Phase 6 — Web UI

- [ ] **Frontend framework.** React or Vue app scaffold.
- [ ] **Library grid view.** Show all media with current poster thumbnails.
- [ ] **Poster selection UI.** Grid of candidates, AI pick highlighted, click to override.
- [ ] **Manual feedback.** Thumbs up/down on AI selections → feeds back into taste model.

---

## Phase 7 — Docker & Release

- [ ] **Switch to Alembic migrations.** Remove `create_all()` from `init_db()`. Run `alembic upgrade head` at startup.
- [ ] **Dockerfile.** Multi-stage build, non-root user, health check.
- [ ] **Docker Compose.** Service definition, volume mounts, GPU passthrough (NVIDIA + Intel).
- [ ] **User documentation.** README, setup guide, configuration reference.

---

## Phase N — Deferred Features (no target phase yet)

- [ ] **Additional poster sources.** Fanart.tv, TheTVDB, TVmaze. TMDB is sole source for initial pipeline.
- [ ] **HDR/DV tracking.** Quality profile syncing, media info comparison, missing HDR/DV flagging.
- [ ] **Backdrops, logos, banners.** Additional artwork types per `design/02-model-schema.md` Section 8 — Path A (columns) or Path B (artwork table).
- [ ] **Health check hardening.** Add more probes (external API reachability, disk space, etc.).
- [ ] **Document async session autoflush behavior.** SQLAlchemy sessions have `autoflush=False` — developers must call `await db.flush()` explicitly before queries that depend on uncommitted state.
