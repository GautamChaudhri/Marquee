# Marquee — TODOs & Deferred Items

Organized by the phase when attention is needed. Reference `design/03-migration-plan.md` for phase definitions.

---

## Phase 1 — Core Infrastructure ✅ COMPLETE

- [x] ~~CORS middleware~~ — added in `main.py`
- [x] ~~DB-probing health check~~ — added in `main.py`
- [x] ~~Graceful shutdown timeout~~ — added in `main.py`
- [x] ~~WAL mode for SQLite~~ — added in `database.py`
- [x] ~~Implement new model schema.~~ — Movie, Series, Season, Episode + ArtworkMixin in `marquee/models/`
- [x] ~~Path translation hardening.~~ — `safe_translate_and_validate()` in `marquee/core/path_utils.py`
- [x] ~~Fix DB path~~ — `db_url_resolved` property resolves relative to project root
- [x] ~~Fix cache/staging paths~~ — `poster_cache_path` / `poster_staging_path` properties

---

## Phase 2 — Integration Layer ✅ COMPLETE

- [x] ~~Request logging middleware.~~ — added in `main.py`
- [x] ~~Structured/JSON logging.~~ — setup in `marquee/logging.py`; `LOG_FORMAT=text|json` config; `JsonFormatter` ready; log messages unified across all modules
- [x] ~~API rate limiting.~~ — `RateLimiter` class in `marquee/core/rate_limit.py`; applied to `POST /api/sync/all` with `SYNC_COOLDOWN_SECONDS` config
- [x] ~~Init *arr clients.~~ — Radarr, Sonarr, and TMDB clients wired in `main.py` lifespan
- [x] ~~Sync service.~~ — implemented in `marquee/core/sync_service.py`
- [x] ~~API route stubs.~~ — library, pipeline, and webhook stubs created; sync route is working

---

## Phase 3 — AI Pipeline ← CURRENT

- [ ] **PaddleOCR integration.** Wire `PosterTextFilter` into the pipeline as a stage.
- [ ] **CLIP embedding extraction.** Wrap inference for embedding generation (replaces DINOv2 from old project).
- [ ] **SHA-256 + pHash dedup.** Two-stage deduplication integrated into the pipeline.
- [ ] **Taste profile trainer/scorer.** Train from existing posters, score candidates.
- [ ] **Pipeline orchestrator.** Connect all stages: fetch → dedup → OCR → AI → select → deploy.
- [ ] **File write path validation.** Before writing poster files, validate destination is within `MEDIA_ROOTS`. Reject writes outside allowed paths.
- [ ] **Expose poster counts in sync report.** Add `posters_found` / `posters_missing` to sync API response.
- [ ] **Fix stale poster_path on file deletion.** `_check_existing_poster` should set `poster_path = NULL` when the file no longer exists on disk (heal behavior). Currently it silently preserves the old value.

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
- [ ] **TMDB ID resolution for series.** Currently series use `tvdb_id` as primary key. When adding more poster sources, resolve `tmdb_id` via TMDB `/find` endpoint (using `tvdb_id` or `imdb_id`). Also populate `Season.tmdb_id` for season poster lookups.
- [ ] **HDR/DV tracking.** Quality profile syncing, media info comparison, missing HDR/DV flagging.
- [ ] **Backdrops, logos, banners.** Additional artwork types per `design/02-model-schema.md` Section 8 — Path A (columns) or Path B (artwork table).
- [ ] **Health check hardening.** Add more probes (external API reachability, disk space, etc.).
- [ ] **Document async session autoflush behavior.** SQLAlchemy sessions have `autoflush=False` — developers must call `await db.flush()` explicitly before queries that depend on uncommitted state.
