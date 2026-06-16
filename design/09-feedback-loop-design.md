# Marquee — Feedback Loop Design

**Status:** Implemented (API endpoints, label store, incremental profile updates, auto-retrain)  
**Date:** 2026-06-12  
**Purpose:** Define how the system learns from user interactions, what happens in every override scenario, and how the UI consumes pipeline decisions.

---

## Implementation Summary

The feedback loop is fully implemented (2026-06-15). Key components:

- **Feedback API** (`POST /api/feedback` in `marquee/api/routes/feedback.py`): handles approve/override/reject_all/undo actions, writes v2 labels, deploys via PosterService, appends to taste profile, auto-retrains learned head
- **Label store** (`marquee/ml/feedback_store.py`): append-only JSONL with embedded feature vectors (v2 format) — no dependency on run directories surviving re-runs; backward-compatible with v1 (title+filename join)
- **Profile updater** (`marquee/ml/profile_updater.py`): incremental add/remove of single exemplars to the taste profile `.npz` without a full rebuild (CLIP embedding + DINOv2 + calibration column append)
- **Retroactive features** (`marquee/pipeline/retro_features.py`): when a user selects a poster rejected before its detail features were computed, computes missing features at feedback time (rare one-off cost)
- **Learned head auto-retrain** (`marquee/ml/head_trainer.py`): `train_from_labels()` called after each feedback event when `HEAD_AUTO_RETRAIN=true` and thresholds met
- **Gate override tracking**: every override records which gate rejected the poster; at `FEEDBACK_GATE_ALERT_THRESHOLD` (default 5), the taste status API surfaces a tuning suggestion
- **Deployment**: `PosterService.deploy()` (`marquee/core/poster_service.py`) renders filename, validates path, writes atomically, populates cache, logs ArtworkEvent

The golden rule (§2) is enforced: only explicitly approved posters join the taste profile.
Labels v2 embed the full feature vector at feedback time so training never depends on a run's working directory.

This design doc remains the authoritative reference for the scenarios and data model;
the code at `marquee/api/routes/feedback.py` and related modules is the source of truth for implementation details.


---

## Table of Contents

