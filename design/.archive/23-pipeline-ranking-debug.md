# Design 23: Pipeline Ranking Diagnostic — "All Posters Score = 1"

**Date:** 2026-06-27
**Status:** Investigation complete, no code changes
**Branches analyzed:** `poster` (current), `backend`, `pipeline-refinement`, `main`
**Key commits:** `dcf8181` (pre-jumpstart baseline), `10958b2` (jumpstart), `789e0ab` (current HEAD)

---

## Executive Summary

All posters return a `final_score` of ~1.0 because the **learned head artifact file still exists on disk** from the jumpstart era (Jun 22). With `SCORER=auto`, `select_scorer()` finds this file and uses `LearnedScorer` instead of `WeightedScorer`. The pairwise-trained logistic head has large weight magnitudes and `bias=0.0`, causing `sigmoid(x·w + 0)` to saturate near 1.0 for virtually every poster.

**Immediate mitigation:** Delete `data/ml/learned_head.clip-vit-b-32.npz` — the pipeline will fall back to WeightedScorer and produce differentiated scores again.

---

## 1. Root Cause: Stale Learned Head Artifact

### 1.1 The smoking gun

```
$ ls -la data/ml/learned_head.clip-vit-b-32.npz
-rw-rw-r-- 1 cptbandit builders 3024 Jun 22 15:49 learned_head.clip-vit-b-32.npz
```

```
$ python3.13 -c "from marquee.ml.learned_head import LogisticHead; head = LogisticHead.load()"
Loaded: n_samples=4042, accuracy=0.7058
Feature names: ['aesthetic', 'dino_knn', 'face_area', 'knn_sim', 'lang_match',
                'official_family', 'provenance', 'quality_artifacts',
                'resolution', 'sharpness', 'taste_typicality', 'text_residual',
                'title_colorfulness']
Weights: knn_sim=+6.25, provenance=+3.20, quality_artifacts=+4.02,
         face_area=+2.00, dino_knn=-0.67, aesthetic=-0.55, lang_match=-1.60, ...
Bias: 0.0
Trained at: 2026-06-22T22:49:57

Test: all features=0.5 → score=0.999438
Test: all features=1.0 → score=1.000000
Test: all features=0.0 → score=0.500000
```

### 1.2 How `select_scorer()` resolves (current state, `SCORER=auto`)

```python
# marquee/pipeline/scorer.py:78-96
def select_scorer(config=...):
    mode = config.SCORER  # "auto"
    # "auto" branch:
    try:
        head = LogisticHead.load()     # ← FILE EXISTS → SUCCEEDS
    except FileNotFoundError:          # ← never reached
        return WeightedScorer(config)
    except RuntimeError:
        return WeightedScorer(config)
    # File loads OK → uses LearnedScorer
    return LearnedScorer(head)
```

**Before the jumpstart**, this file did NOT exist. `LogisticHead.load()` raised `FileNotFoundError`, and `select_scorer()` returned `WeightedScorer` — producing differentiated 0–1 scores using hand-tuned weights.

**After the jumpstart**, the head was trained and saved. Even though the code was reverted to the "original" approach, the file persisted on disk. `select_scorer()` now loads it and uses it.

### 1.3 Why the pairwise head produces saturated scores

The head was trained via `LogisticHead.train_pairwise()` (`marquee/ml/learned_head.py:111-158`). This method:
1. Takes `(winner_features - loser_features)` diffs for preference pairs
2. Learns coefficients that maximize pairwise agreement
3. **Explicitly sets `bias = 0.0`** because "a bias shifts every poster's score equally and so cannot change a within-movie ranking"

This is mathematically correct for **ordering** (ranking) — bias cancels in pairwise comparisons. But for producing meaningful **absolute scores**, the lack of bias with large weights causes sigmoid saturation:

```
sigmoid(x·w + 0) where |x·w| is large → sigmoid → 0.999+ (or 0.001- for negative)
```

With 13 features, each in [0,1], even average features (all=0.5) produce `x·w ≈ 7.5`, and `sigmoid(7.5) ≈ 0.999`.

**The pairwise head was designed to RANK correctly, not to SCORE meaningfully.** The distinction is that `score()` returns `P(user would pick)`, but a ranker learned without bias on pairwise diffs cannot calibrate the absolute probability.

---

## 2. Historical Timeline: What Changed When

### 2.1 Phase 0: "Original Well-Working State" (pre-`dcf8181`)

- **Taste profile** built from hand-picked downloaded posters (`marquee/ml/taste_trainer.py`)
- **WeightedScorer** used exclusively — no learned head existed
- **SCORER=auto** → FileNotFoundError → WeightedScorer fallback
- Hand-tuned weights (`marquee/core/pipeline_config.py` `scorer_weights` property):
  - `knn_sim: 0.30` (taste similarity — strongest signal)
  - `title_colorfulness: 0.15`, `face_area: 0.15`
  - `aesthetic: 0.12`, `dino_knn: 0.12`, `taste_typicality: 0.12`, `official_family: 0.12`
  - `text_residual: 0.10`, `sharpness: 0.03`, `quality_artifacts: 0.03`
  - `provenance: 0.02`, `resolution: 0.00`, `lang_match: 0.00`
