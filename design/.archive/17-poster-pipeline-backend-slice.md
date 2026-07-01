# Marquee — Poster-Pipeline Backend Slice (Frontend Readiness)

**Status:** Implemented on 2026-06-21. Backend groundwork that makes the
poster-pipeline frontend vertical slice "just connect in." Everything is routed
through the durable job platform (`design/15`).

This document is the authoritative reference for the batch runner, the new job
types and endpoints, and the behavior changes shipped in this slice.

---

## 1. Goals

Expose, with no further backend work needed by the frontend:

1. Live progress — which stage, and (in a batch) which movie.
2. Per-stage rejection results + the ranked survivors.
3. One-click poster selection that feeds the learned head (UI: **"Key Art
   Engine"**; backend: **learned head**) and deploys to the library.
4. Cross-run metrics.
5. Fast **batch** runs (the OCR pool + DINOv2 load once for the whole batch).
6. An initial-training button (taste profile).
7. A clear-pipeline-cache button.

Decisions locked with the owner: stage-batched runner; **manual** head training
(picks only accumulate to storage); **pairwise** pick labels (chosen +1,
auto-pick −1); initial-training default = the curated training folder.

---

## 2. New job types (all via `job_manager`)

Handlers in `marquee/core/jobs/builtin_handlers.py`, registered through
`core/jobs/handlers.py`.

| Job type | Resources | Retry | Does |
|---|---|---|---|
| `poster_pipeline_batch` | `gpu:1, network_external:1` | `max_attempts=1` | Stage-batched run over many movies (§4). |
| `learned_head_train` | none (numpy) | `max_attempts=1` | `train_from_labels()` — the manual head train. |
| `pipeline_cache_clear` | `maintenance_exclusive:1` | `max_attempts=1` | Clear download caches (§8). |
| `taste_rebuild` (changed) | `gpu:1` | `max_attempts=1` | Now takes `payload.source` = `training_dir` (default) \| `library`; resets the worker's feature extractor after. |

The single-movie `poster_pipeline` handler is unchanged except it now wires a
progress bridge (§5).

---

## 3. New / changed endpoints

| Method + path | Purpose |
|---|---|
| `POST /api/pipeline/batch` | Enqueue a batch. Body `{scope, movie_ids?}`; scope = `missing` (poster_path IS NULL, default) \| `all` \| `selected`. Guarded by `PIPELINE_BATCH_MAX_MOVIES`. Returns job summary + `movie_count`. |
| `GET /api/pipeline/cache` | On-disk size of each pipeline cache (for a "Clear (X MB)" button). |
| `POST /api/pipeline/cache/clear` | Enqueue `pipeline_cache_clear`. Body `{include_embeddings=true, include_archives=false}`. |
| `GET /api/pipeline/metrics` | Cross-run aggregates from `PipelineRun` rows (§7). |
| `POST /api/taste/head/retrain` | Enqueue `learned_head_train` (the "Key Art Engine → Train" button). |
| `POST /api/taste/retrain` (changed) | Now accepts `{source}` (`training_dir` default \| `library`). |
| `GET /api/pipeline/runs/{id}` (changed) | Results payload gained `rejected_by_stage` (§6). |

Batch runs do **not** auto-deploy — completed per-movie runs land in the
existing review queue (`GET /api/pipeline/review-queue`); the user approves each,
which deploys.

---

## 4. Stage-batched batch runner

`marquee/pipeline/batch_runner.py` — `async run_batch(job_id, movies, tmdb,
extractor, progress, should_cancel)`.

**Why.** The single-movie engine spins the PaddleOCR worker pool up and down per
movie, so a batch pays the model load N times. The batch runner streams every
movie through each stage together, so the two costliest shared resources load
**once per batch**:

- **OCR** — one `PosterTextFilter.run_ocr_batch` over every movie's style
  survivors (§4.1).
- **DINOv2 detail features** — one `FeatureExtractor.complete_batch` over the
  union of survivors.

**Stage order** (matches `runner.py`; only the cross-movie grouping is new):

```
FETCH all (async, per movie)
 → SHA-256 + resolution gate          (per movie)
 → STYLE FEATURES + STYLE GATE        (per movie; shared FeatureExtractor — CLIP already resident)
 → OCR ONCE                           (one pool, per-task title tokens)   ← the win
 → pHash                              (per movie)
 → DETAIL/DINO ONCE                   (one complete_batch over the union) ← bonus win
 → fan-junk gate + RANK               (per movie)
 → per movie: archive + PipelineRun row
```

**Reuse.** SHA/pHash dedup, gates, scorer, `build_run_payload`,
`place_gated`/`place_outputs`, and the `data/runs/work/<title>/` layout are the
exact same leaf helpers as the single-movie path, so a candidate's recorded
fields and the archive are byte-for-byte compatible with `/api/pipeline/runs/{id}`.

**Identity.** Each movie gets its own `run_id` (uuid4) + `PipelineRun` row tagged
`batch_id = <this job>`. The job itself is not a `PipelineRun`.

**Shared model stack.** The handler loads the extractor once via
`run_manager._ensure_extractor()` (off the event loop) and passes it in; GPU
resources are released after the batch when `PIPELINE_CACHE_EXTRACTOR` is false.

**Cancellation.** The handler runs a 2 s poll watcher that sets a
`threading.Event` when `job.cancel_requested` flips; `run_batch` checks it
between movies/stages, finalizes completed movies, and `_finalize` coerces any
non-terminal movie to `cancelled`. GPU exclusion vs single runs / taste rebuild
is enforced by the `gpu:1` reservation (capacity 1), not by `run_manager`'s
in-process lock (which is per-process and not shared API↔worker).

### 4.1 OCR per-task tokens

`marquee/pipeline/ocr_filter.py`. PaddleOCR box *detection* is title-agnostic;
only the cheap accept/reject (`_process_image`) needs the movie's title tokens.
The worker pool already proves the model can stay loaded while tokens change
(the taste trainer re-inits tokens per exemplar). Changes:

- `_process_image(path, title_tokens=None, director_tokens=None)` — tokens are
  now **parameters** (fall back to worker globals when omitted, preserving the
  single-movie / test path).
- Worker tasks carry `(index, path, title_tokens, director_tokens)`.
- New `PosterTextFilter.run_ocr_batch(items)` — `items = [(path, title_tokens,
  director_tokens), …]` — runs one pool and returns per-item results **without**
  the no-text fallback. The fallback (`apply_no_text_fallback`, promoted to a
  module function) is applied **per movie** by the batch runner — a movie with
  zero titled survivors must rescue only *its own* textless posters.
- `PosterTextFilter.filter_batch(paths)` is now a thin wrapper over
  `run_ocr_batch` + per-movie fallback — single-movie behavior is unchanged.

---

## 5. Live progress on the durable job stream

`marquee/pipeline/progress_bridge.py` — `JobProgressBridge(job_id)`, an async
context manager used by both the single and batch handlers. It marshals
worker-thread `ProgressEvent`s onto the loop and:

- persists throttled `JobEvent` rows (stage boundaries + movie boundaries always;
  in-stage ticks at most every ~0.4 s), and
- updates the `job.progress` JSON snapshot + `job.current_stage`.

`ProgressEvent` (in `runner.py`) gained optional `movie_id / title /
movie_index / movie_total / movies_done` (None for single runs). `run_manager._execute`
gained a `progress_sink` parameter so the single-movie path emits onto the same
contract.

> Frontend note (see `design/11` + the SSE-progress memory): live bars should
> poll the job snapshot (`GET /api/jobs/{id}`), because an SSE stream opened
> immediately after the POST can miss the first events.

---

## 6. Per-stage rejection tabs

`marquee/api/results.py`. `build_results_payload` gained `rejected_by_stage`: an
ordered list of `{stage, label, count, posters[]}` keyed on the rejection-reason
base, in pipeline order:

```
sha256 → resolution → style (aesthetic/off-style) → ocr → phash → fan_junk → errored
```

No data-model change — the reasons were already recorded on every candidate;
only the grouping is new. The legacy 4-bucket `rejected` map is retained.

---

## 7. Pick → storage + manual head train; metrics

**Pick** (results view) reuses `POST /api/feedback` `override`/`approve`:
pairwise labels (chosen +1, auto-pick −1), the pick appended to the taste
profile **and** copied into `data/training/positive` (`profile_updater.add_exemplar`),
and deploy to the movie folder with the configured `MOVIE_POSTER_FORMAT`
filename. **Behavior change:** `HEAD_AUTO_RETRAIN` now defaults **False**, so a
pick only *accumulates* into storage (labels + exemplars); the learned head is
(re)trained only by the manual `POST /api/taste/head/retrain` button. The
storage the head consumes is `data/feedback/labels.jsonl` (self-contained v2
rows) + the exemplar dirs.

**Metrics.** `PipelineRun` gained `timings_json`, `duration_seconds`, `batch_id`,
populated on both single and batch finalize. `GET /api/pipeline/metrics`
aggregates the last N rows (no archive reads): `by_status`, `by_scorer`,
`duration_seconds {avg, p50, p90, max}`, `avg_counts` / `total_counts` (posters
found, survivors per stage, gated, ranked), `avg_stage_seconds` /
`total_stage_seconds`, and `distinct_batches`.

---

## 8. Initial training + cache clear

**Initial training (item 6).** `taste_rebuild` `source=library` gathers every
movie's currently-deployed poster (preferring the local
`data/cache/posters/movies/{tmdb_id}.jpg`, falling back to `movie.poster_path`)
into a temp dir named `"{title} ({year}).jpg"` and calls
`rebuild_profile(training_dir=…)`. `source=training_dir` (default) uses the
curated `data/training/positive` folder — correct as the default because picks
already accumulate there.

