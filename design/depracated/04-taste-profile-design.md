# Taste Profile — Design Reference

How the taste profile is built from `experiments/data/training_data/`, what it contains,
and how it drives poster scoring.

---

## Training Data

**Location:** `experiments/data/training_data/`  
**Size:** 430 posters, one per movie  
**Format:** `Movie Title (Year).jpg` — manually curated by the user

Each poster was hand-picked as representative of the user's aesthetic taste.  No automated
selection — the training set is pure human preference.

---

## Feature Extraction

For each of the 430 posters, two feature vectors are extracted:

### A. CLIP Embedding (512-dim)

```
Image → preprocess_image()
        ├── Convert to RGB
        ├── Resize to 224×224 (LANCZOS — bicubic equivalent)
        └── Normalize with CLIP mean/std
            mean = [0.48145466, 0.4578275, 0.40821073]
            std  = [0.26862954, 0.26130258, 0.27577711]
     → CLIPImageEncoder.encode()
        ├── ONNX Runtime forward pass (1, 3, 224, 224) → (1, 512)
        ├── Backend: CoreMLExecutionProvider (Apple Neural Engine, 599/625 nodes)
        └── L2-normalize output
     → 512-dim float32 vector (unit length)
```

This captures **visual style**: composition, mood, genre-specific visual language, typography
style.  Two posters with similar layouts and artistic approaches will have similar CLIP embeddings
regardless of their specific movie content.

**Model:** `openai/clip-vit-base-patch16` exported to ONNX (329 MB).

### B. LAB Colour Histogram (48-dim)

```
Image → extract_color_histogram()
        ├── OpenCV: read BGR → resize 256×256 (INTER_AREA) → convert to CIE LAB
        ├── 3 channels × 16 bins each (range [0, 256]) → concatenate
        ├── Normalize per-channel to sum=1
        └── L2-normalize final vector
     → 48-dim float32 vector (unit length)
```

This captures **colour palette**.  CIE LAB is perceptually uniform — equal distances in LAB
space correspond to equal perceived colour differences.  Two posters with similar colour
grading (teal/orange blockbuster, desaturated horror, warm indie) will have similar colour
vectors even if the subject matter differs.

**Library:** OpenCV `cv2.calcHist()` — salvaged as-is from the old project's
`experiments/profiling/profiling_utils.py`.

---

## Centroid Computation

```python
centroid_emb   = L2_normalize(embeddings.mean(axis=0))    # mean of 430 × 512-dim vectors
centroid_color = L2_normalize(color_hists.mean(axis=0))   # mean of 430 × 48-dim vectors
```

Every individual embedding is L2-normalized — they lie on a unit hypersphere.  The centroid
is the **mean direction** of all 430 vectors, re-normalized to unit length.  It points toward
the cluster centre — the "ideal" taste point.

Cosine similarity between any candidate and the centroid tells you how close the candidate is
to the user's average preference:

```
cosine_sim(candidate, centroid) = dot(candidate, centroid)  [since both are unit length]
```

Result range: -1.0 (opposite) to 1.0 (identical direction).  Typical taste range: 0.50–0.90.

---

## .npz File Format

Saved to `marquee/ml/taste_profile.npz` (957 KB for 430 posters):

| Key | Shape | Description |
|---|---|---|
| `centroid_emb` | (512,) | L2-normalized visual taste centroid |
| `centroid_color` | (48,) | L2-normalized colour taste centroid |
| `embeddings` | (430, 512) | All individual CLIP embeddings |
| `color_hists` | (430, 48) | All individual colour histograms |
| `poster_names` | (430,) | Original filenames (e.g. "Iron Man 2 (2010).jpg") |
| `model_name` | scalar | `"clip-vit-b-16"` — which model generated these |

Individual vectors are preserved (not just the centroid) to enable:
- Outlier detection (which posters are "off-taste"?)
- Variance measurement (how wide is the taste cluster?)
- Rebuilding the centroid with different weights
- k-NN queries without re-extracting features
- Switching to a different scoring method later

---

## Diagnostics Output

After training, `taste_trainer.py` prints:

```
============================================================
  TASTE PROFILE DIAGNOSTICS
============================================================
  Model:                  clip-vit-b-16
  Posters processed:      430
  Visual similarity:      mean=0.7597  std=0.0594
  Colour similarity:      mean=0.8000  std=0.1364
  Combined (80%/20%): mean=0.7678  std=0.0586

  Top-5 most on-taste (visual):
    1. Iron Man 2 (2010).jpg                               sim=0.8860
    2. Guardians of the Galaxy (2014).jpg                  sim=0.8814
    3. The Dark Knight (2008).jpg                          sim=0.8772
    4. Alien³ (1992).jpg                                   sim=0.8709
    5. Dawn of the Planet of the Apes (2014).jpg           sim=0.8708

  Bottom-5 most off-taste (visual):
    1. Materialists (2025).jpg                             sim=0.5729
    2. The Void (2016).jpg                                 sim=0.6000
    ...
============================================================

  Interpretation guide:
    Visual mean > 0.65 → cohesive taste, sharp selections expected
    Visual mean < 0.50 → diverse taste, consider curating training set
    Visual std  < 0.10 → consistent style preferences
    Visual std  > 0.15 → wide stylistic range in training set
============================================================
```

**Current diagnostics (430 posters):**
- Visual mean 0.76, std 0.059 — cohesive and consistent ✅
- Colour mean 0.80, std 0.136 — expected variance in colour preference
- Top-5 are comic book / sci-fi / action posters — the genre bias is visible

---

## How Scoring Works

```
Candidate poster
     │
     ├── CLIP embedding (512-dim) → cosine_sim(candidate, centroid_emb)     = emb_sim
     └── LAB colour (48-dim)      → cosine_sim(candidate, centroid_color)  = color_sim
                                          │
                                          ▼
                    final_score = 0.8 × emb_sim + 0.2 × color_sim
```

The 80/20 weighting gives visual style primacy while colour palette acts as a tiebreaker.
Two posters with similar composition but different colour grading will get different scores.

**Configurable via:**
- `TASTE_EMB_WEIGHT` (default 0.8)
- `TASTE_COLOR_WEIGHT` (default 0.2)

---

## TasteStore Abstraction

The profile is loaded via `NumpyTasteStore("taste_profile.npz")` — a `.npz`-backed
implementation of the `TasteStore` abstract base class.

```python
class TasteStore(ABC):
    centroid_emb: np.ndarray      # (512,) — visual centroid
    centroid_color: np.ndarray    # (48,)  — colour centroid
    size: int                     # how many posters stored

    def add(embedding, color_hist, metadata)   # for incremental approvals (future)
    def query_similar(embedding, k=5)          # k-NN cosine
    def get_all()                              # for diagnostics / retraining
```

The scorer depends on the abstract interface, not any specific backend.  Currently
`NumpyTasteStore` wraps the .npz file.  When the incremental approval feature is built,
swap to `ChromaTasteStore` — the scorer doesn't change.

---

## Re-training

```bash
cd /Users/gautam/Forge/Marquee
.venv/bin/python -m marquee.ml.taste_trainer
```

This re-processes `experiments/data/training_data/` and overwrites `taste_profile.npz`.
Run after adding or removing posters from the training set.  Takes ~87 seconds for 430
posters (0.20s/poster on CoreML).

If the ONNX model is regenerated (e.g., after `clip_export.py`), re-run the trainer to
ensure embeddings match the new model's output space.