- **Result:** Well-differentiated scores across posters, good ranking quality

### 2.2 Phase 1: Pairwise Training Infrastructure Added (`dcf8181`, Jun 22 11:43)

Commit: `dcf8181` — "revised reinforcement learning method"

Changes:
- Added `LogisticHead.train_pairwise()` to `marquee/ml/learned_head.py`
- Added `build_pairwise_training_data()` to `marquee/ml/head_trainer.py`
- Added `HEAD_TRAIN_MODE`, `HEAD_MIN_PAIRS`, `FEEDBACK_INDIFF_HATE_PAIR_WEIGHT` to config
- Added feedback v3 ranking event format
- Added `PosterRankingPanel.svelte` frontend component
- **No head was trained yet** — the infrastructure existed but no artifact was created
- Pipeline still used WeightedScorer (no `learned_head.npz` file)

### 2.3 Phase 2: Jumpstart (`10958b2`, Jun 22 evening)

Commit: `10958b2` — "key art engine jumpstart implemented"

Changes:
- Added onboarding system (`marquee/onboarding/`)
- Added `POST /api/onboarding/...` endpoints
- Added seed profile and taste test infrastructure
- **Pipeline was run on many movies during onboarding**
- **Labels accumulated through the feedback loop**
- **Learned head was trained from accumulated labels** (`train_from_labels()` → `head.save()`) at 22:49
- Artifact: `data/ml/learned_head.clip-vit-b-32.npz` (4042 pairs, 70.6% agreement)

**User's assessment:** "This part made the ranking a bit worse" — the learned head, trained on limited data from the jumpstart, produced saturated scores and indistinguishable rankings.

### 2.4 Phase 3: "Went Back to Original" (current `poster` branch)

User reverted to the original design: taste profile from downloaded posters, WeightedScorer as the primary scorer, learned head only activates after 50+ approvals.

**But the learned head FILE was not deleted.** The code logic didn't change (`SCORER=auto`, `select_scorer()` unchanged), but the file now exists where it didn't before. The file persisted across code reversions because it lives in `data/ml/` (gitignored runtime state).

### 2.5 What the code actually does today

```
select_scorer() → SCORER=auto → LogisticHead.load() succeeds (file exists)
                → returns LearnedScorer with the stale pairwise head
                → all posters get sigmoid(x·w + 0) ≈ 0.999 → final_score ≈ 1.0
                → all posters tie at rank 1 (sort order within ties determines display rank)
```

---

## 3. Detailed Comparison: Original vs Current

| Aspect | Original (pre-dcf8181) | Current (poster HEAD) |
|--------|----------------------|----------------------|
| **Scorer used** | WeightedScorer | LearnedScorer (pairwise) |
| **Why** | No `learned_head.npz` file | File exists from jumpstart era |
| **knn_sim weight** | 0.30 (30% of weighted avg) | +6.25 raw coefficient |
| **Score distribution** | 0.2–0.9 range, differentiated | ~0.999 for all non-zero posters |
| **Bias handling** | Weighted avg normalizes to [0,1] | Sigmoid with bias=0 saturates |
| **Resolution weight** | 0.0 (disabled) | +0.43 coefficient (active) |
| **Lang match weight** | 0.0 (disabled) | -1.60 coefficient (penalizes en-match) |
| **Taste profile** | Hand-picked 430+ posters | Same (rebuilt Jun 26, 2314KB) |
| **Feature extraction** | Same normalize.py logic | Same normalize.py logic |
| **Config: SCORER** | "auto" | "auto" |
| **Config: HEAD_MIN_LABELS** | 150 | 150 |
| **Config: HEAD_MIN_MOVIES** | 5 | 10 |
| **Config: HEAD_AUTO_RETRAIN** | False | False |

**Code differences between `dcf8181` and `789e0ab` (current) are almost entirely formatting-only** — line wrapping, import ordering. The core logic in `scorer.py`, `learned_head.py`, `head_trainer.py`, `normalize.py`, and `features.py` is functionally identical.

### 3.1 The learned head coefficients are nonsensical for non-ranking tasks

| Feature | Pairwise Coefficient | WeightedScorer Weight | Direction |
|---------|---------------------|----------------------|-----------|
| `knn_sim` | **+6.25** | 0.30 | ✅ same |
| `quality_artifacts` | **+4.02** | 0.03 | ⚠️ 133× stronger |
| `provenance` | **+3.20** | 0.02 | ⚠️ 160× stronger |
| `face_area` | **+2.00** | 0.15 | ⚠️ 13× stronger |
| `lang_match` | **-1.60** | 0.00 (off) | ❌ opposite |
| `dino_knn` | **-0.67** | 0.12 | ❌ opposite |
| `aesthetic` | **-0.55** | 0.12 | ❌ opposite |
| `title_colorfulness` | **-0.04** | 0.15 | ❌ opposite |

