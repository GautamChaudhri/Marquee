# Marquee Pipeline — Learning Roadmap

A beginner-friendly guide to understanding the AI pipeline, organized from the ground up.
Assumes you know linear algebra and basic neural net concepts.

---

## What the Pipeline Does (30-second summary)

1. **Fetches** all poster candidates for a movie from TMDB
2. **Filters out** the junk (low-res, ugly, fan art, text-heavy) using hard gates
3. **Computes 9 measurements** on each surviving poster (style match, aesthetic quality, title colorfulness, face area, sharpness, etc.)
4. **Ranks** them into a top-5 by combining those measurements into a single score

The AI engine lives in steps 3 and 4 — it learns what you like from 430 hand-picked training posters and uses those to judge new candidates.

---

## Level 1: The Building Blocks

The most foundational concepts — these power almost every step of the pipeline.

### 1. Vector Embeddings

**Why it's in the pipeline:**
CLIP (the vision model) turns every poster image into a list of 512 numbers called an embedding. Similar-looking posters end up with similar embedding numbers. This 512-number "address" is how the pipeline compares posters mathematically.

**Best resource:**
[Vector Embeddings for Beginners — 30 min CS Course](https://www.youtube.com/watch?v=PR7xz5vQKGg) by OpenAI — 30 minutes

**Key intuition:**
An embedding is like GPS coordinates for an image. Just like two restaurants near each other have close GPS coordinates, two visually similar posters have close embedding vectors. The pipeline uses these to find "posters that look like posters you already liked."

### 2. Cosine Similarity

**Why it's in the pipeline:**
This is the tape measure between two poster embeddings. The pipeline uses it everywhere — k-NN scoring, style matching, deduplication. It measures the *angle* between two vectors (not their distance), which means it ignores brightness/scale and only cares about the direction of the numbers.

**Best resource:**
[Cosine Similarity, Clearly Explained!!!](https://www.youtube.com/watch?v=e9U0QAFbfLI) by StatQuest — 12 minutes

**Key intuition:**
With linear algebra background: cos(v, w) = v·w / (|v|·|w|). Two identical posters have cosine=1. Two completely unrelated posters are closer to 0. The pipeline normalizes all embeddings to unit length first (L2-normalized), so cosine similarity = dot product.

### 3. k-Nearest Neighbors (k-NN)

**Why it's in the pipeline:**
This is the core scoring mechanism. For each new poster candidate, the pipeline:
1. Converts it to a CLIP embedding
2. Finds the 10 nearest exemplars (your liked posters) in embedding space
3. Averages their cosine similarities (using softmax weighting — see Level 3)
4. That average is the `knn_sim` scalar — "how much does this look like posters you already like"

**Best resource:**
[K Nearest Neighbors — Intuitive Explained](https://www.youtube.com/watch?v=0p0o5cmgLdE) — 5 minutes

**Key intuition:**
k-NN is the simplest ML algorithm. It literally just finds the k closest examples and measures similarity. No training. No parameters to learn. A poster that looks like 10 posters you already like will score higher than one that looks like only 2. The pipeline's k=10 default balances multimodal taste (horror and animation clusters coexist) without blurring them together.

**k parameter effects:**
- k=1-3: More permissive, captures niche sub-styles but noisy (one odd exemplar can pull in unwanted posters)
- k=10 (default): Captures local style clusters without collapsing to the global mean
- k=30-50: Approaching centroid behavior — smooth and conservative, penalizes niche styles

---

## Level 2: The AI Model — CLIP

### 4. CLIP (Contrastive Language-Image Pre-training)

**Why it's in the pipeline:**
CLIP is the engine that converts images → embeddings. Built by OpenAI, trained on 400 million image-text pairs scraped from the internet. It learns to map images and text into the same vector space — so the embedding for a poster of Iron Man is close to the text "Iron Man movie poster".

**Best resource:**
[OpenAI CLIP Model Explained](https://www.youtube.com/watch?v=jXD6O93Ptks) — 12 minutes

**Pipeline-specific details:**
- Uses **ViT-B/32** (Vision Transformer, patch size 32) — the lightest CLIP variant (~150 MB ONNX)
- Produces **512-dimensional**, L2-normalized embeddings
- Runs on **ONNX Runtime** so it works on NVIDIA GPU, Intel iGPU, or Apple Neural Engine

**Critical insight:**
CLIP is great at *what's in the image* (content) but **bad at style details** like title color, typography quality, or production polish. Two posters identical except the title color sits almost on top of each other in CLIP space. This is exactly why the pipeline doesn't just use CLIP similarity — it adds explicit measurements (Level 4) for everything CLIP is blind to.

**The inside-movie advantage:**
Every candidate for one movie shares the same subject. So when ranking *within a single movie*, CLIP's content strength is a constant — all the variation left between candidates is style, typography, and quality. This is why the explicit features carry most of the discriminative load.

**Model specificity footnote:**
An embedding only means anything relative to the exact model that produced it. B/32 embeddings and B/16 embeddings live in different vector spaces. The taste store carries a `model_name` tag and the pipeline refuses to run on a mismatch.

---

## Level 3: The Math That Combines Everything

### 5. Softmax with Temperature

**Why it's in the pipeline:**
The k-NN score doesn't just average the 10 nearest neighbors equally. It uses `KNN_WEIGHTING=softmax` with `KNN_SOFTMAX_TEMP=0.1`. This means the very closest exemplars dominate the score and distant neighbors barely contribute — like a weighted vote where the closest neighbor's opinion matters 10x more.

**Best resources:**
- [Softmax — What is the Temperature of an AI??](https://www.youtube.com/watch?v=YjVuJjmgclU) by CodeEmporium — 14 minutes
- [Learn Softmax Intuitively](https://www.youtube.com/watch?v=toUnzxrMC-I) — 10 minutes

**Key intuition:**
Softmax turns raw numbers into probabilities that sum to 1. Temperature controls how "greedy" the weighting is:
- Low temperature (0.1): The closest neighbor gets almost all the weight — winner-takes-almost-all
- High temperature (1.0): All 10 neighbors get roughly equal weight — smoother, more democratic
- Default 0.1: A candidate sitting on top of one taste cluster is not diluted by weaker neighbors from other clusters

### 6. Feature Normalization (Min-Max Scaling)

**Why it's in the pipeline:**
The 9 features in the scoring vector have wildly different raw ranges:
- `aesthetic`: ~1-10
- `title_colorfulness`: ~0-100+
- `face_area`: 0-1
- `resolution`: megapixels (0.3-6.0)
- `text_residual`: 0-1

Without normalization, `title_colorfulness` (range 0-100+) would swamp `face_area` (range 0-1). Every feature is squashed to [0,1] so they're comparable and the weights actually mean something.

**Best resource:**
[Normalization vs Standardization — Feature Scaling](https://www.youtube.com/watch?v=bqhQ2LWBheQ) by DataMListic — 8 minutes

**Pipeline-specific convention:**
Every feature is oriented so **higher = better**, and every weight is positive. There are no mixed signs. The two penalty features are inverted at normalization:
- `text_residual` becomes `1 - raw` — means "cleanliness"
- `face_area` becomes `1 - raw` — means "face-absence"

This prevents double-negative sign bugs that plague mixed-sign scorers.

**Active weight convention:**
The active weights sum to 1.0 (e.g. 0.30 + 0.20 + 0.15 + 0.15 + 0.10 + 0.07 + 0.03 = 1.00), so the weighted sum is already in [0,1]. If weights change or dormant features become active, divide by the sum of active weights to keep the score in [0,1].

---

## Level 4: The Learned Head (Future Phases)

These stages are deferred but documented in the pipeline spec. Phase 0 (current) uses hand-tuned weights; these replace them with data-driven weights.

### 7. Logistic Regression

**When it will be used:** Phase 1 — after ~50-100 approvals exist

**What it does:**
A logistic regression model learns 9 weights (one per feature) that best predict "would Gautam pick this poster?" The formula is:

    score = sigmoid(w1*x1 + w2*x2 + ... + w9*x9 + b)

where sigmoid squashes any number into a 0-1 probability.

**Best resource:**
[Logistic Regression Explained Simply](https://www.youtube.com/watch?v=iCrTcILQj4o) — 8 minutes

**Why it matters:**
Phase 0 weights are set by intuition. Phase 1 learns them from your actual behavior. The learned weights are literally a readout of your taste — if `title_colorfulness` gets a high weight, you consistently prefer colored stylized titles over plain white ones.

**With linear algebra background:**
This is a linear model followed by a sigmoid. The decision boundary is a hyperplane in 9-dimensional feature space. Your approvals define which side of the plane a poster falls on. This is the simplest possible "learning from examples" model — exactly right for ~100 labels.

### 8. Gradient Boosting / LightGBM

**When it will be used:** Phase 2 — once enough labels exist (>500+)

**What it does:**
A tree-based model that captures non-linear interactions — relationships like "high aesthetic matters more when face_area is low but not when the poster is also low resolution." Decision trees natively handle this; linear models cannot.

**Best resource:**
[Gradient Boosting — In Depth Intuition](https://www.youtube.com/watch?v=Nol1hVtLOSg) by Krish Naik — 23 minutes

**Why LightGBM specifically:**
- Trains fast on CPU
- Handles missing values natively
- Produces feature importances (explanatory)
- Has a built-in `lambdarank` mode for learning-to-rank (see #9)

### 9. Learning to Rank (LambdaRank)

**When it will be used:** Phase 2 final form

**What it does:**
Instead of predicting an absolute score for each poster (regression) or whether you'd pick it (classification), LambdaRank trains on *preference pairs* — "the poster you selected is better than the one the AI auto-picked." This matches the task exactly (rank within a movie).

**Best resource:**
[Introduction to Learning to Rank](https://towardsdatascience.com/introduction-to-learning-to-rank-b9ec3a312fc8) (article, ~10 min read)

**Key intuition:**
Your override events are natively pairwise: every time you manually pick a different poster, that's a training pair saying "my pick > your auto-pick." LambdaRank learns from these pairs directly, optimizing for the ordering rather than the scores.

---

## Level 5: The Tools and Techniques

Context for how each component works without deep implementation detail.

### 10. ONNX Runtime

**Where it appears:** Runs every AI model in the pipeline

**What it is:**
ONNX (Open Neural Network Exchange) is an open format for ML models — think of it as a universal adapter. A model trained in PyTorch can be exported to ONNX and then run on any hardware via ONNX Runtime.

**Best resource:**
[ONNX — Open Format for ML Models](https://www.youtube.com/watch?v=0t2jOBZSd6s) — 7 minutes

**Why it's used:**
The same CLIP ONNX model runs on:
- NVIDIA GPU (CUDAExecutionProvider)
- Intel iGPU (OpenVINOExecutionProvider)
- Apple Silicon (CoreMLExecutionProvider / ANE)
- Any CPU (CPUExecutionProvider)

No code changes. The pipeline auto-detects which provider is available and falls through the chain. This is how the same pipeline runs on your M3 Pro Mac during dev and on the RTX 3070 Linux box in production.

### 11. Perceptual Hashing (pHash)

**Where it appears:** Near-duplicate poster dedup (Stage 2b)

**What it is:**
A hash algorithm that considers the *visual content* of an image, not the bit-by-bit bytes. Two photos of the same poster resized differently will have the same or very close pHash, even though their SHA-256 hashes are completely different.

**Best resource:**
[Perceptual Hashing — Friday Minis 273](https://www.youtube.com/watch?v=pyzMP9qvxl0) — 5 minutes

**Dedup pipeline:**
1. SHA-256 first: byte-identical duplicates removed (e.g. same file downloaded from different CDNs)
2. pHash second: near-duplicates caught (e.g. same poster art with different title overlays)

**Preference-aware survivor selection (important):**
Within a near-duplicate group, the pipeline doesn't just keep the highest-res version. It picks by: (title found > fewest residual text boxes > knn_sim > resolution tiebreak). This prevents keeping a "MARVEL STUDIOS" overlay variant over the clean title-only version.

### 12. LAION Aesthetic Predictor

**Where it appears:** The `aesthetic` scalar in the feature vector

**What it is:**
A tiny linear model — literally one matrix multiplication: `nn.Linear(512, 1)`. Trained on 176,000 human-rated images, it takes a CLIP embedding as input and outputs a 1-10 "how pretty" score. It costs essentially nothing because the CLIP embedding is already computed for k-NN — the aesthetic score is just one extra matmul on the same vector.

**Best resource:**
[LAIMOON Aesthetic Predictor V2](https://github.com/christophschuhmann/improved-aesthetic-predictor) (GitHub README)

**Pipeline notes:**
- The `.pth` weights are converted to a `.npz` sidecar file once (PyTorch is export-time only)
- Aesthetic is used as a **gate** (a floor below which posters are rejected) AND as a **feature** in the ranking score
- The gate has a "k-NN rescue" feature: even if aesthetic is low, if the poster is very stylistically close to your liked posters, it passes the gate

### 13. PaddleOCR

**Where it appears:** Text detection on posters (Stage 3)

**What it does:**
Detects and recognizes text in images. The pipeline uses it to:
1. Find the title text box (to compute `title_colorfulness` from that region)
2. Identify residual text (taglines, credits, dates) — posters with too much clutter are rejected
3. Verify the title is actually present on the poster

**Best resource:**
[PaddleOCR Quick Start](https://github.com/PaddlePaddle/PaddleOCR) (GitHub README)

**Pipeline-specific usage:**
- Uses PP-OCRv5_mobile_det for throughput
- Auto-detects CUDA (falls back to CPU otherwise)
- 3-pass scan: main pass + top strip + bottom strip (catches header/footer credits)
- OCR is the pipeline bottleneck — it's 20-50x slower than CLIP inference
- That's why the style gate (CLIP-based) runs *before* OCR: OCR only sees candidates already on-style and above the quality floor

### 14. Face Detection (SCRFD / RetinaFace)

**Where it appears:** The `face_area` scalar

**What it does:**
Detects photographic faces in posters. Uses a photographic detector (not an illustrated-face detector) deliberately — artistic posters with drawn faces are *not* penalized, only floating-head photo composites are.

**Pipeline notes:**
- `face_area` = sum of face bounding-box areas / total image area
- Inverted at normalization: `1 - face_area` — means "face-absence" (high = no floating heads)
- Acts as a **ranking penalty**, never a gate — so movies with only floating-head posters still surface their best one

---

## The Core Insight (One Paragraph)

This is the single most important design insight:

> Every candidate for one movie shares the same subject. When ranking *within a single movie*, semantic content is largely held constant, and the variation left between candidates is mostly style, typography, and quality.

CLIP knows all 30 Iron Man posters depict Iron Man. The differences between them — colored title vs white text, official art vs fan-made, clean vs cluttered — are what the explicit features capture. CLIP handles *what's in the image* and the feature vector handles *what makes a good poster*. They work together, not against each other.

---

## Quick Reference: Pipeline Stages

```
Stage 1  — FETCH          TMDB -> staging, download all at w500
Stage 2a — SHA-256        exact-duplicate removal (cheap)
Gate 1   — RESOLUTION     hard floor from TMDB metadata
Stage 4a — STYLE          batched CLIP -> knn_sim + aesthetic + metadata scalars
Gate 2   — STYLE          aesthetic floor (with knn rescue), off-style floor
Stage 3  — OCR            PaddleOCR — gate text-heavy, emit title box + residual
Stage 2b — pHash          near-dupe removal on OCR survivors
Stage 4b — DETAIL         face_area, title_colorfulness, sharpness, text_residual
Gate 3   — FAN-JUNK       optional combo gate (off by default)
Stage 6  — RANK           weighted head -> sort -> top-5
Stage 7  — OUTPUT         re-downloaded at original resolution
```

The order is deliberate: **cheapest signal first.** Timestamp at 2026-06-11.

---

## Recommended Learning Order

| Time | What | Concepts Covered |
|------|------|------------------|
| Today (30 min) | k-NN + Cosine Similarity videos | 60% of the pipeline's scoring logic |
| Tomorrow (45 min) | CLIP + Vector Embeddings videos | The data flowing through the pipeline |
| This week (30 min) | Softmax/Temperature + Normalization | How the numbers combine |
| Eventual curiosity | Logistic Regression -> Gradient Boosting | Future phases of the learned head |

Each "Best Resource" link is chosen for beginner accessibility with your math background.
