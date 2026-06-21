# 18 — Pipeline Settings page, OCR-on-GPU, OCR workers, Text-gate presets & delete deployed posters

**Status:** implementation spec, ready to build. Authored 2026-06-21.
**Audience:** the agent implementing this. Everything you need is here; file paths are repo-relative to `/forge/Marquee`.

## Context

Nine related changes to the poster pipeline + a new Settings UI:

1. **OCR runs on CPU even though an RTX 3070 + `paddlepaddle-gpu` are installed** — a detection bug. Fix it so OCR runs on the GPU.
2. **OCR worker count**: default should be **4**, and an explicitly-set number must always be honored. There is a stale `.env` entry to remove.
3. **Settings page**: a real `/settings` page exposing every tunable pipeline knob, **grouped by pipeline stage**. The backend hot-update API already exists; the page does not.
4. **Text-gate controls**: presets **Title-only** (current) and **Textless** (no text at all) plus a **Custom** mode with per-category allow/deny for the *reliably detectable* categories — **title, director credit, studio, rating, tagline** (cast/actor **billing is deferred** to a later phase). This needs a new first-cut text classifier; today the gate only knows "title vs residual."
5. **"Delete all deployed posters"** maintenance action: delete every poster file placed next to a movie and reset that movie to "missing" so the pipeline can re-run from scratch. Distinct from the existing cache-clear (which *protects* deployed posters).
6. **Knob catalog**: a reference of what exists and what to expose (this doc + `design/08-tuning-knobs.md`).
7. **Metadata gates before download**: reshuffle the pipeline so that every metadata-only gate (resolution floor, plus any future metadata gates like vote-count or language) runs *before* `_download_poster` — candidates that fail a metadata gate are never downloaded at all. The pipeline's analysis behaviour and stage order are unchanged; this is a pure performance optimisation that avoids bandwidth/disk for posters the gate will reject anyway.
8. **Parallel cross-movie downloads**: today `fetch_and_download` downloads posters for one movie at a time. During batch runs of N movies, gather all TMDB metadata first (fast — one API call each), then download all surviving posters across all movies in one parallel pool so the downloads from multiple movies interleave and saturate the TMDB CDN connection.
9. **TMDB poster download size setting**: replace the hardcoded `_DOWNLOAD_SIZE = "w500"` with a configurable `PipelineSettings` field exposed in the Settings UI as a dropdown, default `w500`.

Two pydantic-settings singletons drive everything: `settings` (`marquee/config.py`) and **`pipeline_settings`** (`marquee/core/pipeline_config.py`). The pipeline knobs are `pipeline_settings`.

---

## Target 1 — Run OCR on the GPU (bug fix)

**Root cause** — `marquee/pipeline/ocr_filter.py:88-96`:

```python
def paddle_cuda_available() -> bool:
    if "paddle" not in sys.modules:   # <-- in a freshly spawned worker, paddle isn't imported yet
        return False                  #     so this returns False BEFORE checking CUDA
    try:
        import paddle
        return bool(paddle.device.is_compiled_with_cuda())
    except Exception:
        return False
```

