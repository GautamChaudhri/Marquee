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

- [x] ~~**PaddleOCR integration.**~~ — `PosterTextFilter` class built: 3-pass OCR, FORMAT_BLOCKLIST, fuzzy matching, multiprocessed, GPU auto-detect, contrast-enhance retry, text-free accept. In `marquee/pipeline/ocr_filter.py`.
- [x] ~~**CLIP embedding extraction.**~~ — ViT-B/32 exported to ONNX (~150 MB). `CLIPImageEncoder` with CUDA > OpenVINO > CoreML > CPU providers. In `marquee/ml/embedding.py`.
- [x] ~~**SHA-256 + pHash dedup.**~~ — `PosterDeduper` two-stage dedup with resolution tiebreaker. In `marquee/pipeline/deduper.py`.
- [x] ~~**Taste profile trainer/scorer.**~~ — `taste_trainer.py` trains from 430 posters using CLIP B/32 ONNX. k-NN over exemplars (k=10), LAB color dropped, centroid diagnostics-only. `NumpyTasteStore` with model-name validation. In `marquee/ml/`.
- [x] ~~**Pipeline test endpoint.**~~ Full end-to-end pipeline working in `POST /api/test/pipeline/movie/{id}`. Pipeline logic extracted to `marquee/pipeline/runner.py`.
- [x] ~~**Production pipeline route.**~~ `POST /api/pipeline/movie/{id}/run` in `marquee/api/routes/pipeline.py` with live SSE progress, RunManager singleton, process-lifetime FeatureExtractor (fixes VRAM growth).
- [x] ~~**File write path validation.**~~ `PosterService.deploy()` and `PosterService.restore()` validate via `safe_translate_and_validate()` against `MEDIA_ROOTS` before any write.
- [ ] **Expose poster counts in sync report.** Add `posters_found` / `posters_missing` to sync API response.
- [ ] **Fix stale poster_path on file deletion.** `_check_existing_poster` should set `poster_path = NULL` when file no longer exists. Partially covered by self-heal scan (`heal.py`).

---

## Open Tuning Questions (Phase 3)

- [x] ~~Stylized title recovery: contrast-enhance retry implemented (OCR_ENHANCE_RETRY), 2× contrast boost on no-text detection. text_det_box_thresh=0.3 active.~~
- [ ] pHash threshold at 6 (default in pipeline_config) — needs more movie testing to confirm. Representative selection is now preference-aware (title found > fewest residual boxes > knn_sim > resolution), so raising the threshold to merge title-position variants is safe if ranked output feels redundant.
- [x] ~~Floating-head handling: moved from CLIP negative prompts to face_area scalar.~~
- [x] ~~Pipeline order decided: OCR runs BEFORE pHash.~~
- [x] ~~Text-free posters: OCR_ACCEPT_NO_TEXT batch-level fallback, OCR_REQUIRE_TITLE default true.~~
- [ ] Aesthetic rescue gate: knn_sim threshold relaxes aesthetic floor for stylized posters. Needs tuning (currently knn≥0.55, aesthetic≥2.0).
- [x] ~~NVIDIA GPU support: CUDAExecutionProvider added. PaddleOCR auto-detects CUDA.~~
- [x] ~~Cheapest-signal-first reorder: resolution gate from metadata, batched CLIP + style gates BEFORE OCR.~~
- [x] ~~Text-heavy gate: OCR_MAX_RESIDUAL_BOXES / OCR_MAX_RESIDUAL_AREA_FRACTION.~~
- [x] ~~No-title posters: title_colorfulness neutral 0.5 when no title box found.~~
- [x] ~~Taste sharpening: softmax-weighted k-NN + negative exemplars.~~
- [x] ~~Multi-platform hardware module: CUDA/OpenVINO/CoreML/CPU tiers.~~
- [x] ~~Torch removed from runtime: aesthetic head from .npz sidecar.~~
- [ ] Validate strict title-only OCR gate (OCR_MAX_RESIDUAL_BOXES=0) across more movies.
- [ ] Populate `marquee/experiments/negative_data/` with disliked posters and rebuild profile.
- [ ] Optional INT8 CLIP for N150-class hosts — needs accuracy spot-check.
- [x] ~~Extended features (design/07): zero-shot CLIP axes, CV palette/composition pack, title/face geometry, KDE typicality, DINOv2 k-NN, quality artifacts, YOLO person, Phase-1 learned head plumbing.~~
- [ ] Tune WEIGHT_TASTE_TYPICALITY / WEIGHT_DINO_KNN from RANK DETAIL logs after a few weeks.
- [x] ~~Head training moved from manual CLI to auto via feedback API (`HEAD_AUTO_RETRAIN=true`). Labels v2 embeds feature vectors. 78 labels exist; need ~150 across 5+ movies to activate.~~
- [x] ~~official_family: CLIP cosine to TMDB primary poster as scorer feature (0.12) + primary wins pHash group.~~
- [ ] Extend official_family to multiple authority anchors: Wikipedia film-infobox poster, Fanart.tv likes.
- [x] ~~GPU VRAM growth fixed: RunManager owns a process-lifetime FeatureExtractor instead of per-run sessions. OOM from ~8 runs no longer occurs.~~
- [ ] Re-running a movie wipes `ranked/` — sort into `experiments/feedback/labels.jsonl` instead.