**Cache clear (item 7).** `marquee/core/pipeline_cache.py` clears only an
allow-list — `data/runs/work`, `data/staging`, and (optional)
`data/cache/embeddings`, plus (opt-in) `data/runs/archive`. It **refuses**, with
a path-safety guard, to touch `data/feedback` (labels), `data/training` (+
negative), `data/ml` (taste profile, learned head, zero-shot axes), or
`data/cache/posters` (the deployed-poster cache used for restore/self-heal).
`cache_sizes()` backs the size report.

---

## 9. Schema migration

Alembic `a1b2c3d4e5f6` (down_revision `85985807cac7`) adds to `pipeline_runs`:
`timings_json TEXT`, `duration_seconds FLOAT`, `batch_id VARCHAR(32)` +
`ix_pipeline_runs_batch_id`. Additive and reversible. Apply with
`alembic upgrade head` on the live DB.

---

## 10. Config changes (`core/pipeline_config.py`)

- `HEAD_AUTO_RETRAIN: bool = False` (was `True`) — picks accumulate; manual head train.
- `PIPELINE_BATCH_MAX_MOVIES: int = 500` — admission cap for one batch.

---

## 11. Files

New: `pipeline/batch_runner.py`, `pipeline/progress_bridge.py`,
`core/pipeline_cache.py`, `alembic/versions/a1b2c3d4e5f6_*.py`,
`tests/test_poster_pipeline_backend.py`.

