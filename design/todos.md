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
- [x] ~~Per-source path mapping.~~ — `RADARR_PATH_PREFIX`/`RADARR_MEDIA_PATH`, `SONARR_PATH_PREFIX`/`SONARR_MEDIA_PATH`, auto-derived `effective_media_roots`
- [x] ~~Fix DB path~~ — `db_url_resolved` property resolves relative to project root
- [x] ~~Fix cache/staging paths~~ — `poster_cache_path` / `poster_staging_path` properties

---

## Phase 2 — Integration Layer ✅ COMPLETE

- [x] ~~Request logging middleware.~~ — added in `main.py`
- [x] ~~Structured/JSON logging.~~ — setup in `marquee/logging.py`
- [x] ~~API rate limiting.~~ — `RateLimiter` class in `marquee/core/rate_limit.py`
- [x] ~~Init *arr clients.~~ — Radarr, Sonarr, TMDB clients wired in `main.py` lifespan
- [x] ~~Sync service.~~ — implemented in `marquee/core/sync_service.py`
- [x] ~~API route stubs.~~ — library, pipeline, webhook stubs created; sync route working
- [x] ~~Test pipeline endpoint.~~ — `POST /api/test/pipeline/movie/{id}` built for visual validation

---

## Phase 3 — AI Pipeline ← CURRENT

- [x] ~~**PaddleOCR integration.**~~ — `PosterTextFilter` class built: 3-pass OCR, FORMAT_BLOCKLIST, fuzzy matching, multiprocessed. In `marquee/pipeline/ocr_filter.py`.
- [x] ~~**CLIP embedding extraction.**~~ — ViT-B/16 exported to ONNX (329 MB, CoreML active). `CLIPImageEncoder` with platform-agnostic providers. In `marquee/ml/embedding.py`.
- [x] ~~**SHA-256 + pHash dedup.**~~ — `PosterDeduper` two-stage dedup with resolution tiebreaker. In `marquee/pipeline/deduper.py`.
- [x] ~~**Taste profile trainer/scorer.**~~ — `taste_trainer.py` trains from 430 posters (mean=0.76, std=0.06). `TasteScorer` with CLIP+LAB 80/20 scoring + negative filter. `TasteStore` abstraction with `NumpyTasteStore` + ChromaDB stub. In `marquee/ml/`.
- [ ] **Pipeline orchestrator.** Connect all stages: fetch → dedup → OCR → AI → select → deploy.
- [ ] **File write path validation.** Before writing poster files, validate destination is within `MEDIA_ROOTS`.
- [ ] **Expose poster counts in sync report.** Add `posters_found` / `posters_missing` to sync API response.
- [ ] **Fix stale poster_path on file deletion.** `_check_existing_poster` should set `poster_path = NULL` when file no longer exists.

---

## Open Tuning Questions (Phase 3)

- [ ] PaddleOCR can't read stylized/gothic titles at w500 (Nosferatu). Need to evaluate `text_det_box_thresh=0.3` effectiveness.
- [ ] pHash threshold at 8 — needs more movie testing to confirm
- [ ] Negative filter (floating heads) currently disabled — user undecided
- [ ] OCR-first vs pHash-first pipeline order — both tested, results in `experiments/ocr-first/` and `experiments/data/`
- [ ] Text-free poster repositioning: posters with <3 chars detected text pushed to end of CLIP ranking

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
- [ ] **TMDB ID resolution for series.** Currently series use `tvdb_id` as primary key. When adding more poster sources, resolve `tmdb_id` via TMDB `/find` endpoint.
- [ ] **Director info for OCR.** TMDB credits endpoint needed — currently director defaults to `None`.
- [ ] **HDR/DV tracking.** Quality profile syncing, media info comparison, missing HDR/DV flagging.
- [ ] **Backdrops, logos, banners.** Additional artwork types per `design/02-model-schema.md`.
- [ ] **Health check hardening.** Add more probes (external API reachability, disk space, etc.).
- [ ] **ChromaDB integration.** Replace `NumpyTasteStore` with `ChromaTasteStore` for incremental approval feature.