---

## Phase 4 — API & Webhooks ✅ COMPLETE

- [x] ~~**Library CRUD routes.**~~ — `GET /api/library/movies`, `GET /api/library/movies/{id}` in `marquee/api/routes/library.py`.
- [x] ~~**Pipeline trigger routes.**~~ — `POST /api/pipeline/movie/{id}/run`, `GET /api/pipeline/runs/{run_id}`, `GET /api/pipeline/runs/{run_id}/events` (SSE), `POST /api/pipeline/runs/{run_id}/rescore` in `marquee/api/routes/pipeline.py`. Run history per movie at `GET /api/movies/{id}/runs`.
- [x] ~~**Webhook endpoints.**~~ — `POST /api/webhooks/radarr` and `POST /api/webhooks/sonarr` with event dispatch (Test, Download/upgrade, Rename, MovieFileDelete), auth via WEBHOOK_TOKEN, dry-run mode. Per-movie asyncio.Lock + retry backoff.
- [x] ~~**Taste profile routes.**~~ — `GET /api/taste/status`, `POST /api/taste/retrain` in `marquee/api/routes/taste.py`. Taste-map endpoints at `GET /api/taste/map`, `POST /api/taste/map/candidates`, `GET /api/taste/map/rebuild`, exemplar image/neighbors endpoints.
- [x] ~~**Settings routes.**~~ — `GET /api/config/pipeline`, `PUT /api/config/pipeline` with restart-required validation. Persisted via `data/pipeline_overrides.json`.
- [x] ~~**System routes.**~~ — `GET /api/system/status` (cache stats, heal state, webhook state), `POST /api/system/heal`.
- [x] ~~**Artwork events feed.**~~ — `GET /api/movies/{movie_id}/artwork-events` (deploy/restore history), `POST /api/movies/{movie_id}/poster/restore`.
- [x] ~~**Feedback routes.**~~ — `POST /api/feedback` (approve/override/reject_all), `POST /api/feedback/undo`. Full scenarios A–D implemented.

---

## Phase 5 — Poster Management ✅ COMPLETE

- [x] ~~**Poster cache.**~~ — Local cache at `data/cache/posters/movies/{tmdb_id}.jpg` + `{tmdb_id}.meta.json`. Written by `PosterService.deploy()` and `PosterService.restore()` at deployment time. Exact deployed bytes (full resolution, not transcoded).
- [x] ~~**Self-healing periodic scan.**~~ — `marquee/core/heal.py`: asyncio task in `lifespan()` (every `HEAL_INTERVAL_MINUTES`, default 30). Walks movies with non-null `poster_path`, stats file, restores from cache/URL on miss. Also exposed as `POST /api/system/heal`.
- [x] ~~**Media upgrade restoration.**~~ — Radarr webhook → background task → `PosterService.restore()`. Cache-hit: verify SHA-256, copy to new folder. Cache-miss: re-download from `poster_source_url`. Retry backoff for folder-finalization race. Per-movie lock prevents double-restore.

---

## Phase 6 — Web UI

- [ ] **Frontend framework.** React or Vue app scaffold.
- [ ] **Library grid view.** Show all media with current poster thumbnails.
- [ ] **Poster selection UI.** Grid of candidates, AI pick highlighted, click to override.
- [ ] **Manual feedback.** Thumbs up/down on AI selections → feeds back into taste model.

---

## Phase 7 — Docker & Release

- [x] ~~**Alembic migrations.**~~ — Infrastructure exists in `alembic/` (env.py, versions/, script.py.mako). Migrations are properly tracked with revision history (letterbox + subtitle prefilter migrations are committed). `create_all()` is still used at startup as a safety net, but the canonical schema evolution path is Alembic.
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
- [x] ~~**Subtitle management (design §16-22).**~~ — Fully built: `subtitle_inventories`/`subtitle_tracks` for snapshotting, `managed_subtitle_assets`/`managed_subtitle_bindings` for restore-on-upgrade caching, `subtitle_policies`/`subtitle_policy_bindings` for language-cleanup policies, durable `media_jobs`/`media_batches`/`media_job_events` for the mutation queue, `media_files`/`episode_media_files` for physical file tracking, and `media_backups` for pre-mutation copies. See `marquee/models/` and `design/more-features/03-subtitle-management.md`.
- [x] ~~**Letterbox cropping (design 04-letterbox).**~~ — Fully built: `letterbox_state`/`letterbox_events` models, `marquee/media/letterbox_detect.py` (cropdetect + ImageMagick trim backends), `marquee/media/letterbox_manager.py` (tag application/removal), `marquee/core/letterbox_service.py` (orchestration), `marquee/api/routes/letterbox.py` (REST API), ~25 `LETTERBOX_*` config knobs, letterbox heal scan, resolution pre-filter using `movie.video_width`/`video_height`/`container`. See `design/more-features/04-letterbox-cropping.md`.