Changed: `pipeline/ocr_filter.py`, `pipeline/runner.py`,
`pipeline/run_manager.py`, `core/jobs/builtin_handlers.py`,
`api/results.py`, `api/routes/pipeline.py`, `api/routes/taste.py`,
`models/pipeline_run.py`, `core/pipeline_config.py`.

---

## 12. Testing

`tests/test_poster_pipeline_backend.py` (ML-free): per-call OCR tokens,
per-movie OCR-fallback isolation, per-stage grouping, cache-clear path safety,
library-poster gathering, handler registration. `ruff check` clean on the
changed set. Real-GPU verification (on the RTX box): run `POST /api/pipeline/batch`
over a few movies and confirm the logs show PaddleOCR loading **once** for the
whole batch, per-movie archives written, and the review queue populated.

---

## 13. Follow-ups

- Apply migration `a1b2c3d4e5f6` on the live DB.
- `GET /api/taste/status` still returns a stale `rebuild` field from the
  pre-job-platform subprocess code (`_start_rebuild_process` et al. in
  `taste.py`), which is now dead — remove it and source rebuild status from the
  latest `taste_rebuild` job instead.
- Very large batches can exceed `JOB_MAX_RUNTIME_SECONDS` (1 h default); raise it
  or keep batches modest. Per-movie stage boundary events are verbose at high
  movie counts — a future optimization could coalesce them.
