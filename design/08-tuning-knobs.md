# Marquee Pipeline Tuning Reference

Knob groups, what they control, and how to test each one.
All knobs live in `marquee/core/pipeline_config.py` and are overridable via `.env`.

---

## The method: hold-out cross-validation

Before touching anything, understand what "better" means:

```bash
.venv/bin/python -m marquee.ml.knn_eval          # offline k-NN sweep, <1 second
.venv/bin/python -m marquee.ml.knn_eval --dino   # also sweep DINOv2 space
```

The evaluator reports three numbers per config:

| Column | What it means | Direction |
|---|---|---|
| `ho_mean` / `ho_p5` | Held-out liked posters' average / 5th-pct knn_sim | Higher |
| `gate_miss` | Fraction of held-out liked posters below `GATE_MIN_KNN_SIM` | Must stay 0% |
| `AUC` | P(random kept poster outscores random flagged one) — from `labels.jsonl` | Higher, 1.0 = perfect |

**Golden rule**: `gate_miss > 0%` means you've overtightened something — the gate is absolute, those posters are unrecoverable. Never chase AUC at the cost of gate_miss.

More labels = more decisive AUC. Add movies to `marquee/experiments/feedback/labels.jsonl` (see todos.md) and the evaluator sharpens automatically.

---

## Tuning order

Groups are listed in dependency order — later groups consume signals produced by earlier ones.

```
A (Taste retrieval)
    ↓
B (Gates)       C (Normalization)
         ↓
         D (Scorer weights)
              ↓
              E (Learned head — Phase 1)

OCR / Dedup: orthogonal tracks, tune independently
```

Change one group, validate, commit, then move to the next.

---

## Group A — Taste retrieval (k-NN)

These knobs control how the 430 exemplar embeddings are combined into a single `knn_sim` scalar for each candidate, and the matching `dino_knn` in the DINOv2 space.

| Knob | Default | What it does |
|---|---|---|
| `K_NEIGHBORS` | 10 | How many nearest exemplars to look at |
| `KNN_WEIGHTING` | `softmax` | `softmax` = nearest exemplars dominate; `mean` = plain average |
| `KNN_SOFTMAX_TEMP` | 0.1 | Smaller = nearest exemplar dominates more; larger → approaches `mean` |
| `TASTE_NEG_WEIGHT` | 1.0 | Penalty when closer to disliked exemplars than liked ones (only fires with `negative_data/`) |

**k and temperature are coupled** — k barely matters at temp 0.03 (softmax always picks the single best match anyway); k matters a lot with `mean` weighting. Always tune them together.

**How to test**: `knn_eval` offline. Watch `ho_p5` (no held-out liked poster should approach 0.45), `ho_min`, and AUC. The 2026-06-12 sweep showed zero gate misses at all k from 1–50 and AUC variation of only ±0.015 across the entire grid — meaning k is not a big lever yet. More labels will change this.

**After changing**: if you change `K_NEIGHBORS`, rebuild the taste profile (`python -m marquee.ml.taste_trainer`) — the DINOv2 normalization range (p5/p95 of `dino_self_knn`) is baked in at training time and must stay consistent.

---

## Group B — Style gates

Hard thresholds. Anything below these is rejected and cannot be rescued by ranking.

| Knob | Default | What it does |
|---|---|---|
| `GATE_MIN_KNN_SIM` | 0.45 | Style floor — poster must resemble your liked exemplars |
| `GATE_MIN_AESTHETIC` | 4.5 | Aesthetic floor (LAION head) |
| `GATE_AESTHETIC_RESCUE_KNN` | 0.55 | If `knn_sim ≥` this, relax the aesthetic floor — stylized posters the taste profile endorses shouldn't be penalized |
| `GATE_MIN_AESTHETIC_RESCUED` | 2.0 | Aesthetic floor for taste-profile-rescued posters |
| `GATE_FAN_JUNK_ENABLED` | `false` | Combo gate (off by default) rejecting low-aesthetic + low-provenance + low-resolution combos |

**How to test**: `knn_eval --ks <target> --temps <target>` to confirm `gate_miss` stays 0% at the new threshold. Then rerun a labeled movie and check `GATE REJECT` log lines — any rejection of a poster you've labeled 1 is a false rejection. Raise thresholds conservatively (≤0.02 steps on `GATE_MIN_KNN_SIM`).

