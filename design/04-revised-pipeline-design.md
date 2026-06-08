# Marquee — Revised AI Pipeline Design

Supersedes the scoring design in `04-pipeline-design.md` and `04-taste-profile-design.md`.
This document is the conceptual reference: what the pipeline does, why, what every tool is for,
and everything that can be tuned or swapped. The companion document
`04-agent-build-instructions.md` tells a coding agent how to build it.

---

## 0. The core problem and the fix

The old scorer was `0.8 · cos(emb, centroid) + 0.2 · cos(color, centroid)`. It failed in three
recurring ways: it picked posters with plain white titles instead of colored stylized ones, it
let low-quality fan art win, and when floating-head posters were filtered out by CLIP text
prompts, movies with only floating-head art fell back to junk.

The root cause is a **tool mismatch**, not bad thresholds. A single CLIP-similarity score was
being asked to judge four independent things at once:

1. **Composition** — artistic/illustrative vs floating-head photo composite.
2. **Typography** — colored/stylized title vs plain white default text.
3. **Production quality** — official polish vs low-quality fan art.
4. **Text content** — title only vs title plus taglines/credits.

CLIP embeddings are dominated by *semantic content* (what is depicted) and barely encode
typography color or production quality. Two posters identical except for title color sit almost
on top of each other in CLIP space. So the model is blind on exactly the axes that matter.

**The fix: stop using one similarity score. Decompose taste into measurable per-attribute
scalars, compute each with the right tool, and combine them into a ranked score.** CLIP stays,
but demoted — it is no longer the judge.

### The insight that makes this work: content is controlled within a movie

Every candidate for one movie shares the same subject. So when ranking *within a single movie*,
semantic content is largely held constant, and the variation left between candidates is mostly
style, typography, and quality — precisely the explicit scalars. This is why the explicit
features carry most of the discriminative load at the final pick, and why the embedding's
weakness at style matters less than it first appears: its content strength is a constant in the
within-movie comparison.

---

## 1. Two-decision architecture: GATE then RANK

The single most important structural change. The pipeline makes two distinct kinds of decision:

**GATE — absolute, hard, global, per-candidate.** Uses fixed thresholds that are the same for
every movie. Rejects candidates that are invalid or junk: too low resolution, text-heavy (OCR),
below an aesthetic floor, or wildly off-style. A gate REMOVES a candidate.

**RANK — relative, soft, within-movie.** Among the candidates that survived the gate for *this*
movie, score them and order them. The best available wins even if all of them are mediocre in
absolute terms. A low rank does not remove a candidate; everything past the gate is kept.

The principle: **gate on quality and validity, rank on taste.** This is what fixes the
floating-head fallback. Floating-head is a *ranking penalty*, never a gate — so a movie that only
has floating-head posters still surfaces its best one, and fan junk cannot win the fallback
because the hard quality gate already removed it.

If the gate empties a movie's candidate set entirely (e.g. only sub-500px posters exist), the
item is **flagged for manual review** rather than deploying junk or nothing silently.

---

## 2. End-to-end pipeline

Stages marked **[NOW]** are built in the current phase (inside the test endpoint). Stages marked
**[TODO]** are documented here but deferred.

```
[NOW]  Stage 1  — FETCH        TMDB → staging, download all at w500
                               PosterCandidate already carries:
                               width, height, vote_average, vote_count, language
[NOW]  Stage 2a — SHA-256      exact-duplicate removal (cheap, no OCR needed)
[NOW]  Stage 3  — OCR          PaddleOCR — GATE text-heavy/no-text
                               AND emit: title bbox, residual-text boxes
[NOW]  Stage 2b — pHash        near-dupe removal on OCR survivors
[NOW]  Stage 4  — FEATURES     compute the full scalar vector on pHash survivors
                               (embedding step runs here — last, on fewest images)
[NOW]  Stage 5  — GATE         hard floors: resolution, aesthetic, off-style
                               gated-out copied to a visible bucket with reasons
[NOW]  Stage 6  — RANK         Phase-0 weighted head over normalized features
                               → sort → top-5
[NOW]  Stage 7  — OUTPUT       all ranked posters renamed rank__score__orig into a single
                               `ranked/` folder; top-5 re-downloaded at original resolution
[TODO] Stage 8  — DEPLOY       write to media library, update DB
[TODO] Stage 9  — FEEDBACK     user approves #1 or picks another from top-5
                               → positive + override-negative labels
                               → only manually-approved joins the taste profile
[TODO] Stage 10 — RETRAIN      learned head trained on accumulated labels
```