1. [The Two Learning Layers](#1-the-two-learning-layers)
2. [The Golden Rule](#2-the-golden-rule)
3. [Scenario A: Approve the Auto-Pick](#3-scenario-a-approve-the-auto-pick)
4. [Scenario B: Override with Another Ranked Poster](#4-scenario-b-override-with-another-ranked-poster)
5. [Scenario C: Override with a Rejected Poster](#5-scenario-c-override-with-a-rejected-poster)
6. [Scenario D: Reject the Auto-Pick Without Picking](#6-scenario-d-reject-the-auto-pick-without-picking)
7. [Gate Override Tracking](#7-gate-override-tracking)
8. [Feature Gaps in Rejected Posters](#8-feature-gaps-in-rejected-posters)
9. [UI Workflow (Conceptual)](#9-ui-workflow-conceptual)
10. [Learned Head Activation Threshold](#10-learned-head-activation-threshold)
11. [API Surface (Concise Overview)](#11-api-surface-concise-overview)
12. [Open Questions for Refinement](#12-open-questions-for-refinement)

---

## 1. The Two Learning Layers

The system learns from you at two levels that serve different purposes:

### Layer 1: The Taste Profile (Exemplar Store)

**What it is:** A collection of CLIP embeddings from every poster you've explicitly approved. Currently 430 posters in `taste_profile.clip-vit-b-32.npz`.

**What it learns:** "What kind of posters do I like visually?" — style, composition, color palette. This is the k-NN signal (`knn_sim`).

**How it grows:** Only when you **approve** a poster. Auto-picks that you never review never join. Poster names are stored so you can trace every exemplar back to its source.

**Why this rule:** If un-reviewed auto-picks fed back, the model would reinforce its own guesses and collapse into a self-confirming loop. The feedback loop must be closed by a human.

### Layer 2: The Learned Ranking Head

**What it is:** A logistic regression (Phase 1) or pairwise ranker (Phase 2) trained on the feature vectors of posters you've labeled. Stored in `learned_head.clip-vit-b-32.npz`.

**What it learns:** "Given the measurable features of two posters, which one would I pick?" — the weighting between features.

**How it trains:** From `labels.jsonl`, which records every decision you make. Each label is a feature vector plus a binary label (1 = picked, 0 = not picked). With enough labels from diverse movies, the head learns the feature importance weights from your behavior rather than from hand-tuning.

**Why it's a separate layer:** The taste profile handles visual similarity (the embedding). The learned head handles feature preference (aesthetic vs colorfulness vs face avoidance). They complement each other — one does not replace the other.

---

## 2. The Golden Rule

> **Only manually-approved posters join the taste profile.  
> Auto-picks that are never reviewed must never feed back.  
> Override events generate an explicit negative for the auto-pick.**

This rule exists because a model that trains on its own unverified outputs converges to whatever it already believes — drift, not learning. Every exemplar in the taste profile should trace back to a moment where you said "yes, this one."

The learned head is slightly different: it can use negatives (auto-picks you overrode) because they provide contrast. But the taste profile — the visual anchor — must stay human-curated.

---

## 3. Scenario A: Approve the Auto-Pick

**What happens:** The model's #1 choice is displayed. You look at it and click "Approve."

**What this means:** "You got it right. This is my poster."

### Labels written to `labels.jsonl`:

| Poster | Label | Reason |
|--------|-------|--------|
| Auto-pick (#1) | 1 | "User approved the model's choice" |

One row. This is the simplest feedback event — a single positive.

### Taste profile:

The approved poster's CLIP embedding is added to the exemplar store. Over time, this grows the store and shifts the style centroid toward your actual picks.

### Learned head:

A positive label for the auto-pick. In isolation, this doesn't provide a preference pair — it just says "this feature vector is good." With enough of these, the head learns the feature distribution of "things I actually picked."

### `selected_file`:

The `selected_file` sent with the feedback is same as the auto-pick — the API can detect this and optimize (write one label instead of two, no pairwise signal needed).

---

## 4. Scenario B: Override with Another Ranked Poster

**What happens:** The model ranked 18 posters. #1 is the auto-pick. You scroll down and pick #4 instead.

**What this means:** "The model thought #1 was best. I disagree. THIS poster is better for this movie."

### Labels written to `labels.jsonl`:

| Poster | Label | Reason |
|--------|-------|--------|
| Auto-pick (#1) | 0 | "User overrode this for a different poster" |
| User's pick (#4) | 1 | "User selected this poster instead" |

Two rows. This is a **pairwise preference** — the richest signal the system can receive.

### Taste profile:

The user's pick joins the exemplar store. The auto-pick does NOT.

### Learned head:

A pairwise preference: "for this movie, feature vector B > feature vector A." This is exactly what the Phase-2 pairwise ranking head (LightGBM `lambdarank`) is designed to consume. Even with the Phase-1 logistic head, two separate labels (0 and 1) provide contrast.

### Why this is the strongest signal:

The system now knows not just "I like this poster" but "given these two options, I prefer this one." After 50-100 such override events across diverse movies, the head begins to learn which features discriminate your actual picks from the model's guesses.

---

## 5. Scenario C: Override with a Rejected Poster

**What happens:** You look at the rejected posters tab, find one the OCR filter threw out, and decide it's actually the best poster.

**What this means:** "The pipeline was wrong to reject this. This is my poster, AND this gate threshold should be reviewed."

### Labels written to `labels.jsonl`:

| Poster | Label | Reason |
|--------|-------|--------|
| Auto-pick (#1) | 0 | "User overrode this for a rejected poster" |
| User's pick (rejected) | 1 | "User selected this rejected poster instead" |

### Taste profile:

The user's pick joins the exemplar store — even if it was rejected. Your taste shouldn't be limited by the pipeline's current thresholds.

### Gate override tracking:

This is the critical difference from Scenario B. The system records **which gate rejected this poster** and increments a counter:

```
Gate override counter for "ocr_text_heavy": 1
```

After N overrides on the same gate (proposed threshold: 5), the system surfaces a recommendation: "You've overridden the OCR text-heavy gate 5 times. Current threshold allows 0 residual text boxes. Consider raising to 1 to tolerate official taglines."

These counters are advisory only — the system never changes thresholds automatically. They exist so the UI can alert you when a gate is consistently blocking posters you want.

### Feature gap consideration:

Rejected posters may have partial feature vectors:

- **Rejected at resolution gate:** No features. Only the raw file exists. → Add to taste profile only. Do NOT feed to learned head (no features to train on).
- **Rejected at style gate:** Has `knn_sim`, `aesthetic`, and metadata scalars. → Partially trainable, but the detail features (title_colorfulness, face_area, etc.) are missing. Options: skip learned head, or retroactively compute them.
- **Rejected at OCR gate:** Has style features (batched CLIP ran before OCR). Missing detail features (face, title_colorfulness, sharpness). → Retroactively compute detail features for training.
- **Rejected at pHash/detail gate:** Has most or all features. → Fully trainable.

**Proposed approach:** When a rejected poster is selected, retroactively compute any missing features and write the full feature vector to `labels.jsonl`. This is a one-time cost at feedback time, not at pipeline time. The alternative — always computing features for rejected posters — would add significant overhead to every pipeline run, 99% of which will never produce a feedback event.

---

## 6. Scenario D: Reject the Auto-Pick Without Picking

**What happens:** You look at the results, don't like any of them, and click "Reject" without choosing an alternative.

**What this means:** "None of these are acceptable. I need different posters or different thresholds."

### Labels written to `labels.jsonl`:

| Poster | Label | Reason |
|--------|-------|--------|
| Auto-pick (#1) | 0 | "User rejected the top pick with no alternative" |

### Taste profile:

Nothing joins. There's no poster to add.

### What this should trigger:

This is a signal that the candidate pool was insufficient. Options the system could suggest:
- "Loosen the OCR gate to allow taglines?" (if many were rejected there)
- "Try a different poster source?" (future: Fanart.tv, TheTVDB)
- "This movie may only have subpar posters."

Currently this would just record the negative and move on. In a future UI, a "reject all" could trigger a prompt with actionable suggestions based on the rejection breakdown.

---

## 7. Gate Override Tracking

Every time a user overrides a gate (Scenario C), the system records:

```
{gate_name}_{rejection_reason}: count
```

Examples:
- `style_aesthetic_floor`: 1
- `ocr_text_heavy`: 3
- `ocr_no_text`: 2
- `resolution_floor`: 0

These accumulate across runs and are displayed in the taste status panel. When a counter reaches a threshold (default: 5), the UI flags it:

> ⚠️ **Gate alert:** You've overridden the OCR "text-heavy" gate 5 times.  
> This gate rejects posters with any non-title text. Consider raising `OCR_MAX_RESIDUAL_BOXES` from 0 to 1-2 to allow posters with taglines, which would have accepted 5 posters you manually selected.

The counters reset when the user adjusts the threshold (since the override events were for the old threshold).

**Where stored:** Alongside `labels.jsonl` in `experiments/feedback/gate_overrides.json`, or as an additional field in each label entry.

---

## 8. Feature Gaps in Rejected Posters

This section addresses the technical question: when a user selects a rejected poster, do we have enough features to train the learned head?

| Rejection stage | Features available | Features missing | Trainable? | Resolution |
|---|---|---|---|---|
| Resolution gate | None | All | ❌ | Taste profile only |
| Style gate (aesthetic/off-style) | `knn_sim`, `aesthetic`, `resolution`, `provenance`, `lang_match` | `title_colorfulness`, `face_area`, `sharpness`, `text_residual`, extended features | ⚠️ Partial | Retroactively compute missing features |
| OCR gate | All style features | All detail features | ⚠️ Partial | Retroactively compute detail features |
| pHash dedup | All style + OCR features | Detail features | ⚠️ Partial | Retroactively compute detail features |
| Detail gate (fan-junk) | All features | None | ✅ | Fully trainable |

**Implementation approach:** The feedback endpoint checks what features are present. If any are missing, it runs a targeted extraction pass (CLIP is already cached, so retroactive feature computation is fast — sub-second per poster). The key insight: we accept the one-time cost at feedback time rather than the per-run cost of computing features for every rejected poster.

**Edge case — killed by SHA-256 or pHash:** A dedup-removed poster is byte-identical or visually near-identical to a survivor. The survivor is already in the candidate pool and has features. If the user picks a dedup-removed variant, log it but point them to the survivor — they are virtually the same image.

---

## 9. UI Workflow (Conceptual)

The frontend experience, step by step:

### 9.1 Pipeline Execution

1. User selects a movie → "Find Posters" button
2. A live progress indicator shows the current stage:
   ```
   Stage 4/9: OCR filtering… ████████░░ 15 of 37 posters
   ```
3. Each stage name is human-readable (not internal stage identifiers)
4. When complete, the results view appears

### 9.2 Results View — Three Sections

#### Section A: The Auto-Pick
- Large card showing the #1 poster
- Score + top 3 contributing factors explained in plain language:
  - "Strong style match to your taste profile"
  - "Official key art from TMDB"  
  - "Colored stylized title text"
- **[Approve]** button — prominent, the happy path

#### Section B: All Ranked Survivors
- Collapsed by default, showing "18 survivors found"
- Expands to a grid of all ranked posters
- Each card shows rank, score, and thumbnail
- Clicking any poster shows its full feature breakdown
- Any poster can be clicked → **[Use this instead]**

#### Section C: Rejected Posters
- Collapsed by default, showing "22 posters rejected"
- Filter tabs split by rejection stage: All | Style (4) | OCR (15) | pHash (3)
- Clicking a rejected poster shows:
  - The poster image
  - Why it was rejected in plain language
  - The specific decision details (from the JSON logs, but formatted for humans)
  - **[Use this poster anyway]** — the override button

### 9.3 Feedback Submission

When the user clicks Approve, Use This Instead, or Use This Poster Anyway:

1. The action is sent to the feedback endpoint
2. The UI shows a brief confirmation: "✓ Feedback saved. Taste profile updated."
3. The taste status panel updates (label count increments)
4. No page reload — the results stay visible with the selected poster highlighted

### 9.4 Taste Status Panel

A sidebar or settings tab showing:
- Total labels: 80 (from 4 movies)
- Exemplars in taste profile: 432
- Learned head: Inactive (need 5+ movies)
- Gate override alerts: 1 active (OCR text-heavy: 3 overrides)

---

## 10. Learned Head Activation Threshold

The learned head currently requires:

| Requirement | Current | Target |
|---|---|---|
| Labeled movies | 4 | ≥ 5 |
| Total labels | 78 | ≥ 150 |
| Label diversity | All action/sci-fi/horror blockbusters | Mix of genres |

**Rationale for the thresholds:**

- **5+ movies:** A logistic regression trained on 3 movies learns patterns specific to those movies (e.g., "Avengers has floating-head posters so face_area is a strong negative"). With 5+ diverse movies, the patterns generalize.
- **150+ labels:** Each override event produces 2 labels (negative + positive). Approve events produce 1. With ~30 interactions per movie across 5 movies, this is reachable.
- **Genre diversity:** If all labeled movies are Marvel films, the head learns "avoid floating heads" not "prefer artistic composition." Genre diversity is tracked in the status panel.

The head is activated with `SCORER=auto` (default) — it picks up the learned artifact automatically when the thresholds are met. No manual config change needed.

---

## 11. API Surface (Concise Overview)

The endpoints needed to connect the frontend to this design. Details deferred to implementation.

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/pipeline/movie/{id}/run` | POST | Trigger a pipeline run, return `run_id` |
| `/api/pipeline/runs/{run_id}/stream` | GET | SSE stream of live stage progress |
| `/api/pipeline/runs/{run_id}` | GET | Full results: ranked, rejected by stage, features, explanations |
| `/api/pipeline/runs/{run_id}/posters/{filename}` | GET | Serve poster image files |
| `/api/feedback` | POST | Submit approve/override/reject. Writes labels, updates taste profile |
| `/api/taste/status` | GET | Label count, exemplar count, learned head status, gate override counters |
| `/api/taste/retrain` | POST | Rebuild taste profile + retrain learned head from all accumulated labels |

---

## 12. Open Questions for Refinement

> **All questions below are resolved by the decisions in §13.**
> This section is retained for historical context only; see §13 for the
> implemented answers.

These are design decisions that need discussion before implementation:

1. **Reject-all without alternative (Scenario D):** Currently just records a negative. Should it also surface suggestions? ("Loosen OCR gate — 15 posters were rejected there"). This adds complexity but improves UX.

2. **Retroactive feature computation for rejected posters:** Doing it at feedback time is clean but means the `POST /api/feedback` call may take 1-2 seconds for a poster that needs detail features computed. Is this acceptable or should we pre-compute features for all OCR survivors (not just gate survivors)?

3. **Gate override threshold:** Is 5 overrides the right number before surfacing a recommendation? Too low = noise from one movie. Too high = user suffers in silence.

4. **Dedup-removed posters as overrides:** If a user picks a pHash-deduped poster that is visually identical to a survivor, should we accept it or explain "this is the same image as #7 in your ranked list"?

5. **Negative exemplars from overrides:** When a user overrides the auto-pick, should the auto-pick join `negative_data/` as a disliked exemplar? Currently the design says no — but the code already supports negative exemplars if the folder is populated. This could be a toggle.

6. **Partial labels for partial features (Scenario C):** If a poster was rejected at the resolution gate and has no features at all, should we still write a label to `labels.jsonl`? The learned head can't use it, but it's a record of user intent that could matter for gate override tracking.

7. **Auto-retrain after N new labels:** Should the system automatically rebuild the learned head after every 20 new labels, or only on manual trigger? Auto-retrain keeps the head current but risks training on noise before labels stabilize.

---

# Part 2 — Implementation Plan

**Status:** Implementation-ready design — discussed before build
**Date:** 2026-06-12
**Scope:** Backend only. Everything here exists to make the future frontend a thin client: the UI should never compute, join, or interpret — only render what the API returns and POST what the user clicked.

## 13. Decisions on the Open Questions (§12)

1. **Reject-all suggestions** — Yes, but computed at run time, not feedback time. The run results payload (§17.3) includes a `rejection_summary` (counts grouped by stage and reason, derived from each candidate's `stage_reached`/`rejection_reason` already in `pipeline_run.json`). The frontend maps the dominant reason to a canned suggestion. Zero new feedback-time logic.
2. **Retroactive feature computation** — Accept the 1–2 s cost at feedback time. Key fact discovered in the code: *every* candidate is downloaded to `0-originals/` before any gate runs (`test_pipeline.py` fetch stage), so even resolution-gated rejects have a w500 file on disk. Retroactive computation is possible for everything except `download_error` candidates (those become taste-profile-only, see decision 6).
3. **Gate override threshold** — Default 5, configurable (`FEEDBACK_GATE_ALERT_THRESHOLD`). Counters are **derived, not stored**: every label records a snapshot of the gate knobs active when it was written (§16.2), and `/api/taste/status` counts only override labels whose snapshot matches the *current* knob value. Changing a threshold automatically zeroes its counter — no mutable counter file, no reset bookkeeping (drops the `gate_overrides.json` idea from §7).
4. **Dedup-removed overrides** — Remap server-side. `CandidateScore` gains a `dedup_kept` field (the survivor's filename, already known in `_log_dedup_removal`, currently logged but dropped). If the user picks a dedup-removed poster, the feedback endpoint records the label against the survivor and returns `"remapped_to": "<survivor>"` so the UI can explain.
5. **Negative exemplars from overrides** — Config toggle `FEEDBACK_NEGATIVES_FROM_OVERRIDES`, default **off**. When on, only the rank-1 auto-pick the user overrode is copied to `experiments/negative_data/` — never mid-ranked posters (they weren't actively disliked, just not chosen).
6. **Labels without features** — Always write the label row (it is the record of user intent and feeds gate tracking); `features: null` rows are skipped by the head trainer. With decision 2 this only happens for `download_error` candidates.
7. **Auto-retrain** — The head auto-retrains after every feedback event once the activation thresholds are met (it's a sub-second numpy logistic regression — there is no cost to retraining eagerly). The taste-profile full rebuild stays manual (`POST /api/taste/retrain`) because it takes minutes; incremental exemplar appends (§16.3) keep it current between rebuilds. `SCORER=auto` (`pipeline/scorer.py:119`) picks up new artifacts with zero config change.

## 14. Design Refinements Beyond Part 1

**Labels v2 — self-contained records.** The current trainer joins `(title, orig_filename)` against `runs/<title>/pipeline_run.json` (`ml/head_trainer.py:64`). That join breaks the moment a movie is re-run with different config (the run JSON is overwritten — `todos.md` already flags the wipe problem). v2 label records embed the full feature vectors *at feedback time*, so training never depends on run dirs surviving. The trainer reads v2 rows directly and falls back to the legacy join only for the 78 existing v1 rows.

**Run identity.** Runs get a `run_id` and a DB row (§15). The working directory stays per-title (the `0-originals/` w500 downloads double as a download cache worth keeping), but each run's `pipeline_run.json` is also archived to `data/runs/archive/{run_id}.json` so history survives re-runs.

**Instant re-rank (`rescore`).** Because every run archives raw + normalized features per candidate, re-ranking with different scorer weights or gate floors is pure arithmetic — no images, no inference, <10 ms. `POST /api/pipeline/runs/{run_id}/rescore` is the engine behind frontend knob sliders: drag a weight, see the grid reorder live, *then* persist the knob. This is the single highest-leverage feature for the tuning UI.

**Runtime knobs API.** `GET/PUT /api/config/pipeline` exposes `pipeline_settings`. Mutations are validated by constructing a throwaway `PipelineSettings(**merged)` (reusing the existing validators in `core/pipeline_config.py:231`), applied to the live singleton (gates and scorers read attributes at call time, so the next run sees them), and persisted to `data/pipeline_overrides.json`, which `pipeline_config.py` loads at startup on top of env/.env. Knobs that only bind at process start (`EXECUTION_PROVIDER`, model paths, `AI_MODEL`) are reported in a `restart_required` list and rejected from the hot path.

**Undo.** Misclicks must be cheap to reverse. Every feedback submission gets an `event_id` shared by all rows it writes; `POST /api/feedback/undo` removes those rows, deletes the copied exemplar file, and rewrites the profile without it. Deployment is not reversed (picking a different poster redeploys anyway).

**Two known bugs fixed by the run manager.** (a) VRAM growth from per-run ONNX sessions (`todos.md`: OOM after ~8 runs) — the run manager owns a process-lifetime `FeatureExtractor` singleton instead of constructing one per request (`test_pipeline.py:353`). (b) Concurrent runs — a single `asyncio.Lock` serializes pipeline execution; a second `POST` while busy returns 409 with the active `run_id` so the UI can attach to its event stream instead.

## 15. Data Model Changes

New table `pipeline_runs` (SQLAlchemy model in `marquee/models/pipeline_run.py`, via Alembic — see §20 foundation):

| Column | Type | Notes |
|---|---|---|
| `run_id` | VARCHAR(32) PK | `uuid4().hex` |
| `movie_id` | FK movies.id, indexed | |
| `status` | VARCHAR(20) | `running` / `completed` / `flagged_manual` / `failed` |
| `started_at` / `completed_at` | DATETIME | |
| `scorer_name` | VARCHAR(20) | `weighted` / `learned` — provenance for labels |
| `counts_json` | TEXT | stage survivor counts (the `outcome.counts` dict) |
| `archive_path` | TEXT | `data/runs/archive/{run_id}.json` |
| `output_dir` | TEXT | working dir under `experiments/runs/` |
| `feedback_event_id` | VARCHAR(32) NULL | set when feedback was submitted — UI shows reviewed/unreviewed |

`Movie` gains `genres` (TEXT, JSON array — synced from Radarr's `genres` field in `core/sync_service.py:144`, one-line addition). Needed for label diversity tracking (§10) and the taste map (design 11).

Labels stay in `experiments/feedback/labels.jsonl` — the single training source of truth, append-only, hand-editable, already consumed by `head_trainer`. No DB mirror (status derives everything by parsing it; it's tiny).

## 16. Module Plan

### 16.1 `marquee/pipeline/runner.py` + `run_manager.py` (refactor)

Move the pipeline body out of the route file: `_run_sync_stages`, `_write_run_json`, download helpers (`api/routes/test_pipeline.py:275-607`) move to `pipeline/runner.py` essentially unchanged, with one addition — a `progress: Callable[[ProgressEvent], None]` callback invoked where `_stage_done`/`STAGE START` log today (and per-poster inside the OCR loop, the long stage). `RunManager` (module singleton):

- `start(movie) -> run_id` — creates the DB row, fires the run on a worker thread via `asyncio.to_thread`, bridges progress callbacks onto an `asyncio.Queue` per run with `loop.call_soon_threadsafe`.
- holds the GPU lock, the process-lifetime `FeatureExtractor`, and a ring buffer of past events per run so an SSE client that connects late (or reconnects) replays the full history.
- archives `pipeline_run.json` → `data/runs/archive/{run_id}.json` and finalizes the DB row.

`POST /api/test/pipeline/movie/{id}` becomes a thin wrapper over the same runner (kept for curl-based workflows; can be retired once the frontend exists).

### 16.2 `marquee/ml/feedback_store.py` — labels v2

One JSON object per line, written atomically (append with `O_APPEND` single `write()`):

```json
{
  "v": 2,
  "event_id": "f3a9c2...", "ts": "2026-06-12T18:30:00Z",
  "run_id": "ab12...", "movie_id": 12, "tmdb_id": 562,
  "title": "Die Hard", "year": 1988,
  "orig_filename": "abc.jpg",
  "label": 1,
  "action": "override",
  "role": "user_pick",
  "rank": 4, "stage_reached": "ranked", "rejection_reason": null,
  "scorer_name": "weighted", "model_name": "clip-vit-b-32",
  "gate_snapshot": {"OCR_MAX_RESIDUAL_BOXES": 0, "GATE_MIN_AESTHETIC": 4.5,
                     "GATE_MIN_KNN_SIM": 0.45, "GATE_MIN_WIDTH": 500},
  "raw_features": {...}, "normalized_features": {...}, "extended_features": {...}
}
```

`role` ∈ `user_pick` / `auto_pick` / `explicit_reject` (scenario D's lone negative). The store also exposes `summary()` for `/api/taste/status`: total labels, distinct movies, genre spread (via `Movie.genres`), per-gate override counts filtered by current-knob snapshot match (decision 3).

`ml/head_trainer.py` changes: `collect_samples` first consumes v2 rows' embedded `normalized_features`, then falls back to the run-JSON join for v1 rows. `main()` gets a callable wrapper `train_from_labels() -> LogisticHead | None` so the API can invoke it (CLI stays).

### 16.3 `marquee/ml/profile_updater.py` — incremental exemplar add

Approve/override does, synchronously (~1–2 s):

1. Copy the selected poster (the **original-resolution** file from `ranked/` when available, else the w500 original) into `experiments/training_data/` as `"{title} ({year}).jpg"`, deduped with ` - 2`, ` - 3` suffixes — the folder remains the human-auditable source of truth, exactly as the 430 hand-picked exemplars.
2. Append to `taste_profile.{model}.npz`: CLIP embedding (from the run's embedding cache — `pipeline/features.py:515` already persists per-candidate embeddings in `EMBEDDING_CACHE_DIR`, so no re-encode), DINO embedding (one encode if the profile has the DINO space), one calibration column (reusing `taste_trainer.measure_exemplar_features` on the single file), recomputed `centroid_emb` and `dino_self_knn` (O(N²) on ~450 points — microseconds). Atomic write: temp file + `os.replace`.
3. `POST /api/taste/retrain` remains the consistency anchor: full `taste_trainer` rebuild from the folders, run in background with progress streamed over the same SSE mechanism.

### 16.4 `marquee/pipeline/retro_features.py` — feature backfill for rejects

`compute_full_features(image_path, candidate, movie_title) -> FeatureVector`: runs the style phase (`FeatureExtractor.extract_style`), a single-image OCR pass (`PosterTextFilter.is_acceptable`) to obtain the title bbox/residual boxes regardless of accept/reject, then the detail phase via a relaxed variant of `_complete_one` that does not require `ocr_result.accepted` (`pipeline/features.py:402` currently raises). Used only by the feedback endpoint per decision 2.

### 16.5 Routers

- `marquee/api/routes/pipeline.py` — replace the 501 stubs (`pipeline.py:10-27`).
- `marquee/api/routes/feedback.py` — feedback + undo.
- `marquee/api/routes/taste.py` — status + retrain (+ map endpoints, design 11).
- `marquee/api/routes/config.py` — knobs.
- `marquee/api/explanations.py` — maps gate/rejection reason codes and top-3 scorer contributions to plain-language strings (one dict + a formatter; the UI never sees raw codes like `ocr_text_heavy` without a human translation next to them).

## 17. API Contracts

### 17.1 Run lifecycle

| Endpoint | Method | Notes |
|---|---|---|
| `/api/pipeline/movie/{movie_id}/run` | POST | → `202 {"run_id", "events_url"}`; `409 {"active_run_id"}` if busy |
| `/api/pipeline/runs/{run_id}/events` | GET | SSE (`text/event-stream` via `StreamingResponse`, no new dep). Replays history, then live events, ends with `event: done` |
| `/api/pipeline/runs/{run_id}` | GET | Full results (§17.3) |
| `/api/pipeline/runs/{run_id}/posters/{orig_filename}` | GET | `FileResponse`; resolves the candidate's current `image_path` inside the run dir; filename validated against the run's candidate set (no path input) |
| `/api/pipeline/runs/{run_id}/rescore` | POST | `{"weights": {...}, "gates": {...}}` → re-ranked list from archived features; weights cloned into a throwaway config for `WeightedScorer(config)` (`pipeline/scorer.py:59`) |
| `/api/movies/{movie_id}/runs` | GET | Run history for a movie (DB) |

SSE event shape: `{"run_id", "stage", "state": "start"|"progress"|"end", "done", "total", "survivors", "elapsed_s"}` — stage names already human-mappable (`fetch`, `sha256`, `gate-resolution`, `style-features`, `gate-style`, `ocr`, `phash`, `detail-features`, `rank`, `output`).

### 17.2 Feedback

```
POST /api/feedback
{
  "run_id": "ab12...",
  "action": "approve" | "override" | "reject_all",
  "selected_filename": "xyz.jpg",        // override only
  "deploy": true                          // default true: write poster to the movie folder via PosterService (design 10 §11)
}
→ 200 {
  "event_id": "...", "labels_written": 2,
  "exemplar_added": "Die Hard (1988).jpg",
  "remapped_to": null,                    // dedup twin remap notice (decision 4)
  "gate_override": {"reason": "ocr_text_heavy", "count_at_current_threshold": 3},
  "head": {"retrained": false, "reason": "needs 5 movies, have 4"},
  "deployed_to": "/plunder/movies/Die Hard (1988)/poster.jpg"
}
POST /api/feedback/undo   {"event_id": "..."}
```

Scenario mapping (§3–6) is server-side: `approve` → 1 positive label; `override` → negative for rank-1 + positive for selection (+ retro features if the pick was a reject, + gate snapshot); `reject_all` → 1 `explicit_reject` negative for rank-1. Approve where `selected_filename == auto-pick` collapses to scenario A automatically.

### 17.3 Run results payload (shaped for the three-section UI, §9.2)

```json
{
  "run_id": "...", "movie": {"id": 12, "title": "Die Hard", "year": 1988, "tmdb_id": 562},
  "status": "completed", "scorer": "weighted", "reviewed": false,
  "auto_pick": { "...candidate...", "explanations": ["Strong style match...", "Official key art", "..."] },
  "ranked": [ {"orig_filename", "rank", "final_score", "poster_url", "contributions", "raw_features", "normalized_features"} ],
  "rejected": { "gate": [...], "ocr": [...], "dedup": [...], "errored": [...] },
  "rejection_summary": {"ocr_text_heavy": 15, "style_aesthetic_floor": 4, "dedup_phash": 3},
  "counts": {...}, "stage_timings_s": {...}, "config_snapshot": {...}
}
```

Rejected entries carry `rejection_reason`, its plain-language explanation, `dedup_kept` where applicable, and `poster_url`.

### 17.4 Taste status & knobs

```
GET /api/taste/status → {
  "labels": {"total": 80, "movies": 4, "positives": 41, "genres": ["Action", "Sci-Fi"]},
  "exemplars": {"count": 432, "negatives": 12, "last_rebuild": "...", "stale_appends": 3},
  "learned_head": {"active": false, "n_samples": 78, "train_accuracy": null,
                    "activation": {"movies": {"have": 4, "need": 5}, "labels": {"have": 78, "need": 150}}},
  "gate_alerts": [{"gate": "ocr_text_heavy", "overrides": 5, "threshold_knob": "OCR_MAX_RESIDUAL_BOXES",
                    "current_value": 0, "suggestion": "Raise to 1 to tolerate taglines"}]
}
POST /api/taste/retrain → 202 {"events_url": ...}        // full profile rebuild + head retrain
GET  /api/config/pipeline → {"values": {...}, "defaults": {...}, "overrides": {...}, "restart_required": [...]}
PUT  /api/config/pipeline {"GATE_MIN_AESTHETIC": 4.0} → validated, applied, persisted
```

## 18. Config Additions (`core/pipeline_config.py`)

| Knob | Default | Purpose |
|---|---|---|
| `FEEDBACK_LABELS_PATH` | `experiments/feedback/labels.jsonl` | existing path, promoted to config |
| `FEEDBACK_GATE_ALERT_THRESHOLD` | `5` | decision 3 |
| `FEEDBACK_NEGATIVES_FROM_OVERRIDES` | `false` | decision 5 |
| `FEEDBACK_DEPLOY_DEFAULT` | `true` | approve/override deploys via PosterService |
| `HEAD_MIN_LABELS` / `HEAD_MIN_MOVIES` | `150` / `5` | activation thresholds (§10), checked by auto-retrain |
| `HEAD_AUTO_RETRAIN` | `true` | decision 7 |
| `RUNS_ARCHIVE_DIR` | `data/runs/archive` | run JSON archive |

## 19. Tech Stack

No new Python dependencies. SSE via plain `StreamingResponse`; UUIDs from stdlib; Alembic is already a project dependency (used for the first time here — see §20). The future frontend (separate effort) is assumed React + Vite on port 5173, already whitelisted in `CORS_ORIGINS` (`config.py:70`).

## 20. Build Order & Verification

Shared foundation first (also unblocks designs 10 and 11):

1. **Foundation** — Alembic baseline (`alembic init`, autogenerate from current models, stamp existing DBs), then one migration adding: `movies.genres`, the `pipeline_runs` table, and design 10's artwork columns. Sync `genres` from Radarr. *Verify:* `alembic upgrade head` on a copy of the live DB; `pytest tests/test_models.py tests/test_sync.py`.
2. **Runner refactor + RunManager + run/SSE/results/poster endpoints** — pure mechanical move + event plumbing; the frontend can start building against this alone. *Verify:* run a movie end-to-end via the new endpoint, `curl` the SSE stream, confirm archive JSON + DB row; re-run the same movie and confirm the old archive survives. Confirm VRAM stays flat across 10 consecutive runs (the singleton-extractor fix).
3. **Feedback core** — feedback_store, retro_features, profile_updater, feedback router, head_trainer v2 reading, status endpoint. *Verify:* scenario tests A–D against a fabricated archived run in `tmp_path` (no ML needed for A/B/D; C exercises retro_features and needs ML extras); incremental profile append then a `knn_sim` query showing the new exemplar's influence; undo round-trip leaves `labels.jsonl` and the profile byte-identical.
4. **Knobs + rescore** — config router, overrides persistence, rescore endpoint. *Verify:* PUT an invalid value (rejected by validator), PUT a gate floor and confirm the next run's `CONFIG` log line reflects it; rescore returns a different order when `WEIGHT_FACE_AREA` is zeroed.
5. **Deploy-on-approve** — wire `PosterService` (built in design 10 §11) into the feedback endpoint.

Then design 10 (restoration) and design 11 (taste map) in either order — both depend only on step 1.