The learned head was trained on pairwise *differences*, not absolute values. Large coefficients work for ordering because `(x_winner - x_loser) · w` only needs to be positive. But `x · w + 0` with these coefficients produces saturated sigmoid values for any non-trivial feature vector.

---

## 4. The Fix

### 4.1 Immediate mitigation (no code change)

**Delete the stale learned head artifact:**

```bash
rm data/ml/learned_head.clip-vit-b-32.npz
```

This restores the original behavior: `select_scorer()` gets `FileNotFoundError` → returns `WeightedScorer` → differentiated 0–1 scores using hand-tuned weights. The pipeline will immediately return to the well-working state from Phase 0.

### 4.2 Alternative: force WeightedScorer via config

Set `SCORER=weighted` in `MARQUEE_PIPELINE_SCORER` env var or in the Settings page. This bypasses the auto-detection entirely.

### 4.3 Root cause fix (for future learned head activation)

When the learned head eventually activates legitimately (after 50+ approvals), the pairwise training needs a **probability calibration step**:

1. After `train_pairwise()`, run a second pass to learn a bias term that calibrates `sigmoid(x·w + bias)` to actual user approval rates
2. Or: train a Platt scaler on a held-out set of approve/reject labels
3. Or: modify `LogisticHead.train_pairwise()` to optionally learn bias from pointwise labels after convergence

Without calibration, the learned head can rank correctly but cannot produce interpretable scores.

### 4.4 Config change to prevent accidental reactivation

`HEAD_MIN_LABELS` (150) and `HEAD_MIN_MOVIES` (10) are the gates that prevent the head from being trained. But once a head artifact exists (even from a previous session), these gates are bypassed — `select_scorer()` just loads whatever file is on disk. Consider:

- `select_scorer()` should also check that the head's `n_samples` meets `HEAD_MIN_LABELS` threshold
- Or: `select_scorer()` should check `HEAD_AUTO_RETRAIN` before auto-loading

---

## 5. What Worked Well in the Original State

The original Phase 0 design (taste profile + WeightedScorer only) worked well because:

1. **Taste profile k-NN (`knn_sim`)** is the dominant signal (0.30 weight) — it captures the "feels right" quality that's hard to quantify
2. **Hand-tuned weights** encode explicit design intent:
   - `knn_sim` (0.30) — taste similarity is the most important signal
   - `title_colorfulness` (0.15) — colorful titles are visually appealing
   - `face_area` (0.15) — absence of floating heads (inverted: 1.0 - face_area)
   - Secondary signals at 0.12 each (aesthetic, dino_knn, taste_typicality, official_family)
   - Soft signals at 0.03 (sharpness, quality_artifacts) — quality gates, not rank deciders
   - Disabled at 0.00 (resolution, lang_match) — handled by gates instead
3. **Weighted average renormalization** ensures the score is always in [0,1] regardless of which features are present
4. **Calibration bands** (`CALIBRATION_ENABLED=true`) adjust feature normalization to match your actual taste distribution

---

## 6. Verification After Fix

After deleting the learned head file, verify with:

```bash
# Check scorer resolution (should log "SCORER | weighted (auto: no learned head artifact)")
curl -s http://localhost:3165/api/test/pipeline/movie/<id> | python3 -c "
import json,sys; data=json.load(sys.stdin)
for c in data['candidates']:
    print(f'{c[\"rank\"]:3d}  {c[\"final_score\"]:.4f}  {c[\"orig_filename\"]}')
"
```

Expected: rank 1..N with scores in 0.2–0.9 range, differentiated per poster.

---

## Appendix A: File Inventory (relevant to this issue)

| File | Role | Changed since Phase 0? |
|------|------|----------------------|
| `marquee/pipeline/scorer.py` | `select_scorer()`, WeightedScorer, LearnedScorer | No (formatting only) |
| `marquee/ml/learned_head.py` | LogisticHead training + inference | No (formatting only) |
| `marquee/ml/head_trainer.py` | `train_from_labels()`, pairwise data builder | No (formatting only) |
| `marquee/ml/normalize.py` | `normalize_features()` | No (formatting only) |
| `marquee/pipeline/features.py` | FeatureExtractor | No (formatting only) |
| `marquee/pipeline/runner.py` | `run_sync_stages()` — calls `select_scorer()` | No (formatting only) |
| `marquee/core/pipeline_config.py` | SCORER, LEARNED_HEAD_PATH, scorer_weights | `HEAD_MIN_MOVIES`: 5→10 |
| `data/ml/learned_head.clip-vit-b-32.npz` | **THE PROBLEM FILE** | Created Jun 22, never deleted |

## Appendix B: Git Commit Reference

| Commit | Branch | Date | Description |
|--------|--------|------|-------------|
| `dcf8181` | backend | Jun 22 11:43 | "revised reinforcement learning method" — added pairwise training, preference ranking feedback |
| `10958b2` | backend | Jun 22 evening | "key art engine jumpstart implemented" — onboarding, seed profile, taste test |
| `3ce64ce` | pipeline-refinement | — | Merge base between pipeline-refinement and current poster branch |
| `789e0ab` | poster (HEAD) | — | "fixing pipeline issues" — current state |