Why this order: SHA-256 exact dedup is byte-cheap and unambiguous so it runs first. OCR then
runs on the thinned set. pHash runs *after* OCR — not before — so that when near-duplicate
variants differ only in text content (e.g. one version has a tagline overlay, another is clean),
the OCR gate decides which survives rather than a resolution tiebreak (see §9). Features run
only on pHash survivors. Resolution is known from TMDB metadata before download, so the cheapest
gate signal is available earliest.

---

## 3. The taste profile (exemplar store)

Revised from `04-taste-profile-design.md`.

**Source:** `experiments/data/training_data/` — ~430 hand-picked posters, one per movie, named
`Movie Title (Year).jpg`. Pure human preference, no automated selection.

**What is stored** (`taste_profile.npz`):

| Key | Shape | Purpose |
|---|---|---|
| `embeddings` | (N, 512) | All individual CLIP **B/32** embeddings, L2-normalized. **Used for k-NN.** |
| `poster_names` | (N,) | Original filenames |
| `centroid_emb` | (512,) | Mean direction — **diagnostics only**, not used for scoring |
| `model_name` | scalar | `"clip-vit-b-32"` — which model produced these (see §12) |

**Removed from the old design:** `color_hists`, `centroid_color`. The LAB color signal is gone;
title colorfulness replaces it.

**How it scores:** the style feature for a candidate is **the mean cosine similarity to its
k nearest exemplars** (k≈10), not cosine to the centroid. k-NN over individual exemplars handles
multimodal taste (a horror cluster and an animation cluster coexist) without the centroid's
blur, and without manual genre buckets.

### The k parameter

Default **k = 10**. Effect of changing it:

- **Decrease k (→ 1–3):** style score = closeness to the single nearest liked poster. More
  permissive and multimodal — a candidate near *any one* liked poster scores high. Captures niche
  sub-styles but is noisier and outlier-sensitive (one odd exemplar can pull candidates in).
- **Increase k (→ 30–50):** averages over many neighbors, approaching centroid behavior — **blur
  creeps back.** Smoother and more conservative; demands the candidate sit near the *bulk* of your
  taste; penalizes niche sub-styles.
- **k ≈ 10:** captures local style clusters without collapsing to the global mean.

### Backend: numpy now, Chroma later

The `TasteStore` ABC is backed by `NumpyTasteStore` (a `.npz` + brute-force cosine). At ~430
vectors growing by one per manual approval, this will be sub-millisecond forever — k-NN over a
few thousand 512-dim vectors is a single matmul, faster than any vector-DB round trip. **A vector
database is not needed and would be over-engineering at this scale.** The ABC keeps
`ChromaTasteStore` as a drop-in swap *if* one of these ever becomes true:

- The searchable embedding set reaches ~100k+ vectors (won't happen via approvals).
- You add a library-wide "find visually similar posters across my whole collection" feature over
  tens of thousands of stored posters.

If you reach that point, FAISS (a library, no server) is the lighter first step before Chroma
(a service) on a homelab. Until then: numpy.

---

## 4. The feature vector

Computed per candidate that survives OCR. Each scalar, what it measures, the tool, and the raw
range:

| Scalar | Measures | Tool | Raw range |
|---|---|---|---|
| `knn_sim` | how on-style (mean cosine to k nearest exemplars) | CLIP B/32 ONNX + numpy | ~0.4–0.9 |
| `aesthetic` | production quality / "how pretty" | LAION B/32 linear head on the CLIP embedding | ~1–10 |
| `title_colorfulness` | colored stylized title vs plain white | OpenCV on the PaddleOCR title box | ~0–100+ |
| `text_residual` | leftover non-title text (clutter) | PaddleOCR box count + area fraction | 0–1 |
| `resolution` | native available resolution | TMDB `width`×`height` (original dims) | megapixels |
| `sharpness` | soft/upscaled fan art | OpenCV Laplacian variance (on w500) | unbounded |
| `face_area` | floating-head penalty | face detector ONNX, Σ face-box area / image | 0–1 |
| `provenance` | official-vs-fan, popular-vs-obscure | TMDB `vote_average`/`vote_count` (shrinkage) | 0–1 |
| `lang_match` | title language matches preference | TMDB `language` field | 0/0.5/1 |

Notes on the non-obvious ones:

- **`title_colorfulness`** uses the Hasler–Süsstrunk colorfulness metric on the title crop. The
  title box is identified by matching OCR detections against the known title tokens (the OCR
  filter already does this matching). White text → near-zero; colored/stylized → high.
- **`face_area`** uses a *photographic* face detector on purpose. It fires on floating-head photo
  composites and mostly misses illustrated faces — which is the behavior you want: artistic
  posters with drawn faces are not penalized, only photo floating-heads are. This is a feature of
  the asymmetry, not a flaw.
- **`provenance`** must use a shrinkage (Bayesian) average, not raw `vote_average`. A 9.0 from
  2 votes is not more trustworthy than a 7.5 from 500. Use
  `adjusted = (v/(v+m))·R + (m/(v+m))·C` where `R`=vote_average, `v`=vote_count, `C`=global mean
  (~6.5), `m`=confidence weight (~25). This is the same idea FileBot converged on
  (`vote_average × vote_count`). **TMDB and the future TVDB use different scales — normalize each
  source independently to 0–1 before it enters the vector.**

The embedding itself is **reduced to one scalar** (`knn_sim`) for the scorer — the raw 512-dim
vector is *not* concatenated into the feature vector. With only hundreds of labels (Phase 1), a
head over 512+9 dims would overfit; reducing the embedding to its task-relevant scalar keeps the
head ~9-dimensional, trainable, and interpretable.

---

## 5. The scorer (learned head), in phases

The "head" is the small model on top of the frozen feature extractors. It maps the feature
vector → one score. Built as a **pluggable interface** so the implementation can change without
touching the pipeline.

**Phase 0 — hand-weighted [NOW].** `final_score = Σ wᵢ · normalizedᵢ`, weights set by intuition,
all in config. Works on day one with zero labels. The detailed logs (§ in agent doc) print the
per-feature contribution so you can see why each poster ranked where it did and re-tune the
weights from evidence.

**One scoring convention — no exceptions.** Every feature is oriented so that **higher = better**,
and **every weight is positive**. There are no mixed signs in the scorer. The two features that
read as "penalties" are inverted at normalization so they point the same way as everything else:

- `text_residual` is normalized as `1 - raw`, so the value means *cleanliness* (high = little
  residual text), with a positive weight.
- `face_area` is normalized as `1 - face_area`, so the value means *face-absence* (high = few/no
  faces), with a positive weight.

Mixing identity-plus-negative-weight with invert-plus-positive-weight in the same scorer is exactly
the inconsistency that produces double-negative sign bugs — so it is banned.

**Score range is free under this convention.** Every normalized feature is in [0,1] and the active
weights sum to 1.0 (0.30 + 0.20 + 0.15 + 0.15 + 0.10 + 0.07 + 0.03 = 1.00), so the weighted sum is
already a value in [0,1]. There is **no theoretical-min/max mapping and no separate scaling step**.
The only guard: if any weight changes, or if currently-zero-weight features (`resolution`,
`lang_match`) are given nonzero weights, divide the sum by the total of the active weights to keep
the score in [0,1].

**Phase 1 — logistic regression [TODO].** Once the feedback loop has accumulated ~50–100
approvals: each shown candidate gets a label (approved/selected → 1; the overridden auto-pick →
0; others unlabeled). Logistic regression learns weights so `σ(w·x + b) ≈ P(I'd pick this)`. The
learned weights *are* a readout of your taste. Beats hand weights because they come from your
actual behavior.

**Phase 2 — LightGBM, then learning-to-rank [TODO].** Trees capture non-linear feature
interactions, stay robust on small tabular data, run in milliseconds on CPU, and expose feature
importances. The final form is pairwise ranking (LightGBM `lambdarank`) trained on preference
pairs — which your override events natively are (`selected > auto-pick`). This matches the task
(rank within a movie) exactly.

The same feature vector feeds all phases; only `f(x)` changes.

---

## 6. Blur and drift control

Blur is the disease of the centroid — the mean of stylistically diverse vectors is a meaningless
middle. The defenses, in priority order:

1. **k-NN over exemplars instead of a centroid** (§3). This alone removes most blur and handles
   multimodal taste automatically.
2. **Explicit scalars are genre-agnostic.** "Colored title," "high aesthetic," "low face area"
   are good regardless of genre, so they carry the consistent part of your taste with no blur at
   all.
3. **Feedback hygiene [TODO].** Only manually-approved posters join the profile. Auto-picks that
   are never reviewed must never feed back, or the model reinforces its own unverified guesses
   and collapses. Override events generate an explicit negative.

**Genres are deliberately NOT used as buckets.** Hard genre buckets fragment your feedback data
(each bucket would need its own 50–100 approvals before a per-bucket head could train), and genre
is only a coarse proxy for style anyway. If genre-conditional preferences later prove strong, the
right way to use genre is as a **soft feature** appended to the vector (one-hot from Radarr/TMDB),
letting one head learn the conditioning without partitioning the data. Documented as an
experiment, off by default.

---

## 7. Tools inventory

| Tool | What it is | Used for | Where it runs |
|---|---|---|---|
| **CLIP ViT-B/32** (ONNX) | OpenAI image-text encoder, 512-dim | the embedding behind `knn_sim` and the aesthetic head | ONNX Runtime |
| **LAION aesthetic head** | `nn.Linear(512,1)` trained on B/32 CLIP embeddings (`sa_0_4_vit_b_32_linear.pth`) | the `aesthetic` scalar | numpy/torch, one matmul |
| **PaddleOCR** | text detection + recognition | OCR gate; emits title box + residual-text boxes | CPU/GPU |
| **OpenCV** | classic CV | colorfulness, Laplacian sharpness, crops | CPU |
| **face detector** (ONNX) | lightweight detector (SCRFD / RetinaFace / OpenCV res10 SSD) | the `face_area` scalar | ONNX Runtime |
| **imagehash** | pHash | near-duplicate dedup | CPU |
| **numpy / scikit-learn** | arrays, k-NN, (later) logistic head | k-NN sim, normalization, Phase-1 head | CPU |
| **LightGBM** [TODO] | gradient-boosted trees | Phase-2 head | CPU |
| **ONNX Runtime** | inference engine | runs all ONNX models | EP-configurable (see §8) |

---

## 8. Hardware and execution providers

**Backbone: CLIP B/32**, chosen for three reasons: lightest variant (~150 MB ONNX), the LAION
aesthetic head exists for it, and with explicit scalars carrying the style load the embedding does
not need B/16's finer detail.

**Execution provider is environment-dependent and must be configurable** with auto-detection and
a CPU fallback:

- **Dev (Apple Mac):** `CoreMLExecutionProvider` (Apple Neural Engine). Current development is on
  an M3 Pro Mac with 36 GB RAM, so the model artifacts must be sized to run there for now; on this
  machine CoreML is the provider that activates.
- **Production target (Intel iGPU homelab):** `OpenVINOExecutionProvider`. This is the piece that
  makes CLIP and the face detector run well on the Plex/QSV crowd's hardware with no dedicated GPU.
- **Fallback everywhere:** `CPUExecutionProvider`.

The ONNX model is portable across all three; only the provider at session creation changes.
**Provider selection must skip providers that are not available on the current machine and fall
through to the next, never raise.** OpenVINO is not present on macOS, so on the dev Mac selection
falls through to CoreML — nothing needs to be installed for OpenVINO until the homelab deployment.

**On-demand vs batch** (current mode: on-demand): on-demand means a human waits, so per-movie
latency is user-facing and biases toward the light B/32. Batch (overnight, whole library) hides
latency and would let you afford a heavier model. Profile end-to-end before optimizing — PaddleOCR
(3-pass with a 2× upscale) is the likely bottleneck, not the embedding. Cache embeddings so
re-runs never recompute.

---

## 9. Things to experiment with / tune

Everything here is a knob, not a commitment.

- **k (k-NN neighbors):** default 10. See §3 for increase/decrease effects.
- **Phase-0 weights:** the whole point of the per-feature contribution logs is to tune these from
  evidence. Start from the defaults in the agent doc, inspect bad picks, adjust.
- **Gate thresholds:** resolution floor, aesthetic floor, off-style floor. Keep the off-style
  floor conservative (low) so it rarely fires — over-gating reintroduces the fallback-to-junk
  failure.
- **Combined fan-junk gate:** (low aesthetic AND low votes AND low resolution) as a single hard
  reject. Off by default; turn on if fan art still slips through.
- **L/14 upgrade:** if B/32's style discrimination proves too crude, upgrade to CLIP ViT-L/14
  (768-dim, richer) and switch to the improved LAION MLP aesthetic head (L/14). Heavier — pair it
  with batch mode so the latency is hidden. Requires rebuilding the exemplar store (different
  model space — see §12).
- **DINOv2 swap:** DINOv2 encodes style/texture better than CLIP. If the cross-movie style gate is
  too coarse, swap the *embedding* to DINOv2 and keep CLIP **only** as the aesthetic head's input
  (the aesthetic head is CLIP-specific). Costs a second model per poster — justify it before
  adding it on an iGPU.
- **pHash placement:** pHash now runs *after* OCR (adopted). Near-duplicate variants sometimes
  differ only in text content — one version has a tagline or credits overlay, another is clean.
  With pHash before OCR, the resolution tiebreak kept the higher-res text-heavy version, then OCR
  rejected it, silently eliminating a valid poster design. Moving pHash after OCR lets the OCR gate
  arbitrate the pair first: if one passes and one fails, the clean version survives. The cost is
  OCR running on all SHA-256 survivors (not the pHash-thinned set), but near-duplicate groups are
  typically small so the overhead is modest.
- **Genre as a soft feature:** §6. Append genre one-hot to the vector if conditional preferences
  emerge. Never as a hard bucket.
- **title_colorfulness glyph isolation:** default measures colorfulness over the whole title crop;
  an experiment isolates glyph pixels (Otsu threshold inside the box) to remove background
  influence.
- **ChromaDB / FAISS:** only at the scale thresholds in §3.
- **Pairwise ranking [TODO]:** the Phase-2 endpoint once feedback data exists.

---

## 10. Future work (explicitly deferred)

- The **feedback loop** (Stage 9): approval/override capture, label generation, appending approved
  embeddings to the profile.
- The **learned head** (Phase 1/2): training job, scheduled retrain, model versioning.
- **Deploy to library** (Stage 8): write the chosen poster into the media tree, update DB.
- **TVDB** for TV shows: a second source with its own metadata; provenance must be normalized
  separately from TMDB (§4).
- **VLM-as-judge** as an optional high-tier re-ranker over the top-5 for users with a dedicated
  GPU.

---

## 11. What this replaces in the old docs

- `04-pipeline-design.md` Stage 4 (`0.8·emb + 0.2·color`, negative prompts, `CandidateScore` with
  only emb/color similarity) → the feature vector + GATE/RANK split + expanded score record.
- `04-taste-profile-design.md` centroid-as-scorer and LAB color histogram → k-NN over exemplars,
  LAB dropped, B/16 → B/32.
- The CLIP **negative-prompt** floating-head filter → the `face_area` scalar as a soft ranking
  penalty.

---

## 12. Footnote — embeddings are model-specific

**An embedding only means anything relative to the exact model that produced it.** A B/32
embedding and a B/16 embedding (or L/14, or DINOv2) live in different vector spaces and are not
comparable even when the dimensionality matches. The same is true of the aesthetic head: the
LAION B/32 linear head is trained on B/32 embeddings and produces garbage on B/16 or L/14 inputs.

Consequences that are mandatory, not optional:

1. Every stored embedding artifact (`taste_profile.npz`, any cached candidate embeddings) carries
   a `model_name` tag **and** the model name appears in the filename, e.g.
   `taste_profile.clip-vit-b-32.npz`.
2. Changing the embedding model (B/32 → L/14, or → DINOv2) **invalidates the entire store**. The
   exemplar store must be rebuilt and all cached embeddings recomputed.
3. The aesthetic head must always match the embedding backbone. Swapping one requires swapping the
   other.
4. On load, the pipeline must verify the store's `model_name` matches the configured model and
   refuse to run (loud error) on a mismatch, rather than silently scoring against a stale space.

---

## 13. Run robustness (operational contract)

How the pipeline behaves around failures and re-runs. A single bad poster must never sink a movie,
but a broken environment must never be papered over.

**Per-candidate failures are tolerated; systemic failures abort.** If one candidate fails OCR or
feature extraction (corrupt image, decode error, an OCR crash on that file), reject only that
candidate, record the reason (`ocr_error` / `feature_error`) on its record, route the file to a
visible bucket for inspection, and continue with the rest. If a failure affects every candidate —
model fails to load, taste store missing, or the `model_name` mismatch guard trips — abort the
whole run loudly. Per-candidate problems are data; environment problems are infrastructure, and the
two are handled differently.

**Re-runs are idempotent.** At the start of a run, clear the generated stage directories and the
previous run artifacts (`pipeline.log`, `pipeline_run.json`), then regenerate. Do not delete the
`0-originals/` w500 downloads — Stage 1 already skips existing files, so keeping them avoids
re-fetching from the CDN. A re-run must never mix stale results with fresh ones.

**Original-resolution re-download is best-effort.** The top-5 are re-downloaded at original
resolution, but the w500 copy is already a valid usable poster. If an original re-download fails
(CDN blip, network error), keep the w500 copy, log the failure, and record the per-item resolution
status in `pipeline_run.json` (`original_download: false`) so it is visible which of the five are
full-resolution. A failed re-download never loses the pick or aborts the run.
