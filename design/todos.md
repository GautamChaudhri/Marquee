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
- [x] ~~**Pipeline test endpoint.**~~ Full end-to-end pipeline (fetch → SHA-256 → OCR → pHash → Features → Gate → Rank → Output) working in `POST /api/test/pipeline/movie/{id}`. Production route still pending.
- [ ] **File write path validation.** Before writing poster files, validate destination is within `MEDIA_ROOTS`.
- [ ] **Expose poster counts in sync report.** Add `posters_found` / `posters_missing` to sync API response.
- [ ] **Fix stale poster_path on file deletion.** `_check_existing_poster` should set `poster_path = NULL` when file no longer exists.

---

## Open Tuning Questions (Phase 3)

- [x] ~~Stylized title recovery: contrast-enhance retry implemented (OCR_ENHANCE_RETRY), 2× contrast boost on no-text detection. text_det_box_thresh=0.3 active. Pending visual validation on gothic/stylized posters.~~
- [ ] pHash threshold at 6 (default in pipeline_config) — needs more movie testing to confirm
- [x] ~~Floating-head handling: moved from CLIP negative prompts (deleted) to face_area scalar in the feature vector. Active as a ranking penalty.~~
- [x] ~~Pipeline order decided: OCR runs BEFORE pHash (adopted). When near-duplicate variants differ only in text content, OCR gate arbitrates.~~
- [x] ~~Text-free posters: handled by OCR_ACCEPT_NO_TEXT flag (default true). If OCR finds no text even after contrast retry, the poster is accepted rather than rejected — lands at end of ranking as a fallback.~~
- [ ] Aesthetic rescue gate: knn_sim threshold relaxes aesthetic floor for stylized posters. Needs tuning (currently knn≥0.55, aesthetic≥2.0).
- [x] ~~NVIDIA GPU support: CUDAExecutionProvider added to provider chain. PaddleOCR auto-detects CUDA via paddle_dynamic engine. CLIP/face ONNX models run on GPU.~~
- [x] ~~Cheapest-signal-first reorder: resolution gate from metadata, batched CLIP + style gates BEFORE OCR. OCR only sees on-style candidates.~~
- [x] ~~Text-heavy gate made threshold-configurable (OCR_MAX_RESIDUAL_BOXES / OCR_MAX_RESIDUAL_AREA_FRACTION). Default 0 = strict title-only text (project target). Raise to 1-2 later to tolerate taglines as a rank penalty.~~
- [x] ~~No-title posters: title_colorfulness normalizes to neutral 0.5 when no title box found (title_found flag).~~
- [x] ~~Taste sharpening: softmax-weighted k-NN (KNN_WEIGHTING/KNN_SOFTMAX_TEMP) + negative exemplars (negative_data/ + TASTE_NEG_WEIGHT junk-proximity penalty).~~
- [x] ~~Multi-platform hardware module (ml/hardware.py): CUDA/OpenVINO-GPU/OpenVINO-CPU/CoreML/CPU tiers, CUDA lib preloading from pip nvidia packages, auto CLIP batch + OCR worker sizing. Docker per-tier profiles in docker/. See design/06.~~
- [x] ~~Torch removed from runtime: aesthetic head loads from .npz sidecar; torch needed only for model export.~~
- [ ] Validate the strict title-only OCR gate (OCR_MAX_RESIDUAL_BOXES=0) across more movies; when the target relaxes, revisit 1-2 boxes + 4% area.
- [ ] Populate `marquee/experiments/negative_data/` with disliked posters and rebuild profile to activate the junk penalty.
- [ ] Optional INT8 CLIP for N150-class hosts (`clip_export --quantize`, AI_MODEL=clip-vit-b-32-int8 + profile rebuild) — needs accuracy spot-check against fp32 ranking.
- [x] ~~Extended features (recs 1-6, design/07): zero-shot CLIP axes, classic-CV palette/composition pack, title/face geometry, exemplar-calibrated KDE typicality, DINOv2 second k-NN (auto on GPU tiers), quality artifacts + YOLO person detector (EXTRA_QUALITY_ENABLED flag), Phase-1 learned head plumbing (SCORER=auto + head_trainer + labels.jsonl).~~
- [ ] Tune WEIGHT_TASTE_TYPICALITY / WEIGHT_DINO_KNN from RANK DETAIL + TYPICALITY logs after a few weeks of runs.
- [ ] Feedback UI writes `experiments/feedback/labels.jsonl`; at ~50-100 labels run `python -m marquee.ml.head_trainer` to activate the learned head (rec 7 / L14 / VLM still deferred).

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