---

## Group C — Normalization ranges

These determine the mapping from a raw feature value to a [0, 1] score fed into the ranking formula. Wrong ranges compress everything to the same score.

| Knob | Default | Controls |
|---|---|---|
| `NORM_KNN_MIN` / `NORM_KNN_MAX` | 0.4 / 0.9 | knn_sim ramp (cosine → score) |
| `NORM_OFFICIAL_MIN` / `NORM_OFFICIAL_MAX` | 0.60 / 0.95 | official_family ramp; measured on Avengers/Interstellar/SD: official 0.90+, fan art 0.60–0.80 |
| `NORM_AESTHETIC_MAX` | 10.0 | LAION aesthetic head max |
| `NORM_SHARPNESS_MAX` | 2000.0 | Laplacian variance max |
| `NORM_RESOLUTION_MAX_MP` | 6.0 | Resolution ceiling in megapixels |
| `NORM_TITLE_COLORFULNESS_MAX` | 60.0 | Title colorfulness ceiling |
| `CALIBRATION_BANDWIDTH_SCALE` | 1.0 | KDE bandwidth multiplier for exemplar-calibrated normalization; >1 = wider/more forgiving taste bands |
| `CALIBRATION_MIN_SAMPLES` | 20 | Minimum exemplar samples before KDE calibration activates (falls back to fixed ramp below this) |

**How to test**: add `WEIGHT_*` logging or look at `RANK DETAIL` log lines in pipeline runs to see what normalized values features are actually producing. If everything clusters at 0.0 or 1.0, the range is too tight or too loose. The official_family range was measured empirically from 3 movies' cached embeddings — recalibrate after adding more labeled movies.

---

## Group D — Scorer weights

These balance the 13 features in the Phase-0 weighted scorer. The scorer renormalizes by the total of *available* weights so toggling a feature off (or its model being absent) doesn't break the [0, 1] output range.

| Knob | Default | Notes |
|---|---|---|
| `WEIGHT_KNN_SIM` | 0.30 | Primary style signal — usually the strongest single feature |
| `WEIGHT_OFFICIAL_FAMILY` | 0.12 | CLIP cosine to TMDB primary poster; strongest discriminator in labeled data |
| `WEIGHT_TITLE_COLORFULNESS` | 0.15 | Colorfulness of the title text area |
| `WEIGHT_FACE_AREA` | 0.15 | Floating-head penalty (large face → lower score) |
| `WEIGHT_DINO_KNN` | 0.12 | DINOv2 second style opinion (texture/medium, GPU tiers only) |
| `WEIGHT_TASTE_TYPICALITY` | 0.12 | Mean KDE typicality across `TYPICALITY_FEATURES` |
| `WEIGHT_AESTHETIC` | 0.12 | LAION aesthetic head — was 0.20, reduced 2026-06-11 (loves slick fan art) |
| `WEIGHT_TEXT_RESIDUAL` | 0.10 | Non-title text penalty |
| `WEIGHT_SHARPNESS` | 0.03 | Laplacian variance (image sharpness) |
| `WEIGHT_QUALITY_ARTIFACTS` | 0.03 | Blockiness + sensor noise (clean = 1) |
| `WEIGHT_PROVENANCE` | 0.02 | TMDB poster votes — was 0.07, reduced 2026-06-11 (votes are nearly all zero) |
| `WEIGHT_RESOLUTION` | 0.0 | Off — resolution is a gate, not a scoring signal |
| `WEIGHT_LANG_MATCH` | 0.0 | Off — irrelevant once OCR gates foreign-text posters |

**`TYPICALITY_FEATURES`** (list knob): the per-feature KDE typicality contributors — palette, composition, typography geometry, face/person geometry, CLIP zero-shot axes, aesthetic. Each feature's contribution to `taste_typicality` is weighted equally; the list is configurable.

**How to test**: offline re-rank using saved `pipeline_run.json` and the labels. Or rerun labeled movies and compare rank lists against labels — did flagged posters move down, kept posters stay up? The preview learned head (2026-06-12) independently confirmed: `official_family` +1.71 (strongest), `aesthetic` −0.01 (near-zero) — which is consistent with the current weights. This is the group most directly replaced by the learned head once enough labels exist.