OCR workers are spawned with `multiprocessing.get_context("spawn")`; each worker calls `_load_ocr()` (`ocr_filter.py:143-163`) → `_resolve_ocr_device(paddle_cuda_available=paddle_cuda_available())` (`:77-85`). In the fresh worker, `"paddle" not in sys.modules` is `True` → returns `False` → `_resolve_ocr_device` returns `"cpu"` for `OCR_DEVICE=auto`. The early guard exists to keep the **status/debug** path lightweight (don't import paddle into the API process just to report status), but it wrongly forces the worker path to CPU.

Confirmed environment: `paddlepaddle-gpu==3.3.1`, `paddle.device.is_compiled_with_cuda() == True`. So GPU works; only detection is broken. (CUDA stack must stay **cu12** — do not reinstall paddle.)

**Fix** — give `paddle_cuda_available` an opt-in import, and use it from the worker (which imports paddle via PaddleOCR anyway):

```python
def paddle_cuda_available(*, allow_import: bool = False) -> bool:
    if not allow_import and "paddle" not in sys.modules:
        return False
    try:
        import paddle
        return bool(paddle.device.is_compiled_with_cuda())
    except Exception:
        return False
```

In `_load_ocr()` (`:153`) change the call to `paddle_cuda_available(allow_import=True)`. Leave all status/debug callers on the default (lightweight) form so the API process never imports paddle.

**Verify:** a run logs `Loading PaddleOCR on device=gpu`; `nvidia-smi` shows GPU util > 0 during the OCR stage; the CPU is no longer pegged by the OCR worker processes.

---

## Target 2 — OCR workers (default 4, honor explicit value)

- **`marquee/core/pipeline_config.py`**: change `OCR_WORKERS` default `0` → **`4`** (keep the field; `0` still means auto).
- **`marquee/ml/hardware.py:277-290`** `effective_ocr_workers()` currently caps an explicit value to the CUDA-tier auto size unless `OCR_DEVICE == "gpu"`. After the Target-1 fix, `OCR_DEVICE` stays the *string* `"auto"`, so the cap would still clamp `4 → 3`. Replace the body with: honor any explicit value, else auto-size:
  ```python
  def effective_ocr_workers() -> int:
      configured = pipeline_settings.OCR_WORKERS
      if configured > 0:
          return configured
      return detect_hardware().ocr_workers
  ```
  (Drop the CUDA-tier cap; the user wants the configured number used. Optionally clamp to a sane upper bound like 16 to guard against typos.)
- **`.env`**: remove the line `OCR_WORKERS=0` (the user authorized this). Why: precedence is `pipeline_overrides.json` kwargs > `.env` > field default, so a lingering `.env` `OCR_WORKERS=0` would override the new default of 4 with "auto". `OCR_DEVICE=auto` may stay (it now correctly resolves to GPU) or be removed — both are fine. **Note:** `.env` may be owned by the app user (`cptbandit`); if the build agent can't write it, surface that and have the user delete the line.
- **Expose** `OCR_DEVICE` (enum auto/cpu/gpu) and `OCR_WORKERS` (int) in the Settings "Text gate / OCR" group. They're read from the singleton at call time (`effective_ocr_workers`, `_load_ocr`), so a hot-update applies to the **next OCR batch** — no restart.
- **VRAM caveat:** OCR on GPU with 4 Paddle contexts shares the 8 GB 3070 with CLIP (resident during OCR) — keep an eye on `nvidia-smi`. If it OOMs, the user can lower `OCR_WORKERS` or set `OCR_DEVICE=cpu` from the Settings page.

---

## Target 3 — Settings page (expose every knob, grouped by stage)

**Backend already exists** — `marquee/api/routes/config.py`:
- `GET /api/config/pipeline` → `{values, defaults, overrides, restart_required}` (`:63-74`).
- `PUT /api/config/pipeline` (`:77-121`) validates a proposed change by building a throwaway `PipelineSettings` (runs all cross-field validators), applies it to the live singleton (`setattr`), persists to `data/pipeline_overrides.json` (`save_overrides`), and calls `run_manager.reset_extractor()`. Already correct — **reuse as-is**.
- `RESTART_REQUIRED` (`:35-52`) blocks model/provider/path knobs from the hot path.
- The live-rescore pattern (`pipeline.py` `_clone_config` / `POST /runs/{id}/rescore`) is the model for "apply overrides without mutating the singleton" — relevant if you add a per-run preview later; not required here.

**Add knob metadata + grouping** so the UI can render sections and the right control per knob. New module `marquee/core/pipeline_config_meta.py` (or inline in `config.py`):
- `KNOB_GROUPS: list[{ "id", "label", "knobs": [field_name, ...] }]` ordered by pipeline stage (see Target 6 for the grouping).
- `KNOB_META: dict[field_name → { "kind": "weight"|"float"|"int"|"bool"|"enum"|"str", "min"?, "max"?, "step"?, "options"? }]`. Examples: `WEIGHT_*` → `{kind:weight,min:0,max:1,step:0.01}`; `GATE_MIN_AESTHETIC` → `{kind:float,min:0,max:10,step:0.1}`; `OCR_DEVICE` → `{kind:enum,options:["auto","cpu","gpu"]}`; bools → `{kind:bool}`.
- Extend `GET /api/config/pipeline` to also return `groups` (the ordered groups) and `meta` (per-knob hints merged with `PipelineSettings.model_fields[name].description` for help text). PUT is unchanged.
- Knobs in `RESTART_REQUIRED` and any `*_PATH`/`*_DIR`/model-identity fields render **read-only with a "restart" badge** (or are omitted) — they're already rejected by PUT.

**Frontend** — replace the stub `frontend/src/routes/settings/+page.svelte` (currently `Stub`, note "gap G7"):
- `frontend/src/routes/settings/+page.ts`: `load` → `getPipelineConfig(fetch)` (and optionally `GET /api/settings` for a read-only Connections/Paths panel — `marquee/api/routes/settings.py` already returns integration/path status).
- New `frontend/src/lib/api/config.ts`: `getPipelineConfig(fetch)` → typed `{values, defaults, overrides, restart_required, groups, meta}`; `putPipelineConfig(fetch, values)` → `{applied, overrides}`. Reuse `apiGet`/`apiSend`.
- UI: `TabBar` across the groups (Weights · Gates · Text gate · Style · Detail · Calibration · Head · Dedup · Advanced). Each knob renders by `meta.kind`: weight/float/int → slider or number input with min/max/step; bool → toggle; enum → select; str → text. Show the default and a "modified" dot when `overrides` contains it. Dirty-track edits; a **Save** button PUTs only changed keys → toast from the response; a **Reset to default** affordance sets the value back to `defaults[name]` (PUT). Reuse `SectionHeader`, `TabBar`, `ConfirmDialog`, `toast`, the existing button/input styles; match the gold-on-dark token system.
- Hot-update semantics: after Save, the next pipeline run uses the new values (the singleton is mutated + extractor reset server-side). Surface that ("applies on the next run").

---

## Target 4 — Text-gate presets + Custom (semantic-lite) — chosen scope: presets + easy categories

**Today** (`marquee/pipeline/ocr_filter.py` + `gate.py` + `types.py`): three OCR passes (full + top/bottom strips, `OCR_DETAIL_PASSES`), dedup by text+IoU, then a **title box** is found by fuzzy-matching detected boxes against per-movie `title_tokens`/`director_tokens`. Everything not matching the title is **residual**; "significant" residual (big box, or ≥4-char non-digit word not near the title) is counted, and the gate rejects when significant residual count > `OCR_MAX_RESIDUAL_BOXES` (default 0) or area > `OCR_MAX_RESIDUAL_AREA_FRACTION` (default 0.04), or `no_title`/`no_text`/`format_blocklist`. There is **no semantic classification** of residual text. Per-movie `title_tokens`/`director_tokens` are already plumbed through `run_ocr_batch` (design 17).

**Build:**

1. **Expose all `OCR_*` knobs** in the Settings "Text gate" group (raw control): `OCR_DEVICE`, `OCR_WORKERS`, `OCR_DETAIL_PASSES`, `OCR_MAX_RESIDUAL_BOXES`, `OCR_MAX_RESIDUAL_AREA_FRACTION`, `OCR_CONFIDENCE_THRESHOLD`, `OCR_STRIP_CONFIDENCE_THRESHOLD`, `OCR_BOTTOM_CONFIDENCE_THRESHOLD`, `OCR_FUZZY_CUTOFF`, `OCR_TITLE_PROXIMITY_PIXELS`, `OCR_RESIDUAL_SIGNIFICANT_AREA_FRACTION`, `OCR_RESIDUAL_SIGNIFICANT_WIDTH_FRACTION`, `OCR_REQUIRE_TITLE`, `OCR_ACCEPT_NO_TEXT`, `OCR_ENHANCE_RETRY`.

2. **New knobs** (`pipeline_config.py`):
   - `OCR_TEXT_MODE: str = "title_only"` — enum `{"title_only","textless","custom"}`.
   - Custom per-category allow toggles: `OCR_ALLOW_TITLE: bool = True`, `OCR_ALLOW_DIRECTOR: bool = False`, `OCR_ALLOW_STUDIO: bool = False`, `OCR_ALLOW_RATING: bool = False`, `OCR_ALLOW_TAGLINE: bool = False`. (Cast/billing intentionally absent — deferred.)
   - Add these to `KNOB_META` with a dedicated "text gate" UI treatment (preset radio + category checkboxes).

3. **First-cut classifier** — new `classify_text_box(box, *, image_w, image_h, title_box, title_tokens, director_tokens) -> str` in `ocr_filter.py`, returning one of `title|director|rating|studio|tagline|billing|other`:
   - `title`: the matched title box (existing logic).
   - `director`: text matches `/directed\s+by/i` OR words match `director_tokens`.
   - `rating`: regex MPAA-ish — `/\b(rated\s+)?(g|pg|pg-13|nc-17|r|nr|unrated)\b/i`, certification marks.
   - `studio`: word in a static `STUDIO_KEYWORDS` set (paramount, warner bros, disney, universal, sony, columbia, lionsgate, mgm, netflix, a24, focus features, dreamworks, pixar, …); usually top/bottom zone.
   - `tagline`: not any of the above, short-ish phrase, roughly centered (x near image center), upper/mid zone — heuristic by `bbox` position + length.
   - `billing`/`other`: dense small bottom-zone text or anything unmatched → treated as **denied residual** for now (cast-billing toggle deferred).
   - Signals available per box: `bbox` (position), `area`, `text`, `confidence`, `geometry_valid` — see `types.py`. Compute y-center fraction and area fraction from `image_h`/`image_w`.
   - Optionally record the category on the box / in `OCRCandidateResult.extended` so the results page can show *why* text was kept/rejected.

4. **Gate behavior** (in the residual-significance + accept/reject decision):
   - `title_only` (default): unchanged — require title; any significant residual → reject.
   - `textless`: accept only if there is **no** title box **and** no significant residual (reject title-bearing and any text). New branch.
   - `custom`: classify each *significant* residual box; it counts as a violation only if its category's allow-toggle is **off**. Title is gated by `OCR_ALLOW_TITLE`. Apply the existing `OCR_MAX_RESIDUAL_BOXES`/`OCR_MAX_RESIDUAL_AREA_FRACTION` thresholds over the **denied** boxes only.

5. **Settings UI "Text gate" section:** preset radio (Title-only / Textless / Custom) bound to `OCR_TEXT_MODE`; when Custom, show category toggles (Title, Director, Studio, Rating, Tagline) + a collapsible "Advanced OCR knobs" with the raw thresholds. Show a disabled "Cast / billing — coming soon" row.

**Deferred (Phase 2, document only):** cast/actor **billing** detection (dense bottom-zone block cross-referenced against TMDB cast names), and/or a learned text-region classifier for higher accuracy. Note the heuristics above are imperfect; the presets cover the primary intent.

---

## Target 5 — "Delete all deployed posters" button

Deploy mechanics (`marquee/core/poster_service.py`): a poster is written to `<movie.folder_path>/<MOVIE_POSTER_FORMAT>` (default `poster.jpg`), a cache copy goes to `data/cache/posters/movies/{tmdb_id}.jpg` (+ `.meta.json`), and the `Movie` artwork columns are set (`poster_path`, `poster_source`, `poster_source_url`, `poster_ai_selected`, `poster_user_approved`, `poster_deployed_filename`, `poster_deployed_at`, `poster_sha256`, `poster_phash`) plus an `ArtworkEvent`. There is **no delete** function (only `restore`, which on failure sets `poster_path=None`).

**New job** `poster_deploy_reset` — `marquee/core/jobs/builtin_handlers.py` (mirror the `pipeline_cache_clear` handler shape):
- Load `Movie` rows where `poster_path IS NOT NULL`.
- Per movie: validate the folder via `safe_translate_and_validate(movie.folder_path, source="radarr")` (`marquee/core/path_utils.py`); confirm `Path(movie.poster_path).parent == folder` (confinement — never trust the stored path blindly); `unlink(missing_ok=True)`.
- Reset all `poster_*` columns to the "missing" state (the 9 fields above → `None`/`False`). This makes the movie match `ix_movies_missing_poster` and reappear in the Run-tab missing list.
- Write `ArtworkEvent(action="deploy_reset", source="maintenance", detail=…)`.
- **Keep** the cache copies (`data/cache/posters/...`) — they are restore fallbacks and not "next to the movie." (Optional `purge_cache: bool=False` flag if you want a deeper wipe; default keep.)
- Resources: `{"maintenance_exclusive": 1}` (serialize with cache-clear/backup) and consider `{"media_write": 1}` since it deletes files under media roots. `max_attempts=1`; idempotent (a re-run skips already-reset movies). Commit once at the end; return `{reset, failed, errors}`.

**Endpoint** `POST /api/pipeline/posters/reset` (in `pipeline.py`) → `job_manager.create(job_type="poster_deploy_reset", …)` → `job_summary`.

**Frontend:** a **Danger zone** section on the Settings page: "Delete all deployed posters" → `ConfirmDialog` (red `tone="bad"`, strong copy: *"Deletes every `poster.jpg` next to your movies and marks all movies as missing so the pipeline can re-run. Does NOT touch your taste profile, the Key Art Engine, or your labels."*); optionally require typing to confirm. New FE wrapper `resetDeployedPosters(fetch)`.

**Contrast:** `marquee/core/pipeline_cache.py` (the existing cache-clear) explicitly **protects** `poster_cache_path` and deployed posters. This new job is the opposite — keep them clearly separate and labeled so the two are never confused.

---

## Target 6 — Knob catalog (what to expose, grouped)

Authoritative list lives in `marquee/core/pipeline_config.py`; narrative reference in `design/08-tuning-knobs.md`. Group the Settings UI as:

- **Weights** (`WEIGHT_*`, 13 — renormalized at runtime): knn_sim, aesthetic, title_colorfulness, face_area, text_residual, dino_knn, taste_typicality, official_family, quality_artifacts, sharpness, provenance, resolution, lang_match.
- **Gates**: `GATE_MIN_WIDTH`, `GATE_MIN_AESTHETIC`, `GATE_MIN_KNN_SIM`, `GATE_AESTHETIC_RESCUE_KNN`, `GATE_MIN_AESTHETIC_RESCUED`, `GATE_FAN_JUNK_*`.
- **Text gate / OCR**: all `OCR_*` (Target 4) + the new `OCR_TEXT_MODE`/`OCR_ALLOW_*`.
- **Style / taste**: `K_NEIGHBORS`, `KNN_WEIGHTING`, `KNN_SOFTMAX_TEMP`, `TASTE_NEG_WEIGHT`, `PREFERRED_LANG`.
- **Detail**: `DINO_ENABLED`, `EXTRA_QUALITY_ENABLED`, `PERSON_CONFIDENCE_THRESHOLD`, `FACE_CONFIDENCE_THRESHOLD`, `FACE_NMS_THRESHOLD`, `QUALITY_BLOCKINESS_SAT`, `QUALITY_NOISE_SAT`.
- **Normalization** (`NORM_*`, 9) and **Residual blend** (`RESIDUAL_*`, `PROV_*`) — Advanced.
- **Calibration**: `CALIBRATION_ENABLED`, `CALIBRATION_BANDWIDTH_SCALE`, `CALIBRATION_MIN_SAMPLES`.
- **Learned head / feedback**: `SCORER`, `HEAD_MIN_LABELS`, `HEAD_MIN_MOVIES`, `HEAD_AUTO_RETRAIN`, `FEEDBACK_*`.
- **Dedup**: `DEDUP_PHASH_THRESHOLD`, `DEDUP_MIN_POSTER_WIDTH`.
- **Batch**: `PIPELINE_BATCH_MAX_MOVIES`.

**Read-only / restart-required (badge, not editable via hot path):** `AI_MODEL`, `EXECUTION_PROVIDER`, `CLIP_BATCH_SIZE`, and every `*_PATH`/`*_DIR` (model artifacts, feedback/training dirs). These are in `RESTART_REQUIRED` or are paths — set them via `.env` only.

---

## Target 7 — Metadata gates before download (pipeline reshuffle)

**Current state** — `runner.py` `fetch_and_download()` (line 360) downloads **every** candidate poster at w500 as soon as the TMDB metadata is known. The resolution gate (`PosterGate.evaluate_metadata`) runs later in `run_sync_stages` (line 480) — after all images are already on disk. The `PosterCandidate` dataclass already carries `width`, `height`, `aspect_ratio`, `language`, `vote_average`, and `vote_count` — all available from the TMDB API response **without downloading a single byte**.

**Goal** — reshuffle so that metadata-only gates are evaluated **before** `_download_poster` is called. The pipeline's analysis behaviour and final results are identical; the only difference is that bandwidth-rejected posters are never downloaded or written to disk.

**Today's metadata-only gates:**

| Gate | Input | Source |
|---|---|---|
| Resolution floor (`GATE_MIN_WIDTH`) | `candidate.width` | TMDB metadata |
| *(future)* Vote-count floor | `candidate.vote_count` | TMDB metadata |
| *(future)* Language allowlist | `candidate.language` | TMDB metadata |

Only the resolution floor is active today; the others are placeholders for future gate types that will also benefit from this reshuffle automatically.

**Implementation** — modify `fetch_and_download()` in `marquee/pipeline/runner.py`:

1. After `candidates = await tmdb.get_movie_images(movie.tmdb_id)` (line 371), instantiate `PosterGate()` and run `evaluate_metadata(original_width=candidate.width)` on each candidate.
2. Split `candidates` into `downloadable` (passed the gate) and `metadata_gated` (failed).
3. For each gated candidate: create a `CandidateScore` record with `rejection_reason="resolution_floor"`, `stage_reached="fetch"`, and `gate_decision="gated"`. These records are counted in `counts["resolution_gated"]` just as they would be in `run_sync_stages` today.
4. Only `downloadable` candidates proceed to `asyncio.gather` of `_download_poster()`.
5. `all_files` is built from the **survivors of both metadata gates + successful downloads**; a candidate that passes the metadata gate but fails to download still gets an error record as before.

The downstream `run_sync_stages` resolution gate loop (line 480-500) becomes a **no-op** — all survivors there already passed the metadata gate. Keep the loop as a safety assertion (log a warning if any survivor fails, then reject it as before), but it should never fire.

**Batch runner** — `batch_runner.py` `_movie_prelude()` (line 254) currently does resolution gate in the sync phase. After this reshuffle, that code path is also a no-op for the same reason. Do **not** remove it — keep it as the safety net, but add a comment that metadata gates are now pre-download.

**Counts compatibility** — the `FetchOutcome.counts` dict already has `"posters_found"`, `"downloaded"`, `"skipped"`, `"errors"`. Add `"metadata_gated": len(metadata_gated)` so the run summary reflects how many were rejected before download. `run_sync_stages` still tracks `resolution_gated` in `outcome.counts`, which will now be zero under normal operation.

**No new config needed** — the existing `GATE_MIN_WIDTH` knob already controls this behaviour. The reshuffle is a pure optimisation; it changes nothing about how the gate works or what values it uses.

**Verification:** run a pipeline on a movie with known low-res posters. The run log should show `FETCH | metadata_gated=N` where N > 0, and those filenames should never appear in the `0-originals/` directory. Total downloads should decrease by exactly N. Final run results (which posters pass/fail) must be identical to the pre-reshuffle behaviour.

---

## Target 8 — Parallel cross-movie downloads in batch runs

**Current state** — `batch_runner.py` `run_batch()` (line 114) has a `for ctx in contexts:` loop that calls `await fetch_and_download(...)` for each movie **sequentially** (line 160-175). Within a single movie, `fetch_and_download` already uses `asyncio.gather` with `_DOWNLOAD_SEMAPHORE(5)` for parallel per-movie downloads. But across movies, it's serial: movie 2 waits for all of movie 1's downloads to finish.

For a batch of 12 movies with ~40 candidates each, this means ~480 sequential downloads (modulo the per-movie parallelism of 5). The TMDB CDN can handle far more concurrent connections.

**Goal** — split the batch fetch phase into two sub-phases so all movies' downloads overlap:

```
Phase A (fast — metadata only): fetch TMDB metadata for all movies concurrently
Phase B (parallel — downloads): gather all surviving posters across all movies,
                                download them in one big parallel pool
```

**Implementation:**

1. **Phase A — metadata gather.** In `run_batch()`, replace the sequential `for ctx in contexts: await fetch_and_download(...)` with a concurrent metadata phase:
   - Extract the TMDB metadata call from `fetch_and_download` into a new function `fetch_candidates(tmdb, movie) -> FetchCandidates` that returns `(candidates, primary_name)` — no downloads.
   - Run `fetch_candidates` for all movies via `asyncio.gather()`.
   - Apply the **metadata gates** (Target 7) to each movie's candidates immediately, so only survivors need downloading.

2. **Phase B — cross-movie parallel download pool.** Create a single `httpx.AsyncClient(timeout=60.0)` shared across all movies. Flatten all surviving `(candidate, destination_path)` pairs across all movies into one list. Download them all via a single `asyncio.gather()` with a **shared semaphore** sized to the batch — e.g., `asyncio.Semaphore(max(5, min(total_survivors, 15)))`.

3. **Per-movie FetchOutcome** is assembled from the sub-results.

4. **New constant** `_BATCH_DOWNLOAD_SEMAPHORE = asyncio.Semaphore(15)` (or compute dynamically from `len(contexts)`). The single-movie path still uses `_DOWNLOAD_SEMAPHORE(5)` — no change there.

**Backward compatibility** — the single-movie `fetch_and_download` path is unchanged. Only the batch runner's fetch loop changes. The `FetchOutcome` dataclass and its usage downstream are unmodified.

**Expected speedup:** for a 12-movie batch where each movie has ~40 candidates, the current approach takes `12 × (metadata ~0.5s + downloads ~8s) ≈ 102s`. After this change: `metadata ~2s (parallel) + downloads ~10s (all parallel) ≈ 12s`. Roughly **8× faster** for the fetch phase of large batches. Actual speedup depends on TMDB CDN latency and the semaphore cap.

**Verification:** run a batch of 5+ movies. Log timestamps should show all movies' downloads overlapping (interleaved log lines from different movies within the same second). Total fetch-phase wall time should be close to the slowest single movie's time, not the sum.

---

## Target 9 — TMDB poster download size setting

**Current state** — `runner.py:64` hardcodes `_DOWNLOAD_SIZE = "w500"`. The only way to change it is to edit the source. This is a single global constant used in `_download_poster()` (line 334: `poster.url(size=_DOWNLOAD_SIZE)`) and nowhere else in the download path. The final `place_ranked()` in `output.py` always uses `"original"` for the top-5 full-resolution re-download — that is separate and should stay as-is.

**Goal** — make the initial-fetch poster size configurable via `PipelineSettings` and expose it in the Settings UI.

**Implementation:**

1. **New field in `PipelineSettings`** (`marquee/core/pipeline_config.py`):
   ```python
   # TMDB poster size for initial pipeline fetch. "original" downloads
   # uncapped resolution (1-10 MB per poster); smaller sizes save bandwidth
   # at the cost of slightly degraded OCR and face-detection accuracy.
   # "w500" (500 px wide, ~50-200 KB) is the default sweet spot.
   TMDB_POSTER_SIZE: str = "w500"
   ```
   Valid values: `"w92"`, `"w154"`, `"w185"`, `"w342"`, `"w500"`, `"w780"`, `"original"`.

2. **Validation** — add to `validate_pipeline_settings()`:
   ```python
   _TMDB_SIZES = {"w92", "w154", "w185", "w342", "w500", "w780", "original"}
   if self.TMDB_POSTER_SIZE not in _TMDB_SIZES:
       raise ValueError(f"TMDB_POSTER_SIZE must be one of {sorted(_TMDB_SIZES)}")
   ```

3. **Replace the constant** in `runner.py`:
   - Remove `_DOWNLOAD_SIZE = "w500"`.
   - In `_download_poster()`, use `pipeline_settings.TMDB_POSTER_SIZE` instead.
   - In `fetch_and_download()`, same change for the `poster.url(size=...)` call.

4. **Settings UI exposure** — add to the `KNOB_META` in `pipeline_config_meta.py`:
   ```python
   "TMDB_POSTER_SIZE": {
       "kind": "enum",
       "options": ["w92", "w154", "w185", "w342", "w500", "w780", "original"],
   }
   ```
   Place it in a new group **"Downloads"** (or in the **Advanced** group alongside `PIPELINE_BATCH_MAX_MOVIES`). The dropdown should label each option with its pixel width and estimated file size for clarity: `"w500 (500px, ~100KB)"`.

5. **Hot-update safe** — the size is read at call time (`_download_poster` looks up `pipeline_settings.TMDB_POSTER_SIZE`), so a Settings page change takes effect on the next run without a restart.

**Trade-offs to document in the UI help text:**
- `w92`–`w185`: very fast, tiny files, but OCR and face detection degrade significantly
- `w342`: reasonable balance, ~40% smaller files than w500
- `w500` **(default)**: good OCR accuracy at ~50-200 KB per poster
- `w780`–`original`: best feature quality, but 1-10 MB per poster — only useful for small candidate sets or when disk/bandwidth are abundant

**Verification:** set `TMDB_POSTER_SIZE=w342` in the Settings page, Save, run a pipeline. Check the `0-originals/` directory — all downloaded posters should be 342px wide. Verify with `file` or `identify` (ImageMagick). Set back to `w500`, re-run, confirm 500px width. The `ranked/` top-5 files should still be full resolution (original) — the output re-download is unaffected.

---

## Critical files

- **Edit:** `marquee/pipeline/ocr_filter.py` (paddle CUDA detection; `_load_ocr`; new `classify_text_box` + `STUDIO_KEYWORDS`; gate-mode branches), `marquee/pipeline/gate.py`/`types.py` (text-mode decision; optional per-box category), `marquee/ml/hardware.py` (`effective_ocr_workers`), `marquee/core/pipeline_config.py` (`OCR_WORKERS=4`; `OCR_TEXT_MODE`/`OCR_ALLOW_*`; `TMDB_POSTER_SIZE`), `marquee/pipeline/runner.py` (metadata gates before download in `fetch_and_download`; `_download_poster` uses `pipeline_settings.TMDB_POSTER_SIZE`), `marquee/pipeline/batch_runner.py` (parallel cross-movie fetch in `run_batch`), `marquee/api/routes/config.py` (add `groups`+`meta` to GET), `marquee/api/routes/pipeline.py` (poster-reset endpoint), `marquee/core/jobs/builtin_handlers.py` (`poster_deploy_reset`), `.env` (remove `OCR_WORKERS=0`).
- **New:** `marquee/core/pipeline_config_meta.py` (groups + control meta, including `TMDB_POSTER_SIZE` knob and new "Downloads" group), `frontend/src/lib/api/config.ts`, `frontend/src/routes/settings/{+page.ts,+page.svelte}` (replace stub), small reusable knob-control + a Danger-zone confirm.
- **Reuse:** the hot-update `config.py` GET/PUT, `save_overrides`/`load_overrides`, `safe_translate_and_validate` (`path_utils.py`), `job_manager.create` + `maintenance_exclusive`, the `pipeline_cache_clear` handler as a shape, `ConfirmDialog`/`TabBar`/`SectionHeader`/`toast` and the `apiGet`/`apiSend` client.

## Verification

1. **OCR GPU:** run a pipeline → log `Loading PaddleOCR on device=gpu`; `nvidia-smi` GPU util > 0 during OCR; CPU not pegged by OCR workers.
2. **Workers:** out-of-box → 4 workers spawn (logs / `active_worker_status`); set 2 via Settings → next run uses 2; confirm VRAM headroom with `nvidia-smi`.
3. **Settings page:** GET renders all groups; edit `GATE_MIN_AESTHETIC`, Save → `data/pipeline_overrides.json` updated, next run uses it; restart-required knobs read-only.
4. **Text gate:** Title-only unchanged; Textless selects textless posters; Custom + Director-allowed accepts title+director posters and rejects others — validate against a known small set.
5. **Delete posters:** deploy a poster, run reset → file removed, `poster_status=missing`, `ArtworkEvent` logged, cache copy retained, movie reappears in the Run-tab missing list.
6. **Lint & build:** `ruff check marquee` clean; `cd frontend && npm run check && npm run lint && npm run build` green.
7. **Metadata gates before download:** run a pipeline on a movie with known low-res posters (< GATE_MIN_WIDTH). Log should show `FETCH | metadata_gated=N` where N > 0. Those filenames must never appear in `0-originals/`. Final run results must be identical to pre-reshuffle behaviour.
8. **Parallel batch downloads:** run a batch of 5+ movies. Log timestamps show interleaved download log lines from different movies within the same second. Total fetch-phase wall time ~ slowest single movie's time, not the sum. Per-movie download counts are correct.
9. **TMDB poster size setting:** set `TMDB_POSTER_SIZE=w342` via Settings → Save → run pipeline → check `0-originals/` posters are 342px wide (`file` or `identify`). Set back to `w500`, confirm reversion. Top-5 `ranked/` posters must still be full original resolution.