---

## Group E — Learned ranking head (Phase 1)

Once ~150+ labels exist across 5+ diverse movies:

```bash
python -m marquee.ml.head_trainer    # trains logistic head from labels.jsonl
```

Activate with `SCORER=auto` (picks up the artifact automatically) or force with `SCORER=learned`. The learned head replaces Group D's hand-tuned weights with a data-driven logistic regression over the same normalized features.

Current state: 78 labels (3 movies), preview accuracy 75.6% — too thin to activate. See `todos.md`.

---

## OCR track (independent)

| Knob | Default | What it does |
|---|---|---|
| `OCR_MAX_RESIDUAL_BOXES` | 0 | 0 = strict title-only; raise to 1–2 to tolerate taglines as a rank penalty |
| `OCR_MAX_RESIDUAL_AREA_FRACTION` | 0.04 | Secondary area guard |
| `OCR_REQUIRE_TITLE` | `true` | Reject posters whose text never matches the title |
| `OCR_ACCEPT_NO_TEXT` | `true` | Batch-level fallback: rescue textless posters only when zero titled survive |
| `OCR_CONFIDENCE_THRESHOLD` | 0.75 | Full-image detection confidence |
| `OCR_STRIP_CONFIDENCE_THRESHOLD` | 0.65 | Top/bottom strip detection confidence |
| `OCR_BOTTOM_CONFIDENCE_THRESHOLD` | 0.50 | Bottom strip (credits block) confidence |
| `OCR_FUZZY_CUTOFF` | 0.60 | Title token fuzzy-match cutoff |
| `OCR_TITLE_PROXIMITY_PIXELS` | 30.0 | Residual boxes within this distance of the title are treated as title fragments (small boxes only) |
| `OCR_RESIDUAL_SIGNIFICANT_AREA_FRACTION` | 0.005 | Geometry-based significance: a confident box ≥ this fraction of image area is real text even when the recognizer garbled it |
| `OCR_RESIDUAL_SIGNIFICANT_WIDTH_FRACTION` | 0.40 | Same rule, width-based |
| `OCR_DETAIL_PASSES` | `true` | Top-strip and 2× bottom-strip passes; disable on N150-class CPUs |

**How to test**: rerun a movie, check `OCR REJECT` / `OCR ACCEPT` log lines. Any poster you labeled as kept and that got rejected is a false rejection — loosen the threshold that caused it. Any poster you labeled as flagged (because it has non-title text) and that passed is a false accept — tighten. Cross-reference with the `reason=` field (`no_text`, `no_title`, `text_heavy`).

---

## Dedup track (independent)

| Knob | Default | What it does |
|---|---|---|
| `DEDUP_PHASH_THRESHOLD` | 6 | pHash Hamming distance — variants ≤ this are treated as the same image |
| `DEDUP_MIN_POSTER_WIDTH` | 500 | Minimum width to even enter dedup |

Preference tuple (how the "winner" is chosen from a pHash cluster): `(title_found, -residual_boxes, is_primary, knn_sim)` then resolution as final tiebreak. Raising the threshold to 8–10 merges more title-position variants — use `DEDUP KEEP` log lines to verify the right variant is winning its cluster.

---

## Quick reference: run the evaluator

```bash
# Full sweep across all k / weighting / temperature combos
.venv/bin/python -m marquee.ml.knn_eval

# Targeted sweep after narrowing down
.venv/bin/python -m marquee.ml.knn_eval --ks 3,5,7,10 --temps 0.05,0.1,0.2

# Include DINOv2 space (uses profile, not embedding cache)
.venv/bin/python -m marquee.ml.knn_eval --dino

# Change k without editing .env: set env var inline
K_NEIGHBORS=5 KNN_SOFTMAX_TEMP=0.05 .venv/bin/python -m marquee.ml.knn_eval --ks 5 --temps 0.05
```

The evaluator reads from `marquee/ml/taste_profile.clip-vit-b-32.npz` and `data/cache/embeddings/clip-vit-b-32/`. No GPU required.
